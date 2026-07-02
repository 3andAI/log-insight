import pytest
from fastapi.testclient import TestClient

from log_insight import db
from log_insight.app import create_app
from log_insight.normalize import SOURCE_JOURNALD, LogRecord

S = 1_000_000


def make_config(tmp_path, *, with_db=True):
    db_path = tmp_path / "logs.db"
    if with_db:
        conn = db.connect(db_path)
        db.init_db(conn, db_path)
        rows = [
            LogRecord(ts_utc=(1000 + i) * S, level=("error" if i % 2 else "info"),
                      source_type=SOURCE_JOURNALD, source="app.service", host="h",
                      message=f"line {i} refused" if i % 2 else f"line {i} ok",
                      dedup_key=f"k{i}")
            for i in range(5)
        ]
        db.insert_records(conn, rows)
        conn.commit()
        conn.close()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[database]\npath = "{db_path}"\n')
    return cfg


def client(tmp_path, **kw):
    return TestClient(create_app(str(make_config(tmp_path, **kw))))


def test_index_renders(tmp_path):
    r = client(tmp_path).get("/?from=1970-01-01T00:00&to=2100-01-01T00:00")
    assert r.status_code == 200
    assert "Log Insight" in r.text
    assert "Errors + Warnings" in r.text
    assert "refused" in r.text  # a seeded message is shown


def test_index_empty_when_no_db(tmp_path):
    r = client(tmp_path, with_db=False).get("/")
    assert r.status_code == 200
    assert "collector may not have run yet" in r.text


def test_api_search_returns_json_rows(tmp_path):
    r = client(tmp_path).get("/api/search?from=1970-01-01T00:00&to=2100-01-01T00:00")
    body = r.json()
    assert r.status_code == 200
    assert len(body["rows"]) == 5
    # newest first
    assert body["rows"][0]["ts_utc"] > body["rows"][-1]["ts_utc"]


def test_api_search_level_filter(tmp_path):
    r = client(tmp_path).get("/api/search?level=error&from=1970-01-01T00:00&to=2100-01-01T00:00")
    rows = r.json()["rows"]
    assert rows and all(row["level"] == "error" for row in rows)


def test_api_search_keyset_paging(tmp_path):
    c = client(tmp_path)
    first = c.get("/api/search?limit=2&from=1970-01-01T00:00&to=2100-01-01T00:00").json()
    assert len(first["rows"]) == 2 and first["next"] is not None
    nxt = first["next"]
    second = c.get(
        f"/api/search?limit=2&before_ts={nxt['ts']}&before_id={nxt['id']}"
        "&from=1970-01-01T00:00&to=2100-01-01T00:00"
    ).json()
    first_ids = {row["id"] for row in first["rows"]}
    second_ids = {row["id"] for row in second["rows"]}
    assert first_ids.isdisjoint(second_ids)  # no overlap across pages
