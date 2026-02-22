from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import SourceConfig
from app.parser import ParsedSession


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def sync_sources(self, sources: list[SourceConfig]) -> dict[str, int]:
        ids: dict[str, int] = {}
        for src in sources:
            self.conn.execute(
                """
                INSERT INTO sources (name, root_path, distro, user_name, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(root_path) DO UPDATE SET
                  name=excluded.name,
                  distro=excluded.distro,
                  user_name=excluded.user_name,
                  enabled=excluded.enabled,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (src.name, src.root_path, src.distro, src.user, 1 if src.enabled else 0),
            )
        self.conn.commit()
        rows = self.conn.execute("SELECT id, root_path FROM sources").fetchall()
        for row in rows:
            ids[row["root_path"]] = row["id"]
        return ids

    def get_sources(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sources ORDER BY enabled DESC, name"
        ).fetchall()

    def get_enabled_sources(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY name"
        ).fetchall()

    def get_files_map(self, source_id: int) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        return {row["rel_path"]: row for row in rows}

    def upsert_file(
        self,
        *,
        source_id: int,
        rel_path: str,
        full_path: str,
        mtime_ns: int,
        size: int,
        sha1: str | None,
        is_deleted: bool = False,
    ) -> int:
        self.conn.execute(
            """
            INSERT INTO files (
                source_id, rel_path, full_path, mtime_ns, size, sha1, is_deleted,
                parse_status, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(source_id, rel_path) DO UPDATE SET
                full_path=excluded.full_path,
                mtime_ns=excluded.mtime_ns,
                size=excluded.size,
                sha1=COALESCE(excluded.sha1, files.sha1),
                is_deleted=excluded.is_deleted,
                last_seen_at=CURRENT_TIMESTAMP
            """,
            (source_id, rel_path, full_path, mtime_ns, size, sha1, 1 if is_deleted else 0),
        )
        row = self.conn.execute(
            "SELECT id FROM files WHERE source_id = ? AND rel_path = ?",
            (source_id, rel_path),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def mark_deleted_files(self, source_id: int, missing_rel_paths: list[str]) -> int:
        if not missing_rel_paths:
            return 0
        qmarks = ",".join("?" for _ in missing_rel_paths)
        params: list[Any] = [source_id, *missing_rel_paths]
        cur = self.conn.execute(
            f"""
            UPDATE files
            SET is_deleted = 1
            WHERE source_id = ? AND rel_path IN ({qmarks}) AND is_deleted = 0
            """,
            params,
        )
        return cur.rowcount

    def set_file_parse_error(
        self,
        file_id: int,
        *,
        parser_version: str,
        full_path: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE files
            SET parse_status = 'error',
                parser_version = ?,
                error_message = ?,
                last_indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (parser_version, error_message[:1000], file_id),
        )
        self.conn.execute(
            """
            INSERT INTO parse_errors (file_id, full_path, parser_version, error_type, error_message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (file_id, full_path, parser_version, error_type, error_message[:4000]),
        )

    def replace_file_content(
        self,
        *,
        file_id: int,
        parsed: ParsedSession,
        parser_version: str,
    ) -> None:
        self.conn.execute("DELETE FROM sessions WHERE file_id = ?", (file_id,))
        session_cur = self.conn.execute(
            """
            INSERT INTO sessions (
                file_id, session_key, title, started_at, updated_at, raw_meta_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                parsed.session_key,
                parsed.title,
                parsed.started_at,
                parsed.updated_at,
                parsed.raw_meta_json,
            ),
        )
        session_id = int(session_cur.lastrowid)
        if parsed.messages:
            self.conn.executemany(
                """
                INSERT INTO messages (
                    session_id, msg_index, role, message_kind, content, created_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        m.msg_index,
                        m.role,
                        m.message_kind,
                        m.content,
                        m.created_at,
                        m.raw_json,
                    )
                    for m in parsed.messages
                ],
            )
        self.conn.execute(
            """
            UPDATE files
            SET parse_status = 'ok',
                parser_version = ?,
                error_message = NULL,
                is_deleted = 0,
                last_indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (parser_version, file_id),
        )

    def start_scan_run(self, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scan_runs (mode) VALUES (?)",
            (mode,),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_scan_run(
        self,
        run_id: int,
        *,
        status: str,
        files_seen: int,
        files_changed: int,
        files_deleted: int,
        errors_count: int,
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE scan_runs
            SET finished_at = CURRENT_TIMESTAMP,
                status = ?,
                files_seen = ?,
                files_changed = ?,
                files_deleted = ?,
                errors_count = ?,
                notes = ?
            WHERE id = ?
            """,
            (status, files_seen, files_changed, files_deleted, errors_count, notes, run_id),
        )
        self.conn.commit()

    def set_app_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        self.conn.commit()

    def get_app_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def list_sessions(
        self,
        *,
        q: str | None = None,
        source_id: int | None = None,
        distro: str | None = None,
        user_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        sql = """
        SELECT
            s.id,
            s.session_key,
            s.title,
            s.started_at,
            s.updated_at,
            f.full_path,
            f.rel_path,
            src.id AS source_id,
            src.name AS source_name,
            src.distro,
            src.user_name,
            (
              SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id
            ) AS messages_count
        FROM sessions s
        JOIN files f ON f.id = s.file_id
        JOIN sources src ON src.id = f.source_id
        WHERE f.is_deleted = 0
        """
        params: list[Any] = []
        if source_id is not None:
            sql += " AND src.id = ?"
            params.append(source_id)
        if distro:
            sql += " AND src.distro = ?"
            params.append(distro)
        if user_name:
            sql += " AND src.user_name = ?"
            params.append(user_name)
        if q:
            sql += """
            AND (
              COALESCE(s.title, '') LIKE ?
              OR EXISTS (
                  SELECT 1
                  FROM messages_fts fts
                  JOIN messages m2 ON m2.id = fts.rowid
                  WHERE m2.session_id = s.id AND fts.content MATCH ?
              )
            )
            """
            params.extend([f"%{q}%", q])
        sql += """
        ORDER BY COALESCE(s.updated_at, s.started_at, s.created_at) DESC, s.id DESC
        LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                s.*,
                f.full_path,
                f.rel_path,
                src.name AS source_name,
                src.distro,
                src.user_name
            FROM sessions s
            JOIN files f ON f.id = s.file_id
            JOIN sources src ON src.id = f.source_id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()

    def get_messages_for_session(
        self,
        session_id: int,
        *,
        include_service: bool = False,
        ofac: bool = False,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT id, msg_index, role, message_kind, content, created_at
            FROM messages
            WHERE session_id = ?
        """
        params: list[Any] = [session_id]
        if ofac:
            # Pull full ordered stream and collapse to: user block + last final Codex reply.
            sql += " ORDER BY msg_index ASC, id ASC"
            rows = self.conn.execute(sql, params).fetchall()
            return self._apply_ofac_view(rows)
        elif not include_service:
            sql += " AND COALESCE(message_kind, 'unknown') NOT IN ('assistant_reasoning', 'assistant_service', 'assistant')"
        sql += " ORDER BY msg_index ASC, id ASC"
        return self.conn.execute(sql, params).fetchall()

    @staticmethod
    def _apply_ofac_view(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
        output: list[sqlite3.Row] = []
        user_block: list[sqlite3.Row] = []
        last_final: sqlite3.Row | None = None

        def flush_block() -> None:
            nonlocal user_block, last_final
            if not user_block:
                return
            output.extend(user_block)
            if last_final is not None:
                output.append(last_final)
            user_block = []
            last_final = None

        for row in rows:
            kind = str(row["message_kind"] or "unknown")
            if kind == "user":
                if user_block and last_final is not None:
                    flush_block()
                user_block.append(row)
                continue

            if kind == "assistant_final":
                if user_block:
                    # Keep only the latest final answer until the next user message arrives.
                    last_final = row
                continue

            # All non-user / non-final messages are ignored in OFAC mode.

        flush_block()
        return output

    def count_reasoning_for_session(self, session_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM messages
            WHERE session_id = ? AND COALESCE(message_kind, 'unknown') = 'assistant_reasoning'
            """,
            (session_id,),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def count_service_for_session(self, session_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM messages
            WHERE session_id = ?
              AND COALESCE(message_kind, 'unknown') IN ('assistant_reasoning', 'assistant_service', 'assistant')
            """,
            (session_id,),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def count_non_ofac_for_session(self, session_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM messages
            WHERE session_id = ?
              AND COALESCE(message_kind, 'unknown') NOT IN ('user', 'assistant_final')
            """,
            (session_id,),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def list_scan_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def list_parse_errors(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM parse_errors ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def get_dashboard_stats(self) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM sources WHERE enabled = 1) AS enabled_sources,
              (SELECT COUNT(*) FROM files WHERE is_deleted = 0) AS active_files,
              (SELECT COUNT(*) FROM sessions s
               JOIN files f ON f.id = s.file_id
               WHERE f.is_deleted = 0) AS sessions_count,
              (SELECT COUNT(*) FROM messages m
               JOIN sessions s ON s.id = m.session_id
               JOIN files f ON f.id = s.file_id
               WHERE f.is_deleted = 0) AS messages_count
            """
        ).fetchone()
        return dict(row) if row is not None else {}

    def search_messages(
        self,
        q: str,
        limit: int = 50,
        *,
        include_service: bool = False,
    ) -> list[sqlite3.Row]:
        if not q.strip():
            return []
        sql = """
            SELECT
                m.id,
                m.content,
                m.role,
                m.message_kind,
                m.created_at,
                s.id AS session_id,
                COALESCE(s.title, s.session_key) AS session_title,
                src.name AS source_name,
                snippet(messages_fts, 0, '[', ']', ' … ', 12) AS snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            JOIN files f ON f.id = s.file_id
            JOIN sources src ON src.id = f.source_id
            WHERE messages_fts.content MATCH ? AND f.is_deleted = 0
        """
        params: list[Any] = [q]
        if not include_service:
            sql += " AND COALESCE(m.message_kind, 'unknown') IN ('user', 'assistant_final')"
        sql += """
            ORDER BY rank
            LIMIT ?
        """
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def as_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def dump_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)
