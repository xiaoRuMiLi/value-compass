"""SQLite 连接与建库迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fundamental.utils import paths

SCHEMA_VERSION = 1


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """按 schema.sql 建库；用 PRAGMA user_version 做轻量迁移。"""
    conn = connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn
