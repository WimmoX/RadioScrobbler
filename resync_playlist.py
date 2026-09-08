"""Reconcile the local 'known playlist contents' cache with the real Spotify playlist.

Run this after manually adding/removing tracks in Spotify, so sync_playlist.py's
diff (add/remove) stays accurate without needing to re-read the playlist every run.
"""
import argparse
import os

from dotenv import load_dotenv

import db
from stations import STATIONS
from sync_playlist import get_or_create_playlist, get_spotify_client


def get_playlist_track_uris(sp, playlist_id: str) -> set[str]:
    uris = set()
    results = sp.playlist_items(playlist_id, fields="items.track.uri,next", additional_types=["track"])
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("uri"):
                uris.add(track["uri"])
        results = sp.next(results) if results.get("next") else None
    return uris


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
    sp = get_spotify_client()
    playlist_id = get_or_create_playlist(sp, conn, station_slug, playlist_name)

    actual_uris = get_playlist_track_uris(sp, playlist_id)
    known_uris = db.get_playlist_tracks(conn, station_slug)

    added_manually = actual_uris - known_uris
    removed_manually = known_uris - actual_uris

    db.replace_playlist_tracks(conn, station_slug, list(actual_uris))
    db.add_to_blocklist(conn, station_slug, list(removed_manually))

    print(f"Playlist '{playlist_name}' has {len(actual_uris)} tracks on Spotify.")
    print(f"  {len(added_manually)} tracks were added outside the script")
    print(f"  {len(removed_manually)} tracks were removed outside the script "
          f"(added to blocklist, won't be re-added)")
    print("Local cache is now back in sync with Spotify.")


if __name__ == "__main__":
    main()
