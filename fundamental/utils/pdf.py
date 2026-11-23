"""PDF 文本提取。"""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: str | Path) -> str:
    """提取 PDF 正文。港交所公告的嵌入字体 pypdf 解不出中文，优先 PyMuPDF。"""

    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            pages = []
            for page in doc:
                try:
                    pages.append(page.get_text() or "")
                except Exception:  # noqa: BLE001
                    pages.append("")
        text = "\n".join(pages)
        if text.strip():
            return text
    except Exception:  # noqa: BLE001
        pass

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


# 巨潮检索里「XX年度报告披露的提示性公告」只有一两页"报告已于X日披露"的声明，
# 不含任何财务数据或管理层讨论。若把它当财报正文，逐段通读只能读到空壳：
# 既拿不到原文证据，还会让原文守卫误判覆盖充足。
_SHELL_TITLE_WORDS = ("提示性公告", "披露的提示", "披露提示", "提示公告")

# 财报正文抽出后至少有数千字；低于此阈值视为抽取失败或公告壳
MIN_SOURCE_TEXT_CHARS = 2000


def is_source_title_usable(title: str) -> bool:
    """标题级过滤：披露提示性公告不作为可取证原文。"""
    return not any(word in (title or "") for word in _SHELL_TITLE_WORDS)


def is_source_text_usable(text: str, title: str = "") -> bool:
    """文本级过滤：必须是财报正文（排除壳标题、空文本与过短抽取结果）。"""
    return is_source_title_usable(title) and len((text or "").strip()) >= MIN_SOURCE_TEXT_CHARS
