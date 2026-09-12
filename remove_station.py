"""Remove a station from the system: config, scraped history, and any
tracks that came exclusively from it in playlists we're tracking.

Tracks that also aired on another (remaining) station are left in place —
only tracks that would have no reason to be there anymore get removed.

Usage: python remove_station.py <station-id>
"""
import argparse
import os
import re

from dotenv import load_dotenv

import db
from sync_playlist import get_spotify_client, get_or_create_playlist

STATIONS_FILE = "stations.py"


def remove_from_config(station: str) -> bool:
    with open(STATIONS_FILE) as f:
        content = f.read()

    pattern = rf'^\s*"{re.escape(station)}":\s*".*?",\n'
    new_content = re.sub(pattern, "", content, count=1, flags=re.MULTILINE)

    if new_content == content:
        return False

    with open(STATIONS_FILE, "w") as f:
        f.write(new_content)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("station", help="Station-ID om te verwijderen, bv. 'kink' of 'uk/bbcradio6'")
    parser.add_argument("--keep-playlist-tracks", action="store_true",
                         help="Laat tracks in playlists staan; verwijder alleen uit config en scrape-historie")
    args = parser.parse_args()
    station = args.station

    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    play_count = conn.execute(
        "SELECT COUNT(*) FROM plays WHERE station_slug = ?", (station,)
    ).fetchone()[0]
    print(f"'{station}': {play_count} plays in de database")

    if not args.keep_playlist_tracks and play_count:
        sp = get_spotify_client()
        for playlist_key in db.playlist_keys(conn):
            tracks = db.get_playlist_tracks(conn, playlist_key)
            exclusive = []
            for uri in tracks:
                row = conn.execute(
                    "SELECT artist, title FROM spotify_matches WHERE spotify_uri = ?", (uri,)
                ).fetchone()
                if not row:
                    continue
                artist, title = row
                if db.track_source_stations(conn, artist, title) == [station]:
                    exclusive.append(uri)

            if exclusive:
                playlist_id = db.get_playlist_id(conn, playlist_key)
                if playlist_id:
                    sp.playlist_remove_all_occurrences_of_items(playlist_id, exclusive)
                db.remove_playlist_tracks(conn, playlist_key, exclusive)
                print(f"  {len(exclusive)} nummers verwijderd uit playlist '{playlist_key}' "
                      f"(kwamen alleen van '{station}')")

    removed = db.delete_station_plays(conn, station)
    print(f"  {removed} plays verwijderd uit de database")

    if remove_from_config(station):
        print(f"  '{station}' verwijderd uit {STATIONS_FILE}")
    else:
        print(f"  (geen regel voor '{station}' gevonden in {STATIONS_FILE} — misschien al weg)")

    own_playlist_id = db.get_playlist_id(conn, station)
    if own_playlist_id:
        print(f"  Let op: er is nog een eigen Spotify-playlist gekoppeld aan '{station}' "
              f"(id {own_playlist_id}) — die krijgt geen nieuwe tracks meer, maar is niet "
              f"verwijderd. Doe dat zelf in Spotify als je 'm niet meer wil.")


if __name__ == "__main__":
    main()
