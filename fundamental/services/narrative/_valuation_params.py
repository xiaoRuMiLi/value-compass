"""估值参数校准：本地基本面 + 联网研报 → LLM 给保守参数 → 复用公式重算 + 股息复投预期。

校准参数是 DDM/DCF 的唯一长参来源：生成后写入缓存，主估值链路（analyze_stock）读取
同一份参数重算，避免页面上方「固定默认」与下方「校准」出现两套互相矛盾的合理价。
analysis 层仍是纯函数，参数由 services 层注入，失败时回退到由历史增速推导的保守参数。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fundamental.analysis.valuation import evaluate_ddm, evaluate_dcf, evaluate_fcf, evaluate_fcff
from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat_json, has_live_llm
from fundamental.services.risk_free import resolve_risk_free
from fundamental.utils.intel import load_intel_digest
from fundamental.utils.prompts import load_prompt

from ._common import (
    _build_prompt,
    _max_tokens,
    _now,
    _read_cache,
    _valuation_params_cache_path,
    _write_cache,
)
from ._data import _load_master_context, _load_score_financials

# 参数边界：防止 LLM 给出极端值导致 Gordon 公式失真。
_GROWTH_MIN, _GROWTH_MAX = -0.05, 0.12
_DISCOUNT_MIN, _DISCOUNT_MAX = 0.07, 0.14
_TERMINAL_MIN, _TERMINAL_MAX = 0.0, 0.04
_GAP = 0.02  # discount - growth 的最小间距

# 校准逻辑升级时递增，让缓存失效。
# v4：估值主链路改为注入校准参数；旧缓存里的 rationale 基于缺失的 DPS/FCF，必须重算。
# v5：新增乐观/悲观情景三档输出。
# v6：自由现金流折现（标准 FCF 口径）纳入校准与三档情景，与 DDM/DCF 同源参数。
# v7：DCF 期初改为未调整的 OCF（与 FCF 口径区分）；港股 OCF/资本开支/利息从原文拆分，
#     校准重算改用同一份现金流输入，旧缓存需重算。
# v8：新增 FCFF/WACC 企业价值折现 + EV→股权桥接，独立卡片与三档参数。
# v9：WACC 引入无风险利率市场锚点（A股中国10Y国债/其它美国10Y国债），
#     LLM 折现率受「Rf + 最低风险溢价」下限约束，旧缓存需重算。
_VALUATION_PARAMS_VERSION = 9

_METHOD_LABELS = {
    "ddm": "股利折现",
    "dcf": "现金流折现",
    "adjusted_fcf": "自由现金流折现",
    "fcff": "企业自由现金流折现",
}
_RATING_LABELS = {
    "buy": "买入",
    "overweight": "增持",
    "neutral": "中性",
    "underweight": "减持",
    "sell": "卖出",
}
_CHANGE_LABELS = {"upgrade": "上调", "downgrade": "下调", "maintain": "维持"}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    return max(low, min(high, _num(value, default)))


_DEFAULTS = {
    "ddm": {"growth": 0.03, "discount": 0.09},
    "dcf": {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.02},
    "adjusted_fcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02},
    "fcff": {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.02},
}


def _two_stage_block(raw: Any, defaults: dict[str, float], rationale_fallback: str) -> dict[str, Any]:
    """归一化两阶段现金流模型（dcf / adjusted_fcf / fcff）的 g / 永续 g / r。"""
    raw = raw if isinstance(raw, dict) else {}
    g = _clamp(raw.get("growth"), _GROWTH_MIN, _GROWTH_MAX, defaults["growth"])
    t = _clamp(raw.get("terminal_growth"), _TERMINAL_MIN, _TERMINAL_MAX, defaults["terminal_growth"])
    r = _clamp(raw.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, defaults["discount"])
    if r - g < _GAP:
        r = min(_DISCOUNT_MAX, g + _GAP)
    if r - t < _GAP:
        t = max(_TERMINAL_MIN, r - _GAP)
    text = raw.get("rationale")
    return {
        "growth": round(g, 4),
        "discount": round(r, 4),
        "terminal_growth": round(t, 4),
        "rationale": str(text).strip() if isinstance(text, str) and text.strip() else rationale_fallback,
    }


def _normalize_block(raw: Any, defaults: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """把一组 DDM/DCF 参数钳制到边界，并保证 discount - growth 有足够间距。"""

    raw = raw if isinstance(raw, dict) else {}
    ddm = raw.get("ddm") if isinstance(raw.get("ddm"), dict) else {}
    base = defaults or _DEFAULTS
    dd = base["ddm"]

    ddm_g = _clamp(ddm.get("growth"), _GROWTH_MIN, _GROWTH_MAX, dd["growth"])
    ddm_r = _clamp(ddm.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, dd["discount"])
    if ddm_r - ddm_g < _GAP:
        ddm_r = min(_DISCOUNT_MAX, ddm_g + _GAP)
        if ddm_r - ddm_g < _GAP:  # g 贴到上限时反过来压 g
            ddm_g = ddm_r - _GAP

    def rationale(block: dict[str, Any], fallback: str) -> str:
        text = block.get("rationale")
        return str(text).strip() if isinstance(text, str) and text.strip() else fallback

    return {
        "ddm": {
            "growth": round(ddm_g, 4),
            "discount": round(ddm_r, 4),
            "rationale": rationale(ddm, "由模型按保守原则校准；详见参数依据。"),
        },
        "dcf": _two_stage_block(
            raw.get("dcf"), base["dcf"], "由模型按保守原则校准；详见参数依据。"
        ),
        "adjusted_fcf": _two_stage_block(
            raw.get("adjusted_fcf"), base["adjusted_fcf"], "由模型按保守原则校准；详见参数依据。"
        ),
        "fcff": _two_stage_block(
            raw.get("fcff"), base["fcff"], "由模型按保守原则校准；详见参数依据。"
        ),
    }


def _derive_scenarios(base: dict[str, Any]) -> dict[str, Any]:
    """LLM 未给情景时，从基准参数派生乐观/悲观档（仍经边界钳制）。"""

    d, c = base["ddm"], base["dcf"]
    f = base.get("adjusted_fcf") or _DEFAULTS["adjusted_fcf"]
    e = base.get("fcff") or _DEFAULTS["fcff"]
    return {
        "optimistic": _normalize_block(
            {
                "ddm": {"growth": d["growth"] + 0.01, "discount": d["discount"] - 0.01,
                        "rationale": "假设主业景气回升、股东回报政策延续，增长上调、折现率下调；属情景推演，非基准预测。"},
                "dcf": {"growth": c["growth"] + 0.02, "discount": c["discount"] - 0.01,
                        "terminal_growth": c["terminal_growth"] + 0.01,
                        "rationale": "假设自由现金流改善、风险溢价回落，增长与永续上调；属情景推演，非基准预测。"},
                "adjusted_fcf": {"growth": f["growth"] + 0.02, "discount": f["discount"] - 0.01,
                                 "terminal_growth": f["terminal_growth"] + 0.01,
                                 "rationale": "假设经营现金流改善、资本开支回落且加权成本下降，标准FCF与永续上调；属情景推演，非基准预测。"},
                "fcff": {"growth": e["growth"] + 0.02, "discount": e["discount"] - 0.01,
                         "terminal_growth": e["terminal_growth"] + 0.01,
                         "rationale": "假设经营景气回升、资本开支效率改善，企业自由现金流与永续增长上调、WACC 回落；股权桥接项不变；属情景推演。"},
            }
        ),
        "pessimistic": _normalize_block(
            {
                "ddm": {"growth": d["growth"] - 0.01, "discount": d["discount"] + 0.01,
                        "rationale": "假设增长放缓、股东回报不可持续，增长下调、折现率上调；属情景推演，非基准预测。"},
                "dcf": {"growth": c["growth"] - 0.02, "discount": c["discount"] + 0.01,
                        "terminal_growth": c["terminal_growth"] - 0.01,
                        "rationale": "假设竞争加剧、现金流质量走弱，增长与永续下调；属情景推演，非基准预测。"},
                "adjusted_fcf": {"growth": f["growth"] - 0.02, "discount": f["discount"] + 0.01,
                                 "terminal_growth": max(0.0, f["terminal_growth"] - 0.01),
                                 "rationale": "假设增长放缓、资本开支高企或应收回款走弱拖累标准FCF，增长与永续下调；属情景推演，非基准预测。"},
                "fcff": {"growth": e["growth"] - 0.02, "discount": e["discount"] + 0.01,
                         "terminal_growth": max(0.0, e["terminal_growth"] - 0.01),
                         "rationale": "假设主业增长放缓、资本开支刚性与融资成本上升推高 WACC，企业自由现金流与永续下调；属情景推演。"},
            }
        ),
    }


def _normalize_params(raw: Any) -> dict[str, Any]:
    """归一化 LLM 输出：基准 + 乐观/悲观情景，全部钳制到边界。"""

    raw = raw if isinstance(raw, dict) else {}
    base = _normalize_block(raw)
    scenarios_raw = raw.get("scenarios") if isinstance(raw.get("scenarios"), dict) else {}
    derived = _derive_scenarios(base)
    scenarios: dict[str, Any] = {}
    for name in ("optimistic", "pessimistic"):
        block = scenarios_raw.get(name)
        if isinstance(block, dict) and any(
            isinstance(block.get(key), dict) for key in ("ddm", "dcf", "adjusted_fcf", "fcff")
        ):
            scenarios[name] = _normalize_block(block)
        else:
            scenarios[name] = derived[name]
    base["scenarios"] = scenarios
    base["summary"] = str(raw.get("summary") or "").strip() if isinstance(raw.get("summary"), str) else ""
    return base


def _fallback_params(facts: dict[str, Any]) -> dict[str, Any]:
    """无 LLM 时，用公司历史增速推导保守参数（模板回退）。"""

    growth_values = [
        item.get("net_profit_yoy")
        for item in (facts.get("annual_growth") or [])
        if isinstance(item.get("net_profit_yoy"), (int, float))
    ]
    # 取近 3 年净利增速中位数，向下修正并限制在边界内。
    recent = growth_values[-3:]
    if recent:
        ordered = sorted(recent)
        median = ordered[len(ordered) // 2]
        hist_g = median / 100.0
    else:
        hist_g = 0.03
    g = _clamp(min(hist_g, 0.06), _GROWTH_MIN, 0.08, 0.03)  # 永续 g 更保守，上限 8%
    base = {
        "ddm": {
            "growth": round(g, 4),
            "discount": round(max(g + 0.04, 0.09), 4),
            "rationale": "按近三年净利增速中位数向下修正得到永续增长率，折现率取增长率之上至少 4 个百分点，属保守估计。",
        },
        "dcf": {
            "growth": round(min(max(g, 0.03), 0.08), 4),
            "discount": 0.10,
            "terminal_growth": round(min(g, 0.025), 4),
            "rationale": "增长阶段增速参考近年净利增速并封顶，永续增长率压低，折现率取 10% 反映风险。",
        },
        "adjusted_fcf": {
            "growth": round(min(max(g, 0.03), 0.08), 4),
            "discount": 0.10,
            "terminal_growth": round(min(g, 0.025), 4),
            "rationale": "标准FCF口径，增长假设与现金流折现同源：参考近年净利增速并封顶，永续压低，折现率取 10%。",
        },
        "fcff": {
            "growth": round(min(max(g, 0.03), 0.08), 4),
            "discount": 0.10,
            "terminal_growth": round(min(g, 0.025), 4),
            "rationale": "企业自由现金流口径，增长参考近年净利增速并封顶、永续压低；WACC 取 10% 作为保守假设（库内无 Beta 与债务成本，非计算值）。",
        },
        "summary": "模板回退：参数由历史净利增速推导，未经 LLM 结合行业与研报校准，仅供参考。",
    }
    base["scenarios"] = _derive_scenarios(base)
    return base


def _dividend_reinvestment(
    dps: float | None, price: float | None, growth: float, years: int = 10
) -> dict[str, Any] | None:
    """10 年股息复投预期：每年股息按收盘价再买入，股价按增长率 g 增长。

    返回 10 年末持股数、期末市值、累计投入与简单年化，用于把抽象估值具象化。
    """

    if not dps or not price or price <= 0:
        return None
    shares = 1.0  # 以初始 1 股为基准
    years_to_grow = [0.0] * (years + 1)
    total_dividend = 0.0
    for year in range(1, years + 1):
        dividend_per_share = dps * (1.0 + growth) ** (year - 1)
        share_price = price * (1.0 + growth) ** (year - 1)
        cash = shares * dividend_per_share
        total_dividend += cash
        if share_price > 0:
            shares += cash / share_price
        years_to_grow[year] = share_price
    ending_price = price * (1.0 + growth) ** years
    ending_value = shares * ending_price
    total_return = ending_value / price - 1.0
    annualized = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else None
    return {
        "years": years,
        "growth": round(growth, 4),
        "initial_shares": 1.0,
        "ending_shares": round(shares, 4),
        "ending_price": round(ending_price, 2),
        "ending_value": round(ending_value, 2),
        "total_dividend": round(total_dividend, 2),
        "total_return": round(total_return, 4),
        "annualized": round(annualized, 4) if annualized is not None else None,
    }


def _valuation_facts(
    result: dict[str, Any],
    financials: dict[str, Any],
    context: dict[str, Any],
    research: list[dict[str, Any]],
    intel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = result.get("snapshot")
    price = snapshot.price if snapshot else None
    # 无风险利率市场锚点：给 LLM 的 facts 带上 Rf 与折现率下限，
    # 让它知道 fcff.discount 不应低于该下限；重算时也会用同一份锚点。
    symbol = str(result.get("symbol") or "")
    try:
        risk_free = resolve_risk_free(symbol).as_dict() if symbol else None
    except Exception:  # noqa: BLE001 - 宏观数据缺失不应阻断校准
        risk_free = None
    settings = load_settings()
    intel_limit = int(settings.get("valuation.intel_limit", 3))
    industry_chars = int(settings.get("valuation.industry_chars", 1200))
    survey_chars = int(settings.get("valuation.survey_chars", 1500))
    dps = None
    dcf_cash = None
    shares = None
    fcf_components = None
    component_note = None
    fcff_inputs = None
    for item in result.get("valuation") or []:
        if item.method == "ddm":
            dps = (item.inputs or {}).get("dividend_per_share")
        elif item.method == "dcf":
            # 现金流折现用未扣资本开支/利息的 OCF，与标准 FCF 卡片区分口径。
            dcf_cash = (item.inputs or {}).get("fcf")
            shares = (item.inputs or {}).get("shares")
        elif item.method == "adjusted_fcf":
            # 标准 FCF 口径的分项输入：OCF − 资本开支 + 利息收入 − 利息支出。
            inp = item.inputs or {}
            src = inp.get("sources") or {}
            fcf_components = {
                "ocf": inp.get("ocf", src.get("ocf")),
                "capex": inp.get("capex", src.get("capex")),
                "interest_income": inp.get("interest_income", src.get("interest_income")),
                "interest_expense": inp.get("interest_expense", src.get("interest_expense")),
            }
            component_note = (item.assumptions or {}).get("component_note")
        elif item.method == "fcff":
            # EV→股权桥接原始分项，重算时与 analyze 主链路完全同源。
            inp = item.inputs or {}
            fcff_inputs = {
                key: inp.get(key)
                for key in (
                    "ocf", "capex", "interest_expense", "cash", "marketable_securities",
                    "nonoperating_invest", "debt", "minority_equity", "revenue",
                )
            }

    return {
        "company": {
            "name": (context.get("company_basics") or {}).get("name") or result.get("symbol"),
            "industry": (context.get("company_basics") or {}).get("industry"),
            "profile_summary": (context.get("company_basics") or {}).get("profile_summary"),
            "price": price,
        },
        "industry": context.get("industry"),
        "peer_comparison": context.get("peer_comparison"),
        "market_valuation": context.get("market_valuation"),
        "latest_report": context.get("latest_report"),
        "annual_growth": financials.get("annual_growth", []),
        "annual_quality": financials.get("annual_quality", []),
        "dividend_history": financials.get("dividend_history", []),
        "model_inputs": {
            "dps": dps, "dcf_cash": dcf_cash, "shares": shares, "price": price,
            "fcf_components": fcf_components, "component_note": component_note,
            "fcff_inputs": fcff_inputs,
        },
        "risk_free": risk_free,
        "research_reports": research,
        "sellside": _sellside_sentiment(research),
        "intel": {
            "industry_reports": [
                {
                    "date": item.get("date") or "",
                    "org": item.get("org") or "",
                    "title": item.get("title") or "",
                    "excerpt": (item.get("content") or "")[:industry_chars],
                }
                for item in (intel or {}).get("industry_reports", [])[:intel_limit]
            ],
            "surveys": [
                {
                    "date": item.get("date") or "",
                    "org": item.get("org") or "",
                    "title": item.get("title") or "",
                    "excerpt": (item.get("content") or "")[:survey_chars],
                }
                for item in (intel or {}).get("surveys", [])[:intel_limit]
            ],
        },
    }


def _overall_range(calibrated: dict[str, Any]) -> dict[str, Any] | None:
    """把可用模型的区间合成为一个直观的合理股价区间。

    下沿取各模型最低值、上沿取最高值，中枢取各模型中点均值；
    缺少自由现金流的公司（如银行）只有 DDM 可用时，明确标注依据，不臆造 DCF 结果。
    """

    available: list[tuple[str, dict[str, Any]]] = []
    for method in ("ddm", "dcf", "adjusted_fcf", "fcff"):
        item = calibrated.get(method) or {}
        low, high = item.get("fair_value_low"), item.get("fair_value_high")
        if (
            isinstance(low, (int, float))
            and isinstance(high, (int, float))
            and 0 < low <= high
        ):
            available.append((method, item))
    if not available:
        return None

    lows = [item["fair_value_low"] for _, item in available]
    highs = [item["fair_value_high"] for _, item in available]
    centers = [(item["fair_value_low"] + item["fair_value_high"]) / 2 for _, item in available]
    center = round(sum(centers) / len(centers), 2)
    price = next(
        (item.get("current_price") for _, item in available if item.get("current_price")),
        None,
    )
    margin = round(center / price - 1, 4) if isinstance(price, (int, float)) and price > 0 else None
    methods = [method for method, _ in available]
    labels = "、".join(_METHOD_LABELS[m] for m in methods)
    note = (
        f"综合{labels}共 {len(methods)} 种模型：区间取各模型并集，中枢为各模型中点均值。"
        if len(methods) > 1
        else f"仅基于{labels}模型，其他模型因缺少必要输入未参与，合理价参考性有限。"
    )
    return {
        "low": round(min(lows), 2),
        "high": round(max(highs), 2),
        "center": center,
        "current_price": price,
        "margin_of_safety": margin,
        "methods": methods,
        "note": note,
    }


def _sellside_sentiment(
    reports: list[dict[str, Any]] | None, window_days: int = 30
) -> dict[str, Any]:
    """卖方分歧度：评级分布、目标价区间与近期评级变动（数据缺失则明确标注）。"""

    reports = reports or []
    distribution: dict[str, int] = {}
    changes = {"upgrade": 0, "downgrade": 0, "maintain": 0}
    targets: list[float] = []
    recent = 0
    today = date.today()
    for report in reports:
        rating = report.get("rating")
        if isinstance(rating, str) and rating:
            distribution[rating] = distribution.get(rating, 0) + 1
        change = report.get("rating_change")
        if isinstance(change, str) and change in changes:
            changes[change] += 1
        target = report.get("target_price")
        if isinstance(target, (int, float)) and target > 0:
            targets.append(float(target))
        raw_date = str(report.get("date") or "")[:10]
        try:
            if (today - date.fromisoformat(raw_date)).days <= window_days:
                recent += 1
        except ValueError:
            pass

    if not reports:
        summary = "暂无研报覆盖，卖方观点数据不足。"
    else:
        parts = [f"研报共 {len(reports)} 篇，近 {window_days} 天 {recent} 篇"]
        top = sorted(distribution.items(), key=lambda item: -item[1])[:2]
        if top:
            parts.append(
                "评级分布："
                + "、".join(f"{_RATING_LABELS.get(key, key)}{value}家" for key, value in top)
            )
        moves = [f"{_CHANGE_LABELS[key]}{value}家" for key, value in changes.items() if value]
        if moves:
            parts.append("评级变动：" + "、".join(moves))
        if targets:
            parts.append(f"目标价 {min(targets):.2f}~{max(targets):.2f} 元")
        summary = "；".join(parts) + "。卖方观点仅供参考，可能偏乐观。"

    return {
        "total": len(reports),
        "recent_count": recent,
        "window_days": window_days,
        "rating_distribution": distribution,
        "rating_changes": changes,
        "target_price_low": round(min(targets), 2) if targets else None,
        "target_price_high": round(max(targets), 2) if targets else None,
        "summary": summary,
    }


def _citations(intel: dict[str, Any] | None) -> list[dict[str, str]]:
    """汇总可溯源的参考资料清单（日期/机构/标题/原文链接）。"""

    settings = load_settings()
    per_kind = int(settings.get("valuation.citation_per_kind", 5))
    total = int(settings.get("valuation.citation_total", 20))

    groups = (
        ("个股研报", "company_reports"),
        ("行业研报", "industry_reports"),
        ("机构调研", "surveys"),
    )
    citations: list[dict[str, str]] = []
    for kind, key in groups:
        for item in ((intel or {}).get(key) or [])[:per_kind]:
            citations.append(
                {
                    "kind": kind,
                    "date": str(item.get("date") or ""),
                    "org": str(item.get("org") or ""),
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                }
            )
    return citations[:total]


def _recompute(facts: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """用校准参数复用项目公式重算 DDM/DCF，并给出股息复投预期。"""

    inputs = facts.get("model_inputs") or {}
    price = inputs.get("price")
    dps = inputs.get("dps")
    dcf_cash = inputs.get("dcf_cash")
    shares = inputs.get("shares")
    fcfc = inputs.get("fcf_components") or {}
    component_note = inputs.get("component_note")
    fic = inputs.get("fcff_inputs") or {}
    risk_free = facts.get("risk_free")

    def to_dict(result: Any, method: str) -> dict[str, Any]:
        return {
            "method": method,
            "fair_value_low": result.fair_value_low,
            "fair_value_high": result.fair_value_high,
            "margin_of_safety": result.margin_of_safety,
            "current_price": result.current_price,
            "assumptions": dict(result.assumptions),
            "inputs": dict(result.inputs),
            "basis": "calibrated",
        }

    def evaluate_one(p: dict[str, Any]) -> dict[str, Any]:
        d, c = p["ddm"], p["dcf"]
        f = p.get("adjusted_fcf") or p["dcf"]
        e = p.get("fcff") or p["dcf"]
        block = {
            "ddm": to_dict(
                evaluate_ddm(dps, price, growth=d["growth"], discount=d["discount"]), "ddm"
            ),
            "dcf": to_dict(
                evaluate_dcf(
                    dcf_cash, shares, price,
                    growth=c["growth"], discount=c["discount"],
                    terminal_growth=c["terminal_growth"],
                ),
                "dcf",
            ),
            "adjusted_fcf": to_dict(
                evaluate_fcf(
                    fcfc.get("ocf"), shares, price,
                    capex=fcfc.get("capex"),
                    interest_income=fcfc.get("interest_income"),
                    interest_expense=fcfc.get("interest_expense"),
                    growth=f["growth"], discount=f["discount"],
                    terminal_growth=f["terminal_growth"],
                    component_note=component_note,
                ),
                "adjusted_fcf",
            ),
            "fcff": to_dict(
                evaluate_fcff(
                    fic.get("ocf"), shares, price,
                    interest_expense=fic.get("interest_expense"),
                    capex=fic.get("capex"),
                    cash=fic.get("cash"),
                    marketable_securities=fic.get("marketable_securities"),
                    nonoperating_invest=fic.get("nonoperating_invest"),
                    debt=fic.get("debt"),
                    minority_equity=fic.get("minority_equity"),
                    revenue=fic.get("revenue"),
                    growth=e["growth"], discount=e["discount"],
                    terminal_growth=e["terminal_growth"],
                    risk_free=risk_free,
                ),
                "fcff",
            ),
        }
        block["overall"] = _overall_range(block)
        return block

    calibrated = evaluate_one(params)
    d = params["ddm"]
    calibrated["dividend_reinvestment"] = _dividend_reinvestment(dps, price, d["growth"])
    # 乐观/悲观情景各自独立重算，输入不变、只变假设，得到可对照的三档合理价。
    calibrated["scenarios"] = {
        name: evaluate_one(p) for name, p in (params.get("scenarios") or {}).items()
    }
    return calibrated


def _assemble(
    facts: dict[str, Any], params: dict[str, Any], source: str
) -> dict[str, Any]:
    result = {
        "params": params,
        "calibrated": _recompute(facts, params),
        "source": source,
    }
    if source == "llm":
        result["research_count"] = len(facts.get("research_reports") or [])
    return result


def _finalize(facts: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_params(facts)
    if not has_live_llm():
        return _assemble(facts, fallback, "template")
    try:
        system = load_prompt("valuation_params")
        data = chat_json(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=_max_tokens("valuation_params", 3500),
        )
        params = _normalize_params(data)
        return _assemble(facts, params, "llm")
    except (LlmError, LlmConfigError, FileNotFoundError):
        return _assemble(facts, fallback, "template")


def load_calibration_params(symbol: str) -> dict[str, Any] | None:
    """读取已缓存的校准参数，供 analyze 主链路注入。

    只读不生成，避免 analyze → 校准 → analyze 的循环；缓存不存在或版本过旧时返回 None，
    由 analyze 使用 analysis 层的默认参数，待校准端点生成后下次调用自动生效。
    """

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cached = _read_cache(_valuation_params_cache_path(symbol), ttl_days)
    if not cached or cached.get("params_version") != _VALUATION_PARAMS_VERSION:
        return None
    params = cached.get("params")
    if not isinstance(params, dict) or not params.get("ddm") or not params.get("dcf"):
        return None
    return params


def generate_valuation_params(
    symbol: str,
    db_path: Path | None = None,
    refresh: bool = False,
    research_limit: int | None = None,
) -> dict[str, Any]:
    """生成估值参数校准结果：优先 LLM，失败回退模板，带独立缓存。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _valuation_params_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached and cached.get("params_version") == _VALUATION_PARAMS_VERSION:
            return cached

    result = analyze_stock(symbol, db_path)
    if "error" in result:
        return {
            "symbol": symbol,
            "error": result["error"],
            "source": "template",
            "generated_at": _now(),
        }

    financials = _load_score_financials(symbol, db_path)
    context = _load_master_context(symbol, db_path)
    intel = load_intel_digest(symbol, refresh=refresh, research_limit=research_limit)
    research = intel.get("company_reports") or []
    facts = _valuation_facts(result, financials, context, research, intel)

    output = _finalize(facts)
    output["sellside"] = _sellside_sentiment(research)
    output["citations"] = _citations(intel)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    output["params_version"] = _VALUATION_PARAMS_VERSION
    _write_cache(cache_path, output)
    return output
