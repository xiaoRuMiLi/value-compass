"""财报原文非标准口径抽取：LLM 读数字并附引文，代码校验与换算。

标准三大表与主要指标由 provider 落库；本模块只处理「原文明确披露、标准接口没有」
的桥接资产口径（公允价值类金融资产、权益类投资、存款与理财）。职责边界：

- LLM 负责从原文读出数字、原文单位、币种与引文，不做估值判断；
- 代码负责字段白名单、单位/币种换算、引文核对、期间对齐，抽不到就留空并写原因；
- 本模块不写数据库，由 scripts/backfill_extracted_fields.py 负责按报告期落库。

analysis 层不依赖本模块；services 层在标准口径缺失时才读取库内抽取结果。
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage, ReportType
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.fx import hkd_to_cny
from fundamental.services.llm import chat_json, has_live_llm
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_garbled_text
from fundamental.utils.prompts import load_prompt

_logger = get_logger(__name__)


@dataclass(frozen=True)
class ExtractField:
    """一个待抽取口径：field_code 为落库主键，bridge_key 指向 EV→股权桥接档位。"""

    code: str
    label: str
    aliases: tuple[str, ...]
    bridge_key: str


FIELDS: tuple[ExtractField, ...] = (
    ExtractField(
        "FAIR_VALUE_FIN_ASSETS",
        "公允价值类金融资产合计",
        ("公允价值计量的金融资产", "公允價值計量的金融資產", "按公平值計量",
         "指定为以公允价值计量", "交易性金融资产", "其他金融资产", "第三级金融工具"),
        "fair_value",
    ),
    ExtractField(
        "EQUITY_INVEST_TOTAL",
        "权益类投资合计",
        ("对联营公司的投资", "於聯營公司的投資", "联营及合营", "聯營及合營",
         "上市投资公司", "非上市投资公司", "投资物业", "長期股權投資", "长期股权投资"),
        "equity",
    ),
    ExtractField(
        "INVESTMENT_PORTFOLIO_TOTAL",
        "投资组合总额",
        ("投资组合", "投資組合", "investment portfolio"),
        "equity",
    ),
    ExtractField(
        "SHORT_TERM_DEPOSIT",
        "短期存款与理财",
        ("短期存款", "短期定期存款", "流動存款", "短期投资", "短期理財"),
        "deposit",
    ),
    ExtractField(
        "LONG_TERM_DEPOSIT",
        "中长期存款与理财",
        ("中长期存款", "長期存款", "定期存款", "中长期定期存款", "理財產品", "理财产品"),
        "deposit",
    ),
    # 以下三项属于 FCF 公式项，不是 EV→股权桥接资产；bridge_key 用 fcf_ 前缀，
    # 保证 bridge_value_from_extracted(fair_value/equity/deposit) 不会把它们加回股权价值。
    ExtractField(
        "CAPEX_CASH_PAID",
        "购建固定资产等支付的现金（资本开支）",
        ("购建固定资产", "購建固定資產", "购建固定资产、无形资产和其他长期资产支付的现金",
         "購建固定資產、無形資產和其他長期資產支付的現金", "资本开支", "資本開支"),
        "fcf_capex",
    ),
    ExtractField(
        "INTEREST_EXPENSE_DETAIL",
        "利息支出",
        ("利息支出", "利息费用", "利息費用", "其中：利息费用", "其中：利息費用"),
        "fcf_interest_expense",
    ),
    ExtractField(
        "INTEREST_INCOME_DETAIL",
        "利息收入",
        ("利息收入", "收到的利息收入", "利息收入现金"),
        "fcf_interest_income",
    ),
)
FIELD_BY_CODE = {field.code: field for field in FIELDS}
FCF_CASH_FIELDS = ("CAPEX_CASH_PAID", "INTEREST_EXPENSE_DETAIL", "INTEREST_INCOME_DETAIL")


def parse_number(value: Any) -> float | None:
    """把「6,140」「1 024.5」「人民币 88 亿」中的数字部分转成 float。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    text = text.replace(" ", "").replace("\u3000", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    number = float(m.group(0))
    return number if math.isfinite(number) else None


def _unit_scale(unit: str) -> tuple[float, str]:
    """原文单位 → 元倍数。未知单位返回 1.0 并给出 warning，绝不静默猜测。"""
    text = (unit or "").strip().lower().replace(" ", "").replace("\u3000", "")
    if "十亿" in text or "十億" in text:
        return 1e9, "十亿"
    if "百万" in text or "百萬" in text:
        return 1e6, "百万"
    if "亿" in text or "億" in text:
        return 1e8, "亿"
    if "万" in text or "萬" in text:
        return 1e4, "万"
    if "千" in text:
        return 1e3, "千"
    if text in ("元", "人民币", "人民幣", "港元", "港幣", "美元", "", "cny", "hkd", "usd"):
        return 1.0, "元"
    return 1.0, f"未识别单位「{unit}」，按元处理"


def to_cny(value: float, currency: str) -> tuple[float, float, str, bool]:
    """原文金额（已换算到元）→ 人民币元，返回 (value_cny, rate, source, is_fallback)。"""
    code = (currency or "").strip().upper()
    if code in ("CNY", "RMB", "人民币", "人民幣", ""):
        return value, 1.0, "原币即人民币", False
    if code in ("HKD", "港元", "港币", "港幣"):
        fx = hkd_to_cny()
        return value * float(fx["rate"]), float(fx["rate"]), str(fx["source"]), bool(fx["is_fallback"])
    if code in ("USD", "美元"):
        rate = float(load_settings().get("fx.usd_to_cny_default", 7.2))
        return value * rate, rate, "config/default.toml 默认值（未联网核验）", True
    raise ValueError(f"不支持的币种：{currency}")


def normalize_amount(value: Any, unit: str, currency: str) -> dict[str, Any]:
    """校验并换算原文金额；失败抛 ValueError，由调用方转成 warning。"""
    number = parse_number(value)
    if number is None or number <= 0:
        raise ValueError("金额不是正数或无法解析")
    scale, scale_note = _unit_scale(unit)
    value_cny, fx_rate, fx_source, fx_fallback = to_cny(number * scale, currency)
    return {
        "original_value": number,
        "unit": str(unit or "元"),
        "currency": str(currency or "CNY"),
        "value_cny": value_cny,
        "fx_rate": fx_rate,
        "fx_source": fx_source,
        "fx_fallback": fx_fallback,
        "unit_note": "" if scale_note in ("元", "十亿", "百万", "亿", "万", "千") else scale_note,
    }


def _normalized(text: str) -> str:
    """PDF 断行常在词中插空格；核对引文时去掉所有空白与全角空白。"""
    return re.sub(r"\s+", "", str(text or "")).replace("\u3000", "")


def quote_in_text(quote: str, text: str) -> bool:
    """引文必须在原文里真实出现，防止 LLM 编造数字。"""
    q = _normalized(quote)
    return len(q) >= 8 and q in _normalized(text)


def _load_documents(
    symbol: str, db_path: Path | None, annual_only: bool = False
) -> list[dict[str, Any]]:
    """从 documents 表取年报/中报的本地文本路径，按期间倒序。"""
    from fundamental.storage.repository import get_stock_id
    from fundamental.utils.symbols import parse_symbol

    conn = sqlite3.connect(db_path or paths.db_path())
    try:
        conn.row_factory = sqlite3.Row
        code, market = parse_symbol(symbol)
        stock_id = get_stock_id(conn, code, market)
        if stock_id is None:
            return []
        if annual_only:
            rows = conn.execute(
                "SELECT doc_type, period, title, local_path FROM documents "
                "WHERE stock_id = ? AND doc_type = 'annual_report' "
                "ORDER BY period DESC",
                (stock_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT doc_type, period, title, local_path FROM documents "
                "WHERE stock_id = ? AND doc_type IN ('annual_report','semi_report') "
                "ORDER BY period DESC, doc_type DESC",
                (stock_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def period_to_report_date(period: str, doc_type: str) -> tuple[str | None, ReportType]:
    """文档 period（如 2025）→ 真实报告期与报告类型。"""
    m = re.match(r"(\d{4})", str(period or ""))
    if not m:
        return None, ReportType.FY
    year = m.group(1)
    if "semi" in doc_type:
        return f"{year}-06-30", ReportType.H1
    return f"{year}-12-31", ReportType.FY


def _select_snippets(text: str, budget: int) -> str:
    """按字段别名挑出相关段落，控制上下文预算。"""
    lines = [line.strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if not line or re.fullmatch(r"\d+\s*/\s*\d+|\d+", line):
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            continue
        buffer.append(line)
    if buffer:
        paragraphs.append(" ".join(buffer))

    terms = tuple(term for field in FIELDS for term in field.aliases)
    scored = [
        (sum(1 for term in terms if term in para), para)
        for para in paragraphs if len(para) >= 12
    ]
    selected: list[str] = []
    used = 0
    for score, para in sorted(scored, key=lambda pair: pair[0], reverse=True):
        if score == 0:
            continue
        piece = para[:600]
        if used + len(piece) > budget:
            continue
        selected.append(piece)
        used += len(piece)
        if used >= budget:
            break
    return "\n".join(selected)


def _build_user_prompt(symbol: str, sources: list[dict[str, Any]]) -> str:
    lines = [
        f"标的：{symbol}",
        "下面是已归档财报原文片段，每段标注报告期与文档类型。请严格按系统提示抽取。",
        "",
    ]
    for src in sources:
        lines.append(
            f"--- 报告期 {src['report_date']} | {src['doc_type']} | {src['title']} ---\n{src['text']}"
        )
    return "\n".join(lines)


def _normalize_items(raw: Any, sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """把 LLM 输出规范化为可落库记录；每条不合规项写入 warnings 并丢弃。"""
    if isinstance(raw, dict):
        rows = raw.get("items") or raw.get("data") or []
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    if not isinstance(rows, list):
        rows = []

    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            warnings.append("忽略非对象条目")
            continue
        code = str(row.get("field_code") or "").strip()
        if code not in FIELD_BY_CODE:
            warnings.append(f"忽略未知字段码：{code}")
            continue
        period = str(row.get("report_date") or row.get("period") or "").strip()
        source = next((s for s in sources if s["report_date"] == period), None)
        if source is None:
            warnings.append(f"{code} 的报告期 {period} 不在归档文档内，丢弃")
            continue
        quote = str(row.get("quote") or "").strip()
        if not quote_in_text(quote, source["text"]):
            warnings.append(f"{code} 引文未在原文中找到，丢弃")
            continue
        # 引文必须包含所填数字：表格行常并列本期/上期两列，只查引文存在会错配。
        # 数字从原始引文提取：若先去空白，相邻两列金额会粘成一个数（如 28,573,949.37 40,436,892.68）。
        parsed_value = parse_number(row.get("value"))
        number_tokens = re.findall(r"\d[\d,]*\.?\d*", quote)
        quote_numbers = [float(token.replace(",", "")) for token in number_tokens]
        if not quote_numbers:
            quote_numbers = [
                float(token.replace(",", ""))
                for token in re.findall(r"\d[\d,]*\.?\d*", _normalized(quote))
            ]
        if parsed_value is None or not any(abs(number - parsed_value) < 1e-6 for number in quote_numbers):
            warnings.append(f"{code} 引文中未出现所填数字 {row.get('value')}，丢弃")
            continue
        numbers = quote_numbers
        if FIELD_BY_CODE[code].bridge_key.startswith("fcf_"):
            # 附注表格行本期列在前、上期列在后；所填数字必须是引文里的第一个金额列，
            # 否则就是错取了上期列。附注序号（如「40、财务费用」）不是金额，先跳过小整数。
            amounts = [
                n for n in numbers
                if n >= 1000 and not (n.is_integer() and 1900 <= n <= 2100)
            ]
            if len(amounts) >= 2 and abs(amounts[0] - parsed_value) >= 1e-6:
                warnings.append(f"{code} 所填值不是引文中的本期列（疑似取到上期数），丢弃")
                continue
        elif len(numbers) >= 3 and not any(
            marker in quote for marker in ("本期", "上年", "截至", "期末", "於二零", "于二零")
        ):
            warnings.append(f"{code} 引文含多个金额且无期间列标识，丢弃")
            continue
        try:
            amount = normalize_amount(row.get("value"), str(row.get("unit") or "元"), str(row.get("currency") or "CNY"))
        except ValueError as exc:
            warnings.append(f"{code} 金额/币种无效：{exc}")
            continue
        if amount.get("unit_note"):
            warnings.append(f"{code} {amount['unit_note']}")
        if amount["fx_fallback"]:
            warnings.append(f"{code} 汇率使用回退值：{amount['fx_source']}")
        items.append({
            "field_code": code,
            "bridge_key": FIELD_BY_CODE[code].bridge_key,
            "report_date": period,
            "report_type": source["report_type"].value,
            **amount,
            "quote": quote[:500],
            "source_doc": source["title"],
            "source_path": source["path"],
        })

    # 同一字段同一期间只留一条：优先引文更长（信息更完整）的记录。
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item["field_code"], item["report_date"])
        current = deduped.get(key)
        if current is None or len(item["quote"]) > len(current["quote"]):
            deduped[key] = item
    return list(deduped.values()), warnings


def extract_financial_fields(
    symbol: str,
    db_path: Path | None = None,
    *,
    max_docs: int | None = None,
    max_chars: int | None = None,
    annual_only: bool = False,
) -> dict[str, Any]:
    """从归档财报原文抽取非标准口径；返回纯 dict，不写库。"""
    settings = load_settings()
    max_docs = int(max_docs if max_docs is not None else settings.get("financial_extract.max_docs", 2))
    max_chars = int(max_chars if max_chars is not None else settings.get("financial_extract.max_chars_per_doc", 12000))
    result: dict[str, Any] = {
        "symbol": symbol, "items": [], "warnings": [], "sources": [],
        "source": "unavailable",
    }
    if not has_live_llm():
        result["reason"] = "未配置在线 LLM，跳过原文抽取"
        return result

    docs = _load_documents(symbol, db_path, annual_only=annual_only)
    sources: list[dict[str, Any]] = []
    for doc in docs:
        if len(sources) >= max_docs:
            break
        local = doc.get("local_path")
        if not local:
            continue
        txt = Path(local).with_suffix(".txt")
        if not txt.exists():
            continue
        try:
            text = txt.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if is_garbled_text(text):
            continue
        report_date, report_type = period_to_report_date(doc.get("period"), doc.get("doc_type"))
        if not report_date:
            continue
        snippet = _select_snippets(text, max_chars)
        if not snippet:
            continue
        sources.append({
            "report_date": report_date,
            "report_type": report_type,
            "doc_type": doc.get("doc_type"),
            "title": doc.get("title") or txt.stem,
            "path": str(txt),
            "text": snippet,
        })
    result["sources"] = [
        {"report_date": s["report_date"], "doc_type": s["doc_type"], "title": s["title"], "path": s["path"]}
        for s in sources
    ]
    if not sources:
        result["reason"] = "没有可用的财报原文片段"
        return result

    try:
        raw = chat_json(
            [
                ChatMessage("system", load_prompt("financial_extract")),
                ChatMessage("user", _build_user_prompt(symbol, sources)),
            ],
            max_tokens=int(settings.get("financial_extract.max_tokens", 2000)),
        )
    except (LlmError, LlmConfigError, FileNotFoundError) as exc:
        result["reason"] = f"LLM 抽取失败：{type(exc).__name__}"
        return result

    items, warnings = _normalize_items(raw, sources)
    result.update({"items": items, "warnings": warnings, "source": "llm"})
    return result


def latest_extracted_values(
    conn: sqlite3.Connection,
    stock_id: int,
    field_codes: tuple[str, ...] | list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """读取各字段最新报告期的抽取值；标准口径优先，调用方自行决定是否回退。"""
    codes = tuple(field_codes or FIELD_BY_CODE.keys())
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"SELECT field_code, report_date, report_type, value_cny, original_value, currency, "
        f"unit, fx_rate, fx_source, quote, source_doc, fetched_at "
        f"FROM extracted_financial_items WHERE stock_id = ? AND field_code IN ({placeholders}) "
        f"AND status = 'verified' ORDER BY report_date DESC",
        (stock_id, *codes),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        if data["field_code"] not in latest:
            latest[data["field_code"]] = data
    return latest


def bridge_value_from_extracted(extracted: dict[str, dict[str, Any]], bridge_key: str) -> float | None:
    """按桥接档位取抽取值；同档多字段（存款）相加，全部缺失返回 None。"""
    values = [
        float(item["value_cny"])
        for field in FIELDS
        if field.bridge_key == bridge_key
        and (item := extracted.get(field.code))
        and item.get("value_cny") is not None
    ]
    return sum(values) if values else None


def latest_extracted_for_bridge(
    extracted: dict[str, dict[str, Any]], bridge_key: str
) -> dict[str, Any] | None:
    """取桥接档位中最新的原始抽取记录，用于标注来源与报告期。"""
    candidates = [
        item for field in FIELDS
        if field.bridge_key == bridge_key and (item := extracted.get(field.code))
    ]
    return max(candidates, key=lambda item: str(item.get("report_date") or "")) if candidates else None
