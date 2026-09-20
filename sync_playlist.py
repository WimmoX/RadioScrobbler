"""Rebuild a Spotify playlist from recently played tracks in the local database.

Only tracks not already resolved in track_match trigger a Spotify Search
API call, so repeated runs stay cheap.
"""
import argparse
import os
from datetime import datetime, timedelta

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

import db
import quota
import reccobeats
from matching import best_candidate
from retry import RateLimited, call_with_retry
from stations import STATIONS

SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private user-library-read"
LOOKBACK_DAYS = 14


def get_spotify_client() -> spotipy.Spotify:
    # retries=0/status_retries=0 (Les 5): stop spotipy's own urllib3-level
    # retry from sleeping out a 429's Retry-After *inside* the HTTP call,
    # invisibly, for minutes to hours.
    #
    # status_forcelist=[999] (Les 10): with retries=0, urllib3's adapter
    # still *intercepts* any status in its forcelist (default includes 429)
    # before handing control back — and because total=0 leaves no retries
    # to actually perform, it raises requests.exceptions.RetryError instead
    # of a normal HTTPError. spotipy's RetryError handler (unlike its
    # HTTPError handler) does NOT forward the response's real headers or
    # the body's "reason" field — so our own backoff/quota code was
    # silently getting a fake Retry-After of 1 and no QUOTA_EXCEEDED
    # signal, even during a real, multi-hour block. Passing a status code
    # Spotify never returns keeps urllib3 from intercepting 429 at all, so
    # it reaches spotipy's normal HTTPError path with the real data intact.
    # (status_forcelist=[] would NOT work here: spotipy does
    # `status_forcelist or self.default_retry_codes`, and an empty list is
    # falsy in Python, so it would silently fall back to the default.)
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
            scope=SCOPE,
        ),
        retries=0,
        status_retries=0,
        status_forcelist=[999],
    )


def _call_with_retry(fn, *args, **kwargs):
    def attempt():
        try:
            return fn(*args, **kwargs)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 1))
                # QUOTA_EXCEEDED is a different, much longer-lived block than
                # an ordinary rate limit — retrying it just burns more of the
                # same exhausted budget (see LessonsLearned.md), so this
                # raises straight out instead of going through retry.py's
                # backoff loop (which only knows how to retry RateLimited).
                if e.reason == "QUOTA_EXCEEDED":
                    raise quota.QuotaBlocked(retry_after)
                raise RateLimited(retry_after)
            raise
    return call_with_retry(attempt)


def find_track_match(sp: spotipy.Spotify, artist: str, title: str, budget: quota.SearchBudget) -> dict | None:
    """Returns the best-matching Spotify track item (uri/name/artists), or None.
    Callers that need the normalized (title, artist list) for db.save_match
    should use item["name"] / [a["name"] for a in item["artists"]] — that's
    Spotify's own structured data, not our search-query text.

    Raises quota.QuotaExhausted / quota.QuotaBlocked instead of making a
    Search call we shouldn't — see quota.py."""
    def do_search(query):
        budget.check()
        results = _call_with_retry(sp.search, q=query, type="track", limit=5)
        budget.record_call()
        return results["tracks"]["items"]

    items = do_search(f"artist:{artist} track:{title}")
    if not items:
        items = do_search(f"{artist} {title}")
    return best_candidate(items, artist, title)


def filter_liked(sp: spotipy.Spotify, uris: list[str]) -> set[str]:
    """Return the subset of `uris` that are saved in the user's Liked Songs."""
    liked = set()
    track_ids = [uri.split(":")[-1] for uri in uris]
    for batch, id_batch in zip(_batched(uris, 50), _batched(track_ids, 50)):
        results = _call_with_retry(sp.current_user_saved_tracks_contains, id_batch)
        liked.update(uri for uri, is_saved in zip(batch, results) if is_saved)
    return liked


def get_or_create_playlist(sp: spotipy.Spotify, conn, station_slug: str, name: str) -> str:
    cached_id = db.get_playlist_id(conn, station_slug)
    if cached_id:
        return cached_id

    results = sp.current_user_playlists()
    while results:
        for playlist in results["items"]:
            if playlist["name"] == name:
                db.save_playlist_id(conn, station_slug, playlist["id"])
                return playlist["id"]
        results = sp.next(results) if results.get("next") else None
    # user_playlist_create() posts to the old /users/{id}/playlists endpoint,
    # which Spotify's Feb 2026 migration blocks for Development Mode apps
    # (always 403). current_user_playlist_create() uses /me/playlists instead.
    playlist = sp.current_user_playlist_create(name, public=False)
    db.save_playlist_id(conn, station_slug, playlist["id"])
    return playlist["id"]


def _batched(items: list, size: int = 100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "station", nargs="?", choices=STATIONS.keys(),
        help="Station slug (defaults to STATION_SLUG in .env). Known: " + ", ".join(STATIONS),
    )
    args = parser.parse_args()

    load_dotenv()
    station_slug = args.station or os.environ["STATION_SLUG"]
    playlist_name = STATIONS.get(station_slug) or os.environ["PLAYLIST_NAME"]
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")

    conn = db.connect(db_path)
    # played_at is stored with minute precision (see LessonsLearned.md, Les 2);
    # round the cutoff the same way so the boundary comparison is apples-to-apples.
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).replace(second=0, microsecond=0)
    tracks = db.recent_unique_tracks(conn, station_slug, since)
    print(f"{len(tracks)} unique tracks played on {station_slug} in the last {LOOKBACK_DAYS} days")

    sp = get_spotify_client()
    budget = quota.SearchBudget(conn)
    spotify_available = True

    desired_uris = set()
    new_lookups = 0
    reccobeats_fallbacks = 0
    for artist, title in tracks:
        is_cached, uri = db.get_cached_match(conn, artist, title)
        if is_cached:
            if uri:
                desired_uris.add(uri)
            continue

        match, source, reccobeats_id = None, None, None
        if spotify_available:
            try:
                match = find_track_match(sp, artist, title, budget)
                source = "spotify"
            except quota.QuotaExhausted:
                spotify_available = False
            except quota.QuotaBlocked as e:
                budget.record_block(e.retry_after_seconds)
                spotify_available = False

        # Spotify's Search is unavailable for the rest of this run — fall
        # back to ReccoBeats (no quota of its own observed so far) instead
        # of stopping the whole run. Unverified until a later run, once
        # Spotify is available again, confirms it (db.get_cached_match()
        # treats source='reccobeats' as not-yet-cached for that reason).
        if match is None and not spotify_available:
            match = reccobeats.search_track(artist, title)
            if match:
                source = "reccobeats"
                reccobeats_id = match["reccobeats_id"]
                reccobeats_fallbacks += 1

        if match is None and source is None:
            # Spotify was never asked about this track (its budget ran out first) and
            # ReccoBeats found nothing: not a real "no match". Leave it unattempted so a
            # later run can still try Spotify.
            continue

        uri = match["uri"] if match else None
        db.save_match(conn, artist, title, uri,
                       match["name"] if match else None,
                       [a["name"] for a in match["artists"]] if match else None,
                       source=source, reccobeats_id=reccobeats_id,
                       isrc=match.get("isrc") if match else None)
        new_lookups += 1
        if uri:
            desired_uris.add(uri)
    budget.finish()
    print(f"Matched {len(desired_uris)}/{len(tracks)} tracks ({new_lookups} new lookups"
          + (f", {reccobeats_fallbacks} via ReccoBeats fallback" if reccobeats_fallbacks else "") + ")")
    if not spotify_available:
        print(f"Spotify Search unavailable for the rest of this run (limit={budget.limit}/24h, "
              f"blocked_until={budget.blocked_until}) — used ReccoBeats where possible; "
              f"unverified matches will retry Spotify next run.")

    known_uris = db.get_playlist_tracks(conn, station_slug)
    blocked_uris = db.get_blocklist(conn, station_slug)

    to_add = list(desired_uris - known_uris - blocked_uris)
    remove_candidates = list(known_uris - desired_uris)

    liked_uris = filter_liked(sp, remove_candidates) if remove_candidates else set()
    to_remove = [uri for uri in remove_candidates if uri not in liked_uris]
    kept_liked = len(remove_candidates) - len(to_remove)

    if not to_add and not to_remove:
        print("Playlist is already up to date, no Spotify writes needed.")
        return

    playlist_id = get_or_create_playlist(sp, conn, station_slug, playlist_name)

    for batch in _batched(to_remove):
        _call_with_retry(sp.playlist_remove_all_occurrences_of_items, playlist_id, batch)
    for batch in _batched(to_add):
        _call_with_retry(sp.playlist_add_items, playlist_id, batch)

    db.remove_playlist_tracks(conn, station_slug, to_remove)
    db.add_playlist_tracks(conn, station_slug, to_add)

    print(f"Playlist '{playlist_name}': added {len(to_add)}, removed {len(to_remove)} tracks "
          f"({len(desired_uris)} total, {kept_liked} kept because liked, "
          f"{len(desired_uris - known_uris) - len(to_add)} blocked from re-adding)")


if __name__ == "__main__":
    main()
