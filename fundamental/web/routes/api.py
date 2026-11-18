"""最小 API：从 SQLite 读取个股数据。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from fundamental.services.analyze import analyze_stock
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol

router = APIRouter(prefix="/api", tags=["stock"])


def _resolve_stock(conn, symbol: str) -> int:
    try:
        code, market = parse_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stock_id = repository.get_stock_id(conn, code, market)
    if stock_id is None:
        raise HTTPException(status_code=404, detail="股票不存在，请先运行 fetch_data")
    return stock_id


def _rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


@router.get("/stock/{symbol}/overview")
def get_overview(symbol: str) -> dict[str, Any]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        stock = repository.fetch_one(
            conn,
            "SELECT id, code, market, name, industry, sector, list_date, is_st FROM stocks WHERE id = ?",
            (stock_id,),
        )
        snapshot = repository.fetch_one(
            conn,
            "SELECT fetched_at, price, market_cap, turnover_rate FROM snapshots "
            "WHERE stock_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (stock_id,),
        )
        profile = repository.fetch_one(
            conn, "SELECT * FROM company_profile WHERE stock_id = ?", (stock_id,)
        )
        return {
            "stock": dict(stock) if stock else None,
            "profile": dict(profile) if profile else None,
            "snapshot": dict(snapshot) if snapshot else None,
        }
    finally:
        conn.close()


@router.get("/stock/{symbol}/financials")
def get_financials(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    report_types: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = "SELECT * FROM financials WHERE stock_id = ?"
        params: list[Any] = [stock_id]
        if start:
            sql += " AND report_date >= ?"
            params.append(start)
        if end:
            sql += " AND report_date <= ?"
            params.append(end)
        sql += " ORDER BY report_date DESC"
        rows = repository.fetch_all(conn, sql, params)
        wanted = set(report_types.split(",")) if report_types else None
        if wanted:
            rows = [row for row in rows if row["report_type"] in wanted]
        return _rows(rows)
    finally:
        conn.close()


@router.get("/stock/{symbol}/kline")
def get_kline(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = (
            "SELECT trade_date, open, high, low, close, volume, amount, adj_factor "
            "FROM kline WHERE stock_id = ?"
        )
        params: list[Any] = [stock_id]
        if start:
            sql += " AND trade_date >= ?"
            params.append(start)
        if end:
            sql += " AND trade_date <= ?"
            params.append(end)
        sql += " ORDER BY trade_date ASC"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/analysis")
def get_analysis(symbol: str) -> dict[str, Any]:
    result = analyze_stock(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
