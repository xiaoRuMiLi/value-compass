"""报告期 K 线的纯计算：把日 K 线按财报周期聚合成一根区间 K 线。

本模块只消费普通 dict，不读文件/DB、不调 LLM。财务页上方 K 线与下方柱状图共用
报告期 x 轴，柱状图是单季业绩，因此 K 线也必须取对应季度的整段行情，而不是
期末那一天的日 K：
    2026Q1 -> 01-01~03-31   2026Q2 -> 04-01~06-30
    2026Q3 -> 07-01~09-30   2026Q4 -> 10-01~12-31
    2026年报 -> 01-01~12-31

区间 K 线口径：开=区间首日开盘，收=区间末日收盘，低=区间最低，高=区间最高。
"""

from __future__ import annotations

import bisect
from typing import Any

# 报告期后缀 -> (月日起, 月日止)。年报模式取整年。
# 同时兼容两类标签：dashboard 的 "2026Q3"/"2025年报"，资产负债表拆解的 ISO 报告期末
# "2026-09-30"（明细科目按报告期对齐，K 线需落在同一季度区间）。
_PERIOD_BOUNDS: dict[str, tuple[str, str]] = {
    "Q1": ("-01-01", "-03-31"),
    "Q2": ("-04-01", "-06-30"),
    "Q3": ("-07-01", "-09-30"),
    "Q4": ("-10-01", "-12-31"),
    "年报": ("-01-01", "-12-31"),
    "-03-31": ("-01-01", "-03-31"),
    "-06-30": ("-04-01", "-06-30"),
    "-09-30": ("-07-01", "-09-30"),
    "-12-31": ("-10-01", "-12-31"),
}


def period_range(label: str) -> tuple[str, str]:
    """报告期标签 -> (起始日, 结束日)，均为 ISO 日期字符串。

    支持 "2026Q3"/"2025年报" 与 ISO 报告期末 "2026-09-30"；未识别的后缀按整年处理，
    保证任何标签都能落到一个确定的区间。
    """
    year = label[:4]
    start_md, end_md = _PERIOD_BOUNDS.get(label[4:], ("-01-01", "-12-31"))
    return year + start_md, year + end_md


def align_kline_to_periods(
    rows: list[dict[str, Any]], labels: list[str]
) -> list[dict[str, Any] | None]:
    """把按交易日升序排列的日 K 线，按报告期标签聚合成一根区间 K 线。

    返回与 labels 等长的列表：期内有交易日则返回聚合 OHLC，否则为 None（前端用
    '-' 占位）。缺失值不参与极值计算，避免 None 与数值比较抛错。
    """
    dates = [str(row["trade_date"])[:10] for row in rows]
    aligned: list[dict[str, Any] | None] = []
    for label in labels:
        start, end = period_range(label)
        lo = bisect.bisect_left(dates, start)
        hi = bisect.bisect_right(dates, end)
        window = rows[lo:hi]
        if not window:
            aligned.append(None)
            continue
        lows = [r["low"] for r in window if r.get("low") is not None]
        highs = [r["high"] for r in window if r.get("high") is not None]
        opens = [r["open"] for r in window if r.get("open") is not None]
        closes = [r["close"] for r in window if r.get("close") is not None]
        aligned.append(
            {
                # 期末最后交易日，保留原字段名兼容既有 tooltip
                "trade_date": dates[hi - 1],
                "period_start": start,
                "period_end": end,
                "trading_days": len(window),
                "open": opens[0] if opens else None,
                "close": closes[-1] if closes else None,
                "low": min(lows) if lows else None,
                "high": max(highs) if highs else None,
            }
        )
    return aligned


def align_close_at_period_end(
    rows: list[dict[str, Any]], labels: list[str]
) -> list[float | None]:
    """每个报告期取期末日（含）之前最近交易日的收盘价，供现金流图叠加股价折线。

    与 align_kline_to_periods 的区别：这里只要一个收盘价点（期末股价），不需要整段
    OHLC，因此直接按期末日向前找最近交易日。labels 支持两类：
      ISO 报告期末（2026-09-30）-> 期末日即该日期
          年度标签（2025年）    -> 期末日为当年 12-31
    期末日之后无行情（如未上市/未来期）返回 None，不拿远期的价格硬凑。
    """
    dates = [str(row["trade_date"])[:10] for row in rows]
    aligned: list[float | None] = []
    for label in labels:
        raw = str(label)
        end = raw[:10]
        # 年度标签：2025 / 2025年 / 2025年报 均按当年 12-31 取价
        if raw[:4].isdigit() and ("年" in raw[:6] or len(raw) == 4):
            end = raw[:4] + "-12-31"
        elif len(end) < 10:
            aligned.append(None)
            continue
        idx = bisect.bisect_right(dates, end) - 1
        if idx < 0:
            aligned.append(None)
            continue
        close = rows[idx].get("close")
        aligned.append(round(float(close), 2) if close is not None else None)
    return aligned
