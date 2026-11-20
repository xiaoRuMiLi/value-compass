"""五维财务评分的中文解读：事实构建、模板渲染、LLM 增强与缓存。"""

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
    _fmt_pct,
    _max_tokens,
    _now,
    _read_cache,
    _score_cache_path,
    _write_cache,
)
from ._data import _load_score_financials


def _score_facts(score: Any, financials: dict[str, Any] | None = None) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "total": score.total,
        "rating": score.rating,
        "dimensions": [
            {"name": d.dimension, "score": d.score, "weight": d.weight, "detail": d.detail}
            for d in score.dimensions
        ],
    }
    if financials:
        facts["annual_growth"] = financials.get("annual_growth", [])
        facts["annual_quality"] = financials.get("annual_quality", [])
    return facts


def _score_template(facts: dict[str, Any]) -> str:
    dims = facts["dimensions"]
    ranked = sorted(dims, key=lambda d: d["score"], reverse=True)
    strongest, weakest = ranked[0], ranked[-1]
    lines = [
        f"综合财务评分 {facts['total']} 分，评级“{facts['rating']}”。",
        f"最强维度是{strongest['name']}（{strongest['score']:.0f} 分，权重 {strongest['weight']:.0f}%），"
        f"最弱维度是{weakest['name']}（{weakest['score']:.0f} 分，权重 {weakest['weight']:.0f}%）。",
    ]
    growth = facts.get("annual_growth") or []
    if growth:
        seq = "、".join(f"{g['year']}年净利同比{_fmt_pct(g.get('net_profit_yoy'))}" for g in growth)
        lines.append(f"历年净利同比增速：{seq}。")
    quality = facts.get("annual_quality") or []
    if quality:
        latest = quality[-1]
        ocf_ratio = latest.get("ocf_to_profit")
        if ocf_ratio is not None:
            lines.append(f"最近年度经营现金流/净利润 = {ocf_ratio:.2f}。")
        else:
            lines.append("最近年度经营现金流/净利润 = 数据缺失。")
        lines.append(f"最近年度一次性收益占比 {_fmt_pct(latest.get('oneoff_ratio'))}。")
    for dimension in dims:
        flat = (dimension.get("detail") or "").replace("\n", "；")
        lines.append(f"{dimension['name']}：{flat}。")
    return "\n".join(lines)


def _score_finalize(facts: dict[str, Any]) -> dict[str, Any]:
    template = _score_template(facts)
    if not has_live_llm():
        return {"text": template, "source": "template"}
    try:
        system = load_prompt("score_narrative")
        result = chat(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=_max_tokens("score", 1200),
        )
        text = result.content.strip()
    except (LlmError, LlmConfigError):
        text = ""
    if text:
        return {"text": text, "source": "llm"}
    return {"text": template, "source": "template"}


def generate_score_narrative(
    symbol: str, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """生成财务五维评分的中文解读：优先 LLM，失败回退模板，带本地缓存。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _score_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached:
            return cached

    result = analyze_stock(symbol, db_path)
    if "error" in result or result.get("score") is None:
        return {
            "symbol": symbol,
            "error": result.get("error") or "暂无财务评分",
            "text": "",
            "source": "template",
            "generated_at": _now(),
        }
    facts = _score_facts(result["score"], _load_score_financials(symbol, db_path))
    output = _score_finalize(facts)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    _write_cache(cache_path, output)
    return output
