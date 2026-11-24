"""财报原文分段挖掘：先筛风险点，再带风险点逐段摘录+总结，汇总后供最终分析。

与 _evidence.py（关键词检索）的区别：
- _evidence 用硬编码关键词/章节匹配，召回受措辞与章节位置限制；
- 本模块用 LLM 语义理解，段落遍历整份财报（不挑章节），按风险点找证据，
  还能发现扫描器未覆盖的新线索。

设计约束：
1. **分段不切坏结构**：先按「第X节」切；单节超预算再按自然段落/表格边界二次切；
   只有极端大段才在换行处硬切，绝不拦腰截断一句话。
2. **摘录零改写**：原文摘录逐字来自财报，模型只负责挑，不负责改。
3. **年份对齐**：只用风险点所属报告年份的财报（太久远的解释意义不大）。
4. **可审计**：每条摘录带来源文档、章节、报告期。
5. **磁盘缓存**：按"财报文本指纹 + 风险点指纹 + 版本"失效，避免重复花钱。

失败一律降级返回空，不阻断体检主流程。
"""

from __future__ import annotations

import hashlib
import json
import re
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
from ._quality import excerpt_quality, is_numeric_quote

_logger = get_logger(__name__)

# 挖掘结果结构与提示词变化时递增，触发缓存失效
_MINING_VERSION = 11

# 单段模型输出非法 JSON 时的重试指令：只要求格式，不补充任何内容。
_JSON_RETRY_INSTRUCTION = (
    "上一次输出不是合法 JSON。请严格只输出一个 JSON 对象，"
    "不要 Markdown 代码块、不要任何解释文字；"
    "字段保持 excerpts / summary / new_findings / factor_hits。"
    "若本段确实没有可用内容，就输出 "
    "{\"excerpts\": [], \"summary\": \"\", \"new_findings\": [], \"factor_hits\": []}。"
)

_MAX_FACTOR_HITS_PER_SEGMENT = 12
_MAX_VERIFIED_QUOTES_PER_FACTOR = 6

# 同时通读的年报份数：最新一份为主、其余年份佐证判断。
# 通过 narrative.mining_report_count 配置，默认 2（当期 + 上年）。
def _report_count() -> int:
    try:
        configured = int(load_settings().get("narrative.mining_report_count", 2))
    except (TypeError, ValueError):
        configured = 2
    return max(1, min(configured, 4))


# 同时通读的最新季报份数：季报反映当下经营，年报权威但相对滞后。
# 通过 narrative.mining_quarterly_count 配置，默认 3（最近三个季度）；0 表示关闭。
def _quarterly_count() -> int:
    try:
        configured = int(load_settings().get("narrative.mining_quarterly_count", 3))
    except (TypeError, ValueError):
        configured = 3
    return max(0, min(configured, 4))


# 季报文档类型与期序：期序用于报告期排序（同一年内 Q1 < H1 < Q3 < 年报）
_QUARTERLY_TYPES: tuple[tuple[str, int, str], ...] = (
    ("q1_report", 1, "一季报"),
    ("semi_report", 2, "中报"),
    ("q3_report", 3, "三季报"),
)
_ANNUAL_TYPE = "annual_report"

# 段落切分目标字数。留出预算给提示词与风险点列表，取输入软预算的 60%。
def _segment_chars() -> int:
    try:
        configured = int(load_settings().get("narrative.mining_segment_chars", 7000))
    except (TypeError, ValueError):
        configured = 7000
    return max(2000, min(configured, int(input_budget() * 0.6)))


_SECTION_RE = re.compile(r"^\s*第[一二三四五六七八九十百]+节\b")
# 表格边界：PDF 提取后表格多以「项目/科目名 + 数字行」成组出现，空行是天然分隔
_MAX_FINDINGS = 5


def _split_sections(text: str) -> list[tuple[str, str]]:
    """先按「第X节」标题切成 (章节名, 正文)。无章节标记时整篇作为一节。"""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_name = "正文"
    buf: list[str] = []
    for line in lines:
        if _SECTION_RE.match(line) and len(line.strip()) < 40:
            if buf:
                sections.append((current_name, "\n".join(buf)))
            current_name = line.strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((current_name, "\n".join(buf)))
    return [(name, body) for name, body in sections if body.strip()]


def _split_by_paragraphs(text: str, max_chars: int) -> list[str]:
    """在章节内按空行/自然段落切，尽量不破坏表格与句子结构。"""
    blocks = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # 单个 block 就超预算：按换行切；仍超则最后在句号处硬切（保句不保段）
        if len(block) > max_chars:
            if buf:
                chunks.append("\n".join(buf))
                buf, size = [], 0
            chunks.extend(_split_large_block(block, max_chars))
            continue
        if buf and size + len(block) + 2 > max_chars:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(block)
        size += len(block) + 2
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _split_long_line(line: str, max_chars: int) -> list[str]:
    """单行超长：在中文标点处切，绝不切在一句话中间。"""
    out: list[str] = []
    rest = line
    while len(rest) > max_chars:
        cut = max(
            rest.rfind("。", 0, max_chars),
            rest.rfind("；", 0, max_chars),
            rest.rfind("，", 0, max_chars),
        )
        if cut < max_chars // 2:
            cut = max_chars  # 找不到标点才硬切
        out.append(rest[: cut + 1])
        rest = rest[cut + 1 :]
    if rest:
        out.append(rest)
    return out


def _split_large_block(block: str, max_chars: int) -> list[str]:
    """极端大块的兜底切分：先按行，行内仍超长再按标点，绝不切坏结构。"""
    lines = block.splitlines()
    if len(lines) == 1:
        return _split_long_line(block, max_chars)
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if len(line) > max_chars:
            # 单行（如超长表格/段落）本身超预算：先结算缓冲，再单独切这一行
            if buf:
                out.append("\n".join(buf))
                buf, size = [], 0
            out.extend(_split_long_line(line, max_chars))
            continue
        if buf and size + len(line) + 1 > max_chars:
            out.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return out


def split_report(text: str, max_chars: int | None = None) -> list[dict[str, str]]:
    """财报分段主函数：先章节、超了再自然段落/表格边界，输出带章节标签的段。"""
    max_chars = max_chars or _segment_chars()
    segments: list[dict[str, str]] = []
    for section_name, body in _split_sections(text):
        pieces = _split_by_paragraphs(body, max_chars) if len(body) > max_chars else [body]
        for idx, piece in enumerate(pieces):
            segments.append(
                {
                    "index": len(segments) + 1,
                    "section": section_name if len(pieces) == 1 else f"{section_name}（{idx + 1}/{len(pieces)}）",
                    "text": piece.strip(),
                }
            )
    return [s for s in segments if s["text"]]


def _signal_briefs(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("id", "title", "category", "severity", "period", "facts", "trigger")
    return [{k: s[k] for k in keys if k in s} for s in signals]


def _doc_year(d: dict[str, Any]) -> int:
    try:
        return int(str(d.get("period") or "0")[:4])
    except ValueError:
        return 0


_QUARTERLY_SEQ = {t: s for t, s, _ in _QUARTERLY_TYPES}


def _report_seq(d: dict[str, Any]) -> int:
    """报告期排序分 = 年份×10 + 期序（Q1=1/H1=2/Q3=3/年报=4）。

    越大越新。用于让"最新季报"排在年报之前，反映当下；
    同时避免同一年份的季报被年报盖过时失去时效信息。
    """
    seq = _QUARTERLY_SEQ.get(str(d.get("doc_type")), 4)
    return _doc_year(d) * 10 + seq


def _report_label(d: dict[str, Any]) -> str:
    """人类可读的报告期标签，如「2026 三季报」「2025 年报」。"""
    year = _doc_year(d)
    doc_type = str(d.get("doc_type"))
    for t, _, label in _QUARTERLY_TYPES:
        if doc_type == t:
            return f"{year} {label}"
    return f"{year} 年报"


def _pick_documents(
    docs: list[dict[str, Any]],
    years: set[str],
    count: int | None = None,
    quarterly_count: int | None = None,
) -> list[dict[str, Any]]:
    """分层选文档：年报组（权威/拐点归因）+ 最新季报组（反映当下）。

    - 年报组：风险点年份优先（当期），再按年份就近补足历史作佐证，
      共 ``count`` 份（默认 2）。
    - 季报组：全库中报告期最新的 ``quarterly_count`` 份季报（默认 3），
      如 2026 三季报/中报/一季报；它们比最新年报更能反映当下经营。

    返回顺序按 ``_report_seq`` 降序（最新在前）：季报 -> 当期年报 -> 历史年报。
    """
    annuals = [d for d in docs if d.get("doc_type") == _ANNUAL_TYPE and _text_path(d)]
    quarterlies = [
        d for d in docs
        if str(d.get("doc_type")) in _QUARTERLY_SEQ and _text_path(d)
    ]
    count = count or _report_count()
    quarterly_count = _quarterly_count() if quarterly_count is None else quarterly_count

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(d: dict[str, Any]) -> None:
        key = str(d.get("local_path") or d.get("id") or d.get("title"))
        if key in seen:
            return
        seen.add(key)
        selected.append(d)

    # 1) 年报：风险点年份命中的排前，再按年份就近补齐历史佐证，总数不超过 count
    annuals.sort(key=lambda d: -_doc_year(d))

    def annual_count() -> int:
        return sum(1 for x in selected if x.get("doc_type") == _ANNUAL_TYPE)

    for year in sorted((y for y in years if y.isdigit()), key=lambda y: -int(y)):
        if annual_count() >= count:
            break
        for d in annuals:
            if annual_count() >= count:
                break
            if str(d.get("period")) == year:
                add(d)
    for d in annuals:
        if annual_count() >= count:
            break
        add(d)

    # 2) 最新季报：按报告期排序分取最近的几份
    quarterlies.sort(key=lambda d: -_report_seq(d))
    for d in quarterlies[:quarterly_count]:
        add(d)

    # 3) 统一按报告期排序分降序（最新在前）
    selected.sort(key=lambda d: -_report_seq(d))
    return selected


def _risk_years(signals: list[dict[str, Any]]) -> set[str]:
    years: set[str] = set()
    for s in signals:
        m = re.search(r"(20\d{2})", str(s.get("period") or ""))
        if m:
            years.add(m.group(1))
    return years


def _as_bool(value: Any) -> bool:
    """模型可能给布尔、字符串或数字；只有明确肯定才算建议触发。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "是", "y")


def _quote_verified(quote: str, raw_text: str) -> bool:
    """引文逐字核验：忽略空白差异后必须能在归档原文中定位。

    这是 documentary 因子防幻觉的第一道闸：模型改写、概括或凭空生成的摘录
    一律剔除，不进入 analysis 层的触发确认，也不出现在报告证据里。
    """
    needle = re.sub(r"\s+", "", str(quote or ""))
    if len(needle) < 8:
        return False
    haystack = re.sub(r"\s+", "", str(raw_text or ""))
    return needle in haystack


def _fingerprint(
    docs_text: list[tuple[str, str]],
    signals: list[dict[str, Any]],
    factor_briefs: list[dict[str, Any]] | None = None,
) -> str:
    """指纹纳入财报文本、风险点清单与因子任务单，任一变化都触发重算。"""
    doc_prints = [(path, len(text), text[:1200]) for path, text in docs_text]
    raw = _MINING_VERSION, doc_prints, _signal_briefs(signals), factor_briefs or []
    blob = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _cache_path(symbol: str) -> Path:
    directory = paths.cache_dir() / "narrative"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"report_mining_{symbol}.json"


def _read_cache(path: Path, fingerprint: str, refresh: bool) -> dict[str, Any] | None:
    if refresh or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    return data


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _chat_segment_json(messages: list[ChatMessage], max_tokens: int = 2600) -> dict[str, Any]:
    """单段挖掘的 JSON 调用：非法 JSON 时用「只输出 JSON」指令重试一次。

    不本地拼接/补全截断内容（会捏造摘录）；仍失败就抛错，由调用方把该段
    记入 failed_segments，避免线索静默丢失却显示成「通读无发现」。
    """

    failure = "未知错误"
    try:
        raw = chat_json(messages, max_tokens=max_tokens)
        if isinstance(raw, dict):
            return raw
        failure = "返回内容不是 JSON 对象"
    except Exception as exc:  # noqa: BLE001
        failure = f"{type(exc).__name__}: {exc}"
    retry = list(messages) + [ChatMessage("user", _JSON_RETRY_INSTRUCTION)]
    try:
        raw = chat_json(retry, temperature=0, max_tokens=max_tokens)
        if isinstance(raw, dict):
            _logger.info("财报分段挖掘 JSON 重试成功")
            return raw
        failure = "重试后仍不是 JSON 对象"
    except Exception as exc:  # noqa: BLE001
        failure = f"重试失败 {type(exc).__name__}: {exc}"
    raise ValueError(failure)


def _map_segment(
    segment: dict[str, str],
    briefs: list[dict[str, Any]],
    system: str,
    doc_meta: dict[str, str] | None = None,
    factor_briefs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """单段 Map：带风险点 + 因子任务单，让模型摘录原文 + 总结 + 挂牌。

    doc_meta 记录本段来自哪份财报（source/period/is_current），写进每条结果，
    让多份年报的证据能各自溯源并区分"当期 vs 历史佐证"。
    ``factor_briefs`` 是注册表生成的取证任务单：一次通读同时给所有原文因子
    挂牌，不按因子数放大 LLM 调用；模型只给引文与触发建议，不判最终触发。
    """
    doc_meta = doc_meta or {}
    user = json.dumps(
        {
            "section": segment["section"], "text": segment["text"],
            "risk_signals": briefs,
            "factor_probes": factor_briefs or [],
            "document": {
                "source": doc_meta.get("source", ""),
                "period": doc_meta.get("period", ""),
                "report_label": doc_meta.get("report_label", ""),
                "doc_type": doc_meta.get("doc_type", ""),
            },
        },
        ensure_ascii=False,
    )
    empty = {
        "excerpts": [], "summary": "", "new_findings": [], "factor_hits": [],
        "section": segment["section"],
        "source": doc_meta.get("source", ""), "period": doc_meta.get("period", ""),
        "is_current": doc_meta.get("is_current", False),
        "report_label": doc_meta.get("report_label", ""),
        "doc_type": doc_meta.get("doc_type", ""),
        "seq": doc_meta.get("seq", 0),
    }
    try:
        raw = _chat_segment_json(
            [ChatMessage("system", system), ChatMessage("user", user)],
            max_tokens=2600,
        )
        excerpts = []
        for item in (raw.get("excerpts") or [])[:20]:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            excerpts.append(
                {
                    "signal_id": str(item.get("signal_id") or "").strip(),
                    "quote": quote[:500],
                    "section": str(item.get("section") or segment["section"])[:60],
                    "why": str(item.get("why") or "")[:80],
                    "source": doc_meta.get("source", ""),
                    "period": doc_meta.get("period", ""),
                    "is_current": doc_meta.get("is_current", False),
                    "report_label": doc_meta.get("report_label", ""),
                    "doc_type": doc_meta.get("doc_type", ""),
                    "seq": doc_meta.get("seq", 0),
                }
            )
        findings = []
        for item in (raw.get("new_findings") or [])[:_MAX_FINDINGS]:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description") or "").strip()
            quote = str(item.get("quote") or "").strip()
            if not desc or not quote:  # 新线索必须附原文，否则不采信
                continue
            findings.append(
                {
                    "description": desc[:200],
                    "quote": quote[:500],
                    "section": str(item.get("section") or segment["section"])[:60],
                    "source": doc_meta.get("source", ""),
                    "period": doc_meta.get("period", ""),
                    "is_current": doc_meta.get("is_current", False),
                    "report_label": doc_meta.get("report_label", ""),
                    "doc_type": doc_meta.get("doc_type", ""),
                    "seq": doc_meta.get("seq", 0),
                }
            )
        factor_hits = []
        for item in (raw.get("factor_hits") or [])[:_MAX_FACTOR_HITS_PER_SEGMENT]:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("factor_id") or "").strip()
            quote = str(item.get("quote") or "").strip()
            if not fid or not quote:  # 因子挂牌同样必须附原文摘录
                continue
            factor_hits.append(
                {
                    "factor_id": fid,
                    "quote": quote[:500],
                    "reason": str(item.get("reason") or "")[:120],
                    "suggested": _as_bool(item.get("suggested")),
                    "section": str(item.get("section") or segment["section"])[:60],
                    "source": doc_meta.get("source", ""),
                    "period": doc_meta.get("period", ""),
                    "is_current": doc_meta.get("is_current", False),
                    "report_label": doc_meta.get("report_label", ""),
                    "doc_type": doc_meta.get("doc_type", ""),
                    "seq": doc_meta.get("seq", 0),
                }
            )
        return {
            "excerpts": excerpts,
            "summary": str(raw.get("summary") or "")[:600],
            "new_findings": findings,
            "factor_hits": factor_hits,
            "section": segment["section"],
            "source": doc_meta.get("source", ""),
            "period": doc_meta.get("period", ""),
            "is_current": doc_meta.get("is_current", False),
            "report_label": doc_meta.get("report_label", ""),
            "doc_type": doc_meta.get("doc_type", ""),
            "seq": doc_meta.get("seq", 0),
        }
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财报分段挖掘失败 %s: %s", segment["section"], exc)
        return {**empty, "parse_failed": True, "failure": str(exc)[:200]}


def _dedup(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        sig = f"{item.get('signal_id', '')}|{item.get(key, '')[:80]}"
        if sig in seen:
            continue
        seen.add(sig)
        out.append(item)
    return out


def _pick_diverse(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按信息密度排序选取，但限制单一报告期占比，避免一份季报的表格数字行刷屏。

    先按 (信息密度, 时效分) 降序；每个 report_label 最多取 ``per_source`` 条，
    保证最新季报、当期年报、历史佐证都有代表性证据进入下游。
    """
    if not items:
        return []
    per_source = max(2, (limit + 1) // 2)
    ranked = sorted(
        items,
        key=lambda x: (excerpt_quality(str(x.get("quote", ""))), x.get("seq", 0)),
        reverse=True,
    )
    chosen: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for item in ranked:
        label = str(item.get("report_label") or item.get("period") or "")
        if used.get(label, 0) >= per_source:
            continue
        used[label] = used.get(label, 0) + 1
        chosen.append(item)
        if len(chosen) >= limit:
            break
    # 不强行凑满：宁可少几条，也要守住"单一报告期不刷屏"的多样性约束。
    # （下游 _merge_evidence 还会再合并关键词兜底证据，名额不会被浪费。）
    return chosen


def mine_report_evidence(
    symbol: str,
    signals: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
    refresh: bool = False,
    max_workers: int = 6,
    factor_briefs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """主入口：风险点 + 因子任务单 + 财报 → 分段摘录/总结/挂牌 → 证据包。

    返回结构：
    {
      "signal_evidence": {signal_id: [{"quote","section","source","period","why","is_current"}]},
      "summaries": [{"section","text","source","period"}],
      "new_findings": [{"description","quote","section","source","period","is_current"}],
      "factor_evidence": {factor_id: {"available", "verified_hits", "rejected_quotes", "documents"}},
      "source_doc": "最新年报（当期）", "period": "2025",
      "source_docs": [{"title","period","is_current"}], "segments": N,
      "failed_segments": [{"section","source","period","reason"}],
      "fingerprint": "...", "generated_at": "..."
    }
    多份年报：当期（最新）为主，其余年份佐证，每条证据都标注来源与是否当期。
    ``factor_briefs`` 由注册表生成：有了它，即使数值风险点为空也会通读财报，
    让「规则驱动原文取证」不再被阈值门控；引文逐字核验只在本模块（持有原文）
    完成，analysis 层只消费纯 dict。失败返回空结构，由叙述层降级。
    """
    empty: dict[str, Any] = {
        "signal_evidence": {}, "summaries": [], "new_findings": [],
        "factor_evidence": {},
        "source_doc": "", "period": "", "source_docs": [], "segments": 0,
        "failed_segments": [],
    }
    # 门控放宽：风险点为空但带了因子任务单时仍通读，这正是「用规则去读财报」
    # 与旧「阈值触发后才读财报」的区别。两者都没有时才不花钱调 LLM。
    if (not signals and not factor_briefs) or not has_live_llm():
        return empty
    try:
        docs = _load_documents(symbol, db_path)
        years = _risk_years(signals)
        selected = _pick_documents(docs, years)
        if not selected:
            return empty
        # 逐份读取文本；当期 = 报告期排序分最高的那份（通常是最新季报）。
        # 季报反映当下、年报权威归因，全部纳入证据，各自带报告类型标签。
        docs_text: list[tuple[dict[str, Any], str, str, str, str, bool, str]] = []
        max_seq = max((_report_seq(d) for d in selected), default=0)
        for d in selected:
            txt_path = _text_path(d)
            if txt_path is None:
                continue
            raw = txt_path.read_text(encoding="utf-8", errors="ignore")
            # 乱码文本层（CID 无 ToUnicode）绝不能进 LLM：模型会对着
            # 「㛮暈⛚㕾」生成看似合理的中文结论，属于幻觉式解读。
            if is_garbled_text(raw):
                _logger.warning("跳过 CID 乱码财报文本 %s", txt_path)
                continue
            period = str(d.get("period") or "")
            source = str(d.get("title") or f"{period} 年年度报告")
            label = _report_label(d)
            docs_text.append((
                d, str(txt_path), raw, source, period,
                _report_seq(d) == max_seq, label,
            ))
        if not docs_text:
            return empty
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财报挖掘加载文档失败 %s: %s", symbol, exc)
        return empty

    # fingerprint 用 (path, raw) 列表
    path_text = [(p, raw) for _, p, raw, _, _, _, _ in docs_text]
    fingerprint = _fingerprint(path_text, signals, factor_briefs)
    cache_file = _cache_path(symbol)
    cached = _read_cache(cache_file, fingerprint, refresh)
    if cached:
        _logger.info("财报挖掘命中缓存 %s（%d 段 / %d 份年报）",
                     symbol, cached.get("segments", 0), len(cached.get("source_docs", [])))
        return cached

    # 每份财报按自身结构分段，打上来源标签（含报告类型与时效分）
    tagged_segments: list[tuple[dict[str, str], dict[str, Any]]] = []
    for d, _, raw, source, period, is_current, label in docs_text:
        meta = {
            "source": source,
            "period": period,
            "is_current": is_current,
            "report_label": label,
            "doc_type": str(d.get("doc_type") or ""),
            "seq": _report_seq(d),
        }
        for seg in split_report(raw):
            tagged_segments.append((seg, meta))
    if not tagged_segments:
        return empty
    briefs = _signal_briefs(signals)
    try:
        system = load_prompt("report_mining")
    except FileNotFoundError:
        return empty

    # 跨所有年报并发处理各段：网络等待占大头
    results: list[dict[str, Any]] = []
    workers = max(1, min(max_workers, len(tagged_segments)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_map_segment, seg, briefs, system, meta, factor_briefs)
            for seg, meta in tagged_segments
        ]
        for future in as_completed(futures):
            results.append(future.result())

    # 解析失败（含重试失败）的段落显式记账，避免「通读无发现」掩盖缺失
    failed_segments = [
        {
            "section": str(res.get("section") or ""),
            "source": str(res.get("source") or ""),
            "period": str(res.get("period") or ""),
            "reason": str(res.get("failure") or ""),
        }
        for res in results if res.get("parse_failed")
    ]
    if failed_segments:
        _logger.warning(
            "财报挖掘 %s：%d / %d 段返回非法 JSON 且重试未成功，已在报告中声明",
            symbol, len(failed_segments), len(tagged_segments),
        )

    # 来源清单：按报告期时效分降序（最新季报 -> 当期年报 -> 历史佐证）
    source_docs = [
        {
            "title": source, "period": period, "is_current": is_current,
            "report_label": label, "doc_type": str(d.get("doc_type") or ""),
            "seq": _report_seq(d),
        }
        for d, _, _, source, period, is_current, label in docs_text
    ]
    source_docs.sort(key=lambda x: -x["seq"])
    # 权威锚点仍是最新年报（归因详细、口径稳定）；primary_notice 单独标注最新季报。
    annual_docs = [x for x in source_docs if x["doc_type"] == _ANNUAL_TYPE]
    anchor = annual_docs[0] if annual_docs else source_docs[0]
    source_doc = anchor["title"]
    period = anchor["period"]
    latest_report = source_docs[0]["report_label"]
    latest_period = source_docs[0]["period"]

    signal_evidence: dict[str, list[dict[str, Any]]] = {}
    summaries: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []

    for res in results:
        if res.get("summary"):
            summaries.append({
                "section": res["section"], "text": res["summary"],
                "source": res.get("source", ""), "period": res.get("period", ""),
                "report_label": res.get("report_label", ""),
                "seq": res.get("seq", 0),
            })
        for f in res.get("new_findings", []):
            findings.append({
                **f,
                "source": f.get("source") or res.get("source", ""),
                "period": f.get("period") or res.get("period", ""),
                "is_current": f.get("is_current", res.get("is_current", False)),
                "report_label": f.get("report_label") or res.get("report_label", ""),
                "seq": f.get("seq", res.get("seq", 0)),
            })
        for ex in res.get("excerpts", []):
            sid = ex["signal_id"]
            if not sid:
                continue
            signal_evidence.setdefault(sid, []).append({
                "quote": ex["quote"],
                "section": ex.get("section") or res["section"],
                "source": ex.get("source") or res.get("source", ""),
                "period": ex.get("period") or res.get("period", ""),
                "why": ex.get("why", ""),
                "is_current": ex.get("is_current", res.get("is_current", False)),
                "report_label": ex.get("report_label") or res.get("report_label", ""),
                "seq": ex.get("seq", res.get("seq", 0)),
            })

    # 原文因子挂牌汇总：只对注册表下发过的因子记账。引文逐字核验放在本模块
    # （只有这里持有归档全文），无法定位的摘录直接剔除并计数，绝不进 analysis。
    # analysis 层拿到的 verified_hits 已全部可复核，触发权始终在代码。
    factor_evidence: dict[str, Any] = {}
    probe_ids = {
        str(b.get("factor_id") or "")
        for b in (factor_briefs or []) if b.get("factor_id")
    }
    if probe_ids:
        doc_raw_by_source = {
            source: raw for _, _, raw, source, _, _, _ in docs_text
        }
        doc_labels = [str(x.get("title") or "") for x in source_docs[:4]]
        for fid in probe_ids:
            factor_evidence[fid] = {
                "available": bool(doc_raw_by_source),
                "verified_hits": [],
                "rejected_quotes": 0,
                "documents": doc_labels,
            }
        for res in results:
            raw_text = doc_raw_by_source.get(str(res.get("source") or ""), "")
            for hit in res.get("factor_hits") or []:
                fid = str(hit.get("factor_id") or "")
                bundle = factor_evidence.get(fid)
                if bundle is None:
                    continue
                if not _quote_verified(str(hit.get("quote") or ""), raw_text):
                    bundle["rejected_quotes"] += 1
                    continue
                bundle["verified_hits"].append({
                    "quote": str(hit.get("quote") or "")[:500],
                    "reason": str(hit.get("reason") or ""),
                    "suggested": bool(hit.get("suggested")),
                    "section": hit.get("section") or res.get("section", ""),
                    "source": hit.get("source") or res.get("source", ""),
                    "period": hit.get("period") or res.get("period", ""),
                    "report_label": hit.get("report_label") or res.get("report_label", ""),
                    "is_current": hit.get("is_current", res.get("is_current", False)),
                })
        for bundle in factor_evidence.values():
            bundle["verified_hits"] = _dedup(
                bundle["verified_hits"], "quote"
            )[:_MAX_VERIFIED_QUOTES_PER_FACTOR]

    # 选取：信息密度优先 + 单一报告期限流。既让年报的"变动原因说明"解释性
    # 短句排前面，又保证最新季报的当下证据有代表，不被一方刷屏。
    for sid in signal_evidence:
        deduped = _dedup(signal_evidence[sid], "quote")
        signal_evidence[sid] = _pick_diverse(deduped, 8)
    summaries = sorted(
        _dedup(summaries, "text"), key=lambda x: -x.get("seq", 0)
    )[:12]
    deduped_findings = _dedup(findings, "quote")
    # 质量优先：带文字结论的线索排前，纯数字表格行沉底；同档按报告期时效。
    # 这避免季报的裸数字行占满 5 个名额，把年报里带公司说明的实质线索挤掉。
    deduped_findings.sort(
        key=lambda x: (
            is_numeric_quote(str(x.get("quote", ""))),
            -excerpt_quality(str(x.get("quote", ""))),
            -x.get("seq", 0),
        )
    )
    # 每份财报最多 2 条：既让年报带文字结论的实质线索优先，
    # 又保证最新季报的当下线索（即便证据是数字行）至少有一条进入，
    # 避免"文字线索全是年报、当下视角缺失"或反过来被季报数字行刷屏。
    findings: list[dict[str, Any]] = []
    per_doc: dict[str, int] = {}
    for f in deduped_findings:
        label = str(f.get("report_label") or f.get("period") or "")
        if per_doc.get(label, 0) >= 2:
            continue
        per_doc[label] = per_doc.get(label, 0) + 1
        f["quote_kind"] = "numeric" if is_numeric_quote(str(f.get("quote", ""))) else "text"
        findings.append(f)
        if len(findings) >= _MAX_FINDINGS:
            break

    output: dict[str, Any] = {
        "signal_evidence": signal_evidence,
        "summaries": summaries,
        "new_findings": findings,
        "factor_evidence": factor_evidence,
        "source_doc": source_doc,
        "period": period,
        "latest_report": latest_report,
        "latest_period": latest_period,
        "source_docs": source_docs,
        "segments": len(tagged_segments),
        "failed_segments": failed_segments,
        "fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_cache(cache_file, output)
    factor_hit_count = sum(
        1 for b in factor_evidence.values() if b.get("verified_hits")
    )
    _logger.info(
        "财报挖掘完成 %s：%d 份年报 / %d 段（%d 段解析失败），"
        "%d 个风险点拿到证据，%d 条新线索，%d 个原文因子拿到已核验引文",
        symbol, len(source_docs), len(tagged_segments), len(failed_segments),
        len(signal_evidence), len(findings), factor_hit_count,
    )
    return output
