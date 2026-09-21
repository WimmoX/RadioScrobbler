"""Match played tracks to Spotify — and nothing else. Unlike sync_playlist.py,
this never creates or changes a playlist; it only fills the local match
tables (track_match / track_services), so playlists can be designed
separately afterwards (e.g. build_playlist.py).

Tracks are handled most-played first (matching_queue.py: popularity = number
of plays, all history). Spotify's scarce Search budget (quota.py) is spent in
two steps, then ReccoBeats picks up what is left:
  1. new tracks — no id at all yet — up to 80% of the daily budget;
  2. verifying tracks that only have a candidate id (from ReccoBeats or
     Relisten), with whatever budget is left (so an unused part of step 1 is
     not wasted);
  3. ReccoBeats (~2 s per lookup, no Spotify budget) for tracks that still
     have no id, in the same order.
A track played once and not seen for 90 days no longer gets Spotify calls.
Prints a progress line every 200 tracks.

Examples:
    python match_tracks.py                       # all stations, all history
    python match_tracks.py zeilsteen slamnonst   # just these stations
    python match_tracks.py --days 30 --limit 300 # last 30 days, at most 300 lookups
    python match_tracks.py --explain 20          # show what would go first, and why; matches nothing
"""
import argparse
import os
import time
from datetime import datetime

from dotenv import load_dotenv

import db
import matching_queue as mq
import quota
from resolve import Resolver
from stations import STATIONS
from sync_playlist import find_track_match, get_spotify_client

NEW_SHARE = 0.8  # share of the Spotify budget for tracks without any id; the rest verifies candidates


def _run(resolver, items, conn, limit, needs_spotify):
    """Resolve `items` in order and share each outcome with the track's other
    spellings. Stops early when the phase needs Spotify and it stops being
    available (returns True: carry on with the next phase) or when the overall
    lookup limit is reached (returns False: stop everything)."""
    for item in items:
        if limit is not None and resolver.new_lookups >= limit:
            return False
        _, how = resolver.resolve(item.artist, item.title)
        if len(item.variants) > 1 and how != "cached":
            db.copy_match(conn, item.artist, item.title, item.variants)
        if needs_spotify and not resolver.spotify_available:
            break
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stations", nargs="*", metavar="station",
                        help="Station slug(s); default: all. Known: " + ", ".join(STATIONS))
    parser.add_argument("--days", type=int, default=None,
                        help="Only tracks played in the last N days (default: all history)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after this many new lookups (Spotify + ReccoBeats), default: no limit")
    parser.add_argument("--explain", type=int, metavar="N", default=None,
                        help="Only print the first N tracks of each step and why, then exit")
    args = parser.parse_args()
    unknown = [st for st in args.stations if st not in STATIONS]
    if unknown:
        parser.error(f"unknown station(s): {', '.join(unknown)} (known: {', '.join(STATIONS)})")

    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    stations = args.stations or list(STATIONS)
    queue = mq.build_queue(conn, stations, args.days)
    now = datetime.now()
    fresh_new = [i for i in queue if i.state == mq.NEW and not i.expired(now)]
    verify = [i for i in queue if i.state == mq.VERIFY and not i.expired(now)]
    expired = sum(1 for i in queue if i.state != mq.DONE and i.expired(now))
    print(f"{len(queue)} tracks ({', '.join(stations)}; "
          f"{'last ' + str(args.days) + ' days' if args.days else 'all history'}): "
          f"{len(fresh_new)} new, {len(verify)} to verify, "
          f"{sum(1 for i in queue if i.state == mq.DONE)} settled by Spotify; "
          f"{expired} one-off tracks older than 90 days skipped for Spotify", flush=True)

    if args.explain is not None:
        for name, items in (("new", fresh_new), ("verify", verify)):
            print(f"-- first {args.explain} of '{name}':")
            for item in items[:args.explain]:
                print(f"  {item.reason()}: {item.artist} - {item.title}"
                      + (f"  (+{len(item.variants) - 1} other spelling(s))" if len(item.variants) > 1 else ""))
        return

    # Tracks Spotify settled under one spelling: give the other spellings the same answer (no API calls).
    shared = mq.propagate(conn, queue)
    if shared:
        print(f"{shared} other spellings updated from a settled sibling", flush=True)

    sp = get_spotify_client()
    budget = quota.SearchBudget(conn)
    resolver = Resolver(conn, sp, budget, find_track_match, total=0)
    started = time.monotonic()

    # 1. new tracks, Spotify only, up to the new-track share of the budget
    resolver.allow_fallback = False
    budget.set_bucket("new", NEW_SHARE)
    resolver.start_phase("1. new tracks via Spotify", len(fresh_new))
    keep_going = _run(resolver, fresh_new, conn, args.limit, needs_spotify=True)

    # 2. verify candidates with the rest of the Spotify budget (the bucket has no cap of its own)
    if keep_going and not budget.blocked and not budget.exhausted():
        resolver.spotify_available = True
        budget.set_bucket("verify", None)
        resolver.start_phase("2. verifying candidates via Spotify", len(verify))
        keep_going = _run(resolver, verify, conn, args.limit, needs_spotify=True)

    # 3. ReccoBeats for tracks that still have no id (Spotify's budget is not touched)
    if keep_going:
        pending = [i for i in mq.build_queue(conn, stations, args.days) if i.state == mq.NEW]
        resolver.spotify_available = False
        resolver.allow_fallback = True
        resolver.start_phase("3. remaining new tracks via ReccoBeats", len(pending))
        _run(resolver, pending, conn, args.limit, needs_spotify=False)
    budget.finish()

    elapsed = int(time.monotonic() - started)
    print(f"Done in {elapsed // 60}m{elapsed % 60:02d}s: {resolver.new_lookups} new lookups "
          f"({resolver.spotify_lookups} Spotify, {resolver.reccobeats_lookups} ReccoBeats)")
    if budget.blocked or budget.exhausted():
        print(f"Spotify Search budget used up for now (limit={budget.limit}/24h, "
              f"blocked_until={budget.blocked_until}).")
    verified, unverified = conn.execute(
        "SELECT COALESCE(SUM(verified), 0), COALESCE(SUM(1 - verified), 0) "
        "FROM track_services WHERE service = 'spotify'").fetchone()
    print(f"Overall: {verified} confirmed by Spotify, {unverified} candidates awaiting Spotify.")


if __name__ == "__main__":
    main()
