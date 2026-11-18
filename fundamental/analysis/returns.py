"""分红复利与总回报计算。"""

from __future__ import annotations

from datetime import date

from fundamental.models import DividendRecord, KlineBar, ReturnResult


def compute_return(
    kline: list[KlineBar],
    dividends: list[DividendRecord],
    start: str,
    end: str,
) -> ReturnResult:
    bars = [bar for bar in kline if start <= bar.trade_date <= end]
    if len(bars) < 2:
        return ReturnResult(start_date=start, end_date=end)

    start_close = bars[0].close
    end_close = bars[-1].close
    price_return = end_close / start_close - 1.0 if start_close else 0.0

    period_dividends = [
        div for div in dividends if div.ex_date and start <= div.ex_date <= end and div.cash_per_share
    ]
    cumulative = sum(div.cash_per_share for div in period_dividends)
    dividend_return = cumulative / start_close if start_close else 0.0
    total_return = price_return + dividend_return

    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    years = days / 365.25
    cagr = (
        (1.0 + total_return) ** (1.0 / years) - 1.0
        if years > 0 and total_return > -1.0
        else None
    )
    yield_series = [
        {
            "date": div.ex_date,
            "cash_per_share": div.cash_per_share,
            "yield": div.cash_per_share / end_close if end_close else None,
        }
        for div in period_dividends
    ]
    return ReturnResult(
        start_date=start,
        end_date=end,
        price_return=round(price_return, 6),
        dividend_return=round(dividend_return, 6),
        total_return=round(total_return, 6),
        total_return_reinvested=None,
        cagr=round(cagr, 6) if cagr is not None else None,
        cagr_reinvested=None,
        cumulative_dividends=round(cumulative, 4),
        dividend_yield_series=yield_series,
    )
