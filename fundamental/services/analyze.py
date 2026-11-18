"""分析编排：DB → models → 各分析模块 → 汇总结果。"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Type, TypeVar

from fundamental.analysis import (
    cycle as cycle_mod,
    delisting as delisting_mod,
    financial as financial_mod,
    financial_review as review_mod,
    fraud_score as fraud_mod,
    integrity as integrity_mod,
    moat as moat_mod,
    redflags as redflags_mod,
    returns as returns_mod,
)
from fundamental.analysis.masters.buffett import BuffettModel
from fundamental.analysis.masters.duan_yongping import DuanYongpingModel
from fundamental.analysis.valuation import evaluate_dcf, evaluate_ddm, evaluate_graham, evaluate_relative
from fundamental.models import (
    AuditOpinion,
    BehaviorType,
    CirculatingHolder,
    DividendRecord,
    FinancialReport,
    HolderCategory,
    IntegrityCategory,
    IntegrityRecord,
    KlineBar,
    Market,
    ReportType,
    Severity,
    Snapshot,
    StatementItem,
    StatementType,
    Stock,
    ValuationPoint,
)
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol

_T = TypeVar("_T")


def _to_model(
    cls: Type[_T],
    row: Any,
    enum_fields: dict[str, Type] | None = None,
    bool_fields: set[str] | None = None,
) -> _T:
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in row.keys():
            continue
        value = row[field.name]
        if enum_fields and field.name in enum_fields and value is not None:
            value = enum_fields[field.name](value)
        if bool_fields and field.name in bool_fields:
            value = bool(value)
        values[field.name] = value
    return cls(**values)


def _load_stock(conn: Any, stock_id: int) -> Stock:
    row = repository.fetch_one(conn, "SELECT * FROM stocks WHERE id = ?", (stock_id,))
    return _to_model(Stock, row, {"market": Market}, {"is_st"})


def _load_snapshot(conn: Any, stock_id: int) -> Snapshot | None:
    row = repository.fetch_one(
        conn, "SELECT * FROM snapshots WHERE stock_id = ? ORDER BY fetched_at DESC LIMIT 1", (stock_id,)
    )
    return _to_model(Snapshot, row) if row else None


def _load_financials(conn: Any, stock_id: int) -> list[FinancialReport]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM financials WHERE stock_id = ? ORDER BY report_date ASC", (stock_id,)
    )
    return [
        _to_model(FinancialReport, row, {"report_type": ReportType, "audit_opinion": AuditOpinion})
        for row in rows
    ]


def _load_kline(conn: Any, stock_id: int) -> list[KlineBar]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM kline WHERE stock_id = ? ORDER BY trade_date ASC", (stock_id,)
    )
    return [_to_model(KlineBar, row) for row in rows]


def _load_valuations(conn: Any, stock_id: int) -> list[ValuationPoint]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM valuations WHERE stock_id = ? ORDER BY trade_date ASC", (stock_id,)
    )
    return [_to_model(ValuationPoint, row) for row in rows]


def _load_dividends(conn: Any, stock_id: int) -> list[DividendRecord]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM dividends WHERE stock_id = ? ORDER BY ex_date DESC", (stock_id,)
    )
    return [_to_model(DividendRecord, row) for row in rows]


def _load_holders(conn: Any, stock_id: int) -> list[CirculatingHolder]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM circulating_holders WHERE stock_id = ? ORDER BY report_date DESC", (stock_id,)
    )
    return [
        _to_model(CirculatingHolder, row, {"holder_category": HolderCategory}, {"is_new"})
        for row in rows
    ]


def _load_integrity(conn: Any, stock_id: int) -> list[IntegrityRecord]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM integrity_records WHERE stock_id = ? ORDER BY record_date DESC", (stock_id,)
    )
    return [
        _to_model(
            IntegrityRecord,
            row,
            {"behavior_type": BehaviorType, "category": IntegrityCategory, "severity": Severity},
        )
        for row in rows
    ]


def _load_statement_items(conn: Any, stock_id: int) -> list[StatementItem]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM statement_items WHERE stock_id = ?", (stock_id,)
    )
    return [
        _to_model(StatementItem, row, {"report_type": ReportType, "statement_type": StatementType})
        for row in rows
    ]


def _latest_dividend(dividends: list[DividendRecord]) -> float | None:
    return next((item.cash_per_share for item in dividends if item.cash_per_share), None)


def _compute_fcf(statement_items: list[StatementItem]) -> float | None:
    yearly = [item for item in statement_items if item.report_type == ReportType.FY]
    if not yearly:
        return None
    latest_date = max(item.report_date for item in yearly)
    items = [item for item in yearly if item.report_date == latest_date]
    ocf = next((item.item_value for item in items if item.item_code == "NETCASH_OPERATE"), None)
    capex = next((item.item_value for item in items if item.item_code == "CONSTRUCT_LONG_ASSET"), None)
    if ocf is None:
        return None
    return ocf - capex if capex is not None else ocf


def analyze_stock(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {"symbol": symbol, "error": "股票不存在，请先运行 fetch_data"}

        financials = _load_financials(conn, stock_id)
        kline = _load_kline(conn, stock_id)
        valuations = _load_valuations(conn, stock_id)
        dividends = _load_dividends(conn, stock_id)
        snapshot = _load_snapshot(conn, stock_id)
        integrity = _load_integrity(conn, stock_id)
        statement_items = _load_statement_items(conn, stock_id)

        latest = financials[-1] if financials else None
        price = snapshot.price if snapshot else None
        market_cap = snapshot.market_cap if snapshot else None
        shares = market_cap / price if (market_cap and price) else None
        eps = latest.eps if latest else None
        bps = latest.bps if latest else None
        pe_ttm = valuations[-1].pe_ttm if valuations else None

        dividend_yield = None
        latest_dps = _latest_dividend(dividends)
        if latest_dps and price:
            dividend_yield = latest_dps / price * 100

        score = financial_mod.score_financial(latest, dividend_yield, pe_ttm) if latest else None
        ret = returns_mod.compute_return(
            kline, dividends, (date.today() - timedelta(days=365 * 5)).isoformat(), date.today().isoformat()
        )
        valuations_result = [
            evaluate_relative(valuations, eps, price),
            evaluate_graham(eps, bps, price),
            evaluate_ddm(latest_dps, price),
            evaluate_dcf(_compute_fcf(statement_items), shares, price),
        ]
        refined_integrity = integrity_mod.refine_integrity(integrity)
        flags = redflags_mod.check_red_flags(financials, refined_integrity)
        fraud = fraud_mod.score_fraud(symbol, financials)
        delist = delisting_mod.check_delisting(symbol, price, market_cap, financials)
        masters = [
            BuffettModel().evaluate(symbol, financials, dividends),
            DuanYongpingModel().evaluate(symbol, financials, dividends),
        ]
        review = review_mod.generate_review(
            symbol, financials, flags, fraud, delist, refined_integrity
        )

        return {
            "symbol": symbol,
            "stock": _load_stock(conn, stock_id),
            "snapshot": snapshot,
            "score": score,
            "return": ret,
            "valuation": valuations_result,
            "moat": moat_mod.assess_moat(symbol, financials),
            "cycle": cycle_mod.assess_cycle(financials),
            "masters": masters,
            "review": review,
            "integrity": refined_integrity,
            "red_flags": flags,
            "fraud": fraud,
            "delisting": delist,
        }
    finally:
        conn.close()
