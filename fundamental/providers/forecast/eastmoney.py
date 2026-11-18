"""东方财富预期差数据源（业绩预告/一致预期/财报日历）。"""

from __future__ import annotations

from fundamental.models import (
    ConsensusEstimate,
    EarningsForecast,
    ForecastType,
    ReportCalendarItem,
    ReportType,
)
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_DC_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_PROFIT_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax"

_FORECAST_MAP = {
    "预增": ForecastType.PREINCREASE,
    "预减": ForecastType.PREDECREASE,
    "扭亏": ForecastType.TURNLOSS,
    "首亏": ForecastType.FIRSTLOSS,
    "续盈": ForecastType.CONTINUEPROFIT,
    "续亏": ForecastType.CONTINUELOSS,
    "略增": ForecastType.SLIGHTINCREASE,
    "略减": ForecastType.SLIGHTDECREASE,
}

_CALENDAR_TYPE = {"1": ReportType.Q1, "2": ReportType.H1, "3": ReportType.Q3, "4": ReportType.FY}


def _map_forecast_type(raw: object) -> ForecastType | None:
    if not raw:
        return None
    text = str(raw)
    for keyword, forecast_type in _FORECAST_MAP.items():
        if keyword in text:
            return forecast_type
    return ForecastType.UNCERTAIN


@register("forecast", "eastmoney")
class EastmoneyForecastProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_earnings_forecasts(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[EarningsForecast]:
        code, _ = parse_symbol(symbol)
        payload = self.client.get_json(
            _DC_URL,
            params={
                "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "NOTICE_DATE",
            },
            ttl=3600,
        )
        results: list[EarningsForecast] = []
        for row in (payload.get("result") or {}).get("data") or []:
            notice_date = (str(row.get("NOTICE_DATE") or "") or "")[:10]
            if start and notice_date and notice_date < start:
                continue
            if end and notice_date and notice_date > end:
                continue
            results.append(
                EarningsForecast(
                    report_date=(str(row.get("REPORT_DATE") or "") or "")[:10],
                    forecast_type=_map_forecast_type(row.get("PREDICT_TYPE")),
                    notice_date=notice_date or None,
                    forecast_net_profit_low=to_float(row.get("PREDICT_AMT_LOWER")),
                    forecast_net_profit_high=to_float(row.get("PREDICT_AMT_UPPER")),
                    net_profit_yoy_low=to_float(row.get("ADD_AMP_LOWER")),
                    net_profit_yoy_high=to_float(row.get("ADD_AMP_UPPER")),
                    content=row.get("PREDICT_CONTENT"),
                )
            )
        return results

    def fetch_consensus_estimates(self, symbol: str) -> list[ConsensusEstimate]:
        code, market = parse_symbol(symbol)
        payload = self.client.get_json(
            _PROFIT_URL, params={"code": f"{market.value}{code}"}, ttl=3600
        )
        results: list[ConsensusEstimate] = []
        for row in payload.get("yctj_list") or []:
            year = str(row.get("YEAR") or "")
            if not year:
                continue
            results.append(
                ConsensusEstimate(
                    report_date=year,
                    est_revenue=to_float(row.get("TOTAL_OPERATE_INCOME")),
                    est_net_profit=to_float(row.get("PARENT_NETPROFIT")),
                    est_eps=to_float(row.get("EPS")),
                )
            )
        return results

    def fetch_report_calendar(self, symbol: str) -> list[ReportCalendarItem]:
        code, _ = parse_symbol(symbol)
        payload = self.client.get_json(
            _DC_URL,
            params={
                "reportName": "RPT_PUBLIC_BS_APPOIN",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "REPORT_DATE",
            },
            ttl=3600,
        )
        results: list[ReportCalendarItem] = []
        for row in (payload.get("result") or {}).get("data") or []:
            results.append(
                ReportCalendarItem(
                    report_type=_CALENDAR_TYPE.get(str(row.get("REPORT_TYPE")), ReportType.FY),
                    report_period=(str(row.get("REPORT_DATE") or "") or "")[:10] or None,
                    planned_date=(str(row.get("FIRST_APPOINT_DATE") or "") or "")[:10] or None,
                    actual_date=(str(row.get("ACTUAL_PUBLISH_DATE") or "") or "")[:10] or None,
                )
            )
        return results
