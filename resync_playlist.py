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


def get_playlist_tracks(sp, playlist_id: str) -> dict[str, tuple[str, list[str]]]:
    """{uri: (title, artist names)} — the name/artists are needed to register
    tracks added outside the script, since the local tables are keyed on tracks."""
    tracks = {}
    results = sp.playlist_items(
        playlist_id, fields="items.item(uri,name,artists.name),next", additional_types=["track"],
    )
    while results:
        for item in results["items"]:
            # Spotify's Feb 2026 migration renamed this field from "track" to "item"
            # (see SpotifyAPI.md) — reading "track" gives None, i.e. an empty
            # playlist, which would blocklist every known track.
            track = item.get("item")
            if track and track.get("uri"):
                tracks[track["uri"]] = (track["name"], [a["name"] for a in track.get("artists", [])])
        results = sp.next(results) if results.get("next") else None
    return tracks


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

    actual_tracks = get_playlist_tracks(sp, playlist_id)
    actual_uris = set(actual_tracks)
    known_uris = db.get_playlist_tracks(conn, station_slug)

    added_manually = actual_uris - known_uris
    removed_manually = known_uris - actual_uris

    for uri, (title, artist_names) in actual_tracks.items():
        db.get_or_create_track(conn, uri, title, artist_names)
    conn.commit()
    db.replace_playlist_tracks(conn, station_slug, list(actual_uris))
    db.add_to_blocklist(conn, station_slug, list(removed_manually))

    print(f"Playlist '{playlist_name}' has {len(actual_uris)} tracks on Spotify.")
    print(f"  {len(added_manually)} tracks were added outside the script")
    print(f"  {len(removed_manually)} tracks were removed outside the script "
          f"(added to blocklist, won't be re-added)")
    print("Local cache is now back in sync with Spotify.")


if __name__ == "__main__":
    main()
