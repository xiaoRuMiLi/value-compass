"""造假预警（简化 M-score 思路）。"""

from __future__ import annotations

from fundamental.models import FinancialReport, FraudScore


def score_fraud(symbol: str, financials: list[FinancialReport]) -> FraudScore:
    ordered = sorted(financials, key=lambda item: item.report_date)
    latest = ordered[-1] if ordered else None
    signals: list[str] = []
    score = 0.0

    if latest and latest.net_profit and latest.net_profit_deducted is not None:
        gap = (latest.net_profit - latest.net_profit_deducted) / abs(latest.net_profit)
        if gap > 0.2:
            signals.append(f"扣非净利显著低于归母净利（差 {gap:.0%}）")
            score += 25

    gross_margins = [item.gross_margin for item in ordered if item.gross_margin is not None]
    if len(gross_margins) >= 3 and max(gross_margins) - min(gross_margins) > 10:
        signals.append("毛利率大幅波动")
        score += 25

    if (
        latest
        and latest.revenue_yoy is not None
        and latest.net_profit_yoy is not None
        and latest.revenue_yoy > 0
        and latest.net_profit_yoy < 0
    ):
        signals.append("增收不增利")
        score += 25

    if latest and latest.debt_ratio is not None and latest.debt_ratio > 70:
        signals.append("资产负债率过高")
        score += 25

    return FraudScore(symbol=symbol, score=min(score, 100.0), signals=signals)
