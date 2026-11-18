"""CapitalFlowPort：资金流向契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import CapitalFlow, NorthboundHolding


class CapitalFlowPort(Protocol):
    def fetch_capital_flows(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[CapitalFlow]: ...

    def fetch_northbound(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[NorthboundHolding]: ...
