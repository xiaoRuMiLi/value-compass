"""段永平模型：生意模式（毛利/净利）、现金流、稳健负债。"""

from __future__ import annotations

from fundamental.analysis.masters.base import build_verdict
from fundamental.config import load_settings
from fundamental.models import DividendRecord, FinancialReport, MasterVerdict


class DuanYongpingModel:
    name = "duan_yongping"

    def evaluate(
        self,
        symbol: str,
        financials: list[FinancialReport],
        dividends: list[DividendRecord] | None = None,
    ) -> MasterVerdict:
        thresholds = load_settings().get("masters.duan_yongping.thresholds") or {}
        gross_min = float(thresholds.get("gross_margin_min", 25))
        net_min = float(thresholds.get("net_margin_min", 15))
        roe_min = float(thresholds.get("roe_min", 15))
        debt_max = float(thresholds.get("debt_ratio_max", 40))

        ordered = sorted(financials, key=lambda item: item.report_date)
        latest = ordered[-1] if ordered else None
        signals = [
            {"name": f"毛利率 ≥ {gross_min}%", "passed": latest is not None and latest.gross_margin is not None and latest.gross_margin >= gross_min, "detail": _fmt(latest.gross_margin if latest else None)},
            {"name": f"净利率 ≥ {net_min}%", "passed": latest is not None and latest.net_margin is not None and latest.net_margin >= net_min, "detail": _fmt(latest.net_margin if latest else None)},
            {"name": f"ROE ≥ {roe_min}%", "passed": latest is not None and latest.roe is not None and latest.roe >= roe_min, "detail": _fmt(latest.roe if latest else None)},
            {"name": f"资产负债率 ≤ {debt_max}%", "passed": latest is not None and latest.debt_ratio is not None and latest.debt_ratio <= debt_max, "detail": _fmt(latest.debt_ratio if latest else None)},
        ]
        return build_verdict(symbol, self.name, signals)


def _fmt(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "-"
