"""journald collector — spec §5.

A **pure transform**: given the last cursor, read new journal entries and return normalized
records + the new cursor. It does not touch the DB (that is `collect.py`). The `reader` is
injectable so unit tests feed canned JSON lines instead of a live journal (spec §5.6).
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from . import CollectorError
from ..normalize import SOURCE_JOURNALD, LogRecord, level_from_priority

log = logging.getLogger(__name__)

# Source identity, in fallback order (spec §5.3).
_SOURCE_FIELDS = ("_SYSTEMD_UNIT", "SYSLOG_IDENTIFIER", "_COMM")


@dataclass
class JournaldBatch:
    records: list[LogRecord]
    new_cursor: str | None  # cursor to persist; None only on a first run that read nothing
    skipped: int            # malformed lines + entries dropped for a missing timestamp


def collect_journald(
    after_cursor: str | None,
    host: str,
    *,
    initial_backfill: str,
    max_batch: int,
    reader=None,
) -> JournaldBatch:
    """Read journal entries after `after_cursor` (or from `initial_backfill` on first run),
    normalize them, and return the batch. See spec §5 for the full contract.

    On a stale/invalid cursor the underlying read fails; we fall back once to a backfill read
    and warn (spec §5.4). If that also fails, `CollectorError` propagates to `collect.py`.
    """
    if reader is None:
        reader = _journalctl_reader

    argv = _build_argv(after_cursor, initial_backfill)
    try:
        batch = _read_and_map(reader(argv), host, max_batch)
    except CollectorError:
        if after_cursor is None:
            raise  # already a backfill read — genuine failure
        log.warning(
            "journald cursor rejected; falling back to initial_backfill=%s", initial_backfill
        )
        argv = _build_argv(None, initial_backfill)
        batch = _read_and_map(reader(argv), host, max_batch)

    # "Nothing new parsed" → leave the stored cursor unchanged (spec §5.1).
    if batch.new_cursor is None:
        batch.new_cursor = after_cursor
    return batch


def _build_argv(after_cursor: str | None, initial_backfill: str) -> list[str]:
    """journalctl invocation. Cursor is exclusive (no duplicate of the cursor entry)."""
    argv = ["journalctl", "-o", "json", "--no-pager"]
    if after_cursor:
        argv += ["--after-cursor", after_cursor]
    elif initial_backfill == "all":
        pass  # whole journal
    elif initial_backfill == "boot":
        argv += ["--boot"]
    else:
        # systemd.time relative expression, e.g. "24h" -> "-24h".
        argv += ["--since", f"-{initial_backfill}"]
    return argv


def _read_and_map(lines: Iterable[str], host: str, max_batch: int) -> JournaldBatch:
    records: list[LogRecord] = []
    new_cursor: str | None = None
    skipped = 0
    seen = 0  # parsed entries (kept or dropped) — bounds the batch (spec §5.2)

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            skipped += 1  # malformed line — does NOT advance the cursor (BP8/§5.4)
            continue

        cursor = entry.get("__CURSOR")
        if cursor:
            new_cursor = cursor  # advance for any parsed entry, kept or dropped (BP8 option B)
        seen += 1

        record = _map_entry(entry, host, cursor)
        if record is None:
            skipped += 1  # parsed but unusable (no timestamp) — cursor already advanced
        else:
            records.append(record)

        if seen >= max_batch:
            break

    return JournaldBatch(records=records, new_cursor=new_cursor, skipped=skipped)


def _map_entry(entry: dict, host: str, cursor: str | None) -> LogRecord | None:
    """Map one journald JSON object to a LogRecord, or None to drop it (spec §5.3)."""
    try:
        ts_utc = int(entry["__REALTIME_TIMESTAMP"])  # µs since epoch, UTC
    except (KeyError, TypeError, ValueError):
        return None  # no usable timestamp → drop

    return LogRecord(
        ts_utc=ts_utc,
        level=level_from_priority(entry.get("PRIORITY")),
        source_type=SOURCE_JOURNALD,
        source=_pick_source(entry),
        host=host,
        message=_decode_message(entry.get("MESSAGE")),
        dedup_key=cursor,  # __CURSOR is unique → idempotent insert
    )


def _pick_source(entry: dict) -> str:
    for key in _SOURCE_FIELDS:
        value = entry.get(key)
        if value:
            return value
    return "unknown"


def _decode_message(message) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        # journald renders binary MESSAGE as an array of byte values.
        try:
            return bytes(message).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return ""
    return str(message)


def _journalctl_reader(argv: list[str]) -> Iterator[str]:
    """Default reader: stream stdout lines from journalctl. Raises CollectorError if the
    process is missing or exits non-zero *on its own* (a stale cursor exits 1). Early break by
    the consumer terminates the process without raising (that is bounded-batch, not failure)."""
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise CollectorError("journalctl not found") from exc

    completed = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line
        completed = True  # stdout exhausted naturally (not an early break)
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()

    if completed and proc.returncode not in (0, None):
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        raise CollectorError(f"journalctl exited {proc.returncode}: {stderr}")
