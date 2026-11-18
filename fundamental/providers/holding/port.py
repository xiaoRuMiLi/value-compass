"""HoldingPort：持仓数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import CirculatingHolder, FundHolding


class HoldingPort(Protocol):
    def fetch_circulating_holders(
        self, symbol: str, report_date: str | None = None
    ) -> list[CirculatingHolder]: ...

    def fetch_fund_holdings(self, symbol: str, end_date: str | None = None) -> list[FundHolding]: ...
