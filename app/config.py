from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SourceConfig:
    name: str
    root_path: str
    distro: str | None = None
    user: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class AppConfig:
    db_path: str = "data/app.db"
    host: str = "127.0.0.1"
    port: int = 8000
    scan_on_startup: bool = True
    polling_enabled: bool = False
    polling_interval_sec: int = 30
    sources: list[SourceConfig] = field(default_factory=list)

    @property
    def db_file(self) -> Path:
        return Path(self.db_path)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_config(data: dict[str, Any]) -> AppConfig:
    sources = [SourceConfig(**src) for src in data.get("sources", [])]
    return AppConfig(
        db_path=data.get("db_path", "data/app.db"),
        host=data.get("host", "127.0.0.1"),
        port=int(data.get("port", 8000)),
        scan_on_startup=bool(data.get("scan_on_startup", True)),
        polling_enabled=bool(data.get("polling_enabled", False)),
        polling_interval_sec=int(data.get("polling_interval_sec", 30)),
        sources=sources,
    )


def load_config() -> AppConfig:
    cwd = Path.cwd()
    config_path = cwd / "config.json"
    if config_path.exists():
        return _parse_config(_load_json(config_path))

    example_path = cwd / "config.example.json"
    if example_path.exists():
        return _parse_config(_load_json(example_path))

    return AppConfig()

