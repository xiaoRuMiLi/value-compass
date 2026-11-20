"""财报深度体检：确定性扫描 → LLM 逐条剖析 → 结构化报告。

与项目其他叙述模块保持同一模式：提示词配置文件 + LLM 增强 + 模板回退 +
独立 TTL 缓存 + 版本号失效。区别在于本模块的**事实部分完全由
``analysis.forensics`` 计算得出**，LLM 只做解释，绝不负责发现数字或下定性结论。
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type, TypeVar

from fundamental.analysis.forensics import scan_forensics
from fundamental.config import load_settings
from fundamental.models import (
    Announcement,
    AuditOpinion,
    BehaviorType,
    CorporateAction,
    CorporateActionType,
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
from fundamental.utils.prompts import load_prompt
from fundamental.utils.symbols import parse_symbol

from ._common import (
    _build_prompt,
    _forensics_cache_path,
    _max_tokens,
    _now,
    _write_cache,
)

# 提示词/输出结构升级时递增，使旧报告缓存失效。
# 阈值改动不改变版本号，而是由 thresholds_fingerprint 让缓存自动失效。
_FORENSICS_VERSION = 3

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


# ---------- 事实组装 ----------


def _brief_signal(signal: dict[str, Any]) -> dict[str, Any]:
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
    return out


def _facts(
    symbol: str,
    inputs: dict[str, Any],
    scan: dict[str, Any],
    intel: dict[str, Any] | None,
    max_signals: int,
    max_anomalies: int,
) -> dict[str, Any]:
    ev = scan["evidence"]
    intel = intel or {}
    return {
        "instruction": (
            "以下全部数据由代码从公开财报与公开资料确定性计算得出，不是模型推测。"
            "请逐条解释、交叉印证，不得引入任何未提供的事实，不得下法律定性结论。"
        ),
        "company": {"symbol": symbol, "name": inputs["name"], "industry": inputs["industry"]},
        "window": scan["window"],
        "periods": scan["periods"],
        "summary": scan["summary"],
        "signals": [_brief_signal(s) for s in scan["signals"][:max_signals]],
        "signals_omitted": max(0, len(scan["signals"]) - max_signals),
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
        insider_trades=inputs["insider_trades"],
        pledges=inputs["pledges"],
        corporate_actions=inputs["corporate_actions"],
        intel=intel,
        lookback_periods=lookback,
        recent_years=recent_years,
        config=cfg,
    )
    facts = _facts(symbol, inputs, scan, intel, max_signals, max_anomalies)

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
        "scan": scan,
        "citations": _citations(scan, intel),
        "coverage": scan["coverage"],
        **report,
    }
    _write_cache(cache_path, output)
    return output
