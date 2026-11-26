"""财报原文阅读服务：按文档 ID 提供分段全文、定位与元数据。

安全边界：只接受数据库登记的文档 ID，不接收用户传入的文件路径；
对外响应一律不包含 local_path / sha256 等服务端路径与校验信息。
全文来自归档 PDF 的同名 .txt（由 utils.pdf 抽取），只读，不修改原文。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from fundamental.storage import db, repository

# 章节标题：中文财报的常见层级。行首匹配，避免把正文里的「第一节」短语误判成标题。
# 纯数字行（页码、表格单元格）不算标题；编号必须带点或中文序号。
_HEADING = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百零〇\d]+[章节篇]"
    r"|[一二三四五六七八九十]+、"
    r"|\d+(?:\.\d+){1,2}(?=\s|[、.：:])"
    r")"
)
# 附注类标题单独兜底（「合并财务报表项目注释」等不一定带编号）。
_NOTE_HEADING = re.compile(r"^\s*(?:.*财务报表附注|.*项目注释|.*公司基本情况)\s*$")

_DOC_COLUMNS = (
    "SELECT d.id, d.doc_type, d.period, d.title, d.source_url, d.local_path, "
    "d.file_size, d.status, s.code, s.market "
    "FROM documents d JOIN stocks s ON s.id = d.stock_id WHERE d.id = ?"
)


def _doc_row(doc_id: int, conn: Any) -> dict[str, Any]:
    row = repository.fetch_one(conn, _DOC_COLUMNS, (doc_id,))
    if row is None:
        raise LookupError("财报文档不存在")
    return dict(row)


def _text_path(row: dict[str, Any]) -> Path | None:
    local = row.get("local_path")
    if not local:
        return None
    path = Path(local).with_suffix(".txt")
    return path if path.is_file() else None


def metadata(doc_id: int, conn: Any) -> dict[str, Any]:
    """文档元数据（脱敏）：不含服务端路径、不含 sha256。"""
    row = _doc_row(doc_id, conn)
    txt = _text_path(row)
    pdf = Path(row["local_path"]) if row.get("local_path") else None
    return {
        "doc_id": row["id"],
        "symbol": f"{row['code']}.{row['market']}",
        "doc_type": row["doc_type"],
        "period": row["period"],
        "title": row["title"],
        "source_url": row["source_url"],
        "file_size": row["file_size"],
        "status": row["status"],
        "has_text": txt is not None,
        "has_pdf": bool(pdf and pdf.is_file()),
    }


def load_text(doc_id: int, conn: Any) -> tuple[dict[str, Any], str]:
    row = _doc_row(doc_id, conn)
    path = _text_path(row)
    if path is None:
        raise LookupError("该财报暂无可阅读的全文文本")
    # 统一换行，保证字符偏移与前端拿到的一致。
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return metadata(doc_id, conn), text


def build_outline(text: str) -> list[dict[str, Any]]:
    """按标题行切段，每段带原文字符偏移 [start, end)。"""
    heads: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and (_HEADING.match(line) or _NOTE_HEADING.match(line)) and len(stripped) <= 80:
            heads.append((offset, stripped))
        offset += len(line)

    if not heads:
        return [{"index": 0, "heading": "全文", "start": 0, "end": len(text), "preview": text[:100]}]

    sections: list[dict[str, Any]] = []
    for i, (start, heading) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        sections.append({
            "index": i,
            "heading": heading,
            "start": start,
            "end": end,
            "preview": text[start : start + 90].replace("\n", " ").strip(),
        })
    return sections


def read_chunk(text: str, start: int, length: int) -> dict[str, Any]:
    """按字符偏移读取一段原文；start/length 均做边界收敛。"""
    total = len(text)
    begin = max(0, min(int(start), total))
    size = max(1, min(int(length), 20000))
    end = min(total, begin + size)
    return {"start": begin, "end": end, "total_chars": total, "text": text[begin:end]}


def _normalize(text: str) -> tuple[str, list[int]]:
    """去空白、全角转半角，返回归一化串及其到原串下标的映射。"""
    chars: list[str] = []
    positions: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        folded = unicodedata.normalize("NFKC", ch)
        if len(folded) != 1:
            folded = ch
        chars.append(folded)
        positions.append(i)
    return "".join(chars), positions


def _snippet(text: str, start: int, end: int, radius: int = 70) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)].replace("\n", " ").strip()


def locate(
    text: str,
    query: str | None = None,
    keywords: str | None = None,
    section: str | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """分级定位原文：精确 → 归一化 → 关键词 → 章节。

    引文由模型从文本切片得来，常出现断行、空格或全半角差异，故必须逐级降级；
    全部未命中时返回 strategy=none 并给出章节候选，由前端明确提示用户。
    """
    matches: list[dict[str, Any]] = []

    def add(start: int, end: int, strategy: str) -> None:
        if any(m["start"] == start for m in matches):
            return
        idx = None
        for s in sections or []:
            if s["start"] <= start < s["end"]:
                idx = s["index"]
                break
        matches.append({
            "start": start,
            "end": end,
            "section_index": idx,
            "snippet": _snippet(text, start, end),
            "strategy": strategy,
        })

    q = (query or "").strip()
    if q:
        pos = text.find(q)
        if pos >= 0:
            add(pos, pos + len(q), "exact")
        if not matches and len(q) >= 8:
            # 引文跨行时精确匹配失效，用归一化串再找一次。
            norm_q, _ = _normalize(q)
            norm_text, positions = _normalize(text)
            pos = norm_text.find(norm_q)
            if pos >= 0 and positions:
                add(positions[pos], positions[min(pos + len(norm_q) - 1, len(positions) - 1)] + 1, "normalized")
        if not matches:
            # 引文过长时取前 24 个有效字符再试，避免整段截断导致全不匹配。
            head = q[:24].strip()
            if len(head) >= 8:
                pos = text.find(head)
                if pos >= 0:
                    add(pos, pos + len(head), "prefix")

    if not matches and keywords:
        parts = [p.strip() for p in re.split(r"[,，、;；|\s]+", keywords) if len(p.strip()) >= 2]
        parts.sort(key=len, reverse=True)
        for part in parts[:8]:
            pos = text.find(part)
            if pos >= 0:
                add(pos, pos + len(part), "keyword")
            if len(matches) >= 3:
                break

    strategy = matches[0]["strategy"] if matches else "none"
    section_index = None
    if section and sections:
        needle = section.strip()
        for s in sections:
            if needle and needle in s["heading"]:
                section_index = s["index"]
                break
    return {
        "strategy": strategy,
        "matches": matches[:8],
        "section_index": section_index,
        "note": "" if matches else "未定位到原文，可能该引文未被归档全文收录；已按章节提供入口，请自行核对",
    }


def get_document(doc_id: int, conn: Any) -> dict[str, Any]:
    info, text = load_text(doc_id, conn)
    sections = build_outline(text)
    return {**info, "total_chars": len(text), "sections": sections}


def get_text(doc_id: int, start: int, length: int, conn: Any) -> dict[str, Any]:
    _, text = load_text(doc_id, conn)
    return read_chunk(text, start, length)


def locate_document(
    doc_id: int,
    conn: Any,
    query: str | None = None,
    keywords: str | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    _, text = load_text(doc_id, conn)
    sections = build_outline(text)
    return locate(text, query=query, keywords=keywords, section=section, sections=sections)


def pdf_path(doc_id: int, conn: Any) -> Path | None:
    row = _doc_row(doc_id, conn)
    local = row.get("local_path")
    if not local:
        return None
    path = Path(local)
    return path if path.is_file() else None


_TITLE_TAILS = ("。", "，", "；", ",", ";")
_SKIP_PAGE_TITLES = {"未經審核", "未经审计", "目錄", "目录"}


_NOISE_WORDS = ("保证", "聲明：", "声明：", "应当到", "應當到", "公司负责人", "公司負責人")


def _clean_outline_label(value: str) -> str:
    label = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value or "")
    label = re.sub(r"\s+", " ", label).strip()
    label = re.sub(r"^OLE_LINK\d+\s*", "", label).strip()
    # 目录页常有「第一节 释义 ...... 5」这样的点线页码，去掉尾巴只留标题。
    label = re.sub(r"\.{4,}\s*\d*\s*$", "", label).strip()
    return label


def _is_bookmark_title(label: str) -> bool:
    if not 2 <= len(label) <= 60:
        return False
    if label.endswith(_TITLE_TAILS):
        return False
    if re.fullmatch(r"[\d\s.%]+", label):
        return False
    # 书签里混有整段免责声明：带这些措辞的长条目不是可点击章节。
    if len(label) > 15 and any(word in label for word in _NOISE_WORDS):
        return False
    if len(label) > 25 and ("，" in label or "," in label):
        return False
    return True


def _is_page_heading(label: str) -> bool:
    if not 3 <= len(label) <= 36:
        return False
    if any(label.startswith(s) for s in _SKIP_PAGE_TITLES) or label.endswith(_TITLE_TAILS):
        return False
    if re.fullmatch(r"[\d\s.%]+", label):
        return False
    if re.match(r"^[─\-－•‧·]\s*", label):
        return False
    # 长句多半是正文，不是标题。
    if len(label) > 12 and re.search(r"[，,、；;。]", label):
        return False
    return True


def _native_pdf_outline(pdf: Any, max_items: int) -> list[dict[str, Any]]:
    """清洗 PDF 原生书签：去掉链接锚点与整段正文式条目。"""
    total = len(pdf)
    entries: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for raw_level, raw_title, raw_page in pdf.get_toc(simple=True) or []:
        title = _clean_outline_label(str(raw_title))
        if not _is_bookmark_title(title):
            continue
        try:
            level = max(1, min(int(raw_level), 6))
            page = max(1, min(int(raw_page), total))
        except (TypeError, ValueError):
            continue
        key = (level, title, page)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"level": level, "title": title, "page": page})
    # 年报书签可多达数百条，只剩章节级条目才便于点击。
    if len(entries) > 120:
        entries = [item for item in entries if item["level"] <= 2]
    return entries[:max_items]


def _page_pdf_outline(pdf: Any, max_items: int) -> list[dict[str, Any]]:
    """无书签 PDF 的兜底目录：从每页页首提取短标题，生成可点击页码。"""
    per_page: list[list[str]] = []
    frequency: Counter[str] = Counter()
    for page in pdf:
        try:
            lines = page.get_text("text").splitlines()
        except Exception:
            lines = []
        candidates: list[str] = []
        for raw in lines[:14]:
            title = _clean_outline_label(raw)
            if _is_page_heading(title) and title not in candidates:
                candidates.append(title)
                if len(candidates) >= 3:
                    break
        per_page.append(candidates)
        frequency.update(set(candidates))

    # 公司名、报告名常作为页眉反复出现，不应占据目录。
    repeated_limit = max(3, len(per_page) // 8)
    entries: list[dict[str, Any]] = []
    last_page: dict[str, int] = {}
    for index, candidates in enumerate(per_page):
        title = next((c for c in candidates if frequency[c] <= repeated_limit), None)
        if not title:
            continue
        if title in last_page and index - last_page[title] <= 3:
            continue
        last_page[title] = index
        entries.append({"level": 1, "title": title, "page": index + 1})
    return entries[:max_items]


def pdf_outline(doc_id: int, conn: Any, max_items: int = 240) -> dict[str, Any]:
    """PDF 阅读器目录：优先原生书签，缺失时按页首标题生成。"""
    path = pdf_path(doc_id, conn)
    if path is None:
        return {"source": "none", "items": [], "total_pages": 0, "note": "原始 PDF 未归档"}
    try:
        import pymupdf  # 仅目录构建时加载，避免影响纯文本链路

        pdf = pymupdf.open(path)
    except Exception as exc:
        return {"source": "none", "items": [], "total_pages": 0, "note": f"PDF 打开失败：{exc}"}

    try:
        total = len(pdf)
        items = _native_pdf_outline(pdf, max_items)
        source = "bookmark"
        if len(items) < 3:
            items = _page_pdf_outline(pdf, max_items)
            source = "pages"
        return {
            "source": source,
            "items": items,
            "total_pages": total,
            "note": "" if items else "未能生成目录，可用翻页按钮浏览",
        }
    finally:
        pdf.close()


@lru_cache(maxsize=32)
def _render_pdf_page_cached(path: str, page_no: int, scale: float) -> tuple[bytes, float, float]:
    """用 MuPDF 按页栅格化；缓存最近 32 页，避免翻页重复渲染。"""
    import pymupdf  # 仅渲染时加载

    pdf = pymupdf.open(path)
    try:
        index = max(1, min(int(page_no), len(pdf))) - 1
        page = pdf[index]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pix.tobytes("png"), float(page.rect.width), float(page.rect.height)
    finally:
        pdf.close()


def render_pdf_page(doc_id: int, page_no: int, scale: float, conn: Any) -> tuple[bytes, float, float]:
    """返回单页 PNG 与 PDF 页面尺寸（pt），供前端按原始坐标叠加定位高亮。

    浏览器 PDF.js 对部分港交所报告的嵌入 CID 字体兼容性差（文字整片消失），
    服务端 MuPDF 渲染与财报归档字体一致，作为统一渲染源更可靠。
    """
    path = pdf_path(doc_id, conn)
    if path is None:
        raise LookupError("原始 PDF 未归档")
    try:
        safe_scale = max(0.3, min(float(scale), 3.0))
    except (TypeError, ValueError):
        safe_scale = 1.0
    return _render_pdf_page_cached(str(path), max(1, int(page_no)), round(safe_scale, 3))


def locate_in_pdf(
    doc_id: int,
    conn: Any,
    query: str | None = None,
    keywords: str | None = None,
    max_hits: int = 12,
) -> dict[str, Any]:
    """在原始 PDF 中定位引文，返回页码与页面内矩形坐标（pt，左上原点）。

    策略：精确引文 → 去断行归一化 → 前 16 字片段 → 关键词。
    只读取归档 PDF，不接收路径参数；PDF 不可用时明确返回 none 让前端降级。
    """
    path = pdf_path(doc_id, conn)
    if path is None:
        return {"strategy": "none", "matches": [], "note": "原始 PDF 未归档"}
    try:
        import pymupdf  # 仅定位时加载，避免影响纯文本链路

        pdf = pymupdf.open(path)
    except Exception as exc:  # 损坏或加密的 PDF
        return {"strategy": "none", "matches": [], "note": f"PDF 打开失败：{exc}"}

    total_pages = len(pdf)
    try:
        def search(text: str) -> list[dict[str, Any]]:
            hits: list[dict[str, Any]] = []
            for page_index, page in enumerate(pdf):
                for rect in page.search_for(text):
                    hits.append({
                        "page": page_index + 1,
                        "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "snippet": text,
                    })
                    if len(hits) >= max_hits:
                        return hits
            return hits

        candidates: list[tuple[str, str]] = []
        text = (query or "").strip()
        if text:
            candidates.append(("exact", text))
            flat = re.sub(r"\s+", "", text)
            if flat and flat != text:
                candidates.append(("normalized", flat))
            # 财报引文常跨行/跨页，前 16 字片段命中率最高。
            if len(text) > 16:
                candidates.append(("prefix", text[:16]))
        for keyword in (keywords or "").split(","):
            keyword = keyword.strip()
            if len(keyword) >= 2:
                candidates.append(("keyword", keyword))

        for strategy, needle in candidates:
            matches = search(needle)
            if matches:
                return {
                    "strategy": strategy,
                    "matches": matches,
                    "total_pages": total_pages,
                    "note": "",
                }
        return {
            "strategy": "none",
            "matches": [],
            "total_pages": total_pages,
            "note": "PDF 中未找到该引文，请在页面中搜索或按章节核对",
        }
    finally:
        pdf.close()
