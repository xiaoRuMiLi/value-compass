"""现金流折现（两阶段简化模型，FCFE）。"""

from __future__ import annotations

from fundamental.models import ValuationResult


def evaluate_dcf(
    fcf: float | None,
    shares: float | None,
    current_price: float | None,
    growth: float = 0.10,
    discount: float = 0.09,
    years: int = 10,
    terminal_growth: float = 0.03,
) -> ValuationResult:
    if not fcf or not shares or not current_price or discount <= terminal_growth:
        return ValuationResult(method="dcf")
    present_value = 0.0
    cash = fcf
    for year in range(1, years + 1):
        cash *= 1.0 + growth
        present_value += cash / (1.0 + discount) ** year
    terminal = cash * (1.0 + terminal_growth) / (discount - terminal_growth)
    present_value += terminal / (1.0 + discount) ** years
    fair_value = present_value / shares
    margin = fair_value / current_price - 1.0
    return ValuationResult(
        method="dcf",
        fair_value_low=round(fair_value * 0.8, 2),
        fair_value_high=round(fair_value * 1.2, 2),
        current_price=current_price,
        margin_of_safety=round(margin, 4),
        assumptions={
            "formula": "两阶段FCFE：未来10年自由现金流折现 + 永续增长终值，区间取 ±20%",
            "growth": growth,
            "discount": discount,
            "years": years,
            "terminal_growth": terminal_growth,
        },
        inputs={"fcf": fcf, "shares": shares},
    )
