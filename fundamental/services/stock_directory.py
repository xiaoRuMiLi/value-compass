"""个股库列表编排：基础行情 + 评分标签。

评分来自 services.analyze 的纯计算（不调 LLM），但逐个算全量评分有成本，
因此按标的做短 TTL 缓存；数据同步后最多等待一个 TTL 刷新标签。
"""

from __future__ import annotations

import time
from typing import Any

from fundamental.config import load_settings
from fundamental.services.analyze import analyze_stock
from fundamental.storage import repository

_CACHE: dict[str, tuple[float, float | None, str | None]] = {}


def _rating(symbol: str) -> tuple[float | None, str | None]:
    ttl = float(load_settings().get("web.stock_rating_cache_seconds", 300))
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached[0] < ttl:
        return cached[1], cached[2]

    score: float | None = None
    rating: str | None = None
    try:
        result = analyze_stock(symbol)
        composite = result.get("score")
        score = getattr(composite, "total", None)
        rating = getattr(composite, "rating", None)
    except Exception:  # noqa: BLE001 - 单只评分失败不应让整个个股库不可用
        pass

    _CACHE[symbol] = (now, score, rating)
    return score, rating


def build_stock_directory(conn: Any) -> list[dict[str, Any]]:
    """返回个股库列表，含最新快照价与评分标签。"""
    results: list[dict[str, Any]] = []
    for stock in repository.fetch_all(conn, "SELECT * FROM stocks ORDER BY code"):
        snapshot = repository.fetch_one(
            conn,
            "SELECT price, pct_change FROM snapshots WHERE stock_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (stock["id"],),
        )
        symbol = f"{stock['code']}.{stock['market']}"
        score, rating = _rating(symbol)
        results.append(
            {
                "symbol": symbol,
                "name": stock["name"],
                "code": stock["code"],
                "market": stock["market"],
                "price": snapshot["price"] if snapshot else None,
                "pct_change": snapshot["pct_change"] if snapshot else None,
                "score": score,
                "rating": rating,
            }
        )
    return results
