"""股利折现（Gordon 增长模型）。"""

from __future__ import annotations

from fundamental.models import ValuationResult


def evaluate_ddm(
    dividend_per_share: float | None,
    current_price: float | None,
    growth: float = 0.05,
    discount: float = 0.08,
) -> ValuationResult:
    if not dividend_per_share or not current_price or discount <= growth:
        return ValuationResult(method="ddm")
    fair_value = dividend_per_share * (1.0 + growth) / (discount - growth)
    margin = fair_value / current_price - 1.0
    return ValuationResult(
        method="ddm",
        fair_value_low=round(fair_value * 0.8, 2),
        fair_value_high=round(fair_value * 1.2, 2),
        current_price=current_price,
        margin_of_safety=round(margin, 4),
        assumptions={"growth": growth, "discount": discount},
        inputs={"dividend_per_share": dividend_per_share},
    )
