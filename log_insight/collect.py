"""Cron entrypoint: run the collectors and persist their output.

Per spec §5.5 this owns the transaction and cursor: records and the new cursor are written
**together** in one transaction, and each collector is wrapped so a failure in one does not
stop the others (only journald exists in M1; files join at M4).
"""

from __future__ import annotations

import argparse
import logging

from . import db
from .collectors import CollectorError
from .collectors.journald import collect_journald
from .config import Config, load_config

log = logging.getLogger("log_insight.collect")

JOURNALD_CURSOR_KEY = "journald:cursor"


def run(config_path: str = "config.toml", *, journald_reader=None) -> None:
    """Load config, ensure schema, and run each collector. `journald_reader` is injectable
    for tests; production uses the default (journalctl)."""
    cfg = load_config(config_path)
    conn = db.connect(cfg.database.path)
    try:
        db.init_db(conn, cfg.database.path)
        _collect_journald(conn, cfg, reader=journald_reader)
        # M4: _collect_files(conn, cfg) — wrapped the same way.
    finally:
        conn.close()


def _collect_journald(conn, cfg: Config, *, reader=None) -> None:
    try:
        after = db.get_state(conn, JOURNALD_CURSOR_KEY)
        batch = collect_journald(
            after,
            cfg.collector.host,
            initial_backfill=cfg.collector.journald.initial_backfill,
            max_batch=cfg.collector.journald.max_batch,
            reader=reader,
        )
    except CollectorError as exc:
        # Per-collector isolation: log and return so other collectors still run.
        log.error("journald collection failed: %s", exc)
        return

    with conn:  # one transaction: records + cursor advance together
        inserted = db.insert_records(conn, batch.records)
        if batch.new_cursor is not None:
            db.set_state(conn, JOURNALD_CURSOR_KEY, batch.new_cursor)

    duplicates = len(batch.records) - inserted
    log.info(
        "journald: inserted=%d duplicates=%d skipped=%d", inserted, duplicates, batch.skipped
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect logs into the Log Insight database.")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="log at DEBUG level")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run(args.config)


if __name__ == "__main__":
    main()
