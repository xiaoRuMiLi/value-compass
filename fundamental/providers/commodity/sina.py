"""新浪期货商品价格数据源。"""

from __future__ import annotations

import json
import re

from fundamental.models import ProductPrice, StockProduct
from fundamental.providers import ProviderNotImplementedError
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register

_KLINE_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20x=/InnerFuturesNewService.getDailyKLine"


@register("commodity", "sina")
class SinaCommodityProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.2)
        )

    def fetch_product_prices(
        self,
        product_code: str,
        source: str = "sina",
        start: str | None = None,
        end: str | None = None,
    ) -> list[ProductPrice]:
        text = self.client.get_text(_KLINE_URL, params={"symbol": product_code}, ttl=3600)
        array_text = re.search(r"\(\s*(\[.*\])\s*\)", text, re.S)
        if not array_text:
            return []
        rows = json.loads(array_text.group(1))
        results: list[ProductPrice] = []
        for row in rows:
            trade_date = str(row.get("d") or "")
            if start and trade_date < start:
                continue
            if end and trade_date > end:
                continue
            results.append(
                ProductPrice(
                    product_code=product_code,
                    source=source,
                    trade_date=trade_date,
                    open=to_float(row.get("o")),
                    high=to_float(row.get("h")),
                    low=to_float(row.get("l")),
                    close=to_float(row.get("c")),
                    volume=to_float(row.get("v")),
                )
            )
        return results

    def search_products(self, keyword: str) -> list[StockProduct]:
        raise ProviderNotImplementedError("商品目录搜索待后续接入，股票↔商品映射见 config/products.toml")
