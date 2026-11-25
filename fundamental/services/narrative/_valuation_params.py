"""估值参数校准：本地基本面 + 联网研报 → LLM 给保守参数 → 复用公式重算 + 股息复投预期。

校准参数是 DDM/DCF 的唯一长参来源：生成后写入缓存，主估值链路（analyze_stock）读取
同一份参数重算，避免页面上方「固定默认」与下方「校准」出现两套互相矛盾的合理价。
analysis 层仍是纯函数，参数由 services 层注入，失败时回退到由历史增速推导的保守参数。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fundamental.analysis.valuation import evaluate_ddm, evaluate_dcf
from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat_json, has_live_llm
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
_VALUATION_PARAMS_VERSION = 5

_METHOD_LABELS = {"ddm": "股利折现", "dcf": "现金流折现"}
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
}


def _normalize_block(raw: Any, defaults: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """把一组 DDM/DCF 参数钳制到边界，并保证 discount - growth 有足够间距。"""

    raw = raw if isinstance(raw, dict) else {}
    ddm = raw.get("ddm") if isinstance(raw.get("ddm"), dict) else {}
    dcf = raw.get("dcf") if isinstance(raw.get("dcf"), dict) else {}
    base = defaults or _DEFAULTS
    dd = base["ddm"]
    dc = base["dcf"]

    ddm_g = _clamp(ddm.get("growth"), _GROWTH_MIN, _GROWTH_MAX, dd["growth"])
    ddm_r = _clamp(ddm.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, dd["discount"])
    if ddm_r - ddm_g < _GAP:
        ddm_r = min(_DISCOUNT_MAX, ddm_g + _GAP)
        if ddm_r - ddm_g < _GAP:  # g 贴到上限时反过来压 g
            ddm_g = ddm_r - _GAP

    dcf_g = _clamp(dcf.get("growth"), _GROWTH_MIN, _GROWTH_MAX, dc["growth"])
    dcf_t = _clamp(dcf.get("terminal_growth"), _TERMINAL_MIN, _TERMINAL_MAX, dc["terminal_growth"])
    dcf_r = _clamp(dcf.get("discount"), _DISCOUNT_MIN, _DISCOUNT_MAX, dc["discount"])
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
    }


def _derive_scenarios(base: dict[str, Any]) -> dict[str, Any]:
    """LLM 未给情景时，从基准参数派生乐观/悲观档（仍经边界钳制）。"""

    d, c = base["ddm"], base["dcf"]
    return {
        "optimistic": _normalize_block(
            {
                "ddm": {"growth": d["growth"] + 0.01, "discount": d["discount"] - 0.01,
                        "rationale": "假设主业景气回升、股东回报政策延续，增长上调、折现率下调；属情景推演，非基准预测。"},
                "dcf": {"growth": c["growth"] + 0.02, "discount": c["discount"] - 0.01,
                        "terminal_growth": c["terminal_growth"] + 0.01,
                        "rationale": "假设自由现金流改善、风险溢价回落，增长与永续上调；属情景推演，非基准预测。"},
            }
        ),
        "pessimistic": _normalize_block(
            {
                "ddm": {"growth": d["growth"] - 0.01, "discount": d["discount"] + 0.01,
                        "rationale": "假设增长放缓、股东回报不可持续，增长下调、折现率上调；属情景推演，非基准预测。"},
                "dcf": {"growth": c["growth"] - 0.02, "discount": c["discount"] + 0.01,
                        "terminal_growth": c["terminal_growth"] - 0.01,
                        "rationale": "假设竞争加剧、现金流质量走弱，增长与永续下调；属情景推演，非基准预测。"},
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
        if isinstance(block, dict) and (
            isinstance(block.get("ddm"), dict) or isinstance(block.get("dcf"), dict)
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
    settings = load_settings()
    intel_limit = int(settings.get("valuation.intel_limit", 3))
    industry_chars = int(settings.get("valuation.industry_chars", 1200))
    survey_chars = int(settings.get("valuation.survey_chars", 1500))
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
    for method in ("ddm", "dcf"):
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
    note = (
        "综合股利折现与现金流折现两种模型：区间取两者并集，中枢为两者中点均值。"
        if len(methods) == 2
        else f"仅基于{_METHOD_LABELS[methods[0]]}模型，另一模型因缺少必要输入未参与，合理价参考性有限。"
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
    fcf = inputs.get("fcf")
    shares = inputs.get("shares")

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
        block = {
            "ddm": to_dict(
                evaluate_ddm(dps, price, growth=d["growth"], discount=d["discount"]), "ddm"
            ),
            "dcf": to_dict(
                evaluate_dcf(
                    fcf, shares, price,
                    growth=c["growth"], discount=c["discount"],
                    terminal_growth=c["terminal_growth"],
                ),
                "dcf",
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
