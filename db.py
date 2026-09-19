"""Local SQLite storage for played tracks and cached Spotify matches."""
import os
import sqlite3
from datetime import datetime, timedelta

AUDIO_FEATURE_COLUMNS = (
    "spotify_uri", "acousticness", "danceability", "energy", "instrumentalness",
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

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    spotify_uri TEXT NOT NULL UNIQUE,
    -- 'spotify' = confirmed directly via Spotify's own Search (authoritative).
    -- 'reccobeats' = only found via ReccoBeats' search while Spotify was
    -- unavailable; reccobeats_id lets us tell the two apart and compare
    -- later. get_cached_match() treats 'reccobeats' rows as not fully
    -- cached, so a future run retries Spotify and upgrades the source once
    -- it succeeds.
    source TEXT NOT NULL DEFAULT 'spotify',
    reccobeats_id TEXT
);

CREATE TABLE IF NOT EXISTS track_artists (
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    PRIMARY KEY (track_id, artist_id)
);

CREATE TABLE IF NOT EXISTS track_match (
    artist_text TEXT NOT NULL,
    title_text TEXT NOT NULL,
    track_id INTEGER REFERENCES tracks(id),
    matched_at TEXT NOT NULL,
    PRIMARY KEY (artist_text, title_text)
);

CREATE TABLE IF NOT EXISTS playlists (
    station_slug TEXT PRIMARY KEY,
    spotify_playlist_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    station_slug TEXT NOT NULL,
    spotify_uri TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (station_slug, spotify_uri)
);

CREATE TABLE IF NOT EXISTS blocklist (
    station_slug TEXT NOT NULL,
    spotify_uri TEXT NOT NULL,
    blocked_at TEXT NOT NULL,
    PRIMARY KEY (station_slug, spotify_uri)
);

CREATE TABLE IF NOT EXISTS audio_features (
    spotify_uri TEXT PRIMARY KEY,
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

-- Self-calibrating budget for Spotify Search calls (see quota.py). We don't
-- know Spotify's actual Development Mode quota (undocumented, can change),
-- so we discover it empirically: a run that exhausts call_limit without a
-- real block nudges it up; a real QUOTA_EXCEEDED 429 snaps it down to
-- whatever actually succeeded. search_calls is a plain append-only log used
-- to count calls in the trailing 24h window.
CREATE TABLE IF NOT EXISTS search_calls (
    called_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_quota_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    call_limit INTEGER NOT NULL,
    blocked_until TEXT
);
"""

SEARCH_QUOTA_SEED_LIMIT = 300


_MIGRATIONS = {
    "plays": {
        "daynr": "ALTER TABLE plays ADD COLUMN daynr INTEGER "
                 "GENERATED ALWAYS AS (CAST(strftime('%u', played_at) AS INTEGER)) VIRTUAL",
        "hr": "ALTER TABLE plays ADD COLUMN hr INTEGER "
              "GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER)) VIRTUAL",
        "daypart": "ALTER TABLE plays ADD COLUMN daypart INTEGER "
                   "GENERATED ALWAYS AS (CAST(strftime('%H', played_at) AS INTEGER) / 6) VIRTUAL",
    },
    "tracks": {
        "source": "ALTER TABLE tracks ADD COLUMN source TEXT NOT NULL DEFAULT 'spotify'",
        "reccobeats_id": "ALTER TABLE tracks ADD COLUMN reccobeats_id TEXT",
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


def connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.execute(
        "INSERT OR IGNORE INTO search_quota_state (id, call_limit, blocked_until) VALUES (1, ?, NULL)",
        (SEARCH_QUOTA_SEED_LIMIT,),
    )
    conn.commit()
    return conn


def save_plays(conn: sqlite3.Connection, station_slug: str, plays) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO plays (station_slug, artist, title, played_at) VALUES (?, ?, ?, ?)",
        [(station_slug, p.artist, p.title, p.played_at.isoformat()) for p in plays],
    )
    conn.commit()


def recent_unique_tracks(conn: sqlite3.Connection, station_slug: str, since: datetime) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT artist, title FROM plays WHERE station_slug = ? AND played_at >= ?",
        (station_slug, since.isoformat()),
    ).fetchall()
    return rows


def get_cached_match(conn: sqlite3.Connection, artist: str, title: str) -> tuple[bool, str | None]:
    """Return (is_cached, spotify_uri). spotify_uri is None if cached as 'no
    match found'. A track only found via ReccoBeats (source='reccobeats',
    see tracks table) counts as NOT cached, so a later run — once Spotify is
    available again — retries it there and upgrades the source on success."""
    row = conn.execute(
        "SELECT t.spotify_uri, t.source FROM track_match tm LEFT JOIN tracks t ON t.id = tm.track_id "
        "WHERE tm.artist_text = ? AND tm.title_text = ?",
        (artist.lower(), title.lower()),
    ).fetchone()
    if row is None:
        return False, None
    spotify_uri, source = row
    if spotify_uri is None:
        return True, None
    return source == "spotify", spotify_uri


def get_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM artists WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO artists (name) VALUES (?)", (name,)).lastrowid


def get_or_create_track(
    conn: sqlite3.Connection, spotify_uri: str, title: str, artist_names: list[str],
    source: str = "spotify", reccobeats_id: str | None = None,
) -> int:
    row = conn.execute("SELECT id, source FROM tracks WHERE spotify_uri = ?", (spotify_uri,)).fetchone()
    if row:
        track_id, existing_source = row
        if source == "spotify" and existing_source != "spotify":
            conn.execute("UPDATE tracks SET source = 'spotify' WHERE id = ?", (track_id,))
        if reccobeats_id:
            conn.execute(
                "UPDATE tracks SET reccobeats_id = COALESCE(reccobeats_id, ?) WHERE id = ?",
                (reccobeats_id, track_id),
            )
    else:
        track_id = conn.execute(
            "INSERT INTO tracks (title, spotify_uri, source, reccobeats_id) VALUES (?, ?, ?, ?)",
            (title, spotify_uri, source, reccobeats_id),
        ).lastrowid
    for name in artist_names:
        artist_id = get_or_create_artist(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO track_artists (track_id, artist_id) VALUES (?, ?)", (track_id, artist_id),
        )
    return track_id


def save_match(
    conn: sqlite3.Connection, artist: str, title: str,
    spotify_uri: str | None, track_title: str | None = None, artist_names: list[str] | None = None,
    source: str = "spotify", reccobeats_id: str | None = None,
) -> None:
    """`track_title`/`artist_names` are the matched track's own (title, artist
    list) — from Spotify if source='spotify', from ReccoBeats if
    source='reccobeats' — pass None/omit when spotify_uri is None (no match
    found anywhere)."""
    track_id = None
    if spotify_uri:
        track_id = get_or_create_track(
            conn, spotify_uri, track_title or title, artist_names or [artist],
            source=source, reccobeats_id=reccobeats_id,
        )
    conn.execute(
        "INSERT OR REPLACE INTO track_match (artist_text, title_text, track_id, matched_at) VALUES (?, ?, ?, ?)",
        (artist.lower(), title.lower(), track_id, datetime.now().isoformat()),
    )
    conn.commit()


def get_track_artists(conn: sqlite3.Connection, spotify_uri: str) -> list[str]:
    rows = conn.execute("""
        SELECT a.name FROM tracks t
        JOIN track_artists ta ON ta.track_id = t.id
        JOIN artists a ON a.id = ta.artist_id
        WHERE t.spotify_uri = ?
    """, (spotify_uri,)).fetchall()
    return [row[0] for row in rows]


def get_playlist_id(conn: sqlite3.Connection, station_slug: str) -> str | None:
    row = conn.execute(
        "SELECT spotify_playlist_id FROM playlists WHERE station_slug = ?", (station_slug,),
    ).fetchone()
    return row[0] if row else None


def save_playlist_id(conn: sqlite3.Connection, station_slug: str, playlist_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO playlists (station_slug, spotify_playlist_id) VALUES (?, ?)",
        (station_slug, playlist_id),
    )
    conn.commit()


def get_playlist_tracks(conn: sqlite3.Connection, station_slug: str) -> set[str]:
    rows = conn.execute(
        "SELECT spotify_uri FROM playlist_tracks WHERE station_slug = ?", (station_slug,),
    ).fetchall()
    return {row[0] for row in rows}


def add_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO playlist_tracks (station_slug, spotify_uri, added_at) VALUES (?, ?, ?)",
        [(station_slug, uri, datetime.now().isoformat()) for uri in uris],
    )
    conn.commit()


def remove_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str]) -> None:
    conn.executemany(
        "DELETE FROM playlist_tracks WHERE station_slug = ? AND spotify_uri = ?",
        [(station_slug, uri) for uri in uris],
    )
    conn.commit()


def replace_playlist_tracks(conn: sqlite3.Connection, station_slug: str, uris: list[str]) -> None:
    conn.execute("DELETE FROM playlist_tracks WHERE station_slug = ?", (station_slug,))
    add_playlist_tracks(conn, station_slug, uris)


def get_blocklist(conn: sqlite3.Connection, station_slug: str) -> set[str]:
    rows = conn.execute(
        "SELECT spotify_uri FROM blocklist WHERE station_slug = ?", (station_slug,),
    ).fetchall()
    return {row[0] for row in rows}


def add_to_blocklist(conn: sqlite3.Connection, station_slug: str, uris: list[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO blocklist (station_slug, spotify_uri, blocked_at) VALUES (?, ?, ?)",
        [(station_slug, uri, datetime.now().isoformat()) for uri in uris],
    )
    conn.commit()


def get_uris_missing_audio_features(conn: sqlite3.Connection) -> list[str]:
    """Spotify URIs we've matched a track to, but haven't fetched ReccoBeats features for yet."""
    rows = conn.execute("""
        SELECT t.spotify_uri FROM tracks t
        LEFT JOIN audio_features af ON af.spotify_uri = t.spotify_uri
        WHERE af.spotify_uri IS NULL
    """).fetchall()
    return [row[0] for row in rows]


def save_audio_features(conn: sqlite3.Connection, spotify_uri: str, features: dict | None) -> None:
    """`features` is a ReccoBeats audio-features item, or None if no match was found."""
    f = features or {}
    conn.execute("""
        INSERT OR REPLACE INTO audio_features
        (spotify_uri, acousticness, danceability, energy, instrumentalness, musical_key,
         liveness, loudness, mode, speechiness, tempo, valence, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        spotify_uri, f.get("acousticness"), f.get("danceability"), f.get("energy"),
        f.get("instrumentalness"), f.get("key"), f.get("liveness"), f.get("loudness"),
        f.get("mode"), f.get("speechiness"), f.get("tempo"), f.get("valence"),
        datetime.now().isoformat(),
    ))
    conn.commit()


def get_audio_features(conn: sqlite3.Connection, spotify_uri: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM audio_features WHERE spotify_uri = ?", (spotify_uri,),
    ).fetchone()
    return dict(zip(AUDIO_FEATURE_COLUMNS, row)) if row else None


def delete_station_plays(conn: sqlite3.Connection, station_slug: str) -> int:
    cursor = conn.execute("DELETE FROM plays WHERE station_slug = ?", (station_slug,))
    conn.commit()
    return cursor.rowcount


def get_match_text(conn: sqlite3.Connection, spotify_uri: str) -> tuple[str, str] | None:
    """One (artist_text, title_text) that resolved to this track, for reverse lookups
    (e.g. 'which stations played this URI?'). Picks arbitrarily if several text
    variants matched the same track — same limitation the old schema had."""
    row = conn.execute("""
        SELECT tm.artist_text, tm.title_text FROM track_match tm
        JOIN tracks t ON t.id = tm.track_id
        WHERE t.spotify_uri = ? LIMIT 1
    """, (spotify_uri,)).fetchone()
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


def log_search_call(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO search_calls (called_at) VALUES (?)", (datetime.now().isoformat(),))
    conn.commit()


def search_calls_in_last_24h(conn: sqlite3.Connection) -> int:
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    return conn.execute("SELECT COUNT(*) FROM search_calls WHERE called_at >= ?", (since,)).fetchone()[0]


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
