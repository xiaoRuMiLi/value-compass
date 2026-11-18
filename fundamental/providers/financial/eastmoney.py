"""东方财富 financial 数据源实现。"""

from __future__ import annotations

from collections.abc import Iterator

from fundamental.models import (
    AuditOpinion,
    FinancialReport,
    ReportType,
    RevenueComposition,
    RevenueDimension,
    StatementItem,
    StatementType,
)
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float, to_percent
from fundamental.providers.registry import register
from fundamental.utils.symbols import parse_symbol

_MAINFINA_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_STATEMENT_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
_BUSINESS_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"

_STATEMENT_ENDPOINT = {
    StatementType.INCOME: "lrbAjaxNew",
    StatementType.BALANCE: "zcfzbAjaxNew",
    StatementType.CASHFLOW: "xjllbAjaxNew",
}

_STATEMENT_FIELDS = {
    StatementType.INCOME: {
        "TOTAL_OPERATE_INCOME": "营业总收入",
        "OPERATE_INCOME": "营业收入",
        "OPERATE_COST": "营业成本",
        "SALE_EXPENSE": "销售费用",
        "MANAGE_EXPENSE": "管理费用",
        "RESEARCH_EXPENSE": "研发费用",
        "FINANCE_EXPENSE": "财务费用",
        "OPERATE_PROFIT": "营业利润",
        "TOTAL_PROFIT": "利润总额",
        "NETPROFIT": "净利润",
        "PARENT_NETPROFIT": "归母净利润",
        "DEDUCT_PARENT_NETPROFIT": "扣非归母净利润",
        "BASIC_EPS": "基本每股收益",
    },
    StatementType.BALANCE: {
        "MONETARYFUNDS": "货币资金",
        "ACCOUNTS_RECE": "应收账款",
        "INVENTORY": "存货",
        "TOTAL_CURRENT_ASSETS": "流动资产合计",
        "FIXED_ASSET": "固定资产",
        "CIP": "在建工程",
        "INTANGIBLE_ASSET": "无形资产",
        "GOODWILL": "商誉",
        "TOTAL_NONCURRENT_ASSETS": "非流动资产合计",
        "TOTAL_ASSETS": "资产总计",
        "SHORT_LOAN": "短期借款",
        "ACCOUNTS_PAYABLE": "应付账款",
        "TOTAL_CURRENT_LIAB": "流动负债合计",
        "LONG_LOAN": "长期借款",
        "TOTAL_NONCURRENT_LIAB": "非流动负债合计",
        "TOTAL_LIABILITIES": "负债合计",
        "SHARE_CAPITAL": "股本",
        "CAPITAL_RESERVE": "资本公积",
        "SURPLUS_RESERVE": "盈余公积",
        "UNASSIGN_RPOFIT": "未分配利润",
        "TOTAL_PARENT_EQUITY": "归母股东权益",
        "MINORITY_EQUITY": "少数股东权益",
        "TOTAL_EQUITY": "股东权益合计",
    },
    StatementType.CASHFLOW: {
        "SALES_SERVICES": "销售商品提供劳务收到现金",
        "TOTAL_OPERATE_INFLOW": "经营活动现金流入小计",
        "BUY_SERVICES": "购买商品接受劳务支付现金",
        "PAY_STAFF_CASH": "支付给职工支付的现金",
        "PAY_ALL_TAX": "支付的各项税费",
        "TOTAL_OPERATE_OUTFLOW": "经营活动现金流出小计",
        "NETCASH_OPERATE": "经营活动现金流量净额",
        "NETCASH_INVEST": "投资活动现金流量净额",
        "NETCASH_FINANCE": "筹资活动现金流量净额",
        "CCE_ADD": "现金及现金等价物净增加额",
        "BEGIN_CCE": "期初现金及现金等价物余额",
        "END_CCE": "期末现金及现金等价物余额",
    },
}

_MAINOP_DIMENSION = {"2": RevenueDimension.PRODUCT, "3": RevenueDimension.REGION}


def _report_type(report_date: str) -> ReportType:
    month_day = report_date[5:10]
    return {
        "03-31": ReportType.Q1,
        "06-30": ReportType.H1,
        "09-30": ReportType.Q3,
        "12-31": ReportType.FY,
    }.get(month_day, ReportType.FY)


def _audit_opinion(raw: object) -> AuditOpinion | None:
    if not raw:
        return None
    text = str(raw)
    if "无法" in text:
        return AuditOpinion.DISCLAIMER
    if "否定" in text:
        return AuditOpinion.ADVERSE
    if "保留" in text:
        return AuditOpinion.QUALIFIED
    if "标准" in text or "无保留" in text:
        return AuditOpinion.STANDARD
    return None


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


@register("financial", "eastmoney")
class EastmoneyFinancialProvider:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(
            cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1)
        )

    def fetch_financials(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        report_types: list[str] | None = None,
    ) -> list[FinancialReport]:
        code, market = parse_symbol(symbol)
        secucode = f"{code}.{market.value}"
        wanted = {ReportType(t).value for t in report_types} if report_types else None
        results: list[FinancialReport] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _MAINFINA_URL,
                params={
                    "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{secucode}")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "REPORT_DATE",
                },
                ttl=3600,
            )
            rows = (payload.get("result") or {}).get("data") or []
            if not rows:
                break
            for row in rows:
                report_date = str(row.get("REPORT_DATE") or "")[:10]
                report_type = _report_type(report_date)
                if start and report_date < start:
                    continue
                if end and report_date > end:
                    continue
                if wanted and report_type.value not in wanted:
                    continue
                results.append(
                    FinancialReport(
                        report_date=report_date,
                        report_type=report_type,
                        revenue=to_float(row.get("TOTALOPERATEREVE")),
                        net_profit=to_float(row.get("PARENTNETPROFIT")),
                        net_profit_deducted=to_float(row.get("KCFJCXSYJLR")),
                        gross_margin=to_float(row.get("XSMLL")),
                        net_margin=to_float(row.get("XSJLL")),
                        roe=to_float(row.get("ROEJQ")),
                        debt_ratio=to_float(row.get("ZCFZL")),
                        current_ratio=to_float(row.get("LD")),
                        eps=to_float(row.get("EPSJB")),
                        bps=to_float(row.get("BPS")),
                        revenue_yoy=to_float(row.get("TOTALOPERATEREVETZ")),
                        net_profit_yoy=to_float(row.get("PARENTNETPROFITTZ")),
                        audit_opinion=_audit_opinion(row.get("OPINION_TYPE")),
                    )
                )
            if len(rows) < 500:
                break
            page += 1
        return results

    def fetch_statement_items(
        self,
        symbol: str,
        statement_type: StatementType | str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[StatementItem]:
        code, market = parse_symbol(symbol)
        statement = StatementType(statement_type)
        fields = _STATEMENT_FIELDS[statement]
        report_dates = [item.report_date for item in self.fetch_financials(symbol, start, end)]
        items: list[StatementItem] = []
        for chunk in _chunks(report_dates, 8):
            payload = self.client.get_json(
                f"{_STATEMENT_URL}/{_STATEMENT_ENDPOINT[statement]}",
                params={
                    "companyType": "4",
                    "reportDateType": "0",
                    "reportType": "1",
                    "dates": ",".join(chunk),
                    "code": f"{market.value}{code}",
                },
                ttl=3600,
            )
            for row in payload.get("data") or []:
                report_date = str(row.get("REPORT_DATE") or "")[:10]
                report_type = _report_type(report_date)
                for field_code, field_name in fields.items():
                    if field_code not in row:
                        continue
                    items.append(
                        StatementItem(
                            report_date=report_date,
                            report_type=report_type,
                            statement_type=statement,
                            item_code=field_code,
                            item_name=field_name,
                            item_value=to_float(row.get(field_code)),
                            item_yoy=to_float(row.get(f"{field_code}_YOY")),
                        )
                    )
        return items

    def fetch_revenue_composition(
        self,
        symbol: str,
        dimension: RevenueDimension | str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[RevenueComposition]:
        code, market = parse_symbol(symbol)
        wanted = RevenueDimension(dimension) if dimension else None
        payload = self.client.get_json(
            _BUSINESS_URL,
            params={"code": f"{market.value}{code}"},
            ttl=3600,
        )
        results: list[RevenueComposition] = []
        for row in payload.get("zygcfx") or []:
            dim = _MAINOP_DIMENSION.get(str(row.get("MAINOP_TYPE")))
            if dim is None:
                continue
            report_date = str(row.get("REPORT_DATE") or "")[:10]
            if start and report_date < start:
                continue
            if end and report_date > end:
                continue
            if wanted and dim != wanted:
                continue
            results.append(
                RevenueComposition(
                    report_date=report_date,
                    dimension=dim,
                    item_name=str(row.get("ITEM_NAME") or ""),
                    revenue=to_float(row.get("MAIN_BUSINESS_INCOME")),
                    ratio=to_percent(row.get("MBI_RATIO")),
                )
            )
        return results
