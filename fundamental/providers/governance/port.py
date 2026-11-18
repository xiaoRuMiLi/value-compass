"""GovernancePort：治理数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import CorporateAction, InsiderTrade, Pledge, ShareholderCount, Unlock


class GovernancePort(Protocol):
    def fetch_corporate_actions(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[CorporateAction]: ...

    def fetch_insider_trades(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[InsiderTrade]: ...

    def fetch_shareholder_count(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ShareholderCount]: ...

    def fetch_pledges(self, symbol: str) -> list[Pledge]: ...

    def fetch_unlocks(self, symbol: str) -> list[Unlock]: ...
