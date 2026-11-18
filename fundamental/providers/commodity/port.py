"""CommodityPort：商品价格契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import ProductPrice, StockProduct


class CommodityPort(Protocol):
    def fetch_product_prices(
        self,
        product_code: str,
        source: str = "sina",
        start: str | None = None,
        end: str | None = None,
    ) -> list[ProductPrice]: ...

    def search_products(self, keyword: str) -> list[StockProduct]: ...
