"""东方财富行业研报数据源：reportapi 的 qType=1 分支。

注意行业研报必须用 industryCode 过滤，传 code 会返回空结果。
"""

from __future__ import annotations

from datetime import date, timedelta

from fundamental.models import ResearchReport
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register

_URL = "https://reportapi.eastmoney.com/report/list"


@register("industry_research", "eastmoney")
class EastmoneyIndustryResearchProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_industry_reports(
        self,
        industry_code: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 10,
    ) -> list[ResearchReport]:
        if not industry_code:
            return []
        begin = (start or (date.today() - timedelta(days=90)).isoformat()).replace("-", "")
        finish = (end or date.today().isoformat()).replace("-", "")
        payload = self.client.get_json(
            _URL,
            params={
                "industryCode": industry_code,
                "pageSize": str(limit),
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": begin,
                "endTime": finish,
                "pageNo": "1",
                "qType": "1",
            },
            ttl=3600,
        )
        rows = payload.get("data") or []
        results: list[ResearchReport] = []
        for row in rows[:limit]:
            info_code = str(row.get("infoCode") or "")
            results.append(
                ResearchReport(
                    publish_date=(str(row.get("publishDate") or ""))[:10] or None,
                    org_name=row.get("orgSName") or row.get("orgName"),
                    title=str(row.get("title") or ""),
                    source_url=f"https://data.eastmoney.com/report/info/{info_code}.html"
                    if info_code
                    else None,
                    info_code=info_code or None,
                    industry_name=row.get("indvInduName") or None,
                    industry_code=industry_code,
                )
            )
        return results
