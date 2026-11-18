"""东方财富行业板块数据源。"""

from __future__ import annotations

from datetime import date

from fundamental.models import IndustryBoard
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register

_URL = "https://push2.eastmoney.com/api/qt/clist/get"


@register("industry", "eastmoney")
class EastmoneyIndustryProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def list_industries(self, trade_date: str | None = None) -> list[IndustryBoard]:
        results: list[IndustryBoard] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _URL,
                params={
                    "pn": str(page),
                    "pz": "100",
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": "m:90 t:2",
                    "fields": "f12,f14,f3,f62,f128,f136,f184,f9,f23",
                },
                ttl=3600,
            )
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            if not rows:
                break
            for row in rows:
                results.append(
                    IndustryBoard(
                        industry_code=str(row.get("f12") or ""),
                        industry_name=str(row.get("f14") or ""),
                        trade_date=trade_date or date.today().isoformat(),
                        pct_change=to_float(row.get("f3")),
                        capital_flow=to_float(row.get("f62")),
                        pe=to_float(row.get("f9")),
                        pb=to_float(row.get("f23")),
                        leader_stock=row.get("f128"),
                    )
                )
            if len(results) >= int(data.get("total") or 0):
                break
            page += 1
        return results

    def fetch_stock_industry(self, symbol: str) -> IndustryBoard:
        raise NotImplementedError("需先接入公司行业映射（F10 公司资料）")
