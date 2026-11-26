"""东方财富 financial 数据源实现。"""

from __future__ import annotations

from collections.abc import Iterator

from fundamental.models import (
    AuditOpinion,
    FinancialReport,
    Market,
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
        "INCOME_TAX": "所得税费用",
        "NETPROFIT": "净利润",
        "PARENT_NETPROFIT": "归母净利润",
        "MINORITY_INTEREST": "少数股东损益",
        "DEDUCT_PARENT_NETPROFIT": "扣非归母净利润",
        # 财务费用明细：FCFF 口径补利息收支（现金流口径常为空）
        "FE_INTEREST_EXPENSE": "利息费用（财务费用明细）",
        "FE_INTEREST_INCOME": "利息收入（财务费用明细）",
        "INVEST_INCOME": "投资收益",
        "BASIC_EPS": "基本每股收益",
    },
    StatementType.BALANCE: {
        "MONETARYFUNDS": "货币资金",
        "ACCOUNTS_RECE": "应收账款",
        "PREPAYMENT": "预付款项",
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
        "CONTRACT_LIAB": "合同负债",
        "TOTAL_CURRENT_LIAB": "流动负债合计",
        "NONCURRENT_LIAB_1YEAR": "一年内到期的非流动负债",
        "LONG_LOAN": "长期借款",
        "SHORT_BOND_PAYABLE": "短期应付债券",
        "BOND_PAYABLE": "应付债券",
        "LEASE_LIAB": "租赁负债",
        "TOTAL_NONCURRENT_LIAB": "非流动负债合计",
        "TOTAL_LIABILITIES": "负债合计",
        # EV→股权桥接：非经营/类现金资产
        "TRADE_FINASSET": "交易性金融资产",
        "HOLD_MATURITY_INVEST": "持有至到期投资",
        "LONG_EQUITY_INVEST": "长期股权投资",
        "OTHER_EQUITY_INVEST": "其他权益工具投资",
        "INVEST_REALESTATE": "投资性房地产",
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
        # FCFE 分项：OCF − 资本开支 − 实付利息。
        # 利息收入科目仅供披露与口径核对：A 股该现金流已含在 OCF 内，按国际通用惯例
        # 不重复加回；港股 OCF 未含损益表利息收支时另走财报原文调整分支。
        "CONSTRUCT_LONG_ASSET": "购建固定资产无形资产和其他长期资产支付的现金",
        "RECEIVE_INTEREST_COMMISSION": "收取利息手续费及佣金的现金",
        "PAY_INTEREST_COMMISSION": "支付利息手续费及佣金的现金",
        # 现金流量表补充资料：经营性营运资本变动（用于现金流质量提示）
        "OPERATE_PAYABLE_ADD": "经营性应付项目的增加",
        "OPERATE_RECE_REDUCE": "经营性应收项目的减少",
        "INVENTORY_REDUCE": "存货的减少",
        # 折旧摊销：严格 FCFF 口径（EBIT×(1−税)+折旧摊销−资本开支−营运资本变动）所需
        "FA_IR_DEPR": "固定资产/油气资产/生产性生物资产折旧",
        "IA_AMORTIZE": "无形资产摊销",
        "LPE_AMORTIZE": "长期待摊费用摊销",
        "USERIGHT_ASSET_AMORTIZE": "使用权资产折旧",
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


# 港股主要指标报表：东财对港股用独立数据集，字段与 A 股不同。
_HK_MAINFIN_REPORT = "RPT_HKF10_FN_MAININDICATOR"
_HK_STATEMENT_REPORT = {
    StatementType.INCOME: "RPT_HKF10_FN_INCOME",
    StatementType.BALANCE: "RPT_HKF10_FN_BALANCE",
}
_HK_DATE_TYPE = {"001": ReportType.FY, "003": ReportType.Q1, "002": ReportType.H1, "004": ReportType.Q3}
# 港股逐科目报表用 STD_ITEM_CODE 数字编码，且同一编码在利润表/资产负债表含义不同。
# 在 adapter 层归一化成项目内部语义码（与 A 股 _STATEMENT_FIELDS 对齐），
# 让 analysis 层的比率推导无需区分市场即可复用；未映射科目保留原码，仍可在三大表查看。
_HK_ITEM_CODE_MAP: dict[StatementType, dict[str, str]] = {
    StatementType.INCOME: {
        "004001001": "TOTAL_OPERATE_INCOME",   # 营业额
        "004001999": "OPERATE_INCOME",          # 营运收入（与 financials.revenue 同口径）
        "004005001": "OPERATE_COST",            # 营运支出
        "004007999": "GROSS_PROFIT",            # 毛利
        "004010999": "OPERATE_PROFIT",          # 经营溢利
        "004011999": "TOTAL_PROFIT",            # 除税前溢利
        "004012999": "NETPROFIT",               # 除税后溢利
        "004025002": "PARENT_NETPROFIT",        # 股东应占溢利
        "004025001": "MINORITY_INTEREST",       # 少数股东损益
        "004011200": "INTEREST_INCOME",         # 利息收入
        "004011201": "INTEREST_EXPENSE",        # 融资成本
        "004012001": "INCOME_TAX",              # 税项
        "004027002": "BASIC_EPS",               # 每股基本盈利
        "004027003": "DILUTED_EPS",             # 每股摊薄盈利
    },
    StatementType.BALANCE: {
        "004001002": "FIXED_ASSET",             # 物业厂房及设备
        "004001999": "TOTAL_NONCURRENT_ASSETS", # 非流动资产合计
        "004002001": "INVENTORY",               # 存货
        "004002003": "ACCOUNTS_RECE",           # 应收账款
        "004002009": "RESTRICTED_CASH",         # 受限制存款及现金（桥接时剔除）
        "004002010": "MONETARYFUNDS",           # 现金及等价物
        "004002011": "SHORT_TERM_DEPOSIT",      # 短期存款（类现金）
        "004002013": "TRADE_FINASSET",          # 以公允价值记账之金融资产（流动）
        "004002022": "OTHER_CURRENT_FINASSET",  # 其他金融资产（流动）
        "004002999": "TOTAL_CURRENT_ASSETS",    # 流动资产合计
        "004001003": "INVEST_REALESTATE",       # 投资物业
        "004001013": "LONG_EQUITY_INVEST",      # 联营公司权益
        "004001016": "JOINT_VENTURE_INVEST",    # 合营公司权益
        "004001022": "NONCURRENT_FINASSET",     # 指定以公允价值记账之金融资产（非流动）
        "004001031": "OTHER_NONCURRENT_FINASSET",  # 其他金融资产（非流动）
        "004001030": "TERM_DEPOSIT",            # 中长期存款（类现金）
        "004009999": "TOTAL_ASSETS",            # 总资产
        "004011001": "ACCOUNTS_PAYABLE",        # 应付账款
        "004011002": "SHORT_BOND_PAYABLE",      # 应付票据（流动）
        "004011006": "SHORT_LEASE_LIAB",        # 融资租赁负债（流动）
        "004011010": "SHORT_LOAN",              # 短期贷款
        "004011999": "TOTAL_CURRENT_LIAB",      # 流动负债合计
        "004020001": "LONG_LOAN",               # 长期贷款
        "004020005": "LEASE_LIAB",              # 融资租赁负债（非流动）
        "004020018": "BOND_PAYABLE",            # 应付票据（非流动）
        "004020999": "TOTAL_NONCURRENT_LIAB",   # 非流动负债合计
        "004025999": "TOTAL_LIABILITIES",       # 总负债
        "004027999": "MINORITY_EQUITY",         # 少数股东权益
        "004030001": "SHARE_CAPITAL",           # 股本
        "004030999": "TOTAL_PARENT_EQUITY",     # 股东权益（归母）
        "004036999": "TOTAL_EQUITY",            # 总权益
    },
}

# 港股现金流量表长表在东财不存在，用主指标表披露的三大现金流净额构造。
# 只取期间流量（累计口径，由 analysis 还原单季），期末现金是时点余额故不纳入。
_HK_CASHFLOW_FIELDS = {
    "NETCASH_OPERATE": "经营活动现金流量净额",
    "NETCASH_INVEST": "投资活动现金流量净额",
    "NETCASH_FINANCE": "筹资活动现金流量净额",
}

_HK_FIELDS = {
    "revenue": "OPERATE_INCOME",
    "net_profit": "HOLDER_PROFIT",  # 归母净利润
    "operating_profit": "OPERATE_PROFIT",
    "pretax_profit": "PRETAX_PROFIT",
    "gross_margin": "GROSS_PROFIT_RATIO",
    "net_margin": "NET_PROFIT_RATIO",
    "roe": "ROE_AVG",
    "roic": "ROIC_YEARLY",
    "debt_ratio": "DEBT_ASSET_RATIO",
    "current_ratio": "CURRENT_RATIO",
    "ocf": "NETCASH_OPERATE",
    "eps": "BASIC_EPS",
    "eps_ttm": "EPS_TTM",
    "bps": "BPS",
    "revenue_yoy": "OPERATE_INCOME_YOY",
    "net_profit_yoy": "HOLDER_PROFIT_YOY",
}


def _hk_report_type(row: dict) -> ReportType:
    by_code = _HK_DATE_TYPE.get(str(row.get("DATE_TYPE_CODE") or ""))
    if by_code:
        return by_code
    return _report_type(str(row.get("STD_REPORT_DATE") or "")[:10])


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
        if market == Market.HK:
            return self._fetch_hk_financials(code, start, end, report_types)
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
                        operating_profit=to_float(row.get("OPERATE_PROFIT")),
                        pretax_profit=to_float(row.get("TOTAL_PROFIT")),
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

    def _fetch_hk_financials(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
        report_types: list[str] | None = None,
    ) -> list[FinancialReport]:
        """港股财务主要指标（东财 RPT_HKF10_FN_MAININDICATOR）。

        港股多数公司只披露年报与中报；季报口径的 ROE 为年化值（ROE_YEARLY），
        年报则用 ROE_AVG（=ROE_YEARLY），可直接映射到 FinancialReport.roe。
        """
        secucode = f"{code}.HK"
        wanted = {ReportType(t).value for t in report_types} if report_types else None
        results: list[FinancialReport] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _MAINFINA_URL,
                params={
                    "reportName": _HK_MAINFIN_REPORT,
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{secucode}")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "STD_REPORT_DATE",
                    "source": "F10",
                    "client": "PC",
                },
                ttl=3600,
            )
            rows = (payload.get("result") or {}).get("data") or []
            if not rows:
                break
            for row in rows:
                report_date = str(row.get("STD_REPORT_DATE") or "")[:10]
                report_type = _hk_report_type(row)
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
                        **{field: to_float(row.get(source)) for field, source in _HK_FIELDS.items()},
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
        if market == Market.HK:
            return self._fetch_hk_statement_items(code, statement, start, end)
        fields = _STATEMENT_FIELDS[statement]
        report_dates = [item.report_date for item in self.fetch_financials(symbol, start, end)]
        items: list[StatementItem] = []
        # 东财 xjllbAjaxNew/zcfzbAjaxNew/lrbAjaxNew 单次 dates 最多返回 5 个报告期，
        # 传更多会静默截断（例如传 8 个只回 5 个），导致历史只落到隔年数据。
        for chunk in _chunks(report_dates, 5):
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

    def _fetch_hk_statement_items(
        self,
        code: str,
        statement: StatementType,
        start: str | None = None,
        end: str | None = None,
    ) -> list[StatementItem]:
        """港股逐科目报表（东财 RPT_HKF10_FN_INCOME / BALANCE 长表）。

        港股没有 A 股三大报表接口；主要指标表只能给出汇总数，营业利润以下的
        投资/联营/利息等分项必须走这里，否则解释层只能看到一个黑箱合计。
        港股现金流量表长表在东财不存在，改由主指标表披露的三大现金流净额构造。
        """
        if statement == StatementType.CASHFLOW:
            return self._fetch_hk_cashflow_items(code, start, end)
        report_name = _HK_STATEMENT_REPORT.get(statement)
        if not report_name:
            return []
        code_map = _HK_ITEM_CODE_MAP.get(statement, {})
        items: list[StatementItem] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _MAINFINA_URL,
                params={
                    "reportName": report_name,
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{code}.HK")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "1,-1",
                    "sortColumns": "STD_REPORT_DATE,STD_ITEM_CODE",
                    "source": "F10",
                    "client": "PC",
                },
                ttl=3600,
            )
            rows = (payload.get("result") or {}).get("data") or []
            if not rows:
                break
            for row in rows:
                report_date = str(row.get("STD_REPORT_DATE") or "")[:10]
                if start and report_date < start:
                    continue
                if end and report_date > end:
                    continue
                raw_code = str(row.get("STD_ITEM_CODE") or "")
                items.append(
                    StatementItem(
                        report_date=report_date,
                        report_type=_hk_report_type(row),
                        statement_type=statement,
                        item_code=code_map.get(raw_code, raw_code),
                        item_name=str(row.get("ITEM_NAME") or ""),
                        item_value=to_float(row.get("AMOUNT")),
                        item_yoy=to_float(row.get("YOY_RATIO")),
                    )
                )
            if len(rows) < 500:
                break
            page += 1
        return items

    def _fetch_hk_cashflow_items(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[StatementItem]:
        """港股现金流量表：用主指标表的经营/投资/筹资现金流净额构造三项。

        东财港股没有现金流量表长表，分项（销售商品收现、购建固定资产等）
        不可得，这里只给出确实披露的三项净额，不编造明细。
        """
        items: list[StatementItem] = []
        page = 1
        while True:
            payload = self.client.get_json(
                _MAINFINA_URL,
                params={
                    "reportName": _HK_MAINFIN_REPORT,
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{code}.HK")',
                    "pageNumber": str(page),
                    "pageSize": "500",
                    "sortTypes": "-1",
                    "sortColumns": "STD_REPORT_DATE",
                    "source": "F10",
                    "client": "PC",
                },
                ttl=3600,
            )
            rows = (payload.get("result") or {}).get("data") or []
            if not rows:
                break
            for row in rows:
                report_date = str(row.get("STD_REPORT_DATE") or "")[:10]
                if start and report_date < start:
                    continue
                if end and report_date > end:
                    continue
                for field, name in _HK_CASHFLOW_FIELDS.items():
                    value = to_float(row.get(field))
                    if value is None:
                        continue
                    items.append(
                        StatementItem(
                            report_date=report_date,
                            report_type=_hk_report_type(row),
                            statement_type=StatementType.CASHFLOW,
                            item_code=field,
                            item_name=name,
                            item_value=value,
                        )
                    )
            if len(rows) < 500:
                break
            page += 1
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
