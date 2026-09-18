"""Build/update a Spotify playlist from the top-played tracks in a specific
day/daypart window — e.g. "vrijdagavond" or a "weekend energy" combo.

Deliberately prints only a short summary to stdout, not the full per-track
list: this is meant to be run standalone (or by an LLM-driven workflow)
without the whole track dump needing to pass through it. Full detail goes
to a log file under logs/ instead.

Examples:
    # vrijdagavond top 30
    python build_playlist.py "RadioScrobbler - Vrijdagavond" --daynr 5 --daypart 3

    # weekend energy: vrijdagavond + heel zaterdag
    python build_playlist.py "Weekend Energy" --daynr 5 --daypart 3 --daynr 6
"""
import argparse
import os
import re
from datetime import datetime

from dotenv import load_dotenv

import db
from sync_playlist import filter_liked, find_track_uri, get_or_create_playlist, get_spotify_client, _batched

LOG_DIR = "logs"


def top_tracks(conn, daynrs, dayparts, exclude_stations, limit):
    where, params = [], []
    if daynrs:
        where.append(f"daynr IN ({','.join('?' * len(daynrs))})")
        params.extend(daynrs)
    if dayparts:
        where.append(f"daypart IN ({','.join('?' * len(dayparts))})")
        params.extend(dayparts)
    for station in exclude_stations:
        where.append("station_slug != ?")
        params.append(station)
    where_clause = " AND ".join(where) if where else "1=1"

    query = f"""
        SELECT artist, title, COUNT(*) as plays FROM plays
        WHERE {where_clause}
        GROUP BY artist, title
        ORDER BY plays DESC
        LIMIT ?
    """
    return conn.execute(query, [*params, limit]).fetchall()


def _slugify(name: str) -> str:
    return "dp_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("playlist_name")
    parser.add_argument("--daynr", type=int, action="append", default=[],
                         help="ma=1..zo=7, herhaalbaar (OR). Leeg = alle dagen.")
    parser.add_argument("--daypart", type=int, action="append", default=[],
                         help="0=nacht 1=ochtend 2=middag 3=avond, herhaalbaar (OR). Leeg = hele dag.")
    parser.add_argument("--exclude-station", action="append", default=[],
                         help="Zender-ID uitsluiten, herhaalbaar")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--playlist-key", default=None,
                         help="Lokale sleutel om deze playlist te tracken (default: afgeleid van de naam)")
    args = parser.parse_args()

    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)
    playlist_key = args.playlist_key or _slugify(args.playlist_name)

    tracks = top_tracks(conn, args.daynr, args.daypart, args.exclude_station, args.top)

    sp = get_spotify_client()
    log_lines = [f"Kandidaten: {len(tracks)} (daynr={args.daynr or 'alle'}, "
                 f"daypart={args.daypart or 'alle'}, exclude={args.exclude_station})"]

    desired_uris = set()
    new_lookups = 0
    for artist, title, plays in tracks:
        is_cached, uri = db.get_cached_match(conn, artist, title)
        if not is_cached:
            uri = find_track_uri(sp, artist, title)
            db.save_match(conn, artist, title, uri)
            new_lookups += 1
        if uri:
            desired_uris.add(uri)
        log_lines.append(f"{'OK  ' if uri else 'MISS'}  {plays:3}x  {artist} - {title}" +
                          (f"  -> {uri}" if uri else ""))

    known_uris = db.get_playlist_tracks(conn, playlist_key)
    blocked_uris = db.get_blocklist(conn, playlist_key)
    to_add = list(desired_uris - known_uris - blocked_uris)
    remove_candidates = list(known_uris - desired_uris)
    liked_uris = filter_liked(sp, remove_candidates) if remove_candidates else set()
    to_remove = [uri for uri in remove_candidates if uri not in liked_uris]

    playlist_id = get_or_create_playlist(sp, conn, playlist_key, args.playlist_name)
    for batch in _batched(to_remove):
        sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
    for batch in _batched(to_add):
        sp.playlist_add_items(playlist_id, batch)
    db.remove_playlist_tracks(conn, playlist_key, to_remove)
    db.add_playlist_tracks(conn, playlist_key, to_add)

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{playlist_key}_{datetime.now():%Y%m%d_%H%M%S}.log")
    with open(log_path, "w") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"Kandidaten: {len(tracks)}, gematcht: {len(desired_uris)} ({new_lookups} nieuwe lookups)")
    print(f"Playlist '{args.playlist_name}': +{len(to_add)} -{len(to_remove)} (nu {len(desired_uris)} totaal)")
    print(f"Link: https://open.spotify.com/playlist/{playlist_id}")
    print(f"Details per nummer: {log_path}")


if __name__ == "__main__":
    main()
