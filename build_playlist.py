"""Build/update a Spotify playlist from the top-played tracks in a specific
day/daypart window — e.g. "vrijdagavond" or a "weekend energy" combo.

Deliberately prints only a short summary to stdout, not the full per-track
list: this is meant to be run standalone (or by an LLM-driven workflow)
without the whole track dump needing to pass through it. Full detail goes
to a log file under logs/ instead.

--top N means N tracks *with a Spotify id*: a track that can't be added because
it has none is skipped and the next one down the ranking takes its place, so
the playlist really gets N tracks (scanning at most N x LOOKAHEAD candidates).

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
import quota
from resolve import Resolver
from sync_playlist import filter_liked, find_track_match, get_or_create_playlist, get_spotify_client, _batched

LOG_DIR = "logs"
LOOKAHEAD = 5   # look at most this many times --top candidates for tracks that have an id


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


def pick_tracks(resolver, candidates, top, log_lines=None):
    """Walk `candidates` (artist, title, plays), most played first, and keep the
    ones that resolve to a Spotify id until `top` distinct ones are found.
    Returns (uris in ranking order, number of candidates looked at)."""
    notes = {"cached": " (cached)", "reccobeats": "  (via ReccoBeats)",
             "candidate": "  (ReccoBeats-kandidaat, nog niet door Spotify bevestigd)",
             "untried": "  (niet geprobeerd: Spotify niet beschikbaar)"}
    uris, scanned = [], 0
    for artist, title, plays in candidates:
        if len(uris) >= top:
            break
        scanned += 1
        uri, how = resolver.resolve(artist, title)
        if uri and uri not in uris:
            uris.append(uri)
        if log_lines is not None:
            log_lines.append(f"{'OK  ' if uri else 'MISS'}  {plays:3}x  {artist} - {title}" +
                             (f"  -> {uri}" if uri and how != "cached" else "") + notes.get(how, ""))
    return uris, scanned


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

    candidates = top_tracks(conn, args.daynr, args.daypart, args.exclude_station, args.top * LOOKAHEAD)

    sp = get_spotify_client()
    budget = quota.SearchBudget(conn)
    resolver = Resolver(conn, sp, budget, find_track_match, total=len(candidates))
    log_lines = [f"Kandidaten: {len(candidates)} (daynr={args.daynr or 'alle'}, "
                 f"daypart={args.daypart or 'alle'}, exclude={args.exclude_station}), "
                 f"gezocht naar {args.top} nummers met een Spotify-id"]

    ordered_uris, scanned = pick_tracks(resolver, candidates, args.top, log_lines)
    desired_uris = set(ordered_uris)
    budget.finish()
    if not resolver.spotify_available:
        log_lines.append(f"Spotify Search unavailable for the rest of this run "
                          f"(limit={budget.limit}/24h, blocked_until={budget.blocked_until}); "
                          f"{resolver.reccobeats_lookups} tracks looked up via ReccoBeats instead")

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

    print(f"Gewenst: {args.top}, gevonden: {len(desired_uris)} (na {scanned} nummers bekeken; {resolver.new_lookups} nieuwe lookups: "
          f"{resolver.spotify_lookups} Spotify, {resolver.reccobeats_lookups} ReccoBeats)")
    if not resolver.spotify_available:
        print(f"Spotify Search unavailable this run — used ReccoBeats where possible; "
              f"unverified matches will retry Spotify next run.")
    print(f"Playlist '{args.playlist_name}': +{len(to_add)} -{len(to_remove)} (nu {len(desired_uris)} totaal)")
    print(f"Link: https://open.spotify.com/playlist/{playlist_id}")
    print(f"Details per nummer: {log_path}")


if __name__ == "__main__":
    main()
