"""SentimentPort：公众情绪契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import Sentiment


class SentimentPort(Protocol):
    def fetch_sentiment(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[Sentiment]: ...
