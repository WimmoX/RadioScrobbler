"""Rebuild a Spotify playlist from recently played tracks in the local database.

Only tracks not already in the spotify_matches cache trigger a Spotify Search
API call, so repeated runs stay cheap.
"""
import argparse
import difflib
import os
import re
import unicodedata
from datetime import datetime, timedelta

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

import db
from retry import RateLimited, call_with_retry
from stations import STATIONS

SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private user-library-read"
LOOKBACK_DAYS = 14


def get_spotify_client() -> spotipy.Spotify:
    # retries=0: spotipy's own urllib3-level retry would otherwise sleep out
    # a 429's full Retry-After *inside* the HTTP call, blocking for minutes
    # to hours with no visibility. Disabling it makes every 429 raise a
    # SpotifyException immediately, so our own _call_with_retry (retry.py)
    # is the only thing controlling backoff/pacing.
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
            scope=SCOPE,
        ),
        retries=0,
        status_retries=0,
    )


def _call_with_retry(fn, *args, **kwargs):
    def attempt():
        try:
            return fn(*args, **kwargs)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                raise RateLimited(int(e.headers.get("Retry-After", 1)))
            raise
    return call_with_retry(attempt)


MIN_TITLE_SIMILARITY = 0.6  # below this, we'd rather report "no match" than guess wrong
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}


def _strip_accents(text: str) -> str:
    """'Caffé' -> 'Cafe', so accent differences between the radio source and
    Spotify's spelling don't break comparisons (they used to: stripping
    non-ASCII characters outright turned 'Caffé' into 'Caff', not 'Caffe')."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize_numbers(text: str) -> str:
    """'Ten Over Ten' -> '10 over 10', so it lines up with a title that spells
    numbers as digits (or vice versa) instead of scoring as barely similar."""
    return " ".join(_NUMBER_WORDS.get(word, word) for word in text.lower().split())


_TITLE_SUFFIX = re.compile(
    r"\s*[\(\[][^)\]]*(feat|ft|with)\.?\s[^)\]]*[\)\]]|\s+-\s+.+$", re.IGNORECASE
)


def _core_title(text: str) -> str:
    """Strip things Spotify adds that a radio announcer wouldn't say out loud:
    '(feat. X)' credits and ' - Remastered 2008' / ' - Radio Edit' style
    suffixes, which otherwise drag the similarity score down for an
    otherwise-correct match (e.g. 'Torch' vs 'Torch - Original 7" Single
    Version')."""
    return _TITLE_SUFFIX.sub("", text).strip()


def _title_similarity(a: str, b: str) -> float:
    variants_a = {a.lower(), _normalize_numbers(a), _core_title(a).lower()}
    variants_b = {b.lower(), _normalize_numbers(b), _core_title(b).lower()}
    return max(
        difflib.SequenceMatcher(None, va, vb).ratio()
        for va in variants_a for vb in variants_b
    )


_ARTIST_SEPARATORS = re.compile(r"\s*(?:&|,|/|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*", re.IGNORECASE)


def _normalize_artist(name: str) -> str:
    """'Fischer-Z' and 'Fischer Z' (radio vs Spotify's stylisation) should be
    treated as the same artist, so strip everything but letters/digits
    (after transliterating accents, not just discarding them)."""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(name.lower()))


def _artist_matches(item: dict, artist: str) -> bool:
    # Radio sources often credit collabs as one string ("Bonobo & Joy Crookes"),
    # while Spotify lists each as a separate artist on the track — split ours
    # the same way and match if any name overlaps.
    searched = {_normalize_artist(n) for n in _ARTIST_SEPARATORS.split(artist) if n.strip()}
    on_track = {_normalize_artist(a["name"]) for a in item["artists"]}
    return bool(searched & on_track)


def _best_candidate(items: list, artist: str, title: str) -> dict | None:
    """Pick the item that's actually the right song, not just Spotify's #1 ranked
    result — for loosely-matched queries, Spotify's own ranking isn't always right
    (e.g. "Royal Blood - 10 over 10" from the radio once matched "Come on Over"
    instead of the correct "Ten Over Ten", which was ranked #1 in the same
    response). We require the artist to match (allowing for collabs and minor
    stylisation differences) and pick the item with the most similar title; if
    nothing clears the similarity bar, we report no match instead of silently
    caching a wrong one."""
    same_artist = [item for item in items if _artist_matches(item, artist)]
    if not same_artist:
        return None
    # Rank by the lenient score first (so "Torch - Single Version" can still
    # win when it's the only candidate), but break ties on the raw,
    # un-stripped similarity — otherwise "Marliese" and "Marliese -
    # Reimagined" score identically and we could just as easily pick the
    # remake over the original the radio actually played.
    def rank(item):
        raw = difflib.SequenceMatcher(None, item["name"].lower(), title.lower()).ratio()
        return (_title_similarity(item["name"], title), raw)
    best = max(same_artist, key=rank)
    if _title_similarity(best["name"], title) < MIN_TITLE_SIMILARITY:
        return None
    return best


def find_track_uri(sp: spotipy.Spotify, artist: str, title: str) -> str | None:
    results = _call_with_retry(sp.search, q=f"artist:{artist} track:{title}", type="track", limit=5)
    items = results["tracks"]["items"]
    if not items:
        results = _call_with_retry(sp.search, q=f"{artist} {title}", type="track", limit=5)
        items = results["tracks"]["items"]
    best = _best_candidate(items, artist, title)
    return best["uri"] if best else None


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

    desired_uris = set()
    new_lookups = 0
    for artist, title in tracks:
        is_cached, uri = db.get_cached_match(conn, artist, title)
        if not is_cached:
            uri = find_track_uri(sp, artist, title)
            db.save_match(conn, artist, title, uri)
            new_lookups += 1
        if uri:
            desired_uris.add(uri)
    print(f"Matched {len(desired_uris)}/{len(tracks)} tracks ({new_lookups} new Spotify lookups, rest from cache)")

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
