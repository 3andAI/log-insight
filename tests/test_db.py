import sqlite3

import pytest

from log_insight import db
from log_insight.normalize import SOURCE_JOURNALD, LogRecord


@pytest.fixture
def conn(tmp_path):
    # A file DB (not :memory:) so WAL mode is meaningful.
    c = db.connect(tmp_path / "logs.db")
    db.init_db(c)
    yield c
    c.close()


def rec(message, *, ts=1_000, level="info", source="app.service", dedup_key=None):
    return LogRecord(
        ts_utc=ts,
        level=level,
        source_type=SOURCE_JOURNALD,
        source=source,
        host="target-host",
        message=message,
        dedup_key=dedup_key,
    )


def test_connect_enables_wal(tmp_path):
    c = db.connect(tmp_path / "logs.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    c.close()


def test_init_db_is_idempotent(tmp_path):
    c = db.connect(tmp_path / "logs.db")
    db.init_db(c)
    db.init_db(c)  # second call must not raise
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entries", "entries_fts", "state"} <= tables
    c.close()


def test_insert_and_fts_match(conn):
    inserted = db.insert_records(
        conn,
        [rec("connection refused to backend"), rec("cache warmed successfully")],
    )
    assert inserted == 2
    rows = conn.execute(
        "SELECT e.message FROM entries_fts f JOIN entries e ON e.id = f.rowid "
        "WHERE entries_fts MATCH ?",
        ("refused",),
    ).fetchall()
    assert [r["message"] for r in rows] == ["connection refused to backend"]


def test_dedup_key_prevents_duplicates(conn):
    first = db.insert_records(conn, [rec("dup line", dedup_key="cursor-1")])
    second = db.insert_records(conn, [rec("dup line", dedup_key="cursor-1")])
    assert (first, second) == (1, 0)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_null_dedup_keys_are_independent(conn):
    # Keyless rows (e.g. future file entries without a key) must not collide.
    db.insert_records(conn, [rec("a"), rec("a")])
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2


def test_state_get_set_overwrite(conn):
    assert db.get_state(conn, "journald:cursor") is None
    db.set_state(conn, "journald:cursor", "s=abc")
    assert db.get_state(conn, "journald:cursor") == "s=abc"
    db.set_state(conn, "journald:cursor", "s=def")
    assert db.get_state(conn, "journald:cursor") == "s=def"


def test_deleted_rows_leave_fts(conn):
    db.insert_records(conn, [rec("temporary line", dedup_key="k")])
    (rowid,) = conn.execute("SELECT id FROM entries").fetchone()
    conn.execute("DELETE FROM entries WHERE id = ?", (rowid,))
    rows = conn.execute(
        "SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?", ("temporary",)
    ).fetchall()
    assert rows == []
