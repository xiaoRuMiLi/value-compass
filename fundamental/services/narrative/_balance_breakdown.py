"""资产负债表拆解叙述：按科目组让 LLM 做简短解读，数字全部由代码提供。

LLM 只写文字、不碰数字；失败回退模板，不阻断页面。沿用缓存/refresh 范式。
"""

from __future__ import annotations

import json
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.llm import chat, has_live_llm
from fundamental.utils.prompts import load_prompt

from ._common import (
    _balance_breakdown_cache_path,
    _max_tokens,
    _now,
    _read_cache,
    _write_cache,
)


def _yi(value: float | None) -> float | None:
    """拆解数据为元，转亿元。"""
    return round(value / 1e8, 2) if value is not None else None


def _build_facts(symbol: str) -> dict[str, Any]:
    # 延迟导入，避免与 services.balance_breakdown 形成模块级循环导入
    from fundamental.services.balance_breakdown import build_balance_view

    view = build_balance_view(symbol)
    labels = view["labels"]
    latest = labels[-1] if labels else ""

    totals = {t["name"]: t for t in view["totals"]}
    assets = _yi(totals.get("总资产", {}).get("latest"))

    groups: list[dict[str, Any]] = []
    for cls in view["investment_classes"]:
        groups.append(
            {
                "组名": cls["name"],
                "最新金额亿元": _yi(cls["latest"]),
                "占总资产%": cls["share_of_assets"],
                "同比%": cls["yoy"],
            }
        )

    fcf = view["fcf_history"]
    fcf_recent = [
        {"期": labels[i], "自由现金流亿元": fcf["values"][i]}
        for i in range(max(0, len(labels) - 8), len(labels))
        if i < len(fcf["values"]) and fcf["values"][i] is not None
    ]

    facts: dict[str, Any] = {
        "标的": symbol,
        "币种": view["currency"],
        "报告期": latest,
        "总资产亿元": assets,
        "投资资产结构": groups,
        "有息负债": [
            {
                "科目": it["name"],
                "最新亿元": _yi(it["latest"]),
                "同比%": it["yoy"],
            }
            for it in view["sections"]["liabilities_current"]["items"]
            + view["sections"]["liabilities_noncurrent"]["items"]
            if it["invest_class"] == "interest_bearing" and it["latest"] is not None
        ],
        "现金及存款": [
            {"科目": it["name"], "最新亿元": _yi(it["latest"])}
            for it in view["sections"]["assets_current"]["items"]
            if it["subgroup"] == "现金及存款" and it["latest"] is not None
        ],
        "历史自由现金流": fcf_recent,
        "财报原文摘录": [
            {"类别": e["category"], "来源": e["source"], "片段": e["text"]}
            for e in view["associate_evidence"]
        ],
    }
    return facts


def _template(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(
        f"截至 {facts['报告期']}，总资产 {facts['总资产亿元']} 亿元（{facts['币种']}）。"
        if facts.get("总资产亿元") else "暂无可解读的资产负债表数据。"
    )
    for g in facts["投资资产结构"]:
        amount = g["最新金额亿元"]
        share = g["占总资产%"]
        parts.append(
            f"【{g['组名']}】最新 {amount} 亿元，占总资产 {share}%；"
            "结构变化需结合公司投资战略与财报附注判断。"
        )
    parts.append("以上为数据层面的描述，不构成投资建议。")
    return "\n\n".join(parts)


def _finalize(facts: dict[str, Any]) -> dict[str, Any]:
    template = _template(facts)
    if not has_live_llm():
        return {"text": template, "source": "template"}
    system = load_prompt("balance_breakdown_narrative")
    payload = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
    try:
        result = chat(
            [ChatMessage("system", system), ChatMessage("user", payload)],
            max_tokens=_max_tokens("balance_breakdown", 1800),
        )
        text = result.content.strip()
    except (LlmError, LlmConfigError):
        text = ""
    return {"text": text, "source": "llm"} if text else {"text": template, "source": "template"}


def generate_balance_breakdown_narrative(
    symbol: str, refresh: bool = False
) -> dict[str, Any]:
    """生成资产负债表分组解读；LLM 优先、模板回退，带本地缓存。"""
    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _balance_breakdown_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached:
            return cached

    facts = _build_facts(symbol)
    if not facts.get("总资产亿元"):
        return {
            "symbol": symbol,
            "text": "暂无足够的资产负债表数据生成解读，请先同步财报数据。",
            "source": "template",
            "generated_at": _now(),
        }
    output = _finalize(facts)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    _write_cache(cache_path, output)
    return output
