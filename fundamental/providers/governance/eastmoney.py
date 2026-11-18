"""东方财富治理数据源（F10 + 公告关键词挖掘）。"""

from __future__ import annotations

from fundamental.models import (
    CorporateAction,
    CorporateActionType,
    InsiderDirection,
    InsiderTrade,
    Pledge,
    ShareholderCount,
    Unlock,
)
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_SHAREHOLDER_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"
_DC_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

_ACTION_MAP = {
    "回购": CorporateActionType.BUYBACK,
    "增发": CorporateActionType.SEO,
    "配股": CorporateActionType.RIGHTS,
    "可转债": CorporateActionType.CONVERTIBLE,
    "股权激励": CorporateActionType.INCENTIVE,
}


@register("governance", "eastmoney")
class EastmoneyGovernanceProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )
        self.announcement = CninfoAnnouncementProvider(self.client)

    def fetch_corporate_actions(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[CorporateAction]:
        results: list[CorporateAction] = []
        seen: set[str] = set()
        for keyword, action_type in _ACTION_MAP.items():
            page = self.announcement.search_announcements(
                symbol, keyword=keyword, start=start, end=end, page=1, size=100
            )
            for announcement in page.items:
                if announcement.title in seen:
                    continue
                seen.add(announcement.title)
                results.append(
                    CorporateAction(
                        action_date=announcement.publish_date,
                        action_type=action_type,
                        source_url=announcement.pdf_url,
                    )
                )
        return results

    def fetch_insider_trades(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[InsiderTrade]:
        results: list[InsiderTrade] = []
        seen: set[str] = set()
        for keyword, direction in (("增持", InsiderDirection.BUY), ("减持", InsiderDirection.SELL)):
            page = self.announcement.search_announcements(
                symbol, keyword=keyword, start=start, end=end, page=1, size=100
            )
            for announcement in page.items:
                if announcement.title in seen:
                    continue
                seen.add(announcement.title)
                results.append(
                    InsiderTrade(
                        trade_date=announcement.publish_date or "",
                        direction=direction,
                    )
                )
        return results

    def fetch_shareholder_count(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ShareholderCount]:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _SHAREHOLDER_URL, params={"code": f"{market.value}{code}"}, ttl=3600
        )
        results: list[ShareholderCount] = []
        for row in payload.get("gdrs") or []:
            end_date = (str(row.get("END_DATE") or "") or "")[:10]
            if start and end_date < start:
                continue
            if end and end_date > end:
                continue
            results.append(
                ShareholderCount(end_date=end_date, holder_count=row.get("HOLDER_TOTAL_NUM"))
            )
        return results

    def fetch_pledges(self, symbol: str) -> list[Pledge]:
        page = self.announcement.search_announcements(symbol, keyword="质押", page=1, size=100)
        return [Pledge(pledge_date=announcement.publish_date, status="质押") for announcement in page.items]

    def fetch_unlocks(self, symbol: str) -> list[Unlock]:
        code, _ = parse_symbol(symbol)
        payload = self.client.get_json(
            _DC_URL,
            params={
                "reportName": "RPT_LIFT_STOCK",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "FREE_DATE",
            },
            ttl=3600,
        )
        results: list[Unlock] = []
        for row in (payload.get("result") or {}).get("data") or []:
            results.append(
                Unlock(
                    unlock_date=(str(row.get("FREE_DATE") or "") or "")[:10],
                    unlock_shares=to_float(row.get("FREE_SHARES")),
                    unlock_ratio=to_float(row.get("ADD_LISTSHARES_RATIO")),
                    unlock_market_value=to_float(row.get("ADD_LISTING_CAP")),
                )
            )
        return results
