"""公司官网数据源（极简占位：抓首页标题，站点模板后续扩展）。"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fundamental.config import load_settings
from fundamental.models import OfficialUpdate
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol


@register("official", "official")
class OfficialSiteProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.3)
        )

    def fetch_official_updates(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[OfficialUpdate]:
        code, _ = parse_symbol(symbol)
        website = load_settings().get(f"websites.{code}")
        if not website:
            return []
        try:
            text = self.client.get_text(website, ttl=3600)
        except Exception:
            return []
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else website
        return [
            OfficialUpdate(
                fetched_at=datetime.now(timezone.utc).isoformat(),
                category="site",
                title=title,
                url=website,
            )
        ]
