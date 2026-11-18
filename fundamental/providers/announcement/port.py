"""AnnouncementPort：公告数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import Announcement, Document, Page


class AnnouncementPort(Protocol):
    def search_announcements(
        self,
        symbol: str,
        keyword: str | None = None,
        start: str | None = None,
        end: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Announcement]: ...

    def fetch_document(self, symbol: str, doc_type: str, period: str) -> Document: ...

    def list_documents(
        self,
        symbol: str,
        doc_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[Document]: ...
