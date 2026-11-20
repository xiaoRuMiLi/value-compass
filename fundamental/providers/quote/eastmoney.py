"""东方财富 quote 数据源实现。"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

from fundamental.models import KlineBar, Market, Snapshot, Stock, ValuationPoint
from fundamental.providers import ProviderConfigError, ProviderRequestError
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_VALUATION_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# 腾讯财经降级源：东财 push2/push2his 端点在部分网络环境下被阻断时的备用行情接口。
_TX_QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"
_TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
_TX_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj", "HK": "hk"}

_SECID_PREFIX = {"SH": "1", "SZ": "0", "BJ": "0", "HK": "116"}


def _secid(market: Market, code: str) -> str:
    prefix = _SECID_PREFIX.get(market.value)
    if prefix is None:
        raise ProviderConfigError(f"暂不支持的市场: {market.value}")
    return f"{prefix}.{code}"


def _tx_symbol(market: Market, code: str) -> str:
    prefix = _TX_PREFIX.get(market.value)
    if prefix is None:
        raise ProviderConfigError(f"暂不支持的市场: {market.value}")
    return f"{prefix}{code}"


@register("quote", "eastmoney")
class EastmoneyQuoteProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )
        # 腾讯降级源用独立 session（GBK 编码、直连、不读环境代理）。
        self._tx_session = requests.Session()
        self._tx_session.trust_env = False
        self._tx_session.headers.update({"User-Agent": "Mozilla/5.0"})

    def _tx_quote_fields(self, code: str, market: Market) -> list[str]:
        """取腾讯行情快照，返回按 ~ 分隔的字段数组。"""
        symbol = _tx_symbol(market, code)
        resp = self._tx_session.get(_TX_QUOTE_URL.format(symbol=symbol), timeout=15)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")
        match = re.search(r'="(.*)"', text)
        if not match:
            raise ProviderRequestError(f"腾讯行情解析失败: {symbol}")
        return match.group(1).split("~")

    def fetch_stock_info(self, symbol: str) -> Stock:
        code, market = parse_symbol(symbol)
        try:
            payload = self.client.get_json(
                _SNAPSHOT_URL,
                params={"secid": _secid(market, code), "fields": "f57,f58,f100,f127,f128", "fltt": "2"},
            )
            data = payload.get("data") or {}
            industry = data.get("f100") or data.get("f127")
            sector = data.get("f128") or data.get("f127")
            return Stock(
                code=code,
                market=market,
                name=str(data.get("f58") or code),
                industry=str(industry) if industry else None,
                sector=str(sector) if sector else None,
            )
        except ProviderRequestError:
            # 东财 push2 端点被阻断时降级腾讯（腾讯快照无行业字段，留空）。
            fields = self._tx_quote_fields(code, market)
            name = fields[1].replace(" ", "") if len(fields) > 1 else code
            return Stock(code=code, market=market, name=name)

    def fetch_snapshot(self, symbol: str) -> Snapshot:
        code, market = parse_symbol(symbol)
        try:
            payload = self.client.get_json(
                _SNAPSHOT_URL,
                params={
                    "secid": _secid(market, code),
                    "fields": "f43,f57,f58,f116,f117,f168,f170",
                    "fltt": "2",
                },
            )
            data = payload.get("data") or {}
            return Snapshot(
                fetched_at=datetime.now(timezone.utc).isoformat(),
                price=to_float(data.get("f43")),
                market_cap=to_float(data.get("f116")),
                turnover_rate=to_float(data.get("f168")),
                pct_change=to_float(data.get("f170")),
            )
        except ProviderRequestError:
            fields = self._tx_quote_fields(code, market)
            def at(i: int) -> str | None:
                return fields[i] if len(fields) > i else None
            cap_yi = to_float(at(45))
            return Snapshot(
                fetched_at=datetime.now(timezone.utc).isoformat(),
                price=to_float(at(3)),
                # 腾讯总市值单位为亿元，换算为元以对齐东财 f116。
                market_cap=cap_yi * 1e8 if cap_yi is not None else None,
                turnover_rate=to_float(at(38)),
                pct_change=to_float(at(32)),
            )

    def fetch_kline(self, symbol: str, start: str, end: str) -> list[KlineBar]:
        code, market = parse_symbol(symbol)
        try:
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
        except ProviderRequestError:
            # 东财 push2his 被阻断时降级腾讯日K（单次上限1000条，覆盖近期窗口）。
            # 腾讯字段顺序: [date, open, close, high, low, volume]，无成交额。
            return self._tx_kline(code, market, start, end)

    def _tx_kline(self, code: str, market: Market, start: str, end: str) -> list[KlineBar]:
        symbol = _tx_symbol(market, code)
        try:
            resp = self._tx_session.get(
                _TX_KLINE_URL,
                params={"param": f"{symbol},day,{start},{end},1000"},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            # 行情降级失败不应阻断财务数据入库。
            return []
        node = ((payload.get("data") or {}).get(symbol) or {}) if isinstance(payload, dict) else {}
        rows = node.get("day") or node.get("qfqday") or []
        bars: list[KlineBar] = []
        for row in rows:
            if len(row) < 6:
                continue
            try:
                bars.append(
                    KlineBar(
                        trade_date=str(row[0]),
                        open=float(row[1]),
                        close=float(row[2]),
                        high=float(row[3]),
                        low=float(row[4]),
                        volume=float(row[5]),
                        amount=0.0,
                        adj_factor=1.0,
                    )
                )
            except (TypeError, ValueError):
                continue
        return bars

    def fetch_valuations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ValuationPoint]:
        code, market = parse_symbol(symbol)
        secucode = f"{code}.{market.value}"
        points: list[ValuationPoint] = []
        if market == Market.HK:
            # 港股 PE_TTM/PB_TTM 随主要指标按报告期披露，取最近若干期作为估值快照。
            payload = self.client.get_json(
                _VALUATION_URL,
                params={
                    "reportName": "RPT_HKF10_FN_MAININDICATOR",
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{secucode}")',
                    "pageNumber": "1",
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "STD_REPORT_DATE",
                    "source": "F10",
                    "client": "PC",
                },
                ttl=3600,
            )
            for row in (payload.get("result") or {}).get("data") or []:
                trade_date = str(row.get("STD_REPORT_DATE") or "")[:10]
                if start and trade_date < start:
                    continue
                if end and trade_date > end:
                    continue
                points.append(
                    ValuationPoint(
                        trade_date=trade_date,
                        pe_ttm=to_float(row.get("PE_TTM")),
                        pb_mrq=to_float(row.get("PB_TTM")),
                        total_market_cap=to_float(row.get("TOTAL_MARKET_CAP")),
                    )
                )
            return points
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
