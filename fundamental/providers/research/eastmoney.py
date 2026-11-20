"""东方财富研报数据源。"""

from __future__ import annotations

from datetime import date

from fundamental.models import Rating, RatingChange, ResearchReport
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_URL = "https://reportapi.eastmoney.com/report/list"

_RATING_WORDS = (
    ("买入", Rating.BUY),
    ("增持", Rating.OVERWEIGHT),
    ("中性", Rating.NEUTRAL),
    ("减持", Rating.UNDERWEIGHT),
    ("卖出", Rating.SELL),
)


def _to_rating(value: object) -> Rating | None:
    """东财评级名（买入/增持/优于大市等）归一到统一枚举，无法识别时留空。"""

    if not isinstance(value, str):
        return None
    for word, rating in _RATING_WORDS:
        if word in value:
            return rating
    return None


def _to_rating_change(current: object, previous: object) -> RatingChange | None:
    """东财 emRatingValue 数值越大越乐观，与上期比较得出评级变动。"""

    now = to_float(current)
    before = to_float(previous)
    if now is None or before is None:
        return None
    if now > before:
        return RatingChange.UPGRADE
    if now < before:
        return RatingChange.DOWNGRADE
    return RatingChange.MAINTAIN


def _eps_forecast(row: dict[str, object]) -> dict[str, float]:
    fields = {
        "this_year": "predictThisYearEps",
        "next_year": "predictNextYearEps",
        "next_two_year": "predictNextTwoYearEps",
    }
    result: dict[str, float] = {}
    for key, raw_key in fields.items():
        value = to_float(row.get(raw_key))
        if value is not None:
            result[key] = value
    return result


@register("research", "eastmoney")
class EastmoneyResearchProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_reports(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[ResearchReport]:
        code, _ = parse_symbol(symbol)
        begin = (start or "2000-01-01").replace("-", "")
        finish = (end or date.today().isoformat()).replace("-", "")
        results: list[ResearchReport] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _URL,
                params={
                    "industryCode": "*",
                    "pageSize": "500",
                    "industry": "*",
                    "rating": "*",
                    "ratingChange": "*",
                    "beginTime": begin,
                    "endTime": finish,
                    "pageNo": str(page),
                    "qType": "0",
                    "code": code,
                },
                ttl=3600,
            )
            rows = payload.get("data") or []
            if not rows:
                break
            for row in rows:
                info_code = str(row.get("infoCode") or "")
                results.append(
                    ResearchReport(
                        publish_date=(str(row.get("publishDate") or "") or "")[:10] or None,
                        org_name=row.get("orgSName") or row.get("orgName"),
                        analyst=row.get("researcher") or None,
                        rating=_to_rating(row.get("emRatingName")),
                        rating_change=_to_rating_change(
                            row.get("emRatingValue"), row.get("lastEmRatingValue")
                        ),
                        target_price=to_float(row.get("indvAimPriceT"))
                        or to_float(row.get("indvAimPriceL")),
                        title=str(row.get("title") or ""),
                        source_url=f"https://data.eastmoney.com/report/info/{info_code}.html"
                        if info_code
                        else None,
                        info_code=info_code or None,
                        industry_name=row.get("indvInduName") or None,
                        industry_code=str(row.get("indvInduCode") or "") or None,
                        eps_forecast=_eps_forecast(row),
                    )
                )
            hits = int(payload.get("hits") or 0)
            if page * 500 >= hits:
                break
            page += 1
        return results
