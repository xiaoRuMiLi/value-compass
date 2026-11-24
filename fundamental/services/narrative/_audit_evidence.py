"""财报原文的「审计视角 / 会计口径 / 资金动向 / 反证」事实层。

存在意义：规则扫描只吃结构化财务数据，年报里最关键的几类披露——审计意见与
关键审计事项、分季度数据与已披露季报的重大差异、关联财务公司存款、分红相对
当年盈利的覆盖、内控与合规反证——都是**文字与表格**，进不了任何确定性检查，
只能指望 LLM 逐段通读碰运气。实测五粮液 2025 年报里「调整 2025 年部分业务
收入确认相关核算」「货币资金存在性被列为关键审计事项」「关联财务公司存款
479.77 亿元」三条硬事实全部漏掉，而「年度资金占用专项审计报告」这类例行
合规披露反被当成高严重度风险写进结论。

本模块只做**章节定位 + 数字抽取 + 原文摘录**，不下审计结论、不做造假定性；
抽不到就写进 ``missing``，绝不猜测或补数。判定阈值来自
``[forensics.audit_evidence]``，与项目其余阈值一样配置化。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_garbled_text

from ._evidence import _load_documents, _text_path, parse_code

_logger = get_logger(__name__)

_ANNUAL = "annual_report"
_SEMI = "semi_report"


def _clean(text: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _quote(text: str, start: int, end: int, limit: int = 240) -> str:
    return _clean(text[max(0, start): max(0, end)], limit)


def _section(text: str, begin: str, end: str, span: int = 3000) -> str:
    """按章节表头切一段原文；找不到结尾时取固定长度。"""
    i = text.find(begin)
    if i < 0:
        return ""
    j = text.find(end, i + len(begin))
    return text[i: (j if 0 < j - i <= span else i + span)]


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _checked_yes(seg: str) -> bool | None:
    """从「☑是 □否 / □是 ☑否」判定是否勾选了「是」。

    PDF 抽取的勾选框常落在私用区（U+F052 / U+F0A3），因此看「是」前面的
    字符是不是空框 ``□``；判不出来返回 None，绝不猜。
    """
    m = re.search(r"(\S)\s*是\s*(\S)\s*否", seg)
    if not m:
        return None
    return m.group(1) != "□"


# ---------- 各事实抽取器：入参是单份年报/中报全文 ----------

def _audit_opinion(text: str, period: str, source: str) -> list[dict[str, Any]]:
    section = _section(text, "一、审计报告", "第八节", 2200)
    pool = section or text[:6000]
    m = re.search(
        r"审计意见类型\s*(标准的无保留意见|带强调事项段的无保留意见|"
        r"无保留意见|保留意见|否定意见|无法表示意见)", pool, re.S
    )
    if not m:
        return []
    firm = re.search(r"审计机构名称\s*([^\n]{2,40})", pool, re.S)
    sign = re.search(r"审计报告签署日期\s*([\d\s年月日]+)", pool, re.S)
    return [{
        "period": period,
        "opinion": _clean(m.group(1), 30),
        "non_standard": "无保留" not in m.group(1),
        "firm": _clean(firm.group(1), 40) if firm else "",
        "signed_date": _clean(sign.group(1), 24) if sign else "",
        "source": source,
        "quote": _quote(pool, m.start(), m.start() + 90),
    }]


def _key_audit_matters(text: str, period: str, source: str) -> list[dict[str, Any]]:
    section = _section(text, "三、关键审计事项", "四、其他信息", 6000)
    if not section:
        return []
    titles = re.findall(r"（[一二三四五六七八九十]+）\s*([^\n]{3,40})", section)
    items = []
    for title in titles[:6]:
        t = _clean(title, 40)
        if not t or "审计" in t and "事项" in t and len(t) < 6:
            continue
        pos = section.find(t)
        items.append({
            "title": t,
            "quote": _quote(section, max(0, pos), max(0, pos) + 220, 220),
        })
    if not items:
        return []
    return [{
        "period": period,
        "count": len(items),
        "items": items,
        "source": source,
        "quote": _clean(section[:260], 260),
    }]


def _quarterly_discrepancy(text: str, period: str, source: str) -> list[dict[str, Any]]:
    key = "上述财务指标或其加总数是否与公司已披露季度报告"
    i = text.find(key)
    if i < 0:
        return []
    seg = text[i: i + 500]
    applicable = _checked_yes(seg)
    reason = ""
    rm = re.search(r"公司对\s*\d{4}\s*年[^\n]{0,160}", seg)
    if rm:
        reason = _clean(rm.group(0), 180)
    return [{
        "period": period,
        "applicable": applicable,
        "reason": reason,
        "source": source,
        "quote": _clean(seg[:220], 220),
    }]


def _accounting_change(text: str, period: str, source: str) -> list[dict[str, Any]]:
    key = "会计政策、会计估计变更或重大会计差错更正"
    i = text.find(key)
    if i < 0:
        return []
    seg = text[i: i + 320]
    # 「□适用 ☑不适用」= 不适用；「☑适用 □不适用」= 适用
    m = re.search(r"(\S)\s*适用\s*(\S)\s*不适用", seg)
    applicable = None if not m else m.group(1) != "□"
    return [{
        "period": period,
        "applicable": applicable,
        "source": source,
        "quote": _clean(seg[:200], 200),
    }]


def _finance_company_deposit(text: str, period: str, source: str) -> list[dict[str, Any]]:
    section = _section(text, "与存在关联关系的财务公司的往来情况", "公司控股的财务公司", 2600)
    if not section:
        return []
    # 表格：每日最高存款限额 / 利率范围 / 期初 / 存入 / 取出 / 期末（单位万元）
    m = re.search(
        r"财务公司\s*联营企业\s*([\d,]+)\s*([\d.%～~\-]+)\s*"
        r"([\d,]+\.\d{2})\s*([\d,]+\.\d{2})\s*([\d,]+\.\d{2})\s*([\d,]+\.\d{2})",
        section, re.S,
    )
    # 货币资金规模：优先「资产构成重大变动情况」表（单位元），
    # 退化到关键审计事项里的「货\n币资金余额为人民币…万元」（PDF 常把「货币」拆行）。
    cash = re.search(r"货币资金\s*([\d,]+\.\d{2})\s*([\d.]+)\s*%", text, re.S)
    if cash:
        cash_yi = _num(cash.group(1)) / 1e8
    else:
        cash = re.search(r"货\s*币资金余额为人民币\s*([\d,]+(?:\.\d+)?)\s*万元", text, re.S)
        cash_yi = (_num(cash.group(1)) / 10000.0) if cash else None  # 万元 → 亿元
    balance_yi = (_num(m.group(6)) / 10000.0) if m else None
    limit_yi = (_num(m.group(1)) / 10000.0) if m else None
    share = (balance_yi / cash_yi * 100) if (balance_yi and cash_yi) else None
    if balance_yi is None:
        return []
    return [{
        "period": period,
        "balance_yi": round(balance_yi, 2),
        "limit_yi": round(limit_yi, 2) if limit_yi else None,
        "rate_range": _clean(m.group(2), 30) if m else "",
        "opening_yi": round(_num(m.group(3)) / 10000.0, 2) if m else None,
        "cash_yi": round(cash_yi, 2) if cash_yi else None,
        "share_of_cash_pct": round(share, 2) if share else None,
        "source": source,
        "quote": _clean(section[:320], 320),
    }]


def _restricted_assets(text: str, period: str, source: str) -> list[dict[str, Any]]:
    section = _section(text, "截至报告期末的资产权利受限情况", "七、投资状况分析", 1600)
    m = re.search(r"合\s*计\s*([\d,]+\.\d{2})", section, re.S) if section else None
    if not m:
        return []
    return [{
        "period": period,
        "amount_yi": round(_num(m.group(1)) / 1e8, 2),
        "source": source,
        "quote": _clean(section[:260], 260),
    }]


def _dividend_coverage(text: str, period: str, source: str) -> list[dict[str, Any]]:
    # 只认「累计实施现金分红 X 亿元」这类明确口径。
    # 「应付普通股股利」是资产负债表科目，与当年实际派现不是一回事，
    # 混进来会把中报/历史年份的分红覆盖率算错，宁可留空也不猜。
    div = re.search(r"累计实施现金分红\s*([\d.]+)\s*亿元", text)
    if not div:
        return []
    dividend_yi = _num(div.group(1))
    profit = re.search(
        r"归属于上市公司股东的\s*净利润（元）\s*([\d,]+\.\d{2})", text, re.S
    )
    if not profit:
        profit = re.search(r"实现归属于上市公司股东的净利润\s*([\d.]+)\s*亿元", text)
        profit_yi = _num(profit.group(1)) if profit else None
    else:
        profit_yi = (_num(profit.group(1)) / 1e8) if profit else None
    if not dividend_yi or not profit_yi:
        return []
    return [{
        "period": period,
        "cash_dividend_yi": round(dividend_yi, 2),
        "net_profit_yi": round(profit_yi, 2),
        "payout_ratio_pct": round(dividend_yi / profit_yi * 100, 2),
        "source": source,
        "quote": _quote(text, div.start(), div.start() + 110, 160),
    }]


def _internal_control(text: str, period: str, source: str) -> list[dict[str, Any]]:
    opinion = re.search(r"内控审计报告意见类型\s*([^\n]{2,24})", text, re.S)
    major = re.search(r"财务报告重大缺陷数量（个）\s*(\d+)", text, re.S)
    important = re.search(r"财务报告重要缺陷数量（个）\s*(\d+)", text, re.S)
    if not (opinion or major):
        return []
    return [{
        "period": period,
        "opinion": _clean(opinion.group(1), 24) if opinion else "",
        "major_defects": int(major.group(1)) if major else None,
        "important_defects": int(important.group(1)) if important else None,
        "source": source,
        "quote": _quote(text, (opinion or major).start(), (opinion or major).start() + 160, 180),
    }]


_COUNTER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("无非经营性资金占用", r"公司报告期不存在控股股东及其他关联方对上市公司的非经营性占用资金[^\n]{0,30}"),
    ("无违规对外担保", r"公司报告期无违规对外担保情况[^\n]{0,20}"),
    ("无重大诉讼仲裁", r"(?:本报告期|报告期)公司无重大诉讼、仲裁事项[^\n]{0,20}"),
    ("诚信状况良好", r"公司及其控股股东、实际控制人诚信状况良好[^\n]{0,20}"),
    ("内控有效", r"在所有重大方面保持了有效的财务报告内部控制[^\n]{0,20}"),
    ("信披考核最高等级", r"连续\s*\d+\s*年荣获深圳证券交易所信息披露考核最高等级[^\n]{0,20}"),
    ("大股东增持", r"累计增持公司股份\s*[\d,]+\s*股[^\n]{0,120}"),
    ("研发投入未资本化", r"研发投入资本化的金额（元）\s*0\.00"),
)


def _counter_evidence(text: str, period: str, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, pattern in _COUNTER_PATTERNS:
        m = re.search(pattern, text, re.S)
        if not m:
            continue
        out.append({
            "period": period,
            "item": label,
            "source": source,
            "quote": _clean(m.group(0), 180),
        })
    return out


# 「公司实物销售收入是否大于劳务收入」表：销售量 / 生产量 / 库存量三行，
# 每行依次是本期数、上年数、同比增减。比财务比率更早反映渠道动销与压货。
_VOLUME_ROWS = {
    key: re.compile(label + r"\s*吨\s*([\d,]+)\s*([\d,]+)\s*(-?[\d.]+)\s*%", re.S)
    for key, label in (("sales", "销售量"), ("production", "生产量"),
                       ("inventory", "库存量"))
}


def _consolidation_scope(text: str, period: str, source: str) -> list[dict[str, Any]]:
    """「报告期内合并范围是否发生变动」勾选 + 新设/收购主体名称。

    合并范围新增主体会直接把收入与资产并入报表，是同比可比性的硬事实；
    旧事实卡只认「新纳入合并范围的公司有：」一种措辞，五粮液这类
    「出资设立…有限公司」的写法抽不到，故单独按这一章节定位。
    """
    key = "报告期内合并范围是否发生变动"
    i = text.find(key)
    if i < 0:
        return []
    seg = text[i: i + 420]
    checked = re.search(r"(\S)\s*是\s*(\S)\s*否", seg)
    applicable = (checked.group(1) != "□") if checked else None
    tail = _clean(seg[checked.end():], 240) if checked else ""
    # 只保留本段的原因说明，不把下一节标题带进来
    reason = re.split(r"（\d+）", tail)[0].strip()
    compact = re.sub(r"\s+", "", reason)
    entities = re.findall(
        r"(?:设立|投资|新设|收购)([\u4e00-\u9fa5]{2,20}"
        r"(?:股份有限公司|有限责任公司|有限公司))", compact
    )
    if not entities:
        entities = re.findall(
            r"[\u4e00-\u9fa5]{2,20}(?:股份有限公司|有限责任公司|有限公司)", compact
        )
    entities = list(dict.fromkeys(entities))[:8]
    return [{
        "period": period,
        "applicable": applicable,
        "entities": entities,
        "entity_count": len(entities),
        "reason": reason,
        "source": source,
        "quote": _clean(seg[:220], 220),
    }]


def _operating_volume(text: str, period: str, source: str) -> list[dict[str, Any]]:
    """实物经营量：白酒等行业的销量/库存/产能比财务比率更早反映渠道压货。"""
    section = _section(text, "主要产品生产、销售和库存量", "2、成品酒、半成品酒", 2200)
    volume_table = _section(
        text, "公司实物销售收入是否大于劳务收入",
        "（4）公司已签订的重大销售合同", 1600,
    )
    capacity = re.search(
        r"设计年产能（万吨）\s*全年实际产能（万吨）\s*在建产能（万吨）\s*"
        r"酒类\s*([\d.]+)\s*([\d.]+)\s*([\d.]+)", text, re.S
    )
    # 经销商净变化只在「经销商数量情况」章节里取；全文搜第一个「小计」
    # 会把别的表格合计行串进来，成为虚假的渠道收缩证据。
    dealer_section = _section(
        text, "经销商数量情况", "经销客户的主要结算方式", 1500
    )
    dealers = (
        re.search(r"小计\s*([\d,]+)\s*(-?\d+)", dealer_section, re.S)
        if dealer_section else None
    )
    parsed = {key: pattern.search(volume_table) for key, pattern in _VOLUME_ROWS.items()}
    if not section and not capacity and not any(parsed.values()):
        return []
    util = None
    if capacity:
        design, actual = _num(capacity.group(1)), _num(capacity.group(2))
        util = round(actual / design * 100, 2) if design and actual else None

    def row(key: str, group: int) -> float | None:
        m = parsed[key]
        return _num(m.group(group)) if m else None

    return [{
        "period": period,
        "design_capacity_wan_ton": _num(capacity.group(1)) if capacity else None,
        "actual_capacity_wan_ton": _num(capacity.group(2)) if capacity else None,
        "capacity_utilization_pct": util,
        "in_build_capacity_wan_ton": _num(capacity.group(3)) if capacity else None,
        "dealer_count": _num(dealers.group(1)) if dealers else None,
        "dealer_change": _num(dealers.group(2)) if dealers else None,
        "sales_volume_ton": row("sales", 1),
        "sales_volume_prev_ton": row("sales", 2),
        "sales_volume_yoy_pct": row("sales", 3),
        "production_volume_ton": row("production", 1),
        "production_volume_prev_ton": row("production", 2),
        "production_volume_yoy_pct": row("production", 3),
        "inventory_volume_ton": row("inventory", 1),
        "inventory_volume_prev_ton": row("inventory", 2),
        "inventory_volume_yoy_pct": row("inventory", 3),
        "source": source,
        "quote": _clean(section[:420], 420),
        "quote_volume": _clean(volume_table[:420], 420),
    }]


_EXTRACTORS = (
    ("audit_opinion", _audit_opinion),
    ("key_audit_matters", _key_audit_matters),
    ("quarterly_discrepancy", _quarterly_discrepancy),
    ("accounting_change", _accounting_change),
    ("finance_company_deposit", _finance_company_deposit),
    ("restricted_assets", _restricted_assets),
    ("dividend_coverage", _dividend_coverage),
    ("internal_control", _internal_control),
    ("counter_evidence", _counter_evidence),
    ("operating_volume", _operating_volume),
    ("consolidation_scope", _consolidation_scope),
)


def _alerts(result: dict[str, Any], cfg: dict[str, float]) -> list[dict[str, Any]]:
    """由已抽取的硬事实生成确定性提示；不含任何模型推断。"""
    out: list[dict[str, Any]] = []

    for q in result["quarterly_discrepancy"]:
        if q.get("applicable"):
            out.append({
                "level": "high",
                "title": "年报分季度数据与已披露季报/半年报存在重大差异",
                "detail": (
                    f"{q['source']}明确勾选「是」，原因：{(q.get('reason') or '见原文摘录').rstrip('。')}。"
                    "营收同比降幅可能包含收入确认口径调整，跨期可比性需先还原口径再判断经营变化。"
                ),
                "quote": q.get("quote", ""),
                "source": q.get("source", ""),
            })

    opinion = next((o for o in result["audit_opinion"] if not o.get("non_standard")), None)
    kam = result["key_audit_matters"][0] if result["key_audit_matters"] else None
    if opinion and kam:
        out.append({
            "level": "info",
            "title": "审计师关注事项",
            "detail": (
                f"{opinion['source']}审计意见为「{opinion['opinion']}」，"
                f"关键审计事项 {kam['count']} 项："
                + "、".join(i["title"] for i in kam["items"])
                + "。关键审计事项是审计师认为最重要的领域，不等于存在错报。"
            ),
            "quote": kam.get("quote", ""),
            "source": opinion.get("source", ""),
        })

    for d in result["finance_company_deposit"]:
        share = d.get("share_of_cash_pct")
        if share is not None and share >= cfg["deposit_share_pct"]:
            out.append({
                "level": "medium",
                "title": "大额资金存放于关联财务公司",
                "detail": (
                    f"{d['source']}期末在关联财务公司存款 {d['balance_yi']} 亿元，"
                    f"占货币资金 {share}%（货币资金 {d.get('cash_yi')} 亿元），"
                    f"每日最高限额 {d.get('limit_yi')} 亿元、利率 {d.get('rate_range')}。"
                    "需核对存款利率公允性、资金可自由支配程度与关联方资金往来。"
                ),
                "quote": d.get("quote", ""),
                "source": d.get("source", ""),
            })

    for p in result["dividend_coverage"]:
        ratio = p.get("payout_ratio_pct")
        if ratio is not None and ratio >= cfg["payout_alert_pct"]:
            out.append({
                "level": "medium",
                "title": "现金分红超出当年归母净利",
                "detail": (
                    f"{p['source']}现金分红 {p['cash_dividend_yi']} 亿元，"
                    f"归母净利 {p['net_profit_yi']} 亿元，派现/净利 {ratio}%。"
                    "按绝对金额可能未偏离历史，但盈利覆盖能力已明显变化，"
                    "需核对分红资金来源、账上现金储备与后续资本开支安排。"
                ),
                "quote": p.get("quote", ""),
                "source": p.get("source", ""),
            })

    # 多期年报会给出同一套产能结论，只保留最新一份，避免重复的年度结构性提示
    for v in result["operating_volume"][:1]:
        util = v.get("capacity_utilization_pct")
        if util is not None and util <= cfg["capacity_util_pct"]:
            out.append({
                "level": "low",
                "title": "产能利用率偏低且仍有在建产能",
                "detail": (
                    f"{v['source']}设计产能 {v.get('design_capacity_wan_ton')} 万吨、"
                    f"实际 {v.get('actual_capacity_wan_ton')} 万吨（利用率 {util}%），"
                    f"在建 {v.get('in_build_capacity_wan_ton')} 万吨。"
                    "需求下行期继续扩产，需关注后续转固折旧与减值压力。"
                ),
                "quote": v.get("quote", ""),
                "source": v.get("source", ""),
            })
    return out


def extract_audit_evidence(
    symbol: str, db_path: Path | None = None, max_reports: int = 3
) -> dict[str, Any]:
    """抽取最近若干份年报（+最新中报）的审计/会计口径/资金动向与反证事实。

    返回结构里每个字段都是数组，元素必带 ``period`` / ``source`` / ``quote``；
    ``alerts`` 是由这些事实确定性生成的待核查提示，``counter_evidence`` 是
    用来平衡单向风险叙事的公司披露原文。
    """

    settings = load_settings()
    cfg = {
        "deposit_share_pct": float(settings.get(
            "forensics.audit_evidence.deposit_share_pct", 20.0)),
        "payout_alert_pct": float(settings.get(
            "forensics.audit_evidence.payout_alert_pct", 100.0)),
        "capacity_util_pct": float(settings.get(
            "forensics.audit_evidence.capacity_util_pct", 70.0)),
    }

    result: dict[str, Any] = {"symbol": symbol, "source_docs": [], "missing": []}
    for key, _ in _EXTRACTORS:
        result[key] = []

    try:
        docs = _load_documents(symbol, db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("读取财报文档失败（审计视角）%s: %s", symbol, exc)
        result["missing"] = ["文档读取失败"]
        result["alerts"] = []
        return result

    code, _ = parse_code(symbol)
    annuals = [d for d in docs if str(d.get("doc_type")) == _ANNUAL]
    by_period: dict[str, Path] = {}
    for doc in annuals:
        period = str(doc.get("period") or "")
        path = _text_path(doc) if period else None
        if path is not None:
            by_period[period] = path
    # 磁盘归档优先：documents 表可能只登记了业绩公告，完整年报在磁盘上
    try:
        base = paths.documents_dir() / code
        if base.is_dir():
            for folder in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
                if not folder.is_dir() or not folder.name.isdigit():
                    continue
                candidates = [p for p in folder.glob(f"annual_report_{folder.name}*.txt")
                              if p.is_file() and "_results" not in p.name]
                if candidates:
                    by_period[folder.name] = max(candidates, key=lambda p: p.stat().st_size)
    except OSError:
        pass

    picked: list[tuple[str, Path, str]] = []
    for period, path in sorted(by_period.items(), key=lambda kv: kv[0], reverse=True)[:max_reports]:
        picked.append((period, path, f"{period} 年报"))
    # 最新中报补一条，用来抓当期口径与资金变化（同一年可能比年报更新）
    semis = [d for d in docs if str(d.get("doc_type")) == _SEMI]
    if semis:
        latest = max(semis, key=lambda d: str(d.get("period") or ""))
        path = _text_path(latest)
        period = str(latest.get("period") or "")
        if path is not None and period and period not in by_period:
            picked.append((period, path, f"{period} 中报"))

    for period, path, source in picked:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if is_garbled_text(text):
                _logger.warning("跳过 CID 乱码财报文本（审计视角）%s", path)
                continue
        except OSError as exc:
            _logger.warning("读取财报文本失败（审计视角）%s: %s", path, exc)
            continue
        for key, extractor in _EXTRACTORS:
            try:
                values = extractor(text, period, source)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("审计视角抽取 %s 失败: %s", key, exc)
                continue
            result[key].extend(values)
        result["source_docs"].append({
            "period": period, "report_label": source,
            "chars": len(text), "local_path": str(path),
        })

    for key, _ in _EXTRACTORS:
        if not result[key]:
            result["missing"].append(key)
    result["thresholds"] = cfg
    result["alerts"] = _alerts(result, cfg)
    return result
