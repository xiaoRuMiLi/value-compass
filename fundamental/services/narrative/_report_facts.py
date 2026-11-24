"""财报原文「结构化事实卡」：把原文里已写明、但接口字段没有的数字抽成可判定输入。

存在的意义：财报深度体检原来的事实层只吃利润率、同比与少量科目，客户集中度、
关联方销售占比、非经常性损益明细、研发资本化率、会计政策变更、合并范围变化
这些写在同一份年报里的硬数字进不了确定性检查，只能指望 LLM 逐段通读碰运气。
本模块从已归档财报正文中按章节表头定位抽取，交给 analysis 层做组合判定。

只做「抽取」，不做判断；抽不到就明确留空，绝不猜测或补数。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_garbled_text

from ._evidence import _load_documents, _text_path, parse_code

_logger = get_logger(__name__)

_ANNUAL = "annual_report"

# 标签与数值在 PyMuPDF 抽取的文本里常分行，正则统一用 \s* 跨行容错
_RE_A = {
    "top5_customer": re.compile(r"前五名客户合计销售金额占年度销售总额比例\s*([\d.]+)\s*%", re.S),
    "related_sales": re.compile(
        r"前五名客户销售额中关联方销售额占年度销售总额比例\s*([\d.]+)\s*%", re.S
    ),
    "top5_supplier": re.compile(r"前五名供应商合计采购金额占年度采购总额比例\s*([\d.]+)\s*%", re.S),
    "nonrecurring_total": re.compile(r"合\s*计\s*([\d,]+\.\d{2})", re.S),
    "rd_cap_rate": re.compile(
        r"资本化研发支出占研发投入\s*的比例\s*([\d.]+)\s*%\s*([\d.]+)\s*%\s*([\d.]+)\s*%", re.S
    ),
    "rd_cap_profit": re.compile(
        r"资本化研发支出占当期净利\s*润的比重\s*([\d.]+)\s*%", re.S
    ),
    "policy_title": re.compile(r"会计政策、会计估计变更或重大会计差错更正的说明"),
    "consolidation_title": re.compile(r"合并报表范围发生变化的情况说明"),
    "nonstandard_title": re.compile(r"非标准审计报告"),
    "new_entities": re.compile(r"新纳入合并范围的公司有[：:]\s*([\s\S]{0,400}?)。", re.S),
}

_RE_HK = {
    "top5_customer": re.compile(r"五大客戶佔本集團總收入約\s*([\d.]+)\s*%", re.S),
    "max_customer": re.compile(r"最大客戶佔本集團總收\s*入約\s*([\d.]+)\s*%", re.S),
    "top5_supplier": re.compile(r"五大供應商佔本集團總採購額約\s*([\d.]+)\s*%", re.S),
    "buyback": re.compile(r"人民幣\s*([\d,]+)\s*億元股份回購", re.S),
    # 核数师报告里「假若披露不足，則我們應當發表非無保留意見」是条件句，
    # 用关键词「保留意見」会误报；只认「我們認為…真實而公平」这一结论句。
    "opinion_unqualified": re.compile(r"我們認為[\s\S]{0,400}?真實而公平", re.S),
}


def _is_hk(symbol: str) -> bool:
    return str(symbol).upper().endswith(".HK")


def _section(text: str, pattern: re.Pattern, span: int = 180) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    return text[m.start(): m.start() + span]


def _applicable(section: str) -> bool | None:
    """从「□适用 / ☑不适用」判定该章节是否适用；判定不了返回 None。

    年报里两种写法：``□适用 ☑不适用`` 表示不适用，``☑适用 □不适用`` 表示适用。
    PDF 抽取后的勾选符号是私有区字符（U+F052 等），不能简单用「不含不适用」判断，
    必须看「不适用」前面的方框是不是空框 ``□``。
    """
    if not section:
        return None
    head = section[:90]
    no = re.search(r"(\S)\s*不适用", head)
    if no:
        # 「不适用」被勾选 → 不适用；「不适用」前是空方框 → 说明勾的是「适用」
        return no.group(1) == "□"
    yes = re.search(r"(\S)\s*适用(?!不)", head)
    if yes:
        return yes.group(1) != "□"
    return None


def _quote(text: str, pattern: re.Pattern, span: int = 120) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    return re.sub(r"\s+", " ", text[max(0, m.start() - 20): m.end() + span]).strip()


def _disk_annual_text(code: str, period: str) -> Path | None:
    """绕过 documents 表，直接从归档目录找该报告期的完整年报文本。

    归档按 (stock_id, doc_type, period) 判重，港股同一报告期的「业绩公告」与
    「完整年报」只留了一条；完整年报往往已下载到磁盘但没登记进表。事实卡必须
    能读到它，否则最新一年的客户集中度、核数师报告、回购等章节全部取不到。
    """
    try:
        folder = paths.documents_dir() / code / period
    except Exception:  # noqa: BLE001
        return None
    if not folder.is_dir():
        return None
    candidates = [p for p in folder.glob(f"annual_report_{period}*.txt") if p.is_file()]
    if not candidates:
        return None
    full = [p for p in candidates if "_results" not in p.name]
    pool = full or candidates
    prefer = [p for p in pool if p.name.endswith("_report.txt")]
    pool = prefer or pool
    return max(pool, key=lambda p: p.stat().st_size)


def _float(m: re.Match | None, group: int = 1) -> float | None:
    if not m:
        return None
    try:
        return float(m.group(group).replace(",", ""))
    except (ValueError, IndexError):
        return None


def _extract_a(text: str, period: str, source: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    m = _RE_A["top5_customer"].search(text)
    if m:
        out["top5_customer_pct"] = [{
            "period": period, "value": _float(m), "source": source,
            "quote": _quote(text, _RE_A["top5_customer"]),
        }]
    m = _RE_A["related_sales"].search(text)
    if m:
        out["related_party_sales_pct"] = [{
            "period": period, "value": _float(m), "source": source,
            "quote": _quote(text, _RE_A["related_sales"]),
        }]
    m = _RE_A["top5_supplier"].search(text)
    if m:
        out["top5_supplier_pct"] = [{
            "period": period, "value": _float(m), "source": source,
            "quote": _quote(text, _RE_A["top5_supplier"]),
        }]

    # 非经常性损益：限定在「八、非经常性损益项目及金额」章节内取合计，避免匹配到别处
    start = text.find("非经常性损益项目及金额")
    if start >= 0:
        seg = text[start: start + 4000]
        m = _RE_A["nonrecurring_total"].search(seg)
        if m:
            out["nonrecurring_total"] = [{
                "period": period, "value": _float(m), "source": source,
                "quote": re.sub(r"\s+", " ", seg[m.start(): m.start() + 80]).strip(),
            }]

    # 研发资本化率：表格三列依次为当年 / 上年 / 上上年
    m = _RE_A["rd_cap_rate"].search(text)
    if m:
        years = [period, str(int(period) - 1) if period.isdigit() else period,
                 str(int(period) - 2) if period.isdigit() else period]
        profit_m = _RE_A["rd_cap_profit"].search(text)
        profit_share = _float(profit_m)
        out["rd_capitalization_pct"] = [
            {"period": y, "value": _float(m, i + 1),
             "profit_share": profit_share if i == 0 else None,
             "source": source, "quote": _quote(text, _RE_A["rd_cap_rate"])}
            for i, y in enumerate(years)
        ]

    applicable = _applicable(_section(text, _RE_A["policy_title"]))
    if applicable is not None:
        out["accounting_policy_change"] = [{
            "period": period, "applicable": applicable, "source": source,
            "quote": _quote(text, _RE_A["policy_title"], 90),
        }]

    cons_sec = _section(text, _RE_A["consolidation_title"], 500)
    cons = _applicable(cons_sec)
    if cons is not None:
        entities: list[str] = []
        em = _RE_A["new_entities"].search(text)
        if em:
            raw = re.sub(r"\s+", "", em.group(1))
            entities = [x.strip() for x in re.split(r"[、,，;；及]", raw)
                        if x.strip() and len(x.strip()) < 60]
        out["consolidation_change"] = [{
            "period": period, "applicable": cons, "entities": entities,
            "entity_count": len(entities), "source": source,
            "quote": re.sub(r"\s+", " ", cons_sec[:220]).strip(),
        }]

    ns = _applicable(_section(text, _RE_A["nonstandard_title"]))
    if ns is not None:
        out["audit"] = [{
            "period": period, "non_standard": ns, "source": source,
            "quote": _quote(text, _RE_A["nonstandard_title"], 90),
        }]
    return out


def _extract_hk(text: str, period: str, source: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    m = _RE_HK["top5_customer"].search(text)
    if m:
        out["top5_customer_pct"] = [{
            "period": period, "value": _float(m), "source": source,
            "quote": _quote(text, _RE_HK["top5_customer"], 140),
        }]
    m = _RE_HK["top5_supplier"].search(text)
    if m:
        out["top5_supplier_pct"] = [{
            "period": period, "value": _float(m), "source": source,
            "quote": _quote(text, _RE_HK["top5_supplier"], 100),
        }]
    m = _RE_HK["buyback"].search(text)
    if m:
        out["share_buyback"] = [{
            "period": period, "value": _float(m), "unit": "亿元人民币",
            "source": source, "quote": _quote(text, _RE_HK["buyback"], 80),
        }]
    unqualified = _RE_HK["opinion_unqualified"].search(text)
    out["audit"] = [{
        "period": period,
        # 找不到标准无保留结论句时不下判断，宁可标 unknown 也不误报非标意见
        "non_standard": False if unqualified else None,
        "opinion": "unqualified" if unqualified else "unknown",
        "source": source,
        "quote": _quote(text, _RE_HK["opinion_unqualified"], 60) if unqualified else "",
    }]
    return out


def extract_report_facts(
    symbol: str, db_path: Path | None = None, max_reports: int = 3
) -> dict[str, Any]:
    """抽取最近若干份年报里的结构化事实；失败返回空结构，不抛异常给调用方。"""
    result: dict[str, Any] = {
        "symbol": symbol,
        "top5_customer_pct": [],
        "related_party_sales_pct": [],
        "top5_supplier_pct": [],
        "nonrecurring_total": [],
        "rd_capitalization_pct": [],
        "accounting_policy_change": [],
        "consolidation_change": [],
        "audit": [],
        "share_buyback": [],
        "source_docs": [],
        "missing_facts": [],
    }
    try:
        docs = _load_documents(symbol, db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("读取财报文档失败 %s: %s", symbol, exc)
        result["missing_facts"] = ["文档读取失败"]
        return result

    # 同一报告期可能有多份归档（业绩公告 / 完整年报 / 早期重复文件）。
    # documents 表可能只登记了业绩公告，故年份集合与文件路径都以磁盘为准补齐。
    code, _ = parse_code(symbol)
    annuals = [d for d in docs if str(d.get("doc_type")) == _ANNUAL]
    periods: set[str] = set()
    by_period: dict[str, Path] = {}
    for doc in annuals:
        period = str(doc.get("period") or "")
        if not period:
            continue
        periods.add(period)
        path = _text_path(doc)
        if path is not None:
            by_period[period] = path
    try:
        base = paths.documents_dir() / code
        if base.is_dir():
            for folder in base.iterdir():
                if folder.is_dir() and folder.name.isdigit():
                    periods.add(folder.name)
    except OSError:
        pass
    for period in periods:
        disk = _disk_annual_text(code, period)
        if disk is not None:
            by_period[period] = disk

    picked: list[dict[str, Any]] = []
    for period in sorted(by_period.keys(), reverse=True)[:max_reports]:
        picked.append({"period": period, "path": by_period[period],
                       "title": f"{period} 年报（磁盘归档）"})

    hk = _is_hk(symbol)
    for item in picked:
        try:
            text = item["path"].read_text(encoding="utf-8", errors="ignore")
            if is_garbled_text(text):
                _logger.warning("跳过 CID 乱码财报文本 %s", item["path"])
                continue
        except OSError as exc:
            _logger.warning("读取财报文本失败 %s: %s", item["path"], exc)
            continue
        source = f"{item['period']} 年报"
        extracted = _extract_hk(text, item["period"], source) if hk else _extract_a(
            text, item["period"], source
        )
        for key, values in extracted.items():
            if key not in result or not isinstance(values, list):
                continue
            # 研发投入资本化率表本身含三年数据，最新一份年报即可覆盖，
            # 后续年报会重复同一年份，故只取首次抽到的那组。
            if key == "rd_capitalization_pct" and result[key]:
                continue
            result[key].extend(values)
        result["source_docs"].append({
            "period": item["period"], "report_label": source,
            "chars": len(text), "local_path": str(item["path"]),
        })

    for key in ("top5_customer_pct", "related_party_sales_pct", "top5_supplier_pct",
                "nonrecurring_total", "rd_capitalization_pct",
                "accounting_policy_change", "consolidation_change", "audit"):
        if not result[key]:
            result["missing_facts"].append(key)
    return result
