"""大师观点的中文解读：逐年财报摘要 + 大师档案 → LLM 叙述 + 模板回退 + 缓存。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat, has_live_llm
from fundamental.utils.prompts import load_prompt

from ._common import (
    _build_prompt,
    _masters_cache_path,
    _now,
    _read_cache,
    _write_cache,
)
from ._data import _load_master_financials

_MASTER_LABELS = {
    "buffett": "巴菲特",
    "duan_yongping": "段永平",
}


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _master_facts(result: dict[str, Any], financials: dict[str, Any], verdict: Any) -> dict[str, Any]:
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
            "name": _value(stock, "name") or result.get("symbol") or "",
            "industry": _value(stock, "industry") or "未披露",
            "price": _value(snapshot, "price"),
            "market_cap": _value(snapshot, "market_cap"),
            "score_total": _value(score, "total"),
            "score_rating": _value(score, "rating"),
        },
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

    years = facts.get("financial_years") or []
    if years:
        parts.append("最新年度财报摘要显示，" + years[-1]["summary"])
    else:
        parts.append("财报摘要[数据缺失]，无法核对年度与季度经营变化。")

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
        if cached:
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
    masters: list[dict[str, Any]] = []
    for verdict in verdicts:
        facts = _master_facts(result, financials, verdict)
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
    }
    _write_cache(cache_path, output)
    return output
