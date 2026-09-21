"""Turn relisten.nl song ids into candidate Spotify matches — without using
Spotify at all (relisten's /out redirect, see relisten.spotify_id_for()), so
this costs no Spotify quota. Only tracks that were never attempted on Spotify
are looked at; scrape.py stores the song ids while it scrapes relisten.

A relisten link is only relisten's word for it (~93% right in a first check,
and it sometimes has none), so the match is stored *unverified*
(source='relisten', see track_services.verified). match_tracks.py /
sync_playlist.py later let Spotify's own Search confirm or replace it whenever
its budget allows; until then it stays a candidate.

Usage: python match_relisten.py [--limit N]
"""
import argparse
import os
import time

from dotenv import load_dotenv

import db
import matching_queue as mq
import relisten

DEFAULT_LIMIT = 500
PROGRESS_EVERY = 200
REQUEST_DELAY = 0.5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max number of songs to resolve this run (default {DEFAULT_LIMIT})")
    args = parser.parse_args()

    load_dotenv()
    conn = db.connect(os.environ.get("DB_PATH", "data/radioscrobbler.db"))
    # Most played first (matching_queue.py); the limit cuts off the least played.
    songs = mq.sort_by_popularity(conn, db.get_unresolved_relisten_songs(conn), key=lambda s: (s[0], s[1]))[:args.limit]
    print(f"{len(songs)} relisten songs to resolve", flush=True)

    started = time.monotonic()
    matched = no_link = 0
    for i, (artist, title, song_id) in enumerate(songs, 1):
        spotify_id = relisten.spotify_id_for(song_id)
        db.save_relisten_spotify_id(conn, artist, title, spotify_id)
        if spotify_id:
            db.save_match(conn, artist, title, f"spotify:track:{spotify_id}",
                          track_title=title, artist_names=[artist], source="relisten")
            matched += 1
        else:
            no_link += 1
        if i % PROGRESS_EVERY == 0:
            elapsed = int(time.monotonic() - started)
            print(f"  ...{i}/{len(songs)} ({matched} candidates), {elapsed // 60}m{elapsed % 60:02d}s", flush=True)
        time.sleep(REQUEST_DELAY)

    elapsed = int(time.monotonic() - started)
    print(f"Done in {elapsed // 60}m{elapsed % 60:02d}s: {matched} candidate matches stored, "
          f"{no_link} without a Spotify link on relisten.")


if __name__ == "__main__":
    main()
