from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str | Path | None = None) -> None:
    if schema_path is None:
        schema_path = Path(__file__).with_name("schema.sql")
    schema_text = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(schema_text)
    _apply_runtime_migrations(conn)
    conn.commit()


def _apply_runtime_migrations(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "message_kind" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN message_kind TEXT NOT NULL DEFAULT 'unknown'"
        )
        conn.execute(
            """
            UPDATE messages
            SET message_kind = CASE lower(role)
                WHEN 'assistant' THEN 'assistant_final'
                WHEN 'user' THEN 'user'
                WHEN 'system' THEN 'system'
                WHEN 'tool' THEN 'tool'
                ELSE 'unknown'
            END
            """
        )
