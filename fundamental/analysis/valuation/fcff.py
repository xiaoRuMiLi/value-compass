"""企业自由现金流折现（FCFF / Unlevered DCF）+ EV→股权价值桥接。

与页面现有的「现金流折现（OCF 期初）」「自由现金流折现（FCFE 口径）」并列，
本模块走企业价值路径：

    FCFF ≈ 经营活动现金流量净额 + 实付利息 − 资本开支
    EV   = FCFF 两阶段按 WACC 折现的现值 + 永续终值现值
    股权价值 = EV − 有息负债 + 超额现金 + 非经营投资(折价) − 少数股东权益

说明：
- 现金流量表近似版 FCFF 已内含营运资本变动，因此不再单独扣减营运资本；
  严格的 EBIT×(1−税)+折旧摊销−资本开支−营运资本变动公式需要更多假设，
  在计算器说明中列出，项目默认以上式为准。
- WACC 仍由 LLM 结合财报/研报校准，不假装是计算值；但无风险利率 Rf（A股取中国10Y国债、
  其它市场取美国10Y国债）已落库，作为折现率下限的锚点，并把取值与来源写入 assumptions。
- 超额现金 = 货币资金 + 类金融资产 − 营业收入×最低经营现金比例；
  非经营投资按可折价比例计入，避免把经营资产重复加回。
"""

from __future__ import annotations

from typing import Any

from fundamental.models import ValuationResult


def _num(value: float | None) -> float:
    return float(value) if value is not None else 0.0


_BRIDGE_LABELS = {
    "cash": "货币资金",
    "marketable_securities": "交易性金融资产/存款",
    "nonoperating_invest": "非经营投资",
    "debt": "有息负债",
    "minority_equity": "少数股东权益",
    "revenue": "营业收入",
}


def evaluate_fcff(
    ocf: float | None,
    shares: float | None,
    current_price: float | None,
    *,
    interest_expense: float | None = None,
    capex: float | None = None,
    cash: float | None = None,
    marketable_securities: float | None = None,
    nonoperating_invest: float | None = None,
    debt: float | None = None,
    minority_equity: float | None = None,
    revenue: float | None = None,
    growth: float = 0.05,
    discount: float = 0.10,
    terminal_growth: float = 0.02,
    years: int = 10,
    excess_cash_ratio: float = 0.02,
    nonoperating_discount: float = 0.8,
    component_note: str | None = None,
    risk_free: dict[str, Any] | None = None,
) -> ValuationResult:
    """两阶段 FCFF 折现 + EV→股权桥接；金额单位均为元，股本为股。

    折现率由 LLM 校准，但不得低于「Rf + 最低风险溢价」的市场锚点下限；
    下限只是约束，不以此反推 WACC，也不改变 LLM 给出的更高取值。
    """
    rf_floor = (risk_free or {}).get("discount_floor")
    llm_discount = discount
    if isinstance(rf_floor, (int, float)) and rf_floor > 0:
        discount = max(float(discount), float(rf_floor))
    floor_applied = discount > llm_discount + 1e-9
    method = "fcff"
    fcff = None if ocf is None else _num(ocf) + _num(interest_expense) - _num(capex)
    if (
        fcff is None
        or not shares
        or not current_price
        or discount <= terminal_growth
    ):
        return ValuationResult(method=method)

    years = max(1, int(years))
    cash_flow = fcff
    pv = 0.0
    for year in range(1, years + 1):
        cash_flow *= 1.0 + growth
        pv += cash_flow / (1.0 + discount) ** year
    terminal = cash_flow * (1.0 + terminal_growth) / (discount - terminal_growth)
    terminal_pv = terminal / (1.0 + discount) ** years
    enterprise_value = pv + terminal_pv

    min_operating_cash = _num(revenue) * max(0.0, float(excess_cash_ratio))
    excess_cash = max(0.0, _num(cash) - min_operating_cash) + _num(marketable_securities)
    discounted_nonop = _num(nonoperating_invest) * max(0.0, min(1.0, float(nonoperating_discount)))
    equity_value = (
        enterprise_value
        - _num(debt)
        + excess_cash
        + discounted_nonop
        - _num(minority_equity)
    )
    if equity_value <= 0:
        return ValuationResult(method=method)

    fair_value = equity_value / shares
    supplied = {
        "cash": cash,
        "marketable_securities": marketable_securities,
        "nonoperating_invest": nonoperating_invest,
        "debt": debt,
        "minority_equity": minority_equity,
        "revenue": revenue,
    }
    missing = [label for key, label in _BRIDGE_LABELS.items() if supplied[key] is None]
    assumptions: dict[str, Any] = {
        "formula": (
            "企业自由现金流折现（FCFF/WACC）：FCFF = 经营活动现金流量净额 + 实付利息 − 资本开支；"
            "按 WACC 两阶段折现得企业价值 EV，再桥接股权价值；区间取 ±20%"
        ),
        "bridge_formula": (
            "股权价值 = EV − 有息负债 + 超额现金 + 非经营投资(折价) − 少数股东权益"
        ),
        "growth": growth,
        "discount": discount,
        "terminal_growth": terminal_growth,
        "years": years,
        "excess_cash_rule": (
            f"超额现金 = 货币资金 + 交易性金融资产/存款 − 营业收入×{excess_cash_ratio:.1%}"
            "（经营所需最低现金）；受限制资金不计入"
        ),
        "nonoperating_discount": nonoperating_discount,
    }
    if missing:
        assumptions["missing_bridge"] = "、".join(missing) + " 本期未单独披露或为 0，按 0 桥接；如财报列示该类资产，可在计算器中补填"
    if component_note:
        assumptions["component_note"] = component_note
    if risk_free:
        assumptions["risk_free"] = dict(risk_free)
        assumptions["llm_discount"] = round(float(llm_discount), 4)
        assumptions["discount_floor_applied"] = floor_applied
        assumptions["wacc_note"] = (
            "折现率为加权平均资本成本（WACC），由财报与研报校准；"
            + (risk_free.get("note") or "")
            + ("；本次 LLM 取值低于利率下限，已按下限执行。" if floor_applied else "")
        )

    inputs: dict[str, Any] = {
        "ocf": ocf,
        "interest_expense": interest_expense,
        "capex": capex,
        "fcff": fcff,
        "cash": cash,
        "marketable_securities": marketable_securities,
        "nonoperating_invest": nonoperating_invest,
        "debt": debt,
        "minority_equity": minority_equity,
        "revenue": revenue,
        "min_operating_cash": min_operating_cash,
        "excess_cash": excess_cash,
        "nonoperating_assets_discounted": discounted_nonop,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "shares": shares,
    }
    return ValuationResult(
        method=method,
        fair_value_low=round(fair_value * 0.8, 2),
        fair_value_high=round(fair_value * 1.2, 2),
        current_price=current_price,
        margin_of_safety=round(fair_value / current_price - 1.0, 4),
        assumptions=assumptions,
        inputs=inputs,
    )


# 折现率在 FCFF 口径下的正式名称是 WACC，保留别名便于调用方语义化。
evaluate_enterprise_fcf = evaluate_fcff
