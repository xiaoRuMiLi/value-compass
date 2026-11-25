"""财务趋势叙述：用年度/单季的利润与现金流数据生成长期趋势和近期波动解释。

LLM 只负责文字解释，事实由 financial_dashboard 服务提供；失败回退模板，不阻断页面。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.financial_dashboard import build_financial_dashboard
from fundamental.services.llm import chat, has_live_llm
from fundamental.utils.prompts import load_prompt

from ._common import (
    _build_prompt,
    _financial_trend_cache_path,
    _max_tokens,
    _now,
    _read_cache,
    _write_cache,
)


def _scaled(item: dict[str, Any] | None, index: int) -> float | None:
    if item is None or index >= len(item.get("values") or []):
        return None
    value = item["values"][index]
    if value is None:
        return None
    return round(value / (item.get("scale") or 1.0), 2)


def _series(dashboard: dict[str, Any], annual: bool) -> list[dict[str, Any]]:
    primary = {item["key"]: item for item in dashboard["primary"]}
    cashflow = {item["key"]: item for item in dashboard["statements"].get("cashflow", [])}
    rows: list[dict[str, Any]] = []
    for index, label in enumerate(dashboard["labels"]):
        row: dict[str, Any] = {
            "period": label[:4] if annual else label,
            "revenue_yi": _scaled(primary.get("revenue"), index),
            "net_profit_yi": _scaled(primary.get("net_profit"), index),
            "ocf_yi": _scaled(cashflow.get("NETCASH_OPERATE"), index),
            "net_margin": _scaled(primary.get("net_margin"), index),
            "roe": _scaled(primary.get("roe"), index),
        }
        if annual:
            row.update(
                {
                    "invest_cashflow_yi": _scaled(cashflow.get("NETCASH_INVEST"), index),
                    "finance_cashflow_yi": _scaled(cashflow.get("NETCASH_FINANCE"), index),
                }
            )
        rows.append(row)
    return rows[-10:] if annual else rows[-12:]


def _build_facts(symbol: str) -> dict[str, Any]:
    annual = build_financial_dashboard(symbol, "annual")
    quarterly = build_financial_dashboard(symbol, "quarter")
    return {
        "symbol": symbol,
        "annual": _series(annual, annual=True),
        "quarter": _series(quarterly, annual=False),
        "notes": annual.get("notes", [])[:2],
    }


def _first_last(rows: list[dict[str, Any]], key: str) -> tuple[Any, Any]:
    values = [(row["period"], row.get(key)) for row in rows if row.get(key) is not None]
    if not values:
        return None, None
    return values[0], values[-1]


def _template(facts: dict[str, Any]) -> str:
    annual = facts["annual"]
    quarterly = facts["quarter"]
    paragraphs: list[str] = []

    if annual:
        first = annual[0]
        last = annual[-1]
        lines = [
            f"长期趋势：{first['period']}—{last['period']}，"
            f"营业收入由 {first.get('revenue_yi')} 亿元变为 {last.get('revenue_yi')} 亿元，"
            f"归母净利润由 {first.get('net_profit_yi')} 亿元变为 {last.get('net_profit_yi')} 亿元。"
        ]
        revenue_start, revenue_end = _first_last(annual, "revenue_yi")
        profit_start, profit_end = _first_last(annual, "net_profit_yi")
        if revenue_start and revenue_end and revenue_start[1] and revenue_end[1]:
            lines.append(
                f"营业收入累计变化 {(revenue_end[1] / revenue_start[1] - 1) * 100:.1f}%，"
                f"归母净利润累计变化 "
                f"{'数据不足' if not profit_start or not profit_start[1] or not profit_end else f'{(profit_end[1] / profit_start[1] - 1) * 100:.1f}%'}。"
            )
        ocf_start, ocf_end = _first_last(annual, "ocf_yi")
        if ocf_start and ocf_end:
            lines.append(
                f"经营活动现金流量净额由 {ocf_start[1]} 亿元变为 {ocf_end[1]} 亿元，"
                "可用于判断利润增长是否有现金流配合。"
            )
        paragraphs.append("".join(lines))
    else:
        paragraphs.append("长期趋势：暂无足够的年度财务数据。")

    recent = quarterly[-4:]
    if recent:
        lines = [
            "近期波动：" + "、".join(
                f"{row['period']} 归母净利润 {row.get('net_profit_yi')} 亿元、经营现金流 {row.get('ocf_yi')} 亿元"
                for row in recent
            )
            + "。"
        ]
        lines.append("单季之间的利润与现金流差异，可作为近期经营波动的观察依据。")
        paragraphs.append("".join(lines))
    else:
        paragraphs.append("近期波动：暂无足够的单季财务数据。")

    last = annual[-1] if annual else None
    focus = []
    if last and last.get("ocf_yi") is not None and last.get("net_profit_yi"):
        ratio = last["ocf_yi"] / last["net_profit_yi"]
        focus.append(f"最近年度经营现金流/归母净利润为 {ratio:.2f}，偏离 1 越大越需要核对利润质量。")
    focus.append("以上仅为数据层面的趋势描述，不构成投资建议。")
    paragraphs.append("值得关注：" + "".join(focus))
    return "\n\n".join(paragraphs)


def _finalize(facts: dict[str, Any]) -> dict[str, Any]:
    template = _template(facts)
    if not has_live_llm():
        return {"text": template, "source": "template"}
    try:
        system = load_prompt("financial_trend_narrative")
        result = chat(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=_max_tokens("financial_trend", 1600),
        )
        text = result.content.strip()
    except (LlmError, LlmConfigError):
        text = ""
    return {"text": text, "source": "llm"} if text else {"text": template, "source": "template"}


def generate_financial_trend_narrative(
    symbol: str, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """生成现金流量与利润的趋势叙述；LLM 优先、模板回退，带本地缓存。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _financial_trend_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached:
            return cached

    facts = _build_facts(symbol)
    if not facts["annual"] and not facts["quarter"]:
        return {
            "symbol": symbol,
            "text": "暂无足够的财务数据生成趋势叙述，请先同步财报数据。",
            "source": "template",
            "generated_at": _now(),
        }
    output = _finalize(facts)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    _write_cache(cache_path, output)
    return output
