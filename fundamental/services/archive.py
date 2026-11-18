"""财报 PDF 下载与本地存档。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fundamental.models import DocType
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import extract_pdf_text
from fundamental.utils.symbols import parse_symbol

_logger = get_logger(__name__)
_DOC_TYPES = (DocType.ANNUAL_REPORT, DocType.SEMI_REPORT, DocType.Q1_REPORT, DocType.Q3_REPORT)


def _extract_year(title: str) -> str | None:
    match = re.search(r"(20\d{2})", title)
    return match.group(1) if match else None


def archive_documents(
    symbol: str,
    db_path: Path | None = None,
    per_type: int = 3,
) -> dict[str, int]:
    """下载各报告期的财报 PDF，提取文本，并存档。"""
    conn = db.init_db(db_path)
    provider = CninfoAnnouncementProvider()
    code, market = parse_symbol(symbol)
    stock_id = repository.get_stock_id(conn, code, market)
    if stock_id is None:
        conn.close()
        return {"error": "股票不存在，请先 fetch_data"}

    stats: dict[str, int] = {}
    for doc_type in _DOC_TYPES:
        documents = provider.list_documents(symbol, doc_type.value)
        downloaded = 0
        for document in documents:
            if downloaded >= per_type:
                break
            if not document.source_url:
                continue
            exists = repository.fetch_one(
                conn, "SELECT id FROM documents WHERE stock_id = ? AND title = ?", (stock_id, document.title)
            )
            if exists:
                continue
            try:
                content = provider.client.get(document.source_url).content
                year = _extract_year(document.title) or "unknown"
                dest_dir = paths.documents_dir() / code / year
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{doc_type.value}_{year}.pdf"
                dest.write_bytes(content)
                text = extract_pdf_text(dest)
                (dest.with_suffix(".txt")).write_text(text, encoding="utf-8")
                repository.insert_many(
                    conn,
                    "documents",
                    ["stock_id", "doc_type", "period", "title", "source_url", "local_path", "file_size", "sha256", "status"],
                    [
                        (
                            stock_id, doc_type.value, year, document.title, document.source_url,
                            str(dest), len(content), hashlib.sha256(content).hexdigest(), "downloaded",
                        )
                    ],
                )
                downloaded += 1
            except Exception as exc:  # noqa: BLE001
                _logger.warning("下载失败 %s %s: %s", doc_type.value, document.title, exc)
        stats[doc_type.value] = downloaded

    conn.commit()
    conn.close()
    return stats
