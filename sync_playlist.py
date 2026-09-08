"""Rebuild a Spotify playlist from recently played tracks in the local database.

Only tracks not already in the spotify_matches cache trigger a Spotify Search
API call, so repeated runs stay cheap.
"""
import argparse
import os
from datetime import datetime, timedelta

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

import db
from retry import RateLimited, call_with_retry
from stations import STATIONS

SCOPE = "playlist-modify-public playlist-modify-private user-library-read"
LOOKBACK_DAYS = 14


def get_spotify_client() -> spotipy.Spotify:
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
        scope=SCOPE,
    ))


def _call_with_retry(fn, *args, **kwargs):
    def attempt():
        try:
            return fn(*args, **kwargs)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                raise RateLimited(int(e.headers.get("Retry-After", 1)))
            raise
    return call_with_retry(attempt)


def find_track_uri(sp: spotipy.Spotify, artist: str, title: str) -> str | None:
    results = _call_with_retry(sp.search, q=f"artist:{artist} track:{title}", type="track", limit=1)
    items = results["tracks"]["items"]
    if not items:
        results = _call_with_retry(sp.search, q=f"{artist} {title}", type="track", limit=1)
        items = results["tracks"]["items"]
    return items[0]["uri"] if items else None


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

    user_id = sp.me()["id"]
    results = sp.current_user_playlists()
    while results:
        for playlist in results["items"]:
            if playlist["name"] == name:
                db.save_playlist_id(conn, station_slug, playlist["id"])
                return playlist["id"]
        results = sp.next(results) if results.get("next") else None
    playlist = sp.user_playlist_create(user_id, name, public=False)
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
