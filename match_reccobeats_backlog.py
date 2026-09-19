"""Proactively match never-attempted (artist, title) pairs against ReccoBeats
— not just as a Spotify-outage fallback, but to make progress on the backlog
without touching the Spotify quota budget at all (ReccoBeats has shown no
sign of a daily limit so far).

Matches are saved with source='reccobeats' and, per db.get_cached_match(),
count as not-yet-fully-cached — a later sync_playlist.py/build_playlist.py
run will still retry them against Spotify and silently upgrade the source
on success. A ReccoBeats *miss* is deliberately NOT recorded as "no match"
(unlike a Spotify miss): only Spotify's own Search gets to make that final
call, since ReccoBeats' search is comparatively unreliable (see
RECCOBEATS.md) and a false "confirmed no match" would block Spotify from
ever trying.

Usage: python match_reccobeats_backlog.py [--limit N]
"""
import argparse
import os

from dotenv import load_dotenv

import db
import reccobeats

DEFAULT_LIMIT = 300


def get_unattempted(conn, limit):
    return conn.execute("""
        SELECT DISTINCT p.artist, p.title
        FROM plays p
        LEFT JOIN track_match tm
            ON tm.artist_text = LOWER(p.artist) AND tm.title_text = LOWER(p.title)
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
    for artist, title in rows:
        match = reccobeats.search_track(artist, title)
        if match:
            db.save_match(conn, artist, title, match["uri"], match["name"],
                          [a["name"] for a in match["artists"]],
                          source="reccobeats", reccobeats_id=match["reccobeats_id"])
            matched += 1
        else:
            # Bewust NIET opslaan als "geen match" — laat 'm onaangeraakt
            # zodat een latere Spotify-run 'm nog gewoon kan proberen.
            missed += 1

    print(f"Klaar: {matched} gematcht via ReccoBeats, {missed} niet gevonden "
          f"(blijven beschikbaar voor een latere Spotify-poging).")


if __name__ == "__main__":
    main()
