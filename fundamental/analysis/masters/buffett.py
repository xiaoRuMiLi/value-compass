"""巴菲特模型：ROE/毛利率/负债率/成长性。"""

from __future__ import annotations

from fundamental.analysis.masters.base import build_verdict
from fundamental.config import load_settings
from fundamental.models import DividendRecord, FinancialReport, MasterVerdict, ReportType


class BuffettModel:
    name = "buffett"

    def evaluate(
        self,
        symbol: str,
        financials: list[FinancialReport],
        dividends: list[DividendRecord] | None = None,
    ) -> MasterVerdict:
        thresholds = load_settings().get("masters.buffett.thresholds") or {}
        roe_min = float(thresholds.get("roe_min", 15))
        gross_min = float(thresholds.get("gross_margin_min", 30))
        debt_max = float(thresholds.get("debt_ratio_max", 50))
        growth_min = float(thresholds.get("revenue_growth_min", 10))

        ordered = sorted(financials, key=lambda item: item.report_date)
        latest = ordered[-1] if ordered else None
        yearly = [item for item in ordered if item.report_type == ReportType.FY][-3:]

        roe_ok = len(yearly) >= 3 and all(item.roe is not None and item.roe >= roe_min for item in yearly)
        gross_ok = latest is not None and latest.gross_margin is not None and latest.gross_margin >= gross_min
        debt_ok = latest is not None and latest.debt_ratio is not None and latest.debt_ratio <= debt_max
        growth_ok = latest is not None and latest.revenue_yoy is not None and latest.revenue_yoy >= growth_min

        signals = [
            {"name": f"ROE 连续 3 年 ≥ {roe_min}%", "passed": roe_ok, "detail": _yearly_roe(yearly)},
            {"name": f"毛利率 ≥ {gross_min}%", "passed": gross_ok, "detail": _fmt(latest.gross_margin if latest else None)},
            {"name": f"资产负债率 ≤ {debt_max}%", "passed": debt_ok, "detail": _fmt(latest.debt_ratio if latest else None)},
            {"name": f"营收增速 ≥ {growth_min}%", "passed": growth_ok, "detail": _fmt(latest.revenue_yoy if latest else None)},
        ]
        return build_verdict(symbol, self.name, signals)


def _yearly_roe(yearly: list[FinancialReport]) -> str:
    return ", ".join(f"{item.roe:.1f}%" for item in yearly if item.roe is not None)


def _fmt(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "-"
