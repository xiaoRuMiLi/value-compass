"""跨期叙事对照：先用往年财报（不含当期）为财务粉饰/造假风险因素建立叙述基线，
再把「基线 + 当期财报」交给模型做完整对照判断，发现被淡化、回避、改口或首次出现的风险表述。

与现有板块的区别：
- risk_signals / 逐条剖析：从代码算出的信号出发，找公司解释；
- fraud_patterns：从 15 项已知操纵模式出发，代码判定是否踩中；
- 本模块：从「风险因素探针清单」出发，先总结公司过去怎么披露，再对照当期怎么说，
  答案从叙事的跨期变化里长出来——擅长发现"数字没触发阈值、但公司悄悄改了口径"的隐含风险。

设计约束：
1. 往年是基线、当期是判断对象：历史只用最新年报之前的年报，当期 = 最新年报 + 其后的季报。
2. 引文零改写且逐字核验：阶段1、阶段2的每条引文都必须在各自归档原文中可定位，定位不到即剔除。
3. 不下定性：模型只输出「叙事变化 + 待核查线索」，严禁"造假/粉饰/操纵"等法律定性词。
4. 分段处理：当期文本超长时按章节/自然段落切分，与 _report_mining 共用切分器。
5. 独立缓存：按「版本 + 历史/当期文本指纹 + 探针清单」失效；失败一律降级，不阻断体检。

失败一律返回 available=False 的空结构，由叙述层决定如何呈现。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.services.llm import chat_json, has_live_llm, input_budget
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_garbled_text
from fundamental.utils.prompts import load_prompt

from ._evidence import _load_documents, _text_path
from ._quality import excerpt_quality
from ._report_mining import (
    _doc_year,
    _quote_verified,
    _report_label,
    _report_seq,
    split_report,
)

_logger = get_logger(__name__)

# 结构与提示词变化时递增，触发缓存失效
_DRIFT_VERSION = 1
_ANNUAL_TYPE = "annual_report"

# 财务粉饰/造假的叙事风险探针：先给模型，贯穿历史基线与当期对照两个阶段。
# 只描述「关注什么」，不下结论；模型负责在原文里筛相关文字、再对照当期叙事变化。
RISK_FACTOR_PROBES: tuple[dict[str, str], ...] = (
    {"id": "DRF01", "title": "收入确认与渠道压货", "focus": "提前/延后确认收入、验收口径变化、经销商标面压货、年末冲量、收入确认时点变更"},
    {"id": "DRF02", "title": "应收质量与坏账计提", "focus": "应收账款增速远超收入、账龄拉长、坏账计提比例下调、大额核销、应收票据替代"},
    {"id": "DRF03", "title": "存货跌价与积压", "focus": "存货堆积、跌价准备计提成反比、库龄变长、以库存商品结构变化解释"},
    {"id": "DRF04", "title": "现金流与利润背离", "focus": "经营现金流与净利润方向背离、收现比下降、利润靠应计项目支撑"},
    {"id": "DRF05", "title": "关联交易与资金占用", "focus": "关联采购销售定价、控股股东及关联方资金占用、非经营性往来、财务公司存款"},
    {"id": "DRF06", "title": "资产减值与商誉", "focus": "商誉减值时点与幅度、大额减值集中在某一年、减值假设变化、延后计提"},
    {"id": "DRF07", "title": "研发与利息资本化", "focus": "研发支出资本化率上升、借款费用资本化、资本化金额占利润比重"},
    {"id": "DRF08", "title": "会计政策与估计变更", "focus": "会计政策变更、折旧摊销年限调整、收入/成本口径重述、追溯调整"},
    {"id": "DRF09", "title": "股东质押减持与资金需求", "focus": "控股股东高比例质押、被动减持、增持承诺未兑现、股权冻结"},
    {"id": "DRF10", "title": "并购业绩承诺与兑现", "focus": "业绩承诺金额与完成率、承诺期结束后业绩变脸、补偿条款执行"},
    {"id": "DRF11", "title": "担保与或有负债", "focus": "对外担保余额、超限担保、未决诉讼、表外承诺"},
    {"id": "DRF12", "title": "客户与供应商集中度", "focus": "前五大客户/供应商占比变化、单一客户依赖、新增大额交易对手"},
    {"id": "DRF13", "title": "股权激励行权条件", "focus": "行权/解锁业绩条件是否过低、定向授予、条件变更、费用影响"},
    {"id": "DRF14", "title": "突击分红与现金匹配", "focus": "分红比例偏离历史、借钱分红、高分红与再融资并存、分红后资金紧张"},
    {"id": "DRF15", "title": "成本费用跨期与少计", "focus": "费用资本化、成本结转口径变化、期间费用率异常下降、少计成本费用"},
)

# 当期叙事相对基线的变化类型（归一化用，超出则归为「表述变化」）
_SHIFT_TYPES = (
    "承诺未兑现", "口径突变", "表述淡化", "首次出现", "持续强化",
    "数字与叙事背离", "解释缺失", "表述变化",
)
_SEVERITIES = ("high", "medium", "low")

_MAX_HISTORY_HITS_PER_FACTOR = 6
_MAX_BASELINE_CHARS = 7000
_MAX_FINDINGS = 12
_MAX_FINDINGS_PER_FACTOR = 2


def _history_count() -> int:
    """历史基线通读的年报份数（不含最新年报），配置 narrative.drift_history_count。"""
    try:
        configured = int(load_settings().get("narrative.drift_history_count", 3))
    except (TypeError, ValueError):
        configured = 3
    return max(1, min(configured, 6))


def _segment_chars() -> int:
    """复用原文挖掘的分段预算，保证两处切分口径一致。"""
    return max(2000, min(7000, int(input_budget() * 0.6)))


def empty_result(symbol: str, reason: str = "") -> dict[str, Any]:
    """统一降级结构：前端据此判断该板块是否可用。"""
    return {
        "available": False,
        "symbol": symbol,
        "version": _DRIFT_VERSION,
        "risk_factors": [dict(p) for p in RISK_FACTOR_PROBES],
        "history": {"documents": [], "segments": 0, "baseline": {}, "summary": ""},
        "current": {"documents": [], "segments": 0, "findings": [], "summary": ""},
        "rejected_quotes": 0,
        "failed_segments": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **({"error": reason} if reason else {}),
    }


def split_periods(
    docs: list[dict[str, Any]], history_count: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """切分历史/当期文档：历史 = 最新年报之前的年报；当期 = 最新年报 + 其后的季报。

    没有年报时退化为：报告期排序分最高的一份为当期，其余为历史。
    这样历史里不含任何当期文档，满足「先总结往年、不泄漏当期」的要求。
    """
    history_count = history_count or _history_count()
    usable = [d for d in docs if _text_path(d)]
    annuals = [d for d in usable if str(d.get("doc_type")) == _ANNUAL_TYPE]
    if not annuals:
        usable.sort(key=lambda d: -_report_seq(d))
        return usable[1:], usable[:1]

    annuals.sort(key=lambda d: (_doc_year(d), _report_seq(d)), reverse=True)
    anchor_year = _doc_year(annuals[0])
    history = [
        d for d in annuals
        if _doc_year(d) and _doc_year(d) < anchor_year
    ][:history_count]
    current = [d for d in usable if _doc_year(d) >= anchor_year]
    if not current:  # 理论上不会发生（最新年报必在内），兜底
        current = annuals[:1]
    return history, current


def _doc_meta(d: dict[str, Any], seq: int) -> dict[str, Any]:
    txt_path = _text_path(d)
    return {
        "doc": d,
        "path": str(txt_path) if txt_path else "",
        "raw": txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path else "",
        "source": str(d.get("title") or ""),
        "period": str(d.get("period") or ""),
        "report_label": _report_label(d),
        "seq": seq,
    }


def _load_docs_text(
    symbol: str, db_path: Path | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """加载并清洗历史/当期文档文本，跳过 CID 乱码（模型会对着乱码编结论）。"""
    docs = _load_documents(symbol, db_path)
    history, current = split_periods(docs)
    max_seq = max((_report_seq(d) for d in history + current), default=0)

    def clean(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for d in rows:
            meta = _doc_meta(d, _report_seq(d))
            if not meta["raw"] or is_garbled_text(meta["raw"]):
                _logger.warning("跨期叙事对照跳过乱码/空文本 %s", meta["path"])
                continue
            meta["is_current"] = _report_seq(d) == max_seq
            out.append(meta)
        return out

    return clean(history), clean(current)


def _fingerprint(
    history: list[dict[str, Any]], current: list[dict[str, Any]], refresh: bool = False
) -> str:
    doc_prints = [
        (m["path"], len(m["raw"]), m["raw"][:1200])
        for m in history + current
    ]
    # 配置（历史份数）与提示词版本一并进指纹：改了就重算，避免旧基线顺序/筛选口径残留
    raw = (
        _DRIFT_VERSION,
        _history_count(),
        doc_prints,
        [(p["id"], p["title"]) for p in RISK_FACTOR_PROBES],
    )
    return hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _cache_path(symbol: str) -> Path:
    directory = paths.cache_dir() / "narrative"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"narrative_drift_{symbol}.json"


def _read_cache(path: Path, fingerprint: str, refresh: bool) -> dict[str, Any] | None:
    if refresh or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("fingerprint") != fingerprint or not data.get("available"):
        return None
    return data


def _chat_json_retry(
    messages: list[ChatMessage], instruction: str, max_tokens: int
) -> dict[str, Any]:
    """调用模型并要求 JSON；格式非法时按「只输出 JSON」重试一次。"""
    failure = "未知错误"
    try:
        raw = chat_json(messages, max_tokens=max_tokens)
        if isinstance(raw, dict):
            return raw
        failure = "返回内容不是 JSON 对象"
    except Exception as exc:  # noqa: BLE001
        failure = f"{type(exc).__name__}: {exc}"
    retry = list(messages) + [ChatMessage("user", instruction)]
    try:
        raw = chat_json(retry, temperature=0, max_tokens=max_tokens)
        if isinstance(raw, dict):
            return raw
    except Exception as exc:  # noqa: BLE001
        failure = f"重试失败 {type(exc).__name__}: {exc}"
    raise ValueError(failure)


def _tagged_segments(meta: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in split_report(meta["raw"], _segment_chars()):
        out.append({**seg, "meta": meta})
    return out


def _map_history(
    seg: dict[str, Any], system: str, probes: list[dict[str, str]]
) -> dict[str, Any]:
    """阶段1单段：从历史原文里筛出与风险因素相关的文字并摘录。"""
    meta: dict[str, Any] = seg["meta"]
    user = json.dumps(
        {
            "risk_factors": probes,
            "document": {
                "report_label": meta["report_label"],
                "period": meta["period"],
                "source": meta["source"],
            },
            "section": seg["section"],
            "text": seg["text"],
        },
        ensure_ascii=False,
    )
    empty = {
        "hits": [], "summary": "", "section": seg["section"],
        "report_label": meta["report_label"], "period": meta["period"],
        "source": meta["source"],
    }
    try:
        raw = _chat_json_retry(
            [ChatMessage("system", system), ChatMessage("user", user)],
            "上一次输出不是合法 JSON。请严格只输出一个 JSON 对象："
            "{\"hits\": [], \"summary\": \"\"}；不要 Markdown 代码块与解释文字。",
            max_tokens=2200,
        )
        hits: list[dict[str, Any]] = []
        for item in (raw.get("hits") or [])[:10]:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote or not _quote_verified(quote, meta["raw"]):
                continue
            hits.append({
                "factor_id": str(item.get("factor_id") or "").strip(),
                "quote": quote[:500],
                "note": str(item.get("note") or "")[:120],
                "report_label": meta["report_label"],
                "period": meta["period"],
                "source": meta["source"],
                "section": str(item.get("section") or seg["section"])[:60],
            })
        return {
            **empty,
            "hits": hits,
            "summary": str(raw.get("summary") or "")[:600],
        }
    except Exception as exc:  # noqa: BLE001
        _logger.warning("跨期叙事对照·历史段失败 %s: %s", seg["section"], exc)
        return {**empty, "failed": True, "reason": str(exc)[:200]}


def reduce_baseline(
    results: list[dict[str, Any]], probes: list[dict[str, str]]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]], int]:
    """Reduce：按因素分组、逐字引文已核验、去重限流，并压缩成注入阶段2的基线文本。"""
    valid_ids = {p["id"] for p in probes}
    by_factor: dict[str, list[dict[str, Any]]] = {}
    for res in results:
        for hit in res.get("hits") or []:
            fid = hit.get("factor_id")
            if fid not in valid_ids:
                continue
            bucket = by_factor.setdefault(fid, [])
            if any(h["quote"][:80] == hit["quote"][:80] for h in bucket):
                continue
            bucket.append(hit)

    summaries: list[dict[str, str]] = []
    for res in results:
        if res.get("summary"):
            summaries.append({
                "summary": res["summary"],
                "report_label": res.get("report_label", ""),
                "period": res.get("period", ""),
            })
    rejected = sum(1 for res in results if res.get("failed"))

    # 每因素限流：文字结论优先（excerpt_quality 高者在前），同档按报告期从新到旧
    for fid, hits in by_factor.items():
        hits.sort(
            key=lambda h: (excerpt_quality(h["quote"]), str(h.get("period") or "")),
            reverse=True,
        )
        by_factor[fid] = hits[:_MAX_HISTORY_HITS_PER_FACTOR]
    return by_factor, summaries, rejected


def baseline_text(
    by_factor: dict[str, list[dict[str, Any]]],
    probes: list[dict[str, str]],
) -> str:
    """把历史基线压成有限字数的可读文本，注入阶段2。宁可少列，也不超预算。"""
    lines: list[str] = []
    total = 0
    for p in probes:
        hits = by_factor.get(p["id"]) or []
        if not hits:
            continue
        block = [f"[{p['id']} {p['title']}] 往年披露："]
        for h in hits:
            note = f"（{h['note']}）" if h.get("note") else ""
            row = f"- {h['report_label']}：“{h['quote']}”{note}"
            block.append(row)
        text = "\n".join(block)
        if total + len(text) > _MAX_BASELINE_CHARS:
            remaining = _MAX_BASELINE_CHARS - total
            if remaining > 40:
                lines.append(text[:remaining])
            break
        lines.append(text)
        total += len(text)
    return "\n".join(lines) if lines else "（往年财报中未筛出与风险因素直接相关的表述。）"


def _map_current(
    seg: dict[str, Any],
    system: str,
    probes: list[dict[str, str]],
    base_text: str,
) -> dict[str, Any]:
    """阶段2单段：基线 + 当期原文 → 对照判断，输出待核查的叙事变化线索。"""
    meta: dict[str, Any] = seg["meta"]
    user = json.dumps(
        {
            "risk_factors": probes,
            "historical_baseline": base_text,
            "document": {
                "report_label": meta["report_label"],
                "period": meta["period"],
                "source": meta["source"],
                "is_current": meta.get("is_current", False),
            },
            "section": seg["section"],
            "text": seg["text"],
        },
        ensure_ascii=False,
    )
    empty = {
        "findings": [], "summary": "", "section": seg["section"],
        "report_label": meta["report_label"], "period": meta["period"],
        "source": meta["source"],
    }
    try:
        raw = _chat_json_retry(
            [ChatMessage("system", system), ChatMessage("user", user)],
            "上一次输出不是合法 JSON。请严格只输出一个 JSON 对象："
            "{\"findings\": [], \"summary\": \"\"}；不要 Markdown 代码块与解释文字。",
            max_tokens=3200,
        )
        findings: list[dict[str, Any]] = []
        for item in (raw.get("findings") or [])[:8]:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote or not _quote_verified(quote, meta["raw"]):
                continue  # 当期引文必须在当期原文里逐字可定位
            severity = str(item.get("severity") or "low").strip().lower()
            if severity not in _SEVERITIES:
                severity = "low"
            shift = str(item.get("shift_type") or "").strip()
            if shift not in _SHIFT_TYPES:
                shift = "表述变化"
            findings.append({
                "factor_id": str(item.get("factor_id") or "").strip(),
                "title": str(item.get("title") or "")[:80],
                "severity": severity,
                "shift_type": shift,
                "judgement": str(item.get("judgement") or "")[:400],
                "quote": quote[:500],
                "baseline_ref": str(item.get("baseline_ref") or "")[:300],
                "action": str(item.get("action") or "")[:200],
                "report_label": meta["report_label"],
                "period": meta["period"],
                "source": meta["source"],
                "section": str(item.get("section") or seg["section"])[:60],
                "is_current": bool(meta.get("is_current", False)),
            })
        return {
            **empty,
            "findings": findings,
            "summary": str(raw.get("summary") or "")[:600],
        }
    except Exception as exc:  # noqa: BLE001
        _logger.warning("跨期叙事对照·当期段失败 %s: %s", seg["section"], exc)
        return {**empty, "failed": True, "reason": str(exc)[:200]}


def reduce_findings(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Reduce 当期结论：去重、每因素限流、按严重度排序，避免单一因素刷屏。"""
    valid_ids = {p["id"] for p in RISK_FACTOR_PROBES}
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for res in results:
        for f in res.get("findings") or []:
            if f.get("factor_id") not in valid_ids:
                continue
            key = f"{f['factor_id']}|{f['judgement'][:60]}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(f)
    rejected = sum(1 for res in results if res.get("failed"))

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), -excerpt_quality(f["quote"])))
    per_factor: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for f in findings:
        fid = f["factor_id"]
        if per_factor.get(fid, 0) >= _MAX_FINDINGS_PER_FACTOR:
            continue
        per_factor[fid] = per_factor.get(fid, 0) + 1
        out.append(f)
        if len(out) >= _MAX_FINDINGS:
            break
    return out, rejected


def analyze_narrative_drift(
    symbol: str,
    *,
    db_path: Path | None = None,
    refresh: bool = False,
    max_workers: int = 6,
) -> dict[str, Any]:
    """主入口：往年建基线 → 基线 + 当期对照判断 → 独立缓存。失败降级不阻断体检。"""
    if not has_live_llm():
        return empty_result(symbol, "未启用可用的 LLM")
    try:
        history, current = _load_docs_text(symbol, db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("跨期叙事对照加载文档失败 %s: %s", symbol, exc)
        return empty_result(symbol, f"财报文档加载失败：{exc}")
    if not current:
        return empty_result(symbol, "缺少当期财报文档")

    fingerprint = _fingerprint(history, current)
    cache_file = _cache_path(symbol)
    cached = _read_cache(cache_file, fingerprint, refresh)
    if cached:
        _logger.info("跨期叙事对照命中缓存 %s", symbol)
        return cached

    try:
        history_prompt = load_prompt("drift_history")
        current_prompt = load_prompt("drift_current")
    except FileNotFoundError as exc:
        return empty_result(symbol, f"提示词缺失：{exc}")

    probes = [dict(p) for p in RISK_FACTOR_PROBES]
    workers = max(1, min(max_workers, 8))

    def run_map(segments: list[dict[str, Any]], fn: Any) -> list[dict[str, Any]]:
        if not segments:
            return []
        out: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fn, seg) for seg in segments]
            for fut in as_completed(futures):
                out.append(fut.result())
        return out

    # ---------- 阶段1：往年建基线 ----------
    history_segments = [s for m in history for s in _tagged_segments(m)]
    hist_results = run_map(
        history_segments,
        lambda seg: _map_history(seg, history_prompt, probes),
    )
    by_factor, hist_summaries, hist_failed = reduce_baseline(hist_results, probes)
    base_text = baseline_text(by_factor, probes)

    # ---------- 阶段2：基线 + 当期对照判断 ----------
    current_segments = [s for m in current for s in _tagged_segments(m)]
    curr_results = run_map(
        current_segments,
        lambda seg: _map_current(seg, current_prompt, probes, base_text),
    )
    findings, curr_failed = reduce_findings(curr_results)
    summaries = [
        {"summary": r["summary"], "report_label": r["report_label"], "period": r["period"]}
        for r in curr_results if r.get("summary")
    ]

    def doc_list(metas: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [{
            "report_label": m["report_label"], "period": m["period"],
            "source": m["source"], "is_current": m.get("is_current", False),
        } for m in metas]

    output: dict[str, Any] = {
        "available": True,
        "symbol": symbol,
        "version": _DRIFT_VERSION,
        "risk_factors": probes,
        "history": {
            "documents": doc_list(history),
            "segments": len(history_segments),
            "baseline": by_factor,
            "summaries": hist_summaries,
            "summary": "；".join(s["summary"] for s in hist_summaries[:3])[:600],
        },
        "current": {
            "documents": doc_list(current),
            "segments": len(current_segments),
            "findings": findings,
            "summaries": summaries,
            "summary": "；".join(s["summary"] for s in summaries[:3])[:600],
        },
        "rejected_quotes": hist_failed + curr_failed,
        "failed_segments": hist_failed + curr_failed,
        "fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _cache_path(symbol).write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:  # noqa: BLE001
        _logger.warning("跨期叙事对照写缓存失败 %s: %s", symbol, exc)
    _logger.info(
        "跨期叙事对照完成 %s：历史 %d 份/%d 段，当期 %d 份/%d 段，%d 条线索",
        symbol, len(history), len(history_segments),
        len(current), len(current_segments), len(findings),
    )
    return output
