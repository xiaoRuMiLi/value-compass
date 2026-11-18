"""市场周期判断：按年度净利增速趋势。"""

from __future__ import annotations

from fundamental.models import CycleAssessment, FinancialReport, ReportType


def assess_cycle(financials: list[FinancialReport]) -> CycleAssessment:
    yearly = sorted(
        [item for item in financials if item.report_type == ReportType.FY],
        key=lambda item: item.report_date,
    )
    growth = [item.net_profit_yoy for item in yearly if item.net_profit_yoy is not None]
    if len(growth) < 2:
        return CycleAssessment(position="无法判断", evidence=[])

    recent = growth[-3:]
    evidence = [f"近 {len(recent)} 年净利同比: {', '.join(f'{value:.1f}%' for value in recent)}"]
    if all(value > 0 for value in recent) and recent[-1] > recent[0]:
        return CycleAssessment(position="上行", evidence=evidence)
    if recent[-1] > 0 and recent[-1] < recent[-2]:
        return CycleAssessment(position="顶部", evidence=evidence)
    if recent[-1] < 0 and any(value > 0 for value in recent[:-1]):
        return CycleAssessment(position="下行", evidence=evidence)
    if all(value < 0 for value in recent):
        return CycleAssessment(position="底部", evidence=evidence)
    return CycleAssessment(position="震荡", evidence=evidence)
