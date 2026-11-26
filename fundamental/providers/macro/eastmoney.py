"""东方财富宏观指标数据源（CPI/PMI + 中美国债10年期收益率）。"""

from __future__ import annotations

from fundamental.models import MacroIndicator
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.providers.registry import register

_URL = "https://datacenter.eastmoney.com/api/data/v1/get"

_REPORTS = {
    "cpi": ("RPT_ECONOMY_CPI", "CPI同比", "NATIONAL_SAME"),
    "pmi": ("RPT_ECONOMY_PMI", "制造业PMI", "MAKE_INDEX"),
}

# 东财「中美国债收益率」(data.eastmoney.com/cjsj/zmgzsyl.html) 接口。
# 一次返回中美各期限国债到期收益率，中国数据来自中国债券信息网、美国来自美联储。
# 字段编码固定，仅取 10 年期作为估值无风险利率锚点；value 单位为百分数（如 1.5914 = 1.5914%）。
_BOND_YIELD_URL = "https://datacenter.eastmoney.com/api/data/get"
_BOND_YIELD_FIELDS = {
    "cn10y": ("EMM00166466", "中国10年期国债收益率", "中国债券信息网（经东方财富）"),
    "us10y": ("EMG00001310", "美国10年期国债收益率", "美联储（经东方财富）"),
}
_BOND_YIELD_TOKEN = "894050c76af8597a853f5b408b759f5d"


@register("macro", "eastmoney")
class EastmoneyMacroProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_macro_indicators(
        self, codes: list[str] | None = None, start: str | None = None, end: str | None = None
    ) -> list[MacroIndicator]:
        # 未指定 codes 时返回全部指标（CPI/PMI + 中美国债10Y）。
        wanted = set(codes) if codes else (set(_REPORTS) | set(_BOND_YIELD_FIELDS))
        results: list[MacroIndicator] = []
        for code in wanted & set(_REPORTS):
            report, name, value_field = _REPORTS[code]
            results.extend(self._fetch(report, code, name, value_field, start, end))
        bond_codes = wanted & set(_BOND_YIELD_FIELDS)
        if bond_codes:
            results.extend(self._fetch_bond_yields(bond_codes, start, end))
        return results

    def _fetch(
        self,
        report: str,
        code: str,
        name: str,
        value_field: str,
        start: str | None,
        end: str | None,
    ) -> list[MacroIndicator]:
        payload = self.client.get_json(
            _URL,
            params={
                "reportName": report,
                "columns": "ALL",
                "pageNumber": "1",
                "pageSize": "500",
                "sortTypes": "-1",
                "sortColumns": "REPORT_DATE",
            },
            ttl=3600,
        )
        rows = (payload.get("result") or {}).get("data") or []
        results: list[MacroIndicator] = []
        for row in rows:
            trade_date = (str(row.get("REPORT_DATE") or "") or "")[:10]
            if start and trade_date < start:
                continue
            if end and trade_date > end:
                continue
            results.append(
                MacroIndicator(
                    indicator_code=code,
                    indicator_name=name,
                    trade_date=trade_date,
                    value=to_float(row.get(value_field)),
                    source="eastmoney",
                )
            )
        return results

    def _fetch_bond_yields(
        self, codes: set[str], start: str | None, end: str | None
    ) -> list[MacroIndicator]:
        """拉取中美国债10Y收益率序列。

        美方数据常比中方晚一个交易日（时差），因此按日期倒序逐行取值，
        各指标各自跳过空值行，保证拿到的是该市场最近一个有效交易日。
        """
        payload = self.client.get_json(
            _BOND_YIELD_URL,
            params={
                "type": "RPTA_WEB_TREASURYYIELD",
                "sty": "ALL",
                "st": "SOLAR_DATE",
                "sr": "-1",
                "token": _BOND_YIELD_TOKEN,
                "p": "1",
                "ps": "500",
                "pageNo": "1",
                "pageNum": "1",
            },
            ttl=3600,
        )
        rows = (payload.get("result") or {}).get("data") or []
        results: list[MacroIndicator] = []
        for code in codes:
            field, name, source = _BOND_YIELD_FIELDS[code]
            for row in rows:
                trade_date = (str(row.get("SOLAR_DATE") or "") or "")[:10]
                if not trade_date:
                    continue
                if start and trade_date < start:
                    continue
                if end and trade_date > end:
                    continue
                value = to_float(row.get(field))
                if value is None:
                    continue
                results.append(
                    MacroIndicator(
                        indicator_code=code,
                        indicator_name=name,
                        trade_date=trade_date,
                        value=value,
                        source=source,
                    )
                )
        return results
