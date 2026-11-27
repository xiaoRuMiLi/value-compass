"""资产负债表拆解的纯计算：科目分类、金额/占比/同比、投资资产归类。

本模块只消费普通 dict，不读文件/DB、不调 LLM。所有金额、占比、同比均由代码计算，
LLM 只在上游写描述文字，绝不碰数字。

设计要点：
1. 同一科目在库里可能同时存在东财数字码与项目语义码两条（如"联营公司权益"
   004001013 与 LONG_EQUITY_INVEST），因此用「展示项 + 别名」去重：每个报告期
   从别名中取第一个非空值，避免重复加总。
2. 汇总项（总资产/总负债等）单独取，不作为明细项展示。
3. 投资资产按计量方式归类（权益法/公允价值/短期投资/存款理财/其他），供专章聚合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ItemRule:
    """一个展示项：display 为展示名，names 为等价的科目名别名（同含义不同编码）。"""

    display: str
    names: tuple[str, ...]
    section: str
    subgroup: str
    invest_class: str | None = None


# 汇总项：只用于总览与占比分母，不进明细列表
TOTAL_RULES: tuple[ItemRule, ...] = (
    ItemRule("总资产", ("总资产", "TOTAL_ASSETS"), "totals", "资产"),
    ItemRule("流动资产合计", ("流动资产合计", "TOTAL_CURRENT_ASSETS"), "totals", "资产"),
    ItemRule("非流动资产合计", ("非流动资产合计", "TOTAL_NONCURRENT_ASSETS"), "totals", "资产"),
    ItemRule("总负债", ("总负债", "TOTAL_LIABILITIES"), "totals", "负债"),
    ItemRule("流动负债合计", ("流动负债合计", "TOTAL_CURRENT_LIAB"), "totals", "负债"),
    ItemRule("非流动负债合计", ("非流动负债合计", "TOTAL_NONCURRENT_LIAB"), "totals", "负债"),
    ItemRule("股东权益", ("股东权益", "TOTAL_PARENT_EQUITY"), "totals", "权益"),
    ItemRule("少数股东权益", ("少数股东权益", "MINORITY_EQUITY"), "totals", "权益"),
    ItemRule("总权益", ("总权益", "净资产", "TOTAL_EQUITY"), "totals", "权益"),
)

# 明细项：section 取 assets_current / assets_noncurrent / liabilities_current /
# liabilities_noncurrent / equity；invest_class 仅投资资产相关项填写。
ITEM_RULES: tuple[ItemRule, ...] = (
    # 流动资产
    ItemRule("现金及等价物", ("现金及等价物", "MONETARYFUNDS"), "assets_current", "现金及存款"),
    ItemRule("受限制存款及现金", ("受限制存款及现金", "RESTRICTED_CASH"), "assets_current", "现金及存款"),
    ItemRule("短期存款", ("短期存款", "SHORT_TERM_DEPOSIT"), "assets_current", "现金及存款", "deposit"),
    ItemRule("交易性金融资产（流动）", ("交易性金融资产(流动)",), "assets_current", "短期投资", "short_invest"),
    ItemRule("指定以公允价值记账之金融资产（流动）",
             ("指定以公允价值记账之金融资产(流动)", "TRADE_FINASSET"),
             "assets_current", "短期投资", "fair_value"),
    ItemRule("其他金融资产（流动）",
             ("其他金融资产(流动)", "OTHER_CURRENT_FINASSET"),
             "assets_current", "短期投资", "fair_value"),
    ItemRule("持有至到期投资（流动）", ("持有至到期投资(流动)",), "assets_current", "短期投资", "fair_value"),
    ItemRule("衍生金融工具（流动）", ("衍生金融工具-资产(流动)",), "assets_current", "短期投资", "fair_value"),
    ItemRule("持作出售的资产（流动）", ("持作出售的资产(流动)",), "assets_current", "短期投资", None),
    ItemRule("应收账款", ("应收帐款", "应收账款", "ACCOUNTS_RECE"), "assets_current", "应收款项"),
    ItemRule("预付款按金及其他应收款", ("预付款按金及其他应收款",), "assets_current", "应收款项"),
    ItemRule("应收关联方款项", ("应收关联方款项",), "assets_current", "应收款项"),
    ItemRule("存货", ("存货", "INVENTORY"), "assets_current", "存货"),
    # 非流动资产
    ItemRule("联营公司权益", ("联营公司权益", "LONG_EQUITY_INVEST"),
             "assets_noncurrent", "权益法投资", "equity_method"),
    ItemRule("合营公司权益", ("合营公司权益", "JOINT_VENTURE_INVEST"),
             "assets_noncurrent", "权益法投资", "equity_method"),
    ItemRule("于联营公司可赎回工具的投资", ("于联营公司可赎回工具的投资",),
             "assets_noncurrent", "权益法投资", "equity_method"),
    ItemRule("指定以公允价值记账之金融资产（非流动）",
             ("指定以公允价值记账之金融资产", "NONCURRENT_FINASSET"),
             "assets_noncurrent", "公允价值投资", "fair_value"),
    ItemRule("可供出售投资", ("可供出售投资",), "assets_noncurrent", "公允价值投资", "fair_value"),
    ItemRule("持有至到期投资", ("持有至到期投资",), "assets_noncurrent", "公允价值投资", "fair_value"),
    ItemRule("其他金融资产（非流动）",
             ("其他金融资产(非流动)", "OTHER_NONCURRENT_FINASSET"),
             "assets_noncurrent", "公允价值投资", "fair_value"),
    ItemRule("中长期存款", ("中长期存款", "TERM_DEPOSIT"),
             "assets_noncurrent", "存款理财", "deposit"),
    ItemRule("投资物业", ("投资物业", "INVEST_REALESTATE"),
             "assets_noncurrent", "固定资产及投资物业", "other_invest"),
    ItemRule("物业厂房及设备", ("物业厂房及设备", "固定资产", "FIXED_ASSET"),
             "assets_noncurrent", "固定资产及投资物业"),
    ItemRule("在建工程", ("在建工程", "CIP"), "assets_noncurrent", "固定资产及投资物业"),
    ItemRule("土地使用权", ("土地使用权",), "assets_noncurrent", "固定资产及投资物业"),
    ItemRule("无形资产", ("无形资产", "INTANGIBLE_ASSET"), "assets_noncurrent", "无形资产及其他"),
    ItemRule("递延税项资产", ("递延税项资产",), "assets_noncurrent", "无形资产及其他"),
    ItemRule("预付款项", ("预付款项",), "assets_noncurrent", "无形资产及其他"),
    ItemRule("其他非流动资产", ("其他非流动资产", "非流动资产其他项目"),
             "assets_noncurrent", "无形资产及其他"),
    # 流动负债
    ItemRule("应付账款", ("应付帐款", "应付账款", "ACCOUNTS_PAYABLE"), "liabilities_current", "经营负债"),
    ItemRule("应付票据", ("应付票据", "SHORT_BOND_PAYABLE"), "liabilities_current", "有息负债", "interest_bearing"),
    ItemRule("短期贷款", ("短期贷款", "SHORT_LOAN"), "liabilities_current", "有息负债", "interest_bearing"),
    ItemRule("融资租赁负债（流动）", ("融资租赁负债(流动)", "SHORT_LEASE_LIAB"),
             "liabilities_current", "有息负债", "interest_bearing"),
    ItemRule("应付税项", ("应付税项",), "liabilities_current", "经营负债"),
    ItemRule("应付股利", ("应付股利",), "liabilities_current", "经营负债"),
    ItemRule("递延收入（流动）", ("递延收入(流动)",), "liabilities_current", "经营负债"),
    ItemRule("其他应付款及应计费用", ("其他应付款及应计费用",), "liabilities_current", "经营负债"),
    ItemRule("其他金融负债（流动）", ("其他金融负债(流动)",), "liabilities_current", "经营负债"),
    # 非流动负债
    ItemRule("长期贷款", ("长期贷款", "LONG_LOAN"), "liabilities_noncurrent", "有息负债", "interest_bearing"),
    ItemRule("应付票据（非流动）", ("应付票据(非流动)", "BOND_PAYABLE"),
             "liabilities_noncurrent", "有息负债", "interest_bearing"),
    ItemRule("融资租赁负债（非流动）", ("融资租赁负债(非流动)", "LEASE_LIAB"),
             "liabilities_noncurrent", "有息负债", "interest_bearing"),
    ItemRule("长期应付款", ("长期应付款",), "liabilities_noncurrent", "经营负债"),
    ItemRule("递延收入（非流动）", ("递延收入(非流动)",), "liabilities_noncurrent", "经营负债"),
    ItemRule("递延税项负债", ("递延税项负债",), "liabilities_noncurrent", "经营负债"),
    ItemRule("其他金融负债（非流动）", ("其他金融负债(非流动)",), "liabilities_noncurrent", "经营负债"),
    # 权益
    ItemRule("股本", ("股本", "SHARE_CAPITAL"), "equity", "投入资本"),
    ItemRule("股本溢价", ("股本溢价",), "equity", "投入资本"),
    ItemRule("库存股", ("库存股",), "equity", "投入资本"),
    ItemRule("储备", ("储备", "其他储备"), "equity", "储备"),
    ItemRule("保留溢利", ("保留溢利(累计亏损)", "保留溢利"), "equity", "储备"),
)

SECTION_NAMES = {
    "assets_current": "流动资产",
    "assets_noncurrent": "非流动资产",
    "liabilities_current": "流动负债",
    "liabilities_noncurrent": "非流动负债",
    "equity": "股东权益",
}

INVEST_CLASS_NAMES = {
    "equity_method": "权益法投资（联营/合营）",
    "fair_value": "公允价值计量投资",
    "short_invest": "短期投资",
    "deposit": "存款及理财",
    "other_invest": "投资物业及其他",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # 排除 NaN


def _value_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """按科目名 -> {报告期: 数值} 建索引；同报告期后写覆盖先写。"""
    index: dict[str, dict[str, float]] = {}
    for row in rows:
        name = str(row.get("item_name") or "")
        date = str(row.get("report_date") or "")[:10]
        value = _number(row.get("item_value"))
        if not name or len(date) < 10 or value is None:
            continue
        index.setdefault(name, {})[date] = value
    return index


def _rule_series(rule: ItemRule, index: dict[str, dict[str, float]], labels: list[str]) -> list[float | None]:
    """一个展示项在每个报告期的取值：按别名顺序取第一个有值者，天然去重。"""
    series: list[float | None] = []
    for label in labels:
        value = None
        for name in rule.names:
            candidate = index.get(name, {}).get(label)
            if candidate is not None:
                value = candidate
                break
        series.append(value)
    return series


def _yoy(series: list[float | None], labels: list[str]) -> float | None:
    """最新值相对上年同期（报告期年份-1、同月日）的变化率（%）。"""
    if not series or series[-1] is None:
        return None
    latest_label = labels[-1]
    year = int(latest_label[:4])
    prior_label = f"{year - 1}{latest_label[4:]}"
    if prior_label not in labels:
        return None
    prior = series[labels.index(prior_label)]
    if prior in (None, 0):
        return None
    return round((series[-1] - prior) / abs(prior) * 100, 2)


def yearly_labels_from(labels: list[str]) -> list[str]:
    """从 ISO 报告期标签推导年度标签（升序，如 2024年/2025年/2026年）。"""
    years = {str(label)[:4] for label in labels if str(label)[:4].isdigit()}
    return [f"{year}年" for year in sorted(years)]


def aggregate_item_yearly(
    labels: list[str], series: list[float | None], year_labels: list[str]
) -> list[float | None]:
    """账面时点值按自然年聚合：取该年最后一个有值报告期，不累加。

    资产负债表科目是时点值（Q1/Q2/Q3/年报各自是当日余额），累加没有意义；
    年度视图取当年最后一个已披露报告期的值。labels 升序，同年后写覆盖即为年末值。
    纯计算，不读库、不调 LLM。
    """
    by_year: dict[str, float] = {}
    for label, value in zip(labels, series):
        year = str(label)[:4]
        if value is not None and year.isdigit():
            by_year[year] = value
    return [by_year.get(str(year_label)[:4]) for year_label in year_labels]


def build_balance_breakdown(
    statement_rows: list[dict[str, Any]],
    *,
    currency: str = "HKD",
    fcf_history: dict[str, Any] | None = None,
    associate_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把三大表原始科目行拆解为可展示结构；纯计算，不读库、不调 LLM。"""
    balance_rows = [
        row for row in statement_rows if str(row.get("statement_type")) == "balance"
    ]
    index = _value_index(balance_rows)
    # 报告期取并集，升序；只保留账面时点值实际存在的期
    dates: set[str] = set()
    for by_date in index.values():
        dates.update(by_date)
    labels = sorted(dates)

    year_labels = yearly_labels_from(labels)
    totals: dict[str, list[float | None]] = {}
    for rule in TOTAL_RULES:
        totals[rule.display] = _rule_series(rule, index, labels)

    total_assets = totals.get("总资产", [None] * len(labels))
    latest_assets = next((v for v in reversed(total_assets) if v is not None), None)

    def item_payload(rule: ItemRule) -> dict[str, Any]:
        series = _rule_series(rule, index, labels)
        latest = next((v for v in reversed(series) if v is not None), None)
        share = (
            round(latest / latest_assets * 100, 2)
            if latest is not None and latest_assets
            else None
        )
        return {
            "name": rule.display,
            "subgroup": rule.subgroup,
            "invest_class": rule.invest_class,
            "values": series,
            "yearly_values": aggregate_item_yearly(labels, series, year_labels),
            "latest": latest,
            "yoy": _yoy(series, labels),
            "share_of_assets": share,
        }

    sections: dict[str, Any] = {}
    for section_key, section_name in SECTION_NAMES.items():
        items = [item_payload(rule) for rule in ITEM_RULES if rule.section == section_key]
        # 有数据的项排前面，全空的项保留但标出来（前端提示"财报未单列"）
        items.sort(key=lambda it: (it["latest"] is None, -(it["latest"] or 0)))
        section_sum = [
            sum(v for v in (item["values"][i] for item in items) if v is not None) or None
            for i in range(len(labels))
        ]
        sections[section_key] = {
            "name": section_name,
            "total": section_sum,
            "items": items,
        }

    # 投资资产按计量方式聚合（各展示项 values 相加，缺失按 0 计但保留期数）
    invest_classes: list[dict[str, Any]] = []
    for class_key, class_name in INVEST_CLASS_NAMES.items():
        members = [rule for rule in ITEM_RULES if rule.invest_class == class_key]
        if not members:
            continue
        series = [
            sum(
                v for v in (_rule_series(rule, index, labels)[i] for rule in members)
                if v is not None
            )
            for i in range(len(labels))
        ]
        latest = next((v for v in reversed(series) if v is not None), None)
        invest_classes.append(
            {
                "key": class_key,
                "name": class_name,
                "members": [rule.display for rule in members],
                "values": series,
                "latest": latest,
                "share_of_assets": (
                    round(latest / latest_assets * 100, 2)
                    if latest is not None and latest_assets else None
                ),
                "yoy": _yoy(series, labels),
            }
        )

    notes: list[str] = []
    if not balance_rows:
        notes.append("当前标的资产负债表科目尚未入库。")
    else:
        notes.append(
            "同一科目在库内可能同时存在数字码与语义码两条，已按展示项去重，避免重复加总。"
        )
        notes.append("占比分母为总资产；同比为最新期相对上年同期。")

    return {
        "labels": labels,
        "year_labels": year_labels,
        "currency": currency,
        "totals": [
            {
                "name": rule.display,
                "values": totals[rule.display],
                "latest": next((v for v in reversed(totals[rule.display]) if v is not None), None),
                "yoy": _yoy(totals[rule.display], labels),
            }
            for rule in TOTAL_RULES
        ],
        "sections": sections,
        "investment_classes": invest_classes,
        "associate_details": associate_details or [],
        "fcf_history": fcf_history or {"labels": [], "values": [], "ocf": [], "source": ""},
        "notes": notes,
    }


def cumulative_to_single_quarter(
    labels: list[str],
    cumulative: list[float | None],
) -> list[float | None]:
    """把「年内累计」序列转为单季值（纯计算，不改变入参）。

    财务主表的 OCF 是年初至今累计口径，而 FCF 是单季披露值，直接并列会误导。
    差分规则（按自然年）：
      Q1 单季 = Q1 累计
      Q2 单季 = H1 累计 − Q1 累计
      Q3 单季 = 前三季累计 − H1 累计
      Q4 单季 = 全年累计 − 前三季累计
    任一依赖期缺失则返回 None（不硬凑、不用 0 冒充）。非标准报告期原样透传累计值。
    """
    values = {str(label)[:10]: value for label, value in zip(labels, cumulative)}
    prior_key = {"03-31": None, "06-30": "03-31", "09-30": "06-30", "12-31": "09-30"}
    out: list[float | None] = []
    for label in labels:
        date = str(label)[:10]
        month_day = date[5:]
        current = values.get(date)
        base_key = prior_key.get(month_day)
        if base_key is None or current is None:
            out.append(current)
            continue
        base_date = date[:4] + "-" + base_key
        base = values.get(base_date)
        out.append(round(current - base, 2) if base is not None else None)
    return out


def aggregate_fcf_yearly(
    labels: list[str],
    values: list[float | None],
    ocf: list[float | None],
) -> dict[str, Any]:
    """把单季 FCF / 年内累计 OCF 序列按自然年聚合为「全年口径」的纯计算。

    口径对齐很关键：财报原文抽取的 FCF 是**单季值**，财务主表的 OCF 是**年内累计值**
    （Q1/H1/前三季/全年累加）。所以年度模式下：
      - FCF：该年所有已披露单季值相加（缺季时只累加有值的季度，并用 fcf_quarters 标注）
      - OCF：取该年最后一个有值报告期（通常是年报）的累计值，即全年 OCF

    本函数只消费普通 list，不读库、不调 LLM。
    """
    years: dict[str, dict[str, list[float]]] = {}
    for i, label in enumerate(labels):
        year = str(label)[:4]
        if not year.isdigit():
            continue
        bucket = years.setdefault(year, {"fcf": [], "ocf": []})
        value = values[i] if i < len(values) else None
        ocf_value = ocf[i] if i < len(ocf) else None
        if value is not None:
            bucket["fcf"].append(float(value))
        if ocf_value is not None:
            bucket["ocf"].append(float(ocf_value))

    year_labels: list[str] = []
    fcf_values: list[float | None] = []
    ocf_values: list[float | None] = []
    fcf_quarters: list[int] = []
    for year in sorted(years):
        bucket = years[year]
        year_labels.append(f"{year}年")
        fcf_values.append(round(sum(bucket["fcf"]), 2) if bucket["fcf"] else None)
        ocf_values.append(round(bucket["ocf"][-1], 2) if bucket["ocf"] else None)
        fcf_quarters.append(len(bucket["fcf"]))

    return {
        "labels": year_labels,
        "values": fcf_values,
        "ocf": ocf_values,
        "fcf_quarters": fcf_quarters,
        "source": "FCF 为该年已披露单季值之和；OCF 为年内累计口径取年末值（亿元）",
    }
