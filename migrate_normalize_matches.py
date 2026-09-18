"""One-off migration: move the old flat spotify_matches table into the
normalized artists/tracks/track_artists/track_match schema.

Fetches real (title, artist-list) data from Spotify per already-matched URI
rather than reusing our own search-query text, so multi-artist tracks end up
correctly split across track_artists instead of carrying the "Artist A &
Artist B" credit string we happened to search with. One call per track (not
batched): GET /v1/tracks?ids=... (batch) returns 403 for Development Mode
apps, even though GET /v1/tracks/{id} (single) works fine — confirmed by
testing both against the same ID.

Safe to re-run: existing tracks/artists are looked up before being created.
Run once, then (after checking the printed counts look sane) drop the old
spotify_matches table by hand.
"""
import os

import spotipy
from dotenv import load_dotenv

import db
from retry import call_with_retry, RateLimited
from sync_playlist import get_spotify_client


def main():
    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    rows = conn.execute("SELECT artist, title, spotify_uri FROM spotify_matches").fetchall()
    print(f"{len(rows)} rows in spotify_matches to migrate")

    no_match = [(artist, title) for artist, title, uri in rows if not uri]
    matched = [(artist, title, uri) for artist, title, uri in rows if uri]

    for artist, title in no_match:
        db.save_match(conn, artist, title, None)
    print(f"  {len(no_match)} 'no match' rows carried over as-is")

    unique_uris = sorted({uri for _, _, uri in matched})

    sp = get_spotify_client()
    fetched = {}
    for uri in unique_uris:
        track_id = uri.split(":")[-1]

        def attempt(tid=track_id):
            try:
                return sp.track(tid)
            except spotipy.SpotifyException as e:
                if e.http_status == 429:
                    raise RateLimited(int(e.headers.get("Retry-After", 1)))
                raise
        fetched[uri] = call_with_retry(attempt)

    print(f"  fetched real metadata for {len(fetched)}/{len(unique_uris)} tracks")

    migrated = 0
    for artist, title, uri in matched:
        t = fetched.get(uri)
        track_title = t["name"] if t else title
        artist_names = [a["name"] for a in t["artists"]] if t else [artist]
        db.save_match(conn, artist, title, uri, track_title, artist_names)
        migrated += 1

    print(f"  {migrated} matched rows migrated")
    print(f"  artists: {conn.execute('SELECT COUNT(*) FROM artists').fetchone()[0]}")
    print(f"  tracks: {conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    print(f"  track_artists: {conn.execute('SELECT COUNT(*) FROM track_artists').fetchone()[0]}")
    print(f"  track_match: {conn.execute('SELECT COUNT(*) FROM track_match').fetchone()[0]}")


if __name__ == "__main__":
    main()
