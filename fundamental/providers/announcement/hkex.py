"""港交所披露易公告数据源：港股年报 / 业绩公告原文。

与 CninfoAnnouncementProvider 实现同一个 AnnouncementPort，归档服务按市场选源。
原文取证需要中文完整正文，因此只收中文公告，排除英文版与 ESG 报告。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime
from urllib.parse import urljoin

from fundamental.models import Announcement, DocStatus, DocType, Document, Market, Page
from fundamental.providers import ProviderDataError
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils import paths
from fundamental.utils.symbols import parse_symbol

_PREFIX = "https://www1.hkexnews.hk/search/prefix.do"
_SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
_BASE = "https://www1.hkexnews.hk"
_HEADERS = {"Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml"}

_DOC_KEYWORDS = {
    DocType.ANNUAL_REPORT: ("年報", "全年業績公佈"),
    DocType.SEMI_REPORT: ("中期報告", "中期業績公佈"),
    DocType.Q1_REPORT: ("第一季度報告", "三月三十一日止三個月業績公佈"),
    DocType.Q3_REPORT: ("第三季度報告", "九月三十日止三個月及九個月業績公佈"),
}
_EXCLUDE = ("環境、社會及管治", "ESG", "英文版", "English")
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _clean_title(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def _extract_period(title: str) -> str | None:
    """从中文公告标题取报告期年份（业绩公告标题用「二零二三」）。"""

    arabic = re.search(r"(20\d{2})", title)
    if arabic:
        return arabic.group(1)
    chinese = re.search(r"([二零〇一二三四五六七八九]{4})年", title)
    if chinese:
        return "".join(str(_CN_DIGITS.get(ch, 0)) for ch in chinese.group(1))
    return None


def _match_doc_type(title: str, doc: DocType) -> bool:
    if any(word in title for word in _EXCLUDE):
        return False
    keywords = _DOC_KEYWORDS.get(doc, ())
    if doc == DocType.ANNUAL_REPORT:
        # 半年度中期报告不能混进年报
        return any(k in title for k in keywords) and "中期" not in title
    return any(k in title for k in keywords)


@register("announcement", "hkex")
class HkexAnnouncementProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.2)
        )
        self._stock_ids: dict[str, str | None] = {}

    def _stock_id(self, code: str) -> str | None:
        if code in self._stock_ids:
            return self._stock_ids[code]
        text = self.client.get_text(
            _PREFIX,
            params={"callback": "cb", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"},
            headers=_HEADERS,
            ttl=86400,
        )
        for item in re.findall(r"\{[^{}]+\}", text):
            if re.search(r'"code"\s*:\s*"?' + re.escape(code), item):
                found = re.search(r'"stockId"\s*:\s*"?(\d+)', item)
                self._stock_ids[code] = found.group(1) if found else None
                return self._stock_ids[code]
        self._stock_ids[code] = None
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
        if market != Market.HK:
            raise ProviderDataError(f"港交所数据源不支持 {symbol}")
        stock_id = self._stock_id(code)
        if not stock_id:
            return Page(items=[], total=0, page=page, size=size)

        payload = self.client.get_json(
            _SEARCH,
            params={
                "sortDir": "0",
                "sortByOptions": "DateTime",
                "category": "0",
                "market": "SEHK",
                "stockId": stock_id,
                "documentType": "-1",
                "fromDate": (start or "2000-01-01").replace("-", ""),
                "toDate": (end or date.today().isoformat()).replace("-", ""),
                "title": keyword or "",
                "searchType": "1",
                "t1code": "-2",
                "t2Gcode": "-2",
                "t2code": "-2",
                "rowRange": str(max(100, size)),
                "lang": "ZH",
            },
            headers=_HEADERS,
            ttl=3600,
        )
        raw = payload.get("result") or "[]"
        rows = json.loads(raw) if isinstance(raw, str) else raw
        items: list[Announcement] = []
        for row in rows:
            title = _clean_title(str(row.get("TITLE") or ""))
            if keyword and keyword not in title:
                continue
            link = str(row.get("FILE_LINK") or "")
            if not link:
                continue
            try:
                publish_date = datetime.strptime(
                    str(row.get("DATE_TIME") or ""), "%d/%m/%Y %H:%M"
                ).strftime("%Y-%m-%d")
            except ValueError:
                publish_date = None
            items.append(
                Announcement(
                    title=title,
                    publish_date=publish_date,
                    source="hkex",
                    pdf_url=urljoin(_BASE, link),
                )
            )
        return Page(items=items, total=len(items), page=page, size=size)

    def list_documents(
        self,
        symbol: str,
        doc_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[Document]:
        types = [DocType(doc_type)] if doc_type else list(_DOC_KEYWORDS)
        documents: list[Document] = []
        seen: set[str] = set()
        for doc in types:
            # 每个标题关键词单独检索，避免 rowRange 截断掉较早的报告期
            for keyword in _DOC_KEYWORDS.get(doc, ()):
                page = self.search_announcements(symbol, keyword=keyword, start=start, end=end, size=500)
                for announcement in page.items:
                    if not announcement.pdf_url or announcement.pdf_url in seen:
                        continue
                    if not _match_doc_type(announcement.title, doc):
                        continue
                    seen.add(announcement.pdf_url)
                    documents.append(
                        Document(
                            doc_type=doc,
                            period=_extract_period(announcement.title),
                            title=announcement.title,
                            source_url=announcement.pdf_url,
                            status=DocStatus.PENDING,
                        )
                    )
        # 同一年内业绩公告排在年报前：公司的「主要原因」说明更完整
        documents.sort(
            key=lambda d: (
                -int(d.period or 0),
                0 if "業績" in d.title else 1,
                d.title,
            )
        )
        return documents

    def fetch_document(self, symbol: str, doc_type: str, period: str) -> Document:
        code, _ = parse_symbol(symbol)
        doc = DocType(doc_type)
        target = next(
            (
                d for d in self.list_documents(symbol, doc_type)
                if d.source_url and (not period or d.period == period)
            ),
            None,
        )
        if target is None:
            raise ProviderDataError(f"未找到 {doc_type} {period} 的港交所公告")
        content = self.client.get(target.source_url).content
        dest_dir = paths.documents_dir() / code / (period or "misc")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{doc.value}_{period or 'misc'}.pdf"
        dest.write_bytes(content)
        return Document(
            doc_type=doc,
            period=period,
            title=target.title,
            source_url=target.source_url,
            local_path=str(dest),
            file_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status=DocStatus.DOWNLOADED,
        )
