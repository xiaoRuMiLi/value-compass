"""东方财富 quote 数据源实现。"""

from __future__ import annotations

from datetime import datetime, timezone

from fundamental.models import KlineBar, Market, Snapshot, Stock, ValuationPoint
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_VALUATION_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

_SECID_PREFIX = {"SH": "1", "SZ": "0", "BJ": "0", "HK": "116"}


def _secid(market: Market, code: str) -> str:
    prefix = _SECID_PREFIX.get(market.value)
    if prefix is None:
        raise ValueError(f"暂不支持的市场: {market.value}")
    return f"{prefix}.{code}"


@register("quote", "eastmoney")
class EastmoneyQuoteProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_stock_info(self, symbol: str) -> Stock:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _SNAPSHOT_URL,
            params={"secid": _secid(market, code), "fields": "f57,f58", "fltt": "2"},
        )
        data = payload.get("data") or {}
        return Stock(code=code, market=market, name=str(data.get("f58") or code))

    def fetch_snapshot(self, symbol: str) -> Snapshot:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _SNAPSHOT_URL,
            params={
                "secid": _secid(market, code),
                "fields": "f43,f57,f58,f116,f117,f168",
                "fltt": "2",
            },
        )
        data = payload.get("data") or {}
        return Snapshot(
            fetched_at=datetime.now(timezone.utc).isoformat(),
            price=to_float(data.get("f43")),
            market_cap=to_float(data.get("f116")),
            turnover_rate=to_float(data.get("f168")),
        )

    def fetch_kline(self, symbol: str, start: str, end: str) -> list[KlineBar]:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _KLINE_URL,
            params={
                "secid": _secid(market, code),
                "klt": "101",
                "fqt": "0",
                "beg": start.replace("-", ""),
                "end": end.replace("-", ""),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            },
        )
        rows = (payload.get("data") or {}).get("klines") or []
        bars: list[KlineBar] = []
        for row in rows:
            parts = row.split(",")
            if len(parts) < 7:
                continue
            bars.append(
                KlineBar(
                    trade_date=parts[0],
                    open=float(parts[1]),
                    close=float(parts[2]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    volume=float(parts[5]),
                    amount=float(parts[6]),
                    adj_factor=1.0,
                )
            )
        return bars

    def fetch_valuations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ValuationPoint]:
        code, market = parse_symbol(symbol)
        secucode = f"{code}.{market.value}"
        points: list[ValuationPoint] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _VALUATION_URL,
                params={
                    "reportName": "RPT_VALUEANALYSIS_DET",
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{secucode}")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "TRADE_DATE",
                },
                ttl=3600,
            )
            result = payload.get("result") or {}
            rows = result.get("data") or []
            if not rows:
                break
            for row in rows:
                trade_date = str(row.get("TRADE_DATE") or "")[:10]
                if start and trade_date < start:
                    continue
                if end and trade_date > end:
                    continue
                points.append(
                    ValuationPoint(
                        trade_date=trade_date,
                        close_price=to_float(row.get("CLOSE_PRICE")),
                        pe_ttm=to_float(row.get("PE_TTM")),
                        pe_lar=to_float(row.get("PE_LAR")),
                        pb_mrq=to_float(row.get("PB_MRQ")),
                        ps_ttm=to_float(row.get("PS_TTM")),
                        peg=to_float(row.get("PEG_CAR")),
                        pcf_ocf_ttm=to_float(row.get("PCF_OCF_TTM")),
                        total_market_cap=to_float(row.get("TOTAL_MARKET_CAP")),
                    )
                )
            if len(rows) < 500:
                break
            page += 1
        return points
