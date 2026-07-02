import pytest

from log_insight import db, queries
from log_insight.normalize import SOURCE_JOURNALD, LogRecord
from log_insight.queries import Cursor, Filters

S = queries.SECONDS  # µs per second


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "logs.db")
    db.init_db(c)
    yield c
    c.close()


def add(conn, *, ts_s, level, source, message, key):
    db.insert_records(conn, [LogRecord(
        ts_utc=ts_s * S, level=level, source_type=SOURCE_JOURNALD,
        source=source, host="h", message=message, dedup_key=key,
    )])


def seed(conn):
    add(conn, ts_s=100, level="info", source="app.service", message="cache warmed", key="k1")
    add(conn, ts_s=200, level="error", source="app.service", message="connection refused", key="k2")
    add(conn, ts_s=300, level="warning", source="monitor.service", message="disk usage high", key="k3")
    add(conn, ts_s=400, level="error", source="db.service", message="connection timeout", key="k4")
    add(conn, ts_s=500, level="info", source="app.service", message="request handled", key="k5")


def test_keyword_uses_fts(conn):
    seed(conn)
    rows = queries.search(conn, Filters(keyword="connection")).rows
    assert {r["message"] for r in rows} == {"connection refused", "connection timeout"}


def test_keyword_multiple_tokens_are_anded(conn):
    seed(conn)
    rows = queries.search(conn, Filters(keyword="connection refused")).rows
    assert [r["message"] for r in rows] == ["connection refused"]


def test_keyword_special_chars_do_not_error(conn):
    seed(conn)
    # FTS operators in user input must be neutralized, not executed.
    assert queries.search(conn, Filters(keyword='refused OR "')).rows == []


def test_time_range_filter(conn):
    seed(conn)
    rows = queries.search(conn, Filters(ts_from=250 * S, ts_to=450 * S)).rows
    assert sorted(r["ts_utc"] // S for r in rows) == [300, 400]


def test_level_filter(conn):
    seed(conn)
    rows = queries.search(conn, Filters(levels=["error"])).rows
    assert all(r["level"] == "error" for r in rows) and len(rows) == 2


def test_source_filter(conn):
    seed(conn)
    rows = queries.search(conn, Filters(source="app.service")).rows
    assert {r["source"] for r in rows} == {"app.service"} and len(rows) == 3


def test_newest_first_order(conn):
    seed(conn)
    rows = queries.search(conn, Filters()).rows
    assert [r["ts_utc"] // S for r in rows] == [500, 400, 300, 200, 100]


def test_keyset_pagination(conn):
    seed(conn)
    p1 = queries.search(conn, Filters(), limit=2)
    assert [r["ts_utc"] // S for r in p1.rows] == [500, 400]
    assert p1.next_cursor is not None

    p2 = queries.search(conn, Filters(), cursor=p1.next_cursor, limit=2)
    assert [r["ts_utc"] // S for r in p2.rows] == [300, 200]

    p3 = queries.search(conn, Filters(), cursor=p2.next_cursor, limit=2)
    assert [r["ts_utc"] // S for r in p3.rows] == [100]
    assert p3.next_cursor is None  # fewer than limit -> no more pages


def test_keyset_breaks_ties_by_id(conn):
    # Two rows share a timestamp: paging must not skip or repeat either.
    add(conn, ts_s=700, level="info", source="s", message="A", key="a")
    add(conn, ts_s=700, level="info", source="s", message="B", key="b")
    p1 = queries.search(conn, Filters(), limit=1)
    p2 = queries.search(conn, Filters(), cursor=p1.next_cursor, limit=1)
    assert {p1.rows[0]["message"], p2.rows[0]["message"]} == {"A", "B"}


def test_choose_bucket_seconds():
    assert queries.choose_bucket_seconds(1 * 3600 * S, 2, 2) == 60      # <=2h -> minute
    assert queries.choose_bucket_seconds(24 * 3600 * S, 2, 2) == 3600   # <=2d -> hour
    assert queries.choose_bucket_seconds(10 * 86400 * S, 2, 2) == 86400  # else -> day


def test_choose_bucket_caps_bucket_count_on_extreme_range():
    span = 130 * 365 * 86400 * S  # ~130 years
    bucket = queries.choose_bucket_seconds(span, 2, 2, max_buckets=1500)
    assert (span / S) / bucket <= 1500  # bounded number of bars


def test_histogram_counts_only_error_and_warning(conn):
    seed(conn)
    buckets = queries.histogram(conn, Filters(ts_from=0, ts_to=600 * S), bucket_seconds=3600)
    # info entries are excluded; 2 errors + 1 warning fall in the single hour bucket.
    assert sum(b["count"] for b in buckets) == 3


def test_histogram_is_dense_with_zero_buckets(conn):
    add(conn, ts_s=0, level="error", source="s", message="x", key="x")
    add(conn, ts_s=7200, level="error", source="s", message="y", key="y")  # +2h
    buckets = queries.histogram(conn, Filters(ts_from=0, ts_to=7200 * S), bucket_seconds=3600)
    assert [b["count"] for b in buckets] == [1, 0, 1]  # hour0=1, hour1=0, hour2=1


def test_histogram_respects_keyword_and_source(conn):
    seed(conn)
    buckets = queries.histogram(
        conn, Filters(keyword="connection", source="db.service", ts_from=0, ts_to=600 * S),
        bucket_seconds=3600,
    )
    assert sum(b["count"] for b in buckets) == 1  # only the db.service 'connection timeout' error
