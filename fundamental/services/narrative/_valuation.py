"""估值模型的中文批判性解读：事实构建、模板渲染、LLM 增强与缓存。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat_json, has_live_llm
from fundamental.utils.prompts import load_prompt

from ._common import (
    _build_prompt,
    _fmt_pct,
    _fmt_price,
    _fmt_ratio,
    _max_tokens,
    _now,
    _read_cache,
    _valuation_cache_path,
    _write_cache,
)
from ._data import _load_score_financials

_VALUATION_METHOD_TITLES = {
    "relative": "相对估值",
    "graham": "格雷厄姆",
    "ddm": "股利折现",
    "dcf": "现金流折现",
    "adjusted_fcf": "自由现金流折现",
    "fcff": "企业自由现金流折现",
}


def _valuation_facts(result: dict[str, Any], financials: dict[str, Any]) -> dict[str, Any]:
    basis_map = {
        "relative": "历史PE分位与中位数来自公司自身PE序列；EPS来自最新财报",
        "graham": "EPS与BPS来自最新财报；22.5 为格雷厄姆固定系数",
        "ddm": "每股股利来自最新分红；增长率5%与折现率8%为模型默认假设，未按公司基本面校准",
        "dcf": "自由现金流与股本来自财报；增长率10%、折现率9%、永续3%为模型默认假设，未按公司基本面校准",
        "adjusted_fcf": (
            "期初FCF按经营现金流−资本开支+利息收入−利息支出，A股缺失分项按0并已在计算器中标注可补填；"
            "增长率4%、折现率10%、永续2%为模型默认假设；营运资本差额仅作现金流质量提示，不进入公式"
        ),
        "fcff": (
            "FCFF=经营现金流+实付利息−资本开支，按 WACC 折现得企业价值；"
            "桥接项取年报资产负债表（有息负债、货币资金、非经营投资、少数股东权益），"
            "超额现金留足营收2%、非经营投资按8折；WACC由财报与研报校准（库内暂无Beta，非计算值），"
            "但以市场无风险利率为下限：A股取中国10Y国债、其它取美国10Y国债，"
            "债务成本Kd=Rf+信用利差；取值与来源在卡片锚点区列出"
        ),
    }
    valuation: list[dict[str, Any]] = []
    for item in result.get("valuation") or []:
        assumptions = dict(item.assumptions or {})
        formula = assumptions.pop("formula", None)
        valuation.append(
            {
                "method": item.method,
                "formula": formula,
                "fair_value_low": item.fair_value_low,
                "fair_value_high": item.fair_value_high,
                "margin_of_safety": item.margin_of_safety,
                "basis": basis_map.get(item.method, ""),
                "assumptions": assumptions,
                "inputs": dict(item.inputs or {}),
            }
        )
    snapshot = result.get("snapshot")
    return {
        "price": snapshot.price if snapshot else None,
        "valuation": valuation,
        "annual_growth": financials.get("annual_growth", []),
        "annual_quality": financials.get("annual_quality", []),
        "dividend_history": financials.get("dividend_history", []),
    }


def _valuation_template(facts: dict[str, Any]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    for item in facts["valuation"]:
        title = _VALUATION_METHOD_TITLES.get(item["method"], item["method"])
        if item["fair_value_low"] is None:
            content = "数据不足，无法给出合理区间。"
        else:
            margin = item["margin_of_safety"]
            content = (
                f"合理区间 {_fmt_price(item['fair_value_low'])}~{_fmt_price(item['fair_value_high'])}，"
                f"安全边际 {_fmt_ratio(margin) if margin is not None else '数据缺失'}。"
                f"方法：{item['formula'] or '公式缺失'}。"
            )
        sections.append({"method": item["method"], "title": title, "content": content})
    overall_parts: list[str] = []
    growth = facts.get("annual_growth") or []
    if growth:
        seq = "、".join(f"{g['year']}年净利同比{_fmt_pct(g.get('net_profit_yoy'))}" for g in growth)
        overall_parts.append(f"历年净利同比：{seq}")
    dividends = facts.get("dividend_history") or []
    if dividends:
        seq = "、".join(f"{d['year']}年每股派息 {d.get('cash_per_share')}" for d in dividends)
        overall_parts.append(f"分红历史：{seq}")
    overall = "；".join(overall_parts) if overall_parts else "暂无更多基本面佐证。"
    sections.append({"method": "overall", "title": "综合判断", "content": overall})
    return sections


def _parse_valuation_sections(data: Any) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("sections")
    if not isinstance(raw, list):
        return []
    sections: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").strip()
        content = str(item.get("content") or "").strip()
        if not method or not content:
            continue
        title = "综合判断" if method == "overall" else _VALUATION_METHOD_TITLES.get(method, method)
        sections.append({"method": method, "title": title, "content": content})
    return sections if len(sections) >= 2 else []


def _valuation_finalize(facts: dict[str, Any]) -> dict[str, Any]:
    template = _valuation_template(facts)
    if not has_live_llm():
        return {"sections": template, "source": "template"}
    try:
        system = load_prompt("valuation_narrative")
        data = chat_json(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=_max_tokens("valuation", 1600),
        )
        sections = _parse_valuation_sections(data)
    except (LlmError, LlmConfigError):
        sections = []
    if sections:
        return {"sections": sections, "source": "llm"}
    return {"sections": template, "source": "template"}


def generate_valuation_narrative(
    symbol: str, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """生成估值模型的中文批判性解读：优先 LLM，失败回退模板，带本地缓存。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _valuation_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached:
            return cached

    result = analyze_stock(symbol, db_path)
    if "error" in result or not result.get("valuation"):
        return {
            "symbol": symbol,
            "error": result.get("error") or "暂无估值数据",
            "sections": [],
            "source": "template",
            "generated_at": _now(),
        }
    facts = _valuation_facts(result, _load_score_financials(symbol, db_path))
    output = _valuation_finalize(facts)
    output["symbol"] = symbol
    output["generated_at"] = _now()
    _write_cache(cache_path, output)
    return output
