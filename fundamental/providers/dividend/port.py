"""DividendPort：分红送配数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import DividendRecord


class DividendPort(Protocol):
    def fetch_dividends(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[DividendRecord]: ...
