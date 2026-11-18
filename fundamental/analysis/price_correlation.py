"""产品价格与股价相关性（Pearson）。"""

from __future__ import annotations

import math

from fundamental.models import PriceCorrelation


def compute_correlation(
    product_code: str,
    period: str,
    price_series: list[float],
    stock_series: list[float],
) -> PriceCorrelation:
    if len(price_series) < 3 or len(price_series) != len(stock_series):
        return PriceCorrelation(product_code=product_code, period=period, note="样本不足或长度不一致")

    count = len(price_series)
    mean_x = sum(price_series) / count
    mean_y = sum(stock_series) / count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(price_series, stock_series))
    variance_x = sum((x - mean_x) ** 2 for x in price_series)
    variance_y = sum((y - mean_y) ** 2 for y in stock_series)
    if variance_x == 0 or variance_y == 0:
        return PriceCorrelation(product_code=product_code, period=period, note="方差为 0")

    correlation = covariance / math.sqrt(variance_x * variance_y)
    beta = covariance / variance_x
    return PriceCorrelation(
        product_code=product_code,
        period=period,
        correlation=round(correlation, 4),
        beta=round(beta, 4),
    )
