"""风险红旗排查。"""

from __future__ import annotations

from fundamental.models import AuditOpinion, FinancialReport, IntegrityRecord, RedFlag, Severity


def check_red_flags(
    financials: list[FinancialReport],
    integrity: list[IntegrityRecord] | None = None,
) -> list[RedFlag]:
    flags: list[RedFlag] = []
    ordered = sorted(financials, key=lambda item: item.report_date, reverse=True)
    latest = ordered[0] if ordered else None

    if latest and latest.audit_opinion in (
        AuditOpinion.QUALIFIED,
        AuditOpinion.DISCLAIMER,
        AuditOpinion.ADVERSE,
    ):
        flags.append(
            RedFlag("审计意见非标准", Severity.HIGH, f"最近报告期审计意见为 {latest.audit_opinion.value}")
        )

    losses = [
        item for item in ordered[:4] if item.net_profit is not None and item.net_profit < 0
    ]
    if len(losses) >= 3:
        flags.append(RedFlag("连续亏损", Severity.HIGH, f"近 4 期中有 {len(losses)} 期归母净利为负"))

    if latest and latest.net_profit and latest.net_profit_deducted is not None:
        gap = (latest.net_profit - latest.net_profit_deducted) / abs(latest.net_profit)
        if gap > 0.3:
            flags.append(RedFlag("利润含金量不足", Severity.MEDIUM, f"扣非净利低于归母净利 {gap:.0%}"))

    if integrity:
        high = [item for item in integrity if item.severity == Severity.HIGH]
        if high:
            flags.append(RedFlag("诚信高危事件", Severity.HIGH, f"存在 {len(high)} 条高严重度诚信记录"))

    return flags
