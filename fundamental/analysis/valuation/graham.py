"""格雷厄姆估值。"""

from __future__ import annotations

import math

from fundamental.models import ValuationResult


def evaluate_graham(
    eps: float | None,
    bps: float | None,
    current_price: float | None,
) -> ValuationResult:
    if not eps or eps <= 0 or not bps or not current_price:
        return ValuationResult(method="graham")
    graham_value = math.sqrt(22.5 * eps * bps)
    margin = graham_value / current_price - 1.0
    return ValuationResult(
        method="graham",
        fair_value_low=round(graham_value * 0.8, 2),
        fair_value_high=round(graham_value * 1.2, 2),
        current_price=current_price,
        margin_of_safety=round(margin, 4),
        assumptions={"formula": "sqrt(22.5 × EPS × BPS)，区间取 ±20%"},
        inputs={"eps": eps, "bps": bps},
    )
