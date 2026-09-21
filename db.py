"""Local SQLite storage for played tracks and cached Spotify matches."""
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta

AUDIO_FEATURE_COLUMNS = (
    "track_id", "acousticness", "danceability", "energy", "instrumentalness",
    "musical_key", "liveness", "loudness", "mode", "speechiness", "tempo",
    "valence", "fetched_at",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    station_slug TEXT NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    played_at TEXT NOT NULL,
    -- daynr/hr/daypart are derived straight from played_at (never stored
    -- separately), so they can never drift out of sync with it and every
    -- existing row gets a correct value for free, with no backfill script.
    daynr INTEGER GENERATED ALWAYS AS (CAST(strftime('%u', played_at) AS INTEGER)) VIRTUAL,
    hr INTEGER GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER)) VIRTUAL,
    daypart INTEGER GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER) / 6) VIRTUAL,
    PRIMARY KEY (station_slug, artist, title, played_at)
);

-- Normalized matching layer, replacing the old flat spotify_matches table
-- (still present on existing databases until migrate_normalize_matches.py
-- has been run — see LessonsLearned.md). track_match is the bridge between
-- plays' free-text (artist, title) and a resolved track: plays itself stays
-- plain text (it's an immutable scrape log, human-readable by construction,
-- and deliberately not FK'd to a track so scraping never has to wait on
-- matching), but tracks/artists can now represent real multi-artist data
-- instead of us re-splitting a credit string every time.
CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- A track is a music-service-independent thing with its own id; how to find
-- it on Spotify / Apple Music / etc. lives in track_services below.
-- isrc is the international recording code — the one key all services share,
-- so a future second service can link to an existing track instead of
-- creating a duplicate. Not filled everywhere yet.
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    isrc TEXT
);

-- One row per (track, service): that service's own identifier for the track
-- (for Spotify: the full 'spotify:track:...' URI, which is what its API
-- takes). 'reccobeats' is not a listening service but is stored the same
-- way — it is just another place that has its own id for the recording.
-- verified = 1 means the service's *own* search confirmed the match
-- (authoritative). verified = 0 means we only have a candidate id, e.g. a
-- Spotify URI taken from a ReccoBeats search hit while Spotify's own Search
-- was unavailable. get_cached_match() treats unverified rows as not fully
-- cached, so a later run retries the service and upgrades the row.
CREATE TABLE IF NOT EXISTS track_services (
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    service TEXT NOT NULL,
    external_id TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (track_id, service),
    UNIQUE (service, external_id)
);

CREATE TABLE IF NOT EXISTS track_artists (
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    PRIMARY KEY (track_id, artist_id)
);

-- Per service, so "Spotify has no match for this text" (track_id NULL)
-- doesn't stop another service from trying the same text later.
CREATE TABLE IF NOT EXISTS track_match (
    artist_text TEXT NOT NULL,
    title_text TEXT NOT NULL,
    service TEXT NOT NULL DEFAULT 'spotify',
    track_id INTEGER REFERENCES tracks(id),
    matched_at TEXT NOT NULL,
    PRIMARY KEY (artist_text, title_text, service)
);

CREATE TABLE IF NOT EXISTS playlists (
    station_slug TEXT NOT NULL,
    service TEXT NOT NULL DEFAULT 'spotify',
    playlist_id TEXT NOT NULL,
    PRIMARY KEY (station_slug, service)
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    station_slug TEXT NOT NULL,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (station_slug, track_id)
);

CREATE TABLE IF NOT EXISTS blocklist (
    station_slug TEXT NOT NULL,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    blocked_at TEXT NOT NULL,
    PRIMARY KEY (station_slug, track_id)
);

CREATE TABLE IF NOT EXISTS audio_features (
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id),
    acousticness REAL,
    danceability REAL,
    energy REAL,
    instrumentalness REAL,
    musical_key INTEGER,
    liveness REAL,
    loudness REAL,
    mode INTEGER,
    speechiness REAL,
    tempo REAL,
    valence REAL,
    fetched_at TEXT NOT NULL
);

-- relisten.nl's own song id for a played (artist, title) — see relisten.py.
-- spotify_id is what relisten's /out redirect says the Spotify track is; NULL
-- with resolved_at set = relisten has no Spotify link for it (don't ask again).
-- Only relisten's word for it: match_relisten.py stores it as an unverified
-- candidate match, never as a confirmed one.
CREATE TABLE IF NOT EXISTS relisten_songs (
    artist_text TEXT NOT NULL,
    title_text TEXT NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    song_id TEXT NOT NULL,
    spotify_id TEXT,
    resolved_at TEXT,
    PRIMARY KEY (artist_text, title_text)
);

-- Self-calibrating budget for Spotify Search calls (see quota.py). We don't
-- know Spotify's actual Development Mode quota (undocumented, can change),
-- so we discover it empirically: a run that exhausts call_limit without a
-- real block nudges it up; a real QUOTA_EXCEEDED 429 snaps it down to
-- whatever actually succeeded. search_calls is a plain append-only log used
-- to count calls in the trailing 24h window.
CREATE TABLE IF NOT EXISTS search_calls (
    called_at TEXT NOT NULL,
    bucket TEXT
);

CREATE TABLE IF NOT EXISTS search_quota_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    call_limit INTEGER NOT NULL,
    blocked_until TEXT
);
"""

SEARCH_QUOTA_SEED_LIMIT = 300


_MIGRATIONS = {
    "search_calls": {
        "bucket": "ALTER TABLE search_calls ADD COLUMN bucket TEXT",
    },
    "plays": {
        "daynr": "ALTER TABLE plays ADD COLUMN daynr INTEGER "
                 "GENERATED ALWAYS AS (CAST(strftime('%u', played_at) AS INTEGER)) VIRTUAL",
        "hr": "ALTER TABLE plays ADD COLUMN hr INTEGER "
              "GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER)) VIRTUAL",
        "daypart": "ALTER TABLE plays ADD COLUMN daypart INTEGER "
                   "GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER) / 6) VIRTUAL",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to tables that pre-date them. SQLite can't add a STORED
    generated column via ALTER TABLE (only at CREATE TABLE time), so plays'
    daynr/hr/daypart get added as VIRTUAL instead — same values, computed on
    read rather than on write."""
    for table, columns in _MIGRATIONS.items():
        # table_xinfo (not table_info!) is needed here — table_info silently
        # omits generated/virtual columns, which would make this "add if
        # missing" check always think they're missing and crash every
        # subsequent connect() with "duplicate column name".
        existing = {row[1] for row in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()}
        for column, statement in columns.items():
            if column not in existing:
                conn.execute(statement)
    conn.commit()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _migrate_to_track_services(conn: sqlite3.Connection) -> None:
    """One-time move from a Spotify-shaped schema (spotify_uri on tracks,
    playlist_tracks, blocklist and audio_features) to the service-independent
    one (track_services, everything else keyed on tracks.id). Detects the old
    shape itself, so it's a no-op on a fresh or already-migrated database and
    safe to run on every connect().

    Needs a database that already went through migrate_normalize_matches.py
    (i.e. has the tracks table). SQLite can't change a primary key in place,
    so each table is rebuilt: create *_new, copy, drop old, rename. Never
    ALTER TABLE ... RENAME the old `tracks` itself — that would silently
    re-point the foreign keys of track_artists/track_match at the renamed
    table. All of it runs in one transaction and rolls back if any row count
    doesn't add up."""
    if "spotify_uri" not in _columns(conn, "tracks"):
        return

    before = {t: _count(conn, t) for t in
              ("tracks", "playlist_tracks", "blocklist", "audio_features", "track_match", "playlists")}
    conn.execute("BEGIN")
    try:
        conn.execute("""CREATE TABLE track_services (
            track_id INTEGER NOT NULL REFERENCES tracks(id),
            service TEXT NOT NULL,
            external_id TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (track_id, service),
            UNIQUE (service, external_id)
        )""")
        conn.execute("""INSERT INTO track_services (track_id, service, external_id, verified)
            SELECT id, 'spotify', spotify_uri, CASE source WHEN 'spotify' THEN 1 ELSE 0 END FROM tracks""")
        conn.execute("""INSERT INTO track_services (track_id, service, external_id, verified)
            SELECT id, 'reccobeats', reccobeats_id, 1 FROM tracks WHERE reccobeats_id IS NOT NULL""")

        conn.execute("CREATE TABLE tracks_new (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, isrc TEXT)")
        conn.execute("INSERT INTO tracks_new (id, title) SELECT id, title FROM tracks")
        conn.execute("DROP TABLE tracks")
        conn.execute("ALTER TABLE tracks_new RENAME TO tracks")

        conn.execute("""CREATE TABLE track_match_new (
            artist_text TEXT NOT NULL, title_text TEXT NOT NULL,
            service TEXT NOT NULL DEFAULT 'spotify',
            track_id INTEGER REFERENCES tracks(id), matched_at TEXT NOT NULL,
            PRIMARY KEY (artist_text, title_text, service)
        )""")
        conn.execute("""INSERT INTO track_match_new (artist_text, title_text, service, track_id, matched_at)
            SELECT artist_text, title_text, 'spotify', track_id, matched_at FROM track_match""")
        conn.execute("DROP TABLE track_match")
        conn.execute("ALTER TABLE track_match_new RENAME TO track_match")

        conn.execute("""CREATE TABLE playlists_new (
            station_slug TEXT NOT NULL, service TEXT NOT NULL DEFAULT 'spotify',
            playlist_id TEXT NOT NULL, PRIMARY KEY (station_slug, service)
        )""")
        conn.execute("""INSERT INTO playlists_new (station_slug, service, playlist_id)
            SELECT station_slug, 'spotify', spotify_playlist_id FROM playlists""")
        conn.execute("DROP TABLE playlists")
        conn.execute("ALTER TABLE playlists_new RENAME TO playlists")

        for table, date_col in (("playlist_tracks", "added_at"), ("blocklist", "blocked_at")):
            conn.execute(f"""CREATE TABLE {table}_new (
                station_slug TEXT NOT NULL, track_id INTEGER NOT NULL REFERENCES tracks(id),
                {date_col} TEXT NOT NULL, PRIMARY KEY (station_slug, track_id)
            )""")
            conn.execute(f"""INSERT INTO {table}_new (station_slug, track_id, {date_col})
                SELECT o.station_slug, ts.track_id, o.{date_col} FROM {table} o
                JOIN track_services ts ON ts.service = 'spotify' AND ts.external_id = o.spotify_uri""")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

        conn.execute("""CREATE TABLE audio_features_new (
            track_id INTEGER PRIMARY KEY REFERENCES tracks(id),
            acousticness REAL, danceability REAL, energy REAL, instrumentalness REAL,
            musical_key INTEGER, liveness REAL, loudness REAL, mode INTEGER,
            speechiness REAL, tempo REAL, valence REAL, fetched_at TEXT NOT NULL
        )""")
        conn.execute("""INSERT INTO audio_features_new
            SELECT ts.track_id, a.acousticness, a.danceability, a.energy, a.instrumentalness,
                   a.musical_key, a.liveness, a.loudness, a.mode, a.speechiness, a.tempo,
                   a.valence, a.fetched_at
            FROM audio_features a
            JOIN track_services ts ON ts.service = 'spotify' AND ts.external_id = a.spotify_uri""")
        conn.execute("DROP TABLE audio_features")
        conn.execute("ALTER TABLE audio_features_new RENAME TO audio_features")

        after = {t: _count(conn, t) for t in before}
        if after != before:
            raise RuntimeError(f"row counts changed during migration: before={before} after={after}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # Before SCHEMA, so the old-shaped tables are rebuilt instead of being
    # skipped by CREATE TABLE IF NOT EXISTS and left in the wrong shape.
    _migrate_to_track_services(conn)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(
        "INSERT OR IGNORE INTO search_quota_state (id, call_limit, blocked_until) VALUES (1, ?, NULL)",
        (SEARCH_QUOTA_SEED_LIMIT,),
    )
    conn.commit()
    return conn


# Two sources report the same play a minute or two apart (relisten.nl is
# usually 1-2 min ahead of OnlineRadioBox; seen up to 5), so the primary key
# alone can't dedupe across sources. A play with the same title on the same
# station this close to an existing one is the same play.
SAME_PLAY_WINDOW = timedelta(minutes=5)

# The sources also spell the same title differently: accents ("boheme" /
# "bohème"), punctuation, non-breaking spaces, and version tags in brackets
# ("Purple Rain (Short Edit)"). Artists are NOT compared: they differ far more
# ("Adele" / "Adkins, A", "Coolio Ft. L.V." / "Coolio", "One Republic" /
# "OneRepublic"), while two different songs with the same title inside 5
# minutes on one station effectively don't happen.
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_TITLE_IGNORED = {"the", "and"}
_ARTIST_IGNORED = {"the", "and", "ft", "feat", "featuring"}


def _words(text: str, ignored: set[str]) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return " ".join(w for w in re.findall(r"[a-z0-9]+", plain) if w not in ignored)


def title_key(title: str) -> str:
    """Spelling-independent form of a title: no accents, case, punctuation or
    bracketed version tags. Falls back to the unstripped title when brackets
    are all there is ("(Untitled)")."""
    return _words(_BRACKETS.sub(" ", title), _TITLE_IGNORED) or _words(title, _TITLE_IGNORED)


def artist_key(artist: str) -> str:
    """Spelling-independent form of an artist credit ("Ft."/"&"/","/accents/"The")."""
    return _words(artist, _ARTIST_IGNORED)


def save_plays(conn: sqlite3.Connection, station_slug: str, plays) -> int:
    """Store plays; returns how many were new. A play is skipped if the same
    station already has a play with the same title (see title_key) within
    SAME_PLAY_WINDOW — the existing row wins."""
    plays = list(plays)
    if not plays:
        return 0
    lo = min(p.played_at for p in plays) - SAME_PLAY_WINDOW
    hi = max(p.played_at for p in plays) + SAME_PLAY_WINDOW
    seen: dict[str, list[datetime]] = {}
    for artist, title, played_at in conn.execute(
        "SELECT artist, title, played_at FROM plays WHERE station_slug = ? AND played_at BETWEEN ? AND ?",
        (station_slug, lo.isoformat(), hi.isoformat()),
    ):
        seen.setdefault(title_key(title), []).append(datetime.fromisoformat(played_at))

    new_rows = []
    for p in plays:
        times = seen.setdefault(title_key(p.title), [])
        if any(abs(t - p.played_at) <= SAME_PLAY_WINDOW for t in times):
            continue
        times.append(p.played_at)
        new_rows.append((station_slug, p.artist, p.title, p.played_at.isoformat()))
    conn.executemany(
        "INSERT OR IGNORE INTO plays (station_slug, artist, title, played_at) VALUES (?, ?, ?, ?)", new_rows,
    )
    conn.commit()
    return len(new_rows)


def save_relisten_songs(conn: sqlite3.Connection, plays) -> None:
    """Remember relisten's song id per (artist, title) for plays that carry one."""
    conn.executemany(
        "INSERT OR IGNORE INTO relisten_songs (artist_text, title_text, artist, title, song_id) "
        "VALUES (?, ?, ?, ?, ?)",
        [(p.artist.lower(), p.title.lower(), p.artist, p.title, p.source_id) for p in plays if p.source_id],
    )
    conn.commit()


def get_unresolved_relisten_songs(conn: sqlite3.Connection, limit: int | None = None) -> list[tuple[str, str, str]]:
    """(artist, title, song_id) not yet resolved to a Spotify id and never
    attempted on Spotify — the ones a relisten candidate can help."""
    sql = """
        SELECT rs.artist, rs.title, rs.song_id FROM relisten_songs rs
        LEFT JOIN track_match tm ON tm.artist_text = rs.artist_text AND tm.title_text = rs.title_text
                                AND tm.service = 'spotify'
        WHERE rs.resolved_at IS NULL AND tm.artist_text IS NULL
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [tuple(r) for r in conn.execute(sql).fetchall()]


def save_relisten_spotify_id(conn: sqlite3.Connection, artist: str, title: str, spotify_id: str | None) -> None:
    conn.execute(
        "UPDATE relisten_songs SET spotify_id = ?, resolved_at = ? WHERE artist_text = ? AND title_text = ?",
        (spotify_id, datetime.now().isoformat(), artist.lower(), title.lower()),
    )
    conn.commit()


def recent_unique_tracks(conn: sqlite3.Connection, station_slug: str, since: datetime) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT artist, title FROM plays WHERE station_slug = ? AND played_at >= ?",
        (station_slug, since.isoformat()),
    ).fetchall()
    return rows


def get_cached_match(
    conn: sqlite3.Connection, artist: str, title: str, service: str = "spotify",
) -> tuple[bool, str | None]:
    """Return (is_cached, external_id) for this service. external_id is None
    if cached as 'no match found'. A match that is not `verified` (only found
    via ReccoBeats, see track_services) counts as NOT cached, so a later run —
    once the service's own search is available again — retries it there and
    upgrades it on success."""
    row = conn.execute("""
        SELECT tm.track_id, ts.external_id, ts.verified
        FROM track_match tm
        LEFT JOIN track_services ts ON ts.track_id = tm.track_id AND ts.service = tm.service
        WHERE tm.artist_text = ? AND tm.title_text = ? AND tm.service = ?
    """, (artist.lower(), title.lower(), service)).fetchone()
    if row is None:
        return False, None
    track_id, external_id, verified = row
    if track_id is None or external_id is None:
        return True, None
    return bool(verified), external_id


def get_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM artists WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO artists (name) VALUES (?)", (name,)).lastrowid


def _track_id(conn: sqlite3.Connection, service: str, external_id: str) -> int:
    row = conn.execute(
        "SELECT track_id FROM track_services WHERE service = ? AND external_id = ?", (service, external_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"no track known for {service} id {external_id!r}")
    return row[0]


def _track_ids(conn: sqlite3.Connection, service: str, external_ids: list[str]) -> list[int]:
    return [_track_id(conn, service, external_id) for external_id in external_ids]


def _external_ids(conn: sqlite3.Connection, service: str, track_ids: list[int]) -> set[str]:
    """Only tracks that have an id on this service are returned."""
    if not track_ids:
        return set()
    marks = ",".join("?" * len(track_ids))
    rows = conn.execute(
        f"SELECT external_id FROM track_services WHERE service = ? AND track_id IN ({marks})",
        (service, *track_ids),
    ).fetchall()
    return {row[0] for row in rows}


def _link_service(conn: sqlite3.Connection, track_id: int, service: str, external_id: str, verified: bool) -> None:
    """Attach (or upgrade) a track's id on a service. Never downgrades verified -> unverified."""
    conn.execute("""
        INSERT INTO track_services (track_id, service, external_id, verified) VALUES (?, ?, ?, ?)
        ON CONFLICT (track_id, service) DO UPDATE SET verified = MAX(verified, excluded.verified)
    """, (track_id, service, external_id, int(verified)))


def get_or_create_track(
    conn: sqlite3.Connection, external_id: str, title: str, artist_names: list[str],
    service: str = "spotify", source: str = "spotify", reccobeats_id: str | None = None,
    isrc: str | None = None,
) -> int:
    """`service` is whose id `external_id` is; `source` is whose search produced
    the match. They are the same when the service's own search found it
    (verified), and differ when we only have a candidate id from elsewhere
    (source='reccobeats' — unverified until the service itself confirms)."""
    verified = source == service
    row = conn.execute(
        "SELECT track_id FROM track_services WHERE service = ? AND external_id = ?", (service, external_id),
    ).fetchone()
    if row:
        track_id = row[0]
        _link_service(conn, track_id, service, external_id, verified)
        if isrc:
            conn.execute("UPDATE tracks SET isrc = COALESCE(isrc, ?) WHERE id = ?", (isrc, track_id))
    else:
        track_id = conn.execute("INSERT INTO tracks (title, isrc) VALUES (?, ?)", (title, isrc)).lastrowid
        _link_service(conn, track_id, service, external_id, verified)
    if reccobeats_id:
        conn.execute(
            "INSERT OR IGNORE INTO track_services (track_id, service, external_id, verified) VALUES (?, 'reccobeats', ?, 1)",
            (track_id, reccobeats_id),
        )
    for name in artist_names:
        artist_id = get_or_create_artist(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO track_artists (track_id, artist_id) VALUES (?, ?)", (track_id, artist_id),
        )
    return track_id


def save_match(
    conn: sqlite3.Connection, artist: str, title: str,
    external_id: str | None, track_title: str | None = None, artist_names: list[str] | None = None,
    service: str = "spotify", source: str = "spotify", reccobeats_id: str | None = None,
    isrc: str | None = None,
) -> None:
    """`track_title`/`artist_names` are the matched track's own (title, artist
    list) — from the service if source == service, from ReccoBeats if
    source='reccobeats' — pass None/omit when external_id is None (no match
    found anywhere)."""
    track_id = None
    if external_id:
        track_id = get_or_create_track(
            conn, external_id, track_title or title, artist_names or [artist],
            service=service, source=source, reccobeats_id=reccobeats_id, isrc=isrc,
        )
    conn.execute(
        "INSERT OR REPLACE INTO track_match (artist_text, title_text, service, track_id, matched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (artist.lower(), title.lower(), service, track_id, datetime.now().isoformat()),
    )
    conn.commit()


def copy_match(conn: sqlite3.Connection, artist: str, title: str, targets) -> int:
    """Share the outcome for (artist, title) with other spellings of the same
    track (`targets`: iterable of (artist, title)), so a track spelled two ways
    is looked up once. Returns the number of track_match rows written.

    Per service: a target that is already settled for Spotify (verified, or
    Spotify said "no match") keeps its own answer; anything else (no row, or
    only an unconfirmed candidate) takes over the source's. ReccoBeats
    "no match" rows are copied to targets that have no such row."""
    written = 0
    for service in ("spotify", "reccobeats"):
        row = conn.execute(
            "SELECT track_id FROM track_match WHERE artist_text = ? AND title_text = ? AND service = ?",
            (artist.lower(), title.lower(), service),
        ).fetchone()
        if row is None:
            continue
        for t_artist, t_title in targets:
            if (t_artist.lower(), t_title.lower()) == (artist.lower(), title.lower()):
                continue
            if service == "spotify":
                settled, _ = get_cached_match(conn, t_artist, t_title, "spotify")
            else:
                settled = conn.execute(
                    "SELECT 1 FROM track_match WHERE artist_text = ? AND title_text = ? AND service = ?",
                    (t_artist.lower(), t_title.lower(), service),
                ).fetchone() is not None
            if settled:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO track_match (artist_text, title_text, service, track_id, matched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (t_artist.lower(), t_title.lower(), service, row[0], datetime.now().isoformat()),
            )
            written += 1
    conn.commit()
    return written


def get_track_artists(conn: sqlite3.Connection, external_id: str, service: str = "spotify") -> list[str]:
    rows = conn.execute("""
        SELECT a.name FROM track_services ts
        JOIN track_artists ta ON ta.track_id = ts.track_id
        JOIN artists a ON a.id = ta.artist_id
        WHERE ts.service = ? AND ts.external_id = ?
    """, (service, external_id)).fetchall()
    return [row[0] for row in rows]


def get_playlist_id(conn: sqlite3.Connection, station_slug: str, service: str = "spotify") -> str | None:
    row = conn.execute(
        "SELECT playlist_id FROM playlists WHERE station_slug = ? AND service = ?", (station_slug, service),
    ).fetchone()
    return row[0] if row else None


def save_playlist_id(conn: sqlite3.Connection, station_slug: str, playlist_id: str, service: str = "spotify") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO playlists (station_slug, service, playlist_id) VALUES (?, ?, ?)",
        (station_slug, service, playlist_id),
    )
    conn.commit()


# The playlist/blocklist functions below take and return a service's own ids
# (Spotify URIs, today) so callers stay service-flavoured; the tables
# themselves are keyed on tracks.id. Ids must belong to a known track
# (see get_or_create_track) — an unknown id raises KeyError.

def get_playlist_tracks(conn: sqlite3.Connection, station_slug: str, service: str = "spotify") -> set[str]:
    track_ids = [row[0] for row in conn.execute(
        "SELECT track_id FROM playlist_tracks WHERE station_slug = ?", (station_slug,),
    ).fetchall()]
    return _external_ids(conn, service, track_ids)


def add_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str], service: str = "spotify") -> None:
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO playlist_tracks (station_slug, track_id, added_at) VALUES (?, ?, ?)",
        [(station_slug, track_id, now) for track_id in _track_ids(conn, service, uris)],
    )
    conn.commit()


def remove_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str], service: str = "spotify") -> None:
    conn.executemany(
        "DELETE FROM playlist_tracks WHERE station_slug = ? AND track_id = ?",
        [(station_slug, track_id) for track_id in _track_ids(conn, service, uris)],
    )
    conn.commit()


def replace_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str], service: str = "spotify") -> None:
    _track_ids(conn, service, uris)  # resolve first: don't wipe the old contents if one is unknown
    conn.execute("DELETE FROM playlist_tracks WHERE station_slug = ?", (station_slug,))
    add_playlist_tracks(conn, station_slug, uris, service)


def get_blocklist(conn: sqlite3.Connection, station_slug: str, service: str = "spotify") -> set[str]:
    track_ids = [row[0] for row in conn.execute(
        "SELECT track_id FROM blocklist WHERE station_slug = ?", (station_slug,),
    ).fetchall()]
    return _external_ids(conn, service, track_ids)


def add_to_blocklist(conn: sqlite3.Connection, station_slug: str, uris: list[str], service: str = "spotify") -> None:
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO blocklist (station_slug, track_id, blocked_at) VALUES (?, ?, ?)",
        [(station_slug, track_id, now) for track_id in _track_ids(conn, service, uris)],
    )
    conn.commit()


def get_uris_missing_audio_features(conn: sqlite3.Connection, service: str = "spotify") -> list[str]:
    """Ids (on this service) of tracks we've matched, but haven't fetched ReccoBeats features for yet."""
    rows = conn.execute("""
        SELECT ts.external_id FROM track_services ts
        LEFT JOIN audio_features af ON af.track_id = ts.track_id
        WHERE ts.service = ? AND af.track_id IS NULL
    """, (service,)).fetchall()
    return [row[0] for row in rows]


def save_audio_features(
    conn: sqlite3.Connection, external_id: str, features: dict | None, service: str = "spotify",
) -> None:
    """`features` is a ReccoBeats audio-features item, or None if no match was found."""
    f = features or {}
    conn.execute("""
        INSERT OR REPLACE INTO audio_features
        (track_id, acousticness, danceability, energy, instrumentalness, musical_key,
         liveness, loudness, mode, speechiness, tempo, valence, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        _track_id(conn, service, external_id), f.get("acousticness"), f.get("danceability"), f.get("energy"),
        f.get("instrumentalness"), f.get("key"), f.get("liveness"), f.get("loudness"),
        f.get("mode"), f.get("speechiness"), f.get("tempo"), f.get("valence"),
        datetime.now().isoformat(),
    ))
    conn.commit()


def get_audio_features(conn: sqlite3.Connection, external_id: str, service: str = "spotify") -> dict | None:
    row = conn.execute(
        "SELECT * FROM audio_features WHERE track_id = ?", (_track_id(conn, service, external_id),),
    ).fetchone()
    return dict(zip(AUDIO_FEATURE_COLUMNS, row)) if row else None


def delete_station_plays(conn: sqlite3.Connection, station_slug: str) -> int:
    cursor = conn.execute("DELETE FROM plays WHERE station_slug = ?", (station_slug,))
    conn.commit()
    return cursor.rowcount


def get_match_text(conn: sqlite3.Connection, external_id: str, service: str = "spotify") -> tuple[str, str] | None:
    """One (artist_text, title_text) that resolved to this track, for reverse lookups
    (e.g. 'which stations played this URI?'). Picks arbitrarily if several text
    variants matched the same track — same limitation the old schema had."""
    row = conn.execute("""
        SELECT tm.artist_text, tm.title_text FROM track_match tm
        JOIN track_services ts ON ts.track_id = tm.track_id
        WHERE ts.service = ? AND ts.external_id = ? LIMIT 1
    """, (service, external_id)).fetchone()
    return tuple(row) if row else None


def track_source_stations(conn: sqlite3.Connection, artist: str, title: str) -> list[str]:
    """Which stations (still) have this artist/title in their play history."""
    rows = conn.execute(
        "SELECT DISTINCT station_slug FROM plays WHERE LOWER(artist) = ? AND LOWER(title) = ?",
        (artist.lower(), title.lower()),
    ).fetchall()
    return [row[0] for row in rows]


def playlist_keys(conn: sqlite3.Connection) -> list[str]:
    """Every distinct playlist we're locally tracking the contents of."""
    rows = conn.execute("SELECT DISTINCT station_slug FROM playlist_tracks").fetchall()
    return [row[0] for row in rows]


def log_search_call(conn: sqlite3.Connection, bucket: str | None = None) -> None:
    conn.execute("INSERT INTO search_calls (called_at, bucket) VALUES (?, ?)", (datetime.now().isoformat(), bucket))
    conn.commit()


def search_calls_in_last_24h(conn: sqlite3.Connection, bucket: str | None = None) -> int:
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    if bucket is None:
        return conn.execute("SELECT COUNT(*) FROM search_calls WHERE called_at >= ?", (since,)).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM search_calls WHERE called_at >= ? AND bucket = ?", (since, bucket),
    ).fetchone()[0]


def get_quota_state(conn: sqlite3.Connection) -> tuple[int, str | None]:
    """Return (call_limit, blocked_until). blocked_until is an ISO timestamp or None."""
    row = conn.execute("SELECT call_limit, blocked_until FROM search_quota_state WHERE id = 1").fetchone()
    return row[0], row[1]


def set_call_limit(conn: sqlite3.Connection, limit: int) -> None:
    conn.execute("UPDATE search_quota_state SET call_limit = ? WHERE id = 1", (limit,))
    conn.commit()


def set_blocked_until(conn: sqlite3.Connection, iso_timestamp: str | None) -> None:
    conn.execute("UPDATE search_quota_state SET blocked_until = ? WHERE id = 1", (iso_timestamp,))
    conn.commit()
