"""东方财富股吧情绪数据源（HTML 解析）。"""

from __future__ import annotations

import re
from datetime import date

from fundamental.models import Sentiment
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://guba.eastmoney.com/list,{code}.html"


def _extract_ints(text: str, key: str) -> list[int]:
    return [int(value) for value in re.findall(rf'"{key}":(\d+)', text)]


@register("sentiment", "guba")
class GubaSentimentProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.2)
        )

    def fetch_sentiment(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[Sentiment]:
        code, _ = parse_symbol(symbol)
        text = self.client.get_text(_URL.format(code=code), ttl=1800)
        post_count = len(re.findall(r'"post_title":"', text))
        read_count = sum(_extract_ints(text, "post_click_count"))
        replies = _extract_ints(text, "post_comment_count") or _extract_ints(text, "post_reply_count")
        reply_count = sum(replies)
        hot_score = round(read_count / post_count, 2) if post_count else None
        return [
            Sentiment(
                stat_date=date.today().isoformat(),
                post_count=post_count,
                read_count=read_count or None,
                reply_count=reply_count or None,
                hot_score=hot_score,
                sentiment_score=None,
            )
        ]
