"""数据访问基础方法。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Optional, Sequence

from fundamental.models import Market, Stock


def get_stock_id(conn: sqlite3.Connection, code: str, market: Market | str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM stocks WHERE code = ? AND market = ?",
        (code, Market(market).value),
    ).fetchone()
    return row["id"] if row else None


def get_or_create_stock(conn: sqlite3.Connection, stock: Stock) -> int:
    existing = get_stock_id(conn, stock.code, stock.market)
    if existing is not None:
        return existing
    cursor = conn.execute(
        """
        INSERT INTO stocks (code, market, name, industry, sector, list_date, is_st)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stock.code,
            stock.market.value,
            stock.name,
            stock.industry,
            stock.sector,
            stock.list_date,
            int(stock.is_st),
        ),
    )
    return int(cursor.lastrowid)


def get_or_create_industry(
    conn: sqlite3.Connection, name: str, code: str | None = None, source: str = "eastmoney"
) -> int:
    """按名称幂等获取行业主表 id，形成行业 → 多股的一对多关系。"""

    row = conn.execute(
        "SELECT id FROM industries WHERE name = ? AND source = ?", (name, source)
    ).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO industries (code, name, source) VALUES (?, ?, ?)", (code, name, source)
    )
    return int(cursor.lastrowid)


def set_stock_industry(
    conn: sqlite3.Connection, stock_id: int, industry_id: int, industry: str, sector: str | None
) -> None:
    conn.execute(
        "UPDATE stocks SET industry_id = ?, industry = ?, sector = ? WHERE id = ?",
        (industry_id, industry, sector, stock_id),
    )


def insert_many(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> int:
    """批量幂等写入（INSERT OR IGNORE），返回实际插入的行数。"""
    col_sql = ", ".join(columns)
    placeholder_sql = ", ".join("?" for _ in columns)
    sql = f"INSERT OR IGNORE INTO {table} ({col_sql}) VALUES ({placeholder_sql})"
    before = conn.total_changes
    conn.executemany(sql, rows)
    return conn.total_changes - before


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
    return conn.execute(sql, params).fetchone()


def fetch_all(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


# ===== 单期指标解释缓存（供图表悬停提示复用） =====

def save_metric_explanation(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    symbol: str,
    period: str,
    scope: str,
    stmt_type: str,
    keys: list[str],
    fingerprint: str,
    version: int,
    summary: str,
    payload: dict[str, Any],
    generated_at: str,
) -> None:
    """整行覆盖写入；cache_key 已含标的/报告期/口径/科目组合。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO metric_explanation_cache
          (cache_key, symbol, period, scope, stmt_type, keys_json, fingerprint,
           version, summary, payload, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cache_key, symbol, period, scope, stmt_type,
            json.dumps(keys, ensure_ascii=False), fingerprint, version, summary,
            json.dumps(payload, ensure_ascii=False), generated_at,
        ),
    )


def get_metric_explanation(conn: sqlite3.Connection, cache_key: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT payload FROM metric_explanation_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_metric_explanations(conn: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    """返回该标的全部已缓存解释的索引（摘要 + 元数据，不取完整 payload）。"""
    rows = conn.execute(
        """
        SELECT period, scope, stmt_type, keys_json, fingerprint, version, summary, generated_at
        FROM metric_explanation_cache WHERE symbol = ? ORDER BY generated_at DESC
        """,
        (symbol,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            keys = json.loads(row["keys_json"])
        except (TypeError, ValueError):
            keys = []
        result.append(
            {
                "period": row["period"],
                "scope": row["scope"],
                "stmt_type": row["stmt_type"],
                "keys": keys if isinstance(keys, list) else [],
                "fingerprint": row["fingerprint"],
                "version": row["version"],
                "summary": row["summary"],
                "generated_at": row["generated_at"],
            }
        )
    return result
