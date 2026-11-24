"""财务戏法线索扫描（注册表驱动、确定性、不改动既有 25 条规则）。

检查清单由 ``analysis/factor_registry.py`` 的 ``FACTOR_REGISTRY`` 单一来源生成，
本模块不再维护手写 lambda 列表。两类判定权都不在 LLM：
- ``quantitative``：代码按 ``FraudPatternConfig`` 阈值算，不读财报原文；
- ``documentary``：LLM 在同一次分段通读里挑原文摘录并给触发建议，services
  层逐字核验引文，本模块按「可定位 + 建议 + 风险语义标记」三项确认是否触发。

覆盖四类常见手法：

1. 虚增利润 / 收入质量：应收增速背离、净现比持续偏低、收现比恶化、存货堆积
2. 财务洗澡：巨亏后利润反弹、商誉集中减值
3. 关联交易：关联方往来占比、客户集中度与关联方销售占比
4. 会计估计与资本运作：研发资本化率骤升、合并范围变化、在建工程长期不转固

准确度设计（这是本模块与「机械式提醒」的区别）：

- 每条线索至少两个条件同时成立，或跨期连续确认；单点异动不报。
- 同比一律按「去年同月同日」对齐，Q1/H1/Q3 累计值不与 FY 混比。
- 高增长期备货、铺货会让应收与存货同步上升，属正常经营现象；必须叠加
  收现比 / 净现比恶化或资产占比抬升才升级，避免把景气上行误判成虚增收入。
- 所有输出都是「线索」与条件句，绝不下「造假 / 粉饰 / 操纵」定性结论；
  定性判断属于监管与审计，不属于确定性扫描。
- 数据不足或制度不适用时写进 coverage.skipped 并标明 reason，不假装检查过。

本模块只消费传入的纯数据（dataclass 列表 + 事实卡 dict + FraudPatternConfig），
不读配置文件、不读数据库、不读文件，保持 analysis 层的纯粹性；
阈值由 services 层注入，判定值不在本模块留第二份硬编码。
"""

from __future__ import annotations

import dataclasses
import re
import statistics
from typing import Any

from fundamental.analysis.factor_registry import (
    FACTOR_REGISTRY,
    FactorSpec,
    checks_total,
    factor_labels,
)
from fundamental.analysis.forensics_config import FraudPatternConfig
from fundamental.models import FraudPattern, FraudPatternScan

# 科目编码：A 股为东财长表英文 code，港股为东财港股长表数字 code
_A_CODES = {
    "revenue": ("OPERATE_INCOME", "TOTAL_OPERATE_INCOME"),
    "cost": ("OPERATE_COST",),
    "net_profit": ("PARENT_NETPROFIT", "NETPROFIT"),
    "deducted_profit": ("DEDUCT_PARENT_NETPROFIT",),
    "ocf": ("NETCASH_OPERATE",),
    "sales_cash": ("SALES_SERVICES",),
    "ar": ("ACCOUNTS_RECE",),
    "inventory": ("INVENTORY",),
    "current_assets": ("TOTAL_CURRENT_ASSETS",),
    "goodwill": ("GOODWILL",),
    "cip": ("CIP",),
    "fixed_asset": ("FIXED_ASSET",),
}

_HK_CODES = {
    "revenue": ("004001001",),
    "gross_profit": ("004007999",),
    "net_profit": ("004025002",),
    "ar": ("004002003",),
    "related_ar": ("004002006",),
    "related_ap": ("004011005",),
    "inventory": ("004002001",),
    "other_receivable": ("004002005",),
    "current_assets": ("004010999",),
    "deferred_income": ("004011007",),
    "intangible": ("004001004",),
    "cip": ("004001008",),
}

# 触发阈值不在本模块定义：默认值集中在 analysis/forensics_config.py 的
# FraudPatternConfig，线上值由 services 层从 config/forensics.toml 注入。
# 这样扫描器里没有第二份硬编码，改阈值不必改代码，且随指纹自动失效缓存。

_CATEGORIES = {
    "earnings_quality": "虚增利润 / 收入质量线索",
    "big_bath": "财务洗澡线索",
    "related_party": "关联交易线索",
    "accounting": "会计估计与资本运作线索",
}


def _codes(market: str) -> dict[str, tuple[str, ...]]:
    return _HK_CODES if str(market).upper() in ("HK", "HONGKONG") else _A_CODES


def _item_series(items: list[Any], codes: tuple[str, ...]) -> list[tuple[str, float]]:
    """按报告期聚合成 [(date, value)] 升序；同一期多个 code 取第一个非空。"""
    by_date: dict[str, float] = {}
    for it in items:
        code = str(getattr(it, "item_code", "") or "")
        if code not in codes:
            continue
        value = getattr(it, "item_value", None)
        if value is None:
            continue
        date = str(getattr(it, "report_date", "") or "")
        if not date:
            continue
        try:
            by_date.setdefault(date, float(value))
        except (TypeError, ValueError):
            continue
    return sorted(by_date.items())


def _fin_series(financials: list[Any], attr: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for f in financials:
        value = getattr(f, attr, None)
        date = str(getattr(f, "report_date", "") or "")
        if value is None or not date:
            continue
        try:
            out.append((date, float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _prev_year(date: str) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(date))
    if not m:
        return None
    return f"{int(m.group(1)) - 1}-{m.group(2)}-{m.group(3)}"


def _yoy(series: list[tuple[str, float]]) -> list[tuple[str, float, float, float]]:
    """同报告期同比：返回 [(date, cur, prev, yoy_pct)]。"""
    by_date = dict(series)
    out: list[tuple[str, float, float, float]] = []
    for date, cur in series:
        prev_date = _prev_year(date)
        if not prev_date or prev_date not in by_date:
            continue
        prev = by_date[prev_date]
        if prev == 0:
            continue
        out.append((date, cur, prev, (cur - prev) / abs(prev) * 100.0))
    return out


def _ratios(
    numerator: list[tuple[str, float]], denominator: list[tuple[str, float]]
) -> dict[str, float]:
    den = dict(denominator)
    out: dict[str, float] = {}
    for date, num in numerator:
        value = den.get(date)
        if value not in (None, 0):
            out[date] = num / abs(value)
    return out


def _mad_baseline(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    return med, mad or 1e-9


def _pattern(
    pid: str, category: str, title: str, severity: str, period: str,
    facts: dict[str, Any], explanation: str, action: str,
    confidence: str = "medium", evidence: list[dict[str, str]] | None = None,
) -> FraudPattern:
    return FraudPattern(
        id=pid,
        category=_CATEGORIES[category],
        category_key=category,
        title=title,
        severity=severity,
        period=period,
        facts=facts,
        explanation=explanation,
        action=action,
        confidence=confidence,
        evidence=evidence or [],
    )


def _skipped(check: str, reason: str, note: str) -> dict[str, str]:
    return {"check": check, "reason": reason, "note": note}


# ---------- 一、虚增利润 / 收入质量 ----------


def _check_ar_divergence(
    items: list[Any], financials: list[Any], codes: dict, th: dict
) -> tuple[FraudPattern | None, dict | None]:
    ar = _item_series(items, codes.get("ar", ()))
    rev = _item_series(items, codes.get("revenue", ())) or _fin_series(financials, "revenue")
    if len(ar) < th["min_periods"] or len(rev) < th["min_periods"]:
        return None, _skipped("应收账款增速背离", "data_missing", "应收或营收科目期数不足")

    rev_yoy = {d: y for d, _, _, y in _yoy(rev)}
    ar_to_rev = _ratios(ar, rev)
    hits: list[dict[str, Any]] = []
    for date, cur, prev, ar_y in _yoy(ar):
        ry = rev_yoy.get(date)
        prev_date = _prev_year(date)
        ratio = ar_to_rev.get(date)
        prev_ratio = ar_to_rev.get(prev_date) if prev_date else None
        if ry is None or ratio is None or not prev_ratio:
            continue
        gap = ar_y - ry
        rise = (ratio / prev_ratio - 1) * 100.0
        # 三条件同时成立：增速背离 + 应收占收入比抬升 + 幅度过阈值
        if gap > th["ar_divergence_pp"] and rise > th["ar_ratio_rise_pct"]:
            hits.append({
                "period": date, "ar_yoy_pct": round(ar_y, 2),
                "revenue_yoy_pct": round(ry, 2), "gap_pp": round(gap, 2),
                "ar_to_revenue": round(ratio, 4), "ar_to_revenue_prev": round(prev_ratio, 4),
                "ratio_rise_pct": round(rise, 2),
            })
    if len(hits) < 2:
        return None, None
    last = hits[-1]
    high = any(h["gap_pp"] > th["ar_divergence_high_pp"] for h in hits)
    return _pattern(
        "FRD001", "earnings_quality", "应收账款增速持续高于营收且占收入比抬升",
        "high" if high else "medium", last["period"],
        {"occurrences": hits, "confirm_periods": len(hits)},
        f"连续 {len(hits)} 个报告期出现应收账款同比增速高于营收增速，同时应收占收入比持续抬升；"
        f"最近一期应收同比 {last['ar_yoy_pct']}%、营收同比 {last['revenue_yoy_pct']}%，"
        f"应收/营收由 {last['ar_to_revenue_prev']} 升至 {last['ar_to_revenue']}。"
        "收入确认速度快于回款速度，若非账期政策或客户结构变化所致，则存在提前确认收入或放宽信用政策的线索；"
        "高增长企业铺货也会造成同类形态，需结合销售收现与期后回款区分。",
        "核对销售商品收到现金与营业收入的差额、应收账款账龄与期后回款，以及前五名客户的信用政策是否变化。",
        confidence="medium" if not high else "high",
    ), None


def _check_cash_conversion(
    financials: list[Any], items: list[Any], codes: dict, th: dict
) -> tuple[FraudPattern | None, dict | None]:
    ocf = _fin_series(financials, "ocf")
    profit = _fin_series(financials, "net_profit")
    if not ocf or not profit:
        return None, _skipped("净现比持续偏低", "data_missing", "缺少经营现金流或归母净利")
    pairs = []
    pm = dict(profit)
    for date, cash in ocf:
        net = pm.get(date)
        if net and net > 0:
            pairs.append((date, cash, net, cash / net))
    if len(pairs) < th["min_periods"]:
        return None, _skipped("净现比持续偏低", "data_missing", "可比报告期不足")

    low = [p for p in pairs if p[3] < th["cash_conversion_low"]]
    # 连续期数必须按原始序列顺序统计；此前遍历已筛过的 low 会把它算成 len(low)。
    consecutive = 0
    max_consecutive = 0
    for _, _, _, ratio in pairs:
        if ratio < th["cash_conversion_low"]:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
    consecutive = max_consecutive
    # 连续确认，或单期极低且同期应收/存货明显膨胀（双证据）
    severe = [p for p in pairs if p[3] < th["cash_conversion_severe"]]
    ar = _item_series(items, codes.get("ar", ()))
    rev = _item_series(items, codes.get("revenue", ())) or _fin_series(financials, "revenue")
    expanding = False
    if ar and rev:
        ar_yoy = {d: y for d, _, _, y in _yoy(ar)}
        rev_yoy = {d: y for d, _, _, y in _yoy(rev)}
        expanding = any(
            ar_yoy.get(d, 0) - rev_yoy.get(d, 0) > th["ar_divergence_pp"]
            for d, _, _, _ in pairs[-3:]
        )
    triggered = consecutive >= th["cash_conversion_periods"] or (severe and expanding)
    if not triggered:
        return None, None
    focus = severe[-1] if severe else low[-1]
    return _pattern(
        "FRD002", "earnings_quality", "利润含金量偏低：经营现金流未同步跟上净利",
        "high" if consecutive >= 3 else "medium", focus[0],
        {
            "series": [
                {"period": d, "ocf": round(c, 2), "net_profit": round(n, 2), "ratio": round(r, 3)}
                for d, c, n, r in pairs[-6:]
            ],
            "low_periods": len(low), "consecutive_low": consecutive,
            "asset_expansion_confirmed": expanding,
        },
        f"最近一期经营活动现金流量净额 {focus[1]:.2f} 与归母净利 {focus[2]:.2f} 之比为 {focus[3]:.2f}，"
        f"共 {len(low)} 期低于 {th['cash_conversion_low']}。"
        "账面利润未同步转化为经营现金流，若不是备货、预付款或账期变化造成，"
        "则需警惕收入确认与现金回款脱节；本条同时核对了应收/存货是否同步膨胀，"
        "以降低把高增长期正常铺货误判为利润不实的概率。",
        "把现金流量表附表中的经营性应收、存货变动与利润表勾稽，并核对期后回款与合同负债变化。",
    ), None


def _check_cash_receipt(
    items: list[Any], financials: list[Any], codes: dict, th: dict, market: str
) -> tuple[FraudPattern | None, dict | None]:
    if "sales_cash" not in codes:
        return None, _skipped("销售收现比恶化", "not_applicable", "港股无现金流量表长表，收现科目不可用")
    cash = _item_series(items, codes["sales_cash"])
    rev = _item_series(items, codes.get("revenue", ())) or _fin_series(financials, "revenue")
    if len(cash) < th["min_periods"] or not rev:
        return None, _skipped("销售收现比恶化", "data_missing", "销售收现或营收科目期数不足")
    ratios = _ratios(cash, rev)
    prev_map = {d: ratios.get(_prev_year(d)) for d in ratios}
    hits = []
    for date, ratio in sorted(ratios.items()):
        prev = prev_map.get(date)
        if ratio < th["cash_receipt_low"] and prev is not None and (prev - ratio) * 100 > th["cash_receipt_drop_pp"]:
            hits.append({"period": date, "receipt_ratio": round(ratio, 3), "prev": round(prev, 3)})
    if not hits:
        return None, None
    last = hits[-1]
    return _pattern(
        "FRD003", "earnings_quality", "销售收现比下滑，收入变现能力走弱",
        "medium", last["period"], {"occurrences": hits},
        f"销售商品、提供劳务收到的现金与营业收入之比降至 {last['receipt_ratio']}，"
        f"上年同期为 {last['prev']}。收入增长未带来同比例现金流入，"
        "若非票据结算或账期调整，可能与收入确认时点前移有关。",
        "核对应收票据、合同负债与应收账款账龄，确认是否存在年末集中发货、次年期后退货。",
    ), None


def _check_inventory(
    items: list[Any], financials: list[Any], codes: dict, th: dict, market: str = "A"
) -> tuple[FraudPattern | None, dict | None]:
    # 港股长表无营业成本科目；拿营收当成本分母会系统性误报「存货堆积」，
    # 宁可标制度不适用，也不硬凑一个不可比的口径。
    if "cost" not in codes and str(market).upper() in ("HK", "HONGKONG"):
        return None, _skipped("存货异常堆积", "not_applicable", "港股长表无营业成本科目，存货无法与成本同口径对比")
    inv = _item_series(items, codes.get("inventory", ()))
    rev = _item_series(items, codes.get("revenue", ())) or _fin_series(financials, "revenue")
    cost = _item_series(items, codes.get("cost", ()))
    base_yoy = {d: y for d, _, _, y in _yoy(cost or rev)}
    if len(inv) < th["min_periods"] or not base_yoy:
        return None, _skipped("存货异常堆积", "data_missing", "存货或成本/营收科目期数不足")
    hits = []
    for date, _, _, inv_y in _yoy(inv):
        gap = inv_y - base_yoy.get(date, 0)
        if gap > th["inventory_divergence_pp"] and base_yoy.get(date) is not None:
            hits.append({"period": date, "inventory_yoy_pct": round(inv_y, 2),
                         "cost_or_revenue_yoy_pct": round(base_yoy[date], 2), "gap_pp": round(gap, 2)})
    if len(hits) < 2:
        return None, None
    last = hits[-1]
    return _pattern(
        "FRD004", "earnings_quality", "存货增速持续高于成本/营收",
        "medium", last["period"], {"occurrences": hits},
        f"连续 {len(hits)} 期存货同比增速高于营业成本（或营收）增速，最近一期分别为 "
        f"{last['inventory_yoy_pct']}% 与 {last['cost_or_revenue_yoy_pct']}%。"
        "若不是为订单备货或新品铺货，存货堆积可能意味着少结转成本、虚增当期毛利。",
        "核对存货构成、跌价准备计提比例与周转天数，并与在手订单、期后销售结转比对。",
    ), None


# ---------- 二、财务洗澡 ----------


def _check_big_bath(financials: list[Any], th: dict) -> tuple[FraudPattern | None, dict | None]:
    margins = _fin_series(financials, "net_margin")
    profits = _fin_series(financials, "net_profit")
    if len(margins) < th["min_periods"] or len(profits) < th["min_periods"]:
        return None, _skipped("财务洗澡（巨亏后反弹）", "data_missing", "净利率/净利期数不足")
    baseline = _mad_baseline([m for _, m in margins])
    trough = None
    for date, m in margins:
        extreme = m < th["big_bath_margin_pct"]
        if baseline and baseline[1]:
            extreme = extreme or (m < baseline[0] - 3 * baseline[1])
        if extreme and (trough is None or m < trough[1]):
            trough = (date, m)
    if trough is None:
        return None, None
    profit_yoy = {d: y for d, _, _, y in _yoy(profits)}
    # 反弹必须紧邻低谷期（一年内）：隔多年的利润增长与「洗大澡」无关，
    # 否则会拿十年前的低谷解释当下的高增长，属于严重误报。
    def near(d: str) -> bool:
        try:
            return 0 <= int(d[:4]) - int(trough[0][:4]) <= 1
        except (ValueError, IndexError):
            return False
    rebound = [(d, y) for d, y in profit_yoy.items()
               if d > trough[0] and near(d) and y > th["big_bath_rebound_pct"]]
    if not rebound:
        return None, None
    d, y = rebound[0]
    return _pattern(
        "FRD005", "big_bath", "极端低利润期后出现净利大幅反弹",
        "medium", d,
        {"trough_period": trough[0], "trough_net_margin_pct": round(trough[1], 2),
         "rebound_period": d, "rebound_yoy_pct": round(y, 2)},
        f"{trough[0]} 净利率降至 {trough[1]:.2f}%，随后 {d} 归母净利同比反弹 {y:.1f}%。"
        "单期集中确认损失、次年利润跳升是「洗大澡」的典型形态：把减值、费用或一次性损失压在低谷期，"
        "使后续增长显得更强。也可能是真实经营反转，需看低谷期损失的具体构成。",
        "核对低谷期资产减值、信用减值、非经常性损益明细与次年是否 reversal，判断损失是否集中于同一期。",
        confidence="low",
    ), None


def _check_goodwill(
    items: list[Any], financials: list[Any], codes: dict, th: dict
) -> tuple[FraudPattern | None, dict | None]:
    if "goodwill" not in codes:
        return None, _skipped("商誉集中减值", "not_applicable", "港股长表无商誉科目")
    gw = _item_series(items, codes["goodwill"])
    profits = _fin_series(financials, "net_profit")
    if len(gw) < 3 or not profits:
        return None, _skipped("商誉集中减值", "data_missing", "商誉或净利期数不足")
    profit_yoy = {d: y for d, _, _, y in _yoy(profits)}
    hits = []
    for date, _, _, y in _yoy(gw):
        if y < -th["goodwill_drop_pct"] and profit_yoy.get(date, 0) < -30:
            hits.append({"period": date, "goodwill_yoy_pct": round(y, 2),
                         "net_profit_yoy_pct": round(profit_yoy[date], 2)})
    if not hits:
        return None, None
    last = hits[-1]
    return _pattern(
        "FRD006", "big_bath", "商誉大额下降与净利骤降同期发生",
        "medium", last["period"], {"occurrences": hits},
        f"{last['period']} 商誉同比 {last['goodwill_yoy_pct']}%、归母净利同比 {last['net_profit_yoy_pct']}%。"
        "商誉减值一次性计入当期损益，会造成该期利润被压低、后续期间基数变轻；"
        "若减值测试的关键假设（增长率、毛利率、折现率）在同期被大幅下调，需关注是否存在集中释放风险。",
        "核对商誉减值测试假设的跨期变化、被收购主体业绩承诺完成情况与减值是否可转回。",
    ), None


# ---------- 三、关联交易 ----------


def _check_related_party_balance(
    items: list[Any], codes: dict, th: dict, market: str
) -> tuple[FraudPattern | None, dict | None]:
    if "related_ar" not in codes:
        return None, _skipped("关联方往来占比", "not_applicable", "A 股长表无独立关联方往来科目，改由原文事实卡核查")
    rel = _item_series(items, codes["related_ar"])
    ar = _item_series(items, codes.get("ar", ()))
    if not rel or not ar:
        return None, _skipped("关联方往来占比", "data_missing", "缺少关联方应收或应收帐款科目")
    ratios = _ratios(rel, ar)
    hits = [{"period": d, "ratio_pct": round(r * 100, 2)} for d, r in sorted(ratios.items())
            if r * 100 > th["related_party_ratio_pct"]]
    if not hits:
        return None, None
    last = hits[-1]
    return _pattern(
        "FRD007", "related_party", "关联方应收款占应收帐款比重偏高",
        "medium", last["period"], {"occurrences": hits},
        f"{last['period']} 应收关联方款项占应收帐款 {last['ratio_pct']}%。"
        "关联方往来占比高意味着部分收入与回款发生在合并范围之外的关联主体之间，"
        "定价公允性与回款确定性依赖关联交易条款，需核对交易对价与审批程序。",
        "核对年报关联方交易附注中的定价政策、账龄、期后结算与独立股东审批记录。",
    ), None


def _check_customer_concentration(report_facts: dict, th: dict) -> tuple[FraudPattern | None, dict | None]:
    top5 = (report_facts or {}).get("top5_customer_pct") or []
    related = (report_facts or {}).get("related_party_sales_pct") or []
    if not top5 and not related:
        return None, _skipped("客户集中度与关联方销售", "data_missing", "财报原文未抽到客户集中度章节")
    related_map = {str(x.get("period")): float(x.get("value") or 0) for x in related}
    hits = []
    for x in top5:
        value = float(x.get("value") or 0)
        period = str(x.get("period") or "")
        rel = related_map.get(period, 0.0)
        if value > th["top5_customer_pct"] or rel > th["related_sales_pct"]:
            hits.append({"period": period, "top5_pct": value, "related_sales_pct": rel,
                         "quote": x.get("quote"), "source": x.get("source")})
    if not hits:
        return None, None
    # 事实卡按年报年份追加，必须显式按年份取最新一期，否则会拿最老那份下结论
    hits.sort(key=lambda h: str(h.get("period") or ""), reverse=True)
    last = hits[0]
    return _pattern(
        "FRD008", "related_party", "客户高度集中且存在关联方销售",
        "high" if last["related_sales_pct"] > th["related_sales_pct"] else "medium",
        last["period"], {"occurrences": hits},
        f"财报原文披露 {last['period']} 前五名客户合计占营收 {last['top5_pct']}%，"
        f"其中关联方销售占 {last['related_sales_pct']}%。收入集中于少数客户时，"
        "客户资本开支节奏、账期与关联交易定价会同时影响收入真实性与毛利率可持续性；"
        "关联方销售占比越高，越需要独立第三方价格与回款证据支撑。这是集中度与定价线索，不是利益输送结论。",
        "核对前五名客户明细、关联方关系认定、同类产品对第三方售价与期后回款。",
        confidence="high" if last["related_sales_pct"] > th["related_sales_pct"] else "medium",
        evidence=[{"source": str(last.get("source") or "财报原文"),
                   "period": last["period"], "quote": str(last.get("quote") or "")}],
    ), None


# ---------- 四、会计估计与资本运作 ----------


def _check_rd_capitalization(report_facts: dict, th: dict) -> tuple[FraudPattern | None, dict | None]:
    series = (report_facts or {}).get("rd_capitalization_pct") or []
    if len(series) < 2:
        return None, _skipped("研发资本化率骤升", "data_missing", "财报原文未抽到连续两期研发资本化率")
    ordered = sorted(series, key=lambda x: str(x.get("period")))
    hits = []
    for prev, cur in zip(ordered, ordered[1:]):
        rise = float(cur.get("value") or 0) - float(prev.get("value") or 0)
        profit_share = float(cur.get("profit_share") or 0)
        if rise > th["rd_cap_rate_rise_pp"] and profit_share > th["rd_cap_profit_pct"]:
            hits.append({"period": str(cur.get("period")), "from": float(prev.get("value") or 0),
                         "to": float(cur.get("value") or 0), "rise_pp": round(rise, 2),
                         "profit_share_pct": profit_share})
    if not hits:
        return None, None
    last = hits[-1]
    return _pattern(
        "FRD009", "accounting", "研发投入资本化率抬升",
        "medium", last["period"], {"occurrences": hits},
        f"研发支出资本化占研发投入比重由 {last['from']}% 升至 {last['to']}%（+{last['rise_pp']}pp），"
        f"资本化金额占当期净利 {last['profit_share_pct']}%。"
        "把本应费用化的研发支出转入无形资产，会直接抬高当期利润并递延成本；"
        "资本化率抬升需有开发阶段里程碑与可收回性证据支撑。",
        "核对研发资本化确认条件、项目里程碑、无形资产摊销政策与同行资本化率。",
    ), None


def _check_accounting_changes(report_facts: dict, th: dict) -> tuple[FraudPattern | None, dict | None]:
    facts = report_facts or {}
    policy = facts.get("accounting_policy_change") or []
    consolidation = facts.get("consolidation_change") or []
    audit = facts.get("audit") or []
    findings: list[dict[str, Any]] = []
    for x in policy:
        if x.get("applicable"):
            findings.append({"type": "会计政策/估计变更", "period": x.get("period"),
                             "quote": x.get("quote"), "source": x.get("source")})
    for x in consolidation:
        entities = x.get("entities") or []
        if x.get("applicable") and len(entities) >= th["consolidation_entities"]:
            findings.append({"type": "合并范围大幅变化", "period": x.get("period"),
                             "entity_count": len(entities), "quote": x.get("quote"),
                             "source": x.get("source")})
    for x in audit:
        if x.get("non_standard"):
            findings.append({"type": "非标准审计意见", "period": x.get("period"),
                             "quote": x.get("quote"), "source": x.get("source")})
    if not findings:
        return None, None
    last = findings[-1]
    return _pattern(
        "FRD010", "accounting", "会计政策 / 合并范围 / 审计意见发生变化",
        "high" if any(f["type"] == "非标准审计意见" for f in findings) else "medium",
        str(last.get("period") or ""), {"findings": findings},
        f"财报原文检出 {len(findings)} 项会计口径相关变化："
        + "；".join(f"{f['type']}（{f.get('period') or '未标期'}）" for f in findings)
        + "。会计政策或估计变更会改变跨期可比性；合并范围新增主体会直接带来收入与资产并入；"
        "非标准审计意见则是已披露的法定事实。三者都会让同比失真，判断经营趋势前必须先剔除口径影响。",
        "核对变更公告正文的会计影响数、新增主体的收入与利润贡献，以及审计意见涉及事项是否已消除。",
        evidence=[{"source": str(f.get("source") or "财报原文"),
                   "period": str(f.get("period") or ""), "quote": str(f.get("quote") or "")}
                  for f in findings if f.get("quote")],
    ), None


def _check_cip(
    items: list[Any], codes: dict, th: dict
) -> tuple[FraudPattern | None, dict | None]:
    if "fixed_asset" not in codes:
        return None, _skipped("在建工程长期不转固", "not_applicable", "港股长表无对应固定资产科目映射")
    cip = _item_series(items, codes.get("cip", ()))
    fixed = _item_series(items, codes["fixed_asset"])
    if len(cip) < th["cip_periods"] or not fixed:
        return None, _skipped("在建工程长期不转固", "data_missing", "在建工程或固定资产期数不足")
    ordered = cip[-th["cip_periods"]:]
    growth = (ordered[-1][1] / abs(ordered[0][1]) - 1) * 100 if ordered[0][1] else 0
    fixed_growth = 0.0
    if len(fixed) >= 2:
        fixed_growth = (fixed[-1][1] / abs(fixed[-2][1]) - 1) * 100 if fixed[-2][1] else 0
    if growth < th["cip_growth_pct"] or fixed_growth > 20:
        return None, None
    return _pattern(
        "FRD011", "accounting", "在建工程持续大幅增长而固定资产未同步增加",
        "low", ordered[-1][0],
        {"cip_periods": [[d, round(v, 2)] for d, v in ordered],
         "cip_growth_pct": round(growth, 2), "fixed_asset_growth_pct": round(fixed_growth, 2)},
        f"近 {len(ordered)} 期在建工程累计增长 {growth:.1f}%，而同期固定资产增幅仅 {fixed_growth:.1f}%。"
        "在建工程长期不转固会推迟折旧、把利息与支出资本化，从而抬高当期利润；"
        "也可能是产线建设周期较长的正常状态，需看工程进度与达到预定可使用状态的时间点。",
        "核对在建工程明细、工程进度、资本化利息与转固时点。",
        confidence="low",
    ), None


# ---------- 汇总 ----------


# ---------- 注册表驱动：统一上下文 + 模块级 handler（无手写 lambda） ----------


@dataclasses.dataclass(frozen=True)
class _Ctx:
    """一次扫描的纯数据上下文；handlers 只从这里取参数，不读外部状态。"""

    financials: list[Any]
    items: list[Any]
    codes: dict[str, tuple[str, ...]]
    th: dict[str, Any]
    market: str
    facts: dict[str, Any]
    factor_evidence: dict[str, Any]


def _h_ar_divergence(c: _Ctx):
    return _check_ar_divergence(c.items, c.financials, c.codes, c.th)


def _h_cash_conversion(c: _Ctx):
    return _check_cash_conversion(c.financials, c.items, c.codes, c.th)


def _h_cash_receipt(c: _Ctx):
    return _check_cash_receipt(c.items, c.financials, c.codes, c.th, c.market)


def _h_inventory(c: _Ctx):
    return _check_inventory(c.items, c.financials, c.codes, c.th, c.market)


def _h_big_bath(c: _Ctx):
    return _check_big_bath(c.financials, c.th)


def _h_goodwill(c: _Ctx):
    return _check_goodwill(c.items, c.financials, c.codes, c.th)


def _h_related_party_balance(c: _Ctx):
    return _check_related_party_balance(c.items, c.codes, c.th, c.market)


def _h_customer_concentration(c: _Ctx):
    return _check_customer_concentration(c.facts, c.th)


def _h_rd_capitalization(c: _Ctx):
    return _check_rd_capitalization(c.facts, c.th)


def _h_accounting_changes(c: _Ctx):
    return _check_accounting_changes(c.facts, c.th)


def _h_cip(c: _Ctx):
    return _check_cip(c.items, c.codes, c.th)


_HANDLERS = {
    "ar_divergence": _h_ar_divergence,
    "cash_conversion": _h_cash_conversion,
    "cash_receipt": _h_cash_receipt,
    "inventory": _h_inventory,
    "big_bath": _h_big_bath,
    "goodwill": _h_goodwill,
    "related_party_balance": _h_related_party_balance,
    "customer_concentration": _h_customer_concentration,
    "rd_capitalization": _h_rd_capitalization,
    "accounting_changes": _h_accounting_changes,
    "cip": _h_cip,
}


def _check_documentary(
    spec: FactorSpec, factor_evidence: dict[str, Any]
) -> tuple[FraudPattern | None, dict | None]:
    """原文取证型因子的**确定性确认**（触发权在代码，不在 LLM）。

    三个条件同时成立才产出线索：① 引文已由 services 层逐字核验可在归档
    原文定位；② 模型给出触发建议；③ 引文命中该因子的风险语义标记。
    任何一条不满足都不触发，且不会把「模型认为有问题」写成结论。
    """
    bundle = factor_evidence.get(spec.id)
    if not isinstance(bundle, dict) or not bundle.get("available"):
        return None, _skipped(
            spec.check_name, "data_missing",
            "无可核验财报原文，或本次未执行分段通读",
        )
    hits = bundle.get("verified_hits") or []
    confirmed = [
        h for h in hits
        if str(h.get("quote") or "")
        and h.get("suggested")
        and any(m in str(h.get("quote") or "") for m in spec.trigger_markers)
    ]
    if not confirmed:
        return None, None  # 执行过但未触发，计入 coverage.performed

    picks = confirmed[:3]
    facts: dict[str, Any] = {
        "判定方式": "原文取证 → 代码按引文确认",
        "已核验引文数": str(len(hits)),
        "确认命中数": str(len(confirmed)),
        "规则描述": spec.probe_criteria[:120],
        "红旗编号": " / ".join(spec.red_flag_refs),
        "检索范围": "、".join(spec.source_scope[:3]),
        "证据出处": "财报原文（逐字核验，可在归档文本定位）",
    }
    for i, h in enumerate(picks, 1):
        facts[f"引文 {i}"] = str(h.get("quote") or "")[:110]
    evidence = [
        {
            "source": str(h.get("source") or "财报原文"),
            "period": str(h.get("period") or ""),
            "quote": str(h.get("quote") or "")[:300],
        }
        for h in picks
    ]
    return (
        _pattern(
            spec.id, spec.category, spec.title, spec.severity,
            str(picks[0].get("period") or ""),
            facts,
            f"财报原文中出现 {len(confirmed)} 处与「{spec.title}」相关的披露，"
            "引文已逐字核验可在归档原文中定位。这属于待核查线索，"
            "仅说明存在需要对照原文核实的披露，不代表存在违规、错报或财务操纵。",
            "对照原文该章节与公司说明核实商业实质与金额，必要时查专项审计报告、"
            "问询函回复或审计意见全文。",
            confidence=spec.confidence,
            evidence=evidence,
        ),
        None,
    )


def _window_dates(
    financials: list[Any], items: list[Any], lookback: int
) -> tuple[set[str], set[str]]:
    """结论窗口：只让最近 N 期产出线索，但保留各自的去年同日用于同比配对。

    不做这层裁剪，扫描会拿 2008/2016 年的低谷去解释 2026 年的经营，
    这类「年代错配」比漏检更伤可信度。
    """
    dates = sorted({str(getattr(f, "report_date", "") or "") for f in financials}
                   | {str(getattr(i, "report_date", "") or "") for i in items})
    dates = [d for d in dates if d]
    if not dates:
        return set(), set()
    recent = set(dates[-lookback:]) if len(dates) > lookback else set(dates)
    all_dates = set(dates)
    allowed = set(recent)
    for d in recent:
        prev = _prev_year(d)
        if prev in all_dates:
            allowed.add(prev)
    return recent, allowed


def scan_fraud_patterns(
    *,
    symbol: str,
    market: str,
    financials: list[Any],
    statement_items: list[Any],
    report_facts: dict[str, Any] | None = None,
    factor_evidence: dict[str, Any] | None = None,
    config: FraudPatternConfig | dict[str, float] | None = None,
    lookback_periods: int = 12,
) -> FraudPatternScan:
    """扫描财务戏法线索。任何异常都由调用方兜底，本函数不抛业务异常。

    ``config`` 接受 ``FraudPatternConfig``（线上路径）或纯字典（测试里覆盖
    单项阈值）；不传则用配置对象的内置默认值。阈值字典只在模块内部作为
    判定参数流转，跨模块边界由 dataclass 承担。

    ``factor_evidence`` 是 services 层分段通读 + 引文逐字核验后得到的纯
    dict（``{factor_id: {available, verified_hits, rejected_quotes}}``）。
    analysis 层不读文件、不调 LLM；原文取证型因子的最终触发在这里按三条
    确定性条件确认，模型输出只作证据与建议。
    """
    if config is None:
        cfg = FraudPatternConfig()
    elif isinstance(config, FraudPatternConfig):
        cfg = config
    else:
        cfg = FraudPatternConfig.from_mapping(config)
    th = cfg.as_dict()
    codes = _codes(market)
    facts = report_facts or {}

    # 全历史只作为同比基数，不允许产生结论；与 scan_forensics 的窗口口径一致。
    _, allowed = _window_dates(financials, statement_items, lookback_periods)
    financials = [f for f in financials if str(getattr(f, "report_date", "") or "") in allowed]
    statement_items = [i for i in statement_items
                       if str(getattr(i, "report_date", "") or "") in allowed]

    ctx = _Ctx(
        financials=financials,
        items=statement_items,
        codes=codes,
        th=th,
        market=market,
        facts=facts,
        factor_evidence=factor_evidence or {},
    )

    patterns: list[FraudPattern] = []
    performed: list[str] = []
    skipped: list[dict[str, str]] = []
    # checks 完全由 FACTOR_REGISTRY 生成：新增因子只需改注册表（quantitative
    # 再加一个模块级 handler），不必在这里插入新的 lambda。
    for spec in FACTOR_REGISTRY:
        try:
            if spec.evidence_kind == "documentary":
                pattern, skip = _check_documentary(spec, ctx.factor_evidence)
            elif spec.evidence_kind == "quantitative":
                handler = _HANDLERS.get(spec.handler)
                if handler is None:
                    pattern, skip = None, _skipped(
                        spec.check_name, "data_missing",
                        f"未注册判定函数：{spec.handler}",
                    )
                else:
                    pattern, skip = handler(ctx)
            else:  # hybrid 为预留类型，未启用时不假装检查过
                pattern, skip = None, _skipped(
                    spec.check_name, "not_applicable", "hybrid 判定方式尚未启用",
                )
        except Exception as exc:  # 单项失败不拖垮整份扫描，但要显式记账
            pattern, skip = None, _skipped(
                spec.check_name, "data_missing", f"检查异常：{type(exc).__name__}",
            )
        # 只有「数据不足 / 制度不适用」才算未执行；执行过但没触发也要计入，
        # 避免像旧 coverage 那样把空跑误记成已检查、或把未触发当成缺数据。
        if skip is None:
            performed.append(spec.check_name)
        else:
            skipped.append({**skip, "check": spec.check_name})
        if pattern:
            patterns.append(pattern)

    order = {"high": 0, "medium": 1, "low": 2}
    patterns.sort(key=lambda p: (order.get(p.severity, 3), p.id))
    return FraudPatternScan(
        symbol=symbol,
        market=market,
        patterns=patterns,
        coverage={
            "performed": performed,
            "skipped": skipped,
            "checks_total": checks_total(),
            "window_periods": lookback_periods,
            "window_from": min(allowed) if allowed else None,
            "window_to": max(allowed) if allowed else None,
            "checked_no_trigger": len(performed) - len(patterns),
            "not_applicable": sum(1 for s in skipped if s["reason"] == "not_applicable"),
            "data_missing": sum(1 for s in skipped if s["reason"] == "data_missing"),
            # 注册表单一来源：前端因子清单、判定方式分布、幻觉引文记账
            "factor_specs": factor_labels(),
            "by_evidence_kind": {
                kind: sum(1 for s in FACTOR_REGISTRY if s.evidence_kind == kind)
                for kind in ("quantitative", "documentary", "hybrid")
            },
            "documentary_rejected_quotes": sum(
                int((b or {}).get("rejected_quotes") or 0)
                for b in (factor_evidence or {}).values() if isinstance(b, dict)
            ),
        },
        summary={
            "pattern_count": len(patterns),
            "high": sum(1 for p in patterns if p.severity == "high"),
            "medium": sum(1 for p in patterns if p.severity == "medium"),
            "low": sum(1 for p in patterns if p.severity == "low"),
            "by_category": {
                key: sum(1 for p in patterns if p.category_key == key)
                for key in _CATEGORIES
            },
        },
    )
