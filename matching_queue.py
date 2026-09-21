"""The order in which tracks get matched — one shared ordering for every
script that matches (match_tracks.py, sync_playlist.py, match_relisten.py,
match_reccobeats_backlog.py), so resources go to what matters most.

Popularity = how many times a track has been played on the radio, +1 per
play, all stations, all history (it never resets). `plays` is never pruned and
already de-duplicates on insert (db.save_plays), so counting its rows *is* the
counter: nothing to keep in sync and nothing that can double count. Priority =
popularity first, most recently played as tie-breaker. No opaque score.

The same track spelled two ways (accents, "&" vs ",", "Ft.", version tags in
brackets) is ONE queue item: it is looked up once and the outcome is shared
with the other spellings (db.copy_match).

A track played only once, more than 90 days ago, is no longer offered to
Spotify's scarce Search budget — a filter on the queue, not a deletion; the
plays history stays. See Backlogitems1.md, RS-MATCH-01.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import db

NEW, VERIFY, DONE = "new", "verify", "done"
ONE_OFF_EXPIRY = timedelta(days=90)


@dataclass
class QueueItem:
    artist: str            # the most played spelling: what we actually look up
    title: str
    variants: list[tuple[str, str]]
    play_count: int        # popularity
    first_seen: datetime
    last_seen: datetime
    state: str             # NEW: no Spotify answer yet · VERIFY: only a candidate id · DONE: Spotify has spoken
    best_variant: tuple[str, str] | None = field(default=None, repr=False)  # a DONE variant to share from

    def expired(self, now: datetime) -> bool:
        """Played once, long ago: not worth a Spotify Search call any more."""
        return self.play_count <= 1 and now - self.last_seen > ONE_OFF_EXPIRY

    def reason(self) -> str:
        return f"{self.play_count} plays, last {self.last_seen:%Y-%m-%d}, {self.state}"


def track_key(artist: str, title: str) -> tuple[str, str]:
    return db.artist_key(artist), db.title_key(title)


def _spotify_status(conn) -> dict[tuple[str, str], str]:
    """(artist_text, title_text) -> 'verified' | 'nomatch' | 'candidate' for every
    text Spotify has an entry for."""
    status = {}
    for artist_text, title_text, track_id, verified in conn.execute("""
        SELECT tm.artist_text, tm.title_text, tm.track_id, ts.verified
        FROM track_match tm
        LEFT JOIN track_services ts ON ts.track_id = tm.track_id AND ts.service = 'spotify'
        WHERE tm.service = 'spotify'
    """):
        if track_id is None:
            status[(artist_text, title_text)] = "nomatch"
        else:
            status[(artist_text, title_text)] = "verified" if verified else "candidate"
    return status


def build_queue(conn, stations=None, days: int | None = None) -> list[QueueItem]:
    """Every track (normalised spelling) as a QueueItem, highest priority first.

    Popularity always counts all stations and all history; `stations`/`days`
    only limit which tracks are on offer (a track played on one of those
    stations within those days)."""
    counts = {}
    for artist, title, n, first, last in conn.execute(
        "SELECT artist, title, COUNT(*), MIN(played_at), MAX(played_at) FROM plays GROUP BY artist, title"
    ):
        counts[(artist, title)] = (n, datetime.fromisoformat(first), datetime.fromisoformat(last))

    in_scope = None
    if stations is not None or days is not None:
        sql, params = "SELECT DISTINCT artist, title FROM plays WHERE 1 = 1", []
        if stations:
            sql += f" AND station_slug IN ({','.join('?' * len(stations))})"
            params += list(stations)
        if days:
            sql += " AND played_at >= datetime('now', 'localtime', ?)"
            params.append(f"-{days} days")
        in_scope = {(a, t) for a, t in conn.execute(sql, params)}

    groups: dict[tuple[str, str], list] = {}
    for variant, stats in counts.items():
        groups.setdefault(track_key(*variant), []).append((variant, stats))

    status = _spotify_status(conn)
    items = []
    for members in groups.values():
        if in_scope is not None and not any(v in in_scope for v, _ in members):
            continue
        members.sort(key=lambda m: (-m[1][0], m[0]))           # most played spelling first
        variants = [v for v, _ in members]
        play_count = sum(s[0] for _, s in members)
        first_seen = min(s[1] for _, s in members)
        last_seen = max(s[2] for _, s in members)

        by_status = {v: status.get((v[0].lower(), v[1].lower())) for v in variants}
        best = next((v for v in variants if by_status[v] == "verified"), None) \
            or next((v for v in variants if by_status[v] == "nomatch"), None)
        if best:
            state = DONE
        elif any(st == "candidate" for st in by_status.values()):
            state = VERIFY
        else:
            state = NEW
        items.append(QueueItem(variants[0][0], variants[0][1], variants, play_count,
                               first_seen, last_seen, state, best))

    items.sort(key=lambda i: (-i.play_count, -i.last_seen.timestamp(), i.artist.lower(), i.title.lower()))
    return items


def propagate(conn, items) -> int:
    """For tracks Spotify already settled under one spelling, give the other
    spellings the same answer (no API calls). Returns rows written."""
    written = 0
    for item in items:
        if item.state == DONE and len(item.variants) > 1 and item.best_variant:
            written += db.copy_match(conn, *item.best_variant, item.variants)
    return written


def popularity(conn) -> dict[tuple[str, str], int]:
    """Play count per normalised track key — for scripts that only need to sort."""
    counts: dict[tuple[str, str], int] = {}
    for artist, title, n in conn.execute("SELECT artist, title, COUNT(*) FROM plays GROUP BY artist, title"):
        key = track_key(artist, title)
        counts[key] = counts.get(key, 0) + n
    return counts


def sort_by_popularity(conn, pairs, key=lambda p: p):
    """`pairs` sorted most played first. `key` extracts (artist, title) from an element."""
    counts = popularity(conn)
    return sorted(pairs, key=lambda p: -counts.get(track_key(*key(p)), 0))
