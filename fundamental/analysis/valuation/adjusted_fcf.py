"""自由现金流折现（标准 FCF 口径）。

期初自由现金流按市场常用公式计算：

    FCF = 经营活动现金流量净额 − 资本开支 + 利息收入 − 利息支出

营运资本（存货/应收/应付/预付）的增减不再进入公式，只作为现金流质量提示项传入，
用来提醒「经营现金流被应付项目阶段性抬高」这类可能的失真。
之后按两阶段折现：前 N 年按 g 增长逐年折现，末尾按永续终值折现。
"""

from __future__ import annotations

from typing import Any

from fundamental.models import ValuationResult


def _num(value: float | None) -> float:
    return float(value) if value is not None else 0.0


def evaluate_fcf(
    ocf: float | None,
    shares: float | None,
    current_price: float | None,
    capex: float | None = 0.0,
    interest_income: float | None = 0.0,
    interest_expense: float | None = 0.0,
    interest_income_disclosed: float | None = None,
    growth: float = 0.04,
    discount: float = 0.10,
    years: int = 10,
    terminal_growth: float = 0.02,
    working_capital: dict[str, float] | None = None,
    working_capital_alert: str | None = None,
    component_note: str | None = None,
) -> ValuationResult:
    """标准自由现金流两阶段折现。

    FCF = OCF − 资本开支 + 利息收入 − 利息支出。
    working_capital / working_capital_alert 只写入结果与提示，不参与计算。
    """
    method = "adjusted_fcf"
    fcf = None if ocf is None else (
        _num(ocf) - _num(capex) + _num(interest_income) - _num(interest_expense)
    )
    if (
        fcf is None
        or not shares
        or not current_price
        or discount <= terminal_growth
    ):
        return ValuationResult(method=method)

    present_value = 0.0
    cash = fcf
    for year in range(1, years + 1):
        cash *= 1.0 + growth
        present_value += cash / (1.0 + discount) ** year
    terminal = cash * (1.0 + terminal_growth) / (discount - terminal_growth)
    present_value += terminal / (1.0 + discount) ** years

    fair_value = present_value / shares
    margin = fair_value / current_price - 1.0
    inputs: dict[str, Any] = {
        "ocf": ocf,
        "capex": _num(capex),
        "interest_income": _num(interest_income),
        # 原文披露的利息收入：已含在 OCF，不进公式，仅供前端展示与口径核对。
        "interest_income_disclosed": interest_income_disclosed,
        "interest_expense": _num(interest_expense),
        "fcf": fcf,
        "shares": shares,
        "working_capital_changes": dict(working_capital or {}),
    }
    assumptions: dict[str, Any] = {
        "formula": (
            "标准FCF两阶段折现：FCF = 经营活动现金流量净额 − 资本开支 + 利息收入 − 利息支出，"
            "前N年按g增长折现 + 永续终值，区间取±20%；营运资本变动仅作现金流质量提示，不进入公式"
        ),
        "growth": growth,
        "discount": discount,
        "years": years,
        "terminal_growth": terminal_growth,
    }
    if working_capital_alert:
        assumptions["working_capital_alert"] = working_capital_alert
    if component_note:
        assumptions["component_note"] = component_note
    return ValuationResult(
        method=method,
        fair_value_low=round(fair_value * 0.8, 2),
        fair_value_high=round(fair_value * 1.2, 2),
        current_price=current_price,
        margin_of_safety=round(margin, 4),
        assumptions=assumptions,
        inputs=inputs,
    )


# 兼容旧调用方：方法名与接口保持不变，语义已切到标准 FCF 口径。
evaluate_adjusted_fcf = evaluate_fcf
