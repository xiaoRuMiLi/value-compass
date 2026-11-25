"""财务全景的纯计算：累计财报还原单季、主要指标、三大表与财务比率。

本模块只消费普通 dict，不读文件/DB、不调 LLM。所有口径以「公式」字段随结果
一起返回，前端可直接展示，避免用户对不上账。
"""

from __future__ import annotations

from typing import Any

# (key, 展示名, 单位, 缩放系数, 是否累计流量)  缩放系数把原始值转成展示单位
# 只有利润/收入/EPS 这类累计流量才做「累计→单季」还原；BPS、比率是时点/当期披露值，直接用。
PRIMARY_FIELDS: list[tuple[str, str, str, float, bool]] = [
    ("net_profit", "归母净利润", "亿元", 1e8, True),
    ("revenue", "营业收入", "亿元", 1e8, True),
    ("eps", "每股收益", "元", 1.0, True),
    ("bps", "每股净资产", "元", 1.0, False),
    ("net_margin", "净利率", "%", 1.0, False),
    ("gross_margin", "毛利率", "%", 1.0, False),
    ("roe", "ROE", "%", 1.0, False),
    ("debt_ratio", "资产负债率", "%", 1.0, False),
]

# 报告类型 -> 单季模式下的标签（资产负债表时点值按报告期末直接落位，不做差分）
_PERIOD_LABEL = {"Q1": "Q1", "H1": "Q2", "Q3": "Q3", "FY": "Q4"}

# 分组 -> (组名, [(key, 展示名, 公式, 单位)])
RATIO_GROUPS: dict[str, tuple[str, list[tuple[str, str, str, str]]]] = {
    "solvency": (
        "偿债能力",
        [
            ("current_ratio", "流动比率", "流动资产 / 流动负债", "倍"),
            ("quick_ratio", "速动比率", "(流动资产 - 存货) / 流动负债", "倍"),
            ("cash_to_liab", "货币资金 / 流动负债", "货币资金 / 流动负债", "倍"),
            ("long_debt_to_wc", "长期债务 / 营运资金", "非流动负债 / (流动资产 - 流动负债)", "倍"),
        ],
    ),
    "operation": (
        "营运能力",
        [
            ("inventory_turnover", "存货周转率", "营业成本 / 平均存货", "次"),
            ("receivable_turnover", "应收账款周转率", "营业收入 / 平均应收账款", "次"),
            ("current_asset_turnover", "流动资产周转率", "营业收入 / 平均流动资产", "次"),
            ("fixed_asset_turnover", "固定资产周转率", "营业收入 / 平均固定资产", "次"),
            ("total_asset_turnover", "总资产周转率", "营业收入 / 平均总资产", "次"),
        ],
    ),
    "quality": (
        "收益质量",
        [
            ("ocf", "经营活动现金流量净额", "现金流量表：经营活动产生的现金流量净额", "亿元"),
            ("fcf", "自由现金流净额", "港股取财报原文 TTM；A 股源数据缺资本开支科目", "亿元"),
        ],
    ),
    "structure": (
        "资本结构",
        [
            ("equity_multiplier", "权益乘数", "总资产 / 归母权益", "倍"),
            ("debt_to_equity", "产权比率", "负债合计 / 股东权益合计", "倍"),
            ("current_asset_to_equity", "流动资产 / 净资产", "流动资产 / 归母权益", "倍"),
            ("noncurrent_asset_to_equity", "非流动资产 / 净资产", "非流动资产 / 归母权益", "倍"),
            ("current_liab_to_equity", "流动负债 / 净资产", "流动负债 / 归母权益", "倍"),
            ("noncurrent_liab_to_equity", "非流动负债 / 净资产", "非流动负债 / 归母权益", "倍"),
            ("equity_to_capital", "归母权益 / 全部投入资本", "归母权益 / (股东权益合计 + 有息债务)", "%"),
            ("debt_to_capital", "带息债务 / 全部投入资本", "(短期借款 + 长期借款) / (股东权益合计 + 有息债务)", "%"),
        ],
    ),
}

_QUARTER_TYPES = ("Q1", "H1", "Q3", "FY")
_EPS_CODES = {"BASIC_EPS", "DILUTED_EPS", "EPS", "EPSJB", "BASIC_EPS_ADJUST"}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _index_values(rows: list[dict[str, Any]], value_key: str) -> dict[tuple[str, str], float]:
    """按 (年份, 报告类型) 建数值索引；同键后写覆盖先写。"""

    indexed: dict[tuple[str, str], float] = {}
    for row in rows:
        date = str(row.get("report_date") or "")[:10]
        report_type = str(row.get("report_type") or "")
        if len(date) < 10 or report_type not in _QUARTER_TYPES:
            continue
        value = _number(row.get(value_key))
        if value is None:
            continue
        indexed[(date[:4], report_type)] = value
    return indexed


def _quarter_values(index: dict[tuple[str, str], float]) -> dict[str, float | None]:
    """把累计口径还原为单季：Q1 直取，Q2=H1-Q1，Q3=Q3累计-H1，Q4=全年-Q3累计。"""

    years = sorted({year for year, _ in index})
    values: dict[str, float | None] = {}
    for year in years:
        q1 = index.get((year, "Q1"))
        h1 = index.get((year, "H1"))
        q3 = index.get((year, "Q3"))
        fy = index.get((year, "FY"))
        values[f"{year}Q1"] = q1
        values[f"{year}Q2"] = None if q1 is None or h1 is None else h1 - q1
        values[f"{year}Q3"] = None if h1 is None or q3 is None else q3 - h1
        values[f"{year}Q4"] = None if q3 is None or fy is None else fy - q3
    return values


def _annual_values(index: dict[tuple[str, str], float]) -> dict[str, float | None]:
    years = sorted({year for year, report_type in index if report_type == "FY"})
    return {f"{year}年报": index.get((year, "FY")) for year in years}


def _point_values(
    index: dict[tuple[str, str], float], annual: bool
) -> dict[str, float | None]:
    """时点/当期披露值：不做差分，年报模式直取 FY，单季模式按报告期末落位。"""

    if annual:
        return _annual_values(index)
    return {
        f"{year}{_PERIOD_LABEL.get(report_type, 'Q4')}": value
        for (year, report_type), value in index.items()
    }


def _avg(current: float | None, previous: float | None) -> float | None:
    if current is None:
        return None
    if previous is None:
        return current
    return (current + previous) / 2


def _ratio_value(
    key: str, current: dict[str, Any], previous: dict[str, Any] | None, annual: bool = False
) -> float | None:
    """用当前期与上一期的报表科目推导比率；分母为None时返回None。"""

    primary = current["primary"]
    item = current["statement"]
    prior = (previous or {}).get("statement") or {}

    def raw(code: str) -> float | None:
        return item.get(code)

    def mean(code: str) -> float | None:
        return _avg(item.get(code), prior.get(code))

    def divide(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        # 单季模式的收入/成本是单季值，年化后才与常见周转率口径可比。
        return numerator / denominator * (1 if annual else 4) if key.endswith("_turnover") else numerator / denominator

    if key == "current_ratio":
        direct = _number(primary.get("current_ratio"))
        # 财报直披露的流动比率优先，缺失才用报表科目推算。
        return direct if direct is not None else divide(raw("TOTAL_CURRENT_ASSETS"), raw("TOTAL_CURRENT_LIAB"))
    if key == "quick_ratio":
        assets, inventory = raw("TOTAL_CURRENT_ASSETS"), raw("INVENTORY")
        if assets is None:
            return None
        return divide(None if inventory is None else assets - inventory, raw("TOTAL_CURRENT_LIAB"))
    if key == "cash_to_liab":
        return divide(raw("MONETARYFUNDS"), raw("TOTAL_CURRENT_LIAB"))
    if key == "long_debt_to_wc":
        assets, liab = raw("TOTAL_CURRENT_ASSETS"), raw("TOTAL_CURRENT_LIAB")
        if assets is None or liab is None:
            return None
        return divide(raw("TOTAL_NONCURRENT_LIAB"), assets - liab)
    if key == "inventory_turnover":
        return divide(raw("OPERATE_COST"), mean("INVENTORY"))
    if key == "receivable_turnover":
        return divide(raw("OPERATE_INCOME"), mean("ACCOUNTS_RECE"))
    if key == "current_asset_turnover":
        return divide(raw("OPERATE_INCOME"), mean("TOTAL_CURRENT_ASSETS"))
    if key == "fixed_asset_turnover":
        return divide(raw("OPERATE_INCOME"), mean("FIXED_ASSET"))
    if key == "total_asset_turnover":
        return divide(raw("OPERATE_INCOME"), mean("TOTAL_ASSETS"))
    if key == "ocf":
        return raw("NETCASH_OPERATE")
    if key == "fcf":
        # 由服务层按标的注入：港股财报原文 TTM，A 股暂无资本开支科目。
        return None
    if key == "equity_multiplier":
        return divide(raw("TOTAL_ASSETS"), raw("TOTAL_PARENT_EQUITY"))
    if key == "debt_to_equity":
        return divide(raw("TOTAL_LIABILITIES"), raw("TOTAL_EQUITY"))
    if key == "current_asset_to_equity":
        return divide(raw("TOTAL_CURRENT_ASSETS"), raw("TOTAL_PARENT_EQUITY"))
    if key == "noncurrent_asset_to_equity":
        return divide(raw("TOTAL_NONCURRENT_ASSETS"), raw("TOTAL_PARENT_EQUITY"))
    if key == "current_liab_to_equity":
        return divide(raw("TOTAL_CURRENT_LIAB"), raw("TOTAL_PARENT_EQUITY"))
    if key == "noncurrent_liab_to_equity":
        return divide(raw("TOTAL_NONCURRENT_LIAB"), raw("TOTAL_PARENT_EQUITY"))
    if key in ("equity_to_capital", "debt_to_capital"):
        debt_raw = None
        short, long_term = raw("SHORT_LOAN"), raw("LONG_LOAN")
        # 科目存在但该期无借款余额，按 0 处理；科目整体缺失才留空。
        if "SHORT_LOAN" in item or "LONG_LOAN" in item:
            debt_raw = (short or 0) + (long_term or 0)
        equity, debt = raw("TOTAL_PARENT_EQUITY"), debt_raw
        if equity is None or debt is None:
            return None
        capital = (raw("TOTAL_EQUITY") or equity) + debt
        if capital == 0:
            return None
        # unit 为 "%"：按百分数返回，与 debt_ratio 等披露口径一致。
        ratio = equity / capital if key == "equity_to_capital" else debt / capital
        return ratio * 100
    return None


def build_dashboard(
    reports: list[dict[str, Any]],
    statement_rows: list[dict[str, Any]],
    frequency: str = "quarter",
    fcf_by_period: dict[str, float] | None = None,
) -> dict[str, Any]:
    """生成财务全景序列；frequency 取 `quarter`（单季）或 `annual`（年报）。"""

    annual = frequency == "annual"
    transform = _annual_values if annual else _quarter_values

    primary_index = {key: _index_values(reports, key) for key, _, _, _, _ in PRIMARY_FIELDS}
    primary_values = {}
    for key, _, _, _, is_flow in PRIMARY_FIELDS:
        index = primary_index[key]
        primary_values[key] = transform(index) if is_flow else _point_values(index, annual)
    labels = []
    for values in primary_values.values():
        for label in values:
            if label not in labels:
                labels.append(label)
    labels.sort(key=lambda x: (int(x[:4]), 0 if x.endswith("年报") else int(x[-1])))
    # 末期的单季/年报尚未披露时整行为空，不留给前端画空柱。
    labels = [
        label
        for label in labels
        if any(values.get(label) is not None for values in primary_values.values())
    ]

    primary = [
        {
            "key": key,
            "name": name,
            "unit": unit,
            "scale": scale,
            "values": [_number(primary_values[key].get(label)) for label in labels],
        }
        for key, name, unit, scale, _ in PRIMARY_FIELDS
    ]

    by_code: dict[str, dict[str, Any]] = {}
    for row in statement_rows:
        code = str(row.get("item_code") or "")
        if not code:
            continue
        entry = by_code.setdefault(
            code,
            {
                "name": row.get("item_name") or code,
                "statement_type": row.get("statement_type") or "income",
                "rows": [],
            },
        )
        entry["rows"].append(row)

    statements: dict[str, list[dict[str, Any]]] = {"income": [], "balance": [], "cashflow": []}
    for code, entry in by_code.items():
        indexed = _index_values(entry["rows"], "item_value")
        # 资产负债表是时点余额，利润表/现金流量表才是累计流量需还原单季。
        values = (
            _point_values(indexed, annual)
            if entry["statement_type"] == "balance"
            else transform(indexed)
        )
        amount = code not in _EPS_CODES
        statements.setdefault(entry["statement_type"], []).append(
            {
                "key": code,
                "name": entry["name"],
                "unit": "亿元" if amount else "元",
                "scale": 1e8 if amount else 1.0,
                "values": [_number(values.get(label)) for label in labels],
            }
        )
    for items in statements.values():
        items.sort(key=lambda x: x["name"])

    periods: list[dict[str, Any]] = []
    for index, label in enumerate(labels):
        primary_snapshot = {item["key"]: item["values"][index] for item in primary}
        statement_snapshot = {}
        for items in statements.values():
            for item in items:
                statement_snapshot[item["key"]] = item["values"][index]
        periods.append({"label": label, "primary": primary_snapshot, "statement": statement_snapshot})

    ratios: dict[str, list[dict[str, Any]]] = {}
    for group_key, (group_name, specs) in RATIO_GROUPS.items():
        results = []
        for key, name, formula, unit in specs:
            values = [
                _ratio_value(key, periods[i], periods[i - 1] if i else None, annual)
                for i in range(len(labels))
            ]
            if key == "fcf" and fcf_by_period:
                values = [fcf_by_period.get(label) for label in labels]
            results.append(
                {
                    "key": key,
                    "name": name,
                    "formula": formula,
                    "unit": unit,
                    "scale": 1e8 if key == "ocf" else 1.0,
                    "values": values,
                }
            )
        ratios[group_key] = {
            "name": group_name,
            "items": results,
        }

    return {
        "frequency": "annual" if annual else "quarter",
        "labels": labels,
        "primary": primary,
        "statements": statements,
        "ratios": ratios,
        "notes": [
            "单季模式：Q2=半年报-Q1，Q3=三季报-半年报，Q4=年报-三季报；比率类指标按披露值直接展示。",
            "周转率分母取期初期末平均；单季周转率按 ×4 简单年化，年报模式为全年口径。",
            "带息债务按短期借款+长期借款近似；营运资金=流动资产-流动负债。",
        ],
    }
