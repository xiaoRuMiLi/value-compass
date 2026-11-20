"""大师观点的中文解读：逐年财报摘要 + 大师档案 → LLM 叙述 + 模板回退 + 缓存。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat, has_live_llm
from fundamental.utils.intel import load_intel_digest
from fundamental.utils.prompts import load_prompt

from ._common import (
    _MASTERS_PROMPT_VERSION,
    _build_prompt,
    _masters_cache_path,
    _now,
    _read_cache,
    _write_cache,
)
from ._data import _load_master_context, _load_master_financials

_MASTER_LABELS = {
    "buffett": "巴菲特",
    "duan_yongping": "段永平",
}


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _intel_facts(intel: dict[str, Any] | None) -> dict[str, Any]:
    """把情报资料压成适合 prompt 的摘录，控制体量并明确使用边界。"""

    intel = intel or {}

    def brief(key: str, chars: int, limit: int = 2) -> list[dict[str, Any]]:
        return [
            {
                "date": item.get("date") or "",
                "org": item.get("org") or "",
                "title": item.get("title") or "",
                "excerpt": (item.get("content") or "")[:chars],
            }
            for item in (intel.get(key) or [])[:limit]
        ]

    return {
        "company_reports": brief("company_reports", 320),
        "industry_reports": brief("industry_reports", 320),
        "surveys": brief("surveys", 400),
        "note": "卖方研报与调研纪要代表第三方观点，非公司已实现事实；不得据此改写量化结论。",
    }


def _master_facts(
    result: dict[str, Any],
    financials: dict[str, Any],
    verdict: Any,
    context: dict[str, Any] | None = None,
    intel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    basics = context.get("company_basics") or {}
    stock = result.get("stock")
    snapshot = result.get("snapshot")
    score = result.get("score")
    moat = result.get("moat")
    cycle = result.get("cycle")
    review = result.get("review")

    master_name = str(_value(verdict, "master") or "")
    signals = [
        {
            "name": str(_value(signal, "name") or ""),
            "passed": bool(_value(signal, "passed")),
            "detail": str(_value(signal, "detail") or ""),
        }
        for signal in (_value(verdict, "signals") or [])
    ]
    valuation = [
        {
            "method": item.method,
            "fair_value_low": item.fair_value_low,
            "fair_value_high": item.fair_value_high,
            "margin_of_safety": item.margin_of_safety,
        }
        for item in (result.get("valuation") or [])
        if _value(item, "method") is not None
    ]
    return {
        "company": {
            "name": basics.get("name") or _value(stock, "name") or result.get("symbol") or "",
            "industry": basics.get("industry") or _value(stock, "industry") or "未披露",
            "sector": basics.get("sector") or _value(stock, "sector"),
            "business": basics.get("business"),
            "profile_summary": basics.get("profile_summary") or "",
            "list_date": basics.get("list_date"),
            "is_st": bool(basics.get("is_st")),
            "revenue_mix": basics.get("revenue_mix") or [],
            "price": _value(snapshot, "price"),
            "market_cap": _value(snapshot, "market_cap"),
            "score_total": _value(score, "total"),
            "score_rating": _value(score, "rating"),
        },
        "industry": context.get("industry"),
        "peer_comparison": context.get("peer_comparison"),
        "latest_report": context.get("latest_report"),
        "market_valuation": context.get("market_valuation"),
        "master": {
            "name": master_name,
            "label": _MASTER_LABELS.get(master_name, master_name),
            "score": _value(verdict, "score"),
            "verdict": _value(verdict, "verdict"),
            "signals": signals,
        },
        "financial_years": financials.get("years", []),
        "valuation": valuation,
        "moat": {
            "rating": _value(moat, "rating"),
            "evidence": _value(moat, "evidence") or [],
        },
        "cycle": {
            "position": _value(cycle, "position"),
            "evidence": _value(cycle, "evidence") or [],
        },
        "red_flags": [flag.description for flag in (result.get("red_flags") or [])],
        "review_summary": _value(review, "summary") or "",
        "intel": _intel_facts(intel),
    }


def _master_template(facts: dict[str, Any]) -> str:
    master = facts["master"]
    signals = master["signals"]
    passed = [signal["name"] for signal in signals if signal["passed"]]
    failed = [signal["name"] for signal in signals if not signal["passed"]]
    company = facts.get("company") or {}
    label = master["label"]
    company_name = company.get("name") or "该公司"

    parts = [
        f"按{label}框架，{company_name}在 {len(signals)} 项量化检验中有 {len(passed)} 项达标，"
        f"量化结论维持为“{master['verdict']}”。"
    ]
    if passed:
        parts.append("达标项：" + "、".join(passed) + "。")
    if failed:
        parts.append("未达标项：" + "、".join(failed) + "。")

    profile = company.get("profile_summary")
    if profile:
        parts.append(profile)
    industry = facts.get("industry")
    if industry and industry.get("summary"):
        parts.append("行业层面，" + industry["summary"])
    latest = facts.get("latest_report")
    if latest and latest.get("summary"):
        parts.append("最近一期" + latest["summary"])

    years = facts.get("financial_years") or []
    latest_annual = next((item for item in reversed(years) if item.get("annual")), None)
    if latest_annual:
        parts.append("最新年度财报摘要显示，" + latest_annual["summary"])
    elif years:
        parts.append("年度财报摘要显示，" + years[-1]["summary"])
    else:
        parts.append("财报摘要[数据缺失]，无法核对年度与季度经营变化。")

    peers = facts.get("peer_comparison")
    if peers and peers.get("summary"):
        parts.append(peers["summary"])
    market = facts.get("market_valuation")
    if market and market.get("summary"):
        parts.append(market["summary"])

    intel = facts.get("intel") or {}
    reports = intel.get("company_reports") or []
    if reports:
        first = reports[0]
        parts.append(
            f"近期卖方观点可参考{first.get('org') or '券商'}《{first.get('title') or '研报'}》，属第三方判断。"
        )
    surveys = intel.get("surveys") or []
    if surveys:
        parts.append(f"公开调研记录显示公司近期有{surveys[0].get('title') or '投资者关系活动'}，管理层表述仍需验证。")

    moat_rating = (facts.get("moat") or {}).get("rating")
    if moat_rating:
        parts.append(f"护城河评级为“{moat_rating}”。")
    red_flags = facts.get("red_flags") or []
    if red_flags:
        parts.append("风险红旗需留意：" + "；".join(red_flags[:3]) + "。")

    parts.append(
        "建议后续重点验证未达标项能否在年报与季报中改善，并持续核对现金流、负债与利润是否匹配；"
        "该解读为分析参考，不构成投资建议。"
    )
    return "".join(parts)


def _system_prompt(master_name: str) -> str:
    base = load_prompt("masters/_base")
    try:
        profile = load_prompt(f"masters/{master_name}")
    except FileNotFoundError:
        profile = ""
    return "\n\n---\n\n".join(part for part in (base, profile) if part)


def _master_finalize(facts: dict[str, Any]) -> tuple[str, str]:
    template = _master_template(facts)
    if not has_live_llm():
        return template, "template"
    try:
        system = _system_prompt(facts["master"]["name"])
        result = chat(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=700,
        )
        text = result.content.strip()
    except (LlmError, LlmConfigError, FileNotFoundError):
        text = ""
    if text:
        return text, "llm"
    return template, "template"


def generate_masters_narrative(
    symbol: str, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """生成每位大师的观点解读：优先 LLM，失败回退模板，带本地缓存。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _masters_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached and cached.get("masters_version") == _MASTERS_PROMPT_VERSION:
            return cached

    result = analyze_stock(symbol, db_path)
    if "error" in result:
        return {
            "symbol": symbol,
            "error": result["error"],
            "masters": [],
            "source": "template",
            "generated_at": _now(),
        }
    verdicts = result.get("masters") or []
    if not verdicts:
        return {
            "symbol": symbol,
            "error": "暂无大师观点",
            "masters": [],
            "source": "template",
            "generated_at": _now(),
        }

    financials = _load_master_financials(symbol, db_path)
    context = _load_master_context(symbol, db_path)
    intel = load_intel_digest(symbol, refresh=refresh)
    masters: list[dict[str, Any]] = []
    for verdict in verdicts:
        facts = _master_facts(result, financials, verdict, context, intel)
        text, source = _master_finalize(facts)
        masters.append(
            {
                "master": facts["master"]["name"],
                "label": facts["master"]["label"],
                "verdict": facts["master"]["verdict"],
                "score": facts["master"]["score"],
                "text": text,
                "source": source,
            }
        )
    output: dict[str, Any] = {
        "symbol": symbol,
        "masters": masters,
        "source": "llm" if any(item["source"] == "llm" for item in masters) else "template",
        "generated_at": _now(),
        "masters_version": _MASTERS_PROMPT_VERSION,
        "intel_count": sum(
            len(intel.get(key) or [])
            for key in ("company_reports", "industry_reports", "surveys")
        ),
    }
    _write_cache(cache_path, output)
    return output
