"""FinancialPort：财务数据的输入输出契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import FinancialReport, RevenueComposition, RevenueDimension, StatementItem, StatementType


class FinancialPort(Protocol):
    def fetch_financials(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        report_types: list[str] | None = None,
    ) -> list[FinancialReport]: ...

    def fetch_statement_items(
        self,
        symbol: str,
        statement_type: StatementType | str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[StatementItem]: ...

    def fetch_revenue_composition(
        self,
        symbol: str,
        dimension: RevenueDimension | str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[RevenueComposition]: ...
