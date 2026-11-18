"""ForecastPort：预期差数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import ConsensusEstimate, EarningsForecast, ReportCalendarItem


class ForecastPort(Protocol):
    def fetch_earnings_forecasts(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[EarningsForecast]: ...

    def fetch_consensus_estimates(self, symbol: str) -> list[ConsensusEstimate]: ...

    def fetch_report_calendar(self, symbol: str) -> list[ReportCalendarItem]: ...
