"""体检页「当期全量财务数据」聚合服务。

只从库内取数并按来源标注，不调用 LLM、不做估值：
- financials：六项核心指标（含同比）与可选报告期列表
- statement_items：三大表科目（东财标准口径）
- extracted_financial_items：财报原文抽取的非标准字段（带引文与文档入口）

前端只拿到文档 ID 与引文，不下发服务端路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.services.financial_extract import FIELD_BY_CODE
from fundamental.storage import db, repository

CORE_METRICS = (
    ("revenue", "营业收入", "元", "revenue_yoy"),
    ("net_profit", "归母净利润", "元", "net_profit_yoy"),
    ("roe", "ROE", "%", None),
    ("gross_margin", "毛利率", "%", None),
    ("debt_ratio", "资产负债率", "%", None),
    ("current_ratio", "流动比率", "倍", None),
)

# 「关键科目」优先展示的字段；其余进「全部科目」折叠区。
KEY_ITEMS = {
    "income": (
        "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "OPERATE_COST", "GROSS_PROFIT",
        "SALE_EXPENSE", "MANAGE_EXPENSE", "RESEARCH_EXPENSE", "FINANCE_EXPENSE",
        "OPERATE_PROFIT", "TOTAL_PROFIT", "INCOME_TAX", "NETPROFIT",
        "PARENT_NETPROFIT", "DEDUCT_PARENT_NETPROFIT", "MINORITY_INTEREST", "BASIC_EPS",
    ),
    "balance": (
        "MONETARYFUNDS", "TRADE_FINASSET", "ACCOUNTS_RECE",
        "PREPAYMENT", "INVENTORY", "CONTRACT_LIAB", "TOTAL_CURRENT_ASSETS",
        "FIXED_ASSET", "CIP", "INTANGIBLE_ASSET", "GOODWILL", "TOTAL_ASSETS",
        "SHORT_LOAN", "ACCOUNTS_PAYABLE", "NONCURRENT_LIAB_1YEAR", "LONG_LOAN",
        "BOND_PAYABLE", "LEASE_LIAB", "TOTAL_LIABILITIES", "TOTAL_PARENT_EQUITY",
        "MINORITY_EQUITY", "TOTAL_EQUITY",
    ),
    "cashflow": (
        "SALES_SERVICES", "TOTAL_OPERATE_INFLOW", "BUY_SERVICES", "PAY_STAFF_CASH",
        "PAY_ALL_TAX", "OPERATE_PAYABLE_ADD", "OPERATE_RECE_REDUCE", "INVENTORY_REDUCE",
        "NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET", "NETCASH_INVEST", "NETCASH_FINANCE",
        "RECEIVE_INTEREST_COMMISSION", "PAY_INTEREST_COMMISSION", "FA_IR_DEPR",
        "IA_AMORTIZE", "BEGIN_CCE", "END_CCE",
    ),
}

STATEMENT_LABELS = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}
UNIT_OVERRIDES = {"BASIC_EPS": "元/股", "DILUTED_EPS": "元/股"}


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _comparison_dates(report_date: str) -> tuple[str, str]:
    """返回（上年同日、上年 12-31）；用于同期与全年参考列。"""
    year, rest = report_date.split("-", 1)
    prior_year = str(int(year) - 1)
    return f"{prior_year}-{rest}", f"{prior_year}-12-31"


def _statement_values(
    conn: Any, stock_id: int, dates: list[str]
) -> dict[tuple[str, str, str], float | None]:
    """一次查出多个报告期的三大表科目，按 (日期, 表, 科目) 索引，避免逐项查库。"""
    dates = [d for d in dict.fromkeys(dates) if d]
    if not dates:
        return {}
    marks = ",".join("?" for _ in dates)
    rows = repository.fetch_all(
        conn,
        "SELECT report_date, statement_type, item_code, item_value FROM statement_items "
        f"WHERE stock_id = ? AND report_date IN ({marks})",
        (stock_id, *dates),
    )
    return {
        (row["report_date"], row["statement_type"], row["item_code"]): row["item_value"]
        for row in rows
    }


def _delta(value: float | None, base: float | None) -> float | None:
    """增减额：任一侧缺失则留空，绝不以 0 伪造。"""
    if value is None or base is None:
        return None
    return value - base


def _doc_id_by_path(rows: list[dict[str, Any]], source_path: str | None) -> int | None:
    if not source_path:
        return None
    txt = Path(str(source_path).replace("\\", "/"))
    pdf = txt.with_suffix(".pdf").as_posix().lower()
    stem = txt.stem
    for doc in rows:
        local = str(doc.get("local_path") or "").replace("\\", "/").lower()
        if local and (local == pdf or local.endswith("/" + txt.name.lower())):
            return int(doc["id"])
        if stem and stem in local:
            return int(doc["id"])
    return None


def _extracted_rows(conn: Any, stock_id: int, report_date: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _rows(repository.fetch_all(
        conn,
        "SELECT field_code, report_date, report_type, value_cny, currency, unit, quote, source_doc, source_path "
        "FROM extracted_financial_items WHERE stock_id = ? AND report_date = ? "
        "ORDER BY field_code",
        (stock_id, report_date),
    ))
    out: list[dict[str, Any]] = []
    for row in rows:
        field = FIELD_BY_CODE.get(row["field_code"])
        out.append({
            "field_code": row["field_code"],
            "label": field.label if field else row["field_code"],
            "value_cny": row["value_cny"],
            "currency": row["currency"],
            "unit": row["unit"],
            "quote": row["quote"],
            "source_doc": row["source_doc"],
            "doc_id": _doc_id_by_path(docs, row["source_path"]),
            "source": "财报原文抽取",
        })
    return out


def build_report_facts(conn: Any, stock_id: int, report_date: str | None = None) -> dict[str, Any]:
    """汇总当期财务数据；report_date 不存在时回退最新一期。"""
    all_financials = _rows(repository.fetch_all(
        conn,
        "SELECT * FROM financials WHERE stock_id = ? ORDER BY report_date DESC",
        (stock_id,),
    ))
    if not all_financials:
        return {"report_date": None, "periods": [], "core": [], "statements": [], "extracted": [], "note": "库内暂无财务数据"}

    selected = next((r for r in all_financials if r["report_date"] == report_date), all_financials[0])
    date = selected["report_date"]
    report_type = selected["report_type"]

    prior_date, full_date = _comparison_dates(date)
    fin_by_date = {row["report_date"]: row for row in all_financials}
    prior_fin = fin_by_date.get(prior_date)
    full_fin = fin_by_date.get(full_date)

    core: list[dict[str, Any]] = []
    for key, label, unit, yoy_key in CORE_METRICS:
        value = selected.get(key)
        prior = prior_fin.get(key) if prior_fin else None
        full = full_fin.get(key) if full_fin else None
        yoy = selected.get(yoy_key) if yoy_key else None
        if yoy is None and value is not None and prior not in (None, 0):
            yoy = (value - prior) / abs(prior) * 100
        core.append({
            "key": key,
            "label": label,
            "value": value,
            "unit": unit,
            "yoy": yoy,
            "prior_value": prior,
            "prior_full_year": full,
            "change": _delta(value, prior),
            "source": "东财标准口径",
        })

    items = _rows(repository.fetch_all(
        conn,
        "SELECT statement_type, item_code, item_name, item_value, item_yoy FROM statement_items "
        "WHERE stock_id = ? AND report_date = ? AND report_type = ? ORDER BY statement_type, item_code, id",
        (stock_id, date, report_type),
    ))
    compare_values = _statement_values(conn, stock_id, [prior_date, full_date])
    statements: list[dict[str, Any]] = []
    for stmt_type in ("income", "balance", "cashflow"):
        group = []
        for row in items:
            if row["statement_type"] != stmt_type:
                continue
            code = row["item_code"]
            prior = compare_values.get((prior_date, stmt_type, code))
            full = compare_values.get((full_date, stmt_type, code))
            group.append({
                "code": code,
                "name": row["item_name"],
                "value": row["item_value"],
                "yoy": row["item_yoy"],
                "prior_value": prior,
                "prior_full_year": full,
                "change": _delta(row["item_value"], prior),
                "unit": UNIT_OVERRIDES.get(code, "元"),
                "source": "东财标准科目",
                "key": code in KEY_ITEMS[stmt_type],
            })
        statements.append({"type": stmt_type, "label": STATEMENT_LABELS[stmt_type], "items": group})

    docs = _rows(repository.fetch_all(
        conn,
        "SELECT id, doc_type, period, title, local_path FROM documents WHERE stock_id = ?",
        (stock_id,),
    ))
    extracted = _extracted_rows(conn, stock_id, date, docs)

    # 最近一期原文抽取（可能与当期不同），单独标注期间，避免误当本期数据。
    latest_extracted_date = repository.fetch_one(
        conn,
        "SELECT report_date FROM extracted_financial_items WHERE stock_id = ? "
        "ORDER BY report_date DESC LIMIT 1",
        (stock_id,),
    )
    latest_extracted: list[dict[str, Any]] = []
    if latest_extracted_date and latest_extracted_date["report_date"] != date:
        latest_extracted = _extracted_rows(conn, stock_id, latest_extracted_date["report_date"], docs)

    note = "季报/半年报未经审计，口径与年报不同；本期现金流科目可能为累计数。" if report_type != "FY" else "年报数据已经审计，仍以财报原文为准。"

    return {
        "report_date": date,
        "report_type": report_type,
        # 报告期切换只列最近 24 期；更早的数据仍在库里，避免下拉框被上百期淹没。
        "periods": [
            {"report_date": r["report_date"], "report_type": r["report_type"]}
            for r in all_financials[:24]
        ],
        "core": core,
        "statements": statements,
        "comparison": {
            "prior_period": prior_date,
            "prior_full_year": full_date,
            "note": "利润表/现金流为年初至今累计数，上年同期同口径可比；资产负债表为时点数。上年全年为完整年度，仅作参考、不计算同比。增减额 = 本期 − 上年同期。",
        },
        "extracted": extracted,
        "latest_extracted": {"report_date": latest_extracted_date["report_date"] if latest_extracted_date else None, "items": latest_extracted},
        "note": note,
    }


def build_for_symbol(conn: Any, symbol: str, report_date: str | None = None) -> dict[str, Any]:
    from fundamental.utils.symbols import parse_symbol

    code, market = parse_symbol(symbol)
    stock_id = repository.get_stock_id(conn, code, market)
    if stock_id is None:
        raise LookupError("股票不存在，请先运行 fetch_data")
    return build_report_facts(conn, stock_id, report_date)
