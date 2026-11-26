"""企业自由现金流折现（FCFF / Unlevered DCF）+ EV→股权价值桥接。

与页面现有的「现金流折现（OCF 期初）」「自由现金流折现（FCFE 口径）」并列，
本模块走企业价值路径：

    FCFF ≈ 经营活动现金流量净额 + 实付利息 − 资本开支
    EV   = FCFF 两阶段按 WACC 折现的现值 + 永续终值现值
    股权价值 = EV − 有息负债 + 超额现金 + 非经营投资(折价) − 少数股东权益

口径：国际通用做法（CFI）从 CFO 出发加回的是**税后**利息 `利息×(1−税率)`；
本项目按税前实付利息加回，属保守/简化处理，且不加回利息收入（已在 CFO 内，
严格 FCFF 应把非经营利息收入剔除并改用桥接单列金融资产）。

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
    "cash": "货币资金/存款",
    "marketable_securities": "公允价值类金融资产",
    "nonoperating_invest": "权益类投资",
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
    fair_value_assets: float | None = None,
    equity_assets: float | None = None,
    debt: float | None = None,
    minority_equity: float | None = None,
    revenue: float | None = None,
    growth: float = 0.05,
    discount: float = 0.10,
    terminal_growth: float = 0.02,
    years: int = 10,
    excess_cash_ratio: float = 0.02,
    nonoperating_discount: float = 0.8,
    fair_value_discount: float = 0.8,
    equity_discount: float = 0.8,
    component_note: str | None = None,
    risk_free: dict[str, Any] | None = None,
) -> ValuationResult:
    """两阶段 FCFF 折现 + EV→股权桥接；金额单位均为元，股本为股。

    折现率由 LLM 校准，但不得低于「Rf + 最低风险溢价」的市场锚点下限；
    下限只是约束，不以此反推 WACC，也不改变 LLM 给出的更高取值。

    非经营资产按类别分档计入股权价值（折价均为建模假设，由配置注入）：
    - fair_value_assets（公允价值类金融资产）按 fair_value_discount；
    - equity_assets（权益类投资）按 equity_discount；
    - 现金及存款/理财并入 cash，走超额现金、不打折。
    旧参数 marketable_securities / nonoperating_invest 仍兼容：前者归入公允价值档，
    后者归入权益档；新参数显式传入时覆盖旧值。
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

    # 分档桥接：新参数优先，缺省回落到旧参数（保持旧调用方兼容）。
    fv_raw = fair_value_assets if fair_value_assets is not None else marketable_securities
    eq_raw = equity_assets if equity_assets is not None else nonoperating_invest

    def _haircut(rate: float) -> float:
        return max(0.0, min(1.0, float(rate)))

    # 旧调用只传 nonoperating_invest + nonoperating_discount 时，用旧折扣覆盖两档，
    # 使历史测试与校准行为完全一致。
    if fair_value_assets is None and equity_assets is None and nonoperating_invest is not None:
        fv_discount = eq_discount = nonoperating_discount
    else:
        fv_discount = fair_value_discount
        eq_discount = equity_discount

    min_operating_cash = _num(revenue) * max(0.0, float(excess_cash_ratio))
    # 现金、短期/中长期存款按面值全额加回，不打折。
    excess_cash = max(0.0, _num(cash) - min_operating_cash)
    fv_discounted = _num(fv_raw) * _haircut(fv_discount)
    eq_discounted = _num(eq_raw) * _haircut(eq_discount)
    discounted_nonop = fv_discounted + eq_discounted
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
    # 缺失提示按「实际参与桥接的取值」判断：校准重算只传新键，
    # 旧键（marketable_securities / nonoperating_invest）为空属正常，不应误报。
    supplied = {
        "cash": cash,
        "marketable_securities": fv_raw,
        "nonoperating_invest": eq_raw,
        "debt": debt,
        "minority_equity": minority_equity,
        "revenue": revenue,
    }
    missing = [label for key, label in _BRIDGE_LABELS.items() if supplied[key] is None]
    assumptions: dict[str, Any] = {
        "formula": (
            "企业自由现金流折现（FCFF/WACC）：FCFF = 经营活动现金流量净额 + 实付利息 − 资本开支；"
            "国际通用口径加回应为税后利息，本项目按税前实付利息简化；利息收入不加回；"
            "按 WACC 两阶段折现得企业价值 EV，再桥接股权价值；区间取 ±20%"
        ),
        "bridge_formula": (
            "股权价值 = EV − 有息负债 + 超额现金 + 公允价值类资产×折价 + 权益类资产×折价 − 少数股东权益"
        ),
        "growth": growth,
        "discount": discount,
        "terminal_growth": terminal_growth,
        "years": years,
        "excess_cash_rule": (
            f"超额现金 = 货币资金 + 短期/中长期存款与理财 − 营业收入×{excess_cash_ratio:.1%}"
            "（经营所需最低现金）；存款与理财按面值加回、不打折；受限制资金不计入"
        ),
        "nonoperating_discount": nonoperating_discount,
        "fair_value_discount": fv_discount,
        "equity_discount": eq_discount,
        "excess_cash_ratio": max(0.0, float(excess_cash_ratio)),
        "bridge_discount_note": (
            "公允价值类金融资产按 %.0f%%、权益类投资按 %.0f%% 计入；"
            "现金与短期/中长期存款理财按面值全额计入"
            % (fv_discount * 100, eq_discount * 100)
        ),
    }
    if missing:
        assumptions["missing_bridge"] = (
            "、".join(missing) + " 本期未取到对应科目数据，按 0 桥接；"
            "如财报列示该类资产，可在计算器中补填"
        )
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
        "fair_value_assets": fv_raw,
        "equity_assets": eq_raw,
        "fair_value_discounted": fv_discounted,
        "equity_discounted": eq_discounted,
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
