"""护城河评估：毛利率持续性与 ROE 水平。"""

from __future__ import annotations

from fundamental.models import FinancialReport, MoatAssessment


def assess_moat(symbol: str, financials: list[FinancialReport]) -> MoatAssessment:
    if not financials:
        return MoatAssessment(symbol=symbol, rating="未知", evidence=[])
    ordered = sorted(financials, key=lambda item: item.report_date)
    gross_margins = [item.gross_margin for item in ordered if item.gross_margin is not None]
    roes = [item.roe for item in ordered if item.roe is not None]

    score = 0.0
    evidence: list[str] = []
    if gross_margins:
        average = sum(gross_margins) / len(gross_margins)
        spread = max(gross_margins) - min(gross_margins)
        stability = 1.0 - spread / max(average, 1.0)
        score += 40.0 if average >= 40 else (25.0 if average >= 25 else 10.0)
        score += 30.0 if stability >= 0.9 else (20.0 if stability >= 0.7 else 10.0)
        evidence.append(f"毛利率均值 {average:.1f}%，波动 {spread:.1f}pp")
    if roes:
        average_roe = sum(roes) / len(roes)
        score += 30.0 if average_roe >= 20 else (20.0 if average_roe >= 15 else 10.0)
        evidence.append(f"ROE 均值 {average_roe:.1f}%")

    rating = "宽" if score >= 80 else ("中" if score >= 50 else "窄")
    return MoatAssessment(symbol=symbol, rating=rating, evidence=evidence, width=rating)
