from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.config import SourceConfig


@dataclass(slots=True)
class DiscoveredFile:
    rel_path: str
    full_path: str
    mtime_ns: int
    size: int


@dataclass(slots=True)
class SourceScanResult:
    files: list[DiscoveredFile]
    errors: list[str]


def scan_source(source: SourceConfig) -> SourceScanResult:
    root = Path(source.root_path)
    files: list[DiscoveredFile] = []
    errors: list[str] = []

    if not root.exists():
        errors.append(f"Source path does not exist: {source.root_path}")
        return SourceScanResult(files=files, errors=errors)

    try:
        iterator: Iterable[Path] = root.rglob("*")
        for path in iterator:
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                rel_path = path.relative_to(root).as_posix()
                files.append(
                    DiscoveredFile(
                        rel_path=rel_path,
                        full_path=str(path),
                        mtime_ns=stat.st_mtime_ns,
                        size=stat.st_size,
                    )
                )
            except (PermissionError, OSError) as exc:
                errors.append(f"{path}: {exc}")
    except (PermissionError, OSError) as exc:
        errors.append(f"{source.root_path}: {exc}")

    return SourceScanResult(files=files, errors=errors)

