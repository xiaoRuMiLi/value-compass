"""QuotePort：实时行情与历史 K 线/估值的输入输出契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import KlineBar, Snapshot, ValuationPoint


class QuotePort(Protocol):
    def fetch_snapshot(self, symbol: str) -> Snapshot: ...

    def fetch_kline(self, symbol: str, start: str, end: str) -> list[KlineBar]: ...

    def fetch_valuations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ValuationPoint]: ...
