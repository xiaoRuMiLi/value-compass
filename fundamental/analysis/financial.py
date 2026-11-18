"""财务五维评分。"""

from __future__ import annotations

from fundamental.models import CompositeScore, DimensionScore, FinancialReport


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_profitability(report: FinancialReport) -> DimensionScore:
    score = 0.0
    if report.roe is not None:
        score += _clamp(report.roe / 15.0, 0, 1) * 40
    if report.gross_margin is not None:
        score += _clamp(report.gross_margin / 30.0, 0, 1) * 30
    if report.net_margin is not None:
        score += _clamp(report.net_margin / 10.0, 0, 1) * 30
    return DimensionScore("盈利能力", round(score, 1), 25)


def _score_growth(report: FinancialReport) -> DimensionScore:
    score = 0.0
    if report.revenue_yoy is not None:
        score += _clamp(report.revenue_yoy / 10.0, 0, 1) * 50
    if report.net_profit_yoy is not None:
        score += _clamp(report.net_profit_yoy / 15.0, 0, 1) * 50
    return DimensionScore("成长能力", round(score, 1), 25)


def _score_health(report: FinancialReport) -> DimensionScore:
    score = 0.0
    if report.debt_ratio is not None:
        score += _clamp((50.0 - report.debt_ratio) / 50.0, 0, 1) * 60
    if report.current_ratio is not None:
        score += _clamp(report.current_ratio / 2.0, 0, 1) * 40
    return DimensionScore("财务健康", round(score, 1), 20)


def _score_valuation(pe_ttm: float | None) -> DimensionScore:
    if pe_ttm is None:
        return DimensionScore("估值水平", 50.0, 15)
    score = _clamp((40.0 - pe_ttm) / 40.0, 0, 1) * 100
    return DimensionScore("估值水平", round(score, 1), 15)


def _score_returns(dividend_yield: float | None) -> DimensionScore:
    if dividend_yield is None:
        return DimensionScore("股东回报", 50.0, 15)
    return DimensionScore("股东回报", round(_clamp(dividend_yield / 2.0, 0, 1) * 100, 1), 15)


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
) -> CompositeScore:
    dimensions = [
        _score_profitability(report),
        _score_growth(report),
        _score_health(report),
        _score_valuation(pe_ttm),
        _score_returns(dividend_yield),
    ]
    total = sum(dimension.score * dimension.weight / 100.0 for dimension in dimensions)
    return CompositeScore(total=round(total, 1), rating=_rating(total), dimensions=dimensions, red_flags=[])
