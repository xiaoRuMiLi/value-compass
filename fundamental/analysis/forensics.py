"""财报取证扫描：从多期财报与外部证据中「算出」风险点与异常波动。

设计约束（可靠可信优先）：

1. 本模块**只做确定性计算**，不做任何推测、不下「造假」结论。所有输出都带
   精确数值、报告期、触发阈值与证据引用，供 LLM 逐条解释。
2. Q1/H1/Q3 为累计口径，不能与 FY 直接比较。跨年同比一律按「同报告期」对齐，
   环比用累计差分，避免制造假异常。
3. 阈值判定以**公司自身历史序列**为主（中位数 + MAD 离群），不套跨行业硬阈值，
   避免把银行 90% 负债率这类行业属性误判成风险。
4. 数据缺失的检查不静默跳过，统一写入 ``coverage.skipped``，防止
   「没查出问题」被误读成「没问题」。
5. **所有触发阈值都来自 ``config/forensics.toml``**，经 ``ForensicsConfig`` 注入，
   本模块不读配置文件、不留硬编码判定值；测试不传配置时走内置默认值。
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Iterable, Sequence

from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import (
    Announcement,
    AuditOpinion,
    CorporateAction,
    DividendRecord,
    EarningsForecast,
    FinancialReport,
    InsiderTrade,
    IntegrityRecord,
    KlineBar,
    Pledge,
    ReportType,
    StatementItem,
    StatementType,
)

# 报告类型顺序用于累计差分（同一自然年内）
_PERIOD_ORDER = {ReportType.Q1: 1, ReportType.H1: 2, ReportType.Q3: 3, ReportType.FY: 4}
_PERIOD_LABELS = {"Q1": "一季报", "H1": "中报", "Q3": "三季报", "FY": "年报"}

# 线索级关键词与全部触发阈值的默认值都集中在 forensics_config.ForensicsConfig，
# 运行时由 config/forensics.toml 注入，本模块不保留第二份硬编码。

# 需要 statement_items 的费用科目
_EXPENSE_CODES = {
    "SALE_EXPENSE": "销售费用",
    "MANAGE_EXPENSE": "管理费用",
    "RESEARCH_EXPENSE": "研发费用",
    "FINANCE_EXPENSE": "财务费用",
    "OPERATE_COST": "营业成本",
}
_REVENUE_CODES = ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME")


# ---------- 基础工具 ----------


def _yi(value: float | None) -> str:
    return "数据缺失" if value is None else f"{value / 1e8:,.2f} 亿元"


def _pct(value: float | None) -> str:
    return "数据缺失" if value is None else f"{value:.2f}%"


def _pp(value: float | None) -> str:
    return "数据缺失" if value is None else f"{value:+.2f} 个百分点"


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _yoy(cur: float | None, prev: float | None) -> float | None:
    """同比增速（%）。基数为负或零时无意义，返回 None。"""

    if cur is None or prev is None or prev <= 0:
        return None
    return (cur - prev) / prev * 100


def _below_operating(operating: float | None, pretax: float | None) -> float | None:
    """营业利润以下项目合计（投资/公允价值/联营/利息/汇兑/其他）。"""

    if operating is None or pretax is None:
        return None
    return pretax - operating


# 营业利润以下 / 税项的逐科目映射：港股用 STD_ITEM_CODE，A 股用科目名兜底。
_BELOW_ITEM_ALIASES = {
    "other": (
        ("004011997",),
        ("溢利其他项目", "投资收益", "公允价值变动收益", "资产减值损失", "信用减值损失"),
    ),
    "associates": (
        ("004011202",),
        ("应占联营公司溢利", "对联营企业和合营企业的投资收益", "联营企业投资收益"),
    ),
    "interest_income": (("004011200",), ("利息收入",)),
    "finance_cost": (("004011201",), ("融资成本", "财务费用")),
    "tax": (("004012001",), ("税项", "所得税费用")),
}
_BELOW_ITEM_LABELS = {
    "other": "溢利其他项目（投资/处置/公允价值/减值等）",
    "associates": "应占联营公司溢利",
    "interest_income": "利息收入",
    "finance_cost": "融资成本",
    "tax": "税项",
}


def _statement_amount_by_alias(
    items: list[Any] | None,
    report_date: str | None,
    codes: tuple[str, ...],
    names: tuple[str, ...],
) -> float | None:
    if not report_date:
        return None
    for item in items or []:
        if str(item.report_date) != str(report_date):
            continue
        if str(getattr(item, "item_code", "")) in codes or getattr(item, "item_name", "") in names:
            return item.item_value
    return None


def _below_operating_breakdown(
    items: list[Any] | None, report_date: str, prev_date: str | None = None
) -> dict[str, dict[str, float | None]]:
    """把营业利润以下合计拆成具体科目，避免解释层只看到黑箱。"""

    result: dict[str, dict[str, float | None]] = {}
    for key, (codes, names) in _BELOW_ITEM_ALIASES.items():
        cur = _statement_amount_by_alias(items, report_date, codes, names)
        prev = _statement_amount_by_alias(items, prev_date, codes, names) if prev_date else None
        if cur is None and prev is None:
            continue
        result[key] = {
            "current": cur,
            "previous": prev,
            "change": cur - prev if cur is not None and prev is not None else None,
        }
    return result


def _profit_structure_facts(item: dict[str, Any]) -> list[dict[str, str]]:
    """把利润结构作为确定性事实注入信号，供解释层定位因果发生的位置。"""

    op = item.get("operating_profit")
    pretax = item.get("pretax_profit")
    if op is None or pretax is None:
        return []
    below = _below_operating(op, pretax)
    facts = [
        {"label": "本期营业利润", "value": _yi(op)},
        {"label": "本期税前利润", "value": _yi(pretax)},
        {"label": "营业利润以下合计", "value": _yi(below)},
    ]
    prev_op = item.get("prev_operating_profit")
    prev_pretax = item.get("prev_pretax_profit")
    if prev_op is not None:
        facts.append({"label": "上年同期营业利润", "value": _yi(prev_op)})
    if prev_pretax is not None:
        facts.append({"label": "上年同期税前利润", "value": _yi(prev_pretax)})
    prev_below = _below_operating(prev_op, prev_pretax)
    if prev_below is not None:
        facts.append({"label": "上年同期营业利润以下合计", "value": _yi(prev_below)})
        facts.append({
            "label": "营业利润以下同比变化",
            "value": _yi(below - prev_below) if below is not None else "数据缺失",
        })
    for key, data in (item.get("below_breakdown") or {}).items():
        cur, prev, change = data.get("current"), data.get("previous"), data.get("change")
        if cur is None and prev is None:
            continue
        facts.append({
            "label": _BELOW_ITEM_LABELS.get(key, key),
            "value": f"{_yi(prev)} → {_yi(cur)}（变化 {_yi(change)}）",
        })
    return facts


def _profit_structure_series(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """结论窗口内的利润结构快照，只保留代码算出的事实。"""

    result: list[dict[str, Any]] = []
    for item in items:
        op = item.get("operating_profit")
        pretax = item.get("pretax_profit")
        if op is None or pretax is None:
            continue
        below = _below_operating(op, pretax)
        prev_below = _below_operating(
            item.get("prev_operating_profit"), item.get("prev_pretax_profit")
        )
        result.append({
            "period": item["period"],
            "report_date": item["report_date"],
            "report_type": item["report_type"],
            "operating_profit": op,
            "pretax_profit": pretax,
            "below_operating": below,
            "below_operating_prev": prev_below,
            "below_operating_change": (
                below - prev_below if below is not None and prev_below is not None else None
            ),
            "below_breakdown": item.get("below_breakdown") or {},
            "net_profit": item.get("net_profit"),
        })
    return result


def _statement_amount(
    items: list[Any] | None, report_date: str, names: tuple[str, ...]
) -> float | None:
    """从三大报表明细回填利润结构（A 股字段尚未映射到 financials 时）。"""

    for item in items or []:
        if str(item.report_date) == str(report_date) and item.item_name in names:
            return item.item_value
    return None


def _period_key(row: FinancialReport) -> tuple[int, int]:
    """返回 (年份, 期序)，用于同报告期对齐。"""

    year = int(str(row.report_date)[:4])
    return year, _PERIOD_ORDER.get(row.report_type, 0)


def _period_name(row: FinancialReport) -> str:
    label = _PERIOD_LABELS.get(
        row.report_type.value if hasattr(row.report_type, "value") else str(row.report_type), ""
    )
    return f"{row.report_date} {label}"


def _mad_outlier(
    values: Sequence[float],
    current: float,
    *,
    multiplier: float = 3.0,
    min_samples: int = 4,
) -> tuple[bool, float, float]:
    """基于自身历史的中位数绝对偏差离群判定。

    返回 (是否离群, 中位数, MAD)。样本不足或历史几乎无波动时返回 False，
    避免把「本来就没变过的指标」的一次微小变动放大成异常。
    ``multiplier`` / ``min_samples`` 由配置注入。
    """

    if len(values) < min_samples:
        return False, (statistics.median(values) if values else 0.0), 0.0
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    if mad < 1e-6:
        # 历史完全平坦时任何非零偏离都是真异常，不能一律吞掉；
        # 但完全相等（current == median）仍应判为正常。
        return abs(current - median) > 1e-6, median, mad
    return abs(current - median) > multiplier * mad, median, mad


# ---------- 输出构造器 ----------


def _signal(
    sig_id: str,
    category: str,
    severity: str,
    title: str,
    period: str,
    facts: list[dict[str, str]],
    trigger: str,
    description: str,
    refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": sig_id,
        "category": category,
        "severity": severity,
        "title": title,
        "period": period,
        "facts": facts,
        "trigger": trigger,
        "description": description,
        "evidence_refs": refs or [],
    }


def _anomaly(
    ano_id: str,
    metric: str,
    period: str,
    current: float | None,
    previous: float | None,
    change: float | None,
    unit: str,
    threshold: str,
    direction: str,
    note: str,
) -> dict[str, Any]:
    return {
        "id": ano_id,
        "metric": metric,
        "period": period,
        "current": current,
        "previous": previous,
        "change": change,
        "unit": unit,
        "threshold": threshold,
        "direction": direction,
        "note": note,
    }


# ---------- 业绩拐点（跨期模式识别） ----------


def _detect_growth_inflection(
    series: list[dict[str, Any]],
    window_start: int,
    cfg: ForensicsConfig,
) -> dict[str, Any] | None:
    """识别「由多年高增长平台转入持续负增长」的业绩拐点。

    这是一个**跨期模式**，不是单点规则：
      1. 拐点期本身净利同比 < 0；
      2. 拐点期之前连续 ``inflection_platform_periods`` 期净利同比均 >
         ``inflection_platform_floor_pct``，且这段平台均值 > ``profit_turn_high_pct``
         ——确认此前确实处于"多年高增长"而非低基数波动；
      3. 从拐点期到最新，净利同比为负的期数 >= ``inflection_min_negative_periods``
         ——确认转负是趋势而非孤立一期。

    只在结论窗口（window_start..末尾）内寻找拐点期，取最近一个满足条件的。
    返回分析字典（不含 id），由调用方构造 signal / anomaly。
    """
    n = len(series)
    need = cfg.inflection_platform_periods
    floor = cfg.inflection_platform_floor_pct
    latest: dict[str, Any] | None = None

    for i in range(max(window_start, need), n):
        cur = series[i]["net_profit_yoy"]
        if cur is None or cur >= 0:
            continue
        platform = series[i - need:i]
        yoys = [p["net_profit_yoy"] for p in platform]
        if any(y is None or y <= floor for y in yoys):
            continue
        avg = sum(yoys) / len(yoys)  # type: ignore[arg-type]
        if avg <= cfg.profit_turn_high_pct:
            continue
        # 转负后的持续性：从拐点期到最新，净利同比为负的期数
        trailing = series[i:]
        neg_count = sum(
            1 for t in trailing
            if t["net_profit_yoy"] is not None and t["net_profit_yoy"] < 0
        )
        if neg_count < cfg.inflection_min_negative_periods:
            continue
        latest = {
            "turn": series[i],
            "turn_index": i,
            "platform": platform,
            "platform_yoys": yoys,
            "platform_avg": avg,
            "neg_count": neg_count,
            "trailing": trailing,
        }
    return latest


# ---------- 财报序列扫描 ----------


def _scan_financial_series(
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
    lookback: int = 12,
    statement_items: list[Any] | None = None,
) -> int:
    """扫描财报序列。

    全历史用于计算 MAD 离群基线，但**只对最近 ``lookback`` 个报告期产出信号**，
    避免 20 多年前的历史噪音淹没当前结论。返回被略过的历史信号数（供背景说明）。
    所有触发阈值来自 ``cfg``（config/forensics.toml）。
    """

    cfg = cfg or ForensicsConfig()
    ordered = sorted(financials, key=_period_key)
    if len(ordered) < 2:
        coverage["skipped"].append({"check": "财务序列异常", "reason": "财报期数不足 2 期"})
        return 0, []

    # 同报告期（去年同类型）索引，保证累计口径可比
    by_key = {_period_key(row): row for row in ordered}

    series: list[dict[str, Any]] = []
    for row in ordered:
        year, seq = _period_key(row)
        prev = by_key.get((year - 1, seq))
        rt = row.report_type.value if hasattr(row.report_type, "value") else str(row.report_type)
        op = getattr(row, "operating_profit", None)
        pretax = getattr(row, "pretax_profit", None)
        prev_op = getattr(prev, "operating_profit", None) if prev else None
        prev_pretax = getattr(prev, "pretax_profit", None) if prev else None
        date = str(row.report_date)
        prev_date = str(prev.report_date) if prev else None
        if op is None:
            op = _statement_amount(statement_items, date, ("营业利润",))
        if pretax is None:
            pretax = _statement_amount(statement_items, date, ("利润总额", "税前利润"))
        if prev is not None:
            if prev_op is None:
                prev_op = _statement_amount(statement_items, prev_date, ("营业利润",))
            if prev_pretax is None:
                prev_pretax = _statement_amount(statement_items, prev_date, ("利润总额", "税前利润"))
        breakdown = _below_operating_breakdown(statement_items, date, prev_date)
        series.append(
            {
                "row": row,
                "period": _period_name(row),
                "report_date": str(row.report_date),
                "report_type": rt,
                "revenue": row.revenue,
                "net_profit": row.net_profit,
                "prev_revenue": prev.revenue if prev else None,
                "prev_net_profit": prev.net_profit if prev else None,
                "revenue_yoy": _yoy(row.revenue, prev.revenue if prev else None),
                "net_profit_yoy": _yoy(row.net_profit, prev.net_profit if prev else None),
                "gross_margin": row.gross_margin,
                "net_margin": row.net_margin,
                "operating_profit": op,
                "pretax_profit": pretax,
                "prev_operating_profit": prev_op,
                "prev_pretax_profit": prev_pretax,
                "below_breakdown": breakdown,
                "roe": row.roe,
                "debt_ratio": row.debt_ratio,
                "ocf": row.ocf,
                "net_profit_deducted": row.net_profit_deducted,
                "audit_opinion": row.audit_opinion,
            }
        )
    # 全历史用于计算 MAD 离群基线；只对最近 lookback 期产出信号
    start = max(1, len(series) - lookback)
    coverage["performed"].append(
        f"同报告期同比对齐（Q1/H1/Q3/FY 累计口径，不与 FY 混比）；"
        f"结论窗口为最近 {len(series) - start} / {len(series)} 期，全历史作为波动基线；"
        f"MAD 离群基线按报告期类型分组（阈值指纹 {cfg.fingerprint()}）"
    )
    profit_structure = _profit_structure_series(series[start:])
    if profit_structure:
        coverage["performed"].append(
            "利润结构定位：营业利润 / 税前利润 / 营业利润以下合计（按同报告期同比）"
        )
    else:
        coverage["skipped"].append({
            "check": "利润结构定位",
            "reason": "营业利润与税前利润均缺失（港股需 provider 映射，A 股需三大报表明细）",
        })

    # 全量序列仅用于「字段是否缺失」的覆盖度声明
    hist_gm = [s["gross_margin"] for s in series if s["gross_margin"] is not None]
    hist_nm = [s["net_margin"] for s in series if s["net_margin"] is not None]
    hist_roe = [s["roe"] for s in series if s["roe"] is not None]
    hist_debt = [s["debt_ratio"] for s in series if s["debt_ratio"] is not None]

    # 离群基线必须与当期同口径：Q1 累计值只能和历年 Q1 比，不能和 FY 混在一起，
    # 否则混合口径会把 MAD 撑大（或压小），让「是否离群」失真。
    baselines: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in series:
        for key in ("revenue_yoy", "gross_margin", "net_margin", "roe", "debt_ratio"):
            if s[key] is not None:
                baselines[s["report_type"]][key].append(s[key])

    seq_no = 0
    sig_no = 0

    def next_anomaly() -> str:
        nonlocal seq_no
        seq_no += 1
        return f"ANO{seq_no:03d}"

    def next_signal() -> str:
        nonlocal sig_no
        sig_no += 1
        return f"FIN{sig_no:03d}"

    for i in range(start, len(series)):
        s = series[i]
        prev_s = series[i - 1]
        row: FinancialReport = s["row"]
        period = s["period"]
        ref = f"financials:{s['report_date']}"

        # 1. 增收不增利
        if (
            s["revenue_yoy"] is not None
            and s["net_profit_yoy"] is not None
            and s["revenue_yoy"] > cfg.divergence_revenue_yoy_pct
            and s["net_profit_yoy"] < cfg.divergence_profit_yoy_pct
        ):
            signals.append(
                _signal(
                    next_signal(), "盈余质量", "high", "增收不增利", period,
                    [
                        {"label": "营业收入同比", "value": _pct(s["revenue_yoy"])},
                        {"label": "归母净利同比", "value": _pct(s["net_profit_yoy"])},
                        {"label": "营业收入", "value": _yi(s["revenue"])},
                        {"label": "归母净利润", "value": _yi(s["net_profit"])},
                    ],
                    f"营收同比 > {cfg.divergence_revenue_yoy_pct:g}% 且"
                    f"净利同比 < {cfg.divergence_profit_yoy_pct:g}%",
                    f"{period} 营业收入同比 {_pct(s['revenue_yoy'])}，但归母净利润同比 "
                    f"{_pct(s['net_profit_yoy'])}；收入增长未转化为利润。",
                    [ref],
                )
            )

        # 2. 利润异常放大（净利增速远超营收增速）
        if (
            s["revenue_yoy"] is not None
            and s["net_profit_yoy"] is not None
            and s["net_profit_yoy"] > 0
            and s["net_profit_yoy"] - s["revenue_yoy"] > cfg.profit_growth_gap_pp
        ):
            gap = s["net_profit_yoy"] - s["revenue_yoy"]
            signals.append(
                _signal(
                    next_signal(), "盈余质量", "medium", "利润增速显著背离营收", period,
                    [
                        {"label": "营收同比", "value": _pct(s["revenue_yoy"])},
                        {"label": "净利同比", "value": _pct(s["net_profit_yoy"])},
                        {"label": "增速差", "value": _pp(gap)},
                    ],
                    f"净利同比 - 营收同比 > {cfg.profit_growth_gap_pp:g} 个百分点",
                    f"{period} 归母净利同比 {_pct(s['net_profit_yoy'])}，高出营收同比 "
                    f"{_pp(gap)}，利润增速与收入增速的差额由利润结构解释。",
                    [ref],
                )
            )

        # 3. 营收增速突变（相邻同报告期年度）
        if prev_s and s["revenue_yoy"] is not None and prev_s["revenue_yoy"] is not None:
            delta = s["revenue_yoy"] - prev_s["revenue_yoy"]
            if abs(delta) > cfg.revenue_yoy_swing_pp:
                yoy_hist = baselines[s["report_type"]]["revenue_yoy"]
                is_outlier, median, mad = _mad_outlier(
                    yoy_hist, s["revenue_yoy"],
                    multiplier=cfg.mad_multiplier, min_samples=cfg.mad_min_samples,
                )
                signals.append(
                    _signal(
                        next_signal(), "业绩波动", "medium", "营收增速骤变", period,
                        [
                            {"label": "本期营收同比", "value": _pct(s["revenue_yoy"])},
                            {"label": "上年同期营收同比", "value": _pct(prev_s["revenue_yoy"])},
                            {"label": "增速变化", "value": _pp(delta)},
                            {"label": "自身历史中位数", "value": _pct(median) if yoy_hist else "样本不足"},
                        ],
                        f"相邻年度同报告期营收同比变化 > {cfg.revenue_yoy_swing_pp:g} 个百分点",
                        f"{period} 营收同比从 {_pct(prev_s['revenue_yoy'])} 变为 "
                        f"{_pct(s['revenue_yoy'])}，变化 {_pp(delta)}"
                        + ("，且偏离自身历史波动区间。" if is_outlier else "。"),
                        [ref, f"financials:{prev_s['report_date']}"],
                    )
                )
                anomalies.append(
                    _anomaly(
                        next_anomaly(), "营业收入同比", period, s["revenue_yoy"],
                        prev_s["revenue_yoy"], delta, "%",
                        f"|Δ| > {cfg.revenue_yoy_swing_pp:g} 个百分点", "up" if delta > 0 else "down",
                        "同报告期口径；自身历史离群" if is_outlier else "同报告期口径",
                    )
                )

        # 4~7. 率类指标异动（毛利率 / 净利率 / ROE / 负债率）
        # 负债率行业属性极强（银行常态 90%），默认要求「自身历史离群」才报；
        # 其余指标是否也要这一重过滤，由配置 rate_require_outlier 决定。
        rate_specs = (
            ("gross_margin", "毛利率", cfg.gross_margin_pp, "毛利率异动", "盈利率"),
            ("net_margin", "净利率", cfg.net_margin_pp, "净利率异动", "盈利率"),
            ("roe", "ROE", cfg.roe_pp, "ROE 显著变化", "回报率"),
            ("debt_ratio", "资产负债率", cfg.debt_ratio_pp, "资产负债率突变", "资产结构"),
        )
        for key, label, threshold, title, category in rate_specs:
            hist = baselines[s["report_type"]][key]
            require_outlier = key in cfg.rate_require_outlier
            cur = s[key]
            if cur is None:
                continue
            # 找上一年度同报告期的值（而非上一期累计值），保证口径一致
            prev_year_row = by_key.get((_period_key(row)[0] - 1, _period_key(row)[1]))
            prev_val = getattr(prev_year_row, key) if prev_year_row else None
            if prev_val is None:
                continue
            delta = cur - prev_val
            if abs(delta) <= threshold:
                continue
            is_outlier, median, mad = _mad_outlier(
                hist, cur, multiplier=cfg.mad_multiplier, min_samples=cfg.mad_min_samples
            )
            if require_outlier and not is_outlier:
                continue
            severity = "high" if (key == "roe" and delta < cfg.roe_drop_high_pp) else "medium"
            trigger = f"|同比变化| > {threshold:g} 个百分点"
            if require_outlier:
                trigger += "，且偏离自身历史波动"
            facts = [
                {"label": f"本期{label}", "value": _pct(cur)},
                {"label": "上年同期", "value": _pct(prev_val)},
                {"label": "同比变化", "value": _pp(delta)},
                {"label": "自身历史中位数", "value": _pct(median) if hist else "样本不足"},
            ]
            facts.extend(_profit_structure_facts(s))
            signals.append(
                _signal(
                    next_signal(), category, severity, title, period,
                    facts,
                    trigger,
                    f"{period} {label} {_pct(cur)}，较上年同期 {_pct(prev_val)} "
                    f"变化 {_pp(delta)}；历史中位数 {_pct(median) if hist else '样本不足'}。",
                    [ref],
                )
            )
            anomalies.append(
                _anomaly(
                    next_anomaly(), label, period, cur, prev_val, delta, "%",
                    f"|Δ| > {threshold:g} 个百分点", "up" if delta > 0 else "down",
                    "同报告期同比；自身历史 MAD 离群" if is_outlier else "同报告期同比",
                )
            )

        # 8. 扣非净利与归母净利缺口
        if (
            row.net_profit
            and row.net_profit_deducted is not None
            and row.net_profit > 0
        ):
            gap = (row.net_profit - row.net_profit_deducted) / row.net_profit * 100
            if gap > cfg.deducted_gap_pct:
                signals.append(
                    _signal(
                        next_signal(), "盈余质量",
                        "high" if gap > cfg.deducted_gap_high_pct else "medium",
                        "扣非净利显著低于归母净利", period,
                        [
                            {"label": "归母净利润", "value": _yi(row.net_profit)},
                            {"label": "扣非归母净利", "value": _yi(row.net_profit_deducted)},
                            {"label": "非经常性损益占比", "value": _pct(gap)},
                        ],
                        f"非经常性损益占归母净利 > {cfg.deducted_gap_pct:g}%",
                        f"{period} 归母净利 {_yi(row.net_profit)}，扣非后 {_yi(row.net_profit_deducted)}，"
                        f"非经常性损益占比 {_pct(gap)}；当期利润对一次性项目的依赖度需核查。",
                        [ref],
                    )
                )

        # 9. 利润未转化为经营现金流（连续 N 期，N 由配置决定）
        n_ocf = cfg.ocf_consecutive_periods
        if i + 1 >= n_ocf and s["ocf"] is not None and row.net_profit and row.net_profit > 0:
            window = series[i - n_ocf + 1:i + 1]
            ratios = [
                ss["ocf"] / ss["net_profit"]
                for ss in window
                if ss["ocf"] is not None and ss["net_profit"] and ss["net_profit"] > 0
            ]
            if len(ratios) == n_ocf and all(r < cfg.ocf_profit_ratio for r in ratios):
                ratio = ratios[-1]
                trigger = (
                    f"经营现金流/归母净利 连续 {n_ocf} 期 < {cfg.ocf_profit_ratio:g}"
                )
                signals.append(
                    _signal(
                        next_signal(), "现金质量", "high", "利润未转化为经营现金流", period,
                        [
                            {"label": f"连续 {n_ocf} 期比值", "value": " / ".join(f"{r:.2f}" for r in ratios)},
                            {"label": "本期经营现金流", "value": _yi(s["ocf"])},
                            {"label": "本期归母净利", "value": _yi(row.net_profit)},
                        ],
                        trigger,
                        f"{period} 经营现金流 {_yi(s['ocf'])}，仅覆盖归母净利的 {ratio:.0%}，"
                        f"且连续 {n_ocf} 期均低于 {cfg.ocf_profit_ratio:g}；"
                        "需核对应收账款、存货与收入确认节奏。",
                        [ref],
                    )
                )

        # 10. 审计意见非标
        if row.audit_opinion in (
            AuditOpinion.QUALIFIED, AuditOpinion.DISCLAIMER, AuditOpinion.ADVERSE
        ):
            signals.append(
                _signal(
                    next_signal(), "审计与合规", "high", "审计意见非标准", period,
                    [{"label": "审计意见", "value": str(row.audit_opinion.value)}],
                    "审计意见为保留/无法表示/否定",
                    f"{period} 审计意见为 {row.audit_opinion.value}，属已披露事实。",
                    [ref],
                )
            )

    # 10b. 业绩拐点：由多年高增长平台转入持续负增长（跨期模式识别）。
    # 与逐期单点规则不同，这里要求"先有达标的高增长平台、再转负、且转负持续"，
    # 只产出一条信号（拐点确认期），并把转负后的持续期数写进事实，避免逐期重复报。
    inflection = _detect_growth_inflection(series, start, cfg)
    if inflection:
        turn = inflection["turn"]
        platform_yoys = inflection["platform_yoys"]
        avg = inflection["platform_avg"]
        trailing = inflection["trailing"]
        neg_count = inflection["neg_count"]
        swing = turn["net_profit_yoy"] - avg  # type: ignore[operator]
        trailing_desc = "、".join(
            f"{t['period']} {_pct(t['net_profit_yoy'])}"
            for t in trailing if t["net_profit_yoy"] is not None
        )
        signals.append(
            _signal(
                next_signal(), "业绩波动", "high", "业绩拐点：高增长转负", turn["period"],
                [
                    {"label": "拐点确认期", "value": turn["period"]},
                    {"label": "此前高增长平台", "value": f"{len(platform_yoys)} 期，均值 {_pct(avg)}"},
                    {"label": "平台期净利同比", "value": " / ".join(_pct(y) for y in platform_yoys)},
                    {"label": "拐点期净利同比", "value": _pct(turn["net_profit_yoy"])},
                    {"label": "较平台均值变化", "value": _pp(swing)},
                    {"label": "转负后持续", "value": f"近 {len(trailing)} 期中 {neg_count} 期为负"},
                    {"label": "转负后各期", "value": trailing_desc},
                ],
                f"此前连续 {len(platform_yoys)} 期净利同比均 > {cfg.inflection_platform_floor_pct:g}% "
                f"且均值 {_pct(avg)} > {cfg.profit_turn_high_pct:g}%，本期转负，"
                f"且此后 >= {cfg.inflection_min_negative_periods} 期仍为负",
                f"{turn['period']} 结束了一段连续 {len(platform_yoys)} 期、均值 {_pct(avg)} 的高增长，"
                f"归母净利同比转为 {_pct(turn['net_profit_yoy'])}（较平台均值 {_pp(swing)}）；"
                f"此后近 {len(trailing)} 期中有 {neg_count} 期仍为负（{trailing_desc}），"
                "构成业绩拐点而非单期波动。需去拐点期与当期年报原文找公司自己的原因说明，"
                "并核对是需求、价格、渠道、产品结构还是基数效应所致。",
                [f"financials:{turn['report_date']}"] + [
                    f"financials:{p['report_date']}" for p in inflection["platform"][-2:]
                ],
            )
        )
        anomalies.append(
            _anomaly(
                next_anomaly(), "归母净利润同比（业绩拐点）", turn["period"],
                turn["net_profit_yoy"], avg, swing, "%",
                f"高增长平台均值 > {cfg.profit_turn_high_pct:g}% 且本期转负、持续为负", "down",
                "跨期模式；平台均值对拐点期",
            )
        )

    # 11. 连续亏损（按报告期序列）
    loss_window = cfg.loss_lookback_periods
    losses = [
        s for s in series[-loss_window:]
        if s["net_profit"] is not None and s["net_profit"] < 0
    ]
    if len(losses) >= cfg.loss_min_count:
        signals.append(
            _signal(
                next_signal(), "持续经营", "high", "连续多期亏损",
                f"{losses[0]['period']} ~ {losses[-1]['period']}",
                [{"label": "亏损期数", "value": f"{len(losses)} 期"}],
                f"近 {loss_window} 期内 ≥ {cfg.loss_min_count} 期归母净利为负",
                f"近 {loss_window} 期中有 {len(losses)} 期归母净利为负，需关注持续经营能力。",
                [f"financials:{s['report_date']}" for s in losses[:3]],
            )
        )

    if not hist_gm:
        coverage["skipped"].append({"check": "毛利率异动", "reason": "毛利率字段缺失（银行/港股常见）"})
    if not hist_nm:
        coverage["skipped"].append({"check": "净利率异动", "reason": "净利率字段缺失"})
    if not hist_debt:
        coverage["skipped"].append({"check": "资产负债率突变", "reason": "负债率字段缺失"})
    if not any(s["ocf"] is not None for s in series):
        coverage["skipped"].append({"check": "经营现金流与利润背离", "reason": "经营现金流字段缺失（银行/港股常见）"})
    if not any(s["net_profit_deducted"] is not None for s in series):
        coverage["skipped"].append({"check": "扣非净利缺口", "reason": "扣非净利字段缺失"})
    return start, profit_structure


# ---------- 利润表科目扫描 ----------


def _scan_statement_items(
    statement_items: list[StatementItem],
    signals: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
    lookback: int = 12,
) -> None:
    cfg = cfg or ForensicsConfig()
    if not statement_items:
        coverage["skipped"].append({"check": "利润表科目费用率异动", "reason": "无三大报表明细（港股常见）"})
        return

    # 与财报序列保持同一结论窗口，全历史仅作为科目费用率的同比基数来源
    all_dates = sorted({str(i.report_date) for i in statement_items})
    recent_dates = set(all_dates[-lookback:]) if all_dates else set()

    # 按 (报告期, 报表类型) 归集收入与费用；全量入库，历史期仅作为同比基数
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for item in statement_items:
        if item.statement_type != StatementType.INCOME or item.item_value is None:
            continue
        rt = item.report_type.value if hasattr(item.report_type, "value") else str(item.report_type)
        grouped[(str(item.report_date), rt)][item.item_code] = item.item_value

    if not grouped:
        coverage["skipped"].append({"check": "利润表科目费用率异动", "reason": "明细中无利润表数据"})
        return

    coverage["performed"].append(
        f"利润表科目费用率扫描（结论窗口 {len(recent_dates)} 期，同比基数用全量 {len(grouped)} 期）"
    )

    # 同报告期去年对齐：key = (YYYY-MM-DD, type) → 去年同一天
    def prev_year_key(key: tuple[str, str]) -> tuple[str, str] | None:
        day, rt = key
        try:
            return f"{int(day[:4]) - 1}{day[4:]}", rt
        except (ValueError, IndexError):
            return None

    stm_no = 0

    def next_stm() -> str:
        nonlocal stm_no
        stm_no += 1
        return f"STM{stm_no:03d}"

    for key in sorted(grouped):
        if key[0] not in recent_dates:
            continue
        items = grouped[key]
        revenue = next((items[c] for c in _REVENUE_CODES if c in items), None)
        if not revenue or revenue <= 0:
            continue
        prev = grouped.get(prev_year_key(key)) if prev_year_key(key) else None
        prev_revenue = (
            next((prev[c] for c in _REVENUE_CODES if c in prev), None) if prev else None
        )
        # 同一报告期往往因「收入口径整体变化」导致多个科目费用率同步跳动，
        # 逐科目单列会把一个根因刷成一堆风险点；按配置合并成一条，明细不丢。
        hits: list[dict[str, Any]] = []
        for code, label in _EXPENSE_CODES.items():
            value = items.get(code)
            if value is None:
                continue
            ratio = value / revenue * 100
            prev_value = prev.get(code) if prev else None
            prev_ratio = (
                prev_value / prev_revenue * 100
                if (prev_value is not None and prev_revenue and prev_revenue > 0)
                else None
            )
            if prev_ratio is None:
                continue
            delta = ratio - prev_ratio
            if abs(delta) < cfg.expense_ratio_pp:
                continue
            anomalies.append(
                _anomaly(
                    f"EXP{len(anomalies) + 1:03d}", f"{label}/营业收入", key[0],
                    ratio, prev_ratio, delta, "%",
                    f"|Δ| > {cfg.expense_ratio_pp:g} 个百分点",
                    "up" if delta > 0 else "down", "同报告期同比口径",
                )
            )
            hits.append(
                {"code": code, "label": label, "value": value, "ratio": ratio,
                 "prev_ratio": prev_ratio, "delta": delta}
            )

        if not hits:
            continue
        cluster_min = cfg.expense_cluster_min_items
        if cluster_min >= 2 and len(hits) >= cluster_min:
            joined = "、".join(f"{h['label']}率{_pp(h['delta'])}" for h in hits[:5])
            signals.append(
                _signal(
                    next_stm(), "费用与成本", "medium", "费用与成本结构同步异动", key[0],
                    [
                        {"label": "同期异动科目数", "value": f"{len(hits)} 个"},
                        {"label": "各科目费用率同比变化", "value": joined},
                        {"label": "本期营业收入", "value": _yi(revenue)},
                    ],
                    f"同一报告期 ≥ {cluster_min} 个科目费用率同比变化 > "
                    f"{cfg.expense_ratio_pp:g} 个百分点（同源合并）",
                    f"{key[0]} 共 {len(hits)} 个利润表科目费用率同步异动：{joined}。"
                    "多科目同步变动常由营业收入口径/规模变化或费用确认节奏引起，"
                    "建议先核对收入口径与合并范围，再逐科目看附注；"
                    "各科目精确数值见异常波动表。",
                    [f"statement_items:{key[0]}:{h['code']}" for h in hits[:5]],
                )
            )
            continue

        for h in hits:
            signals.append(
                _signal(
                    next_stm(), "费用与成本", "medium", f"{h['label']}率异动", key[0],
                    [
                        {"label": f"本期{h['label']}", "value": _yi(h["value"])},
                        {"label": "本期营业收入", "value": _yi(revenue)},
                        {"label": f"本期{h['label']}率", "value": _pct(h["ratio"])},
                        {"label": "上年同期费用率", "value": _pct(h["prev_ratio"])},
                        {"label": "同比变化", "value": _pp(h["delta"])},
                    ],
                    f"费用率同比变化 > {cfg.expense_ratio_pp:g} 个百分点",
                    f"{key[0]} {h['label']}占营收 {_pct(h['ratio'])}，较上年同期 "
                    f"{_pct(h['prev_ratio'])} 变化 {_pp(h['delta'])}；"
                    "需核对费用确认节奏、资本化或一次性计提。",
                    [f"statement_items:{key[0]}:{h['code']}"],
                )
            )


# ---------- 外部证据扫描 ----------


def _scan_forecasts(
    forecasts: list[EarningsForecast],
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
    recent_years: int = 6,
) -> list[dict[str, Any]]:
    """业绩预告 vs 实际：抓「预告变脸」。

    东财预告常只给「同比增长 50% 以上」而非绝对金额，因此同时支持
    金额区间与同比区间两种口径；同比一律按同报告期去年计算。
    """

    cfg = cfg or ForensicsConfig()
    if not forecasts:
        coverage["skipped"].append({"check": "业绩预告兑现度", "reason": "无业绩预告数据"})
        return []
    by_date = {str(r.report_date): r for r in financials}
    cutoff_year = 0
    try:
        from datetime import date

        cutoff_year = date.today().year - recent_years
    except Exception:
        pass

    def actual_yoy(rd: str, row: FinancialReport) -> float | None:
        prev = by_date.get(f"{int(rd[:4]) - 1}{rd[4:]}")
        if not prev or prev.report_type != row.report_type:
            return None
        return _yoy(row.net_profit, prev.net_profit)

    checks: list[dict[str, Any]] = []
    for fc in forecasts:
        rd = str(fc.report_date)
        try:
            if int(rd[:4]) < cutoff_year:
                continue
        except ValueError:
            continue
        row = by_date.get(rd)
        if not row:
            continue
        content = fc.content or ""
        # 东财预告字段口径混杂：营业收入 / 归母净利 / 扣非净利 / EPS 都可能塞进同一列。
        # 必须与公告正文核对，否则「营收预告 vs 实际净利」会造出约 -50% 的假偏离。
        revenue_forecast = "营业收入" in content and "净利润" not in content
        eps_forecast = "每股收益" in content
        deducted_forecast = "扣除非经常性" in content
        amount_is_profit = (
            "净利润" in content
            and not revenue_forecast
            and not eps_forecast
            and not deducted_forecast
        )
        yoy_is_profit = not (revenue_forecast or eps_forecast)
        amount_low, amount_high = fc.forecast_net_profit_low, fc.forecast_net_profit_high
        yoy_low, yoy_high = fc.net_profit_yoy_low, fc.net_profit_yoy_high
        mode = ""
        hit: bool | None = None
        deviation: float | None = None
        actual: float | None = None
        # 「约 / 左右 / 以上」的点值预告不是精确承诺，容差由配置给出
        tolerance = cfg.forecast_yoy_tolerance_pp
        amount_tol = cfg.forecast_amount_tolerance_pct / 100.0

        if amount_is_profit and amount_low is not None and amount_high is not None and amount_high > 0:
            mode = "净利润金额"
            actual = row.net_profit
            if actual is not None:
                lo = amount_low * (1 - amount_tol)
                hi = amount_high * (1 + amount_tol)
                hit = lo <= actual <= hi
                bound = hi if actual > hi else (lo if actual < lo else actual)
                deviation = (actual - bound) / abs(bound) * 100 if bound else None
        elif yoy_is_profit and (yoy_low is not None or yoy_high is not None):
            mode = "净利同比"
            actual = actual_yoy(rd, row)
            if actual is not None:
                lo = yoy_low - tolerance if yoy_low is not None else float("-inf")
                hi = yoy_high + tolerance if yoy_high is not None else float("inf")
                hit = lo <= actual <= hi
                if actual < lo:
                    deviation = actual - lo if lo != float("-inf") else None
                elif actual > hi:
                    deviation = actual - hi if hi != float("inf") else None
                else:
                    deviation = 0.0
        if actual is None or hit is None or deviation is None:
            continue

        checks.append(
            {
                "report_date": rd,
                "mode": mode,
                "forecast_low": amount_low if mode == "净利润金额" else yoy_low,
                "forecast_high": amount_high if mode == "净利润金额" else yoy_high,
                "actual": actual,
                "hit": hit,
                "deviation": deviation,
                "deviation_unit": "%" if mode == "净利润金额" else "个百分点",
                "forecast_type": fc.forecast_type.value if hasattr(fc.forecast_type, "value") else str(fc.forecast_type or ""),
                "notice_date": fc.notice_date,
                "content": (fc.content or "")[:120],
            }
        )
        threshold = cfg.forecast_deviation
        if not hit and abs(deviation) > threshold:
            unit = "%" if mode == "净利润金额" else " 个百分点"
            signals.append(
                _signal(
                    f"FC{len(checks):03d}", "业绩一致性", "high", "业绩预告与实际显著偏离",
                    rd,
                    [
                        {"label": f"预告{mode}下限", "value": _yi(amount_low) if mode == "净利润金额" else _pct(yoy_low)},
                        {"label": f"预告{mode}上限", "value": _yi(amount_high) if mode == "净利润金额" else _pct(yoy_high)},
                        {"label": "实际值", "value": _yi(row.net_profit) if mode == "净利润金额" else _pct(actual)},
                        {"label": "偏离最近边界", "value": f"{deviation:+.2f}{unit}"},
                    ],
                    f"实际值偏离预告区间 > {threshold:g}{unit}",
                    f"{rd} 业绩预告（{mode}）与实际结果偏离 {deviation:+.2f}{unit}；"
                    "需核对是否有预告修正公告、收入确认时点或一次性损益。",
                    [f"earnings_forecasts:{rd}", f"financials:{rd}"],
                )
            )
    if checks:
        coverage["performed"].append(
            f"业绩预告兑现度比对（近 {recent_years} 年 {len(checks)} 期可比对；"
            f"已按公告正文剔除营收/EPS/扣非口径预告，并对「约/左右」"
            f"留 {cfg.forecast_amount_tolerance_pct:g}% / "
            f"{cfg.forecast_yoy_tolerance_pp:g}pp 容差）"
        )
    else:
        coverage["skipped"].append({"check": "业绩预告兑现度", "reason": f"近 {recent_years} 年无净利口径预告/实际配对"})
    return checks


def _scan_regulatory(
    integrity: list[IntegrityRecord],
    announcements: list[Announcement],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> dict[str, Any]:
    """监管/法律/诚信线索：只列已披露事实，不下结论。"""

    cfg = cfg or ForensicsConfig()
    severe_categories = set(cfg.regulatory_severe_categories)
    routine_patterns = tuple(cfg.regulatory_routine_patterns)

    def is_routine(title: str) -> bool:
        """标题是否为按期披露的例行合规材料（非处罚、非违规证据）。"""
        return any(re.search(p, title or "") for p in routine_patterns)

    regulatory: list[dict[str, Any]] = []
    for item in integrity or []:
        cat = item.category.value if hasattr(item.category, "value") else str(item.category or "")
        sev = item.severity.value if hasattr(item.severity, "value") else str(item.severity or "")
        if cat not in severe_categories:
            continue
        routine = is_routine(item.title or "")
        regulatory.append(
            {
                "date": item.record_date or "",
                "category": cat,
                # 例行合规披露不计入高严重度：标题关键词不等于发生了违规
                "severity": "low" if routine else sev,
                "original_severity": sev,
                "routine": routine,
                "title": item.title,
                "description": (item.description or "")[:160],
                "source": item.source or "",
                "url": item.source_url or "",
                "kind": "例行合规披露（非处罚）" if routine else "已披露监管/诚信记录",
            }
        )

    substantive = [r for r in regulatory if not r["routine"]]
    routine_items = [r for r in regulatory if r["routine"]]
    if substantive:
        high = [r for r in substantive if r["severity"] == "high"]
        signals.append(
            _signal(
                "REG001", "监管与法律", "high" if high else "medium",
                "监管与诚信记录线索",
                f"{min(r['date'] for r in substantive)} ~ {max(r['date'] for r in substantive)}",
                [
                    {"label": "实质性记录条数", "value": f"{len(substantive)} 条"},
                    {"label": "高严重度", "value": f"{len(high)} 条"},
                    {"label": "例行合规披露（已剔除）", "value": f"{len(routine_items)} 条"},
                    {"label": "会计差错更正/重述", "value": f"{sum(1 for r in substantive if r['category'] == 'financial_restatement')} 条"},
                ],
                "存在处罚/重述/问询/立案等已披露记录（已剔除例行合规披露）",
                f"诚信档案中实质性监管/诚信记录 {len(substantive)} 条（高严重度 {len(high)} 条）；"
                f"另有 {len(routine_items)} 条例行合规披露（年度资金占用专项说明/汇总表、"
                "独董声明与承诺、差错责任追究制度等），这类文件是按期披露的合规材料，"
                "不构成违规或处罚证据，已从风险计数中剔除。"
                "以下为已披露事实清单，是否为实质性问题须结合公告原文与监管结论判断。",
                [f"integrity:{r['date']}:{r['title'][:24]}" for r in substantive[:5]],
            )
        )
    else:
        coverage["skipped"].append({
            "check": "监管与诚信记录",
            "reason": (
                f"近 {len(regulatory)} 条记录均为例行合规披露（非处罚/非违规），无实质性监管记录"
                if regulatory else "无相关记录或数据未同步"
            ),
        })

    hits: list[dict[str, Any]] = []
    for ann in announcements or []:
        title = ann.title or ""
        matched = [kw for kw in cfg.risk_title_keywords if kw in title]
        if not matched:
            continue
        routine = is_routine(title)
        hits.append(
            {
                "date": ann.publish_date or "",
                "title": title,
                "keywords": ",".join(matched[:4]),
                "source": ann.source or "",
                "url": ann.source_url or ann.pdf_url or "",
                "routine": routine,
                "kind": "例行合规披露（非处罚）" if routine else "公告标题风险词命中",
            }
        )
    substantive_hits = [h for h in hits if not h["routine"]]
    if substantive_hits:
        signals.append(
            _signal(
                "REG002", "监管与法律", "medium", "公告中出现风险类关键词",
                f"{min(h['date'] for h in substantive_hits if h['date']) or '-'} ~ {max(h['date'] for h in substantive_hits if h['date']) or '-'}",
                [
                    {"label": "命中公告数", "value": f"{len(substantive_hits)} 条"},
                    {"label": "例行合规披露（已剔除）", "value": f"{len(hits) - len(substantive_hits)} 条"},
                ],
                "公告标题命中立案/处罚/问询/差错更正等关键词（已剔除例行合规披露）",
                f"共 {len(substantive_hits)} 条公告标题命中风险关键词"
                f"（另有 {len(hits) - len(substantive_hits)} 条例行合规披露已剔除）；"
                "关键词匹配仅为线索，不代表存在违规。",
                [f"announcement:{h['date']}:{h['title'][:24]}" for h in substantive_hits[:5]],
            )
        )
    else:
        coverage["skipped"].append({
            "check": "公告风险关键词",
            "reason": (f"{len(hits)} 条命中均为例行合规披露" if hits else "无命中或公告数据未同步"),
        })

    return {"regulatory": regulatory, "announcement_hits": hits}


def _scan_capital_moves(
    insider_trades: list[InsiderTrade],
    pledges: list[Pledge],
    corporate_actions: list[CorporateAction],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or ForensicsConfig()
    insider_net = 0.0
    sell_count = 0
    for trade in insider_trades or []:
        if trade.change_shares is None:
            continue
        direction = trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction or "")
        if direction == "sell":
            insider_net -= abs(trade.change_shares)
            sell_count += 1
        elif direction == "buy":
            insider_net += abs(trade.change_shares)

    max_pledge = max(
        (p.pledge_total_ratio or p.pledge_ratio or 0) for p in (pledges or [])
    ) if pledges else 0.0
    actions = [
        {
            "date": a.action_date or "",
            "type": a.action_type.value if hasattr(a.action_type, "value") else str(a.action_type or ""),
            "amount": a.amount,
            "shares": a.shares,
            "status": a.status or "",
        }
        for a in (corporate_actions or [])
    ]

    if sell_count >= cfg.insider_sell_count and insider_net < 0:
        signals.append(
            _signal(
                "CAP001", "资金动向", "medium", "内部人净减持",
                f"近 {len(insider_trades or [])} 笔记录",
                [
                    {"label": "减持笔数", "value": f"{sell_count} 笔"},
                    {"label": "净变动股数", "value": f"{insider_net:,.0f} 股"},
                ],
                f"减持笔数 ≥ {cfg.insider_sell_count} 且净变动为负",
                f"内部人交易记录中 {sell_count} 笔减持，合计净变动 {insider_net:,.0f} 股。"
                "内部人交易属已披露事实，减持动机需结合公告判断。",
                ["insider_trades"],
            )
        )
    if max_pledge > cfg.pledge_ratio_pct:
        signals.append(
            _signal(
                "CAP002", "资金动向", "medium", "股权质押比例偏高",
                "最新记录",
                [{"label": "最高质押比例", "value": _pct(max_pledge)}],
                f"质押比例 > {cfg.pledge_ratio_pct:g}%",
                f"股权质押记录中最高比例 {_pct(max_pledge)}，需关注控股股东资金压力与平仓风险。",
                ["pledges"],
            )
        )
    if not insider_trades and not pledges and not corporate_actions:
        coverage["skipped"].append({"check": "内部人交易/质押/公司行为", "reason": "数据未同步"})
    else:
        coverage["performed"].append("内部人交易/质押/公司行为扫描")

    return {
        "insider_net_shares": insider_net,
        "insider_sell_count": sell_count,
        "max_pledge_ratio": max_pledge,
        "corporate_actions": actions[:12],
    }


# ---------- 资本运作与治理线索扫描 ----------


# 结构化数据里可离群检测的三类：item_code → (中文名, 取数方向)
_CAPEX_ITEM_CODES = {
    "NETCASH_INVEST": ("投资活动现金净流出", "abs"),  # 现金流为负=净流出，取绝对值
    "CIP": ("在建工程", "raw"),
}


def _annual_series_by_code(
    statement_items: list[StatementItem], code: str, *, absolute: bool = False
) -> list[tuple[int, float]]:
    """按年报(FY)取某科目的历年值，返回 [(年份, 值)] 升序。

    只取年报口径，避免 Q1/H1/Q3 累计口径与年报混用制造假异常。
    """
    points: dict[int, float] = {}
    for item in statement_items or []:
        if item.item_code != code or item.item_value is None:
            continue
        rt = item.report_type.value if hasattr(item.report_type, "value") else str(item.report_type)
        if rt != "FY":
            continue
        year = int(str(item.report_date)[:4])
        value = abs(item.item_value) if absolute else item.item_value
        points[year] = float(value)  # 同年多条取最后一条（通常为准）
    return sorted(points.items())


def _scan_capital_operations(
    dividends: list[DividendRecord],
    statement_items: list[StatementItem],
    announcements: list[Announcement],
    signals: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> dict[str, Any]:
    """资本运作与治理线索：分红/扩股/资本支出用自身历史离群，事件类用公告关键词。

    所有输出都是「值得让 LLM 去看财报原文」的线索，不代表存在问题。
    检测口径与项目其他扫描一致：以公司自身历史为基线（中位数 + MAD），
    不套跨行业硬阈值，避免把高分红行业误判为异常。
    """
    cfg = cfg or ForensicsConfig()
    result: dict[str, Any] = {
        "dividend_outliers": [], "share_capital_jumps": [],
        "capex_outliers": [], "goodwill_outliers": [], "action_hits": [],
    }

    # ---- 1. 突击分红：每股派现按年份聚合（同年多次求和），最新年度相对历史 MAD ----
    by_year: dict[int, float] = defaultdict(float)
    for d in dividends or []:
        if d.cash_per_share is None or not d.plan_notice_date:
            continue
        try:
            year = int(str(d.plan_notice_date)[:4])
        except (ValueError, IndexError):
            continue
        by_year[year] += float(d.cash_per_share)
    dividend_series = sorted(by_year.items())
    if len(dividend_series) >= 3:
        years = [y for y, _ in dividend_series]
        values = [v for _, v in dividend_series]
        cur_year, cur_val = dividend_series[-1]
        hist = values[:-1]
        outlier, median, mad = _mad_outlier(
            hist, cur_val, multiplier=cfg.dividend_mad_multiplier,
            min_samples=cfg.mad_min_samples,
        )
        if outlier and cur_val > median:
            pct = (cur_val - median) / median * 100 if median else None
            signals.append(_signal(
                "CAPOPS-DIV", "资本运作", "medium", "异常突击分红",
                f"{cur_year} 年",
                [
                    {"label": f"{cur_year}年每股派现(合计)", "value": f"{cur_val:.3f} 元"},
                    {"label": "历史年度中位数", "value": f"{median:.3f} 元"},
                    {"label": "偏离历史", "value": f"+{pct:.1f}%" if pct is not None else "数据缺失"},
                ],
                f"每股派现相对历史中位数偏离 > {cfg.dividend_mad_multiplier:g}×MAD 且高于历史",
                f"{cur_year} 年每股派现合计 {cur_val:.3f} 元，明显高于历史年度中位数 "
                f"{median:.3f} 元（偏离 {pct:.1f}%）。高分红可能反映现金流充裕与股东回报意愿，"
                "也可能在盈利下滑期形成「突击分红」，需结合当年净利润、经营现金流与分红方案原文判断。",
                [f"dividends:{cur_year}"],
            ))
            result["dividend_outliers"].append(
                {"year": cur_year, "value": cur_val, "median": median, "pct": pct}
            )
        else:
            coverage["skipped"].append(
                {"check": "异常突击分红", "reason": f"最新年度派现未显著偏离历史（{len(hist)} 个历史年度）"}
            )
    else:
        coverage["skipped"].append({"check": "异常突击分红", "reason": "分红年度样本不足 3 年"})

    # ---- 2. 扩充股本：SHARE_CAPITAL 年报序列同比增幅 ----
    share_series = _annual_series_by_code(statement_items, "SHARE_CAPITAL")
    if len(share_series) >= 2:
        cur_year, cur_share = share_series[-1]
        prev_year, prev_share = share_series[-2]
        growth = _yoy(cur_share, prev_share)
        if growth is not None and growth > cfg.share_capital_growth_pct:
            signals.append(_signal(
                "CAPOPS-SHARE", "资本运作", "medium", "扩充股本",
                f"{prev_year}→{cur_year} 年报",
                [
                    {"label": f"{cur_year}年股本", "value": f"{cur_share / 1e8:,.2f} 亿股"},
                    {"label": f"{prev_year}年股本", "value": f"{prev_share / 1e8:,.2f} 亿股"},
                    {"label": "同比增幅", "value": _pct(growth)},
                ],
                f"股本同比增幅 > {cfg.share_capital_growth_pct:g}%",
                f"股本由 {prev_year} 年的 {prev_share / 1e8:,.2f} 亿股变动至 {cur_year} 年的 "
                f"{cur_share / 1e8:,.2f} 亿股（{_pct(growth)}）。扩股可能来自增发、配股、"
                "转增或股权激励行权，需结合公告与稀释效应判断对每股收益的影响。",
                [f"statement_items:SHARE_CAPITAL:{cur_year}"],
            ))
            result["share_capital_jumps"].append(
                {"year": cur_year, "growth": growth, "from": prev_share, "to": cur_share}
            )
        else:
            coverage["skipped"].append({"check": "扩充股本", "reason": "股本同比未显著增长"})
    else:
        coverage["skipped"].append({"check": "扩充股本", "reason": "股本年报样本不足"})

    # ---- 3. 异常资本支出：投资现金净流出 + 在建工程，各自相对历史 MAD ----
    capex_found = False
    for code, (label, mode) in _CAPEX_ITEM_CODES.items():
        series = _annual_series_by_code(statement_items, code, absolute=(mode == "abs"))
        if len(series) < 3:
            continue
        years = [y for y, _ in series]
        values = [v for _, v in series]
        cur_year, cur_val = series[-1]
        hist = values[:-1]
        outlier, median, mad = _mad_outlier(
            hist, cur_val, multiplier=cfg.capex_mad_multiplier,
            min_samples=cfg.mad_min_samples,
        )
        if outlier and cur_val > median:
            capex_found = True
            pct = (cur_val - median) / median * 100 if median else None
            anomalies.append(_anomaly(
                f"CAPX{len(anomalies) + 1:03d}", label, f"{cur_year} 年报",
                cur_val, median, pct, "元",
                f"相对历史 > {cfg.capex_mad_multiplier:g}×MAD",
                "up", "年报口径，自身历史基线",
            ))
            result["capex_outliers"].append(
                {"item": label, "year": cur_year, "value": cur_val, "median": median, "pct": pct}
            )
    if not capex_found:
        coverage["skipped"].append({"check": "异常资本支出", "reason": "投资现金流/在建工程未显著偏离历史"})
    else:
        # 汇总成一条信号（明细已在 anomalies 中）
        items = result["capex_outliers"]
        desc = "；".join(
            f"{i['item']}{i['year']}年 {_yi(i['value'])}（历史中位 {_yi(i['median'])}，偏高 {i['pct']:.0f}%）"
            for i in items if i.get("pct") is not None
        )
        signals.append(_signal(
            "CAPOPS-CAPEX", "资本运作", "medium", "异常资本支出",
            " / ".join(f"{i['year']}" for i in items),
            [{"label": "偏离项数", "value": f"{len(items)} 项"},
             {"label": "明细", "value": desc[:160]}],
            f"投资现金流出/在建工程相对自身历史 > {cfg.capex_mad_multiplier:g}×MAD",
            f"资本支出相关科目相对公司自身历史明显偏高：{desc}。"
            "扩产投入可能支撑未来增长，也可能形成过剩产能与减值压力，"
            "需结合在建工程转固节奏与投资项目收益在财报原文中核对。",
            [f"statement_items:{i['item']}" for i in items],
        ))

    # ---- 4. 商誉突增：收购留下的会计痕迹 ----
    goodwill_series = _annual_series_by_code(statement_items, "GOODWILL")
    if len(goodwill_series) >= 3:
        cur_year, cur_gw = goodwill_series[-1]
        hist_vals = [v for _, v in goodwill_series[:-1]]
        outlier, median, mad = _mad_outlier(
            hist_vals, cur_gw, multiplier=cfg.goodwill_mad_multiplier,
            min_samples=cfg.mad_min_samples,
        )
        if outlier and cur_gw > median:
            signals.append(_signal(
                "CAPOPS-GW", "资本运作", "medium", "商誉显著增加（收购线索）",
                f"{cur_year} 年报",
                [{"label": f"{cur_year}年商誉", "value": _yi(cur_gw)},
                 {"label": "历史中位数", "value": _yi(median)}],
                f"商誉相对自身历史 > {cfg.goodwill_mad_multiplier:g}×MAD",
                f"{cur_year} 年商誉 {_yi(cur_gw)}，明显高于历史中位数 {_yi(median)}。"
                "商誉增加通常来自溢价收购，需关注被收购方的业绩承诺、"
                "未来减值风险与是否属于跨界并购，具体交易对价与原因见财报原文。",
                [f"statement_items:GOODWILL:{cur_year}"],
            ))
            result["goodwill_outliers"].append({"year": cur_year, "value": cur_gw, "median": median})
        else:
            coverage["skipped"].append({"check": "商誉突增", "reason": "商誉未显著偏离历史"})
    else:
        coverage["skipped"].append({"check": "商誉突增", "reason": "商誉年报样本不足 3 年"})

    # ---- 5. 事件类线索：公告标题关键词（收购/跨界/股权激励/关联交易） ----
    keyword_groups = cfg.capital_action_keywords or {}
    for label, keywords in keyword_groups.items():
        hits = []
        for ann in announcements or []:
            title = ann.title or ""
            matched = [kw for kw in keywords if kw in title]
            if not matched:
                continue
            hits.append({
                "date": ann.publish_date or "",
                "title": title,
                "keywords": ",".join(matched[:3]),
                "url": ann.source_url or ann.pdf_url or "",
            })
        if hits:
            hits.sort(key=lambda h: h["date"], reverse=True)
            signals.append(_signal(
                f"CAPOPS-EVT-{label}", "资本运作", "low", label,
                f"{min(h['date'] for h in hits if h['date']) or '-'} ~ {max(h['date'] for h in hits if h['date']) or '-'}",
                [{"label": "命中公告数", "value": f"{len(hits)} 条"},
                 {"label": "最近一条", "value": hits[0]["title"][:40]}],
                f"公告标题命中「{'/'.join(keywords[:3])}」等关键词",
                f"共 {len(hits)} 条公告标题涉及「{label}」（关键词命中仅为线索，"
                "不构成对公司行为的定性）。需结合公告与财报原文确认交易实质、金额与审批程序。",
                [f"announcement:{h['date']}:{h['title'][:24]}" for h in hits[:5]],
            ))
            result["action_hits"].extend([{**h, "type": label} for h in hits])
        else:
            coverage["skipped"].append({"check": f"{label}公告线索", "reason": "公告标题无关键词命中"})

    if any(result.values()):
        coverage["performed"].append("资本运作与治理线索扫描（分红/扩股/资本支出/商誉离群 + 公告关键词）")
    return result


def _scan_price_reaction(
    kline: list[KlineBar],
    announcements: list[Announcement],
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> list[dict[str, Any]]:
    """财报披露日前后的股价与成交量异动，与业绩方向交叉比对。"""

    cfg = cfg or ForensicsConfig()
    w = cfg.price_window_days
    pub_days = cfg.price_publish_window_days
    bars = sorted(kline or [], key=lambda b: b.trade_date)
    if len(bars) < cfg.price_min_bars:
        coverage["skipped"].append({"check": "财报披露股价反应", "reason": "K 线数据不足"})
        return []

    # 从公告标题里定位定期报告披露日
    pub_dates: dict[str, str] = {}
    for ann in announcements or []:
        title = ann.title or ""
        if not re.search(r"(季度报告|年度报告|半年度报告|中期报告|业绩快报)", title):
            continue
        day = ann.publish_date or ""
        if not day:
            continue
        # 用标题中的年份 + 报告类型粗略归属到最近的财报期
        pub_dates.setdefault(day[:7], day)

    index = {b.trade_date: i for i, b in enumerate(bars)}
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]

    # 按报告期找披露日：报告期后 20~75 天内首个交易日作为兜底近似
    report_days = sorted({str(r.report_date) for r in financials})[-cfg.price_reaction_periods:]
    reactions: list[dict[str, Any]] = []
    for rd in report_days:
        candidates = [d for d in pub_dates.values() if rd < d <= _plus_days(rd, pub_days)]
        day = min(candidates) if candidates else None
        approx = day is None
        if day is None:
            day = next((b.trade_date for b in bars if rd < b.trade_date <= _plus_days(rd, pub_days)), None)
        if not day or day not in index:
            continue
        i = index[day]
        if i < w or i + w >= len(bars):
            continue
        pre, post = closes[i - w], closes[i + w]
        if not pre or post is None:
            continue
        ret = (post - pre) / pre * 100
        window = [v for v in volumes[i - w:i + w] if v is not None]
        vol_pre = statistics.fmean([v for v in volumes[i - w:i] if v is not None]) if window else 0
        vol_post = statistics.fmean([v for v in volumes[i:i + w] if v is not None])
        vol_ratio = vol_post / vol_pre if vol_pre else None

        # 该报告期的净利同比（同报告期去年）
        row = next((r for r in financials if str(r.report_date) == rd), None)
        prev = next(
            (r for r in financials
             if str(r.report_date) == f"{int(rd[:4]) - 1}{rd[4:]}"
             and r.report_type == (row.report_type if row else None)),
            None,
        ) if row else None
        np_yoy = _yoy(row.net_profit, prev.net_profit if prev else None) if row else None

        reactions.append(
            {
                "report_date": rd,
                "publish_date": day,
                "publish_date_approx": approx,
                "window_return_pct": ret,
                "volume_ratio": vol_ratio,
                "net_profit_yoy": np_yoy,
            }
        )
        if np_yoy is not None and (
            (np_yoy > cfg.market_profit_yoy_up_pct and ret < cfg.market_return_down_pct)
            or (np_yoy < cfg.market_profit_yoy_down_pct and ret > cfg.market_return_up_pct)
        ):
            signals.append(
                _signal(
                    f"MKT{len(reactions):03d}", "市场反应", "medium",
                    "业绩方向与股价反应背离", rd,
                    [
                        {"label": "归母净利同比", "value": _pct(np_yoy)},
                        {"label": f"披露日前后 {w} 日涨跌", "value": _pct(ret)},
                        {"label": "成交量放大倍数", "value": f"{vol_ratio:.2f}×" if vol_ratio else "数据缺失"},
                        {"label": "披露日", "value": day + ("（近似）" if approx else "")},
                    ],
                    f"净利同比 > +{cfg.market_profit_yoy_up_pct:g}% 但股价 {w} 日跌 > "
                    f"{abs(cfg.market_return_down_pct):g}%，或净利同比 < "
                    f"{cfg.market_profit_yoy_down_pct:g}% 但涨 > {cfg.market_return_up_pct:g}%",
                    f"{rd} 归母净利同比 {_pct(np_yoy)}，披露日前后 {w} 个交易日股价 {_pct(ret)}，"
                    f"成交量 {vol_ratio:.2f}×；业绩方向与市场反应不一致，"
                    "可能存在提前反应、预期差或对公司口径的质疑。"
                    if vol_ratio
                    else f"{rd} 归母净利同比 {_pct(np_yoy)}，披露日前后 {w} 个交易日股价 {_pct(ret)}。",
                    [f"kline:{day}", f"financials:{rd}"],
                )
            )

    if not reactions:
        coverage["skipped"].append({"check": "财报披露股价反应", "reason": "无法定位披露日或窗口数据不足"})
    else:
        coverage["performed"].append(f"财报披露股价反应（{len(reactions)} 期）")
    return reactions


def _plus_days(day: str, days: int) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    except ValueError:
        return day


def _scan_external_views(
    intel: dict[str, Any] | None,
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> list[dict[str, Any]]:
    """第三方资料中的负面印证：只作线索，不作结论。"""

    cfg = cfg or ForensicsConfig()
    if not intel:
        coverage["skipped"].append({"check": "研报/调研负面印证", "reason": "情报资料未加载"})
        return []
    views: list[dict[str, Any]] = []
    for kind, key in (
        ("个股研报", "company_reports"), ("行业研报", "industry_reports"), ("机构调研", "surveys")
    ):
        for item in (intel.get(key) or []):
            text = f"{item.get('title') or ''} {(item.get('content') or '')[:cfg.external_scan_chars]}"
            matched = [kw for kw in cfg.negative_view_keywords if kw in text]
            if not matched:
                continue
            views.append(
                {
                    "kind": kind,
                    "date": item.get("date") or "",
                    "org": item.get("org") or "",
                    "title": item.get("title") or "",
                    "keywords": ",".join(matched[:5]),
                    "url": item.get("url") or "",
                    "excerpt": (item.get("content") or "")[:cfg.external_excerpt_chars],
                }
            )
    if views:
        signals.append(
            _signal(
                "EXT001", "第三方印证", "low", "第三方资料出现负面表述",
                f"共 {len(views)} 条",
                [{"label": "命中资料数", "value": f"{len(views)} 条"}],
                "研报/调研标题或正文命中下调/低于预期/减值/问询等词",
                f"共 {len(views)} 条第三方资料出现负面表述。关键词命中仅为线索，"
                "卖方观点可能偏乐观或偏悲观，须回到原文核对。",
                [f"intel:{v['kind']}:{v['date']}" for v in views[:5]],
            )
        )
    else:
        coverage["skipped"].append({"check": "研报/调研负面印证", "reason": "无负面关键词命中"})
    return views


# ---------- 单季还原（累计差分）与现金含量对照 ----------


def _quarter_label(year: int, prev_seq: int, seq: int) -> str:
    """相邻累计期差分得到单季；缺期时写成区间，不把多期增量冒充单季。"""

    if seq - prev_seq == 1:
        return f"{year} Q{seq}"
    return f"{year} Q{prev_seq + 1}~Q{seq}"


def _derive_quarters(
    financials: list[FinancialReport], lookback: int
) -> dict[int, dict[str, Any]]:
    """Q1/H1/Q3/FY 累计值 → 单季增量（同一自然年内差分，绝不跨年）。

    只看结论窗口内的报告期；科目缺失时该单季字段留 None，不补数、不插值。
    ``is_single`` 为 False 的条目是多期合并增量，只进事实序列、不参与分布判定。
    """

    window = financials[-lookback:] if lookback and lookback < len(financials) else financials
    by_year: dict[int, dict[str, Any]] = {}
    for row in sorted(window, key=_period_key):
        year, seq = _period_key(row)
        if seq <= 0:
            continue
        by_year.setdefault(year, {"rows": {}, "quarters": []})["rows"][seq] = row
    for year, bucket in by_year.items():
        rows: dict[int, FinancialReport] = bucket["rows"]
        prev_seq = 0
        for seq in sorted(rows):
            cur = rows[seq]
            prev = rows.get(prev_seq)
            q: dict[str, Any] = {
                "label": _quarter_label(year, prev_seq, seq),
                "year": year, "seq": seq,
                "report_date": str(cur.report_date),
                "is_single": (seq == 1 and prev is None)
                or (prev is not None and seq - prev_seq == 1),
            }
            for attr in ("net_profit", "ocf", "revenue"):
                value = getattr(cur, attr, None)
                if value is None:
                    q[attr] = None
                elif prev is None:
                    # Q1 本身就是单季累计；缺 Q1 时首条累计值不能当单季用
                    q[attr] = value if seq == 1 else None
                else:
                    base = getattr(prev, attr, None)
                    q[attr] = value - base if base is not None else None
            bucket["quarters"].append(q)
            prev_seq = seq
        bucket["fy"] = rows.get(4)
    return by_year


def _scan_quarterly_distribution(
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
    lookback: int = 12,
) -> dict[str, Any]:
    """单季利润分布、单季现金流大额流出、全年 vs 最新累计期现金含量对照。

    解决的误判：只看最新三季报累计的「经营现金流/净利」会把季节性回款节奏
    当成全年恶化；只看全年同比又看不见 Q2 单季利润塌陷。三条都是**待解释线索**
    （low 为主），由 LLM 结合财报原文判断季节性、口径与实质性。
    """

    cfg = cfg or ForensicsConfig()
    by_year = _derive_quarters(financials, lookback)
    quarters = [
        q for year in sorted(by_year) for q in by_year[year]["quarters"] if q["is_single"]
    ]
    summary: dict[str, Any] = {
        "method": "同一自然年内 Q1/H1/Q3/FY 累计值差分；不跨年、不与去年累计直接相减",
        "quarters": [
            {k: v for k, v in q.items() if k != "is_single"} for q in quarters
        ],
        "annual_cash_conversion": None,
        "latest_cumulative_cash_conversion": None,
    }
    if not quarters:
        coverage["skipped"].append({
            "check": "单季还原与分布异常",
            "reason": "结论窗口内没有相邻累计报告期，无法差分出单季值",
        })
        return summary
    coverage["performed"].append(
        f"单季还原：结论窗口覆盖 {len(by_year)} 个报告年份，共差分出 {len(quarters)} 个单季增量"
    )

    # 最近一个完整年度：作为年度现金含量与单季现金流规模的共同基准
    fy_rows = [b["fy"] for b in by_year.values() if b.get("fy") is not None]
    latest_fy = max(fy_rows, key=lambda r: str(r.report_date)) if fy_rows else None
    fy_profit = getattr(latest_fy, "net_profit", None) if latest_fy else None
    fy_ocf = getattr(latest_fy, "ocf", None) if latest_fy else None
    annual_ratio = (
        fy_ocf / fy_profit if (fy_ocf is not None and fy_profit and fy_profit > 0) else None
    )
    if annual_ratio is not None:
        summary["annual_cash_conversion"] = {
            "period": str(latest_fy.report_date), "report_type": "FY",
            "ocf": fy_ocf, "net_profit": fy_profit, "ratio": round(annual_ratio, 3),
        }
    else:
        coverage["skipped"].append({
            "check": "全年现金含量对照",
            "reason": "最近完整年度缺经营现金流或归母净利（A 股需科目明细回填 ocf）",
        })

    sig_no = 0

    def next_id() -> str:
        nonlocal sig_no
        sig_no += 1
        return f"QTR{sig_no:03d}"

    for year in sorted(by_year):
        bucket = by_year[year]
        fy = bucket.get("fy")
        year_quarters = [
            q for q in bucket["quarters"] if q["is_single"] and q["net_profit"] is not None
        ]
        # 1. 单季利润分布：只在完整年度 + 足够单季样本时判定
        has_complete_year = (
            fy is not None and getattr(fy, "net_profit", None) and fy.net_profit > 0
            and len(year_quarters) >= cfg.quarter_min_periods
        )
        if has_complete_year:
            avg = fy.net_profit / len(year_quarters)
            low = min(year_quarters, key=lambda q: q["net_profit"])
            if low["net_profit"] < avg / cfg.quarter_profit_gap_x:
                share = low["net_profit"] / fy.net_profit * 100
                facts = [
                    {"label": f"{q['label']} 归母净利", "value": _yi(q["net_profit"])}
                    for q in year_quarters
                ]
                facts += [
                    {"label": "全年归母净利", "value": _yi(fy.net_profit)},
                    {"label": "年均单季净利", "value": _yi(avg)},
                    {"label": "最小单季占全年", "value": _pct(share)},
                    {"label": "数据出处", "value": f"financials {year} 年累计报告期差分"},
                ]
                signals.append(
                    _signal(
                        next_id(), "业绩波动",
                        "medium" if low["net_profit"] < 0 else "low",
                        "单季利润分布极不均衡", f"{year} 年",
                        facts,
                        f"最小单季净利 < 年均单季净利的 1/{cfg.quarter_profit_gap_x:g} 倍"
                        f"（且全年盈利、至少 {cfg.quarter_min_periods} 个单季可还原）",
                        f"{year} 年 {low['label']} 归母净利 {_yi(low['net_profit'])}，"
                        f"仅占全年 {_pct(share)}，而年均单季为 {_yi(avg)}。"
                        "白酒等行业回款与确认有季节性，这一分布本身不代表异常，"
                        "需结合分季度数据、收入确认口径调整与公司说明判断是否有实质变化。",
                        [f"financials:{q['report_date']}" for q in year_quarters],
                    )
                )

        # 2. 单季经营现金流大额净流出：以上一完整年度归母净利作规模基准。
        # 本年尚未出年报（如最新三个季度）同样要检查，不能因缺 FY 跳过。
        ocf_quarters = [
            q for q in bucket["quarters"] if q["is_single"] and q["ocf"] is not None
        ]
        if fy_profit and fy_profit > 0 and len(ocf_quarters) >= 2:
            limit = fy_profit * cfg.quarter_ocf_outflow_pct / 100
            hits = [q for q in ocf_quarters if q["ocf"] < -limit]
            if hits:
                signals.append(
                    _signal(
                        next_id(), "现金质量", "low", "单季经营现金流大额净流出", f"{year} 年",
                        [
                            {"label": f"{q['label']} 经营现金流", "value": _yi(q["ocf"])}
                            for q in ocf_quarters
                        ] + [
                            {"label": "规模基准（最近完整年度归母净利）", "value": _yi(fy_profit)},
                            {"label": "触发线", "value": _yi(-limit)},
                            {"label": "数据出处", "value": f"financials {year} 年累计报告期差分"},
                        ],
                        f"单季经营现金流净流出 > 上一完整年度归母净利的 "
                        f"{cfg.quarter_ocf_outflow_pct:g}%（{_yi(limit)}）",
                        f"{year} 年有 {len(hits)} 个单季经营现金流净流出超过 {_yi(limit)}："
                        + "、".join(f"{q['label']} {_yi(q['ocf'])}" for q in hits)
                        + "。累计口径下单一季度的现金流受回款、备货与税费节奏影响很大，"
                        "需对照全年净现比与去年同期单季，不宜直接推断盈利质量恶化。",
                        [f"financials:{q['report_date']}" for q in hits],
                    )
                )

    # 3. 全年 vs 最新累计期现金含量（两者口径不同，必须显式并列而非相减）
    ordered = sorted(financials, key=_period_key)
    latest = next(
        (
            r for r in reversed(ordered)
            if getattr(r, "ocf", None) is not None
            and getattr(r, "net_profit", None)
            and r.net_profit > 0
        ),
        None,
    )
    if latest_fy and latest and str(latest.report_date) > str(latest_fy.report_date):
        latest_ratio = latest.ocf / latest.net_profit
        summary["latest_cumulative_cash_conversion"] = {
            "period": str(latest.report_date),
            "report_type": latest.report_type.value
            if hasattr(latest.report_type, "value") else str(latest.report_type),
            "ocf": latest.ocf, "net_profit": latest.net_profit,
            "ratio": round(latest_ratio, 3),
        }
        if (
            annual_ratio is not None
            and annual_ratio >= cfg.cash_conversion_healthy
            and latest_ratio < cfg.ocf_profit_ratio
        ):
            signals.append(
                _signal(
                    next_id(), "现金质量", "low",
                    "最新累计期现金含量显著低于上一完整年度", str(latest.report_date),
                    [
                        {"label": f"{latest_fy.report_date} 全年经营现金流", "value": _yi(fy_ocf)},
                        {"label": f"{latest_fy.report_date} 全年归母净利", "value": _yi(fy_profit)},
                        {"label": "全年经营现金流/净利", "value": f"{annual_ratio:.2f}"},
                        {"label": f"{latest.report_date} 累计经营现金流", "value": _yi(latest.ocf)},
                        {"label": f"{latest.report_date} 累计归母净利", "value": _yi(latest.net_profit)},
                        {"label": "累计期经营现金流/净利", "value": f"{latest_ratio:.2f}"},
                    ],
                    f"上一完整年度经营现金流/净利 >= {cfg.cash_conversion_healthy:g}，"
                    f"而最新累计期 < {cfg.ocf_profit_ratio:g}",
                    f"上一完整年度经营现金流 {_yi(fy_ocf)} / 归母净利 {_yi(fy_profit)} = "
                    f"{annual_ratio:.2f}，但 {latest.report_date} 累计口径只有 {latest_ratio:.2f}。"
                    "两者口径不同（全年 vs 年初至今累计），低比值更多反映回款节奏与季节性；"
                    "判断盈利质量须以完整年度为主、累计期为辅，并结合应收账款与合同负债变化。",
                    [f"financials:{latest_fy.report_date}", f"financials:{latest.report_date}"],
                )
            )
    else:
        coverage["skipped"].append({
            "check": "全年 vs 最新累计期现金含量对照",
            "reason": "没有晚于最近完整年度、且同时具备经营现金流与归母净利的累计报告期",
        })
    return summary


# ---------- 财报原文结构化事实的趋势 / 组合信号 ----------


def _scan_evidence_facts(
    report_facts: dict[str, Any] | None,
    audit_evidence: dict[str, Any] | None,
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    cfg: ForensicsConfig | None = None,
) -> dict[str, Any]:
    """把「财报正文抽出的硬数字」转成确定性趋势/组合线索。

    services 层只做章节定位 + 数字抽取 + 原文摘录，本函数只做数值判定；
    不读文件、不读配置、不调用模型。抽不到就写 coverage.skipped，绝不补数。
    """

    cfg = cfg or ForensicsConfig()
    rf = report_facts or {}
    ae = audit_evidence or {}
    summary: dict[str, Any] = {
        "related_party_trend": [], "operating_volume": None, "consolidation_scope": None,
    }
    sig_no = 0

    def next_id() -> str:
        nonlocal sig_no
        sig_no += 1
        return f"RPT{sig_no:03d}"

    def as_float(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    # 1. 关联方销售 / 采购占比连续抬升（多期年报趋势，而非单点高值）
    trend_hits: list[dict[str, Any]] = []
    for key, label in (
        ("related_party_sales_pct", "关联方销售占年度销售总额"),
        ("related_party_purchase_pct", "关联方采购占年度采购总额"),
    ):
        rows = [
            r for r in (rf.get(key) or [])
            if isinstance(r, dict) and as_float(r.get("value")) is not None
        ]
        rows.sort(key=lambda r: str(r.get("period") or ""))
        values = [as_float(r["value"]) for r in rows]
        if len(rows) < 2 or any(v is None for v in values):
            continue
        delta = values[-1] - values[0]  # type: ignore[operator]
        rising = all(b >= a for a, b in zip(values, values[1:]))  # type: ignore[operator]
        summary["related_party_trend"].append({
            "metric": label, "periods": [str(r.get("period")) for r in rows],
            "values": values, "change_pp": round(delta, 2), "rising": rising,
            "source": str(rows[-1].get("source") or ""),
        })
        if rising and delta > cfg.related_trend_pp:
            trend_hits.append({
                "label": label, "rows": rows, "values": values, "delta": delta,
            })
    if trend_hits:
        facts: list[dict[str, str]] = []
        for hit in trend_hits:
            first, last = hit["rows"][0], hit["rows"][-1]
            facts.append({
                "label": f"{hit['label']}（{first.get('period')}→{last.get('period')}）",
                "value": f"{hit['values'][0]:.2f}% → {hit['values'][-1]:.2f}%"
                         f"（+{hit['delta']:.2f} 个百分点）",
            })
            facts.append({
                "label": f"{hit['label']} 原文出处",
                "value": f"{last.get('source') or '财报原文'}："
                         f"{str(last.get('quote') or '')[:90]}",
            })
        signals.append(
            _signal(
                next_id(), "关联交易", "low", "关联方交易占比连续抬升",
                str(trend_hits[0]["rows"][-1].get("period") or ""),
                facts,
                f"同一占比指标连续 {len(trend_hits[0]['values'])} 期不下降，"
                f"且首末合计抬升 > {cfg.related_trend_pp:g} 个百分点",
                "多期年报正文披露的关联方销售/采购占比持续抬升。"
                "占比上升会改变收入与采购对关联主体的依赖程度，"
                "需核对关联交易定价、审批程序与同类第三方价格；"
                "这是可比性与依赖度线索，不是利益输送或造假的结论。",
                [f"report_facts:{str(h['rows'][-1].get('period') or '')}" for h in trend_hits],
            )
        )
    else:
        coverage["skipped"].append({
            "check": "关联方交易占比趋势",
            "reason": "财报原文未抽到连续两期关联方销售/采购占比，或抬升未达阈值",
        })

    # 2. 实物量组合：销量下滑 + 库存累积（+ 经销商净减少作为加权证据）
    volume = next((v for v in (ae.get("operating_volume") or []) if isinstance(v, dict)), None)
    if not volume:
        coverage["skipped"].append({
            "check": "实物量组合信号", "reason": "财报原文未抽到产销存实物量表",
        })
    else:
        sales_yoy = as_float(volume.get("sales_volume_yoy_pct"))
        inventory_yoy = as_float(volume.get("inventory_volume_yoy_pct"))
        dealer_change = as_float(volume.get("dealer_change"))
        summary["operating_volume"] = {
            "period": volume.get("period"), "source": volume.get("source"),
            "sales_volume_yoy_pct": sales_yoy, "inventory_volume_yoy_pct": inventory_yoy,
            "dealer_change": dealer_change,
        }
        if (
            sales_yoy is not None and inventory_yoy is not None
            and sales_yoy <= -cfg.volume_sales_drop_pct
            and inventory_yoy >= cfg.volume_inventory_rise_pct
        ):
            dealer_hit = dealer_change is not None and dealer_change <= -cfg.dealer_drop_count
            facts = [
                {"label": "实物销售量同比", "value": _pct(sales_yoy)},
                {"label": "实物库存量同比", "value": _pct(inventory_yoy)},
                {"label": "本期销量 / 上年销量（吨）",
                 "value": f"{volume.get('sales_volume_ton') or '数据缺失'} / "
                          f"{volume.get('sales_volume_prev_ton') or '数据缺失'}"},
                {"label": "经销商净变化", "value":
                    f"{dealer_change:+.0f} 家" if dealer_change is not None else "数据缺失"},
                {"label": "原文出处", "value":
                    f"{volume.get('source') or '财报原文'}："
                    f"{str(volume.get('quote_volume') or volume.get('quote') or '')[:90]}"},
            ]
            signals.append(
                _signal(
                    next_id(), "业绩波动", "medium" if dealer_hit else "low",
                    "实物销量下滑与库存、渠道同步承压",
                    str(volume.get("period") or ""), facts,
                    f"销量同比降幅 > {cfg.volume_sales_drop_pct:g}% 且 "
                    f"库存同比增幅 > {cfg.volume_inventory_rise_pct:g}%"
                    + (f"，且经销商净减少 > {cfg.dealer_drop_count:d} 家" if dealer_hit else ""),
                    f"{volume.get('source') or '财报原文'}披露实物销售量同比 {_pct(sales_yoy)}、"
                    f"库存量同比 {_pct(inventory_yoy)}"
                    + (f"、经销商净减少 {abs(dealer_change):.0f} 家" if dealer_hit else "")
                    + "。实物量比财务比率更早反映渠道动销与压货，"
                    "需核对公司年报对变动原因的说明、库存商品结构与后续去化安排；"
                    "该组合只说明需要解释，不直接等同收入确认问题。",
                    [f"report_text:{volume.get('period') or ''}"],
                )
            )
        else:
            coverage["skipped"].append({
                "check": "实物量组合信号",
                "reason": "销量与库存未同时越过阈值，或实物量表缺同比数值",
            })

    # 3. 合并范围新增主体：会直接改变同比可比性，即便只有 1 家也值得列线索
    scope = next((s for s in (ae.get("consolidation_scope") or []) if isinstance(s, dict)), None)
    if cfg.consolidation_min_entities <= 0:
        coverage["skipped"].append({
            "check": "合并范围新增主体", "reason": "consolidation_min_entities=0，配置已关闭",
        })
    elif not scope:
        coverage["skipped"].append({
            "check": "合并范围新增主体", "reason": "财报原文未抽到合并范围变动章节",
        })
    else:
        entities = [str(x) for x in (scope.get("entities") or []) if str(x).strip()]
        summary["consolidation_scope"] = {
            "period": scope.get("period"), "applicable": bool(scope.get("applicable")),
            "entity_count": len(entities), "entities": entities,
            "source": scope.get("source"),
        }
        if scope.get("applicable") and len(entities) >= cfg.consolidation_min_entities:
            signals.append(
                _signal(
                    next_id(), "会计口径", "low", "合并范围新增主体影响同比可比性",
                    str(scope.get("period") or ""),
                    [
                        {"label": "新增主体数", "value": f"{len(entities)} 家"},
                        {"label": "新增主体", "value": "、".join(entities[:5])},
                        {"label": "公司披露", "value": str(scope.get("reason") or "")[:120]},
                        {"label": "原文出处", "value":
                            f"{scope.get('source') or '财报原文'}："
                            f"{str(scope.get('quote') or '')[:90]}"},
                    ],
                    f"年报明确勾选合并范围发生变动，且抽到 >= "
                    f"{cfg.consolidation_min_entities:d} 家新增主体",
                    f"{scope.get('source') or '财报原文'}披露报告期内合并范围发生变动"
                    f"（新增 {len(entities)} 家主体）。新增主体会把收入、资产与利润并入合并报表，"
                    "判断营收与利润同比趋势前需先剔除口径影响；"
                    "这是可比性提示，不代表并表本身存在异常。",
                    [f"report_text:{scope.get('period') or ''}"],
                )
            )
        else:
            coverage["skipped"].append({
                "check": "合并范围新增主体",
                "reason": "年报标注未发生变动，或抽到的新增主体数未达阈值",
            })
    coverage["performed"].append(
        "财报原文结构化事实判定：关联方占比趋势 / 实物量组合 / 合并范围变动"
        "（原文由 services 层抽取，本层只做数值比较）"
    )
    return summary


# ---------- 信号聚合 ----------


def _consolidate_signals(
    signals: list[dict[str, Any]], min_occurrences: int = 3
) -> tuple[list[dict[str, Any]], int]:
    """把连续多期重复出现的同类信号合并成「趋势型风险点」。

    例如 ROE 逐季下滑会产生 4 条「ROE 显著变化」，逐条罗列既冗余又掩盖趋势；
    合并成 1 条后仍把每期明细放进 ``occurrences``，信息不丢失，LLM 可逐期分析。
    ``min_occurrences`` 由配置给出，调小可显著减少条目数；<= 0 表示关闭聚合。
    """

    rank = {"high": 3, "medium": 2, "low": 1}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in signals:
        groups[(item["category"], item["title"])].append(item)

    out: list[dict[str, Any]] = []
    merged_count = 0
    for (category, title), items in groups.items():
        if min_occurrences <= 0 or len(items) < max(2, min_occurrences):
            out.extend(items)
            continue
        ordered = sorted(items, key=lambda x: x["period"])
        first, last = ordered[0], ordered[-1]
        merged = dict(first)
        merged["period"] = f"{first['period']} ~ {last['period']}（共 {len(items)} 期）"
        merged["severity"] = max(items, key=lambda x: rank.get(x["severity"], 0))["severity"]
        merged["facts"] = list(first["facts"]) + [
            {"label": "出现期数", "value": f"{len(items)} 期"},
            {"label": "末次报告期", "value": last["period"]},
        ]
        merged["description"] = (
            f"结论窗口内共有 {len(items)} 个报告期触发「{title}」，"
            f"从 {first['period']} 延续到 {last['period']}；这是持续性趋势而非单期偶发波动，"
            "逐期数值见 occurrences。"
        )
        refs: list[str] = []
        for item in ordered:
            for ref in item.get("evidence_refs") or []:
                if ref not in refs:
                    refs.append(ref)
        merged["evidence_refs"] = refs[:12]
        merged["occurrences"] = [
            {"period": x["period"], "severity": x["severity"], "facts": x["facts"]}
            for x in ordered
        ]
        merged["consolidated"] = True
        out.append(merged)
        merged_count += len(items) - 1
    return out, merged_count


# ---------- 主入口 ----------


def _recent(items: Iterable[Any], attr: str, years: int) -> list[Any]:
    """事件类证据只看近 N 年；日期缺失的记录保留（宁可多列线索，不静默丢弃）。"""

    from datetime import date

    today = date.today()
    try:
        cutoff = today.replace(year=today.year - years).isoformat()
    except ValueError:  # 2 月 29 日
        cutoff = today.replace(year=today.year - years, day=28).isoformat()
    out = []
    for item in items or []:
        day = str(getattr(item, attr, None) or "")
        if not day or day >= cutoff:
            out.append(item)
    return out


def scan_forensics(
    symbol: str,
    financials: list[FinancialReport],
    statement_items: list[StatementItem] | None = None,
    kline: list[KlineBar] | None = None,
    integrity: list[IntegrityRecord] | None = None,
    announcements: list[Announcement] | None = None,
    forecasts: list[EarningsForecast] | None = None,
    dividends: list[DividendRecord] | None = None,
    insider_trades: list[InsiderTrade] | None = None,
    pledges: list[Pledge] | None = None,
    corporate_actions: list[CorporateAction] | None = None,
    intel: dict[str, Any] | None = None,
    *,
    lookback_periods: int | None = None,
    recent_years: int | None = None,
    config: ForensicsConfig | None = None,
    report_facts: dict[str, Any] | None = None,
    audit_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行全量取证扫描，返回结构化信号、异常波动、证据与覆盖度。

    ``config`` 由 services 层从 ``config/forensics.toml`` 注入；不传时用内置默认值
    （便于测试）。``lookback_periods`` 控制财报结论窗口，``recent_years`` 控制
    事件类证据窗口；显式传入时覆盖配置值。全历史仅作为波动基线。

    ``report_facts`` / ``audit_evidence`` 是 services 层从财报正文抽取的纯 dict
    （每条附原文摘录），本模块只做数值判定；不传时对应检查写入 coverage.skipped。
    """

    cfg = config or ForensicsConfig()
    lookback = cfg.lookback_periods if lookback_periods is None else lookback_periods
    years = cfg.recent_years if recent_years is None else recent_years

    signals: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {"performed": [], "skipped": []}
    ordered = sorted(financials, key=_period_key)

    _, profit_structure = _scan_financial_series(
        ordered, signals, anomalies, coverage, cfg=cfg, lookback=lookback,
        statement_items=statement_items,
    )
    _scan_statement_items(
        statement_items or [], signals, anomalies, coverage, cfg=cfg, lookback=lookback
    )
    forecast_checks = _scan_forecasts(
        forecasts or [], ordered, signals, coverage, cfg=cfg, recent_years=years
    )
    regulatory = _scan_regulatory(
        _recent(integrity or [], "record_date", years),
        _recent(announcements or [], "publish_date", years),
        signals,
        coverage,
        cfg=cfg,
    )
    capital = _scan_capital_moves(
        _recent(insider_trades or [], "trade_date", years),
        pledges or [],
        _recent(corporate_actions or [], "action_date", years),
        signals,
        coverage,
        cfg=cfg,
    )
    capital_ops = _scan_capital_operations(
        _recent(dividends or [], "plan_notice_date", years),
        statement_items or [],
        _recent(announcements or [], "publish_date", years),
        signals,
        anomalies,
        coverage,
        cfg=cfg,
    )
    price_reactions = _scan_price_reaction(
        kline or [], announcements or [], ordered, signals, coverage, cfg=cfg
    )
    external_views = _scan_external_views(intel, signals, coverage, cfg=cfg)
    # 累计口径差分还原单季 / 全年-累计现金含量对照（只用 financials + cfg）
    quarterly = _scan_quarterly_distribution(
        ordered, signals, coverage, cfg=cfg, lookback=lookback
    )
    # 财报原文抽取的结构化事实 → 确定性趋势/组合线索（只用传入的纯 dict + cfg）
    structured = _scan_evidence_facts(
        report_facts, audit_evidence, signals, coverage, cfg=cfg
    )

    # 趋势聚合 + 统一编号，保证 id 全局唯一且稳定
    signals, merged_count = _consolidate_signals(signals, cfg.consolidate_min_occurrences)
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    signals.sort(key=lambda x: (-severity_rank.get(x["severity"], 0), x["title"], x["period"]))
    for i, item in enumerate(signals, start=1):
        item["id"] = f"SIG{i:03d}"
    for i, item in enumerate(anomalies, start=1):
        item["id"] = f"ANO{i:03d}"

    evidence = {
        **regulatory,
        "forecast_checks": forecast_checks,
        "capital_moves": capital,
        "capital_operations": capital_ops,
        "price_reactions": price_reactions,
        "external_views": external_views,
        "structured": structured,
    }
    counts = {level: sum(1 for s in signals if s["severity"] == level) for level in ("high", "medium", "low")}

    return {
        "symbol": symbol,
        "window": {
            "lookback_periods": lookback,
            "recent_years": years,
            "config_fingerprint": cfg.fingerprint(),
            "conclusion_from": str(ordered[-lookback].report_date)
            if len(ordered) >= lookback else (str(ordered[0].report_date) if ordered else None),
            "conclusion_to": str(ordered[-1].report_date) if ordered else None,
        },
        "periods": {
            "count": len(ordered),
            "first": str(ordered[0].report_date) if ordered else None,
            "last": str(ordered[-1].report_date) if ordered else None,
            "statement_periods": len({str(i.report_date) for i in (statement_items or [])}),
        },
        "signals": signals,
        "anomalies": anomalies,
        "profit_structure": profit_structure,
        "quarterly": quarterly,
        "evidence": evidence,
        "coverage": coverage,
        "summary": {
            "signal_count": len(signals),
            "raw_signal_count": len(signals) + merged_count,
            "consolidated": merged_count,
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "anomaly_count": len(anomalies),
            "risk_level": (
                "high" if counts["high"] >= cfg.risk_high_min_high
                else (
                    "medium" if counts["high"] or counts["medium"] >= cfg.risk_medium_min_medium
                    else ("low" if signals else "clear")
                )
            ),
        },
    }
