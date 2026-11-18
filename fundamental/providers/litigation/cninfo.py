"""诉讼数据源：基于公告关键词挖掘。"""

from __future__ import annotations

from fundamental.models import Litigation
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.providers.registry import register

_KEYWORDS = ("诉讼", "仲裁", "判决", "裁定", "起诉")


@register("litigation", "cninfo")
class CninfoLitigationProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.15)
        )
        self.announcement = CninfoAnnouncementProvider(self.client)

    def search_litigations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[Litigation]:
        results: list[Litigation] = []
        seen: set[str] = set()
        for keyword in _KEYWORDS:
            page = self.announcement.search_announcements(
                symbol, keyword=keyword, start=start, end=end, page=1, size=100
            )
            for announcement in page.items:
                if announcement.title in seen:
                    continue
                seen.add(announcement.title)
                results.append(
                    Litigation(
                        title=announcement.title,
                        publish_date=announcement.publish_date,
                        source_url=announcement.pdf_url,
                    )
                )
        return results
