"""quote 能力：实时行情、复权 K 线、历史估值。"""

from fundamental.providers.quote.eastmoney import EastmoneyQuoteProvider
from fundamental.providers.quote.tushare import TushareQuoteProvider

__all__ = ["EastmoneyQuoteProvider", "TushareQuoteProvider"]
