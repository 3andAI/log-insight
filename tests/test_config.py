import textwrap

import pytest

from log_insight.config import ConfigError, is_loopback, load_config


def write(tmp_path, body):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_full_config(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            """
            [database]
            path = "/var/lib/log-insight/logs.db"

            [server]
            host = "0.0.0.0"
            port = 9000
            allow_nonloopback = true

            [collector]
            host = "prod-1"
            watched_files = ["/var/log/syslog", "/var/log/auth.log"]

            [collector.journald]
            initial_backfill = "boot"
            max_batch = 100
            """,
        )
    )
    assert cfg.database.path == "/var/lib/log-insight/logs.db"
    assert (cfg.server.host, cfg.server.port, cfg.server.allow_nonloopback) == ("0.0.0.0", 9000, True)
    assert cfg.collector.host == "prod-1"
    assert cfg.collector.watched_files == ["/var/log/syslog", "/var/log/auth.log"]
    assert (cfg.collector.journald.initial_backfill, cfg.collector.journald.max_batch) == ("boot", 100)


def test_defaults_when_keys_missing(tmp_path):
    cfg = load_config(write(tmp_path, "[database]\n"))
    assert cfg.database.path == "logs.db"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.server.allow_nonloopback is False
    assert cfg.collector.watched_files == []
    assert cfg.collector.journald.max_batch == 5000


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.0.0.5", "::1"])
def test_is_loopback_true(host):
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.1", "192.168.1.2", "example.com", ""])
def test_is_loopback_false(host):
    assert is_loopback(host) is False


# --- validation: security-sensitive values are validated, not coerced (spec G1) ---

def test_allow_nonloopback_string_is_rejected(tmp_path):
    # The footgun: bool("false") would be True and silently disable the bind guard.
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, '[server]\nallow_nonloopback = "false"\n'))


@pytest.mark.parametrize("value", ["0", "-1", '"5000"', "1.5"])
def test_max_batch_must_be_positive_int(tmp_path, value):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, f"[collector.journald]\nmax_batch = {value}\n"))


@pytest.mark.parametrize("value", ["0", "70000", "-5"])
def test_port_must_be_in_range(tmp_path, value):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, f"[server]\nport = {value}\n"))


def test_watched_files_must_be_list_of_strings(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "[collector]\nwatched_files = \"/var/log/syslog\"\n"))
