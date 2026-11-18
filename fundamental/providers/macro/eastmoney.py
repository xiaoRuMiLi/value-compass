"""东方财富宏观指标数据源（CPI/PMI）。"""

from __future__ import annotations

from fundamental.models import MacroIndicator
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register

_URL = "https://datacenter.eastmoney.com/api/data/v1/get"

_REPORTS = {
    "cpi": ("RPT_ECONOMY_CPI", "CPI同比", "NATIONAL_SAME"),
    "pmi": ("RPT_ECONOMY_PMI", "制造业PMI", "MAKE_INDEX"),
}


@register("macro", "eastmoney")
class EastmoneyMacroProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_macro_indicators(
        self, codes: list[str] | None = None, start: str | None = None, end: str | None = None
    ) -> list[MacroIndicator]:
        wanted = set(codes) if codes else set(_REPORTS)
        results: list[MacroIndicator] = []
        for code in wanted:
            report, name, value_field = _REPORTS[code]
            results.extend(self._fetch(report, code, name, value_field, start, end))
        return results

    def _fetch(
        self,
        report: str,
        code: str,
        name: str,
        value_field: str,
        start: str | None,
        end: str | None,
    ) -> list[MacroIndicator]:
        payload = self.client.get_json(
            _URL,
            params={
                "reportName": report,
                "columns": "ALL",
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "REPORT_DATE",
            },
            ttl=3600,
        )
        rows = (payload.get("result") or {}).get("data") or []
        results: list[MacroIndicator] = []
        for row in rows:
            trade_date = (str(row.get("REPORT_DATE") or "") or "")[:10]
            if start and trade_date < start:
                continue
            if end and trade_date > end:
                continue
            results.append(
                MacroIndicator(
                    indicator_code=code,
                    indicator_name=name,
                    trade_date=trade_date,
                    value=to_float(row.get(value_field)),
                    source="eastmoney",
                )
            )
        return results
