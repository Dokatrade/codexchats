from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import AppConfig
from app.parser import PARSER_VERSION, parse_session_file
from app.repository import Repository
from app.scanner import scan_source


@dataclass(slots=True)
class ScanSummary:
    status: str
    mode: str
    files_seen: int
    files_changed: int
    files_deleted: int
    errors_count: int
    notes: str | None = None


class Indexer:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo
        self._lock = threading.Lock()

    def should_run_full_scan(self) -> bool:
        return self.repo.get_app_state("initial_scan_done") != "true"

    def run_startup_scan(self) -> ScanSummary | None:
        if not self.config.scan_on_startup:
            return None
        mode = "full" if self.should_run_full_scan() else "quick"
        return self.run_scan(mode=mode)

    def run_scan(self, mode: str = "quick") -> ScanSummary:
        if mode not in {"quick", "full"}:
            raise ValueError(f"Unsupported scan mode: {mode}")

        with self._lock:
            return self._run_scan_locked(mode)

    def _run_scan_locked(self, mode: str) -> ScanSummary:
        run_id = self.repo.start_scan_run(mode)
        files_seen = 0
        files_changed = 0
        files_deleted = 0
        errors_count = 0
        notes: list[str] = []
        status = "ok"

        try:
            source_id_map = self.repo.sync_sources(self.config.sources)

            for src_cfg in self.config.sources:
                if not src_cfg.enabled:
                    continue
                source_id = source_id_map.get(src_cfg.root_path)
                if source_id is None:
                    errors_count += 1
                    notes.append(f"Missing source id for {src_cfg.root_path}")
                    continue

                existing = self.repo.get_files_map(source_id)
                seen: set[str] = set()
                scan_result = scan_source(src_cfg)

                if scan_result.errors:
                    errors_count += len(scan_result.errors)
                    notes.extend(scan_result.errors[:5])

                for file_meta in scan_result.files:
                    files_seen += 1
                    seen.add(file_meta.rel_path)
                    old = existing.get(file_meta.rel_path)
                    changed = self._is_changed(old, file_meta.mtime_ns, file_meta.size, mode)

                    sha1 = None
                    if changed:
                        sha1 = self._sha1_file(file_meta.full_path)
                    file_id = self.repo.upsert_file(
                        source_id=source_id,
                        rel_path=file_meta.rel_path,
                        full_path=file_meta.full_path,
                        mtime_ns=file_meta.mtime_ns,
                        size=file_meta.size,
                        sha1=sha1,
                        is_deleted=False,
                    )

                    if not changed:
                        continue

                    files_changed += 1
                    try:
                        parsed = parse_session_file(file_meta.full_path)
                        self.repo.replace_file_content(
                            file_id=file_id, parsed=parsed, parser_version=PARSER_VERSION
                        )
                    except Exception as exc:  # noqa: BLE001 - MVP parser must be fault-tolerant
                        errors_count += 1
                        self.repo.set_file_parse_error(
                            file_id,
                            parser_version=PARSER_VERSION,
                            full_path=file_meta.full_path,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )

                missing = [rel for rel in existing.keys() if rel not in seen]
                files_deleted += self.repo.mark_deleted_files(source_id, missing)
                self.repo.conn.commit()

            if mode == "full":
                self.repo.set_app_state("initial_scan_done", "true")
            self.repo.set_app_state("last_scan_mode", mode)
            self.repo.set_app_state("last_scan_status", "ok" if errors_count == 0 else "ok_with_errors")
        except Exception as exc:  # noqa: BLE001
            status = "error"
            errors_count += 1
            notes.append(f"Fatal scan error: {type(exc).__name__}: {exc}")
        finally:
            summary = ScanSummary(
                status=status if status == "error" else ("ok" if errors_count == 0 else "ok_with_errors"),
                mode=mode,
                files_seen=files_seen,
                files_changed=files_changed,
                files_deleted=files_deleted,
                errors_count=errors_count,
                notes="\n".join(notes[:20]) if notes else None,
            )
            self.repo.finish_scan_run(
                run_id,
                status=summary.status,
                files_seen=files_seen,
                files_changed=files_changed,
                files_deleted=files_deleted,
                errors_count=errors_count,
                notes=summary.notes,
            )
            return summary

    @staticmethod
    def _is_changed(old_row, mtime_ns: int, size: int, mode: str) -> bool:
        if mode == "full" or old_row is None:
            return True
        old_parser_version = (old_row["parser_version"] or "") if "parser_version" in old_row.keys() else ""
        return (
            int(old_row["mtime_ns"]) != mtime_ns
            or int(old_row["size"]) != size
            or int(old_row["is_deleted"]) == 1
            or old_parser_version != PARSER_VERSION
        )

    @staticmethod
    def _sha1_file(full_path: str) -> str | None:
        path = Path(full_path)
        try:
            h = hashlib.sha1()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None
