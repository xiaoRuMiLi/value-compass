"""ResearchPort：研报数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import ResearchReport


class ResearchPort(Protocol):
    def fetch_reports(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ResearchReport]: ...
