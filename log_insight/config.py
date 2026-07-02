"""Load `config.toml` into typed settings, with defaults, plus the loopback helper the
web-app bind guard uses (spec G1)."""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DatabaseConfig:
    path: str = "logs.db"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    allow_nonloopback: bool = False


@dataclass
class JournaldConfig:
    initial_backfill: str = "24h"
    max_batch: int = 5000


@dataclass
class CollectorConfig:
    host: str = "localhost"
    watched_files: list[str] = field(default_factory=list)
    journald: JournaldConfig = field(default_factory=JournaldConfig)


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)


class ConfigError(Exception):
    """A config value has the wrong type or is out of range. Raised eagerly at load time so a
    typo can never silently weaken a security control (e.g. the G1 bind guard)."""


def _as_str(value, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string, got {value!r}")
    return value


def _as_bool(value, key: str) -> bool:
    # No coercion: TOML has a native boolean, so anything else (e.g. "false") is a mistake we
    # must reject rather than truthy-coerce — this flag gates non-loopback binding (G1).
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean true/false, got {value!r}")
    return value


def _as_positive_int(value, key: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{key} must be a positive integer, got {value!r}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key} must be <= {maximum}, got {value!r}")
    return value


def load_config(path: str | Path) -> Config:
    """Parse and validate a TOML config file. Missing keys fall back to defaults; present keys
    are type/range checked (raises ConfigError). Security-sensitive values are validated, not
    coerced (spec G1)."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    db = data.get("database", {})
    srv = data.get("server", {})
    col = data.get("collector", {})
    jd = col.get("journald", {})

    watched = col.get("watched_files", [])
    if not isinstance(watched, list) or not all(isinstance(w, str) for w in watched):
        raise ConfigError(f"collector.watched_files must be a list of strings, got {watched!r}")

    return Config(
        database=DatabaseConfig(path=_as_str(db.get("path", "logs.db"), "database.path")),
        server=ServerConfig(
            host=_as_str(srv.get("host", "127.0.0.1"), "server.host"),
            port=_as_positive_int(srv.get("port", 8000), "server.port", maximum=65535),
            allow_nonloopback=_as_bool(
                srv.get("allow_nonloopback", False), "server.allow_nonloopback"
            ),
        ),
        collector=CollectorConfig(
            host=_as_str(col.get("host", "localhost"), "collector.host"),
            watched_files=list(watched),
            journald=JournaldConfig(
                initial_backfill=_as_str(
                    jd.get("initial_backfill", "24h"), "collector.journald.initial_backfill"
                ),
                max_batch=_as_positive_int(
                    jd.get("max_batch", 5000), "collector.journald.max_batch"
                ),
            ),
        ),
    )


def is_loopback(host: str) -> bool:
    """True if `host` is loopback ("localhost", 127.0.0.0/8, or ::1). Used by the M2 bind
    guard: a non-loopback bind is refused unless `server.allow_nonloopback` is set (spec G1)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
