"""诚信记录数据源：基于公告关键词与规则挖掘。"""

from __future__ import annotations

from fundamental.models import (
    BehaviorType,
    IntegrityCategory,
    IntegrityRecord,
    Severity,
)
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.providers.registry import register

_RULES = (
    ("违规担保", IntegrityCategory.VIOLATION_GUARANTEE, BehaviorType.NEGATIVE, Severity.HIGH),
    ("资金占用", IntegrityCategory.FUND_OCCUPATION, BehaviorType.NEGATIVE, Severity.HIGH),
    ("处罚", IntegrityCategory.PENALTY, BehaviorType.NEGATIVE, Severity.HIGH),
    ("问询", IntegrityCategory.INQUIRY, BehaviorType.NEGATIVE, Severity.MEDIUM),
    ("更正", IntegrityCategory.FINANCIAL_RESTATEMENT, BehaviorType.NEGATIVE, Severity.MEDIUM),
    ("追溯", IntegrityCategory.FINANCIAL_RESTATEMENT, BehaviorType.NEGATIVE, Severity.MEDIUM),
    ("差错", IntegrityCategory.FINANCIAL_RESTATEMENT, BehaviorType.NEGATIVE, Severity.MEDIUM),
    ("承诺", IntegrityCategory.BROKEN_PROMISE, BehaviorType.NEGATIVE, Severity.MEDIUM),
)


@register("integrity", "cninfo")
class CninfoIntegrityProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.15)
        )
        self.announcement = CninfoAnnouncementProvider(self.client)

    def fetch_integrity_records(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[IntegrityRecord]:
        results: list[IntegrityRecord] = []
        seen: set[str] = set()
        for keyword, category, behavior, severity in _RULES:
            page = self.announcement.search_announcements(
                symbol, keyword=keyword, start=start, end=end, page=1, size=100
            )
            for announcement in page.items:
                if announcement.title in seen:
                    continue
                seen.add(announcement.title)
                results.append(
                    IntegrityRecord(
                        record_date=announcement.publish_date,
                        behavior_type=behavior,
                        category=category,
                        title=announcement.title,
                        description=announcement.title,
                        severity=severity,
                        source="cninfo",
                        source_url=announcement.pdf_url,
                    )
                )
        return results
