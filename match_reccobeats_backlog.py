"""Proactively match never-attempted (artist, title) pairs against ReccoBeats
— not just as a Spotify-outage fallback, but to make progress on the backlog
without touching the Spotify quota budget at all (ReccoBeats has shown no
sign of a daily limit so far).

Matches are saved with source='reccobeats' and, per db.get_cached_match(),
count as not-yet-fully-cached — a later sync_playlist.py/build_playlist.py
run will still retry them against Spotify and silently upgrade the source
on success. A ReccoBeats *miss* is remembered too, but only as a claim about
ReccoBeats (track_match, service='reccobeats', no track): it stops this script
from asking ReccoBeats about the same track every run (issue #3), and says
nothing about Spotify — so it is NOT a Spotify "no match", and Spotify's own
Search still gets to make that final call (ReccoBeats' search is comparatively
unreliable, see RECCOBEATS.md).

Usage: python match_reccobeats_backlog.py [--limit N]
"""
import argparse
import os
import time

from dotenv import load_dotenv

import db
import reccobeats

DEFAULT_LIMIT = 300
PROGRESS_EVERY = 100


def get_unattempted(conn, limit):
    return conn.execute("""
        SELECT DISTINCT p.artist, p.title
        FROM plays p
        LEFT JOIN track_match tm
            ON tm.artist_text = LOWER(p.artist) AND tm.title_text = LOWER(p.title)
            AND tm.service IN ('spotify', 'reccobeats')
        WHERE tm.artist_text IS NULL
        LIMIT ?
    """, (limit,)).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                         help=f"Max aantal nog nooit geprobeerde nummers deze run (default {DEFAULT_LIMIT})")
    args = parser.parse_args()

    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    rows = get_unattempted(conn, args.limit)
    print(f"{len(rows)} nog nooit geprobeerde nummers, matchen via ReccoBeats...")

    matched = 0
    missed = 0
    started = time.monotonic()
    for i, (artist, title) in enumerate(rows, 1):
        if i % PROGRESS_EVERY == 0:
            elapsed = int(time.monotonic() - started)
            print(f"  ...{i}/{len(rows)} ({matched} gematcht), {elapsed // 60}m{elapsed % 60:02d}s", flush=True)
        match = reccobeats.search_track(artist, title)
        if match:
            db.save_match(conn, artist, title, match["uri"], match["name"],
                          [a["name"] for a in match["artists"]],
                          source="reccobeats", reccobeats_id=match["reccobeats_id"],
                          isrc=match["isrc"])
            matched += 1
        else:
            # Onthouden als ReccoBeats-miss (service='reccobeats'): voorkomt dat
            # dit script 'm elke run opnieuw vraagt, en zegt niets over Spotify,
            # dus een latere Spotify-run kan 'm nog gewoon proberen.
            db.save_match(conn, artist, title, None, service="reccobeats")
            missed += 1

    print(f"Klaar: {matched} gematcht via ReccoBeats, {missed} niet gevonden "
          f"(onthouden als ReccoBeats-miss; Spotify mag ze later nog steeds proberen).")


if __name__ == "__main__":
    main()
