"""把结构化分析结果渲染成分段可读叙述：LLM 增强 + 模板回退 + 本地缓存。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.analyze import analyze_stock
from fundamental.services.llm import chat, chat_json, has_live_llm
from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.prompts import load_prompt
from fundamental.utils.symbols import parse_symbol

_SECTION_TITLES = ("企业基本信息", "护城河", "经营与周期", "估值与安全边际", "风险与治理")
_NARRATIVE_VERSION = 3

_VALUATION_METHOD_TITLES = {
    "relative": "相对估值",
    "graham": "格雷厄姆",
    "ddm": "股利折现",
    "dcf": "现金流折现",
}


def _plain(value: Any) -> Any:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else value


def _fmt_yi(value: Any) -> str:
    return "数据缺失" if value is None else f"{value / 1e8:.2f} 亿元"


def _fmt_pct(value: Any) -> str:
    return "数据缺失" if value is None else f"{value:.2f}%"


def _fmt_ratio(value: Any) -> str:
    return "数据缺失" if value is None else f"{value * 100:.2f}%"


def _fmt_price(value: Any) -> str:
    return "数据缺失" if value is None else f"{value:.2f} 元"


def _load_extra_rows(conn: Any, stock_id: int) -> dict[str, Any]:
    profile = repository.fetch_one(
        conn,
        "SELECT business, official_website FROM company_profile WHERE stock_id = ?",
        (stock_id,),
    )
    latest = repository.fetch_one(
        conn,
        "SELECT report_date, revenue, net_profit, roe, gross_margin, net_margin, "
        "revenue_yoy, net_profit_yoy, debt_ratio FROM financials "
        "WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1",
        (stock_id,),
    )
    regions = repository.fetch_all(
        conn,
        "SELECT item_name, ratio FROM revenue_composition rc "
        "WHERE rc.stock_id = ? AND rc.dimension = 'region' AND rc.report_date = "
        "(SELECT MAX(report_date) FROM revenue_composition r2 "
        "WHERE r2.stock_id = rc.stock_id AND r2.dimension = rc.dimension) "
        "ORDER BY rc.ratio DESC LIMIT 4",
        (stock_id,),
    )
    products = repository.fetch_all(
        conn,
        "SELECT item_name, ratio FROM revenue_composition rc "
        "WHERE rc.stock_id = ? AND rc.dimension = 'product' AND rc.report_date = "
        "(SELECT MAX(report_date) FROM revenue_composition r2 "
        "WHERE r2.stock_id = rc.stock_id AND r2.dimension = rc.dimension) "
        "ORDER BY rc.ratio DESC LIMIT 8",
        (stock_id,),
    )
    insiders = repository.fetch_all(
        conn,
        "SELECT trade_date, insider_name, position, direction, change_ratio FROM insider_trades "
        "WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 8",
        (stock_id,),
    )
    pledge = repository.fetch_one(
        conn, "SELECT COUNT(*) AS n FROM pledges WHERE stock_id = ?", (stock_id,)
    )
    integrity = repository.fetch_all(
        conn,
        "SELECT record_date, category, title, description, severity FROM integrity_records "
        "WHERE stock_id = ? ORDER BY record_date DESC LIMIT 10",
        (stock_id,),
    )
    announcements = repository.fetch_all(
        conn,
        "SELECT publish_date, category, title FROM announcements "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 6",
        (stock_id,),
    )
    litigations = repository.fetch_all(
        conn,
        "SELECT publish_date, title, role, amount, status FROM litigations "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 5",
        (stock_id,),
    )
    sentiment = repository.fetch_one(
        conn,
        "SELECT stat_date, post_count, sentiment_score FROM sentiment "
        "WHERE stock_id = ? ORDER BY stat_date DESC LIMIT 1",
        (stock_id,),
    )
    research = repository.fetch_all(
        conn,
        "SELECT publish_date, org_name, rating, title FROM research_reports "
        "WHERE stock_id = ? ORDER BY publish_date DESC LIMIT 5",
        (stock_id,),
    )
    capital = repository.fetch_all(
        conn,
        "SELECT trade_date, main_net, main_net_ratio FROM capital_flows "
        "WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 5",
        (stock_id,),
    )
    return {
        "business": profile["business"] if profile else None,
        "official_website": profile["official_website"] if profile else None,
        "latest_financials": dict(latest) if latest else None,
        "revenue_region": [{"item": r["item_name"], "ratio": r["ratio"]} for r in regions],
        "revenue_products": [{"item": r["item_name"], "ratio": r["ratio"]} for r in products],
        "insider_trades": [dict(r) for r in insiders],
        "pledge_count": pledge["n"] if pledge else 0,
        "integrity_records": [dict(r) for r in integrity],
        "announcements": [dict(r) for r in announcements],
        "litigations": [dict(r) for r in litigations],
        "sentiment": dict(sentiment) if sentiment else None,
        "research_reports": [dict(r) for r in research],
        "capital_flows": [dict(r) for r in capital],
    }


def _load_extra(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {}
        return _load_extra_rows(conn, stock_id)
    finally:
        conn.close()


def _load_score_financials(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    """加载历年财务与经营现金流，供五维评分解读做基本面佐证。"""

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {}
        rows = repository.fetch_all(
            conn,
            "SELECT report_date, revenue_yoy, net_profit_yoy, net_profit, net_profit_deducted, "
            "roe, gross_margin, net_margin FROM financials "
            "WHERE stock_id = ? AND report_type = 'FY' ORDER BY report_date ASC",
            (stock_id,),
        )
        ocf_rows = repository.fetch_all(
            conn,
            "SELECT report_date, item_value FROM statement_items "
            "WHERE stock_id = ? AND item_code = 'NETCASH_OPERATE' AND report_type = 'FY'",
            (stock_id,),
        )
        dividend_rows = repository.fetch_all(
            conn,
            "SELECT ex_date, cash_per_share FROM dividends "
            "WHERE stock_id = ? AND ex_date IS NOT NULL ORDER BY ex_date ASC",
            (stock_id,),
        )
        ocf_map = {row["report_date"]: row["item_value"] for row in ocf_rows}
        dividend_history = [
            {"year": (row["ex_date"] or "")[:4], "cash_per_share": row["cash_per_share"]}
            for row in dividend_rows[-6:]
        ]

        annual_growth: list[dict[str, Any]] = []
        annual_quality: list[dict[str, Any]] = []
        for row in rows[-5:]:
            year = (row["report_date"] or "")[:4]
            net_profit = row["net_profit"]
            deducted = row["net_profit_deducted"]
            ocf = ocf_map.get(row["report_date"])
            annual_growth.append(
                {
                    "year": year,
                    "revenue_yoy": row["revenue_yoy"],
                    "net_profit_yoy": row["net_profit_yoy"],
                }
            )
            annual_quality.append(
                {
                    "year": year,
                    "roe": row["roe"],
                    "gross_margin": row["gross_margin"],
                    "net_margin": row["net_margin"],
                    "ocf": ocf,
                    "net_profit": net_profit,
                    "net_profit_deducted": deducted,
                    "ocf_to_profit": (ocf / net_profit) if (ocf is not None and net_profit) else None,
                    "oneoff_ratio": (
                        (net_profit - deducted) / net_profit * 100
                        if (net_profit is not None and deducted is not None and net_profit)
                        else None
                    ),
                }
            )
        return {
            "annual_growth": annual_growth,
            "annual_quality": annual_quality,
            "dividend_history": dividend_history,
        }
    finally:
        conn.close()


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
            f"{d['name']} {d['score']:.0f}/{d['weight']}" for d in facts.get("dimensions", [])
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
        method_names = {"relative": "相对估值", "graham": "格雷厄姆", "ddm": "股利折现", "dcf": "现金流折现"}
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


def _build_prompt(facts: dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2, default=str)


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
            max_tokens=1600,
        )
        sections = _parse_sections(data)
    except (LlmError, LlmConfigError):
        sections = []
    if sections:
        return {"sections": sections, "source": "llm"}
    return {"sections": template, "source": "template"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_dir() -> Path:
    directory = paths.cache_dir() / "narrative"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _overview_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"{symbol}.json"


def _score_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"score_{symbol}.json"


def _valuation_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"valuation_{symbol}.json"


def _read_cache(path: Path, ttl_days: float) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != _NARRATIVE_VERSION:
        return None
    generated_at = data.get("generated_at")
    if generated_at:
        try:
            generated = datetime.fromisoformat(generated_at)
        except ValueError:
            return None
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - generated).total_seconds() > ttl_days * 86400:
            return None
    return data


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["version"] = _NARRATIVE_VERSION
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
    ranked = sorted(dims, key=lambda d: d["score"] / d["weight"] if d["weight"] else 0, reverse=True)
    strongest, weakest = ranked[0], ranked[-1]
    lines = [
        f"综合财务评分 {facts['total']} 分，评级“{facts['rating']}”。",
        f"最强维度是{strongest['name']}（{strongest['score']:.0f}/{strongest['weight']:.0f}），"
        f"最弱维度是{weakest['name']}（{weakest['score']:.0f}/{weakest['weight']:.0f}）。",
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
            max_tokens=800,
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


def _valuation_facts(result: dict[str, Any], financials: dict[str, Any]) -> dict[str, Any]:
    basis_map = {
        "relative": "历史PE分位与中位数来自公司自身PE序列；EPS来自最新财报",
        "graham": "EPS与BPS来自最新财报；22.5 为格雷厄姆固定系数",
        "ddm": "每股股利来自最新分红；增长率5%与折现率8%为模型默认假设，未按公司基本面校准",
        "dcf": "自由现金流与股本来自财报；增长率10%、折现率9%、永续3%为模型默认假设，未按公司基本面校准",
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
            max_tokens=1200,
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
