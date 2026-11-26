"""个股概览叙述：事实构建、模板渲染、LLM 增强与缓存。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat, chat_json, has_live_llm
from fundamental.utils.prompts import load_prompt

from ._common import (
    _build_prompt,
    _fmt_pct,
    _fmt_price,
    _fmt_ratio,
    _fmt_yi,
    _max_tokens,
    _now,
    _overview_cache_path,
    _plain,
    _read_cache,
    _write_cache,
)
from ._data import _load_extra


def _build_facts(result: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    stock = result.get("stock")
    snapshot = result.get("snapshot")
    score = result.get("score")
    moat = result.get("moat")
    cycle = result.get("cycle")
    fraud = result.get("fraud")
    delisting = result.get("delisting")
    review = result.get("review")

    dimensions = [
        {"name": d.dimension, "score": d.score, "weight": d.weight}
        for d in (score.dimensions if score else [])
    ]
    valuation = [
        {
            "method": v.method,
            "fair_value_low": v.fair_value_low,
            "fair_value_high": v.fair_value_high,
            "margin_of_safety": v.margin_of_safety,
        }
        for v in (result.get("valuation") or [])
    ]
    red_flags = [
        {
            "type": _plain(r.flag_type),
            "severity": _plain(r.severity),
            "description": r.description,
        }
        for r in (result.get("red_flags") or [])
    ]
    masters = [
        {"master": m.master, "verdict": m.verdict, "score": m.score}
        for m in (result.get("masters") or [])
    ]

    facts: dict[str, Any] = {
        "name": stock.name if stock else "",
        "industry": (stock.industry if stock else None) or "未披露",
        "sector": (stock.sector if stock else None) or "未披露",
        "business": extra.get("business"),
        "official_website": extra.get("official_website"),
        "price": snapshot.price if snapshot else None,
        "market_cap": snapshot.market_cap if snapshot else None,
        "score_total": score.total if score else None,
        "score_rating": score.rating if score else None,
        "dimensions": dimensions,
        "moat": {"rating": moat.rating if moat else None, "evidence": moat.evidence if moat else []},
        "cycle": {
            "position": cycle.position if cycle else None,
            "evidence": cycle.evidence if cycle else [],
        },
        "fraud": {"score": fraud.score if fraud else None, "signals": fraud.signals if fraud else []},
        "delisting": {
            "score": delisting.score if delisting else None,
            "triggers": delisting.triggers if delisting else [],
        },
        "valuation": valuation,
        "review_summary": review.summary if review else "",
        "integrity_summary": review.integrity_summary if review else "",
        "red_flags": red_flags,
        "masters": masters,
        "latest_financials": extra.get("latest_financials"),
        "revenue_region": extra.get("revenue_region", []),
        "revenue_products": extra.get("revenue_products", []),
        "insider_trades": extra.get("insider_trades", []),
        "pledge_count": extra.get("pledge_count", 0),
        "integrity_records": extra.get("integrity_records", []),
        "announcements": extra.get("announcements", []),
        "litigations": extra.get("litigations", []),
        "sentiment": extra.get("sentiment"),
        "research_reports": extra.get("research_reports", []),
        "capital_flows": extra.get("capital_flows", []),
    }
    facts["sources"] = _collect_sources(facts)
    return facts


def _collect_sources(facts: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    if facts.get("latest_financials"):
        sources.append("财务报表")
    if facts.get("revenue_region") or facts.get("revenue_products"):
        sources.append("主营构成")
    if facts.get("valuation"):
        sources.append("历史估值")
    if facts.get("price") is not None:
        sources.append("实时行情")
    if facts.get("integrity_records"):
        sources.append("诚信记录")
    if facts.get("red_flags"):
        sources.append("风险红旗")
    if facts.get("announcements"):
        sources.append("公司公告")
    if facts.get("litigations"):
        sources.append("诉讼仲裁")
    if facts.get("sentiment"):
        sources.append("股吧情绪")
    if facts.get("research_reports"):
        sources.append("研报评级")
    if facts.get("capital_flows"):
        sources.append("资金流向")
    if facts.get("insider_trades"):
        sources.append("增减持")
    if facts.get("pledge_count") is not None:
        sources.append("股权质押")
    if facts.get("masters"):
        sources.append("大师模型")
    return sources


def _render_template(facts: dict[str, Any]) -> list[dict[str, str]]:
    fin = facts.get("latest_financials") or {}
    name = facts.get("name") or "该股票"
    sections: list[dict[str, str]] = []

    region = "、".join(f"{r['item']} {_fmt_pct(r['ratio'])}" for r in facts.get("revenue_region", [])[:3])
    products = "、".join(
        f"{p['item']} {_fmt_pct(p['ratio'])}" for p in facts.get("revenue_products", [])[:5]
    )
    business = facts.get("business")
    if business:
        biz = business
    elif products:
        biz = f"主营产品为 {products}"
    else:
        biz = "主营业务[数据缺失]"
    basic_segments = [f"{name}的业务可概括为：{biz}"]
    if region:
        basic_segments.append(f"收入地域分布为 {region}")
    basic_segments.append(
        f"最新报告期（{fin.get('report_date') or '数据缺失'}）实现营业收入 {_fmt_yi(fin.get('revenue'))}、"
        f"归母净利润 {_fmt_yi(fin.get('net_profit'))}，ROE {_fmt_pct(fin.get('roe'))}、"
        f"毛利率 {_fmt_pct(fin.get('gross_margin'))}、净利率 {_fmt_pct(fin.get('net_margin'))}"
    )
    score = facts.get("score_total")
    if score is not None:
        dims = "；".join(
            f"{d['name']} {d['score']:.0f} 分（权重 {d['weight']}%）"
            for d in facts.get("dimensions", [])
        )
        basic_segments.append(f"综合财务评分 {score:.1f} 分，评级“{facts.get('score_rating')}”（{dims}）")
    sections.append({"title": "企业基本信息", "content": "；".join(basic_segments) + "。"})

    moat = facts.get("moat") or {}
    moat_evidence = "；".join(moat.get("evidence") or []) or "缺少更多定量证据"
    moat_reason = (
        f"长期维持 {_fmt_pct(fin.get('gross_margin'))} 的毛利率与 {_fmt_pct(fin.get('roe'))} 的 ROE，"
        "说明公司具备稳定的定价权或成本优势，这种超额盈利能跨周期持续，通常源于品牌、稀缺性、"
        "转换成本或牌照等壁垒；若仅是行业景气驱动，高毛利会随周期回落"
    )
    sections.append(
        {
            "title": "护城河",
            "content": (
                f"护城河评级为“{moat.get('rating') or '未知'}”。判断的关键不是毛利率本身，"
                f"而是它为何能持续：{moat_reason}（{moat_evidence}）。"
            ),
        }
    )

    cycle = facts.get("cycle") or {}
    cycle_evidence = "；".join(cycle.get("evidence") or []) or "[数据缺失]"
    cycle_segments = [
        f"当前周期判断为“{cycle.get('position') or '无法判断'}”，证据是 {cycle_evidence}",
        f"最新报告期营收同比 {_fmt_pct(fin.get('revenue_yoy'))}、净利同比 {_fmt_pct(fin.get('net_profit_yoy'))}",
    ]
    capital = facts.get("capital_flows") or []
    if capital:
        cycle_segments.append(f"最新交易日主力资金净额 {_fmt_yi(capital[0].get('main_net'))}")
    sections.append({"title": "经营与周期", "content": "；".join(cycle_segments) + "。"})

    if facts.get("valuation"):
        method_names = {
            "relative": "相对估值", "graham": "格雷厄姆", "ddm": "股利折现",
            "dcf": "现金流折现", "adjusted_fcf": "自由现金流折现",
        }
        items = "；".join(
            f"{method_names.get(v['method'], v['method'])} {_fmt_price(v['fair_value_low'])}~"
            f"{_fmt_price(v['fair_value_high'])}（安全边际 {_fmt_ratio(v['margin_of_safety'])}）"
            for v in facts["valuation"]
        )
        price = facts.get("price")
        price_clause = f"以现价 {_fmt_price(price)} 衡量，" if price is not None else ""
        valuation_content = (
            f"各方法合理区间为：{items}。{price_clause}不同口径对成长、分红与资产价值的假设不同，"
            "区间分歧较大，安全边际需要结合公司所处阶段与盈利质量综合判断。"
        )
    else:
        valuation_content = "估值数据[数据缺失]，暂无法给出合理区间与安全边际。"
    sections.append({"title": "估值与安全边际", "content": valuation_content})

    fraud = facts.get("fraud") or {}
    delisting = facts.get("delisting") or {}
    fraud_signals = "；".join(fraud.get("signals") or []) or "未发现明显异常信号"
    delist_triggers = "；".join(delisting.get("triggers") or []) or "未触发面值、市值或持续亏损等条件"
    integrity = facts.get("integrity_records") or []
    if integrity:
        samples = "；".join(
            f"{r.get('title')}（{r.get('category') or '未分类'}，{r.get('severity') or '未知'}"
            f"{'，' + r['description'] if r.get('description') else ''}）"
            for r in integrity[:5]
        )
        integrity_text = f"诚信记录 {len(integrity)} 条，例如：{samples}"
    else:
        integrity_text = facts.get("integrity_summary") or "诚信记录[数据缺失]"
    red_flags = "；".join(r["description"] for r in facts.get("red_flags", [])) or "暂无突出风险红旗"
    insiders = facts.get("insider_trades") or []
    if insiders:
        buys = sum(1 for i in insiders if str(i.get("direction") or "").lower() in ("buy", "增持"))
        sells = sum(1 for i in insiders if str(i.get("direction") or "").lower() in ("sell", "减持"))
        trend = "偏积极" if buys >= sells else "偏谨慎"
        insider_text = (
            f"近期披露高管/股东增减持共 {len(insiders)} 条（增持 {buys} 条、减持 {sells} 条），"
            f"内部人行为整体{trend}"
        )
    else:
        insider_text = "暂无高管增减持记录"
    litigations = facts.get("litigations") or []
    lit_text = "；".join(l["title"] for l in litigations[:3]) if litigations else "暂无诉讼仲裁记录"
    pledge = facts.get("pledge_count")
    risk_segments = [
        f"造假风险评分 {fraud.get('score') if fraud.get('score') is not None else '数据缺失'} 分，信号为“{fraud_signals}”",
        f"退市风险评分 {delisting.get('score') if delisting.get('score') is not None else '数据缺失'} 分，{delist_triggers}",
        f"治理与诚信：{integrity_text}；风险红旗“{red_flags}”；{insider_text}；股权质押 {pledge if pledge is not None else '数据缺失'} 笔",
        f"诉讼仲裁：{lit_text}",
    ]
    sections.append({"title": "风险与治理", "content": "；".join(risk_segments) + "。"})

    return sections


def _parse_sections(data: Any) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("sections")
    if not isinstance(raw, list):
        return []
    sections: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if title and content:
            sections.append({"title": title, "content": content})
    return sections if len(sections) >= 2 else []


def _finalize(facts: dict[str, Any]) -> dict[str, Any]:
    template = _render_template(facts)
    if not has_live_llm():
        return {"sections": template, "source": "template"}
    try:
        system = load_prompt("overview_narrative")
        data = chat_json(
            [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
            max_tokens=_max_tokens("overview", 2000),
        )
        sections = _parse_sections(data)
    except (LlmError, LlmConfigError):
        sections = []
    if sections:
        return {"sections": sections, "source": "llm"}
    return {"sections": template, "source": "template"}


def generate_overview_narrative(
    symbol: str, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """生成个股概览叙述：优先缓存与 LLM，失败或未配置时回退模板。"""

    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _overview_cache_path(symbol)
    if not refresh:
        cached = _read_cache(cache_path, ttl_days)
        if cached:
            return cached

    result = analyze_stock(symbol, db_path)
    if "error" in result:
        return {
            "symbol": symbol,
            "error": result["error"],
            "sections": [],
            "sources": [],
            "source": "template",
            "generated_at": _now(),
        }
    facts = _build_facts(result, _load_extra(symbol, db_path))
    output = _finalize(facts)
    output["symbol"] = symbol
    output["sources"] = facts.get("sources", [])
    output["generated_at"] = _now()
    _write_cache(cache_path, output)
    return output
