"""估值参数校准：本地基本面 + 联网研报 → LLM 给保守参数 → 复用公式重算 + 股息复投预期。

与默认估值（analyze 里写死的 g/r）并存，作为「参数校准」对照展示，不改动 analyze 主链路。
失败或未配置模型时回退到由历史增速推导的保守参数，始终给出可解释的结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.analysis.valuation import evaluate_ddm, evaluate_dcf
from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat_json, has_live_llm
from fundamental.utils.prompts import load_prompt
from fundamental.utils.research_text import load_research_digest

from ._common import (
    _build_prompt,
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
_VALUATION_PARAMS_VERSION = 1


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    return max(low, min(high, _num(value, default)))


def _normalize_params(raw: Any) -> dict[str, Any]:
    """把 LLM 输出的参数钳制到边界，并保证 discount - growth 有足够间距。"""

    raw = raw if isinstance(raw, dict) else {}
    ddm = raw.get("ddm") if isinstance(raw.get("ddm"), dict) else {}
    dcf = raw.get("dcf") if isinstance(raw.get("dcf"), dict) else {}

    ddm_g = _clamp(ddm.get("growth"), _GROWTH_MIN, _GROWTH_MAX, 0.03)
    ddm_r = _clamp(ddm.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, 0.09)
    if ddm_r - ddm_g < _GAP:
        ddm_r = min(_DISCOUNT_MAX, ddm_g + _GAP)
        if ddm_r - ddm_g < _GAP:  # g 贴到上限时反过来压 g
            ddm_g = ddm_r - _GAP

    dcf_g = _clamp(dcf.get("growth"), _GROWTH_MIN, _GROWTH_MAX, 0.05)
    dcf_t = _clamp(dcf.get("terminal_growth"), _TERMINAL_MIN, _TERMINAL_MAX, 0.02)
    dcf_r = _clamp(dcf.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, 0.10)
    if dcf_r - dcf_g < _GAP:
        dcf_r = min(_DISCOUNT_MAX, dcf_g + _GAP)
    if dcf_r - dcf_t < _GAP:
        dcf_t = max(_TERMINAL_MIN, dcf_r - _GAP)

    def rationale(block: dict[str, Any], fallback: str) -> str:
        text = block.get("rationale")
        return str(text).strip() if isinstance(text, str) and text.strip() else fallback

    return {
        "ddm": {
            "growth": round(ddm_g, 4),
            "discount": round(ddm_r, 4),
            "rationale": rationale(ddm, "由模型按保守原则校准；详见参数依据。"),
        },
        "dcf": {
            "growth": round(dcf_g, 4),
            "discount": round(dcf_r, 4),
            "terminal_growth": round(dcf_t, 4),
            "rationale": rationale(dcf, "由模型按保守原则校准；详见参数依据。"),
        },
        "summary": str(raw.get("summary") or "").strip() if isinstance(raw.get("summary"), str) else "",
    }


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
    return {
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
        "summary": "模板回退：参数由历史净利增速推导，未经 LLM 结合行业与研报校准，仅供参考。",
    }


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
    research: list[dict[str, str]],
) -> dict[str, Any]:
    snapshot = result.get("snapshot")
    price = snapshot.price if snapshot else None
    dps = None
    fcf = None
    shares = None
    for item in result.get("valuation") or []:
        if item.method == "ddm":
            dps = (item.inputs or {}).get("dividend_per_share")
        elif item.method == "dcf":
            fcf = (item.inputs or {}).get("fcf")
            shares = (item.inputs or {}).get("shares")

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
        "model_inputs": {"dps": dps, "fcf": fcf, "shares": shares, "price": price},
        "research_reports": research,
    }


def _recompute(facts: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """用校准参数复用项目公式重算 DDM/DCF，并给出股息复投预期。"""

    inputs = facts.get("model_inputs") or {}
    price = inputs.get("price")
    dps = inputs.get("dps")
    fcf = inputs.get("fcf")
    shares = inputs.get("shares")

    d = params["ddm"]
    ddm = evaluate_ddm(dps, price, growth=d["growth"], discount=d["discount"])
    c = params["dcf"]
    dcf = evaluate_dcf(fcf, shares, price, growth=c["growth"], discount=c["discount"], terminal_growth=c["terminal_growth"])

    def to_dict(result: Any) -> dict[str, Any]:
        return {
            "fair_value_low": result.fair_value_low,
            "fair_value_high": result.fair_value_high,
            "margin_of_safety": result.margin_of_safety,
            "current_price": result.current_price,
        }

    return {
        "ddm": to_dict(ddm),
        "dcf": to_dict(dcf),
        "dividend_reinvestment": _dividend_reinvestment(dps, price, d["growth"]),
    }


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
            max_tokens=1400,
        )
        params = _normalize_params(data)
        return _assemble(facts, params, "llm")
    except (LlmError, LlmConfigError, FileNotFoundError):
        return _assemble(facts, fallback, "template")


def generate_valuation_params(
    symbol: str,
    db_path: Path | None = None,
    refresh: bool = False,
    research_limit: int = 4,
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
    research = load_research_digest(symbol, limit=research_limit, refresh=refresh)
    facts = _valuation_facts(result, financials, context, research)

    output = _finalize(facts)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    output["params_version"] = _VALUATION_PARAMS_VERSION
    _write_cache(cache_path, output)
    return output
