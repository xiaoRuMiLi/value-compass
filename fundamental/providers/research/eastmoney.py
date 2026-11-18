"""东方财富研报数据源。"""

from __future__ import annotations

from datetime import date

from fundamental.models import ResearchReport
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://reportapi.eastmoney.com/report/list"


@register("research", "eastmoney")
class EastmoneyResearchProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_reports(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ResearchReport]:
        code, _ = parse_symbol(symbol)
        begin = (start or "2000-01-01").replace("-", "")
        finish = (end or date.today().isoformat()).replace("-", "")
        results: list[ResearchReport] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _URL,
                params={
                    "industryCode": "*",
                    "pageSize": "500",
                    "industry": "*",
                    "rating": "*",
                    "ratingChange": "*",
                    "beginTime": begin,
                    "endTime": finish,
                    "pageNo": str(page),
                    "qType": "0",
                    "code": code,
                },
                ttl=3600,
            )
            rows = payload.get("data") or []
            if not rows:
                break
            for row in rows:
                info_code = row.get("infoCode") or ""
                results.append(
                    ResearchReport(
                        publish_date=(str(row.get("publishDate") or "") or "")[:10] or None,
                        org_name=row.get("orgSName") or row.get("orgName"),
                        title=str(row.get("title") or ""),
                        source_url=f"https://data.eastmoney.com/report/info/{info_code}.html"
                        if info_code
                        else None,
                    )
                )
            hits = int(payload.get("hits") or 0)
            if page * 500 >= hits:
                break
            page += 1
        return results
