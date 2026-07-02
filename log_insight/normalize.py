"""The normalized log-entry shape shared by both collectors, plus the level mapping.

Owning `LogRecord` and `level_from_priority` here (spec §5.5) guarantees that journald and
`/var/log` entries end up with the *same* `level` enum, so the overview can count across both.
"""

from __future__ import annotations

from dataclasses import dataclass

# Normalized level enum (plain strings — stable for storage and for `level IN (...)` filters).
LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"
LEVEL_DEBUG = "debug"
LEVEL_UNKNOWN = "unknown"
LEVELS = (LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO, LEVEL_DEBUG, LEVEL_UNKNOWN)

# Levels the "unusually many errors" overview counts (spec F3).
ERROR_LEVELS = (LEVEL_ERROR, LEVEL_WARNING)

SOURCE_JOURNALD = "journald"
SOURCE_FILE = "file"


@dataclass(frozen=True)
class LogRecord:
    """One normalized log line, ready to persist. `ts_utc` is microseconds since the Unix
    epoch, UTC. `dedup_key`, when set, is a unique key enabling idempotent inserts (journald
    uses `__CURSOR`); None means "no dedup key" (multiple such rows are allowed)."""

    ts_utc: int
    level: str          # one of LEVELS
    source_type: str    # SOURCE_JOURNALD | SOURCE_FILE
    source: str         # unit name / syslog identifier / file path
    host: str           # configured logical host (multi-tenant seam)
    message: str
    dedup_key: str | None = None


def level_from_priority(priority: str | int | None) -> str:
    """Map a syslog PRIORITY to a normalized level (spec §5.3).

    0–3 → error, 4 → warning, 5–6 → info, 7 → debug, missing/unparseable/out-of-range → unknown.
    Accepts the value as journald emits it (a decimal string) or as an int.
    """
    if priority is None:
        return LEVEL_UNKNOWN
    try:
        p = int(priority)
    except (TypeError, ValueError):
        return LEVEL_UNKNOWN
    if 0 <= p <= 3:
        return LEVEL_ERROR
    if p == 4:
        return LEVEL_WARNING
    if 5 <= p <= 6:
        return LEVEL_INFO
    if p == 7:
        return LEVEL_DEBUG
    return LEVEL_UNKNOWN
