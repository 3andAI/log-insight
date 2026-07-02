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


def load_config(path: str | Path) -> Config:
    """Parse a TOML config file. Missing keys fall back to the dataclass defaults."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    db = data.get("database", {})
    srv = data.get("server", {})
    col = data.get("collector", {})
    jd = col.get("journald", {})

    return Config(
        database=DatabaseConfig(path=db.get("path", "logs.db")),
        server=ServerConfig(
            host=srv.get("host", "127.0.0.1"),
            port=int(srv.get("port", 8000)),
            allow_nonloopback=bool(srv.get("allow_nonloopback", False)),
        ),
        collector=CollectorConfig(
            host=col.get("host", "localhost"),
            watched_files=list(col.get("watched_files", [])),
            journald=JournaldConfig(
                initial_backfill=jd.get("initial_backfill", "24h"),
                max_batch=int(jd.get("max_batch", 5000)),
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
