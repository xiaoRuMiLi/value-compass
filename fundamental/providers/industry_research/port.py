"""IndustryResearchPort：行业研报数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import ResearchReport


class IndustryResearchPort(Protocol):
    def fetch_industry_reports(
        self, industry_code: str, start: str | None = None, end: str | None = None, limit: int = 10
    ) -> list[ResearchReport]: ...
