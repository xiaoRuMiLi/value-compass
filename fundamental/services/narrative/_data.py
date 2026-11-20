"""叙述所需的补充数据加载（财务、主营构成、治理、舆情等）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol


def _load_extra_rows(conn: Any, stock_id: int) -> dict[str, Any]:
    profile = repository.fetch_one(
        conn,
        "SELECT business, official_website FROM company_profile WHERE stock_id = ?",
        (stock_id,),
    )
    latest = repository.fetch_one(
        conn,
        "SELECT report_date, revenue, net_profit, roe, gross_margin, net_margin, "
        "revenue_yoy, net_profit_yoy, debt_ratio FROM financials "
        "WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1",
        (stock_id,),
    )
    regions = repository.fetch_all(
        conn,
        "SELECT item_name, ratio FROM revenue_composition rc "
        "WHERE rc.stock_id = ? AND rc.dimension = 'region' AND rc.report_date = "
        "(SELECT MAX(report_date) FROM revenue_composition r2 "
        "WHERE r2.stock_id = rc.stock_id AND r2.dimension = rc.dimension) "
        "ORDER BY rc.ratio DESC LIMIT 4",
        (stock_id,),
    )
    products = repository.fetch_all(
        conn,
        "SELECT item_name, ratio FROM revenue_composition rc "
        "WHERE rc.stock_id = ? AND rc.dimension = 'product' AND rc.report_date = "
        "(SELECT MAX(report_date) FROM revenue_composition r2 "
        "WHERE r2.stock_id = rc.stock_id AND r2.dimension = rc.dimension) "
        "ORDER BY rc.ratio DESC LIMIT 8",
        (stock_id,),
    )
    insiders = repository.fetch_all(
        conn,
        "SELECT trade_date, insider_name, position, direction, change_ratio FROM insider_trades "
        "WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 8",
        (stock_id,),
    )
    pledge = repository.fetch_one(
        conn, "SELECT COUNT(*) AS n FROM pledges WHERE stock_id = ?", (stock_id,)
    )
    integrity = repository.fetch_all(
        conn,
        "SELECT record_date, category, title, description, severity FROM integrity_records "
        "WHERE stock_id = ? ORDER BY record_date DESC LIMIT 10",
        (stock_id,),
    )
    announcements = repository.fetch_all(
        conn,
        "SELECT publish_date, category, title FROM announcements "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 6",
        (stock_id,),
    )
    litigations = repository.fetch_all(
        conn,
        "SELECT publish_date, title, role, amount, status FROM litigations "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 5",
        (stock_id,),
    )
    sentiment = repository.fetch_one(
        conn,
        "SELECT stat_date, post_count, sentiment_score FROM sentiment "
        "WHERE stock_id = ? ORDER BY stat_date DESC LIMIT 1",
        (stock_id,),
    )
    research = repository.fetch_all(
        conn,
        "SELECT publish_date, org_name, rating, title FROM research_reports "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 5",
        (stock_id,),
    )
    capital = repository.fetch_all(
        conn,
        "SELECT trade_date, main_net, main_net_ratio FROM capital_flows "
        "WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 5",
        (stock_id,),
    )
    return {
        "business": profile["business"] if profile else None,
        "official_website": profile["official_website"] if profile else None,
        "latest_financials": dict(latest) if latest else None,
        "revenue_region": [{"item": r["item_name"], "ratio": r["ratio"]} for r in regions],
        "revenue_products": [{"item": r["item_name"], "ratio": r["ratio"]} for r in products],
        "insider_trades": [dict(r) for r in insiders],
        "pledge_count": pledge["n"] if pledge else 0,
        "integrity_records": [dict(r) for r in integrity],
        "announcements": [dict(r) for r in announcements],
        "litigations": [dict(r) for r in litigations],
        "sentiment": dict(sentiment) if sentiment else None,
        "research_reports": [dict(r) for r in research],
        "capital_flows": [dict(r) for r in capital],
    }


def _load_extra(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {}
        return _load_extra_rows(conn, stock_id)
    finally:
        conn.close()


def _load_score_financials(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    """加载历年财务与经营现金流，供五维评分解读做基本面佐证。"""

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {}
        rows = repository.fetch_all(
            conn,
            "SELECT report_date, revenue_yoy, net_profit_yoy, net_profit, net_profit_deducted, "
            "roe, gross_margin, net_margin FROM financials "
            "WHERE stock_id = ? AND report_type = 'FY' ORDER BY report_date ASC",
            (stock_id,),
        )
        ocf_rows = repository.fetch_all(
            conn,
            "SELECT report_date, item_value FROM statement_items "
            "WHERE stock_id = ? AND item_code = 'NETCASH_OPERATE' AND report_type = 'FY'",
            (stock_id,),
        )
        dividend_rows = repository.fetch_all(
            conn,
            "SELECT ex_date, cash_per_share FROM dividends "
            "WHERE stock_id = ? AND ex_date IS NOT NULL ORDER BY ex_date ASC",
            (stock_id,),
        )
        ocf_map = {row["report_date"]: row["item_value"] for row in ocf_rows}
        dividend_history = [
            {"year": (row["ex_date"] or "")[:4], "cash_per_share": row["cash_per_share"]}
            for row in dividend_rows[-6:]
        ]

        annual_growth: list[dict[str, Any]] = []
        annual_quality: list[dict[str, Any]] = []
        for row in rows[-5:]:
            year = (row["report_date"] or "")[:4]
            net_profit = row["net_profit"]
            deducted = row["net_profit_deducted"]
            ocf = ocf_map.get(row["report_date"])
            annual_growth.append(
                {
                    "year": year,
                    "revenue_yoy": row["revenue_yoy"],
                    "net_profit_yoy": row["net_profit_yoy"],
                }
            )
            annual_quality.append(
                {
                    "year": year,
                    "roe": row["roe"],
                    "gross_margin": row["gross_margin"],
                    "net_margin": row["net_margin"],
                    "ocf": ocf,
                    "net_profit": net_profit,
                    "net_profit_deducted": deducted,
                    "ocf_to_profit": (ocf / net_profit) if (ocf is not None and net_profit) else None,
                    "oneoff_ratio": (
                        (net_profit - deducted) / net_profit * 100
                        if (net_profit is not None and deducted is not None and net_profit)
                        else None
                    ),
                }
            )
        return {
            "annual_growth": annual_growth,
            "annual_quality": annual_quality,
            "dividend_history": dividend_history,
        }
    finally:
        conn.close()


_QUARTER_LABELS = {"Q1": "一季报", "H1": "中报", "Q3": "三季报"}


def _pct(value: Any) -> float | None:
    return round(float(value), 2) if isinstance(value, (int, float)) else None


def _year_metric(row: Any, ocf_map: dict[str, Any]) -> dict[str, Any]:
    ocf = ocf_map.get(row["report_date"])
    if ocf is None:
        ocf = row["ocf"]
    net_profit = row["net_profit"]
    deducted = row["net_profit_deducted"]
    return {
        "report_date": row["report_date"],
        "report_type": str(row["report_type"]),
        "revenue_yi": _pct(row["revenue"] / 1e8) if row["revenue"] is not None else None,
        "net_profit_yi": _pct(net_profit / 1e8) if net_profit is not None else None,
        "roe": _pct(row["roe"]),
        "gross_margin": _pct(row["gross_margin"]),
        "net_margin": _pct(row["net_margin"]),
        "debt_ratio": _pct(row["debt_ratio"]),
        "revenue_yoy": _pct(row["revenue_yoy"]),
        "net_profit_yoy": _pct(row["net_profit_yoy"]),
        "ocf_yi": _pct(ocf / 1e8) if ocf is not None else None,
        "ocf_to_profit": round(ocf / net_profit, 2) if (ocf is not None and net_profit) else None,
        "oneoff_ratio": (
            round((net_profit - deducted) / net_profit * 100, 2)
            if (net_profit is not None and deducted is not None and net_profit)
            else None
        ),
        "audit_opinion": row["audit_opinion"],
    }


def _year_summary(year: dict[str, Any]) -> str:
    annual = year.get("annual")
    if annual:
        parts = [
            f"营收同比 {annual['revenue_yoy'] if annual['revenue_yoy'] is not None else '[数据缺失]'}%",
            f"归母净利同比 {annual['net_profit_yoy'] if annual['net_profit_yoy'] is not None else '[数据缺失]'}%",
            f"ROE {annual['roe'] if annual['roe'] is not None else '[数据缺失]'}%",
            f"毛利率 {annual['gross_margin'] if annual['gross_margin'] is not None else '[数据缺失]'}%",
            f"负债率 {annual['debt_ratio'] if annual['debt_ratio'] is not None else '[数据缺失]'}%",
        ]
        if annual.get("ocf_to_profit") is not None:
            parts.append(f"经营现金流/净利润 {annual['ocf_to_profit']}")
        summary = f"{year['year']} 年报：" + "、".join(parts) + "。"
    else:
        summary = f"{year['year']} 年报[数据缺失]。"

    quarters = []
    for item in year.get("quarterly", []):
        label = _QUARTER_LABELS.get(item["report_type"], item["report_type"])
        rev = item["revenue_yoy"] if item["revenue_yoy"] is not None else "[数据缺失]"
        profit = item["net_profit_yoy"] if item["net_profit_yoy"] is not None else "[数据缺失]"
        quarters.append(f"{label} 营收同比 {rev}%/净利同比 {profit}%")
    if quarters:
        summary += "同年" + "；".join(quarters) + "。"
    return summary


def _load_master_financials(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    """按年汇总年报与同年季报/中报，供大师视角叙述使用。

    输出 years[].annual 为年报关键指标，years[].quarterly 为同年季报口径，
    years[].summary 是可直接读给的中文摘要，避免把原始报表行整段塞给模型。
    """

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {"years": []}
        rows = repository.fetch_all(
            conn,
            "SELECT report_date, report_type, revenue, net_profit, net_profit_deducted, "
            "gross_margin, net_margin, roe, debt_ratio, revenue_yoy, net_profit_yoy, "
            "ocf, audit_opinion FROM financials WHERE stock_id = ? ORDER BY report_date ASC",
            (stock_id,),
        )
        ocf_rows = repository.fetch_all(
            conn,
            "SELECT report_date, item_value FROM statement_items "
            "WHERE stock_id = ? AND item_code = 'NETCASH_OPERATE' AND report_type = 'FY'",
            (stock_id,),
        )
        ocf_map = {row["report_date"]: row["item_value"] for row in ocf_rows}

        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            year = (row["report_date"] or "")[:4]
            if not year:
                continue
            bucket = buckets.setdefault(
                year, {"year": year, "annual": None, "quarterly": []}
            )
            metric = _year_metric(row, ocf_map)
            if str(row["report_type"]) == "FY":
                bucket["annual"] = metric
            else:
                bucket["quarterly"].append(metric)

        years = [buckets[key] for key in sorted(buckets)][-5:]
        for year in years:
            year["summary"] = _year_summary(year)
        return {"years": years}
    finally:
        conn.close()
