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


def _source_key(url: str, title: str) -> str:
    """公告唯一标识：优先取巨潮 PDF 文件名中的公告编号，避免不同公告同名覆盖。

    同一 doc_type + 年份可能对应多份公告（延期公告 / 真报告 / 更正公告），
    仅用 doc_type+year 命名会让后下载的公告覆盖先前的正文文件。取 URL 末段
    的公告 ID 作为文件名一部分，保证各公告各存一份、互不覆盖。
    """
    match = re.search(r"(\d{6,})\.(?:pdf|PDF)\s*$", url or "")
    if match:
        return match.group(1)
    return hashlib.sha1(f"{url}{title}".encode("utf-8")).hexdigest()[:12]


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
            # 文件名带公告唯一 ID：同期多份公告（延期/真报告/更正）各存一份，
            # 杜绝后来的公告覆盖已归档正文（五粮液 2025 年报即因此被延期公告污染）。
            source_key = _source_key(document.source_url or "", document.title)
            dest_dir = paths.documents_dir() / code / year
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{doc_type.value}_{year}_{kind}_{source_key}.pdf"
            txt = dest.with_suffix(".txt")
            # 文档表按 (stock_id, doc_type, period) 唯一：同一报告期的业绩公告
            # 与完整年报只留一条。按这组键判重，避免被唯一约束忽略后反复下载；
            # 但记录存在时还要核验本地文件确实是可取证正文：文本过短/乱码/文件
            # 已被同名覆盖（sha 与库不符）都视为没拿到正文，删旧记录重新下载。
            row = repository.fetch_one(
                conn,
                "SELECT id, title, local_path, sha256 FROM documents WHERE stock_id = ? AND doc_type = ? AND period = ?",
                (stock_id, doc_type.value, year),
            )
            if row is not None:
                existing_ok = False
                local = Path(row["local_path"] or "")
                local_txt = local.with_suffix(".txt")
                if local.exists() and local_txt.exists() and local_txt.stat().st_size > 0:
                    try:
                        existing = local_txt.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        existing = ""
                    # sha 不符说明本地 PDF 被别的公告覆盖过，必须重下
                    sha_ok = bool(row["sha256"]) and hashlib.sha256(
                        local.read_bytes()
                    ).hexdigest() == row["sha256"]
                    existing_ok = sha_ok and is_source_text_usable(
                        existing, str(row["title"] or "")
                    )
                if existing_ok:
                    continue
                conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
            try:
                content = provider.client.get(document.source_url).content
                # 先提取并校验文本，通过后才落盘：避免空壳 PDF 先写盘再被
                # continue 跳过，留下一个占着报告期的坏文件。
                tmp = dest.with_name(dest.stem + ".tmp.pdf")
                tmp.write_bytes(content)
                text = extract_pdf_text(tmp)
                if not is_source_text_usable(text, document.title):
                    tmp.unlink(missing_ok=True)
                    _logger.warning(
                        "跳过非正文文档 %s %s（披露提示/延期公告壳或抽取文本过短）",
                        doc_type.value, document.title,
                    )
                    continue
                tmp.replace(dest)
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
