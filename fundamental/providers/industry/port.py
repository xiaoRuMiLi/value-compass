"""IndustryPort：行业板块数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import IndustryBoard


class IndustryPort(Protocol):
    def list_industries(self, date: str | None = None) -> list[IndustryBoard]: ...

    def fetch_stock_industry(self, symbol: str) -> IndustryBoard: ...
