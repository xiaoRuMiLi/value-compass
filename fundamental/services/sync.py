"""数据同步：取数 → 归一化 → 入库。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fundamental.models import StatementType
from fundamental.providers.financial.eastmoney import EastmoneyFinancialProvider
from fundamental.providers.quote.eastmoney import EastmoneyQuoteProvider
from fundamental.storage import db, repository


def sync_stock(
    symbol: str,
    db_path: Path | None = None,
    kline_start: str = "1990-01-01",
) -> dict[str, int]:
    """拉取单只股票的全部数据并写入 SQLite，返回各表写入行数。"""
    conn = db.init_db(db_path)
    quote = EastmoneyQuoteProvider()
    financial = EastmoneyFinancialProvider()

    stock = quote.fetch_stock_info(symbol)
    stock_id = repository.get_or_create_stock(conn, stock)
    today = date.today().isoformat()
    stats: dict[str, int] = {}

    snapshot = quote.fetch_snapshot(symbol)
    repository.insert_many(
        conn,
        "snapshots",
        ["stock_id", "fetched_at", "price", "market_cap", "turnover_rate"],
        [(stock_id, snapshot.fetched_at, snapshot.price, snapshot.market_cap, snapshot.turnover_rate)],
    )
    stats["snapshots"] = 1

    bars = quote.fetch_kline(symbol, kline_start, today)
    stats["kline"] = repository.insert_many(
        conn,
        "kline",
        ["stock_id", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj_factor"],
        [
            (stock_id, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.amount, b.adj_factor)
            for b in bars
        ],
    )

    valuations = quote.fetch_valuations(symbol)
    stats["valuations"] = repository.insert_many(
        conn,
        "valuations",
        [
            "stock_id", "trade_date", "close_price", "pe_ttm", "pe_lar", "pb_mrq",
            "ps_ttm", "peg", "pcf_ocf_ttm", "total_market_cap",
        ],
        [
            (
                stock_id, v.trade_date, v.close_price, v.pe_ttm, v.pe_lar, v.pb_mrq,
                v.ps_ttm, v.peg, v.pcf_ocf_ttm, v.total_market_cap,
            )
            for v in valuations
        ],
    )

    financials = financial.fetch_financials(symbol)
    stats["financials"] = repository.insert_many(
        conn,
        "financials",
        [
            "stock_id", "report_date", "report_type", "revenue", "net_profit",
            "net_profit_deducted", "gross_margin", "net_margin", "roe", "roic",
            "debt_ratio", "current_ratio", "ocf", "fcf", "eps", "bps",
            "revenue_yoy", "net_profit_yoy", "audit_opinion",
        ],
        [
            (
                stock_id, f.report_date, f.report_type.value, f.revenue, f.net_profit,
                f.net_profit_deducted, f.gross_margin, f.net_margin, f.roe, f.roic,
                f.debt_ratio, f.current_ratio, f.ocf, f.fcf, f.eps, f.bps,
                f.revenue_yoy, f.net_profit_yoy, _enum_value(f.audit_opinion),
            )
            for f in financials
        ],
    )

    statement_rows = []
    for statement_type in (StatementType.INCOME, StatementType.BALANCE, StatementType.CASHFLOW):
        statement_rows.extend(financial.fetch_statement_items(symbol, statement_type))
    stats["statement_items"] = repository.insert_many(
        conn,
        "statement_items",
        ["stock_id", "report_date", "report_type", "statement_type", "item_code", "item_name", "item_value", "item_yoy"],
        [
            (
                stock_id, s.report_date, s.report_type.value, s.statement_type.value,
                s.item_code, s.item_name, s.item_value, s.item_yoy,
            )
            for s in statement_rows
        ],
    )

    composition = financial.fetch_revenue_composition(symbol)
    stats["revenue_composition"] = repository.insert_many(
        conn,
        "revenue_composition",
        ["stock_id", "report_date", "dimension", "item_name", "revenue", "ratio"],
        [
            (stock_id, r.report_date, r.dimension.value, r.item_name, r.revenue, r.ratio)
            for r in composition
        ],
    )

    conn.commit()
    conn.close()
    return stats


def _enum_value(value: Any) -> Any:
    return value.value if value is not None else None
