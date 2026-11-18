"""退市风险：面值/市值/财务类退市指标。"""

from __future__ import annotations

from fundamental.models import DelistingRisk, FinancialReport


def check_delisting(
    symbol: str,
    price: float | None,
    market_cap: float | None,
    financials: list[FinancialReport],
) -> DelistingRisk:
    triggers: list[str] = []
    score = 0.0
    if price is not None and price < 1.0:
        triggers.append("面值低于 1 元")
        score += 50
    if market_cap is not None and market_cap < 3e9:
        triggers.append("市值低于 3 亿元")
        score += 50

    ordered = sorted(financials, key=lambda item: item.report_date, reverse=True)
    recent = ordered[:2]
    if (
        len(recent) == 2
        and all(item.net_profit is not None and item.net_profit < 0 for item in recent)
        and all(item.revenue is not None and item.revenue < 1e8 for item in recent)
    ):
        triggers.append("连续亏损且营收低于 1 亿元")
        score += 80

    return DelistingRisk(symbol=symbol, score=min(score, 100.0), triggers=triggers)
