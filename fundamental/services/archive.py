"""财报 PDF 下载与本地存档。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fundamental.models import DocType, Market
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.providers.announcement.hkex import HkexAnnouncementProvider
from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import extract_pdf_text, is_source_text_usable
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
    code, market = parse_symbol(symbol)
    # 原文取证按市场选源：A 股巨潮，港股披露易；两者实现同一 Port 契约
    provider = HkexAnnouncementProvider() if market == Market.HK else CninfoAnnouncementProvider()
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
            # 港股业绩公告与完整年报同一报告期，必须用不同文件名，否则后者覆盖前者
            year = document.period or _extract_year(document.title) or "unknown"
            kind = "results" if ("業績" in document.title or "业绩" in document.title) else "report"
            dest_dir = paths.documents_dir() / code / year
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{doc_type.value}_{year}_{kind}.pdf"
            txt = dest.with_suffix(".txt")
            # 文档表按 (stock_id, doc_type, period) 唯一：同一报告期的业绩公告
            # 与完整年报只留一条。按这组键判重，避免被唯一约束忽略后反复下载；
            # 但若记录存在却只是「披露提示性公告」壳或抽取文本过短，视为没拿到
            # 正文，删掉旧记录后重新下载覆盖，不让空壳长期占着报告期。
            row = repository.fetch_one(
                conn,
                "SELECT id, title FROM documents WHERE stock_id = ? AND doc_type = ? AND period = ?",
                (stock_id, doc_type.value, year),
            )
            if row is not None and txt.exists() and txt.stat().st_size > 0:
                try:
                    existing = txt.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    existing = ""
                if is_source_text_usable(existing, str(row["title"] or "")):
                    continue
                conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
            try:
                content = provider.client.get(document.source_url).content
                dest.write_bytes(content)
                text = extract_pdf_text(dest)
                # 只有财报正文才入库；提示公告壳不写 txt、不计数，继续找下一份候选
                if not is_source_text_usable(text, document.title):
                    _logger.warning(
                        "跳过非正文文档 %s %s（披露提示壳或抽取文本过短）",
                        doc_type.value, document.title,
                    )
                    continue
                txt.write_text(text, encoding="utf-8")
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
