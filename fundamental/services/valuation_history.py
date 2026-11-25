"""报告页 K 线估值叠加的取数编排。

职责边界：从 DB 取日 K、官方估值快照、财报指标，换算港股币种后交给
analysis.valuation_history 做纯计算；本模块不产出估值结论，也不写库。
"""

from __future__ import annotations

from typing import Any

from fundamental.analysis.valuation_history import build_aligned_series
from fundamental.models import Market
from fundamental.services.fx import hkd_to_cny
from fundamental.storage import repository
from fundamental.utils.symbols import parse_symbol


def build_valuation_history(conn: Any, symbol: str) -> dict[str, Any]:
    """返回与日 K 等长、可直接叠加的 PE/PB/ROE 序列。

    港股 F10 的 PE_TTM/PB_TTM 是当期快照按报告期重复，不是历史序列，
    因此港股只走财报推导口径；A 股优先用东财日频估值，2018 年前缺口
    用财报口径补齐，避免折线在缺口处被强行连成直线。
    """
    code, market = parse_symbol(symbol)
    stock_id = repository.get_stock_id(conn, code, market)
    if stock_id is None:
        raise LookupError("股票不存在，请先运行 fetch_data")

    kline_rows = repository.fetch_all(
        conn,
        "SELECT trade_date, close FROM kline WHERE stock_id = ? ORDER BY trade_date ASC",
        (stock_id,),
    )
    dates = [str(row["trade_date"])[:10] for row in kline_rows]
    closes = [float(row["close"]) for row in kline_rows if row["close"] is not None]
    if len(closes) != len(dates):
        closes = [float(row["close"]) if row["close"] is not None else 0.0 for row in kline_rows]

    valuation_rows = repository.fetch_all(
        conn,
        "SELECT trade_date, pe_ttm, pb_mrq FROM valuations WHERE stock_id = ?",
        (stock_id,),
    )
    official: dict[str, dict[str, float]] = {}
    for row in valuation_rows:
        official[str(row["trade_date"])[:10]] = {
            "pe_ttm": row["pe_ttm"],
            "pb_mrq": row["pb_mrq"],
        }

    report_rows = repository.fetch_all(
        conn,
        "SELECT report_date, report_type, eps, eps_ttm, bps, roe FROM financials "
        "WHERE stock_id = ? ORDER BY report_date ASC",
        (stock_id,),
    )
    reports = [dict(row) for row in report_rows]

    fx_info: dict[str, Any] | None = None
    fx_rate = 1.0
    if market == Market.HK:
        fx_info = hkd_to_cny()
        fx_rate = float(fx_info["rate"])

    series = build_aligned_series(
        dates,
        closes,
        official,
        reports,
        fx_rate=fx_rate,
        prefer_official=(market != Market.HK),
    )
    return {"dates": dates, "fx": fx_info, **series}
