"""无风险利率锚点：为 FCFF/WACC 折现率提供可追溯的市场利率下限。

口径（用户约定）：
- A 股（SH/SZ/BJ）：无风险利率取中国 10 年期国债到期收益率（cn10y）。
- 其它市场（HK/US）：取美国 10 年期国债到期收益率（us10y）；
  港股中资股现金流以人民币计、无风险利率以美元计，明确标注币种错配。
- 债务成本 Kd = Rf + 信用利差（在 Rf 上按一定百分点加，可配置）。
- 折现率（WACC）仍由 LLM 结合财报与研报校准，但不得低于
  「Rf + 最低风险溢价」这一下限；下限只是约束，不替代 LLM 定参。

本模块只读宏观指标表，不依赖估值结果，因此不会形成 analyze → 校准 → analyze 循环。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from typing import Any

from fundamental.config import load_settings
from fundamental.models import Market
from fundamental.storage import db
from fundamental.utils.symbols import parse_symbol

# 默认建模假设（可在 config/default.toml [valuation] 覆盖）。
_DEFAULT_CREDIT_SPREAD = 0.015   # 债务成本 = Rf + 1.5 个百分点
_DEFAULT_MIN_PREMIUM = 0.04      # 折现率下限 = Rf + 4 个百分点
_DEFAULT_FALLBACK_RF = 0.02      # 完全无宏观数据时的兜底参考值（仅用于展示，不钳制）

_A_MARKETS = {Market.SH, Market.SZ, Market.BJ}


@dataclass(frozen=True)
class RiskFreeAnchor:
    """单个标的的无风险利率锚点及派生参数。"""

    market: str
    rf_code: str
    rf_label: str
    rf_pct: float | None          # 百分数，如 1.59
    rf_rate: float | None         # 小数，如 0.0159
    rf_date: str | None
    rf_source: str | None
    rf_currency: str             # 无风险利率计价币种
    cashflow_currency: str       # 财报现金流计价币种
    currency_mismatch: bool
    credit_spread_pct: float
    debt_cost_pct: float | None
    debt_cost_rate: float | None
    min_risk_premium_pct: float
    discount_floor_pct: float | None
    discount_floor: float | None  # 小数；无数据时为 None（不钳制）
    note: str
    missing: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest_macro(conn: sqlite3.Connection, code: str) -> tuple[float | None, str | None, str | None]:
    row = conn.execute(
        "SELECT value, trade_date, source FROM macro_indicators "
        "WHERE indicator_code = ? AND value IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT 1",
        (code,),
    ).fetchone()
    if row is None:
        return None, None, None
    return row["value"], row["trade_date"], row["source"]


def resolve_risk_free(symbol: str, conn: sqlite3.Connection | None = None) -> RiskFreeAnchor:
    """按标的所属市场解析无风险利率锚点。

    conn 可选：调用方（analyze_stock）已有连接时复用，避免重复开库；
    不传则自行开关一个只读连接。
    """
    settings = load_settings()
    credit_spread = float(settings.get("valuation.rf_credit_spread", _DEFAULT_CREDIT_SPREAD))
    min_premium = float(settings.get("valuation.rf_min_risk_premium", _DEFAULT_MIN_PREMIUM))

    _, market = parse_symbol(symbol)
    if market in _A_MARKETS:
        rf_code, rf_label, rf_ccy = "cn10y", "中国10年期国债收益率", "CNY"
        cashflow_ccy, mismatch = "CNY", False
    else:
        rf_code, rf_label, rf_ccy = "us10y", "美国10年期国债收益率", "USD"
        # 港股中资股财报多为人民币；美股为美元。
        cashflow_ccy = "USD" if market == Market.US else "CNY"
        mismatch = market != Market.US

    owns_conn = conn is None
    if owns_conn:
        conn = db.connect()
    try:
        value_pct, trade_date, source = _latest_macro(conn, rf_code)  # type: ignore[arg-type]
    finally:
        if owns_conn:
            conn.close()  # type:ignore[union-attr]

    missing = value_pct is None
    rf_rate = None if missing else float(value_pct) / 100.0
    debt_rate = None if missing else rf_rate + credit_spread  # type: ignore[operator]
    floor = None if missing else rf_rate + min_premium  # type: ignore[operator]

    parts = [
        f"无风险利率 Rf 取 {rf_label}"
        + (f" {value_pct:.4f}%（{trade_date}，{source}）" if not missing else "，但库内暂无该指标数据"),
    ]
    if not missing:
        parts.append(
            f"债务成本 Kd = Rf + {credit_spread * 100:.2f} 个百分点 = {debt_rate * 100:.4f}%（信用利差为建模假设）"
        )
        parts.append(
            f"折现率下限 = Rf + {min_premium * 100:.2f} 个百分点 = {floor * 100:.4f}%；"
            "最终 WACC 仍由财报与研报校准，仅约束其不低于该下限，不以此反推 WACC"
        )
        if mismatch:
            parts.append(
                f"币种错配提示：无风险利率以{rf_ccy}计、公司现金流以{cashflow_ccy}计，"
                "按市场规则统一采用境外10年期国债，跨市场比较时需注意该口径差异"
            )
    else:
        parts.append("因缺少国债数据，本次未对 LLM 折现率施加利率下限，参数仍为纯校准假设")
    note = "；".join(parts) + "。"

    return RiskFreeAnchor(
        market=str(market),
        rf_code=rf_code,
        rf_label=rf_label,
        rf_pct=round(float(value_pct), 4) if not missing else None,
        rf_rate=round(rf_rate, 6) if not missing else None,  # type: ignore[arg-type]
        rf_date=trade_date,
        rf_source=source,
        rf_currency=rf_ccy,
        cashflow_currency=cashflow_ccy,
        currency_mismatch=mismatch,
        credit_spread_pct=round(credit_spread * 100, 2),
        debt_cost_pct=round(debt_rate * 100, 4) if debt_rate is not None else None,
        debt_cost_rate=round(debt_rate, 6) if debt_rate is not None else None,
        min_risk_premium_pct=round(min_premium * 100, 2),
        discount_floor_pct=round(floor * 100, 4) if floor is not None else None,
        discount_floor=round(floor, 6) if floor is not None else None,
        note=note,
        missing=missing,
    )
