"""数据访问基础方法。"""

from __future__ import annotations

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
