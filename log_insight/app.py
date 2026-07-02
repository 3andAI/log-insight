"""FastAPI web app: one page (filters → histogram → results) plus a JSON endpoint for
keyset 'Load older' paging. Read-only over the DB (the collector is the sole writer).

Localhost-only is enforced at serve time by `enforce_bind_policy` (spec G1); building the app
(as tests do) never binds, so the guard lives in `serve()`, not `create_app()`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import queries
from .config import enforce_bind_policy, load_config
from .normalize import LEVELS
from .queries import Cursor, Filters

log = logging.getLogger("log_insight.app")

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_DAY_US = 86_400 * queries.SECONDS
_REFRESH_SECONDS = 30  # BP5: auto-refresh cadence
_PAGE = 50


def create_app(config_path: str | None = None) -> FastAPI:
    cfg = load_config(config_path or os.environ.get("LOG_INSIGHT_CONFIG", "config.toml"))
    app = FastAPI(title="Log Insight")
    app.state.config = cfg

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        keyword: str = "",
        level: str = "all",
        source: str = "",
        from_: str = Query("", alias="from"),
        to: str = Query(""),
    ):
        now = _now_us()
        ts_from = _parse_us(from_, now - _DAY_US)
        ts_to = _parse_us(to, now)
        filters = _filters(keyword, level, source, ts_from, ts_to)

        with _read(cfg.database.path) as conn:
            if conn is None:
                rows, next_cursor, buckets = [], None, []
            else:
                page = queries.search(conn, filters, limit=_PAGE)
                rows, next_cursor = page.rows, page.next_cursor
                bucket_s = queries.choose_bucket_seconds(
                    max(ts_to - ts_from, 1),
                    cfg.overview.minute_bucket_max_hours,
                    cfg.overview.hour_bucket_max_days,
                )
                buckets = queries.histogram(conn, filters, bucket_seconds=bucket_s)

        max_count = max((b["count"] for b in buckets), default=0)
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "keyword": keyword,
                "level": level,
                "source": source,
                "from_value": _to_local_input(ts_from),
                "to_value": _to_local_input(ts_to),
                "levels": LEVELS,
                "rows": [_display(r) for r in rows],
                "next_cursor": next_cursor,
                "buckets": [
                    {
                        "count": b["count"],
                        "height": round(100 * b["count"] / max_count) if max_count else 0,
                        "label": _fmt_local(b["bucket_start"]),
                    }
                    for b in buckets
                ],
                "total_errors": sum(b["count"] for b in buckets),
                "refresh_seconds": _REFRESH_SECONDS,
            },
        )

    @app.get("/api/search")
    def api_search(
        keyword: str = "",
        level: str = "all",
        source: str = "",
        from_: str = Query("", alias="from"),
        to: str = Query(""),
        before_ts: int | None = None,
        before_id: int | None = None,
        limit: int = _PAGE,
    ):
        now = _now_us()
        filters = _filters(keyword, level, source, _parse_us(from_, now - _DAY_US), _parse_us(to, now))
        cursor = Cursor(before_ts, before_id) if before_ts is not None and before_id is not None else None
        limit = max(1, min(limit, 500))

        with _read(cfg.database.path) as conn:
            if conn is None:
                return JSONResponse({"rows": [], "next": None})
            page = queries.search(conn, filters, cursor=cursor, limit=limit)

        return JSONResponse(
            {
                "rows": [_display(r) for r in page.rows],
                "next": None
                if page.next_cursor is None
                else {"ts": page.next_cursor.ts_utc, "id": page.next_cursor.id},
            }
        )

    return app


# --- helpers (pure; no request state) ---

def _filters(keyword: str, level: str, source: str, ts_from: int, ts_to: int) -> Filters:
    levels = None if level == "all" else [level]
    return Filters(
        keyword=keyword.strip() or None,
        ts_from=ts_from,
        ts_to=ts_to,
        levels=levels,
        source=source.strip() or None,
    )


class _read:
    """Context manager yielding a read-only connection, or None if the DB doesn't exist yet
    (fresh install before the first collector run)."""

    def __init__(self, path: str):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        if not os.path.exists(self.path):
            return None
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, *exc):
        if self.conn is not None:
            self.conn.close()


def _now_us() -> int:
    return int(time.time() * queries.SECONDS)


def _parse_us(value: str, default: int) -> int:
    value = (value or "").strip()
    if not value or value.lower() == "now":
        return default
    try:
        return int(datetime.fromisoformat(value).timestamp() * queries.SECONDS)
    except ValueError:
        return default


def _fmt_local(ts_us: int) -> str:
    return datetime.fromtimestamp(ts_us / queries.SECONDS).strftime("%Y-%m-%d %H:%M:%S")


def _to_local_input(ts_us: int) -> str:
    # value format for <input type="datetime-local">
    return datetime.fromtimestamp(ts_us / queries.SECONDS).strftime("%Y-%m-%dT%H:%M")


def _display(row: sqlite3.Row) -> dict:
    return {
        "time": _fmt_local(row["ts_utc"]),
        "level": row["level"],
        "source": row["source"],
        "message": row["message"],
        "ts_utc": row["ts_utc"],
        "id": row["id"],
    }


def serve() -> None:
    """CLI entrypoint: enforce the bind policy (spec G1), then run uvicorn."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    enforce_bind_policy(app.state.config.server, logger=log)  # refuses non-loopback unless allowed
    uvicorn.run(app, host=app.state.config.server.host, port=app.state.config.server.port)


if __name__ == "__main__":
    serve()
