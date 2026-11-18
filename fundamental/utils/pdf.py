"""PDF 文本提取。"""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: str | Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)
