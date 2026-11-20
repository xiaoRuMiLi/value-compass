"""叙述所需的补充数据加载（财务、主营构成、治理、舆情等）。"""

from __future__ import annotations

import re
import statistics
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
    quarters = []
    for item in year.get("quarterly", []):
        label = _QUARTER_LABELS.get(item["report_type"], item["report_type"])
        rev = item["revenue_yoy"] if item["revenue_yoy"] is not None else "[数据缺失]"
        profit = item["net_profit_yoy"] if item["net_profit_yoy"] is not None else "[数据缺失]"
        quarters.append(f"{label} 营收同比 {rev}%/净利同比 {profit}%")
    quarter_text = "；".join(quarters)

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
        if quarter_text:
            summary += "同年" + quarter_text + "。"
        return summary
    if quarter_text:
        return f"{year['year']} 年报尚未披露，已披露" + quarter_text + "。"
    return f"{year['year']} 年报[数据缺失]。"


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


_REPORT_LABELS = {"FY": "年报", "Q1": "一季报", "H1": "中报", "Q3": "三季报"}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _signed_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "[数据缺失]"
    return f"{'+' if value >= 0 else ''}{float(value):.2f}%"


def _industry_snapshot(conn: Any, stock: Any) -> dict[str, Any] | None:
    """用股票的细分赛道匹配行业板块行情，匹配不到时返回 None。"""

    names = []
    for key in ("sector", "industry"):
        value = _row_get(stock, key)
        if value and str(value) not in names:
            names.append(str(value))
    if not names:
        return None

    row = None
    for name in names:
        row = repository.fetch_one(
            conn,
            "SELECT * FROM industry WHERE industry_name = ? ORDER BY trade_date DESC LIMIT 1",
            (name,),
        )
        if row:
            break
    if row is None:
        # 行业表用申万二/三级名（如“白酒Ⅱ”），股票表用简称（如“白酒”）。
        for name in names:
            base = re.sub(r"[ⅠⅡⅢⅣ]$", "", name).strip()
            for variant in (base, f"{base}Ⅱ", f"{base}Ⅲ", f"{base}Ⅰ", f"{base}行业"):
                if not variant:
                    continue
                row = repository.fetch_one(
                    conn,
                    "SELECT * FROM industry WHERE industry_name = ? ORDER BY trade_date DESC LIMIT 1",
                    (variant,),
                )
                if row:
                    break
            if row:
                break
    if row is None:
        return None

    pct = _row_get(row, "pct_change")
    capital_flow = _row_get(row, "capital_flow")
    parts = [f"匹配板块“{_row_get(row, 'industry_name')}”（{_row_get(row, 'trade_date')}）"]
    if isinstance(pct, (int, float)):
        parts.append(f"当日涨跌 {_signed_pct(pct)}")
    if isinstance(_row_get(row, "pe"), (int, float)):
        parts.append(f"板块 PE {float(row['pe']):.2f} 倍")
    if isinstance(_row_get(row, "pb"), (int, float)):
        parts.append(f"板块 PB {float(row['pb']):.2f} 倍")
    if isinstance(capital_flow, (int, float)):
        parts.append(f"主力净流入 {capital_flow / 1e8:.2f} 亿元")
    if _row_get(row, "leader_stock"):
        parts.append(f"板块龙头 {row['leader_stock']}")
    return {
        "matched_name": _row_get(row, "industry_name"),
        "trade_date": _row_get(row, "trade_date"),
        "pe": _pct(row["pe"]),
        "pb": _pct(row["pb"]),
        "pct_change": _pct(pct),
        "capital_flow_yi": round(capital_flow / 1e8, 2) if isinstance(capital_flow, (int, float)) else None,
        "leader_stock": _row_get(row, "leader_stock"),
        "summary": "，".join(parts) + "。",
    }


def _peer_comparison(conn: Any, stock: Any) -> dict[str, Any] | None:
    """同行业公司最近年报的 ROE/毛利率/负债率中位数，给护城河判断一个坐标系。"""

    industry = _row_get(stock, "industry")
    if not industry:
        return None
    rows = repository.fetch_all(
        conn,
        "SELECT s.name, f.report_date, f.roe, f.gross_margin, f.debt_ratio "
        "FROM financials f JOIN stocks s ON s.id = f.stock_id "
        "WHERE s.industry = ? AND f.report_type = 'FY' AND f.report_date = "
        "(SELECT MAX(f2.report_date) FROM financials f2 JOIN stocks s2 ON s2.id = f2.stock_id "
        " WHERE s2.industry = ? AND f2.report_type = 'FY') "
        "ORDER BY f.roe DESC",
        (industry, industry),
    )
    if len(rows) < 2:
        return None

    metrics = ("roe", "gross_margin", "debt_ratio")
    medians = {
        key: _pct(statistics.median([row[key] for row in rows if isinstance(row[key], (int, float))]))
        for key in metrics
    }
    medians = {key: value for key, value in medians.items() if value is not None}
    company_name = _row_get(stock, "name")
    mine = next((row for row in rows if row["name"] == company_name), None)
    if mine is None:
        return None

    labels = {"roe": "ROE", "gross_margin": "毛利率", "debt_ratio": "负债率"}
    comparisons = []
    for key in metrics:
        if key not in medians or not isinstance(mine[key], (int, float)):
            continue
        value = _pct(mine[key])
        relation = "高于" if value > medians[key] else ("低于" if value < medians[key] else "持平")
        comparisons.append(f"{labels[key]} {value}%（同业中位数 {medians[key]:.2f}%，{relation}）")
    return {
        "basis": f"同行业“{industry}”最近年报（{_row_get(rows[0], 'report_date')}）",
        "count": len(rows),
        "peers": [
            {
                "name": row["name"],
                "roe": _pct(row["roe"]),
                "gross_margin": _pct(row["gross_margin"]),
                "debt_ratio": _pct(row["debt_ratio"]),
            }
            for row in rows[:6]
        ],
        "company": {key: _pct(mine[key]) for key in metrics},
        "median": medians,
        "summary": (
            f"同行业“{industry}”最近年报（{_row_get(rows[0], 'report_date')}，{len(rows)} 家）："
            + "；".join(comparisons)
            + "。"
            if comparisons
            else f"同行业“{industry}”最近年报可比指标[数据缺失]。"
        ),
    }


def _company_basics(conn: Any, stock_id: int, stock: Any) -> dict[str, Any]:
    profile = repository.fetch_one(
        conn, "SELECT business, official_website FROM company_profile WHERE stock_id = ?", (stock_id,)
    )
    mix_rows = repository.fetch_all(
        conn,
        "SELECT report_date, dimension, item_name, ratio FROM revenue_composition "
        "WHERE stock_id = ? AND report_date = "
        "(SELECT MAX(report_date) FROM revenue_composition WHERE stock_id = ?) "
        "ORDER BY dimension, ratio DESC LIMIT 12",
        (stock_id, stock_id),
    )
    mix: dict[str, dict[str, Any]] = {}
    for row in mix_rows:
        dimension = str(_row_get(row, "dimension") or "")
        group = mix.setdefault(
            dimension,
            {"dimension": dimension, "report_date": _row_get(row, "report_date"), "items": []},
        )
        if len(group["items"]) < 5:
            group["items"].append(
                {"item": _row_get(row, "item_name"), "ratio_pct": _pct(_row_get(row, "ratio"))}
            )
    mix_list = list(mix.values())
    business = _row_get(profile, "business")
    parts = [
        f"{_row_get(stock, 'name')}：行业“{_row_get(stock, 'industry')}”、赛道“{_row_get(stock, 'sector')}”"
    ]
    parts.append(f"主营业务：{business}" if business else "主营业务详情[数据缺失]")
    labels = {"product": "产品构成", "region": "地区构成"}
    for group in mix_list:
        items = "、".join(
            f"{item['item']} {item['ratio_pct']}%"
            for item in group["items"]
            if item["item"]
        )
        if items:
            parts.append(f"{labels.get(group['dimension'], group['dimension'])}（{group['report_date']}）：{items}")
    return {
        "name": _row_get(stock, "name"),
        "industry": _row_get(stock, "industry"),
        "sector": _row_get(stock, "sector"),
        "business": business,
        "list_date": _row_get(stock, "list_date"),
        "is_st": bool(_row_get(stock, "is_st")),
        "revenue_mix": mix_list,
        "profile_summary": "；".join(parts) + "。",
    }


def _latest_report(conn: Any, stock_id: int) -> dict[str, Any] | None:
    row = repository.fetch_one(
        conn,
        "SELECT report_date, report_type, revenue, net_profit, net_profit_deducted, roe, roic, "
        "gross_margin, net_margin, debt_ratio, current_ratio, ocf, fcf, eps, bps, "
        "revenue_yoy, net_profit_yoy, audit_opinion FROM financials "
        "WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1",
        (stock_id,),
    )
    if row is None:
        return None
    report_date = str(_row_get(row, "report_date") or "")
    ocf = _row_get(row, "ocf")
    if ocf is None:
        ocf_row = repository.fetch_one(
            conn,
            "SELECT item_value FROM statement_items "
            "WHERE stock_id = ? AND report_date = ? AND item_code = 'NETCASH_OPERATE' "
            "ORDER BY report_type DESC LIMIT 1",
            (stock_id, report_date),
        )
        ocf = _row_get(ocf_row, "item_value")
    net_profit = _row_get(row, "net_profit")
    deducted = _row_get(row, "net_profit_deducted")
    report_type = str(_row_get(row, "report_type") or "")
    parts = [
        f"{report_date} {_REPORT_LABELS.get(report_type, report_type)}："
        f"营业收入 {(_row_get(row, 'revenue') or 0) / 1e8:.2f} 亿元、同比 {_signed_pct(_row_get(row, 'revenue_yoy'))}",
        f"归母净利润 {((net_profit or 0) / 1e8):.2f} 亿元、同比 {_signed_pct(_row_get(row, 'net_profit_yoy'))}",
    ]
    for label, key in (("毛利率", "gross_margin"), ("净利率", "net_margin"), ("ROE", "roe")):
        value = _pct(_row_get(row, key))
        parts.append(f"{label} {value if value is not None else '[数据缺失]'}%")
    debt, current = _pct(_row_get(row, "debt_ratio")), _pct(_row_get(row, "current_ratio"))
    parts.append(
        f"资产负债率 {debt if debt is not None else '[数据缺失]'}%、"
        f"流动比率 {current if current is not None else '[数据缺失]'}"
    )
    if isinstance(ocf, (int, float)):
        parts.append(f"经营现金流 {ocf / 1e8:.2f} 亿元")
        if isinstance(net_profit, (int, float)) and net_profit:
            parts.append(f"现金流/净利 {ocf / net_profit * 100:.1f}%")
    else:
        parts.append("经营现金流[数据缺失]")
    if isinstance(net_profit, (int, float)) and isinstance(deducted, (int, float)) and net_profit:
        parts.append(f"一次性收益占比 {(net_profit - deducted) / net_profit * 100:.2f}%")
    if _row_get(row, "audit_opinion"):
        parts.append(f"审计意见 {row['audit_opinion']}")
    return {
        "report_date": report_date,
        "report_type": report_type,
        "revenue_yi": _pct((_row_get(row, "revenue") or 0) / 1e8),
        "net_profit_yi": _pct((net_profit or 0) / 1e8),
        "revenue_yoy": _pct(_row_get(row, "revenue_yoy")),
        "net_profit_yoy": _pct(_row_get(row, "net_profit_yoy")),
        "roe": _pct(_row_get(row, "roe")),
        "roic": _pct(_row_get(row, "roic")),
        "gross_margin": _pct(_row_get(row, "gross_margin")),
        "net_margin": _pct(_row_get(row, "net_margin")),
        "debt_ratio": _pct(_row_get(row, "debt_ratio")),
        "current_ratio": _pct(_row_get(row, "current_ratio")),
        "ocf_yi": _pct(ocf / 1e8) if isinstance(ocf, (int, float)) else None,
        "ocf_to_profit": (
            round(ocf / net_profit, 3)
            if isinstance(ocf, (int, float)) and isinstance(net_profit, (int, float)) and net_profit
            else None
        ),
        "oneoff_ratio": (
            round((net_profit - deducted) / net_profit * 100, 2)
            if isinstance(net_profit, (int, float)) and isinstance(deducted, (int, float)) and net_profit
            else None
        ),
        "audit_opinion": _row_get(row, "audit_opinion"),
        "summary": "；".join(parts) + "。",
    }


def _market_valuation(conn: Any, stock_id: int) -> dict[str, Any] | None:
    row = repository.fetch_one(
        conn,
        "SELECT trade_date, close_price, pe_ttm, pe_lar, pb_mrq, ps_ttm, peg, pcf_ocf_ttm "
        "FROM valuations WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1",
        (stock_id,),
    )
    if row is None:
        return None
    parts = [f"{_row_get(row, 'trade_date')} 市场估值"]
    for label, key in (
        ("PE(TTM)", "pe_ttm"),
        ("PB", "pb_mrq"),
        ("PS", "ps_ttm"),
        ("PEG", "peg"),
        ("PCF(经营现金流)", "pcf_ocf_ttm"),
    ):
        value = _pct(_row_get(row, key))
        parts.append(f"{label} {value if value is not None else '[数据缺失]'} 倍")
    return {
        "trade_date": _row_get(row, "trade_date"),
        "close_price": _pct(_row_get(row, "close_price")),
        "pe_ttm": _pct(_row_get(row, "pe_ttm")),
        "pe_lar": _pct(_row_get(row, "pe_lar")),
        "pb_mrq": _pct(_row_get(row, "pb_mrq")),
        "ps_ttm": _pct(_row_get(row, "ps_ttm")),
        "peg": _pct(_row_get(row, "peg")),
        "pcf_ocf_ttm": _pct(_row_get(row, "pcf_ocf_ttm")),
        "summary": "，".join(parts) + "。",
    }


def _load_master_context(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    """大师叙述所需的行业、企业基本面、最近一期财报与市场估值。"""

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {}
        stock = repository.fetch_one(conn, "SELECT * FROM stocks WHERE id = ?", (stock_id,))
        if stock is None:
            return {}
        return {
            "company_basics": _company_basics(conn, stock_id, stock),
            "industry": _industry_snapshot(conn, stock),
            "peer_comparison": _peer_comparison(conn, stock),
            "latest_report": _latest_report(conn, stock_id),
            "market_valuation": _market_valuation(conn, stock_id),
        }
    finally:
        conn.close()
