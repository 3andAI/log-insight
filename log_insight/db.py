"""SQLite access: connection (WAL), schema creation, and the small state/insert helpers.

Design (spec §2): a single writer (the cron collector) and a read-only web app share this DB.
WAL lets the UI read while the collector writes. This module owns *all* SQL DDL and the
`entries` / `entries_fts` / `state` tables; orchestration (transactions, cursors) lives in
`collect.py`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .normalize import LogRecord

# Schema is created with IF NOT EXISTS so init_db is idempotent and safe to run every start.
_SCHEMA = [
    # Normalized entries. `dedup_key` is UNIQUE → INSERT OR IGNORE gives idempotent ingestion
    # (journald supplies __CURSOR). SQLite treats NULLs as distinct, so keyless rows are allowed.
    """
    CREATE TABLE IF NOT EXISTS entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc      INTEGER NOT NULL,          -- microseconds since epoch, UTC
        level       TEXT    NOT NULL,          -- normalize.LEVELS
        source_type TEXT    NOT NULL,          -- 'journald' | 'file'
        source      TEXT    NOT NULL,          -- unit / identifier / file path
        host        TEXT    NOT NULL,          -- logical host (multi-tenant seam)
        message     TEXT    NOT NULL,
        dedup_key   TEXT    UNIQUE             -- idempotency key; NULL = none
    )
    """,
    # Search-path indexes: time range, level filter, source filter (spec §5.5 / F2).
    "CREATE INDEX IF NOT EXISTS idx_entries_ts     ON entries(ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_entries_level  ON entries(level)",
    "CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source)",
    # Full-text over message, external-content mirror of `entries` (spec F1).
    "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts "
    "USING fts5(message, content='entries', content_rowid='id')",
    # Keep the FTS index in sync with the content table.
    """
    CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts(rowid, message) VALUES (new.id, new.message);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, message) VALUES('delete', old.id, old.message);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, message) VALUES('delete', old.id, old.message);
        INSERT INTO entries_fts(rowid, message) VALUES (new.id, new.message);
    END
    """,
    # Generic key/value store for ingestion cursors (journald token, per-file inode+offset).
    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]

_INSERT = (
    "INSERT OR IGNORE INTO entries "
    "(ts_utc, level, source_type, source, host, message, dedup_key) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the DB in WAL mode with row access by name, and restrict its permissions (G2)."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _restrict_permissions(path)  # main db + -shm now exist
    return conn


def init_db(conn: sqlite3.Connection, path: str | Path | None = None) -> None:
    """Create the schema if absent. Idempotent. Pass `path` to re-restrict permissions once
    the schema write has created the -wal sidecar (G2)."""
    with conn:  # one transaction; rolls back on any failure
        for stmt in _SCHEMA:
            conn.execute(stmt)
    if path is not None:
        _restrict_permissions(path)


def _restrict_permissions(path: str | Path) -> None:
    """chmod the DB and its WAL/SHM sidecars to 0600 — the database aggregates PII into one
    searchable place (spec G2), so it must not be group/world-readable regardless of umask.
    Best effort: silently skips what does not exist or cannot be chmod'd (e.g. :memory:)."""
    p = Path(path)
    for target in (p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
        try:
            if target.exists():
                target.chmod(0o600)
        except OSError:
            pass


def insert_records(conn: sqlite3.Connection, records: Iterable[LogRecord]) -> int:
    """Insert records, skipping any whose `dedup_key` already exists. Returns the number
    actually inserted. Caller owns the surrounding transaction (see `collect.py`)."""
    inserted = 0
    for r in records:
        cur = conn.execute(
            _INSERT,
            (r.ts_utc, r.level, r.source_type, r.source, r.host, r.message, r.dedup_key),
        )
        inserted += cur.rowcount  # 1 if inserted, 0 if ignored as a duplicate
    return inserted


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
