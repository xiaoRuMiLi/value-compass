"""东方财富持仓数据源（F10 股东研究）。"""

from __future__ import annotations

from fundamental.models import CirculatingHolder, FundHolding, HolderCategory
from fundamental.providers import ProviderNotImplementedError
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"


def _map_holder_category(raw: object) -> HolderCategory:
    if not raw:
        return HolderCategory.OTHER
    text = str(raw)
    if "基金" in text:
        return HolderCategory.FUND
    if "社保" in text:
        return HolderCategory.SOCIAL_SECURITY
    if "保险" in text:
        return HolderCategory.INSURANCE
    if "QFII" in text.upper():
        return HolderCategory.QFII
    if "个人" in text:
        return HolderCategory.INDIVIDUAL
    return HolderCategory.OTHER


@register("holding", "eastmoney")
class EastmoneyHoldingProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_circulating_holders(
        self, symbol: str, report_date: str | None = None
    ) -> list[CirculatingHolder]:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _URL, params={"code": f"{market.value}{code}"}, ttl=3600
        )
        results: list[CirculatingHolder] = []
        for row in payload.get("sdltgd") or []:
            end_date = (str(row.get("END_DATE") or "") or "")[:10]
            if report_date and end_date != report_date:
                continue
            results.append(
                CirculatingHolder(
                    report_date=end_date,
                    rank=int(row["HOLDER_RANK"]) if row.get("HOLDER_RANK") is not None else None,
                    holder_name=str(row.get("HOLDER_NAME") or ""),
                    holder_category=_map_holder_category(row.get("HOLDER_TYPE")),
                    hold_num=to_float(row.get("HOLD_NUM")),
                    hold_ratio=to_float(row.get("FREE_HOLDNUM_RATIO")),
                    is_new=str(row.get("HOLD_NUM_CHANGE") or "") == "新进",
                )
            )
        return results

    def fetch_fund_holdings(self, symbol: str, end_date: str | None = None) -> list[FundHolding]:
        raise ProviderNotImplementedError("基金持仓需解析天天基金 HTML 或 F10 jjcg，待后续实现")
