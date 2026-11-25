"""K 线估值叠加的纯计算。

只消费已经取好的 dict 列表，不读文件/数据库，也不调用 LLM：
- 累计 EPS 按报告期还原 TTM EPS；
- 报告期指标按「报告期末或之前最近一期」对齐到交易日；
- PE/PB 优先使用官方日频估值，缺口用财报口径推导补齐。

港股 F10 的 PE_TTM/PB_TTM 是「当前快照」按历史报告期重复，不能当历史序列用，
因此港股一律走财报推导口径，并在返回值里标注来源。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

_PERIOD_MONTHS = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}


def ttm_eps_by_report(reports: list[dict[str, Any]]) -> dict[str, float]:
    """累计 EPS → TTM EPS，按报告期日期索引。

    优先用「本期累计 + 上年全年 - 上年同期累计」还原滚动 12 个月；
    缺上年同期时退化为按报告期年化，只用于图表展示，不参与估值结论。
    """
    cumulative = {
        (str(r["report_date"])[:4], str(r["report_type"])): float(r["eps"])
        for r in reports
        if r.get("eps") is not None
    }
    result: dict[str, float] = {}
    for report in reports:
        if report.get("eps") is None:
            continue
        date = str(report["report_date"])[:10]
        year = int(date[:4])
        report_type = str(report["report_type"])
        eps = float(report["eps"])
        if report_type == "FY":
            result[date] = eps
            continue
        prev_fy = cumulative.get((str(year - 1), "FY"))
        prev_same = cumulative.get((str(year - 1), report_type))
        if prev_fy is not None and prev_same is not None:
            result[date] = eps + prev_fy - prev_same
        else:
            months = _PERIOD_MONTHS.get(report_type, 12)
            result[date] = eps * 12 / months
    return result


def as_of(
    dates: list[str], points: dict[str, float]
) -> tuple[list[float | None], list[str | None]]:
    """每个交易日取「报告期末 <= 该日」的最近一期值及其报告期。"""
    keys = sorted(points)
    values: list[float | None] = []
    periods: list[str | None] = []
    for date in dates:
        idx = bisect_right(keys, date) - 1
        if idx < 0:
            values.append(None)
            periods.append(None)
        else:
            values.append(points[keys[idx]])
            periods.append(keys[idx])
    return values, periods


def detect_splits(dates: list[str], closes: list[float]) -> list[tuple[str, float]]:
    """从日 K 的单日跳变识别拆股（如腾讯 2014-05-15 的 1:5）。

    要求跌幅 ≥2.5 倍、后 10 日中位价仍在新价附近，且比例接近整数，
    避免把普通暴跌误判为拆股。返回 (拆股日, 拆股比例)。
    """
    splits: list[tuple[str, float]] = []
    for index in range(1, len(dates)):
        previous = closes[index - 1] if index - 1 < len(closes) else None
        current = closes[index] if index < len(closes) else None
        if not previous or not current or previous <= 0 or current <= 0:
            continue
        ratio = previous / current
        if ratio < 2.5:
            continue
        window = closes[index : index + 10]
        if len(window) < 5:
            continue
        median = sorted(window)[len(window) // 2]
        if abs(median / current - 1) > 0.35:
            continue
        rounded = round(ratio)
        if rounded < 2 or abs(ratio - rounded) / rounded > 0.15:
            continue
        splits.append((dates[index], float(rounded)))
    return splits


def build_aligned_series(
    dates: list[str],
    closes: list[float],
    official: dict[str, dict[str, float]],
    reports: list[dict[str, Any]],
    fx_rate: float = 1.0,
    prefer_official: bool = True,
) -> dict[str, list[Any]]:
    """生成与交易日等长的 PE/PB/ROE 序列及口径、报告期标注。

    official: trade_date -> {"pe_ttm": float|None, "pb_mrq": float|None}
    reports:  含 report_date / report_type / eps / bps / roe 的报告期列表
    fx_rate:  收盘价币种 → 财报币种 的换算系数（港股用 HKD→CNY）
    """
    eps_map = ttm_eps_by_report(reports)
    # 港股 F10 直接披露 EPS_TTM，且拆股前后口径各自一致；优先用它，
    # 避免用累计 EPS 做 TTM 时把拆股前后的数混减（2014Q1 会出现近零分母）。
    reported_eps_periods: set[str] = set()
    for report in reports:
        if report.get("eps_ttm") is not None and float(report["eps_ttm"]) > 0:
            date = str(report["report_date"])[:10]
            eps_map[date] = float(report["eps_ttm"])
            reported_eps_periods.add(date)
    bps_map = {
        str(r["report_date"])[:10]: float(r["bps"])
        for r in reports
        if r.get("bps") is not None and float(r["bps"]) > 0
    }
    roe_map = {
        str(r["report_date"])[:10]: float(r["roe"])
        for r in reports
        if str(r["report_type"]) == "FY" and r.get("roe") is not None
    }

    eps_values, eps_periods = as_of(dates, eps_map)
    bps_values, bps_periods = as_of(dates, bps_map)
    roe_values, roe_periods = as_of(dates, roe_map)

    # 拆股日之后、下一份新口径财报之前，旧 EPS 仍是「每股旧股」口径，
    # 而 K 线价格已是「每股新股」；按拆股比例折算 EPS，避免 PE 假性腰斩。
    splits = detect_splits(dates, closes)
    split_adjusted = [False] * len(dates)
    for index, period in enumerate(eps_periods):
        if period is None or eps_values[index] is None:
            continue
        factor = 1.0
        for split_date, ratio in splits:
            if period < split_date <= dates[index]:
                factor *= ratio
        if factor != 1.0:
            eps_values[index] /= factor
            split_adjusted[index] = True

    # 港股 F10 的 BPS 往往是拆股后重述口径，而拆股前的 K 线价格仍是旧股口径；
    # 拆股前把 BPS 乘回比例，PB 才不会在拆股日假性腰斩。
    bps_split_adjusted = [False] * len(dates)
    for index, period in enumerate(bps_periods):
        if period is None or bps_values[index] is None:
            continue
        factor = 1.0
        for split_date, ratio in splits:
            if period < split_date and dates[index] < split_date:
                factor *= ratio
        if factor != 1.0:
            bps_values[index] *= factor
            bps_split_adjusted[index] = True

    pe: list[float | None] = []
    pb: list[float | None] = []
    pe_source: list[str | None] = []
    pb_source: list[str | None] = []
    pe_period: list[str | None] = []
    pb_period: list[str | None] = []

    for index, date in enumerate(dates):
        close = closes[index] if index < len(closes) else None
        row = official.get(date) if prefer_official else None

        official_pe = row.get("pe_ttm") if row else None
        official_pb = row.get("pb_mrq") if row else None
        official_pe = float(official_pe) if official_pe is not None and float(official_pe) > 0 else None
        official_pb = float(official_pb) if official_pb is not None and float(official_pb) > 0 else None

        derived_pe = None
        derived_pb = None
        if close is not None and close > 0:
            eps = eps_values[index]
            bps = bps_values[index]
            if eps is not None and eps > 0:
                derived_pe = close * fx_rate / eps
            if bps is not None and bps > 0:
                derived_pb = close * fx_rate / bps

        pe.append(official_pe if official_pe is not None else derived_pe)
        pb.append(official_pb if official_pb is not None else derived_pb)
        if official_pe is not None:
            pe_label = "日频"
        elif derived_pe is None:
            pe_label = None
        elif eps_periods[index] in reported_eps_periods:
            pe_label = "财报EPS_TTM·拆股调整" if split_adjusted[index] else "财报EPS_TTM"
        else:
            pe_label = "财报推导"
        pe_source.append(pe_label)
        if official_pb is not None:
            pb_label = "日频"
        elif derived_pb is None:
            pb_label = None
        elif bps_split_adjusted[index]:
            pb_label = "财报推导·拆股调整"
        else:
            pb_label = "财报推导"
        pb_source.append(pb_label)
        pe_period.append(None if official_pe is not None else eps_periods[index])
        pb_period.append(None if official_pb is not None else bps_periods[index])

    return {
        "pe": pe,
        "pb": pb,
        "roe": roe_values,
        "pe_source": pe_source,
        "pb_source": pb_source,
        "pe_period": pe_period,
        "pb_period": pb_period,
        "roe_period": roe_periods,
    }
