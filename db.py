"""Local SQLite storage for played tracks and cached Spotify matches."""
import os
import sqlite3
from datetime import datetime

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
    PRIMARY KEY (station_slug, artist, title, played_at)
);

CREATE TABLE IF NOT EXISTS spotify_matches (
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    spotify_uri TEXT,
    matched_at TEXT NOT NULL,
    PRIMARY KEY (artist, title)
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
"""


def connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
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
    """Return (is_cached, spotify_uri). spotify_uri is None if cached as 'no match found'."""
    row = conn.execute(
        "SELECT spotify_uri FROM spotify_matches WHERE artist = ? AND title = ?",
        (artist.lower(), title.lower()),
    ).fetchone()
    if row is None:
        return False, None
    return True, row[0]


def save_match(conn: sqlite3.Connection, artist: str, title: str, spotify_uri: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO spotify_matches (artist, title, spotify_uri, matched_at) VALUES (?, ?, ?, ?)",
        (artist.lower(), title.lower(), spotify_uri, datetime.now().isoformat()),
    )
    conn.commit()


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
        SELECT DISTINCT sm.spotify_uri
        FROM spotify_matches sm
        LEFT JOIN audio_features af ON af.spotify_uri = sm.spotify_uri
        WHERE sm.spotify_uri IS NOT NULL AND af.spotify_uri IS NULL
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
