"""Read-side queries: keyword/time/level/source search (keyset-paginated) and the
error+warning histogram. Pure SQL over a connection — no HTTP — so it is unit-testable
directly against a seeded DB (spec §5.5 / F1–F3)."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from .normalize import ERROR_LEVELS

_MIN_TS = 0
_MAX_TS = 2**63 - 1  # effectively "no bound"

SECONDS = 1_000_000  # microseconds per second (ts_utc is µs)


@dataclass
class Filters:
    """A search request. Times are µs-epoch UTC. `keyword` is free text (tokenized into an
    AND of quoted terms). `levels`/`source` are optional exact filters."""

    keyword: str | None = None
    ts_from: int = _MIN_TS
    ts_to: int = _MAX_TS
    levels: list[str] | None = None
    source: str | None = None


@dataclass
class Cursor:
    """Keyset position for 'Load older': the last (ts_utc, id) already shown."""

    ts_utc: int
    id: int


@dataclass
class SearchPage:
    rows: list[sqlite3.Row]
    next_cursor: Cursor | None  # None when no older rows remain
    fields: list[str] = field(default_factory=list)


def _fts_query(keyword: str) -> str | None:
    """Turn user text into a safe FTS5 MATCH expression: each whitespace token becomes a
    quoted phrase, AND-ed together. Quoting neutralizes FTS operators so arbitrary input can't
    cause a syntax error or act as an operator."""
    tokens = keyword.split()
    if not tokens:
        return None
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _where(filters: Filters, cursor: Cursor | None) -> tuple[str, list, bool]:
    """Build the shared WHERE clause. Returns (sql, params, uses_fts)."""
    clauses = ["e.ts_utc BETWEEN ? AND ?"]
    params: list = [filters.ts_from, filters.ts_to]
    uses_fts = False

    if filters.keyword:
        match = _fts_query(filters.keyword)
        if match is not None:
            uses_fts = True
            clauses.append("entries_fts MATCH ?")
            params.append(match)

    if filters.levels:
        placeholders = ",".join("?" for _ in filters.levels)
        clauses.append(f"e.level IN ({placeholders})")
        params.extend(filters.levels)

    if filters.source:
        clauses.append("e.source = ?")
        params.append(filters.source)

    if cursor is not None:
        # newest-first keyset: strictly older than the last shown (ts, id).
        clauses.append("(e.ts_utc < ? OR (e.ts_utc = ? AND e.id < ?))")
        params.extend([cursor.ts_utc, cursor.ts_utc, cursor.id])

    return " AND ".join(clauses), params, uses_fts


def search(conn: sqlite3.Connection, filters: Filters, *, cursor: Cursor | None = None,
           limit: int = 50) -> SearchPage:
    """Return up to `limit` matching rows, newest first, plus the cursor for the next page."""
    where, params, uses_fts = _where(filters, cursor)
    join = "JOIN entries_fts ON entries_fts.rowid = e.id" if uses_fts else ""
    sql = (
        f"SELECT e.id, e.ts_utc, e.level, e.source, e.host, e.message "
        f"FROM entries e {join} WHERE {where} "
        f"ORDER BY e.ts_utc DESC, e.id DESC LIMIT ?"
    )
    rows = conn.execute(sql, [*params, limit]).fetchall()
    next_cursor = Cursor(rows[-1]["ts_utc"], rows[-1]["id"]) if len(rows) == limit else None
    return SearchPage(rows=rows, next_cursor=next_cursor,
                      fields=["id", "ts_utc", "level", "source", "host", "message"])


def choose_bucket_seconds(span_us: int, minute_max_hours: int, hour_max_days: int,
                          max_buckets: int = 1500) -> int:
    """Pick a histogram bucket size (seconds) from the range span (spec F3), while capping the
    number of buckets so an extreme range can't render tens of thousands of bars."""
    span_s = span_us / SECONDS
    if span_s <= minute_max_hours * 3600:
        bucket = 60
    elif span_s <= hour_max_days * 86400:
        bucket = 3600
    else:
        bucket = 86400
    if max_buckets:
        bucket = max(bucket, math.ceil(span_s / max_buckets))
    return bucket


def histogram(conn: sqlite3.Connection, filters: Filters, *, bucket_seconds: int) -> list[dict]:
    """Dense error+warning counts per time bucket across [ts_from, ts_to]. Buckets with no
    matches are returned with count 0 so the chart is evenly spaced. Ignores any `levels` on
    the filter — the overview is specifically about error+warning volume."""
    bucket_us = bucket_seconds * SECONDS
    overview_filters = Filters(
        keyword=filters.keyword, ts_from=filters.ts_from, ts_to=filters.ts_to,
        levels=list(ERROR_LEVELS), source=filters.source,
    )
    where, params, uses_fts = _where(overview_filters, None)
    join = "JOIN entries_fts ON entries_fts.rowid = e.id" if uses_fts else ""
    sql = (
        f"SELECT (e.ts_utc / ?) * ? AS bucket, COUNT(*) AS n "
        f"FROM entries e {join} WHERE {where} GROUP BY bucket"
    )
    counts = {r["bucket"]: r["n"] for r in conn.execute(sql, [bucket_us, bucket_us, *params])}

    # Emit a dense series from the first bucket at/after ts_from through ts_to.
    start = (filters.ts_from // bucket_us) * bucket_us
    out: list[dict] = []
    b = start
    while b <= filters.ts_to:
        out.append({"bucket_start": b, "count": counts.get(b, 0)})
        b += bucket_us
    return out
