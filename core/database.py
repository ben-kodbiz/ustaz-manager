"""SQLite schema and connection helpers. SQLite is the source of truth."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core import paths

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ustaz (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL,
    external_id TEXT,
    title TEXT,
    discovered_at TEXT,
    last_seen_at TEXT,
    UNIQUE (ustaz_id, source_type, url)
);

CREATE TABLE IF NOT EXISTS playlist (
    ustaz_id TEXT NOT NULL,
    playlist_id TEXT NOT NULL,
    title TEXT,
    channel_id TEXT,
    video_count INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (ustaz_id, playlist_id)
);

CREATE TABLE IF NOT EXISTS series (
    series_id TEXT PRIMARY KEY,
    ustaz_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    language TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (ustaz_id, slug)
);

CREATE TABLE IF NOT EXISTS category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (ustaz_id, slug)
);

CREATE TABLE IF NOT EXISTS video (
    ustaz_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT,
    original_title TEXT,
    description TEXT,
    channel_id TEXT,
    channel_name TEXT,
    playlist_id TEXT,
    playlist_title TEXT,
    playlist_position INTEGER,
    upload_date TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    web_url TEXT,
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    download_status TEXT NOT NULL DEFAULT 'PENDING',
    download_path TEXT,
    classification_status TEXT NOT NULL DEFAULT 'PENDING',
    category TEXT,
    series_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    downloaded_at TEXT,
    metadata_hash TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (ustaz_id, video_id)
);

CREATE TABLE IF NOT EXISTS video_series (
    ustaz_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    series_id TEXT NOT NULL,
    position INTEGER,
    PRIMARY KEY (ustaz_id, video_id, series_id)
);

CREATE TABLE IF NOT EXISTS video_category (
    ustaz_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (ustaz_id, video_id, category_id)
);

CREATE TABLE IF NOT EXISTS download_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    priority INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (ustaz_id, video_id)
);

CREATE TABLE IF NOT EXISTS processing_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    ustaz_id TEXT,
    video_id TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    payload TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT NOT NULL,
    video_id TEXT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT,
    video_id TEXT,
    job_id INTEGER,
    stage TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_build (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ustaz_id TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT,
    video_count INTEGER,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_video_ustaz_state ON video (ustaz_id, state);
CREATE INDEX IF NOT EXISTS idx_video_download_status ON video (download_status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, priority);
CREATE INDEX IF NOT EXISTS idx_source_ustaz ON source (ustaz_id);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS video_fts USING fts5(
    title,
    description,
    playlist_title,
    series_title,
    category,
    content=''
);
"""


def database_path() -> Path:
    universal_path = paths.UNIVERSAL_CONFIG
    if universal_path.is_file():
        import json

        with universal_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        configured = data.get("storage", {}).get("database")
        if configured:
            return paths.resolve_repo_path(configured)
    return paths.DEFAULT_DATABASE


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(DDL)
    has_fts = fts5_available(conn)
    if has_fts:
        conn.executescript(FTS_DDL)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('fts5', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if has_fts else "0",),
    )
    conn.commit()
    return conn


def has_fts5(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = 'fts5'").fetchone()
    return bool(row) and row["value"] == "1"
