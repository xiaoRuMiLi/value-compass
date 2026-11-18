"""财报体检：大白话汇总 + 疑点清单。"""

from __future__ import annotations

from typing import Any

from fundamental.models import (
    DelistingRisk,
    FinancialReport,
    FinancialReviewReport,
    FraudScore,
    IntegrityRecord,
    RedFlag,
)


def generate_review(
    symbol: str,
    financials: list[FinancialReport],
    red_flags: list[RedFlag] | None = None,
    fraud: FraudScore | None = None,
    delisting: DelistingRisk | None = None,
    integrity: list[IntegrityRecord] | None = None,
) -> FinancialReviewReport:
    ordered = sorted(financials, key=lambda item: item.report_date, reverse=True)
    latest = ordered[0] if ordered else None

    parts: list[str] = []
    if latest and latest.report_date:
        parts.append(f"报告期 {latest.report_date}")
    if latest and latest.revenue is not None:
        parts.append(f"营业收入 {latest.revenue / 1e8:.1f} 亿元")
    if latest and latest.net_profit is not None:
        parts.append(f"归母净利润 {latest.net_profit / 1e8:.1f} 亿元")
    if latest and latest.roe is not None:
        parts.append(f"ROE {latest.roe:.1f}%")
    if latest and latest.gross_margin is not None:
        parts.append(f"毛利率 {latest.gross_margin:.1f}%")
    if latest and latest.debt_ratio is not None:
        parts.append(f"资产负债率 {latest.debt_ratio:.1f}%")
    summary = "；".join(parts) or "暂无财务数据"

    findings: list[dict[str, Any]] = []
    for flag in red_flags or []:
        findings.append({"severity": flag.severity.value, "type": flag.flag_type, "detail": flag.description})
    if fraud and fraud.signals:
        findings.append({"severity": "medium", "type": "造假预警", "detail": "；".join(fraud.signals)})
    if delisting and delisting.triggers:
        findings.append({"severity": "high", "type": "退市风险", "detail": "；".join(delisting.triggers)})

    negative = sum(1 for item in integrity or [] if item.behavior_type.value == "negative")
    integrity_summary = f"诚信记录 {len(integrity or [])} 条，其中负面 {negative} 条"
    return FinancialReviewReport(
        symbol=symbol,
        summary=summary,
        findings=findings,
        integrity_summary=integrity_summary,
        score=fraud.score if fraud else None,
    )
