from pathlib import Path

from log_insight import collect, db
from log_insight.collectors import CollectorError

FIXTURE = Path(__file__).parent / "fixtures" / "journald_sample.jsonl"


def fixture_reader(_argv):
    yield from FIXTURE.read_text().splitlines()


def write_config(tmp_path):
    db_path = tmp_path / "logs.db"
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[database]\npath = "{db_path}"\n\n'
        '[collector]\nhost = "target-host"\n'
    )
    return cfg, db_path


def test_collect_persists_records_and_advances_cursor(tmp_path):
    cfg, db_path = write_config(tmp_path)
    collect.run(str(cfg), journald_reader=fixture_reader)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 11
    assert db.get_state(conn, collect.JOURNALD_CURSOR_KEY).endswith("deadbeef12")
    conn.close()


def test_collect_is_idempotent(tmp_path):
    cfg, db_path = write_config(tmp_path)
    collect.run(str(cfg), journald_reader=fixture_reader)
    collect.run(str(cfg), journald_reader=fixture_reader)  # same data again

    conn = db.connect(db_path)
    # dedup_key (__CURSOR) keeps the re-run from duplicating anything.
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 11
    conn.close()


def test_collect_survives_collector_failure(tmp_path):
    cfg, db_path = write_config(tmp_path)

    def failing(_argv):
        raise CollectorError("journalctl exploded")
        yield  # pragma: no cover

    collect.run(str(cfg), journald_reader=failing)  # must not raise

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    assert db.get_state(conn, collect.JOURNALD_CURSOR_KEY) is None
    conn.close()
