"""LitigationPort：诉讼数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import Litigation


class LitigationPort(Protocol):
    def search_litigations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[Litigation]: ...
