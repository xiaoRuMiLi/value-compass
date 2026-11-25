"""SQLite 连接与建库迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fundamental.utils import paths

SCHEMA_VERSION = 6  # 5→6: financials.eps_ttm（港股拆股前后口径一致）

_MIGRATIONS = {
    1: ["ALTER TABLE snapshots ADD COLUMN pct_change REAL;"],
    2: [
        "CREATE TABLE industries ("
        "id INTEGER PRIMARY KEY, code TEXT, name TEXT NOT NULL, "
        "source TEXT NOT NULL DEFAULT 'eastmoney', UNIQUE(name, source));",
        "ALTER TABLE stocks ADD COLUMN industry_id INTEGER REFERENCES industries(id);",
    ],
    3: [
        "ALTER TABLE financials ADD COLUMN operating_profit REAL;",
        "ALTER TABLE financials ADD COLUMN pretax_profit REAL;",
    ],
    4: [
        # 港股分红以港币计价，需记录币种以免 DDM 把港币 DPS 当成人民币。
        "ALTER TABLE dividends ADD COLUMN currency TEXT DEFAULT 'CNY';",
    ],
    5: [
        # 港股 F10 直接披露 EPS_TTM；用它替代累计 EPS 推导，避免拆股前后口径混用。
        "ALTER TABLE financials ADD COLUMN eps_ttm REAL;",
    ],
}


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """按 schema.sql 建库；用 PRAGMA user_version 做轻量迁移。

    整个迁移过程包在显式事务中：建表/迁移语句与版本号更新要么一起生效，
    要么一起回滚，避免中途失败留下"表已建、版本号未更新"的半迁移库。
    """
    conn = connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return conn

    # executescript 会先隐式提交已开启的事务，因此把 BEGIN 一并放进脚本，
    # 保证脚本内所有语句与 version 更新处于同一事务。
    script_parts = ["BEGIN IMMEDIATE;"]
    if version == 0:
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        script_parts.append(schema)
    else:
        for current in range(version, SCHEMA_VERSION):
            script_parts.extend(_MIGRATIONS.get(current, []))
    script_parts.append(f"PRAGMA user_version = {SCHEMA_VERSION};")
    script_parts.append("COMMIT;")

    try:
        conn.executescript("\n".join(script_parts))
    except Exception:
        # 迁移失败：回滚并关闭连接（连接所有权未转移给调用方）
        conn.rollback()
        conn.close()
        raise
    return conn
