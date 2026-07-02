from pathlib import Path

import pytest

from log_insight.collectors import CollectorError
from log_insight.collectors.journald import (
    _build_argv,
    _journalctl_reader,
    collect_journald,
)

FIXTURE = Path(__file__).parent / "fixtures" / "journald_sample.jsonl"
# Cursors from the fixture (see journald_probe_notes.md).
CURSOR_12 = "s=1a2b3c;i=c;b=0f0f;m=abc;t=00;x=deadbeef12"
CURSOR_3 = "s=1a2b3c;i=3;b=0f0f;m=abc;t=00;x=deadbeef03"


def fixture_reader(_argv):
    """A reader that ignores argv and replays the golden fixture lines."""
    yield from FIXTURE.read_text().splitlines()


def lines_reader(lines):
    def reader(_argv):
        yield from lines
    return reader


def failing_reader(_argv):
    raise CollectorError("boom")
    yield  # pragma: no cover — makes this a generator


def collect(reader, after_cursor=None, max_batch=5000, backfill="24h"):
    return collect_journald(
        after_cursor, "target-host",
        initial_backfill=backfill, max_batch=max_batch, reader=reader,
    )


# --- golden fixture: the full expected mapping (probe notes) ---

def test_full_fixture_counts_and_cursor():
    batch = collect(fixture_reader)
    assert len(batch.records) == 11          # entries 1–11; #12 dropped (no ts)
    assert batch.skipped == 2                # #12 missing ts + malformed line 13
    assert batch.new_cursor == CURSOR_12     # last *parsed* entry (BP8 option B)


def test_full_fixture_levels_in_order():
    recs = collect(fixture_reader).records
    assert [r.level for r in recs] == [
        "info", "error", "warning", "info", "debug",
        "error", "info", "info", "info", "info", "unknown",
    ]


def test_full_fixture_source_fallback_chain():
    recs = collect(fixture_reader).records
    by = {r.message: r.source for r in recs}
    # unit present, identifier-only, comm-only, and none-at-all.
    assert by["Started Session 42 of user svc."] == "session-42.scope"  # _SYSTEMD_UNIT
    assert by["kernel panic imminent"] == "kernel"                       # SYSLOG_IDENTIFIER
    assert by["pam_unix session opened"] == "sshd"                       # _COMM
    assert by["orphan message with no source"] == "unknown"             # terminal fallback


def test_full_fixture_host_and_source_type_and_dedup():
    recs = collect(fixture_reader).records
    assert all(r.host == "target-host" for r in recs)
    assert all(r.source_type == "journald" for r in recs)
    # dedup_key carries __CURSOR so collect.py inserts idempotently.
    assert all(r.dedup_key and r.dedup_key.startswith("s=1a2b3c;") for r in recs)


def test_binary_message_is_decoded():
    recs = collect(fixture_reader).records
    weird = [r for r in recs if r.source == "weird.service"]
    assert len(weird) == 1
    assert weird[0].message == "binary hejä"


# --- bounded batch (spec §5.2) ---

def test_max_batch_truncates_and_advances_cursor():
    batch = collect(fixture_reader, max_batch=3)
    assert len(batch.records) == 3
    assert batch.skipped == 0
    assert batch.new_cursor == CURSOR_3  # cursor of the 3rd parsed entry


# --- cursor / empty / error handling (spec §5.4) ---

def test_empty_read_keeps_existing_cursor():
    batch = collect(lines_reader([]), after_cursor="s=old")
    assert batch.records == []
    assert batch.skipped == 0
    assert batch.new_cursor == "s=old"  # unchanged (spec §5.1)


def test_first_run_empty_returns_none_cursor():
    batch = collect(lines_reader([]), after_cursor=None)
    assert batch.new_cursor is None  # nothing to persist


def test_malformed_only_lines_skipped_not_advancing():
    batch = collect(lines_reader(['{bad', 'also ] bad']), after_cursor="s=old")
    assert batch.records == []
    assert batch.skipped == 2
    assert batch.new_cursor == "s=old"  # trailing malformed does not advance


def test_stale_cursor_falls_back_to_backfill():
    # Reader fails when a cursor is used, succeeds on the backfill read.
    def reader(argv):
        if "--after-cursor" in argv:
            raise CollectorError("stale cursor")
        yield from FIXTURE.read_text().splitlines()

    batch = collect_journald(
        "s=stale", "target-host", initial_backfill="24h", max_batch=5000, reader=reader
    )
    assert len(batch.records) == 11  # fallback succeeded
    assert batch.new_cursor == CURSOR_12


def test_failure_on_first_run_raises():
    with pytest.raises(CollectorError):
        collect(failing_reader, after_cursor=None)


# --- argv construction ---

def test_argv_uses_after_cursor_when_present():
    argv = _build_argv("s=abc", "24h")
    assert "--after-cursor" in argv and "s=abc" in argv
    assert "--since" not in argv


def test_argv_backfill_duration():
    assert _build_argv(None, "24h")[-2:] == ["--since", "-24h"]


def test_argv_backfill_boot():
    assert "--boot" in _build_argv(None, "boot")


def test_argv_backfill_all_is_unbounded():
    argv = _build_argv(None, "all")
    assert "--since" not in argv and "--boot" not in argv


# --- default reader subprocess behavior (spec §5.4) ---

def test_reader_yields_stdout_lines():
    argv = ["sh", "-c", "printf 'a\\nb\\n'"]
    assert [ln.strip() for ln in _journalctl_reader(argv)] == ["a", "b"]


def test_reader_raises_on_nonzero_exit_and_includes_stderr():
    # stdout drains fine; process exits non-zero on its own with stderr output.
    argv = ["sh", "-c", "printf 'line1\\n'; printf 'boom-detail\\n' >&2; exit 3"]
    with pytest.raises(CollectorError) as excinfo:
        list(_journalctl_reader(argv))
    assert "exited 3" in str(excinfo.value)
    assert "boom-detail" in str(excinfo.value)


def test_reader_raises_when_command_missing():
    with pytest.raises(CollectorError):
        list(_journalctl_reader(["definitely-not-a-real-binary-xyz-123"]))


def test_reader_early_break_does_not_raise():
    # Consumer stops early (bounded batch): terminating the process is not a failure.
    argv = ["sh", "-c", "printf 'a\\nb\\nc\\n'; exit 3"]
    gen = _journalctl_reader(argv)
    assert next(gen).strip() == "a"
    gen.close()  # GeneratorExit — must not raise CollectorError


def test_reader_survives_large_stderr_without_deadlock():
    # >64KB of stderr (past a typical pipe buffer) while stdout is short: a pipe would deadlock;
    # the temp-file redirect must not. Guarded so a regression fails instead of hanging forever.
    argv = ["sh", "-c", "printf 'ok\\n'; yes X | head -c 200000 >&2; exit 1"]
    with pytest.raises(CollectorError):
        list(_journalctl_reader(argv))
