"""财务全景服务：从库里取原始报表与行情，交给纯计算模块生成序列。

web 层只透传本服务返回值；这里不产生投资结论，也不调用 LLM。
"""

from __future__ import annotations

from typing import Any

from fundamental.analysis.financial_metrics import build_dashboard
from fundamental.analysis.kline_periods import align_kline_to_periods
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol


def _hk_ttm_fcf(symbol: str, labels: list[str]) -> dict[str, float]:
    """港股自由现金流只有财报原文 TTM 口径，只能落在最后一期。"""

    if not symbol.endswith(".HK") or not labels:
        return {}
    try:
        from fundamental.services.hk_financial_supplement import extract_fcf_ttm

        data = extract_fcf_ttm(symbol)
    except (ImportError, OSError, ValueError, TypeError):
        return {}
    if not data.get("available") or data.get("fcf_ttm_yi") is None:
        return {}
    return {labels[-1]: float(data["fcf_ttm_yi"])}


def build_financial_dashboard(symbol: str, frequency: str = "quarter") -> dict[str, Any]:
    """返回财务全景：主要指标、三大表、分组比率与对齐后的报告期 K 线。"""

    if frequency not in ("quarter", "annual"):
        raise ValueError("frequency 只支持 quarter 或 annual")

    code, market = parse_symbol(symbol)
    conn = db.init_db()
    try:
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            raise ValueError("股票不存在，请先运行 fetch_data")
        reports = [
            dict(row)
            for row in repository.fetch_all(
                conn,
                "SELECT report_date, report_type, revenue, net_profit, eps, bps, "
                "gross_margin, net_margin, roe, debt_ratio, current_ratio "
                "FROM financials WHERE stock_id = ? ORDER BY report_date ASC",
                (stock_id,),
            )
        ]
        statement_rows = [
            dict(row)
            for row in repository.fetch_all(
                conn,
                "SELECT report_date, report_type, statement_type, item_code, item_name, item_value "
                "FROM statement_items WHERE stock_id = ? ORDER BY report_date ASC",
                (stock_id,),
            )
        ]
        kline_rows = [
            dict(row)
            for row in repository.fetch_all(
                conn,
                "SELECT trade_date, open, close, low, high FROM kline "
                "WHERE stock_id = ? ORDER BY trade_date ASC",
                (stock_id,),
            )
        ]
    finally:
        conn.close()

    result = build_dashboard(reports, statement_rows, frequency=frequency)
    fcf_by_period = _hk_ttm_fcf(symbol, result["labels"])
    if fcf_by_period:
        for item in result["ratios"]["quality"]["items"]:
            if item["key"] == "fcf":
                item["values"] = [fcf_by_period.get(label) for label in result["labels"]]
    result["symbol"] = symbol
    # 每根 K 线按其财报周期（季度/年度）聚合区间内全部交易日，与柱状图口径一致
    result["kline"] = align_kline_to_periods(kline_rows, result["labels"])
    result["notes"].insert(
        0,
        "K 线按财报周期聚合：每根对应报告期整段行情（如 2026Q3 = 7/1~9/30），"
        "开=期初首日、收=期末末日、高低取区间极值，与下方柱状图单季口径一致。",
    )
    if not statement_rows:
        result["notes"].append(
            "当前标的的三大报表科目尚未入库，三大表与推导比率暂无数据；主要指标仍可查看。"
        )
    if market == "HK":
        result["notes"].append(
            "港股现金流量表仅披露经营/投资/筹资三项净额，无销售收现、购建固定资产等分项；"
            "自由现金流净额取财报原文 TTM，只落在最新一期。"
        )
    else:
        result["notes"].append(
            "A 股数据源缺少「购建固定资产等支付的现金」科目，自由现金流净额暂无法推导。"
        )
    return result
