"""Tushare quote 数据源实现：A 股日线/估值、港股日线。

与东财实现共用 QuotePort 契约，不改写任何下游模型或表结构。
Tushare 的 token 由 TUSHARE_TOKEN 环境变量或项目 .env 提供；各接口按
权限与频率设置不同缓存时长，频率受限时抛 ProviderRateLimitError 以便
上层切换到其它源。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from fundamental.models import KlineBar, Market, Snapshot, Stock, ValuationPoint
from fundamental.providers import ProviderError
from fundamental.providers._shared.convert import to_float
from fundamental.providers._shared.tushare import TushareClient
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"
_HK_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
_DAILY_BASIC_FIELDS = "ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,turnover_rate,total_mv"
_TTL_DAILY = 3600
_TTL_SLOW = 24 * 3600  # 低积分账号对部分接口限频 1 次/小时，缓存一天避免反复撞限


def _compact_date(value: str) -> str:
    return str(value or "").replace("-", "")[:8]


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) < 8:
        return text
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _year_chunks(start: str, end: str, span: int = 5) -> Iterator[tuple[str, str]]:
    """按自然年分段，避免单次请求超出接口返回上限。"""
    try:
        first = int(start[:4])
        last = int(end[:4])
    except (ValueError, TypeError):
        return
    year = first
    while year <= last:
        chunk_end = min(year + span - 1, last)
        seg_start = start if year == first else f"{year:04d}0101"
        seg_end = end if chunk_end == last else f"{chunk_end:04d}1231"
        yield _compact_date(seg_start), _compact_date(seg_end)
        year = chunk_end + 1


def _recent_window(days: int = 30) -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return _compact_date((today - timedelta(days=days)).isoformat()), _compact_date(today.isoformat())


@register("quote", "tushare")
class TushareQuoteProvider:
    def __init__(self, client: TushareClient | None = None) -> None:
        self.client = client or TushareClient()

    def fetch_stock_info(self, symbol: str) -> Stock:
        code, market = parse_symbol(symbol)
        try:
            rows = self.client.call(
                "stock_basic",
                {"ts_code": symbol},
                "ts_code,name,industry,market,list_date",
                ttl=7 * _TTL_SLOW,
            )
        except ProviderError:
            rows = []
        if rows:
            row = rows[0]
            name = str(row.get("name") or code)
            industry = str(row.get("industry") or "") or None
            return Stock(
                code=code,
                market=market,
                name=name,
                industry=industry,
                list_date=_iso_date(row.get("list_date")) or None,
                is_st="ST" in name.upper(),
            )
        # 港股基础资料接口不一定开放；至少保留代码，避免整条同步链路失败。
        return Stock(code=code, market=market, name=code)

    def fetch_snapshot(self, symbol: str) -> Snapshot:
        _, market = parse_symbol(symbol)
        start, end = _recent_window()
        if market == Market.HK:
            rows = self.client.call(
                "hk_daily", {"ts_code": symbol, "start_date": start, "end_date": end},
                _HK_DAILY_FIELDS, ttl=_TTL_DAILY,
            )
            row = rows[0] if rows else {}
            return Snapshot(
                fetched_at=datetime.now(timezone.utc).isoformat(),
                price=to_float(row.get("close")),
                pct_change=to_float(row.get("pct_chg")),
            )

        rows = self.client.call(
            "daily", {"ts_code": symbol, "start_date": start, "end_date": end},
            _DAILY_FIELDS, ttl=_TTL_DAILY,
        )
        row = rows[0] if rows else {}
        turnover: float | None = None
        market_cap: float | None = None
        try:
            basics = self.client.call(
                "daily_basic", {"ts_code": symbol, "start_date": start, "end_date": end},
                "ts_code,trade_date,turnover_rate,total_mv", ttl=_TTL_SLOW,
            )
            if basics:
                turnover = to_float(basics[0].get("turnover_rate"))
                cap_wan = to_float(basics[0].get("total_mv"))
                market_cap = cap_wan * 1e4 if cap_wan is not None else None
        except ProviderError:
            pass
        return Snapshot(
            fetched_at=datetime.now(timezone.utc).isoformat(),
            price=to_float(row.get("close")),
            market_cap=market_cap,
            turnover_rate=turnover,
            pct_change=to_float(row.get("pct_chg")),
        )

    def fetch_kline(self, symbol: str, start: str, end: str) -> list[KlineBar]:
        _, market = parse_symbol(symbol)
        start_compact, end_compact = _compact_date(start), _compact_date(end)
        if market == Market.HK:
            rows = self.client.call(
                "hk_daily",
                {"ts_code": symbol, "start_date": start_compact, "end_date": end_compact},
                _HK_DAILY_FIELDS, ttl=_TTL_SLOW,
            )
            return self._bars_from_rows(rows, amount_scale=1.0)

        bars: list[KlineBar] = []
        last_error: ProviderError | None = None
        for seg_start, seg_end in _year_chunks(start, end):
            try:
                rows = self.client.call(
                    "daily",
                    {"ts_code": symbol, "start_date": seg_start, "end_date": seg_end},
                    _DAILY_FIELDS, ttl=_TTL_DAILY,
                )
            except ProviderError as exc:
                last_error = exc
                continue
            # Tushare 日线成交额单位为千元，换算成元以对齐东财 K 线口径。
            bars.extend(self._bars_from_rows(rows, amount_scale=1000.0))
        if not bars and last_error is not None:
            raise last_error

        try:
            factors = self.client.call(
                "adj_factor",
                {"ts_code": symbol, "start_date": start_compact, "end_date": end_compact},
                "ts_code,trade_date,adj_factor", ttl=_TTL_SLOW,
            )
        except ProviderError:
            factors = []
        by_date = {
            _iso_date(row.get("trade_date")): to_float(row.get("adj_factor")) or 1.0
            for row in factors
        }
        if by_date:
            bars = [replace(bar, adj_factor=by_date.get(bar.trade_date, 1.0)) for bar in bars]
        return bars

    def fetch_valuations(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ValuationPoint]:
        _, market = parse_symbol(symbol)
        start_compact = _compact_date(start) if start else ""
        end_compact = _compact_date(end) if end else ""
        params: dict[str, Any] = {"ts_code": symbol}
        if start_compact:
            params["start_date"] = start_compact
        if end_compact:
            params["end_date"] = end_compact

        if market == Market.HK:
            rows = self.client.call(
                "hk_daily", params, _HK_DAILY_FIELDS, ttl=_TTL_SLOW
            )
            return [
                ValuationPoint(
                    trade_date=_iso_date(row.get("trade_date")),
                    close_price=to_float(row.get("close")),
                )
                for row in rows
                if self._in_range(_iso_date(row.get("trade_date")), start, end)
            ]

        rows = self.client.call("daily_basic", params, _DAILY_BASIC_FIELDS, ttl=_TTL_SLOW)
        points: list[ValuationPoint] = []
        for row in rows:
            trade_date = _iso_date(row.get("trade_date"))
            if not self._in_range(trade_date, start, end):
                continue
            cap_wan = to_float(row.get("total_mv"))
            points.append(
                ValuationPoint(
                    trade_date=trade_date,
                    close_price=to_float(row.get("close")),
                    pe_ttm=to_float(row.get("pe_ttm")),
                    # Tushare daily_basic 的 pe 为静态市盈率，对应东财 PE_LAR。
                    pe_lar=to_float(row.get("pe")),
                    pb_mrq=to_float(row.get("pb")),
                    ps_ttm=to_float(row.get("ps_ttm")),
                    total_market_cap=cap_wan * 1e4 if cap_wan is not None else None,
                )
            )
        return points

    @staticmethod
    def _bars_from_rows(rows: list[dict[str, Any]], amount_scale: float) -> list[KlineBar]:
        bars: list[KlineBar] = []
        for row in rows:
            values = [to_float(row.get(key)) for key in ("open", "close", "high", "low", "vol", "amount")]
            if any(value is None for value in values):
                continue
            open_, close, high, low, volume, amount = values
            bars.append(
                KlineBar(
                    trade_date=_iso_date(row.get("trade_date")),
                    open=float(open_), close=float(close), high=float(high), low=float(low),
                    volume=float(volume), amount=float(amount) * amount_scale, adj_factor=1.0,
                )
            )
        return sorted(bars, key=lambda bar: bar.trade_date)

    @staticmethod
    def _in_range(trade_date: str, start: str | None, end: str | None) -> bool:
        if start and trade_date < start:
            return False
        if end and trade_date > end:
            return False
        return True
