"""东方财富机构调研数据源：公开披露的投资者关系活动记录表。"""

from __future__ import annotations

from fundamental.models import SurveyRecord
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT_NAME = "RPT_ORG_SURVEY"


@register("survey", "eastmoney")
class EastmoneySurveyProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_surveys(self, symbol: str, limit: int = 10) -> list[SurveyRecord]:
        code, _ = parse_symbol(symbol)
        payload = self.client.get_json(
            _URL,
            params={
                "reportName": _REPORT_NAME,
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": str(limit),
                "sortColumns": "NOTICE_DATE",
                "sortTypes": "-1",
            },
            ttl=3600,
        )
        result = payload.get("result")
        rows = result.get("data") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            return []

        records: list[SurveyRecord] = []
        for row in rows[:limit]:
            notice_date = (str(row.get("NOTICE_DATE") or ""))[:10]
            way = row.get("RECEIVE_WAY_EXPLAIN") or None
            secucode = str(row.get("SECUCODE") or code)
            receive_start = str(row.get("RECEIVE_START_DATE") or "")[:10]
            records.append(
                SurveyRecord(
                    publish_date=notice_date or None,
                    company=row.get("SECURITY_NAME_ABBR") or row.get("ORG_NAME"),
                    title=f"{way or '投资者关系活动'}（{notice_date}）",
                    content=str(row.get("CONTENT") or ""),
                    participants=row.get("RECEIVE_OBJECT") or None,
                    way=way,
                    source_url=row.get("URL") or None,
                    source_key=f"{secucode}_{notice_date}_{receive_start}",
                )
            )
        return records
