"""巨潮资讯公告数据源。"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from fundamental.models import Announcement, DocStatus, DocType, Document, Market, Page
from fundamental.providers import ProviderDataError
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils import paths
from fundamental.utils.symbols import parse_symbol

_TOPSEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
_HISANNOUNCEMENT = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_PDF_BASE = "https://static.cninfo.com.cn/"

_DOC_KEYWORDS = {
    DocType.ANNUAL_REPORT: "年度报告",
    DocType.SEMI_REPORT: "半年度报告",
    DocType.Q1_REPORT: "一季度报告",
    DocType.Q3_REPORT: "三季度报告",
    DocType.PROSPECTUS: "招股说明书",
}


def _match_doc_type(title: str, doc: DocType) -> bool:
    if doc == DocType.ANNUAL_REPORT:
        return "年度报告" in title and "半年度报告" not in title
    if doc == DocType.SEMI_REPORT:
        return "半年度报告" in title
    if doc == DocType.Q1_REPORT:
        return "一季度报告" in title or "第一季度报告" in title
    if doc == DocType.Q3_REPORT:
        return "三季度报告" in title or "第三季度报告" in title
    if doc == DocType.PROSPECTUS:
        return "招股说明书" in title
    return False


@register("announcement", "cninfo")
class CninfoAnnouncementProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.15)
        )
        self._org_cache: dict[str, str | None] = {}

    def _org_id(self, code: str) -> str | None:
        if code in self._org_cache:
            return self._org_cache[code]
        response = self.client.post_form(_TOPSEARCH, data={"keyWord": code, "maxNum": "10"})
        for item in response.json():
            if item.get("code") == code:
                self._org_cache[code] = item.get("orgId")
                return item.get("orgId")
        self._org_cache[code] = None
        return None

    def search_announcements(
        self,
        symbol: str,
        keyword: str | None = None,
        start: str | None = None,
        end: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Announcement]:
        code, market = parse_symbol(symbol)
        org_id = self._org_id(code) or ""
        column = "sse" if market == Market.SH else "szse"
        response = self.client.post_form(
            _HISANNOUNCEMENT,
            data={
                "pageNum": str(page),
                "pageSize": str(size),
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{code},{org_id}",
                "searchkey": keyword or "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start or '2000-01-01'}~{end or '2099-12-31'}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
        )
        data = response.json()
        total = int(data.get("totalAnnouncement") or 0)
        items: list[Announcement] = []
        for row in data.get("announcements") or []:
            title = re.sub(r"<[^>]+>", "", row.get("announcementTitle") or "").strip()
            if keyword and keyword not in title:
                continue
            timestamp = row.get("announcementTime")
            publish_date = (
                datetime.fromtimestamp(int(timestamp) / 1000).strftime("%Y-%m-%d")
                if timestamp
                else None
            )
            adjunct = row.get("adjunctUrl")
            items.append(
                Announcement(
                    title=title,
                    publish_date=publish_date,
                    source="cninfo",
                    pdf_url=f"{_PDF_BASE}{adjunct}" if adjunct else None,
                )
            )
        return Page(items=items, total=total, page=page, size=size)

    def list_documents(
        self,
        symbol: str,
        doc_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[Document]:
        types = [DocType(doc_type)] if doc_type else list(_DOC_KEYWORDS)
        documents: list[Document] = []
        for doc in types:
            result = self.search_announcements(
                symbol, keyword=_DOC_KEYWORDS.get(doc, ""), start=start, end=end, page=1, size=500
            )
            for announcement in result.items:
                if not _match_doc_type(announcement.title, doc):
                    continue
                documents.append(
                    Document(
                        doc_type=doc,
                        title=announcement.title,
                        source_url=announcement.pdf_url,
                        status=DocStatus.PENDING,
                    )
                )
        return documents

    def fetch_document(self, symbol: str, doc_type: str, period: str) -> Document:
        code, _ = parse_symbol(symbol)
        doc = DocType(doc_type)
        result = self.search_announcements(
            symbol, keyword=_DOC_KEYWORDS.get(doc, ""), page=1, size=500
        )
        target = next(
            (
                announcement
                for announcement in result.items
                if announcement.pdf_url
                and _match_doc_type(announcement.title, doc)
                and (not period or period in (announcement.title or ""))
            ),
            None,
        )
        if target is None:
            raise ProviderDataError(f"未找到 {doc_type} {period} 的公告")
        content = self.client.get(target.pdf_url).content
        dest_dir = paths.documents_dir() / code / (period or "misc")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{target.publish_date or date.today().isoformat()}_{doc.value}.pdf"
        dest.write_bytes(content)
        return Document(
            doc_type=doc,
            period=period,
            title=target.title,
            source_url=target.pdf_url,
            local_path=str(dest),
            file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status=DocStatus.DOWNLOADED,
        )
