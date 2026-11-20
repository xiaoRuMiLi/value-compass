"""财务五维评分。"""

from __future__ import annotations

from fundamental.models import CompositeScore, DimensionScore, FinancialReport


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_profitability(
    report: FinancialReport, selling_expense_ratio: float | None = None
) -> DimensionScore:
    """盈利能力 = 净利率 + 扣非净利率 + 销售费用率 + 毛利转化 + ROE 的结构化评分。"""

    lines: list[str] = []
    total = 0.0

    net_margin = report.net_margin
    if net_margin is None:
        return DimensionScore("盈利能力", 0.0, 25, "净利率数据缺失")
    earned = _clamp(net_margin / 20.0, 0, 1) * 30
    total += earned
    lines.append(f"净利率 {net_margin:.2f}% → {earned:.1f}/30（min(净利率/20%,1)×30）")

    deducted = None
    if report.net_profit_deducted is not None and report.revenue:
        deducted = report.net_profit_deducted / report.revenue * 100
    effective = deducted if deducted is not None else net_margin
    earned = _clamp(effective / 18.0, 0, 1) * 20
    total += earned
    note = "" if deducted is not None else "（扣非缺失，以净利率替代）"
    lines.append(f"扣非净利率 {effective:.2f}% → {earned:.1f}/20（min(扣非净利率/18%,1)×20）{note}")

    if selling_expense_ratio is not None:
        earned = _clamp((40.0 - selling_expense_ratio) / 40.0, 0, 1) * 20
        lines.append(
            f"销售费用率 {selling_expense_ratio:.2f}% → {earned:.1f}/20"
            "（max(1-销售费用率/40%,0)×20）"
        )
    else:
        earned = 10.0
        lines.append("销售费用率 数据缺失 → 10.0/20（中性给分）")
    total += earned

    conversion = None
    if report.gross_margin:
        conversion = net_margin / report.gross_margin * 100
    if conversion is not None:
        earned = _clamp(conversion / 60.0, 0, 1) * 20
        lines.append(
            f"毛利→净利转化率 {conversion:.2f}% → {earned:.1f}/20（min(转化率/60%,1)×20）"
        )
    else:
        earned = 0.0
        lines.append("毛利→净利转化率 数据缺失 → 0.0/20")
    total += earned

    roe = report.roe
    if roe is not None:
        earned = _clamp(roe / 15.0, 0, 1) * 10
        lines.append(f"ROE {roe:.2f}% → {earned:.1f}/10（min(ROE/15%,1)×10）")
    else:
        earned = 0.0
        lines.append("ROE 数据缺失 → 0.0/10")
    total += earned

    return DimensionScore("盈利能力", round(min(total, 100.0), 1), 25, "\n".join(lines))


def _score_growth(report: FinancialReport) -> DimensionScore:
    lines: list[str] = []
    total = 0.0
    if report.revenue_yoy is not None:
        earned = _clamp(report.revenue_yoy / 10.0, 0, 1) * 50
        total += earned
        lines.append(f"营收同比 {report.revenue_yoy:.2f}% → {earned:.1f}/50（min(营收同比/10%,1)×50）")
    else:
        lines.append("营收同比 数据缺失 → 0.0/50")
    if report.net_profit_yoy is not None:
        earned = _clamp(report.net_profit_yoy / 15.0, 0, 1) * 50
        total += earned
        lines.append(f"净利同比 {report.net_profit_yoy:.2f}% → {earned:.1f}/50（min(净利同比/15%,1)×50）")
    else:
        lines.append("净利同比 数据缺失 → 0.0/50")
    return DimensionScore("成长能力", round(total, 1), 25, "\n".join(lines))


def _score_health(report: FinancialReport) -> DimensionScore:
    lines: list[str] = []
    total = 0.0
    if report.debt_ratio is not None:
        earned = _clamp((50.0 - report.debt_ratio) / 50.0, 0, 1) * 60
        total += earned
        lines.append(
            f"资产负债率 {report.debt_ratio:.2f}% → {earned:.1f}/60"
            "（min(max((50%-资产负债率)/50%,0),1)×60）"
        )
    else:
        lines.append("资产负债率 数据缺失 → 0.0/60")
    if report.current_ratio is not None:
        earned = _clamp(report.current_ratio / 2.0, 0, 1) * 40
        total += earned
        lines.append(f"流动比率 {report.current_ratio:.2f} → {earned:.1f}/40（min(流动比率/2,1)×40）")
    else:
        lines.append("流动比率 数据缺失 → 0.0/40")
    return DimensionScore("财务健康", round(total, 1), 20, "\n".join(lines))


def _score_valuation(pe_ttm: float | None) -> DimensionScore:
    if pe_ttm is None:
        return DimensionScore("估值水平", 50.0, 15, "PE(TTM) 数据缺失 → 50.0/100（中性给分）")
    score = _clamp((40.0 - pe_ttm) / 40.0, 0, 1) * 100
    return DimensionScore(
        "估值水平", round(score, 1), 15,
        f"PE(TTM) {pe_ttm:.2f} → {score:.1f}/100（min(max(1-PE/40,0),1)×100）",
    )


def _score_returns(dividend_yield: float | None) -> DimensionScore:
    if dividend_yield is None:
        return DimensionScore("股东回报", 50.0, 15, "股息率 数据缺失 → 50.0/100（中性给分）")
    score = _clamp(dividend_yield / 2.0, 0, 1) * 100
    return DimensionScore(
        "股东回报", round(score, 1), 15,
        f"股息率 {dividend_yield:.2f}% → {score:.1f}/100（min(股息率/2%,1)×100）",
    )


def _rating(total: float) -> str:
    if total >= 80:
        return "强烈推荐"
    if total >= 70:
        return "推荐"
    if total >= 60:
        return "观望"
    return "回避"


def score_financial(
    report: FinancialReport,
    dividend_yield: float | None = None,
    pe_ttm: float | None = None,
    selling_expense_ratio: float | None = None,
) -> CompositeScore:
    dimensions = [
        _score_profitability(report, selling_expense_ratio),
        _score_growth(report),
        _score_health(report),
        _score_valuation(pe_ttm),
        _score_returns(dividend_yield),
    ]
    total = sum(dimension.score * dimension.weight / 100.0 for dimension in dimensions)
    return CompositeScore(total=round(total, 1), rating=_rating(total), dimensions=dimensions, red_flags=[])
