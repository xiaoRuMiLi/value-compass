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
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Iterable, Sequence

from fundamental.models import (
    Announcement,
    AuditOpinion,
    CorporateAction,
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

# 监管/法律/风险线索关键词（用于公告标题；只作线索，不作结论）
_RISK_TITLE_KEYWORDS = (
    "立案", "处罚", "罚没", "诉讼", "仲裁", "问询函", "关注函", "监管函",
    "警示函", "冻结", "查封", "会计差错", "前期差错", "更正", "追溯调整",
    "退市", "风险警示", "违规担保", "资金占用", "被执行", "重整", "涉嫌",
    "调查", "通报批评", "公开谴责", "减值", "业绩预告修正", "业绩变脸",
)

# 第三方资料中的负面印证词（只作为线索级证据）
_NEGATIVE_VIEW_KEYWORDS = (
    "下调", "低于预期", "不及预期", "减值", "问询", "诉讼", "处罚", "风险",
    "承压", "下滑", "缩水", "争议", "质疑", "违规",
)

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


def _period_key(row: FinancialReport) -> tuple[int, int]:
    """返回 (年份, 期序)，用于同报告期对齐。"""

    year = int(str(row.report_date)[:4])
    return year, _PERIOD_ORDER.get(row.report_type, 0)


def _period_name(row: FinancialReport) -> str:
    label = _PERIOD_LABELS.get(
        row.report_type.value if hasattr(row.report_type, "value") else str(row.report_type), ""
    )
    return f"{row.report_date} {label}"


def _mad_outlier(values: Sequence[float], current: float) -> tuple[bool, float, float]:
    """基于自身历史的中位数绝对偏差离群判定。

    返回 (是否离群, 中位数, MAD)。样本不足或历史几乎无波动时返回 False，
    避免把「本来就没变过的指标」的一次微小变动放大成异常。
    """

    if len(values) < 4:
        return False, (statistics.median(values) if values else 0.0), 0.0
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    if mad < 1e-6:
        # 历史完全平坦时任何非零偏离都是真异常，不能一律吞掉；
        # 但完全相等（current == median）仍应判为正常。
        return abs(current - median) > 1e-6, median, mad
    return abs(current - median) > 3 * mad, median, mad


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


# ---------- 财报序列扫描 ----------


def _scan_financial_series(
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    coverage: dict[str, Any],
    lookback: int = 12,
) -> int:
    """扫描财报序列。

    全历史用于计算 MAD 离群基线，但**只对最近 ``lookback`` 个报告期产出信号**，
    避免 20 多年前的历史噪音淹没当前结论。返回被略过的历史信号数（供背景说明）。
    """

    ordered = sorted(financials, key=_period_key)
    if len(ordered) < 2:
        coverage["skipped"].append({"check": "财务序列异常", "reason": "财报期数不足 2 期"})
        return 0

    # 同报告期（去年同类型）索引，保证累计口径可比
    by_key = {_period_key(row): row for row in ordered}

    series: list[dict[str, Any]] = []
    for row in ordered:
        year, seq = _period_key(row)
        prev = by_key.get((year - 1, seq))
        rt = row.report_type.value if hasattr(row.report_type, "value") else str(row.report_type)
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
        f"结论窗口为最近 {len(series) - start} / {len(series)} 期，全历史作为波动基线"
    )

    hist_yoy = [s["revenue_yoy"] for s in series if s["revenue_yoy"] is not None]
    hist_gm = [s["gross_margin"] for s in series if s["gross_margin"] is not None]
    hist_nm = [s["net_margin"] for s in series if s["net_margin"] is not None]
    hist_roe = [s["roe"] for s in series if s["roe"] is not None]
    hist_debt = [s["debt_ratio"] for s in series if s["debt_ratio"] is not None]

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
            and s["revenue_yoy"] > 10
            and s["net_profit_yoy"] < -10
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
                    "营收同比 > +10% 且净利同比 < -10%",
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
            and s["net_profit_yoy"] - s["revenue_yoy"] > 40
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
                    "净利同比 - 营收同比 > 40 个百分点",
                    f"{period} 归母净利同比 {_pct(s['net_profit_yoy'])}，高出营收同比 "
                    f"{_pp(gap)}；需核对是否由非经常性损益、费用资本化或基数效应造成。",
                    [ref],
                )
            )

        # 3. 营收增速突变（相邻同报告期年度）
        if prev_s and s["revenue_yoy"] is not None and prev_s["revenue_yoy"] is not None:
            delta = s["revenue_yoy"] - prev_s["revenue_yoy"]
            if abs(delta) > 30:
                is_outlier, median, mad = _mad_outlier(hist_yoy, s["revenue_yoy"])
                signals.append(
                    _signal(
                        next_signal(), "业绩波动", "medium", "营收增速骤变", period,
                        [
                            {"label": "本期营收同比", "value": _pct(s["revenue_yoy"])},
                            {"label": "上年同期营收同比", "value": _pct(prev_s["revenue_yoy"])},
                            {"label": "增速变化", "value": _pp(delta)},
                            {"label": "自身历史中位数", "value": _pct(median) if hist_yoy else "样本不足"},
                        ],
                        "相邻年度同报告期营收同比变化 > 30 个百分点",
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
                        "|Δ| > 30 个百分点", "up" if delta > 0 else "down",
                        "同报告期口径；自身历史离群" if is_outlier else "同报告期口径",
                    )
                )

        # 4~7. 率类指标异动（毛利率 / 净利率 / ROE / 负债率）
        rate_specs = (
            ("gross_margin", "毛利率", hist_gm, 5.0, "毛利率异动", "盈利率", False),
            ("net_margin", "净利率", hist_nm, 5.0, "净利率异动", "盈利率", False),
            ("roe", "ROE", hist_roe, 3.0, "ROE 显著变化", "回报率", False),
            # 负债率行业属性极强（银行常态 90%），必须同时满足「自身历史离群」才报，
            # 否则会把行业常态或季度噪音误判成结构突变。
            ("debt_ratio", "资产负债率", hist_debt, 6.0, "资产负债率突变", "资产结构", True),
        )
        for key, label, hist, threshold, title, category, require_outlier in rate_specs:
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
            is_outlier, median, mad = _mad_outlier(hist, cur)
            if require_outlier and not is_outlier:
                continue
            severity = "high" if (key == "roe" and delta < -3) else "medium"
            signals.append(
                _signal(
                    next_signal(), category, severity, title, period,
                    [
                        {"label": f"本期{label}", "value": _pct(cur)},
                        {"label": "上年同期", "value": _pct(prev_val)},
                        {"label": "同比变化", "value": _pp(delta)},
                        {"label": "自身历史中位数", "value": _pct(median) if hist else "样本不足"},
                    ],
                    f"|同比变化| > {threshold:g} 个百分点，且偏离自身历史波动",
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
            if gap > 20:
                signals.append(
                    _signal(
                        next_signal(), "盈余质量", "high" if gap > 40 else "medium",
                        "扣非净利显著低于归母净利", period,
                        [
                            {"label": "归母净利润", "value": _yi(row.net_profit)},
                            {"label": "扣非归母净利", "value": _yi(row.net_profit_deducted)},
                            {"label": "非经常性损益占比", "value": _pct(gap)},
                        ],
                        "非经常性损益占归母净利 > 20%",
                        f"{period} 归母净利 {_yi(row.net_profit)}，扣非后 {_yi(row.net_profit_deducted)}，"
                        f"非经常性损益占比 {_pct(gap)}；当期利润对一次性项目的依赖度需核查。",
                        [ref],
                    )
                )

        # 9. 利润未转化为经营现金流（连续 2 期）
        if i > 0 and s["ocf"] is not None and row.net_profit and row.net_profit > 0:
            prev_ocf = prev_s["ocf"] if prev_s else None
            prev_np = prev_s["net_profit"] if prev_s else None
            ratio = s["ocf"] / row.net_profit
            prev_ratio = (
                prev_ocf / prev_np if (prev_ocf is not None and prev_np and prev_np > 0) else None
            )
            if ratio < 0.8 and prev_ratio is not None and prev_ratio < 0.8:
                signals.append(
                    _signal(
                        next_signal(), "现金质量", "high", "利润未转化为经营现金流", period,
                        [
                            {"label": "本期经营现金流/净利", "value": f"{ratio:.2f}"},
                            {"label": "上一期经营现金流/净利", "value": f"{prev_ratio:.2f}"},
                            {"label": "本期经营现金流", "value": _yi(s["ocf"])},
                            {"label": "本期归母净利", "value": _yi(row.net_profit)},
                        ],
                        "经营现金流/归母净利 连续 2 期 < 0.8",
                        f"{period} 经营现金流 {_yi(s['ocf'])}，仅覆盖归母净利的 {ratio:.0%}，"
                        f"且上一期同样低于 0.8；需核对应收账款、存货与收入确认节奏。",
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

    # 11. 连续亏损（按报告期序列）
    losses = [s for s in series[-8:] if s["net_profit"] is not None and s["net_profit"] < 0]
    if len(losses) >= 3:
        signals.append(
            _signal(
                next_signal(), "持续经营", "high", "连续多期亏损",
                f"{losses[0]['period']} ~ {losses[-1]['period']}",
                [{"label": "亏损期数", "value": f"{len(losses)} 期"}],
                "近 8 期内 ≥ 3 期归母净利为负",
                f"近 8 期中有 {len(losses)} 期归母净利为负，需关注持续经营能力。",
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
    return start


# ---------- 利润表科目扫描 ----------


def _scan_statement_items(
    statement_items: list[StatementItem],
    signals: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    coverage: dict[str, Any],
    lookback: int = 12,
) -> None:
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
            if abs(delta) < 3:
                continue
            anomalies.append(
                _anomaly(
                    f"EXP{len(anomalies) + 1:03d}", f"{label}/营业收入", key[0],
                    ratio, prev_ratio, delta, "%",
                    "|Δ| > 3 个百分点", "up" if delta > 0 else "down",
                    "同报告期同比口径",
                )
            )
            signals.append(
                _signal(
                    f"STM{len([s for s in signals if s['id'].startswith('STM')]) + 1:03d}",
                    "费用与成本", "medium", f"{label}率异动", key[0],
                    [
                        {"label": f"本期{label}", "value": _yi(value)},
                        {"label": "本期营业收入", "value": _yi(revenue)},
                        {"label": f"本期{label}率", "value": _pct(ratio)},
                        {"label": "上年同期费用率", "value": _pct(prev_ratio)},
                        {"label": "同比变化", "value": _pp(delta)},
                    ],
                    "费用率同比变化 > 3 个百分点",
                    f"{key[0]} {label}占营收 {_pct(ratio)}，较上年同期 {_pct(prev_ratio)} "
                    f"变化 {_pp(delta)}；需核对费用确认节奏、资本化或一次性计提。",
                    [f"statement_items:{key[0]}:{code}"],
                )
            )


# ---------- 外部证据扫描 ----------


def _scan_forecasts(
    forecasts: list[EarningsForecast],
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
    recent_years: int = 6,
) -> list[dict[str, Any]]:
    """业绩预告 vs 实际：抓「预告变脸」。

    东财预告常只给「同比增长 50% 以上」而非绝对金额，因此同时支持
    金额区间与同比区间两种口径；同比一律按同报告期去年计算。
    """

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
        # 「约 / 左右 / 以上」的点值预告不是精确承诺，留 5% / 5pp 容差
        tolerance = 5.0

        if amount_is_profit and amount_low is not None and amount_high is not None and amount_high > 0:
            mode = "净利润金额"
            actual = row.net_profit
            if actual is not None:
                lo, hi = amount_low * 0.95, amount_high * 1.05
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
        threshold = 20.0
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
            "已按公告正文剔除营收/EPS/扣非口径预告，并对「约/左右」留 5% 容差）"
        )
    else:
        coverage["skipped"].append({"check": "业绩预告兑现度", "reason": f"近 {recent_years} 年无净利口径预告/实际配对"})
    return checks


def _scan_regulatory(
    integrity: list[IntegrityRecord],
    announcements: list[Announcement],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """监管/法律/诚信线索：只列已披露事实，不下结论。"""

    severe_categories = {
        "penalty", "financial_restatement", "misleading_disclosure", "inquiry",
        "violation_guarantee", "fund_occupation", "broken_promise",
    }
    regulatory: list[dict[str, Any]] = []
    for item in integrity or []:
        cat = item.category.value if hasattr(item.category, "value") else str(item.category or "")
        sev = item.severity.value if hasattr(item.severity, "value") else str(item.severity or "")
        if cat not in severe_categories:
            continue
        regulatory.append(
            {
                "date": item.record_date or "",
                "category": cat,
                "severity": sev,
                "title": item.title,
                "description": (item.description or "")[:160],
                "source": item.source or "",
                "url": item.source_url or "",
                "kind": "已披露监管/诚信记录",
            }
        )
    if regulatory:
        high = [r for r in regulatory if r["severity"] == "high"]
        signals.append(
            _signal(
                "REG001", "监管与法律", "high" if high else "medium",
                "监管与诚信记录线索",
                f"{min(r['date'] for r in regulatory)} ~ {max(r['date'] for r in regulatory)}",
                [
                    {"label": "记录条数", "value": f"{len(regulatory)} 条"},
                    {"label": "高严重度", "value": f"{len(high)} 条"},
                    {"label": "会计差错更正/重述", "value": f"{sum(1 for r in regulatory if r['category'] == 'financial_restatement')} 条"},
                ],
                "存在处罚/重述/资金占用/承诺类等已披露记录",
                f"诚信档案共 {len(regulatory)} 条相关记录（高严重度 {len(high)} 条）。"
                "以下为已披露事实清单，是否为实质性问题须结合公告原文与监管结论判断。",
                [f"integrity:{r['date']}:{r['title'][:24]}" for r in regulatory[:5]],
            )
        )
    else:
        coverage["skipped"].append({"check": "监管与诚信记录", "reason": "无相关记录或数据未同步"})

    hits: list[dict[str, Any]] = []
    for ann in announcements or []:
        title = ann.title or ""
        matched = [kw for kw in _RISK_TITLE_KEYWORDS if kw in title]
        if not matched:
            continue
        hits.append(
            {
                "date": ann.publish_date or "",
                "title": title,
                "keywords": ",".join(matched[:4]),
                "source": ann.source or "",
                "url": ann.source_url or ann.pdf_url or "",
                "kind": "公告标题风险词命中",
            }
        )
    if hits:
        signals.append(
            _signal(
                "REG002", "监管与法律", "medium", "公告中出现风险类关键词",
                f"{min(h['date'] for h in hits if h['date']) or '-'} ~ {max(h['date'] for h in hits if h['date']) or '-'}",
                [{"label": "命中公告数", "value": f"{len(hits)} 条"}],
                "公告标题命中立案/处罚/问询/差错更正/资金占用等关键词",
                f"共 {len(hits)} 条公告标题命中风险关键词（关键词匹配仅为线索，不代表存在违规）。",
                [f"announcement:{h['date']}:{h['title'][:24]}" for h in hits[:5]],
            )
        )
    else:
        coverage["skipped"].append({"check": "公告风险关键词", "reason": "无命中或公告数据未同步"})

    return {"regulatory": regulatory, "announcement_hits": hits}


def _scan_capital_moves(
    insider_trades: list[InsiderTrade],
    pledges: list[Pledge],
    corporate_actions: list[CorporateAction],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
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

    if sell_count >= 3 and insider_net < 0:
        signals.append(
            _signal(
                "CAP001", "资金动向", "medium", "内部人净减持",
                f"近 {len(insider_trades or [])} 笔记录",
                [
                    {"label": "减持笔数", "value": f"{sell_count} 笔"},
                    {"label": "净变动股数", "value": f"{insider_net:,.0f} 股"},
                ],
                "减持笔数 ≥ 3 且净变动为负",
                f"内部人交易记录中 {sell_count} 笔减持，合计净变动 {insider_net:,.0f} 股。"
                "内部人交易属已披露事实，减持动机需结合公告判断。",
                ["insider_trades"],
            )
        )
    if max_pledge > 30:
        signals.append(
            _signal(
                "CAP002", "资金动向", "medium", "股权质押比例偏高",
                "最新记录",
                [{"label": "最高质押比例", "value": _pct(max_pledge)}],
                "质押比例 > 30%",
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


def _scan_price_reaction(
    kline: list[KlineBar],
    announcements: list[Announcement],
    financials: list[FinancialReport],
    signals: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    """财报披露日前后的股价与成交量异动，与业绩方向交叉比对。"""

    bars = sorted(kline or [], key=lambda b: b.trade_date)
    if len(bars) < 12:
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
    report_days = sorted({str(r.report_date) for r in financials})[-8:]
    reactions: list[dict[str, Any]] = []
    for rd in report_days:
        candidates = [d for d in pub_dates.values() if rd < d <= _plus_days(rd, 75)]
        day = min(candidates) if candidates else None
        approx = day is None
        if day is None:
            day = next((b.trade_date for b in bars if rd < b.trade_date <= _plus_days(rd, 75)), None)
        if not day or day not in index:
            continue
        i = index[day]
        if i < 5 or i + 5 >= len(bars):
            continue
        pre, post = closes[i - 5], closes[i + 5]
        if not pre or post is None:
            continue
        ret = (post - pre) / pre * 100
        window = [v for v in volumes[i - 5:i + 5] if v is not None]
        vol_pre = statistics.fmean([v for v in volumes[i - 5:i] if v is not None]) if window else 0
        vol_post = statistics.fmean([v for v in volumes[i:i + 5] if v is not None])
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
            (np_yoy > 15 and ret < -5) or (np_yoy < -15 and ret > 10)
        ):
            signals.append(
                _signal(
                    f"MKT{len(reactions):03d}", "市场反应", "medium",
                    "业绩方向与股价反应背离", rd,
                    [
                        {"label": "归母净利同比", "value": _pct(np_yoy)},
                        {"label": "披露日前后 5 日涨跌", "value": _pct(ret)},
                        {"label": "成交量放大倍数", "value": f"{vol_ratio:.2f}×" if vol_ratio else "数据缺失"},
                        {"label": "披露日", "value": day + ("（近似）" if approx else "")},
                    ],
                    "净利同比 > +15% 但股价 5 日跌 > 5%，或净利同比 < -15% 但涨 > 10%",
                    f"{rd} 归母净利同比 {_pct(np_yoy)}，披露日前后 5 个交易日股价 {_pct(ret)}，"
                    f"成交量 {vol_ratio:.2f}×；业绩方向与市场反应不一致，"
                    "可能存在提前反应、预期差或对公司口径的质疑。"
                    if vol_ratio
                    else f"{rd} 归母净利同比 {_pct(np_yoy)}，披露日前后 5 个交易日股价 {_pct(ret)}。",
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
) -> list[dict[str, Any]]:
    """第三方资料中的负面印证：只作线索，不作结论。"""

    if not intel:
        coverage["skipped"].append({"check": "研报/调研负面印证", "reason": "情报资料未加载"})
        return []
    views: list[dict[str, Any]] = []
    for kind, key in (
        ("个股研报", "company_reports"), ("行业研报", "industry_reports"), ("机构调研", "surveys")
    ):
        for item in (intel.get(key) or []):
            text = f"{item.get('title') or ''} {(item.get('content') or '')[:600]}"
            matched = [kw for kw in _NEGATIVE_VIEW_KEYWORDS if kw in text]
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
                    "excerpt": (item.get("content") or "")[:200],
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


# ---------- 信号聚合 ----------


def _consolidate_signals(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """把连续多期重复出现的同类信号合并成「趋势型风险点」。

    例如 ROE 逐季下滑会产生 4 条「ROE 显著变化」，逐条罗列既冗余又掩盖趋势；
    合并成 1 条后仍把每期明细放进 ``occurrences``，信息不丢失，LLM 可逐期分析。
    """

    rank = {"high": 3, "medium": 2, "low": 1}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in signals:
        groups[(item["category"], item["title"])].append(item)

    out: list[dict[str, Any]] = []
    merged_count = 0
    for (category, title), items in groups.items():
        if len(items) < 3:
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
            "应优先判断其驱动因素（行业周期、基数效应、会计口径或经营质量变化）。"
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
    insider_trades: list[InsiderTrade] | None = None,
    pledges: list[Pledge] | None = None,
    corporate_actions: list[CorporateAction] | None = None,
    intel: dict[str, Any] | None = None,
    *,
    lookback_periods: int = 12,
    recent_years: int = 6,
) -> dict[str, Any]:
    """执行全量取证扫描，返回结构化信号、异常波动、证据与覆盖度。

    ``lookback_periods`` 控制财报结论窗口（默认近 12 期 ≈ 3 年），
    ``recent_years`` 控制事件类证据窗口（默认近 6 年）。全历史仅作为波动基线。
    """

    signals: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {"performed": [], "skipped": []}
    ordered = sorted(financials, key=_period_key)

    _scan_financial_series(ordered, signals, anomalies, coverage, lookback_periods)
    _scan_statement_items(statement_items or [], signals, anomalies, coverage, lookback_periods)
    forecast_checks = _scan_forecasts(forecasts or [], ordered, signals, coverage, recent_years)
    regulatory = _scan_regulatory(
        _recent(integrity or [], "record_date", recent_years),
        _recent(announcements or [], "publish_date", recent_years),
        signals,
        coverage,
    )
    capital = _scan_capital_moves(
        _recent(insider_trades or [], "trade_date", recent_years),
        pledges or [],
        _recent(corporate_actions or [], "action_date", recent_years),
        signals,
        coverage,
    )
    price_reactions = _scan_price_reaction(kline or [], announcements or [], ordered, signals, coverage)
    external_views = _scan_external_views(intel, signals, coverage)

    # 趋势聚合 + 统一编号，保证 id 全局唯一且稳定
    signals, merged_count = _consolidate_signals(signals)
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
        "price_reactions": price_reactions,
        "external_views": external_views,
    }
    counts = {level: sum(1 for s in signals if s["severity"] == level) for level in ("high", "medium", "low")}

    return {
        "symbol": symbol,
        "window": {
            "lookback_periods": lookback_periods,
            "recent_years": recent_years,
            "conclusion_from": str(ordered[-lookback_periods].report_date)
            if len(ordered) >= lookback_periods else (str(ordered[0].report_date) if ordered else None),
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
            "risk_level": "high" if counts["high"] >= 2 else ("medium" if counts["high"] or counts["medium"] >= 3 else ("low" if signals else "clear")),
        },
    }
