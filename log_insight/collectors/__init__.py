"""Log collectors. `journald` (M1) and `files` (M4) each turn a source into LogRecords."""


class CollectorError(Exception):
    """A collector could not read its source (e.g. journalctl missing or failed). Raised so
    `collect.py` can catch it per-collector and let other collectors still run (spec §5.4)."""

