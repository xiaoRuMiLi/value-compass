"""财报深度体检：确定性扫描 → LLM 逐条剖析 → 结构化报告。

与项目其他叙述模块保持同一模式：提示词配置文件 + LLM 增强 + 模板回退 +
独立 TTL 缓存 + 版本号失效。区别在于本模块的**事实部分完全由
``analysis.forensics`` 计算得出**，LLM 只做解释，绝不负责发现数字或下定性结论。
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type, TypeVar

from fundamental.analysis.forensics import scan_forensics
from fundamental.analysis.forensic_patterns import scan_fraud_patterns
from fundamental.config import load_settings
from fundamental.models import (
    Announcement,
    AuditOpinion,
    BehaviorType,
    CorporateAction,
    CorporateActionType,
    DividendRecord,
    EarningsForecast,
    FinancialReport,
    ForecastType,
    InsiderDirection,
    InsiderTrade,
    IntegrityCategory,
    IntegrityRecord,
    KlineBar,
    Pledge,
    ReportType,
    Severity,
    StatementItem,
    StatementType,
    ChatMessage,
)
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.forensics_config import load_forensics_config
from fundamental.services.llm import chat_json, has_live_llm
from fundamental.storage import db, repository
from fundamental.utils.intel import load_intel_digest
from fundamental.utils.logging import get_logger
from fundamental.utils.prompts import load_prompt
from fundamental.utils.symbols import parse_symbol

from ._evidence import retrieve_evidence
from ._quality import excerpt_quality as _excerpt_quality
from ._report_facts import extract_report_facts
from ._source_guard import ensure_source_documents
from ._report_mining import mine_report_evidence
from ._common import (
    _build_prompt,
    _forensics_cache_path,
    _max_tokens,
    _now,
    _write_cache,
)

# 提示词/输出结构升级时递增，使旧报告缓存失效。
# 阈值改动不改变版本号，而是由 thresholds_fingerprint 让缓存自动失效。
_FORENSICS_VERSION = 20

_logger = get_logger(__name__)

# 喂给最终 LLM 的新线索条数上限（控制上下文预算，全文仍写进 output 供前端展示）
_MAX_MINING_FINDINGS_IN_FACTS = 5
# 财务戏法线索注入总纲的条数上限；完整结果作为报告并列字段供前端逐条查看
_MAX_FRAUD_PATTERNS_IN_FACTS = 3

_JUDGEMENTS = {"confirmed", "suspicious", "normal", "unverifiable"}
_CONFIDENCES = {"high", "medium", "low"}
_LEVELS = {"high", "medium", "low", "clear"}

_T = TypeVar("_T")


# ---------- 数据加载 ----------


def _to(cls: Type[_T], row: Any, enums: dict[str, Type] | None = None) -> _T:
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in row.keys():
            continue
        value = row[field.name]
        if enums and field.name in enums and value is not None:
            value = enums[field.name](value)
        values[field.name] = value
    return cls(**values)


def _load_inputs(symbol: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """从本地 SQLite 读取体检所需的全部原始数据。"""

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return None
        stock = repository.fetch_one(conn, "SELECT * FROM stocks WHERE id = ?", (stock_id,))

        def rows(table: str) -> list[Any]:
            return repository.fetch_all(conn, f"SELECT * FROM {table} WHERE stock_id = ?", (stock_id,))

        return {
            "name": stock["name"] if stock else symbol,
            "industry": (stock["industry"] if stock else None) or "未知行业",
            "financials": [
                _to(FinancialReport, r, {"report_type": ReportType, "audit_opinion": AuditOpinion})
                for r in rows("financials")
            ],
            "statement_items": [
                _to(StatementItem, r, {"report_type": ReportType, "statement_type": StatementType})
                for r in rows("statement_items")
            ],
            "kline": [_to(KlineBar, r) for r in rows("kline")],
            "integrity": [
                _to(
                    IntegrityRecord, r,
                    {"behavior_type": BehaviorType, "category": IntegrityCategory, "severity": Severity},
                )
                for r in rows("integrity_records")
            ],
            "announcements": [_to(Announcement, r) for r in rows("announcements")],
            "forecasts": [
                _to(EarningsForecast, r, {"forecast_type": ForecastType})
                for r in rows("earnings_forecasts")
            ],
            "dividends": [_to(DividendRecord, r) for r in rows("dividends")],
            "insider_trades": [
                _to(InsiderTrade, r, {"direction": InsiderDirection})
                for r in rows("insider_trades")
            ],
            "pledges": [_to(Pledge, r) for r in rows("pledges")],
            "corporate_actions": [
                _to(CorporateAction, r, {"action_type": CorporateActionType})
                for r in rows("corporate_actions")
            ],
        }
    finally:
        conn.close()


def _fill_ocf_from_items(
    financials: list[FinancialReport], statement_items: list[StatementItem]
) -> list[FinancialReport]:
    """A 股 financials.ocf 常为空，但 statement_items 里有 NETCASH_OPERATE，按报告期回填。

    只在本模块内部修正输入，不写数据库，也不改同步链路；港股 financials.ocf
    本来就由主指标接口填充齐全，这里不会动它。修掉「原文里明明有经营现金流，
    事实层却判字段缺失、把检查跳过」这一割裂。
    """
    if all(f.ocf is not None for f in financials):
        return financials
    by_date: dict[str, float] = {}
    for item in statement_items:
        if str(item.item_code) != "NETCASH_OPERATE" or item.item_value is None:
            continue
        by_date.setdefault(str(item.report_date), float(item.item_value))
    if not by_date:
        return financials
    filled: list[FinancialReport] = []
    changed = 0
    for f in financials:
        value = by_date.get(str(f.report_date))
        if f.ocf is None and value is not None:
            if dataclasses.is_dataclass(f):
                filled.append(dataclasses.replace(f, ocf=value))
            else:
                # 兼容非 dataclass 适配器（只按属性取值），避免 replace() 抛 TypeError
                data = dict(vars(f))
                data["ocf"] = value
                filled.append(type(f)(**data))
            changed += 1
        else:
            filled.append(f)
    if changed:
        _logger.info("从科目明细回填 ocf %d 期", changed)
    return filled


# ---------- 事实组装 ----------


def _merge_evidence(
    mined: dict[str, Any],
    keyword_map: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    """合并两路证据：LLM 分段摘录优先，关键词检索兜底，统一成一种结构。

    - mined.signal_evidence: {sid: [{quote, section, source, period, why}]}
    - keyword_map: {sid: [{text, source, period, keywords}]}

    统一输出 {sid: [{text, source, period, why, method}]}，供 _brief_signal 挂载。
    """
    merged: dict[str, list[dict[str, str]]] = {}
    mined_signals = mined.get("signal_evidence") or {}
    all_ids = set(mined_signals) | set(keyword_map)
    for sid in all_ids:
        mined_items: list[dict[str, str]] = []
        keyword_items: list[dict[str, str]] = []
        seen: set[str] = set()
        for hit in mined_signals.get(sid, []):
            text = str(hit.get("quote") or "").strip()
            if not text or text[:60] in seen:
                continue
            seen.add(text[:60])
            mined_items.append(
                {
                    "text": text,
                    "source": f"{hit.get('source', '')}·{hit.get('section', '')}".strip("·"),
                    "period": str(hit.get("period") or ""),
                    "why": str(hit.get("why") or ""),
                    "method": "llm_mining",
                    "is_current": bool(hit.get("is_current", False)),
                    "report_label": str(hit.get("report_label") or ""),
                    "seq": int(hit.get("seq") or 0),
                }
            )
        for hit in keyword_map.get(sid, []):
            text = str(hit.get("text") or "").strip()
            if not text or text[:60] in seen:
                continue
            seen.add(text[:60])
            keyword_items.append(
                {
                    "text": text,
                    "source": str(hit.get("source") or ""),
                    "period": str(hit.get("period") or ""),
                    "why": str(hit.get("keywords") or ""),
                    "method": "keyword",
                    "is_current": bool(hit.get("is_current", False)),
                    "report_label": str(hit.get("report_label") or ""),
                    "seq": 0,
                }
            )
        # 两路各自排序后交错保留名额：挖掘摘录时效新，但关键词证据命中
        # 「该下降主要由於…」这类关键句，不能被当期摘录整体挤出上下文。
        mined_items.sort(key=lambda it: (
            not it.get("is_current", False),
            -it.get("seq", 0),
            -_excerpt_quality(it["text"]),
        ))
        keyword_items.sort(key=lambda it: -_excerpt_quality(it["text"]))
        items: list[dict[str, str]] = []
        for i in range(max(len(mined_items), len(keyword_items))):
            if i < len(mined_items):
                items.append(mined_items[i])
            if i < len(keyword_items):
                items.append(keyword_items[i])
            if len(items) >= 8:
                break
        if items:
            merged[sid] = items[:8]
    return merged


def _brief_signal(
    signal: dict[str, Any],
    evidence_map: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    keys = (
        "id", "category", "severity", "title", "period", "facts",
        "trigger", "description", "evidence_refs", "consolidated",
    )
    out = {k: signal[k] for k in keys if k in signal}
    occurrences = signal.get("occurrences") or []
    if occurrences:
        out["occurrences"] = [
            {"period": o.get("period"), "severity": o.get("severity"), "facts": (o.get("facts") or [])[:3]}
            for o in occurrences[:8]
        ]
    # 公司原文证据：挂到对应风险点，LLM 逐条剖析时可引用公司自己的解释
    evidence = (evidence_map or {}).get(str(signal.get("id") or "")) or []
    if evidence:
        out["official_explanation"] = evidence
    return out


def _facts(
    symbol: str,
    inputs: dict[str, Any],
    scan: dict[str, Any],
    intel: dict[str, Any] | None,
    max_signals: int,
    max_anomalies: int,
    evidence_map: dict[str, list[dict[str, str]]] | None = None,
    mining: dict[str, Any] | None = None,
    fraud: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ev = scan["evidence"]
    fraud = fraud or {}
    intel = intel or {}
    evidence_map = evidence_map or {}
    covered = sum(1 for s in scan["signals"][:max_signals] if s["id"] in evidence_map)
    return {
        "instruction": (
            "以下全部数据由代码从公开财报与公开资料确定性计算得出，不是模型推测。"
            "请逐条解释、交叉印证，不得引入任何未提供的事实，不得下法律定性结论。"
            "profit_structure 给出营业利润、税前利润与营业利润以下合计的逐期金额，"
            "用于定位比率变动发生在经营端还是经营端之外；解释必须落到具体科目、金额与方向，"
            "不要用核查提醒代替解释。"
            "signal.official_explanation 是公司财报原文中对该类波动的官方说明，"
            "是解释该风险点的首选依据：引用时必须注明来自公司财报原文；"
            "若某条风险点没有 official_explanation，就明确写「财报原文中未找到相关说明」，"
            "不要臆造公司的解释。"
            "report_mining.source_docs 是本次通读的多份财报，既有最新季报也有年报："
            "季报（如 2026 三季报/中报/一季报）反映当下经营、时效最新，"
            "年报（如 2025 年报）权威详细、含公司对全年波动的归因，其余更早年报是历史佐证。"
            "判断『现在怎么样、拐点是否延续』必须优先引用最新季报，"
            "解释『全年波动原因』则引用年报的公司自述；两者结合，不要只用旧年报下当期结论。"
            "每条证据都带 report_label（报告期标签）与 is_current，引用时须注明来自哪一期。"
            "report_mining.report_summaries 是公司财报分段后的客观总结，"
            "report_mining.new_findings 是模型通读原文后发现、但确定性扫描未覆盖的线索"
            "（每条都附了原文摘录）。verdict.essence 作为全文总纲，必须同时纳入规则信号与"
            "new_findings 中最实质的发现——特别是带具体数字的线索（关联方采购/销售占比、"
            "研发资本化比重、财务公司信贷、未履行承诺等），不要只谈治理与市场预期而遗漏它们，"
            "否则总述会与下方剖析对不上。新线索只作提示，不得当作已确认事实，"
            "引用须注明来自财报原文摘录。"
            "fraud_patterns 是确定性「财务戏法线索」扫描结果，每条都带组合判定条件与数值证据，"
            "覆盖虚增利润/收入质量、财务洗澡、关联交易、会计估计四类；"
            "verdict.essence 需纳入其中最实质的 1~2 条并解释因果链（谁导致谁、具体科目与金额），"
            "但绝不能据此下「财务造假/粉饰/操纵」定性结论；未触发的检查不等于没有问题。"
            "无论新增多少线索，essence 都必须保留至少一处公司财报原文里的具体科目与金额"
            "（来自 official_explanations），并注明报告期；不得用新增线索替换掉原有的原文取证。"
        ),
        "company": {"symbol": symbol, "name": inputs["name"], "industry": inputs["industry"]},
        "fraud_patterns": {
            "summary": fraud.get("summary", {}),
            "checked_but_not_triggered": (fraud.get("coverage") or {}).get("checked_no_trigger", 0),
            "patterns": [
                {
                    "id": p["id"], "title": p["title"], "category": p["category"],
                    "severity": p["severity"], "period": p["period"],
                    "confidence": p["confidence"],
                    "explanation": p["explanation"][:300],
                    "facts": p["facts"],
                }
                for p in (fraud.get("patterns") or [])[:_MAX_FRAUD_PATTERNS_IN_FACTS]
            ],
        },
        "window": scan["window"],
        "periods": scan["periods"],
        "summary": scan["summary"],
        "profit_structure": scan.get("profit_structure", []),
        "signals": [_brief_signal(s, evidence_map) for s in scan["signals"][:max_signals]],
        "signals_omitted": max(0, len(scan["signals"]) - max_signals),
        "official_explanation_coverage": {
            "signals_with_evidence": covered,
            "signals_total": min(len(scan["signals"]), max_signals),
        },
        "report_mining": {
            "source_doc": (mining or {}).get("source_doc", ""),
            "period": (mining or {}).get("period", ""),
            "latest_report": (mining or {}).get("latest_report", ""),
            "latest_period": (mining or {}).get("latest_period", ""),
            "source_docs": (mining or {}).get("source_docs", []),
            "report_summaries": (mining or {}).get("summaries", [])[:8],
            "new_findings": (mining or {}).get("new_findings", [])[:_MAX_MINING_FINDINGS_IN_FACTS],
        },
        "anomalies": scan["anomalies"][:max_anomalies],
        "evidence": {
            "regulatory": (ev.get("regulatory") or [])[:8],
            "announcement_hits": (ev.get("announcement_hits") or [])[:8],
            "forecast_checks": (ev.get("forecast_checks") or [])[:8],
            "capital_moves": ev.get("capital_moves") or {},
            "price_reactions": (ev.get("price_reactions") or [])[:8],
            "external_views": [
                {k: v.get(k) for k in ("kind", "date", "org", "title", "keywords", "excerpt")}
                for v in (ev.get("external_views") or [])[:6]
            ],
            "company_reports": [
                {
                    "date": r.get("date"), "org": r.get("org"), "title": r.get("title"),
                    "excerpt": (r.get("content") or "")[:600],
                }
                for r in (intel.get("company_reports") or [])[:4]
            ],
            "industry_reports": [
                {"date": r.get("date"), "org": r.get("org"), "title": r.get("title")}
                for r in (intel.get("industry_reports") or [])[:3]
            ],
            "surveys": [
                {"date": r.get("date"), "org": r.get("org"), "title": r.get("title")}
                for r in (intel.get("surveys") or [])[:3]
            ],
        },
        "coverage": scan["coverage"],
    }


# ---------- LLM 输出校验 ----------


def _text(value: Any, limit: int, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    cleaned = " ".join(value.split()).strip()
    return cleaned[:limit] if cleaned else default


def _string_list(value: Any, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        if isinstance(item, str) and item.strip():
            out.append(" ".join(item.split())[:item_limit])
    return out


def _normalize(raw: Any, scan: dict[str, Any], max_signals: int) -> dict[str, Any] | None:
    """把 LLM 输出收敛到可信结构：未知枚举降级、缺失项补齐、绝不新增事实。"""

    if not isinstance(raw, dict):
        return None

    verdict_raw = raw.get("verdict") if isinstance(raw.get("verdict"), dict) else {}
    level = verdict_raw.get("level")
    if level not in _LEVELS:
        level = scan["summary"]["risk_level"]

    by_id = {
        item["id"]: item
        for item in (raw.get("analyses") or [])
        if isinstance(item, dict) and item.get("id")
    }
    analyses: list[dict[str, Any]] = []
    for signal in scan["signals"]:
        a = by_id.get(signal["id"]) or {}
        judgement = a.get("judgement")
        confidence = a.get("confidence")
        analyses.append(
            {
                "id": signal["id"],
                "title": signal["title"],
                "severity": signal["severity"],
                "category": signal["category"],
                "period": signal["period"],
                "substance": _text(a.get("substance"), 500, signal["description"]),
                "benign": _text(a.get("benign"), 350, "未生成合理解释，请结合行业属性与会计口径自行核对。"),
                "risk": _text(a.get("risk"), 350, "未生成风险假设，建议查阅对应报告期公告原文。"),
                "judgement": judgement if judgement in _JUDGEMENTS else "unverifiable",
                "confidence": confidence if confidence in _CONFIDENCES else "low",
                "action": _text(a.get("action"), 250, "建议核对对应报告期财报原文与监管公告。"),
                "analyzed": bool(a) and signal["id"] in by_id,
            }
        )

    summary = scan["summary"]
    window = scan["window"]
    fallback_essence = (
        f"本次扫描 {scan['periods']['count']} 期财报，结论窗口 {window['conclusion_from']} 至 "
        f"{window['conclusion_to']}，检出 {summary['signal_count']} 个风险点、"
        f"{summary['anomaly_count']} 项异常波动。大模型未返回综合判断，"
        "以下逐条剖析与证据清单仍可作为核查依据。"
    )
    return {
        "verdict": {
            "level": level,
            "headline": _text(verdict_raw.get("headline"), 80, "已生成基于公开财报的取证扫描结果"),
            "essence": _text(verdict_raw.get("essence"), 1200, fallback_essence),
        },
        "analyses": analyses,
        "cross_checks": _string_list(raw.get("cross_checks"), 8, 400),
        "caveats": _string_list(raw.get("caveats"), 6, 400),
    }


def _template(symbol: str, scan: dict[str, Any]) -> dict[str, Any]:
    """无 LLM 时的客观回退：只陈述代码算出的事实，不做任何推测。"""

    summary = scan["summary"]
    window = scan["window"]
    skipped = [item["check"] for item in scan["coverage"]["skipped"]]
    essence = (
        f"本次体检共扫描 {scan['periods']['count']} 期财报，结论窗口为 "
        f"{window['conclusion_from']} 至 {window['conclusion_to']}（近 {window['lookback_periods']} 期），"
        f"检出 {summary['signal_count']} 个风险点（高等级 {summary['high']} 个）与 "
        f"{summary['anomaly_count']} 项异常波动。"
        "当前未启用大模型解读，以下仅列出由代码确定性计算出的客观证据与触发阈值，"
        "不构成对实质问题的判断；请结合公告原文、审计意见与监管结论自行核验。"
    )
    if summary["consolidated"]:
        essence += f" 其中 {summary['consolidated']} 条逐期重复信号已合并为趋势型风险点，明细保留在各条的 occurrences 中。"
    if skipped:
        essence += " 因数据缺失而未执行的检查：" + "、".join(skipped[:4]) + "，故「未检出」不等于「不存在」。"

    analyses = [
        {
            "id": s["id"], "title": s["title"], "severity": s["severity"],
            "category": s["category"], "period": s["period"],
            "substance": s["description"],
            "benign": "未启用大模型解读，未生成合理解释。",
            "risk": "未启用大模型解读，未生成待核查假设。",
            "judgement": "unverifiable", "confidence": "low",
            "action": "建议查阅该报告期财报原文与对应公告。",
            "analyzed": False,
        }
        for s in scan["signals"]
    ]
    return {
        "verdict": {
            "level": summary["risk_level"],
            "headline": "已生成客观取证扫描（未启用 LLM 解读）",
            "essence": essence,
        },
        "analyses": analyses,
        "cross_checks": ["未启用大模型解读，未生成跨信号关联分析。"],
        "caveats": [
            "本报告基于公开财报与公开资料，不构成审计意见、监管结论或投资建议。",
            "「未检出异常」仅表示在现有数据与阈值下未触发规则，不代表不存在实质性问题。",
        ],
    }


# ---------- 引用清单 ----------


def _citations(scan: dict[str, Any], intel: dict[str, Any] | None) -> list[dict[str, str]]:
    ev = scan["evidence"]
    intel = intel or {}
    items: list[dict[str, str]] = []

    def add(kind: str, date: Any, org: Any, title: Any, url: Any) -> None:
        items.append(
            {
                "kind": kind,
                "date": str(date or ""),
                "org": str(org or ""),
                "title": str(title or ""),
                "url": str(url or ""),
            }
        )

    for r in (ev.get("regulatory") or [])[:8]:
        add("监管/诚信记录", r.get("date"), r.get("source"), r.get("title"), r.get("url"))
    for h in (ev.get("announcement_hits") or [])[:6]:
        add("公告线索", h.get("date"), h.get("source"), h.get("title"), h.get("url"))
    for v in (ev.get("external_views") or [])[:6]:
        add(f"第三方印证·{v.get('kind', '')}", v.get("date"), v.get("org"), v.get("title"), v.get("url"))
    for r in (intel.get("company_reports") or [])[:4]:
        add("个股研报", r.get("date"), r.get("org"), r.get("title"), r.get("url"))
    for r in (intel.get("industry_reports") or [])[:3]:
        add("行业研报", r.get("date"), r.get("org"), r.get("title"), r.get("url"))
    for s in (ev.get("price_reactions") or [])[:4]:
        ret = s.get("window_return_pct")
        add(
            "披露日股价反应", s.get("publish_date"), "本地行情",
            f"{s.get('report_date')} 报告披露日前后股价涨跌 {ret:.2f}%" if ret is not None else "股价反应",
            "",
        )

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = item["title"] or item["date"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:20]


# ---------- 缓存 ----------


def _read_forensics_cache(
    path: Path, ttl_days: float, fingerprint: str
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("forensics_version") != _FORENSICS_VERSION:
        return None
    # 阈值配置一变，旧的扫描结果就必须重算，不必手动清缓存或点「重新分析」
    if data.get("thresholds_fingerprint") != fingerprint:
        return None
    generated = data.get("generated_at")
    if generated:
        try:
            moment = datetime.fromisoformat(generated)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - moment).total_seconds() > ttl_days * 86400:
            return None
    return data


# ---------- 主入口 ----------


def generate_financial_forensics(
    symbol: str,
    db_path: Path | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """生成财报深度体检报告：确定性扫描 + LLM 剖析 + 模板回退 + 独立缓存。"""

    cfg = load_forensics_config()
    fingerprint = cfg.fingerprint()
    ttl_days = float(load_settings().get("narrative.cache_ttl_days", 10))
    cache_path = _forensics_cache_path(symbol)
    if not refresh:
        cached = _read_forensics_cache(cache_path, ttl_days, fingerprint)
        if cached:
            return cached

    inputs = _load_inputs(symbol, db_path)
    if inputs is None:
        return {
            "symbol": symbol,
            "error": "股票不存在，请先运行 fetch_data",
            "source": "template",
            "generated_at": _now(),
            "forensics_version": _FORENSICS_VERSION,
        }

    # A 股主表缺 ocf 但科目明细里有：先补齐再扫描，避免「原文有数却判缺失」。
    inputs["financials"] = _fill_ocf_from_items(
        inputs["financials"], inputs["statement_items"]
    )

    # 前置守卫：先用财报原文把证据底座备齐，再进入扫描与逐条剖析。
    # 缺档时自动按市场下载多期原文；失败不阻断，但状态会写进报告。
    guard = ensure_source_documents(
        symbol,
        db_path=db_path,
        min_documents=cfg.min_source_documents,
        min_years=cfg.min_source_years,
        per_type=cfg.source_archive_per_type,
    )

    try:
        intel = load_intel_digest(symbol, refresh=refresh)
    except Exception:  # 情报资料是增强项，失败不应阻断体检
        intel = None

    # 窗口、输出预算与全部触发阈值统一来自 config/forensics.toml
    lookback = cfg.lookback_periods
    recent_years = cfg.recent_years
    max_signals = cfg.max_signals
    max_anomalies = cfg.max_anomalies

    scan = scan_forensics(
        symbol,
        financials=inputs["financials"],
        statement_items=inputs["statement_items"],
        kline=inputs["kline"],
        integrity=inputs["integrity"],
        announcements=inputs["announcements"],
        forecasts=inputs["forecasts"],
        dividends=inputs.get("dividends"),
        insider_trades=inputs["insider_trades"],
        pledges=inputs["pledges"],
        corporate_actions=inputs["corporate_actions"],
        intel=intel,
        lookback_periods=lookback,
        recent_years=recent_years,
        config=cfg,
    )

    # 财务戏法线索：独立于 scan.signals 的并列检查，不占用原有风险点名额、
    # 不进入逐条剖析数组，因此不会影响既有 25 条规则与 LLM 剖析完整性。
    # 事实卡只读归档文本，扫描只吃纯数据；任一环节失败都写进报告状态，不静默。
    try:
        report_facts = extract_report_facts(symbol, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财报结构化事实抽取失败 %s: %s", symbol, exc)
        report_facts = {"error": str(exc), "source_docs": [], "missing_facts": ["抽取失败"]}
    market = "HK" if str(symbol).upper().endswith(".HK") else "A"
    try:
        fraud = scan_fraud_patterns(
            symbol=symbol,
            market=market,
            financials=inputs["financials"],
            statement_items=inputs["statement_items"],
            report_facts=report_facts,
            config=cfg.fraud_patterns,
            lookback_periods=lookback,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财务戏法检查失败 %s: %s", symbol, exc)
        fraud = {
            "symbol": symbol, "market": market, "patterns": [],
            "coverage": {"performed": [], "skipped": [], "checks_total": 11,
                         "checked_no_trigger": 0, "not_applicable": 0, "data_missing": 0},
            "summary": {"pattern_count": 0, "high": 0, "medium": 0, "low": 0,
                        "by_category": {}},
            "error": str(exc),
        }

    # 财报原文证据：先跑关键词检索（离线、快），再跑分段挖掘（LLM、贵）。
    # 两者都失败不阻断主流程——没有原文时 LLM 仍按扫描数据解释。
    try:
        keyword_map = retrieve_evidence(
            symbol, scan["signals"][:max_signals], db_path=db_path, max_chars=900
        )
    except Exception:  # noqa: BLE001
        keyword_map = {}

    # 分段挖掘：带风险点逐段摘录+总结，能发现扫描器未覆盖的新线索。
    # 结果自带磁盘缓存（按财报指纹+风险点指纹失效），refresh=true 时强制重跑。
    mining: dict[str, Any] = {}
    try:
        mining = mine_report_evidence(
            symbol, scan["signals"][:max_signals], db_path=db_path, refresh=refresh
        )
    except Exception:  # noqa: BLE001
        mining = {}

    # 关键词检索本身不带当期标记，用挖掘出的"最新报告期年份"给命中打标：
    # 最新季报（如 2026）与当期年报（如 2025）都算当期证据，历史年份才是佐证。
    # 这样"当下为主"的排序对两路证据都生效，且 2026 季报不会被误降级。
    current_years = {
        str(y) for y in (
            (mining or {}).get("latest_period", ""),
            (mining or {}).get("period", ""),
        ) if y
    }
    if current_years:
        for hits in keyword_map.values():
            for hit in hits:
                hit["is_current"] = str(hit.get("period") or "") in current_years

    evidence_map = _merge_evidence(mining, keyword_map)

    facts = _facts(
        symbol, inputs, scan, intel, max_signals, max_anomalies,
        evidence_map=evidence_map, mining=mining, fraud=fraud,
    )

    report: dict[str, Any] | None = None
    source = "template"
    if has_live_llm():
        try:
            system = load_prompt("financial_forensics")
            # 输出预算按风险点数动态放大：信号多时 JSON 会很长，固定上限会把它截断，
            # 导致解析失败并静默回退模板。配置值作为下限，上限 8000 token。
            base = _max_tokens("forensics", 3000)
            needed = 1200 + 380 * min(len(scan["signals"]), max_signals)
            raw = chat_json(
                [ChatMessage("system", system), ChatMessage("user", _build_prompt(facts))],
                max_tokens=max(base, min(needed, 8000)),
            )
            report = _normalize(raw, scan, max_signals)
        except (LlmError, LlmConfigError, FileNotFoundError, TypeError, ValueError):
            report = None
        source = "llm" if report else "template_fallback"
    if report is None:
        report = _template(symbol, scan)

    # 免责声明是硬兜底，即使 LLM 输出遗漏也必须存在
    caveats = list(report["caveats"])
    disclaimer = "本报告基于公开财报与公开资料，不构成审计意见、监管结论或投资建议。"
    if not any("不构成" in c for c in caveats):
        caveats.append(disclaimer)
    report["caveats"] = caveats[:6]

    output: dict[str, Any] = {
        "symbol": symbol,
        "source": source,
        "generated_at": _now(),
        "forensics_version": _FORENSICS_VERSION,
        "thresholds_fingerprint": fingerprint,
        "thresholds": cfg.as_dict(),
        "company": {"name": inputs["name"], "industry": inputs["industry"]},
        "source_guard": guard,
        "scan": scan,
        "official_explanations": evidence_map,
        "report_mining": mining,
        "fraud_patterns": fraud,
        "report_facts": report_facts,
        "citations": _citations(scan, intel),
        "coverage": scan["coverage"],
        **report,
    }
    _write_cache(cache_path, output)
    return output
