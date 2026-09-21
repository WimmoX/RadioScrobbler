"""Match played tracks to Spotify — and nothing else. Unlike sync_playlist.py,
this never creates or changes a playlist; it only fills the local match
tables (track_match / track_services), so playlists can be designed
separately afterwards (e.g. build_playlist.py).

Uses the same source order as sync_playlist.py (see resolve.py): cache, then
Spotify's own Search while its self-calibrating budget lasts (quota.py), then
ReccoBeats (~2 s per lookup, no quota of its own observed). Tracks that
Spotify hasn't confirmed yet (unverified ReccoBeats candidates) are retried on
Spotify whenever its budget allows. Prints a progress line every 200 tracks.

Examples:
    python match_tracks.py                       # all stations, all history
    python match_tracks.py zeilsteen slamnonst   # just these stations
    python match_tracks.py --days 30 --limit 300 # last 30 days, at most 300 lookups
"""
import argparse
import os
import time

from dotenv import load_dotenv

import db
import quota
from resolve import Resolver
from stations import STATIONS
from sync_playlist import find_track_match, get_spotify_client


def get_tracks(conn, stations, days):
    marks = ",".join("?" * len(stations))
    # Most played first: Spotify's budget is small (see quota.py), so it goes
    # to the tracks that weigh most in a "top N" playlist.
    sql = f"SELECT artist, title FROM plays WHERE station_slug IN ({marks})"
    params = list(stations)
    if days:
        sql += " AND played_at >= datetime('now', 'localtime', ?)"
        params.append(f"-{days} days")
    sql += " GROUP BY LOWER(artist), LOWER(title) ORDER BY COUNT(*) DESC"
    return conn.execute(sql, params).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stations", nargs="*", metavar="station",
                        help="Station slug(s); default: all. Known: " + ", ".join(STATIONS))
    parser.add_argument("--days", type=int, default=None,
                        help="Only tracks played in the last N days (default: all history)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after this many new lookups (Spotify + ReccoBeats), default: no limit")
    args = parser.parse_args()
    unknown = [st for st in args.stations if st not in STATIONS]
    if unknown:
        parser.error(f"unknown station(s): {', '.join(unknown)} (known: {', '.join(STATIONS)})")

    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    stations = args.stations or list(STATIONS)
    tracks = get_tracks(conn, stations, args.days)
    print(f"{len(tracks)} unique tracks ({', '.join(stations)}; "
          f"{'last ' + str(args.days) + ' days' if args.days else 'all history'})", flush=True)

    sp = get_spotify_client()
    budget = quota.SearchBudget(conn)
    resolver = Resolver(conn, sp, budget, find_track_match, total=len(tracks))

    started = time.monotonic()
    for artist, title in tracks:
        if args.limit is not None and resolver.new_lookups >= args.limit:
            break
        resolver.resolve(artist, title)
    budget.finish()

    elapsed = int(time.monotonic() - started)
    print(f"Done in {elapsed // 60}m{elapsed % 60:02d}s: {resolver.processed}/{len(tracks)} tracks handled, "
          f"{resolver.new_lookups} new lookups ({resolver.spotify_lookups} Spotify, "
          f"{resolver.reccobeats_lookups} ReccoBeats)")
    if not resolver.spotify_available:
        print(f"Spotify Search budget used up for now (limit={budget.limit}/24h, "
              f"blocked_until={budget.blocked_until}); ReccoBeats covered the rest.")
    verified, unverified = conn.execute(
        "SELECT COALESCE(SUM(verified), 0), COALESCE(SUM(1 - verified), 0) "
        "FROM track_services WHERE service = 'spotify'").fetchone()
    print(f"Overall: {verified} confirmed by Spotify, {unverified} ReccoBeats candidates awaiting Spotify.")


if __name__ == "__main__":
    main()
