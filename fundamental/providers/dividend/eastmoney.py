"""东方财富分红送配数据源。"""

from __future__ import annotations

import re

from fundamental.models import DividendRecord, Market
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
# 港股现金分红句式：「每股派港币5.3元」。实物分派（分派美团/京东股票）不算现金 DPS。
_RE_HK_CASH = re.compile(r"每股派港币\s*([\d.]+)\s*元")
_RE_HK_CASH_ALT = re.compile(r"每股派\s*([\d.]+)\s*港元")


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
        if market == Market.HK:
            return self._fetch_hk_dividends(symbol, start=start, end=end)
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

    def _fetch_hk_dividends(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[DividendRecord]:
        """港股分红：东财 F10 的 RPT_HKF10_INFO_DIVIDEND 报表。

        A 股报表 RPT_SHAREBONUS_DET 不返回港股，因此单列一路。字段 PLAN_EXPLAIN
        是自然语言（「每股派港币5.3元」），只提取现金分红；实物分派（分派其他
        上市公司股票）不产生现金 DPS，跳过。币种标 HKD，由上游换算后再进 DDM。
        """
        code, market = parse_symbol(symbol)
        secucode = f"{code}.{market.value}"
        payload = self.client.get_json(
            _URL,
            params={
                "reportName": "RPT_HKF10_INFO_DIVIDEND",
                "columns": "ALL",
                "filter": f'(SECUCODE="{secucode}")',
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "EX_DIVIDEND_DATE",
                "source": "F10",
                "client": "PC",
            },
            ttl=3600,
        )
        rows = (payload.get("result") or {}).get("data") or []
        results: list[DividendRecord] = []
        for row in rows:
            # 排除实物分派/特别说明，只认现金分红句式
            if str(row.get("IS_DIVIDEND") or "") != "1":
                continue
            explain = str(row.get("PLAN_EXPLAIN") or "")
            m = _RE_HK_CASH.search(explain) or _RE_HK_CASH_ALT.search(explain)
            if not m:
                continue
            cash_per_share = to_float(m.group(1))
            if cash_per_share is None:
                continue
            ex_date = (str(row.get("EX_DIVIDEND_DATE") or ""))[:10] or None
            notice_date = (str(row.get("NOTICE_DATE") or ""))[:10] or None
            anchor = ex_date or notice_date
            if start and anchor and anchor < start:
                continue
            if end and anchor and anchor > end:
                continue
            results.append(
                DividendRecord(
                    plan_notice_date=notice_date,
                    ex_date=ex_date,
                    record_date=(str(row.get("RECORD_DATE") or ""))[:10] or None,
                    pay_date=(str(row.get("DIVIDEND_DATE") or ""))[:10] or None,
                    cash_per_share=cash_per_share,
                    currency="HKD",
                )
            )
        return results
