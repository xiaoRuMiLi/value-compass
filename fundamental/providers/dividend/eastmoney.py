"""东方财富分红送配数据源。"""

from __future__ import annotations

from fundamental.models import DividendRecord
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


@register("dividend", "eastmoney")
class EastmoneyDividendProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_dividends(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[DividendRecord]:
        code, market = parse_symbol(symbol)
        secucode = f"{code}.{market.value}"
        results: list[DividendRecord] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _URL,
                params={
                    "reportName": "RPT_SHAREBONUS_DET",
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{secucode}")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "EX_DIVIDEND_DATE",
                },
                ttl=3600,
            )
            rows = (payload.get("result") or {}).get("data") or []
            if not rows:
                break
            for row in rows:
                ex_date = (str(row.get("EX_DIVIDEND_DATE") or "") or "")[:10] or None
                plan_date = (str(row.get("PLAN_NOTICE_DATE") or "") or "")[:10] or None
                anchor = ex_date or plan_date
                if start and anchor and anchor < start:
                    continue
                if end and anchor and anchor > end:
                    continue
                cash_10 = to_float(row.get("PRETAX_BONUS_RMB"))
                results.append(
                    DividendRecord(
                        plan_notice_date=plan_date,
                        ex_date=ex_date,
                        record_date=(str(row.get("EQUITY_RECORD_DATE") or "") or "")[:10] or None,
                        pay_date=None,
                        cash_per_share=(cash_10 / 10.0) if cash_10 is not None else None,
                        bonus_ratio=to_float(row.get("BONUS_RATIO")),
                        transfer_ratio=to_float(row.get("IT_RATIO")),
                    )
                )
            if len(rows) < 500:
                break
            page += 1
        return results
