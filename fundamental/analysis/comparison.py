"""横向对比：单公司指标计算。"""

from __future__ import annotations

from datetime import date

from fundamental.analysis.cycle import assess_cycle
from fundamental.models import CompanyComparison, DividendRecord, FinancialReport, ReportType


def compute_company_comparison(
    symbol: str,
    name: str,
    industry: str,
    financials: list[FinancialReport],
    dividends: list[DividendRecord] | None = None,
) -> CompanyComparison:
    ordered = sorted(financials, key=lambda item: item.report_date)
    yearly = [item for item in ordered if item.report_type == ReportType.FY]
    latest = ordered[-1] if ordered else None

    revenue_cagr: float | None = None
    if len(yearly) >= 2 and yearly[0].revenue and yearly[-1].revenue and yearly[0].revenue > 0:
        years = (date.fromisoformat(yearly[-1].report_date) - date.fromisoformat(yearly[0].report_date)).days / 365.25
        if years > 0:
            revenue_cagr = (yearly[-1].revenue / yearly[0].revenue) ** (1.0 / years) - 1.0

    payout: float | None = None
    if dividends and yearly and yearly[-1].eps and yearly[-1].eps > 0:
        year = yearly[-1].report_date[:4]
        per_share = sum(
            item.cash_per_share
            for item in dividends
            if item.cash_per_share and item.ex_date and item.ex_date[:4] == year
        )
        payout = per_share / yearly[-1].eps

    cycle = assess_cycle(financials)
    return CompanyComparison(
        symbol=symbol,
        name=name,
        industry=industry,
        revenue_cagr=round(revenue_cagr, 4) if revenue_cagr is not None else None,
        gross_margin=latest.gross_margin if latest else None,
        net_margin=latest.net_margin if latest else None,
        roe=latest.roe if latest else None,
        dividend_payout_ratio=round(payout, 4) if payout is not None else None,
        cycle_position=cycle.position,
    )
