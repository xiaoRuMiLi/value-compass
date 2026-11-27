"""资产负债表拆解编排：取数 → 纯计算 → 组织前端结构。

web 层只透传本服务返回值；这里不产生投资结论，也不调 LLM（LLM 解读在 narrative 层）。
"""

from __future__ import annotations

from typing import Any

from fundamental.analysis.balance_breakdown import (
    aggregate_fcf_yearly,
    build_balance_breakdown,
    cumulative_to_single_quarter,
)
from fundamental.analysis.kline_periods import align_close_at_period_end, align_kline_to_periods
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol


def _fcf_history(conn: Any, stock_id: int, labels: list[str]) -> dict[str, Any]:
    """从抽取表取历史单季 FCF，与报告期对齐；OCF 取财务主表。"""
    fcf_rows = repository.fetch_all(
        conn,
        "SELECT report_date, value_cny, quote, source_doc FROM extracted_financial_items "
        "WHERE stock_id = ? AND field_code = 'FCF_DISCLOSED' ORDER BY report_date ASC",
        (stock_id,),
    )
    fcf_by_date = {str(r["report_date"])[:10]: r for r in fcf_rows}
    ocf_rows = repository.fetch_all(
        conn,
        "SELECT report_date, ocf FROM financials WHERE stock_id = ? ORDER BY report_date ASC",
        (stock_id,),
    )
    ocf_by_date = {str(r["report_date"])[:10]: r["ocf"] for r in ocf_rows}

    values = []
    ocf_values = []
    quotes = []
    for label in labels:
        fcf = fcf_by_date.get(label)
        values.append(round(fcf["value_cny"] / 1e8, 2) if fcf and fcf["value_cny"] is not None else None)
        ocf = ocf_by_date.get(label)
        ocf_values.append(round(ocf / 1e8, 2) if ocf is not None else None)
        quotes.append(
            {"report_date": label, "quote": fcf["quote"], "source": fcf["source_doc"]}
            if fcf and fcf["quote"] else None
        )
    # 主表 OCF 是年内累计口径，与单季 FCF 并列会误导；差分出单季值供季度图，
    # 同时保留累计值供年度聚合（年末累计即全年 OCF）。
    ocf_single = cumulative_to_single_quarter(labels, ocf_values)
    return {
        "labels": labels,
        "values": values,
        "ocf": ocf_single,
        "ocf_cumulative": ocf_values,
        "quotes": quotes,
        "source": "港股财报原文披露单季 FCF（亿元）；OCF 为财报主表累计数差分出的单季值（亿元）",
        "available": any(v is not None for v in values),
    }


def _associate_evidence(symbol: str) -> list[dict[str, Any]]:
    """联营/合营公司原文证据片段（只检索不判断，供前端展示与 LLM 解读引用）。"""
    try:
        # 延迟导入，避免 services 与 narrative 包之间的模块级循环导入
        from fundamental.services.narrative._asset_quality import asset_quality_digest

        digest = asset_quality_digest(symbol)
    except (OSError, ValueError, TypeError):
        return []
    out: list[dict[str, Any]] = []
    for item in digest or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "category": item.get("category"),
                "source": item.get("source"),
                "keywords": item.get("keywords"),
                "text": text[:600],
            }
        )
    return out[:8]


def build_balance_view(symbol: str) -> dict[str, Any]:
    """返回资产负债表拆解视图；纯取数与编排，不含投资结论。"""
    code, market = parse_symbol(symbol)
    conn = db.init_db()
    try:
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            raise ValueError("股票不存在，请先运行 fetch_data")
        statement_rows = [
            dict(row)
            for row in repository.fetch_all(
                conn,
                "SELECT report_date, report_type, statement_type, item_code, item_name, item_value "
                "FROM statement_items WHERE stock_id = ? ORDER BY report_date ASC",
                (stock_id,),
            )
        ]
        currency = "HKD" if market.value == "HK" else "CNY"
        result = build_balance_breakdown(statement_rows, currency=currency)
        # 历史 FCF 用拆解后的报告期对齐（与三大表同一 x 轴）
        fcf_history = _fcf_history(conn, stock_id, result["labels"])
        fcf_history["yearly"] = aggregate_fcf_yearly(
            fcf_history["labels"], fcf_history["values"], fcf_history["ocf_cumulative"]
        )
        # 现金流图叠加股价折线：季度和年度各自按期末日对齐收盘价（纯计算层完成）
        kline_rows = [
            dict(row)
            for row in repository.fetch_all(
                conn,
                "SELECT trade_date, open, close, low, high FROM kline "
                "WHERE stock_id = ? ORDER BY trade_date ASC",
                (stock_id,),
            )
        ]
        fcf_history["price"] = align_close_at_period_end(kline_rows, fcf_history["labels"])
        fcf_history["yearly"]["price"] = align_close_at_period_end(
            kline_rows, fcf_history["yearly"]["labels"]
        )
        result["fcf_history"] = fcf_history
        # 科目明细弹出层：季度/年度两套报告期 K 线，与科目 values / yearly_values 共用 x 轴
        result["period_kline"] = {
            "quarter": {
                "labels": result["labels"],
                "kline": align_kline_to_periods(kline_rows, result["labels"]),
            },
            "year": {
                "labels": result["year_labels"],
                "kline": align_kline_to_periods(kline_rows, result["year_labels"]),
            },
        }
    finally:
        conn.close()

    result["symbol"] = symbol
    result["associate_evidence"] = _associate_evidence(symbol)
    result["notes"].append(
        "金额单位随币种（港元/人民币）标注；占比以总资产为分母；同比为最新期对上年同期。"
    )
    if not result["fcf_history"]["available"]:
        result["notes"].append(
            "当前标的无可抽取的历史自由现金流：港股需归档财报原文含披露句式，"
            "A 股现金流表分项缺失时无法推导。"
        )
    return result
