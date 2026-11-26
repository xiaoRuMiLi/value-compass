"""桥接资产质量证据抽取：从财报原文切出投资资产构成段落供 LLM 定折价率。

设计约束（与 _evidence.py 一脉相承）：
1. **只检索、不判断**：从已归档财报 .txt 中按关键词切出原文段落，
   是否打折、打几折交给 LLM 结合 facts 判断。本模块不产生任何估值结论。
2. **离线可用、可溯源**：只做关键词匹配 + 段落切片，每条证据带来源文档与命中关键词。
3. **报告期对齐**：优先最新年报（投资组合构成披露最全），无年报再退半年报。
4. **预算可控**：片段数量与长度由调用方配置，避免挤占 LLM 上下文。

抽取目标（对应 FCFF 桥接的三档资产）：
- 公允价值类：以公允价值计量的金融资产、第三级金融工具、无公开报价/流通性折让
- 权益类：联营/合营公司投资、上市/非上市投资公司权益、投资物业
- 现金类：现金及等价物、定期存款、受限/质押存款
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fundamental.storage import db, repository
from fundamental.utils.logging import get_logger

from ._evidence import _load_documents, _text_path, parse_code

_logger = get_logger(__name__)

# 关键词 → 归类标签。命中任一关键词即收录该段落；同一段落可归多类。
# 同时覆盖 A 股简体与港股繁体措辞。
_ASSET_TERMS: dict[str, tuple[str, ...]] = {
    "公允价值类": (
        "以公允价值计量", "以公允價值計量", "公允价值计量的金融资产",
        "公允價值計量的金融資產", "第三级金融工具", "第三級金融工具",
        "第三層級", "不可观察输入", "不可觀察輸入", "缺乏市场流通性的折让",
        "缺乏市場流通性的折讓", "无公开市场报价", "並無公開市場報價",
        "交易性金融资产", "交易性金融資產", "其他金融资产", "其他金融資產",
    ),
    "权益类": (
        "对联营公司的投资", "於聯營公司的投資", "联营及合营", "聯營及合營",
        "合营公司的投资", "合營公司的投資", "上市投资公司", "上市投資公司",
        "非上市投资公司", "非上市投資公司", "投资组合", "投資組合",
        "投资物业", "投資物業", "长期股权投资", "長期股權投資",
        "权益法计量", "權益法計量",
    ),
    "现金类": (
        "现金及现金等价物", "現金及現金等價物", "定期存款", "短期存款",
        "受限资金", "受限制資金", "受限存款", "质押存款", "質押存款",
        "货币资金", "貨幣資金", "现金净额", "現金淨額",
    ),
}

# 段落里出现这些"资产质量信号"时优先保留，它们直接决定折价率高低。
_QUALITY_SIGNALS = (
    "上市", "非上市", "公允价值", "公允價值", "账面价值", "賬面價值",
    "无公开报价", "並無公開市場報價", "第三级", "第三級", "折让", "折讓",
    "流通性", "流动性", "流動性", "受限", "质押", "質押", "减值", "減值",
)


def _split_paragraphs(text: str) -> list[str]:
    """把财报文本切成段落，剔除纯页码/空行，合并断行。"""
    lines = [line.strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        if not line or re.fullmatch(r"\d+\s*/\s*\d+|\d+", line):
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        paragraphs.append(" ".join(buf))
    return [p for p in paragraphs if len(p) >= 12]


def _classify(paragraph: str) -> list[str]:
    """返回该段落命中的资产类别标签。"""
    return [label for label, terms in _ASSET_TERMS.items() if any(t in paragraph for t in terms)]


def _quality_score(paragraph: str) -> int:
    """资产质量信号词命中数，用于排序时把最有价值的段落排前面。"""
    return sum(1 for signal in _QUALITY_SIGNALS if signal in paragraph)


def asset_quality_digest(
    symbol: str,
    db_path: Path | None = None,
    max_chars: int = 2200,
    max_snippets: int = 6,
) -> list[dict[str, str]]:
    """从最新财报原文抽取资产构成证据，每条带 category/source/keywords/text。

    只返回原文片段，不做任何估值判断；无法读取财报时返回空列表，调用方按缺省口径处理。
    """
    try:
        docs = _load_documents(symbol, db_path)
    except Exception:  # noqa: BLE001 - 证据缺失不应阻断校准
        return []
    if not docs:
        return []

    # 年报优先（投资组合构成披露最全），其次半年报；只取最近两份避免噪声。
    ranked = sorted(
        docs,
        key=lambda d: (0 if "annual" in str(d.get("doc_type") or "").lower() else 1,
                       str(d.get("period") or "")),
        reverse=False,
    )
    ranked = list(reversed(ranked))[:2]

    snippets: list[dict[str, str]] = []
    for doc in ranked:
        txt_path = _text_path(doc)
        if txt_path is None:
            continue
        try:
            text = txt_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        label = str(doc.get("title") or doc.get("period") or txt_path.stem)
        for para in _split_paragraphs(text):
            categories = _classify(para)
            if not categories:
                continue
            score = _quality_score(para)
            if score == 0 and len(para) > 300:
                # 无质量信号的长段落往往是目录/表格堆砌，跳过
                continue
            snippet = para[:420]
            snippets.append({
                "category": "/".join(categories),
                "source": label,
                "keywords": ",".join(
                    term for terms in _ASSET_TERMS.values() for term in terms if term in para
                )[:80],
                "text": snippet,
                "_score": str(score),
            })

    # 质量信号多的排前面，同类去重，控制总量与总字数
    snippets.sort(key=lambda s: int(s["_score"]), reverse=True)
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    total = 0
    for s in snippets:
        key = s["text"][:80]
        if key in seen:
            continue
        seen.add(key)
        if total + len(s["text"]) > max_chars:
            continue
        result.append({k: v for k, v in s.items() if k != "_score"})
        total += len(s["text"])
        if len(result) >= max_snippets:
            break
    return result
