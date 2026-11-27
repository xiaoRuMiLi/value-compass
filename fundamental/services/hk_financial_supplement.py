"""港股财务补充数据：从已归档财报原文抽取 DCF/DDM 需要的权威口径数据。

背景：港股现金流量表长表在东财不存在（见 providers/financial/eastmoney.py），
分红接口也只覆盖 A 股，导致腾讯等港股的 DDM/DCF 长期拿不到输入。但公司自己的
财报原文里其实写得很清楚：

- 自由现金流：每个季度用固定句式披露「第N季的自由现金流为人民币X亿元，反映经营
  活动现金净额Y亿元，部分被资本开支Z亿元…所抵销」。这是公司自己的 FCF 口径，
  比第三方估算更权威，且按季披露。
- 回购注销：董事会报告「购买、出售或赎回本公司上市证券」段落写明总代价、股数与
  「购回的股份其后已被注销」。

设计约束（与项目其他 services 模块一致）：
- 本模块负责「抽取」，返回纯 dict，不写库、不下估值结论；analysis 层只消费数字。
- 抽不到就明确留空 + 原因，绝不猜测或补数。
- 每条数据带原文引文与来源文档，可溯源、可人工核对。
- 不改写原文，不把「待核查」写成定性结论。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_garbled_text

from .narrative._evidence import _load_documents, _text_path

_logger = get_logger(__name__)

# 自由现金流：季度财报固定句式。繁体为主，兼容简体。
# 「第[一二三四]季的自由现金流为人民币X亿元」或「产生的自由现金流为人民币X亿元」
_RE_FCF = re.compile(
    r"(?:第[一二三四]季的自由現金流[為为]|產生的自由現金流[為为]"
    r"|錄得(?P<neg>負)自由現金流|自由現金流[為为])"
    r"\s*人民幣\s*(?P<fcf>[\d,]+)\s*億元",
    re.S,
)
# 「若剔除…我们的自由现金流为X亿元」是调整后口径，与标准季度口径不可加，
# 必须排除；否则会把 2026Q2 的调整值 376 当成标准值（正确应为 430）。
_RE_FCF_ADJUSTED = re.compile(r"若剔\s*除[\s\S]{0,60}?自由現金流[為为]", re.S)
# 季度现金流分项（归一化空白后匹配）：兼容正/负自由现金流表述。
# 腾讯原文：自由现金流 = 经营现金流 − 资本开支 − 媒体内容 − 租赁负债；
# 这里把 OCF 与资本开支单列出来，供股权自由现金流 FCFE（OCF−capex−实付利息）复核；
# 港股 OCF 不含损益表利息收支时，按财报原文披露值单独调整（A 股利息收入已含在 OCF，不加回）。
# 媒体内容/租赁仅作口径差异说明，不混入 FCFE。
_RE_FCF_LINE = re.compile(
    r"(?:第[一二三四]季的自由現金流[為为]|產生的自由現金流[為为]|錄得(?P<neg>負)自由現金流)"
    r"\s*人民幣\s*(?P<fcf>[\d,]+)\s*億元",
    re.S,
)
# PDF 断行常在词中插入空格（「淨 額」「人 民幣」），匹配前先去掉所有空白。
_RE_OCF_LINE = re.compile(r"經營活動所(?:產生的)?現金(?:流量)?淨額人民幣(?P<ocf>[\d,]+)億元")
_RE_CAPEX_LINE = re.compile(r"資本開支付款人民幣(?P<capex>[\d,]+)億元")
_RE_MEDIA_LINE = re.compile(r"媒體內容付款人民幣(?P<media>[\d,]+)億元")
_RE_LEASE_LINE = re.compile(r"租賃負債付款人民幣(?P<lease>[\d,]+)億元")
# 年报损益表利息收支（单位：人民币百万元）
_RE_INTEREST_INCOME = re.compile(r"利息收入\s+([\d,]{4,})\s+([\d,]{4,})")
_RE_FINANCE_COST = re.compile(r"財務成本\s+\(([\d,]+)\)\s+\(([\d,]+)\)")
# 回购注销：董事会报告段落。「总代价约X亿港元…购回合共N股股份。购回的股份其后已被注销」
_RE_BUYBACK_HKD = re.compile(
    r"總代價約\s*([\d,]+)\s*億港元[\s\S]{0,120}?購回合共\s*([\d,]+)\s*股股份",
    re.S,
)
_RE_CANCELLED = re.compile(r"購回的股份其後已被註銷|購回及註銷股份|購回及註銷")
# 香港/中国数字转中文季度映射
_Q_CN = {"一": 1, "二": 2, "三": 3, "四": 4}
_DOC_QUARTER = {
    "q1_report": 1, "semi_report": 2, "q3_report": 3, "annual_report": 4,
}


def _to_yi(value: str) -> float:
    """「340」「1,024」→ 亿元浮点。"""
    return float(value.replace(",", ""))


def _load_report_texts(symbol: str) -> list[dict[str, Any]]:
    """加载已归档且文本可用的财报（与体检共用文档池与乱码守卫）。"""
    docs = _load_documents(symbol, None)
    out: list[dict[str, Any]] = []
    for doc in docs:
        txt = _text_path(doc)
        if txt is None:
            continue
        try:
            raw = txt.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if is_garbled_text(raw):
            continue
        out.append({**doc, "text": raw, "path": str(txt)})
    return out


def _doc_quarter(doc_type: str, text: str) -> int | None:
    """由文档类型判定其披露的是第几季度。

    港股年报的 MD&A 只披露 Q4 单季自由现金流（不是全年），因此归为 Q4；
    其余按文档类型映射。正文若明确写「第N季」则以正文为准。
    """
    m = re.search(r"第([一二三四])季的自由現金流", text)
    if m:
        return _Q_CN.get(m.group(1))
    for key, q in _DOC_QUARTER.items():
        if key in str(doc_type):
            return q
    return None


def extract_fcf_ttm(symbol: str, all_quarters: bool = False) -> dict[str, Any]:
    """抽取自由现金流；默认取最近四季汇总 TTM，all_quarters 时返回全部逐季。

    all_quarters=True 供历史 FCF 落库用：不经 [-4:] 截断，返回归档原文中
    全部可识别季度，每个季度带 report_date/report_type 便于按报告期入库。
    返回结构（纯 dict）：
    {
      "available": bool,
      "fcf_ttm_yi": float | None,   # 最近四季合计；all_quarters 时为 None
      "quarters": [{"period","quarter","report_date","report_type",
                    "fcf_yi","quote","source"}],
      "method": "公司财报原文披露的自由现金流口径",
      "note": "…",
      "reason": "…"  # 不可用时说明原因
    }
    """
    empty = {
        "available": False, "fcf_ttm_yi": None, "quarters": [],
        "method": "公司财报原文披露的自由现金流口径", "note": "",
    }
    docs = _load_report_texts(symbol)
    if not docs:
        return {**empty, "reason": "无可用财报原文"}

    quarters: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for doc in docs:
        text = doc["text"]
        q = _doc_quarter(str(doc.get("doc_type") or ""), text)
        if q is None:
            continue
        # 逐个匹配点筛选：跳过「若剔除…」调整口径，只取标准季度披露。
        # 窗口取 120 字符，覆盖「若剔除算力采购的预付款项，我们的自由现金流为」整句。
        match = None
        for cand in _RE_FCF.finditer(text):
            head = text[max(0, cand.start() - 120):cand.end()]
            if _RE_FCF_ADJUSTED.search(head):
                continue
            match = cand
            break
        if match is None:
            continue
        m = match
        period = str(doc.get("period") or "")
        key = (period, q)
        if key in seen:
            continue
        seen.add(key)
        quote_start = max(0, m.start() - 20)
        quote = re.sub(r"\s+", " ", text[quote_start:m.end() + 30]).strip()
        quarter_end = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}[q]
        report_type = {1: "Q1", 2: "H1", 3: "Q3", 4: "FY"}[q]
        fcf_value = _to_yi(m.group("fcf"))
        if m.group("neg"):
            fcf_value = -fcf_value
        quarters.append({
            "period": period,
            "quarter": q,
            "report_date": period + quarter_end,
            "report_type": report_type,
            "fcf_yi": fcf_value,
            "quote": quote,
            "source": str(doc.get("title") or period),
        })

    if not quarters:
        return {**empty, "reason": "财报原文未找到自由现金流披露句式"}

    # 按 (年, 季) 排序
    quarters.sort(key=lambda x: (int(x["period"]), x["quarter"]))
    if all_quarters:
        note = (
            "公司财报原文披露的单季自由现金流（经营现金流扣除资本开支、媒体内容及租赁负债等），"
            f"从归档原文抽出 {len(quarters)} 个季度，按报告期落库，单位亿元人民币。"
        )
        return {
            "available": True,
            "fcf_ttm_yi": None,
            "quarters": quarters,
            "method": "公司财报原文披露的自由现金流口径",
            "note": note,
        }

    # 默认模式：取最近四个连续季度汇总 TTM；缺季则最多取到手的数量并标注。
    recent = quarters[-4:]
    total = round(sum(q["fcf_yi"] for q in recent), 2)
    # 校验连续性：相邻季度应为 1→2→3→4→次年1
    def idx(q: dict[str, Any]) -> int:
        return int(q["period"]) * 4 + q["quarter"]
    continuous = all(idx(recent[i + 1]) - idx(recent[i]) == 1
                     for i in range(len(recent) - 1))
    note = (
        "自由现金流为公司财报原文披露口径（经营现金流扣除资本开支、媒体内容及租赁负债等），"
        "按最近四个季度汇总为 TTM，单位亿元人民币。"
    )
    if len(recent) < 4 or not continuous:
        note += (
            f" 当前仅取到 {len(recent)} 个季度且不连续，TTM 代表性有限，"
            "需补齐缺失季度财报后重算。"
        )
    return {
        "available": True,
        "fcf_ttm_yi": total,
        "quarters": recent,
        "method": "公司财报原文披露的自由现金流口径",
        "note": note,
    }


def extract_cashflow_ttm(symbol: str) -> dict[str, Any]:
    """从港股财报原文抽取最近四季 OCF / 资本开支 / 利息收支（单位：亿元人民币）。

    港股现金流量表长表在东财不存在，但腾讯在每季业绩公告里以固定句式披露：
    「自由现金流为人民币X亿元，反映经营活动所产生的现金净额人民币Y亿元，
      部分被资本开支Z亿元、媒体内容付款...所抵销」。
    本函数把其中的 OCF 与资本开支单列出来，并取年报损益表的利息收入/财务成本，
    供股权自由现金流 FCFE（OCF−资本开支−实付利息）复核。港股 OCF 未包含损益表
    利息收支时，按财报原文披露值单独调整；A 股利息收入已含在 OCF，按国际通用惯例
    不重复加回。公司披露的自由现金流口径额外扣除了媒体内容与租赁负债，二者差异会在说明里注明。

    返回结构：
    {
      "available": bool, "ocf_ttm_yi": float|None, "capex_ttm_yi": float|None,
      "interest_income_yi": float|None, "interest_expense_yi": float|None,
      "reported_fcf_ttm_yi": float|None, "quarters": [...], "note": str,
    }
    """
    empty = {
        "available": False, "ocf_ttm_yi": None, "capex_ttm_yi": None,
        "interest_income_yi": None, "interest_expense_yi": None,
        "reported_fcf_ttm_yi": None, "quarters": [], "note": "",
    }
    docs = _load_report_texts(symbol)
    if not docs:
        return {**empty, "reason": "无可用财报原文"}

    quarters: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for doc in docs:
        raw = re.sub(r"\s+", "", doc["text"])
        spaced = re.sub(r"\s+", " ", doc["text"])
        q = _doc_quarter(str(doc.get("doc_type") or ""), spaced)
        if q is None:
            continue
        match = None
        for cand in _RE_FCF_LINE.finditer(raw):
            # 「若剔除算力预付款…自由现金流」是调整口径，不能当标准季度值。
            head = raw[max(0, cand.start() - 80):cand.end()]
            if "若剔除" in head:
                continue
            match = cand
            break
        if match is None:
            continue
        period = str(doc.get("period") or "")
        key = (period, q)
        if key in seen:
            continue
        seen.add(key)
        seg = raw[match.start():match.start() + 500]
        ocf_m = _RE_OCF_LINE.search(seg)
        capex_m = _RE_CAPEX_LINE.search(seg)
        media_m = _RE_MEDIA_LINE.search(seg)
        lease_m = _RE_LEASE_LINE.search(seg)
        fcf_yi = _to_yi(match.group("fcf"))
        if match.group("neg"):
            fcf_yi = -fcf_yi
        quarters.append({
            "period": period,
            "quarter": q,
            "fcf_yi": fcf_yi,
            "ocf_yi": _to_yi(ocf_m.group("ocf")) if ocf_m else None,
            "capex_yi": _to_yi(capex_m.group("capex")) if capex_m else None,
            "media_yi": _to_yi(media_m.group("media")) if media_m else None,
            "lease_yi": _to_yi(lease_m.group("lease")) if lease_m else None,
            "source": str(doc.get("title") or period),
        })

    if not quarters:
        return {**empty, "reason": "财报原文未找到季度现金流披露句式"}

    quarters.sort(key=lambda x: (int(x["period"]), x["quarter"]))
    recent = quarters[-4:]
    idx = lambda q: int(q["period"]) * 4 + q["quarter"]
    continuous = all(idx(recent[i + 1]) - idx(recent[i]) == 1 for i in range(len(recent) - 1))

    def total(key: str) -> float | None:
        vals = [q[key] for q in recent if q.get(key) is not None]
        return round(sum(vals), 2) if len(vals) == len(recent) else None

    # 年报损益表利息收支（最新年度，单位百万元 → 亿元）
    interest_income: float | None = None
    interest_expense: float | None = None
    annuals = sorted(
        (d for d in docs if "annual_report" in str(d.get("doc_type") or "")),
        key=lambda d: str(d.get("period") or ""), reverse=True,
    )
    for doc in annuals:
        text = re.sub(r"\s+", " ", doc["text"])
        inc = _RE_INTEREST_INCOME.search(text)
        cost = _RE_FINANCE_COST.search(text)
        if inc:
            interest_income = round(_to_yi(inc.group(1)) / 100, 2)
        if cost:
            interest_expense = round(_to_yi(cost.group(1)) / 100, 2)
        if inc or cost:
            break

    ocf_ttm = total("ocf_yi")
    capex_ttm = total("capex_yi")
    note = (
        "港股现金流量表长表在行情源不存在，OCF 与资本开支取财报原文「自由现金流」段落"
        "（经营活动现金净额、资本开支付款）按最近四个季度汇总；"
        "利息收入/财务成本取最新年报损益表（财务成本含汇兑损益，按原文金额计入）。"
        "公司披露的自由现金流还扣除了媒体内容付款与租赁负债付款，口径比标准 FCF 更严，"
        "故两者数值不同，媒体内容/租赁在下方季度明细中单列。"
    )
    if not continuous:
        note += f" 当前仅取到 {len(recent)} 个季度且不连续，TTM 代表性有限。"
    if ocf_ttm is None or capex_ttm is None:
        note += " 部分季度缺少 OCF/资本开支明细，该项按不可用处理，不用 0 冒充。"

    return {
        "available": ocf_ttm is not None or capex_ttm is not None,
        "ocf_ttm_yi": ocf_ttm,
        "capex_ttm_yi": capex_ttm,
        "interest_income_yi": interest_income,
        "interest_expense_yi": interest_expense,
        "reported_fcf_ttm_yi": total("fcf_yi"),
        "quarters": recent,
        "note": note,
    }


def total_shareholder_return_per_share(
    symbol: str,
    dividends: list[Any],
    shares: float | None,
    fx_rate: float,
) -> dict[str, Any]:
    """合成 DDM 口径的「股东回报每股」= 现金分红每股 + 回购注销每股。

    - 现金分红：最近一次每股派息（港股为 HKD），按 fx_rate 换算成 CNY；
    - 回购注销：最近年度回购总代价（HKD）按 fx_rate 换算成 CNY，除以总股本得每股；
    - 若任一项缺失则返回已有项，并注明口径，绝不臆造数字。

    ``dividends`` 是 DividendRecord 列表（含 currency 字段）；``shares`` 为总股本；
    ``fx_rate`` 为 HKD→CNY 汇率。本函数只做换算与加总，不下估值结论。
    """
    result: dict[str, Any] = {
        "cash_dividend_per_share_cny": None,
        "buyback_per_share_cny": None,
        "total_per_share_cny": None,
        "fx_rate": fx_rate,
        "note": "",
        "parts": [],
    }

    # 现金分红：取最近一条有 cash_per_share 的记录
    cash_cny: float | None = None
    for d in dividends:
        raw = getattr(d, "cash_per_share", None)
        if not raw:
            continue
        cur = str(getattr(d, "currency", "CNY") or "CNY").upper()
        cash_cny = float(raw) * fx_rate if cur == "HKD" else float(raw)
        result["cash_dividend_per_share_cny"] = round(cash_cny, 4)
        result["parts"].append(f"每股现金分红 {raw}{cur} ≈ {cash_cny:.3f}元")
        break

    # 回购注销：最近年度总代价 / 总股本
    buyback_cny_per_share: float | None = None
    bb = extract_buyback_cancellation(symbol)
    if bb.get("available") and bb.get("amount_hkd_yi") and shares:
        amount_cny = float(bb["amount_hkd_yi"]) * 1e8 * fx_rate
        buyback_cny_per_share = amount_cny / shares
        result["buyback_per_share_cny"] = round(buyback_cny_per_share, 4)
        result["parts"].append(
            f"{bb['period']}年回购注销 {bb['amount_hkd_yi']}亿港元 ≈ {amount_cny / 1e8:.1f}亿元"
            f" / {shares / 1e8:.1f}亿股 ≈ 每股 {buyback_cny_per_share:.3f}元"
        )
        result["buyback_detail"] = bb

    # 合成
    if cash_cny is not None or buyback_cny_per_share is not None:
        total = (cash_cny or 0.0) + (buyback_cny_per_share or 0.0)
        result["total_per_share_cny"] = round(total, 4)
        result["note"] = (
            "DDM 股息口径 = 每股现金分红 + 每股回购注销（均按 HKD→CNY 汇率换算）。"
            "回购注销体现股东回报，但未必可持续；无现金分红或无回购时按已有项计算并注明。"
        )
    return result


def extract_buyback_cancellation(symbol: str) -> dict[str, Any]:
    """抽取最近一个完整年度的回购注销（总代价、股数、是否已注销）。

    返回结构（纯 dict）：
    {
      "available": bool,
      "amount_hkd_yi": float | None,   # 总代价（亿港元）
      "shares": int | None,            # 回购股数
      "cancelled": bool,               # 是否已注销
      "period": str,
      "quote": str,
      "source": str,
      "note": str,
      "reason": "…"
    }
    """
    empty = {
        "available": False, "amount_hkd_yi": None, "shares": None,
        "cancelled": False, "period": "", "quote": "", "source": "", "note": "",
    }
    docs = _load_report_texts(symbol)
    annuals = [d for d in docs if "annual_report" in str(d.get("doc_type") or "")]
    if not annuals:
        return {**empty, "reason": "无可用年报原文"}
    annuals.sort(key=lambda d: str(d.get("period") or ""), reverse=True)

    for doc in annuals:
        text = doc["text"]
        m = _RE_BUYBACK_HKD.search(text)
        if not m:
            continue
        cancelled = bool(_RE_CANCELLED.search(text))
        quote_start = max(0, m.start() - 30)
        quote = re.sub(r"\s+", " ", text[quote_start:m.end() + 60]).strip()
        return {
            "available": True,
            "amount_hkd_yi": _to_yi(m.group(1)),
            "shares": int(m.group(2).replace(",", "")),
            "cancelled": cancelled,
            "period": str(doc.get("period") or ""),
            "quote": quote,
            "source": str(doc.get("title") or ""),
            "note": (
                "取自年报「购买、出售或赎回本公司上市证券」段落；金额为回购总代价（港元），"
                "股数为购回股份数，注销状态按原文表述。与 MD&A 中的净现金回购口径可能不同。"
            ),
        }
    return {**empty, "reason": "年报原文未找到回购注销披露句式"}
