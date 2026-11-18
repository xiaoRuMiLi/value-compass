"""东方财富资金流向与沪港通持股。"""

from __future__ import annotations

from fundamental.models import CapitalFlow, NorthboundHolding
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_FFLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_NORTHBOUND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_SECID_PREFIX = {"SH": "1", "SZ": "0", "BJ": "0", "HK": "116"}


@register("capitalflow", "eastmoney")
class EastmoneyCapitalFlowProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_capital_flows(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[CapitalFlow]:
        code, market = parse_symbol(symbol)
        secid = f"{_SECID_PREFIX[market.value]}.{code}"
        payload = self.client.get_json(
            _FFLOW_URL,
            params={
                "secid": secid,
                "klt": "101",
                "lmt": "0",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56",
            },
            ttl=3600,
        )
        results: list[CapitalFlow] = []
        for line in (payload.get("data") or {}).get("klines") or []:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            trade_date = parts[0]
            if start and trade_date < start:
                continue
            if end and trade_date > end:
                continue
            results.append(
                CapitalFlow(
                    trade_date=trade_date,
                    main_net=to_float(parts[1]),
                    small_net=to_float(parts[2]),
                    medium_net=to_float(parts[3]),
                    large_net=to_float(parts[4]),
                    super_large_net=to_float(parts[5]),
                )
            )
        return results

    def fetch_northbound(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[NorthboundHolding]:
        code, _ = parse_symbol(symbol)
        payload = self.client.get_json(
            _NORTHBOUND_URL,
            params={
                "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "TRADE_DATE",
            },
            ttl=3600,
        )
        results: list[NorthboundHolding] = []
        for row in (payload.get("result") or {}).get("data") or []:
            trade_date = (str(row.get("TRADE_DATE") or "") or "")[:10]
            if start and trade_date < start:
                continue
            if end and trade_date > end:
                continue
            results.append(
                NorthboundHolding(
                    trade_date=trade_date,
                    hold_shares=to_float(row.get("HOLD_SHARES")),
                    hold_ratio=to_float(row.get("HOLD_SHARES_RATIO")),
                )
            )
        return results
