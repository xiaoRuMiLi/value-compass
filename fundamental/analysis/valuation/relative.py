"""相对估值：PE/PB 历史分位。"""

from __future__ import annotations

from statistics import median

from fundamental.models import ValuationPoint, ValuationResult


def evaluate_relative(
    valuations: list[ValuationPoint],
    eps: float | None,
    current_price: float | None,
) -> ValuationResult:
    pe_values = [v.pe_ttm for v in valuations if v.pe_ttm and v.pe_ttm > 0]
    if not pe_values or not eps or not current_price:
        return ValuationResult(method="relative")

    current_pe = pe_values[-1]
    median_pe = median(pe_values)
    fair_value = median_pe * eps
    fair_low = sorted(pe_values)[int(len(pe_values) * 0.2)] * eps
    fair_high = sorted(pe_values)[int(len(pe_values) * 0.8)] * eps
    percentile = sum(1 for value in pe_values if value <= current_pe) / len(pe_values)
    margin = fair_value / current_price - 1.0
    return ValuationResult(
        method="relative",
        fair_value_low=round(fair_low, 2),
        fair_value_high=round(fair_high, 2),
        current_price=current_price,
        margin_of_safety=round(margin, 4),
        assumptions={"median_pe": round(median_pe, 2), "pe_percentile": round(percentile, 4)},
        inputs={"eps": eps, "current_pe": current_pe},
    )
