"""股票代码与市场归一化。"""

from __future__ import annotations

from fundamental.models import Market


def parse_symbol(symbol: str) -> tuple[str, Market]:
    """把 600519 / 600519.SH / sh600519 / 00700.HK / BABA.US 解析为 (code, Market)。"""
    value = symbol.strip()
    if not value:
        raise ValueError("symbol 不能为空")
    upper = value.upper()

    if "." in upper:
        code, market = upper.rsplit(".", 1)
        market = Market(market)
        return _normalize_code(code, market), market
    if upper.startswith("SH"):
        return upper[2:], Market.SH
    if upper.startswith("SZ"):
        return upper[2:], Market.SZ
    if upper.startswith("BJ"):
        return upper[2:], Market.BJ

    if upper.isdigit():
        if len(upper) == 6:
            if upper[0] == "6":
                return upper, Market.SH
            if upper[0] in ("0", "3"):
                return upper, Market.SZ
            if upper[0] in ("4", "8"):
                return upper, Market.BJ
            raise ValueError(f"无法推断 A 股市场: {symbol}")
        if len(upper) <= 5:
            return upper.zfill(5), Market.HK
        raise ValueError(f"无法识别数字代码: {symbol}")

    if upper.isalpha():
        return upper, Market.US
    raise ValueError(f"无法识别 symbol: {symbol}")


def to_symbol(code: str, market: Market | str) -> str:
    return f"{code}.{Market(market).value}"


def _normalize_code(code: str, market: Market) -> str:
    if market == Market.HK:
        return code.zfill(5)
    return code
