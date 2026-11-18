"""东方财富市场热点（概念板块）。"""

from __future__ import annotations

from datetime import date

from fundamental.models import Hotspot
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register

_URL = "https://push2.eastmoney.com/api/qt/clist/get"


@register("hotspot", "eastmoney")
class EastmoneyHotspotProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_hotspots(self, trade_date: str | None = None) -> list[Hotspot]:
        results: list[Hotspot] = []
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
                    "fs": "m:90 t:3",
                    "fields": "f12,f14,f3,f62,f128,f136,f140",
                },
                ttl=3600,
            )
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            if not rows:
                break
            for row in rows:
                results.append(
                    Hotspot(
                        trade_date=trade_date or date.today().isoformat(),
                        board_code=str(row.get("f12") or ""),
                        board_name=str(row.get("f14") or ""),
                        pct_change=to_float(row.get("f3")),
                        main_net=to_float(row.get("f62")),
                        leader_stock_code=row.get("f140"),
                        leader_pct_change=to_float(row.get("f136")),
                    )
                )
            if len(results) >= int(data.get("total") or 0):
                break
            page += 1
        return results
