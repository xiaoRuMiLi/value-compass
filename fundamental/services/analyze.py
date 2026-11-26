"""分析编排：DB → models → 各分析模块 → 汇总结果。"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Type, TypeVar

from fundamental.analysis import (
    comparison as comparison_mod,
    cycle as cycle_mod,
    delisting as delisting_mod,
    financial as financial_mod,
    financial_review as review_mod,
    fraud_score as fraud_mod,
    integrity as integrity_mod,
    moat as moat_mod,
    price_correlation as price_correlation_mod,
    redflags as redflags_mod,
    returns as returns_mod,
)
from fundamental.analysis.masters.buffett import BuffettModel
from fundamental.analysis.masters.duan_yongping import DuanYongpingModel
from fundamental.analysis.valuation import (
    evaluate_dcf,
    evaluate_fcf,
    evaluate_ddm,
    evaluate_fcff,
    evaluate_graham,
    evaluate_relative,
)
from fundamental.services.risk_free import resolve_risk_free
from fundamental.models import (
    AuditOpinion,
    BehaviorType,
    CirculatingHolder,
    DividendRecord,
    FinancialReport,
    HolderCategory,
    IntegrityCategory,
    IntegrityRecord,
    KlineBar,
    Market,
    ReportType,
    Severity,
    Snapshot,
    StatementItem,
    StatementType,
    Stock,
    ValuationPoint,
)
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol

_T = TypeVar("_T")


def _to_model(
    cls: Type[_T],
    row: Any,
    enum_fields: dict[str, Type] | None = None,
    bool_fields: set[str] | None = None,
) -> _T:
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in row.keys():
            continue
        value = row[field.name]
        if enum_fields and field.name in enum_fields and value is not None:
            value = enum_fields[field.name](value)
        if bool_fields and field.name in bool_fields:
            value = bool(value)
        values[field.name] = value
    return cls(**values)


def _load_stock(conn: Any, stock_id: int) -> Stock:
    row = repository.fetch_one(conn, "SELECT * FROM stocks WHERE id = ?", (stock_id,))
    return _to_model(Stock, row, {"market": Market}, {"is_st"})


def _load_snapshot(conn: Any, stock_id: int) -> Snapshot | None:
    row = repository.fetch_one(
        conn, "SELECT * FROM snapshots WHERE stock_id = ? ORDER BY fetched_at DESC LIMIT 1", (stock_id,)
    )
    return _to_model(Snapshot, row) if row else None


def _load_financials(conn: Any, stock_id: int) -> list[FinancialReport]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM financials WHERE stock_id = ? ORDER BY report_date ASC", (stock_id,)
    )
    return [
        _to_model(FinancialReport, row, {"report_type": ReportType, "audit_opinion": AuditOpinion})
        for row in rows
    ]


def _load_kline(conn: Any, stock_id: int) -> list[KlineBar]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM kline WHERE stock_id = ? ORDER BY trade_date ASC", (stock_id,)
    )
    return [_to_model(KlineBar, row) for row in rows]


def _load_valuations(conn: Any, stock_id: int) -> list[ValuationPoint]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM valuations WHERE stock_id = ? ORDER BY trade_date ASC", (stock_id,)
    )
    return [_to_model(ValuationPoint, row) for row in rows]


def _load_dividends(conn: Any, stock_id: int) -> list[DividendRecord]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM dividends WHERE stock_id = ? ORDER BY ex_date DESC", (stock_id,)
    )
    return [_to_model(DividendRecord, row) for row in rows]


def _load_holders(conn: Any, stock_id: int) -> list[CirculatingHolder]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM circulating_holders WHERE stock_id = ? ORDER BY report_date DESC", (stock_id,)
    )
    return [
        _to_model(CirculatingHolder, row, {"holder_category": HolderCategory}, {"is_new"})
        for row in rows
    ]


def _load_integrity(conn: Any, stock_id: int) -> list[IntegrityRecord]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM integrity_records WHERE stock_id = ? ORDER BY record_date DESC", (stock_id,)
    )
    return [
        _to_model(
            IntegrityRecord,
            row,
            {"behavior_type": BehaviorType, "category": IntegrityCategory, "severity": Severity},
        )
        for row in rows
    ]


def _load_statement_items(conn: Any, stock_id: int) -> list[StatementItem]:
    rows = repository.fetch_all(
        conn, "SELECT * FROM statement_items WHERE stock_id = ?", (stock_id,)
    )
    return [
        _to_model(StatementItem, row, {"report_type": ReportType, "statement_type": StatementType})
        for row in rows
    ]


def _latest_dividend(dividends: list[DividendRecord]) -> float | None:
    return next((item.cash_per_share for item in dividends if item.cash_per_share), None)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calibration_params(symbol: str) -> dict[str, Any] | None:
    """惰性读取校准参数，避免 analyze 与校准模块在导入期形成环。"""

    try:
        from fundamental.services.narrative._valuation_params import load_calibration_params

        return load_calibration_params(symbol)
    except Exception:  # 校准是增强项，任何失败都回退到 analysis 默认参数
        return None


def _resolve_dps(
    symbol: str, dividends: list[DividendRecord], shares: float | None
) -> tuple[float | None, dict[str, Any] | None]:
    """为 DDM 选定每股股息输入，返回 (dps元, 口径详情)。

    - 港股：股东回报口径 = 每股现金分红 + 每股回购注销，均换算成 CNY。
      东财港股分红接口只覆盖 A 股，需走专门的港股 provider；回购注销从年报原文抽。
    - A 股：沿用最近一次 cash_per_share（人民币）。
    """
    code, market = parse_symbol(symbol)
    if market == Market.HK:
        try:
            from fundamental.services.fx import hkd_to_cny
            from fundamental.services.hk_financial_supplement import total_shareholder_return_per_share

            fx = hkd_to_cny()
            detail = total_shareholder_return_per_share(
                symbol, dividends, shares, fx["rate"]
            )
            detail["fx_source"] = fx["source"]
            detail["fx_date"] = fx["date"]
            return detail.get("total_per_share_cny"), detail
        except Exception:  # noqa: BLE001
            return None, None
    return _latest_dividend(dividends), None


def _fcf_components(statement_items: list[StatementItem]) -> dict[str, float | None]:
    """按最新年报构造标准 FCF 分项：OCF − 资本开支 + 利息收入 − 利息支出。

    数据源缺少某项时按 0 处理并在 note 中说明，由前端计算器允许用户补填。
    """
    result: dict[str, float | None] = {
        "ocf": None, "capex": 0.0, "interest_income": 0.0,
        "interest_expense": 0.0, "fcf": None, "note": None,
    }
    yearly = [
        item for item in statement_items
        if item.report_type == ReportType.FY and item.statement_type == StatementType.CASHFLOW
    ]
    if not yearly:
        result["note"] = "库内无最新年报现金流量表，FCF 分项按 0 处理（可在计算器内填写）"
        return result
    latest_date = max(item.report_date for item in yearly)
    items = [item for item in yearly if item.report_date == latest_date]

    def by_code(code: str) -> float | None:
        return next((i.item_value for i in items if i.item_code == code), None)

    ocf = by_code("NETCASH_OPERATE")
    capex = by_code("CONSTRUCT_LONG_ASSET")
    interest_income = by_code("RECEIVE_INTEREST_COMMISSION")
    interest_expense = by_code("PAY_INTEREST_COMMISSION")
    # 区分两种情况：科目整列未采集（库内缺少）vs 科目有披露但值为 0/空（该年没有该项）。
    # 后者是年报"披露即 0"的正常情形，不应让用户以为数据缺失。
    missing = [
        label for label, code in (
            ("经营活动现金流量净额", "NETCASH_OPERATE"),
            ("购建固定资产等支付的现金（资本开支）", "CONSTRUCT_LONG_ASSET"),
            ("利息收入", "RECEIVE_INTEREST_COMMISSION"),
            ("利息支出", "PAY_INTEREST_COMMISSION"),
        )
        if not any(i.item_code == code for i in items)
    ]
    zero_disclosed = [
        label for label, value in (
            ("资本开支", capex), ("利息收入", interest_income), ("利息支出", interest_expense)
        )
        if value is None or value == 0
    ]
    result.update({
        "ocf": ocf,
        "capex": capex,
        "interest_income": interest_income,
        "interest_expense": interest_expense,
        "fcf": None if ocf is None else (
            ocf - (capex or 0.0) + (interest_income or 0.0) - (interest_expense or 0.0)
        ),
        "note": _fcf_note(latest_date, ocf, capex, interest_income, interest_expense, missing, zero_disclosed),
    })
    return result


def _fcf_note(
    date: str,
    ocf: float | None,
    capex: float | None,
    interest_income: float | None,
    interest_expense: float | None,
    missing: list[str],
    zero_disclosed: list[str],
) -> str:
    """用中文分项展示标准 FCF 的实际取数，避免误把披露为 0 当成数据缺失。"""
    def yi(value: float | None) -> str:
        return "—" if value is None else f"{value / 1e8:.2f}亿"

    note = (
        f"标准FCF取 {date} 年报：经营活动现金流量净额 {yi(ocf)} − 资本开支 {yi(capex)} "
        f"+ 利息收入 {yi(interest_income)} − 利息支出 {yi(interest_expense)}"
    )
    if zero_disclosed:
        note += f"；{'、'.join(zero_disclosed)}该年披露为 0 / 未单列，按 0 计入"
    if missing:
        note += f"；库内整列未采集：{'、'.join(missing)}"
    return note


def _ocf_value(statement_items: list[StatementItem]) -> float | None:
    """最新年报经营活动现金流量净额。"""
    return _fcf_components(statement_items)["ocf"]


def _compute_fcf(statement_items: list[StatementItem]) -> float | None:
    return _fcf_components(statement_items)["fcf"]


def _resolve_fcf_components(
    symbol: str, statement_items: list[StatementItem]
) -> dict[str, float | None]:
    """为 DCF/FCF 折现选定现金流分项，返回与 _fcf_components 同构的字典。"""
    code, market = parse_symbol(symbol)
    if market == Market.HK:
        try:
            from fundamental.services.hk_financial_supplement import (
                extract_cashflow_ttm, extract_fcf_ttm,
            )

            # 优先用拆分口径：OCF / 资本开支 / 利息收支各自来自财报原文，
            # 标准 FCF 由公式算出。旧版直接把「公司披露 FCF」塞进 ocf 并把
            # capex/利息填 0，页面会误显示资本开支为 0，必须避免。
            parts = extract_cashflow_ttm(symbol)
            if parts.get("available") and parts.get("ocf_ttm_yi") is not None:
                to_yuan = lambda v: None if v is None else float(v) * 1e8
                ocf, capex = to_yuan(parts["ocf_ttm_yi"]), to_yuan(parts["capex_ttm_yi"])
                inc, exp = to_yuan(parts["interest_income_yi"]), to_yuan(parts["interest_expense_yi"])
                fcf = None if ocf is None else (
                    ocf - (capex or 0.0) + (inc or 0.0) - (exp or 0.0)
                )
                note = parts.get("note") or ""
                if parts.get("reported_fcf_ttm_yi") is not None:
                    note += (
                        f" 公司披露口径自由现金流 TTM {parts['reported_fcf_ttm_yi']:.2f}亿，"
                        "与标准FCF的差额为媒体内容付款+租赁负债付款等公司自定扣项。"
                    )
                return {
                    "ocf": ocf, "capex": capex, "interest_income": inc,
                    "interest_expense": exp, "fcf": fcf, "note": note,
                }
            # 拆分口径不可用时退到公司披露 FCF：明确标注 ocf 缺失，不伪造 capex/利息为 0。
            supplement = extract_fcf_ttm(symbol)
            if supplement.get("available") and supplement.get("fcf_ttm_yi") is not None:
                return {
                    "ocf": None, "capex": None, "interest_income": None,
                    "interest_expense": None,
                    "fcf": float(supplement["fcf_ttm_yi"]) * 1e8,
                    "note": (supplement.get("note") or "财报原文自由现金流 TTM")
                    + "（仅取到公司披露FCF，OCF/资本开支/利息未拆分，不按0冒充）",
                }
        except Exception:  # noqa: BLE001 - 补充数据失败不应拖垮主分析
            pass
        return {
            "ocf": None, "capex": None, "interest_income": None,
            "interest_expense": None, "fcf": None, "note": None,
        }
    return _fcf_components(statement_items)


def _resolve_fcf(symbol: str, statement_items: list[StatementItem]) -> tuple[float | None, str | None]:
    """兼容旧调用：返回 (fcf元, 口径说明)。"""
    comps = _resolve_fcf_components(symbol, statement_items)
    return comps["fcf"], comps["note"]


# EV→股权桥接取数口径：A 股用东财三大报表归一化码，港股用东财港股长表归一化码。
_FCFF_BRIDGE_CODES = {
    "A": {
        "cash": ["MONETARYFUNDS"],
        "marketable": ["TRADE_FINASSET"],
        "nonoperating": [
            "LONG_EQUITY_INVEST", "OTHER_EQUITY_INVEST",
            "HOLD_MATURITY_INVEST", "INVEST_REALESTATE",
        ],
        "debt": [
            "SHORT_LOAN", "NONCURRENT_LIAB_1YEAR", "LONG_LOAN",
            "SHORT_BOND_PAYABLE", "BOND_PAYABLE", "LEASE_LIAB",
        ],
        "interest": ["FE_INTEREST_EXPENSE", "INTEREST_EXPENSE"],
    },
    "HK": {
        # 受限制存款（004002009）显式不纳入，避免把不可自由支配的现金加回。
        "cash": ["MONETARYFUNDS", "SHORT_TERM_DEPOSIT"],
        "marketable": ["TRADE_FINASSET", "TERM_DEPOSIT"],
        "nonoperating": [
            "LONG_EQUITY_INVEST", "JOINT_VENTURE_INVEST",
            "NONCURRENT_FINASSET", "INVEST_REALESTATE",
        ],
        "debt": [
            "SHORT_LOAN", "LONG_LOAN", "SHORT_BOND_PAYABLE",
            "BOND_PAYABLE", "SHORT_LEASE_LIAB", "LEASE_LIAB",
        ],
        "interest": ["INTEREST_EXPENSE"],
    },
}


def _fcff_inputs(
    symbol: str,
    statement_items: list[StatementItem],
    financials: list[FinancialReport],
    fcf_components: dict[str, float | None],
) -> dict[str, Any]:
    """为 FCFF/WACC 模型取桥接科目，缺项返回 None 并在 note 中说明。"""
    code, market = parse_symbol(symbol)
    codes = _FCFF_BRIDGE_CODES["HK" if market == Market.HK else "A"]

    def latest_fy(statement: StatementType) -> tuple[list[StatementItem], str | None]:
        items = [i for i in statement_items
                 if i.statement_type == statement and i.report_type == ReportType.FY]
        if not items:
            return [], None
        latest_date = max(i.report_date for i in items)
        return [i for i in items if i.report_date == latest_date], latest_date

    balance, balance_date = latest_fy(StatementType.BALANCE)
    income, _income_date = latest_fy(StatementType.INCOME)

    def item_value(items: list[StatementItem], item_code: str) -> float | None:
        return next((i.item_value for i in items if i.item_code == item_code), None)

    def summed(items: list[StatementItem], item_codes: list[str]) -> float | None:
        values = [item_value(items, c) for c in item_codes]
        return sum(v for v in values if v is not None) if any(v is not None for v in values) else None

    # 利息优先用现金流「支付利息手续费及佣金的现金」；A 股该科目常空，退到利润表财务费用明细。
    interest = fcf_components.get("interest_expense")
    if interest is None:
        interest = next(
            (v for c in codes["interest"] if (v := item_value(income, c)) is not None),
            None,
        )
    # 最低经营现金按最近一个完整财年营收计提，不能用最新季报的累计营收。
    fy_reports = [f for f in financials if f.report_type == ReportType.FY]
    revenue = fy_reports[-1].revenue if fy_reports else None
    if revenue is None:
        revenue = next(
            (item_value(income, c) for c in ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME")
             if item_value(income, c) is not None),
            None,
        )
    result: dict[str, float | None] = {
        "ocf": fcf_components.get("ocf"),
        "capex": fcf_components.get("capex"),
        "interest_expense": interest,
        "cash": summed(balance, codes["cash"]),
        "marketable_securities": summed(balance, codes["marketable"]),
        "nonoperating_invest": summed(balance, codes["nonoperating"]),
        "debt": summed(balance, codes["debt"]),
        "minority_equity": item_value(balance, "MINORITY_EQUITY"),
        "revenue": revenue,
    }
    missing = [label for label, value in (
        ("经营现金流", result["ocf"]), ("资本开支", result["capex"]),
        ("利息支出", interest), ("货币资金", result["cash"]),
        ("有息负债", result["debt"]), ("少数股东权益", result["minority_equity"]),
        ("营业收入", revenue),
    ) if value is None]
    note = (
        f"FCFF 桥接取 {balance_date or '最新'} 年报资产负债表：有息负债、货币资金、"
        "类现金金融资产、非经营投资、少数股东权益；超额现金按营业收入的 2% 留足经营周转，"
        "非经营投资按 8 折计入"
    )
    if market == Market.HK:
        note += "；港股现金含短期存款、不含受限制存款，非经营投资含联营/合营与指定金融资产"
    if missing:
        note += "；未取到：" + "、".join(missing) + "（按 0 桥接，可在计算器补填）"
    result["note"] = note
    return result


def _working_capital_changes(
    statement_items: list[StatementItem],
) -> tuple[dict[str, float], str]:
    """取最近两个年报的营运资本科目，返回 (各科变动, 口径说明)。

    变动 = 最新年报 − 上一份年报；正数代表该项增加（占用现金或占用他人资金）。
    库存/应收/预付的 item_code 见下；缺失的科目返回 0 并在说明里标注，
    由前端计算器允许用户覆盖。
    """
    codes = {
        "inventory": "INVENTORY",
        "receivable": "ACCOUNTS_RECE",
        "payable": "ACCOUNTS_PAYABLE",
        "prepaid": "PREPAYMENT",
        "contract_liability": "CONTRACT_LIAB",
    }
    balance = [
        item for item in statement_items
        if item.statement_type == StatementType.BALANCE and item.report_type == ReportType.FY
    ]
    dates = sorted({item.report_date for item in balance})
    if len(dates) < 2:
        return {key: 0.0 for key in codes}, "只有单期年报，营运资本变动按 0 处理（可在计算器内填写）"
    latest, prev = dates[-1], dates[-2]
    values: dict[str, float] = {}
    for key, code in codes.items():
        def at(day: str) -> float | None:
            return next(
                (item.item_value for item in balance
                 if item.report_date == day and item.item_code == code and item.item_value is not None),
                None,
            )
        new_v, old_v = at(latest), at(prev)
        values[key] = (new_v - old_v) if (new_v is not None and old_v is not None) else 0.0
    missing = [k for k, code in codes.items()
               if not any(i.report_date in (latest, prev) and i.item_code == code for i in balance)]
    note = f"营运资本差额取当期 {latest} 与上一期 {prev} 年报资产负债表科目差额，仅作提示、不进入FCF公式"
    if missing:
        note += "；" + "、".join(missing) + " 库内无对应科目，按 0 处理（可在计算器内填写）"
    note += "；该差额为时点余额差，与现金流量表补充资料「经营性应付/应收项目增加、存货减少」口径不同，以财报原文为准"
    return values, note


def _yi(value: float | None) -> str:
    return "-" if value is None else f"{value / 1e8:.2f}亿"


def _signed_yi(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{'+' if value >= 0 else '-'}{abs(value) / 1e8:.2f}亿"


def _cashflow_wc_changes(statement_items: list[StatementItem]) -> dict[str, float | None]:
    """现金流量表补充资料的营运资本变动（口径最贴近"粉饰"来源）。

    注意符号：原文「经营性应付项目的增加」为正表示占用他人资金、抬高 OCF；
    「经营性应收项目的减少」为正表示回款、抬高 OCF；「存货的减少」为正表示去库存、抬高 OCF。
    这里保留原文符号，展示时按各自含义描述。
    """
    yearly = [
        item for item in statement_items
        if item.statement_type == StatementType.CASHFLOW and item.report_type == ReportType.FY
    ]
    if not yearly:
        return {}
    latest = max(item.report_date for item in yearly)
    codes = {
        "payable_increase": "OPERATE_PAYABLE_ADD",
        "receivable_decrease": "OPERATE_RECE_REDUCE",
        "inventory_decrease": "INVENTORY_REDUCE",
    }
    return {
        key: next((i.item_value for i in yearly
                   if i.report_date == latest and i.item_code == code), None)
        for key, code in codes.items()
    }


def _cashflow_quality_note(
    ocf: float | None,
    net_profit: float | None,
    wc_changes: dict[str, float],
    wc_note: str | None,
    cashflow_wc: dict[str, float | None] | None = None,
) -> str | None:
    """把营运资本差额与经营现金流/净利润落差写成可展示的提示。"""
    if ocf is None and net_profit is None and not wc_changes and not cashflow_wc:
        return None
    parts = ["营运资本差额仅作现金流质量提示，不参与FCF公式"]
    # 现金流量表补充资料是"经营性应付/应收增加"的直接披露，最能解释 OCF 与利润的背离。
    if cashflow_wc and any(v is not None for v in cashflow_wc.values()):
        parts.append(
            "现金流量表补充资料："
            + "经营性应付项目增加 " + _signed_yi(cashflow_wc.get("payable_increase"))
            + "、经营性应收项目减少 " + _signed_yi(cashflow_wc.get("receivable_decrease"))
            + "、存货减少 " + _signed_yi(cashflow_wc.get("inventory_decrease"))
        )
    if wc_note:
        parts.append(wc_note)
    parts.append(
        "资产负债表时点差额（增减额）：存货增加 " + _signed_yi(wc_changes.get("inventory"))
        + "、应收增加 " + _signed_yi(wc_changes.get("receivable"))
        + "、应付增加 " + _signed_yi(wc_changes.get("payable"))
        + "、预付款项增加 " + _signed_yi(wc_changes.get("prepaid"))
        + "、合同负债（经销商预付款）增加 " + _signed_yi(wc_changes.get("contract_liability"))
    )
    if wc_changes.get("contract_liability"):
        parts.append(
            "合同负债（经销商预付款）增加代表先收到经销商货款，会抬高当期经营现金流但不等于已实现利润，"
            "减少则相反；该科目已含在经营性应付项目增加内，此处单列仅为提示"
        )
    if wc_changes.get("prepaid"):
        parts.append(
            "预付款项（公司预付供应商货款）增加代表现金先流出、存货尚未入库，会压低当期经营现金流，"
            "减少则相反；该科目已含在经营性应收项目减少的反向口径内，此处单列仅为提示"
        )
    if ocf is not None and net_profit is not None:
        gap = ocf - net_profit
        relation = "高" if gap >= 0 else "低"
        parts.append(
            f"经营现金流 {_yi(ocf)} 较净利润 {_yi(net_profit)}{relation} {_yi(abs(gap))}"
        )
        if gap > 0:
            parts.append(
                "若差额主要来自应付项目阶段性增加或营运资本释放，标准FCF起点可能偏高，"
                "应结合后续付款节奏与回款质量核对"
            )
    return "；".join(parts)


def _latest_income_value(items: list[StatementItem], name: str) -> float | None:
    income = [item for item in items if item.statement_type == StatementType.INCOME]
    if not income:
        return None
    latest_date = max(item.report_date for item in income)
    for item in income:
        if item.report_date == latest_date and item.item_name == name:
            return item.item_value
    return None


def _selling_expense_ratio(statement_items: list[StatementItem]) -> float | None:
    selling = _latest_income_value(statement_items, "销售费用")
    revenue = _latest_income_value(statement_items, "营业总收入") or _latest_income_value(
        statement_items, "营业收入"
    )
    if selling is None or not revenue:
        return None
    return selling / revenue * 100


def analyze_stock(
    symbol: str,
    db_path: Path | None = None,
    valuation_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """valuation_params 由 services/narrative 的校准模块注入（g/r/永续），不传则用 analysis 默认值。"""

    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {"symbol": symbol, "error": "股票不存在，请先运行 fetch_data"}

        financials = _load_financials(conn, stock_id)
        kline = _load_kline(conn, stock_id)
        valuations = _load_valuations(conn, stock_id)
        dividends = _load_dividends(conn, stock_id)
        snapshot = _load_snapshot(conn, stock_id)
        integrity = _load_integrity(conn, stock_id)
        statement_items = _load_statement_items(conn, stock_id)

        latest = financials[-1] if financials else None
        price = snapshot.price if snapshot else None
        market_cap = snapshot.market_cap if snapshot else None
        shares = market_cap / price if (market_cap and price) else None
        eps = latest.eps if latest else None
        bps = latest.bps if latest else None
        pe_ttm = valuations[-1].pe_ttm if valuations else None

        dividend_yield = None
        latest_dps, dps_detail = _resolve_dps(symbol, dividends, shares)
        if latest_dps and price:
            dividend_yield = latest_dps / price * 100

        score = (
            financial_mod.score_financial(
                latest, dividend_yield, pe_ttm, _selling_expense_ratio(statement_items)
            )
            if latest
            else None
        )
        ret = returns_mod.compute_return(
            kline, dividends, (date.today() - timedelta(days=365 * 5)).isoformat(), date.today().isoformat()
        )
        fcf_components = _resolve_fcf_components(symbol, statement_items)
        fcf_value = fcf_components["fcf"]
        # 「现金流折现」用未扣资本开支/利息的经营活动现金流净额作为期初；
        # 「自由现金流折现」才按标准 FCF（OCF−资本开支+利息收入−利息支出）。
        # 两者口径不同，避免两张卡片数值完全撞车。
        ocf_value = fcf_components["ocf"] if fcf_components["ocf"] is not None else fcf_value
        dcf_basis = (
            "现金流折现期初采用经营活动现金流量净额（未扣资本开支与利息收支）；"
            "标准FCF口径见下方自由现金流折现卡片。"
        )
        fcf_note = fcf_components["note"]
        wc_changes, wc_note = _working_capital_changes(statement_items)
        cashflow_wc = _cashflow_wc_changes(statement_items)
        yearly_items = [i for i in statement_items
                        if i.report_type == ReportType.FY and i.statement_type == StatementType.INCOME]
        latest_fy = max((i.report_date for i in yearly_items), default=None)
        net_profit = next(
            (i.item_value for i in yearly_items
             if i.report_date == latest_fy and i.item_code == "NETPROFIT"), None
        )
        # g/r 以校准模块为准；未生成校准时回退到 analysis 默认参数。
        params = valuation_params if isinstance(valuation_params, dict) else _calibration_params(symbol)
        ddm_p = (params or {}).get("ddm") or {}
        dcf_p = (params or {}).get("dcf") or {}
        acf_p = (params or {}).get("adjusted_fcf") or (params or {}).get("dcf") or {}
        fcff_p = (params or {}).get("fcff") or (params or {}).get("dcf") or {}
        fcff_inputs = _fcff_inputs(symbol, statement_items, financials, fcf_components)
        # 无风险利率锚点：A股取中国10Y国债、其它取美国10Y国债，作为 WACC 下限。
        # 复用已打开的 conn，避免重复开库；缺数据时返回 missing=True，不施加下限。
        rf_anchor = resolve_risk_free(symbol, conn)
        valuations_result = [
            evaluate_relative(valuations, eps, price),
            evaluate_graham(eps, bps, price),
            evaluate_ddm(
                latest_dps,
                price,
                growth=_as_float(ddm_p.get("growth"), 0.05),
                discount=_as_float(ddm_p.get("discount"), 0.08),
            ),
            evaluate_dcf(
                ocf_value,
                shares,
                price,
                growth=_as_float(dcf_p.get("growth"), 0.10),
                discount=_as_float(dcf_p.get("discount"), 0.09),
                terminal_growth=_as_float(dcf_p.get("terminal_growth"), 0.03),
            ),
            evaluate_fcf(
                ocf=ocf_value,
                capex=fcf_components["capex"],
                interest_income=fcf_components["interest_income"],
                interest_expense=fcf_components["interest_expense"],
                shares=shares,
                current_price=price,
                growth=_as_float(acf_p.get("growth"), 0.04),
                discount=_as_float(acf_p.get("discount"), 0.10),
                terminal_growth=_as_float(acf_p.get("terminal_growth"), 0.02),
                working_capital=wc_changes,
                working_capital_alert=_cashflow_quality_note(
                    fcf_components["ocf"], net_profit, wc_changes, wc_note, cashflow_wc
                ),
                component_note=fcf_note,
            ),
            evaluate_fcff(
                ocf=fcff_inputs["ocf"],
                shares=shares,
                current_price=price,
                interest_expense=fcff_inputs["interest_expense"],
                capex=fcff_inputs["capex"],
                cash=fcff_inputs["cash"],
                marketable_securities=fcff_inputs["marketable_securities"],
                nonoperating_invest=fcff_inputs["nonoperating_invest"],
                debt=fcff_inputs["debt"],
                minority_equity=fcff_inputs["minority_equity"],
                revenue=fcff_inputs["revenue"],
                growth=_as_float(fcff_p.get("growth"), 0.05),
                discount=_as_float(fcff_p.get("discount"), 0.10),
                terminal_growth=_as_float(fcff_p.get("terminal_growth"), 0.02),
                component_note=fcff_inputs["note"],
                risk_free=rf_anchor.as_dict(),
            ),
        ]
        # 港股 DCF 用的是原文 FCF TTM，DDM 用的是分红+回购口径；
        # 把口径说明附到结果里供前端/叙述层标注来源。
        for vr in valuations_result:
            if vr.method == "dcf":
                vr.assumptions["param_source"] = "calibrated" if dcf_p else "default"
                vr.assumptions["basis"] = dcf_basis
                if fcf_note:
                    vr.assumptions["fcf_source"] = fcf_note
            elif vr.method == "ddm":
                vr.assumptions["param_source"] = "calibrated" if ddm_p else "default"
                if dps_detail:
                    vr.assumptions["dps_basis"] = dps_detail
            elif vr.method == "adjusted_fcf":
                vr.assumptions["param_source"] = "calibrated" if acf_p else "default"
                if fcf_note:
                    vr.assumptions["fcf_source"] = fcf_note
                if wc_note:
                    vr.assumptions["working_capital_basis"] = wc_note
                if cashflow_wc and any(v is not None for v in cashflow_wc.values()):
                    vr.assumptions["cashflow_wc"] = cashflow_wc
                vr.inputs["sources"] = {
                    "net_profit": net_profit,
                    "ocf": fcf_components["ocf"],
                    "capex": fcf_components["capex"],
                    "interest_income": fcf_components["interest_income"],
                    "interest_expense": fcf_components["interest_expense"],
                    "fcf": fcf_value,
                }
                vr.inputs["base_source"] = "derived" if fcf_components["ocf"] is not None else "direct"
            elif vr.method == "fcff":
                vr.assumptions["param_source"] = "calibrated" if fcff_p else "default"
                # wacc_note / risk_free 已由 evaluate_fcff 写入，此处不覆盖。
                vr.assumptions["risk_free_source"] = "market_anchor"
        refined_integrity = integrity_mod.refine_integrity(integrity)
        flags = redflags_mod.check_red_flags(financials, refined_integrity)
        fraud = fraud_mod.score_fraud(symbol, financials)
        delist = delisting_mod.check_delisting(symbol, price, market_cap, financials)
        masters = [
            BuffettModel().evaluate(symbol, financials, dividends),
            DuanYongpingModel().evaluate(symbol, financials, dividends),
        ]
        review = review_mod.generate_review(
            symbol, financials, flags, fraud, delist, refined_integrity
        )

        return {
            "symbol": symbol,
            "stock": _load_stock(conn, stock_id),
            "snapshot": snapshot,
            "score": score,
            "return": ret,
            "valuation": valuations_result,
            "moat": moat_mod.assess_moat(symbol, financials),
            "cycle": cycle_mod.assess_cycle(financials),
            "masters": masters,
            "review": review,
            "integrity": refined_integrity,
            "red_flags": flags,
            "fraud": fraud,
            "delisting": delist,
        }
    finally:
        conn.close()


def compute_return_for(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    db_path: Path | None = None,
) -> Any:
    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return returns_mod.compute_return([], [], start or "", end or "")
        kline = _load_kline(conn, stock_id)
        dividends = _load_dividends(conn, stock_id)
        start = start or (kline[0].trade_date if kline else "")
        end = end or (kline[-1].trade_date if kline else "")
        return returns_mod.compute_return(kline, dividends, start, end)
    finally:
        conn.close()


def compare_stocks(symbols: list[str], db_path: Path | None = None) -> list[dict[str, Any]]:
    conn = db.init_db(db_path)
    try:
        results: list[dict[str, Any]] = []
        for symbol in symbols:
            code, market = parse_symbol(symbol)
            stock_id = repository.get_stock_id(conn, code, market)
            if stock_id is None:
                continue
            stock = _load_stock(conn, stock_id)
            financials = _load_financials(conn, stock_id)
            dividends = _load_dividends(conn, stock_id)
            snapshot = _load_snapshot(conn, stock_id)
            if not financials:
                continue
            comparison = comparison_mod.compute_company_comparison(
                symbol, stock.name, stock.industry or "", financials, dividends
            )
            moat = moat_mod.assess_moat(symbol, financials)
            latest = _latest_fy_or_latest(financials)
            results.append(
                {
                    "symbol": symbol,
                    "name": stock.name,
                    "industry": stock.industry,
                    "price": snapshot.price if snapshot else None,
                    "market_cap": snapshot.market_cap if snapshot else None,
                    "revenue_cagr": comparison.revenue_cagr,
                    "gross_margin": latest.gross_margin,
                    "net_margin": latest.net_margin,
                    "roe": latest.roe,
                    "debt_ratio": latest.debt_ratio,
                    "revenue_yoy": latest.revenue_yoy,
                    "net_profit_yoy": latest.net_profit_yoy,
                    "dividend_yield": comparison.dividend_yield,
                    "moat_rating": moat.rating,
                    "cycle": comparison.cycle_position,
                }
            )
        return results
    finally:
        conn.close()


def screen_stocks(
    min_roe: float | None = None,
    min_gross: float | None = None,
    max_debt: float | None = None,
    min_revenue_growth: float | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db(db_path)
    try:
        results: list[dict[str, Any]] = []
        for stock in repository.fetch_all(conn, "SELECT * FROM stocks ORDER BY code"):
            financials = _load_financials(conn, stock["id"])
            if not financials:
                continue
            latest = _latest_fy_or_latest(financials)
            if min_roe is not None and (latest.roe is None or latest.roe < min_roe):
                continue
            if min_gross is not None and (latest.gross_margin is None or latest.gross_margin < min_gross):
                continue
            if max_debt is not None and (latest.debt_ratio is None or latest.debt_ratio > max_debt):
                continue
            if min_revenue_growth is not None and (
                latest.revenue_yoy is None or latest.revenue_yoy < min_revenue_growth
            ):
                continue
            snapshot = _load_snapshot(conn, stock["id"])
            results.append(
                {
                    "symbol": f"{stock['code']}.{stock['market']}",
                    "name": stock["name"],
                    "market_cap": snapshot.market_cap if snapshot else None,
                    "roe": latest.roe,
                    "gross_margin": latest.gross_margin,
                    "net_margin": latest.net_margin,
                    "debt_ratio": latest.debt_ratio,
                    "revenue_yoy": latest.revenue_yoy,
                }
            )
        return results
    finally:
        conn.close()


def _latest_fy_or_latest(financials: list[FinancialReport]) -> FinancialReport | None:
    yearly = [item for item in financials if item.report_type == ReportType.FY]
    if yearly:
        return yearly[-1]
    return financials[-1] if financials else None


def price_compare(
    symbol: str,
    product_code: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    conn = db.init_db(db_path)
    try:
        code, market = parse_symbol(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return {"symbol": symbol, "error": "股票不存在"}
        if product_code:
            code_to_use = product_code
        else:
            row = repository.fetch_one(
                conn, "SELECT product_code FROM stock_products WHERE stock_id = ? LIMIT 1", (stock_id,)
            )
            if row is None:
                return {"symbol": symbol, "error": "未配置商品映射"}
            code_to_use = row["product_code"]
        prices = repository.fetch_all(
            conn, "SELECT trade_date, close FROM product_prices WHERE product_code = ? ORDER BY trade_date ASC",
            (code_to_use,),
        )
        kline = _load_kline(conn, stock_id)
        price_by_date = {row["trade_date"]: row["close"] for row in prices}
        stock_by_date = {bar.trade_date: bar.close for bar in kline}
        dates = sorted(set(price_by_date) & set(stock_by_date))
        price_series = [price_by_date[day] for day in dates]
        stock_series = [stock_by_date[day] for day in dates]
        correlation = price_correlation_mod.compute_correlation(
            code_to_use,
            f"{dates[0] if dates else ''}~{dates[-1] if dates else ''}",
            price_series,
            stock_series,
        )
        return {
            "product_code": code_to_use,
            "price_series": [{"date": day, "value": value} for day, value in zip(dates, price_series)],
            "stock_series": [{"date": day, "value": value} for day, value in zip(dates, stock_series)],
            "correlation": correlation,
        }
    finally:
        conn.close()
