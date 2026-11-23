"""财报原文证据检索：为每个风险点找到公司自己的官方说明。

设计约束（与取证扫描器一脉相承）：

1. **只检索、不判断**：本模块从已下载的财报 .txt 中切出相关段落返回，
   是否采信、如何解读交给 LLM。绝不改写公司原文，也不新增事实。
2. **关键词路线、离线可用**：不依赖向量库 / embedding / 网络，只用关键词
   匹配 + 段落切片。召回优先，上下文预算由调用方控制。
3. **报告期对齐**：优先取风险点所属报告期的财报原文（年报 MD&A 最全，
   季报有「变动原因说明」表），避免拿 2022 年的解释去印证 2025 年的异常。
4. **可溯源**：每条证据带来源文档、报告期、命中关键词，供前端展示与人工核对。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_source_title_usable

_logger = get_logger(__name__)

# 风险类型 → 检索关键词。按 (category, title) 的顺序匹配，命中任一关键词即算相关。
# 关键词取自财报「变动原因说明 / 管理层讨论与分析」的常见措辞。
_EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "增收不增利": ("营业收入", "营业总收入", "净利润", "毛利", "产品结构", "销量"),
    "利润增速显著背离营收": ("净利润", "非经常性", "投资收益", "公允价值", "净利润增长"),
    "营收增速骤变": ("营业收入", "营业总收入", "销量", "销售渠道", "产品结构", "价格"),
    "业绩拐点：高增长转负": ("净利润", "归母净利润", "变动原因", "主要是", "主要原因", "销量", "价格", "产品结构", "基数", "行业", "调整期", "需求"),
    "毛利率异动": ("毛利率", "营业成本", "产品结构", "成本", "销售价格"),
    "净利率异动": ("净利率", "净利润", "费用", "产品结构", "毛利率"),
    "ROE 显著变化": (
        "净资产收益率", "ROE", "净利润", "所有者权益", "分红",
        # 港股公告用繁体及「权益持有人应占盈利」口径，并常把原因写在同比段落
        "股本回報率", "權益持有人應佔盈利", "本公司權益持有人應佔盈利",
        "股東權益", "股息", "視同處置", "美團", "1,066",
        "非國際財務報告準則", "投資收益", "聯營公司", "減值撥備",
    ),
    "资产负债率突变": ("资产负债率", "负债", "资产", "存款", "借款"),
    "扣非净利显著低于归母净利": ("非经常性损益", "扣除非经常", "政府补助", "处置", "投资收益"),
    "利润未转化为经营现金流": ("经营活动产生的现金流量", "经营现金流", "应收账款", "存货", "回款"),
    "审计意见非标": ("审计意见", "保留意见", "无法表示意见", "非标准", "会计师事务所"),
    "连续多期亏损": ("亏损", "净利润", "经营", "持续经营", "营业收入"),
    "费用与成本结构同步异动": ("销售费用", "管理费用", "研发费用", "财务费用", "营业成本", "费用"),
    "费用率异动": ("销售费用", "管理费用", "研发费用", "财务费用", "营业成本"),
    "业绩预告与实际显著偏离": ("业绩预告", "预告", "净利润", "变动原因", "主要原因"),
    "监管与诚信记录线索": ("处罚", "监管", "问询", "占用", "违规", "更正"),
    "公告中出现风险类关键词": ("风险", "立案", "处罚", "问询", "减值", "更正"),
    "内部人净减持": ("减持", "股份变动", "股东", "交易"),
    "股权质押比例偏高": ("质押", "控股股东", "股份"),
    "业绩方向与股价反应背离": (
        "净利润", "营业收入", "变动原因", "主要原因", "经营情况",
        "主要由於", "同比增長", "同比下降", "權益持有人應佔盈利",
    ),
    "第三方资料出现负面表述": ("风险", "下滑", "承压"),
    # 资本运作与治理线索（财报原文里公司自己的说明）
    "异常突击分红": ("利润分配", "现金分红", "每股派现", "分红方案", "分红比例", "未分配利润"),
    "扩充股本": ("股本", "总股本", "注册资本", "资本公积", "转增", "增发", "限制性股票", "归属"),
    "异常资本支出": ("购建固定资产", "在建工程", "资本性支出", "投资活动", "投资项目", "产能", "募集资金"),
    "商誉显著增加（收购线索）": ("商誉", "收购", "并购", "合并成本", "可辨认净资产", "业绩承诺"),
    "新增收购": ("收购", "并购", "股权收购", "合并范围", "购买日", "交易对价", "业绩承诺"),
    "跨界投资": ("对外投资", "设立子公司", "参股", "新业务", "跨界", "投资标的"),
    "异常股权激励": ("股权激励", "限制性股票", "股票期权", "激励计划", "授予", "行权条件", "考核指标"),
    "关联交易": ("关联交易", "关联方", "关联自然人", "定价政策", "日常关联交易", "资金往来"),
}

# 通用兜底关键词：任何风险点都可尝试这些（公司解释经营波动的高频词）
_FALLBACK_TERMS: tuple[str, ...] = (
    "变动原因", "主要原因", "主要是", "管理层讨论与分析", "经营情况讨论与分析",
    "营业收入", "净利润", "经营活动产生的现金流量",
    # 港股繁体口径：公司解释通常写成「该下降主要由於…」
    "主要由於", "主要反映", "同比增長", "同比下降", "管理層討論與分析",
    "權益持有人應佔盈利", "非國際財務報告準則", "視同處置", "美團",
)

# 财报里「公司对波动做说明」的黄金段落标记，命中即高优先级
_EXPLANATION_MARKERS: tuple[str, ...] = (
    "变动原因说明", "变动的情况、原因", "主要原因", "经营情况讨论与分析",
    "主营业务分析", "报告期内主要经营情况",
    "主要由於", "該下降主要由於", "主要反映", "管理層討論與分析",
)

# 章节边界：MD&A 通常从「第三节 管理层讨论与分析」到「第四节」；季报从「主要财务数据」开始
# 注意排除目录行（标题后跟一长串点线 + 页码），必须匹配正文中的真实章节标题
_SECTION_START = re.compile(
    r"^(第[三四][节節]\s*管理[层層][讨论論]{2}[與与]分析|管理[层層][讨论論]{2}[與与]分析|"
    r"財務回顧|一、主要财务数据|主要会计数据和财务指标)\s*$"
)
_SECTION_END = re.compile(r"^第[四五][节節]\s*(公司治理|重要事项|公司管治|重要事項)")


def _terms_for(signal: dict[str, Any]) -> tuple[str, ...]:
    """按 title 精确匹配 → category 模糊匹配 → 兜底关键词。"""
    title = str(signal.get("title") or "")
    if title in _EVIDENCE_TERMS:
        return _EVIDENCE_TERMS[title]
    for key, terms in _EVIDENCE_TERMS.items():
        if key and (key in title or title in key):
            return terms
    return _FALLBACK_TERMS


def _report_year(signal: dict[str, Any]) -> str | None:
    """从信号 period 里提取年份（如 '2024-12-31 年报' → '2024'）。"""
    match = re.search(r"(20\d{2})", str(signal.get("period") or ""))
    return match.group(1) if match else None


def _load_documents(symbol: str, db_path: Path | None) -> list[dict[str, Any]]:
    """从 documents 表读取已归档的财报文本路径（A 股巨潮 / 港股披露易）。"""
    conn = db.init_db(db_path)
    try:
        code, market = parse_code(symbol)
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            return []
        rows = repository.fetch_all(
            conn,
            "SELECT doc_type, period, title, local_path FROM documents WHERE stock_id = ?",
            (stock_id,),
        )
        # 披露提示性公告只有"报告已披露"的声明，读它取不到任何经营/财务表述，
        # 在入口处就挡掉，避免污染检索证据与逐段通读的文档池。
        return [dict(r) for r in rows if is_source_title_usable(str(r["title"] or ""))]
    finally:
        conn.close()


def parse_code(symbol: str) -> tuple[str, Any]:
    from fundamental.utils.symbols import parse_symbol

    return parse_symbol(symbol)


def _text_path(doc: dict[str, Any]) -> Path | None:
    """由 pdf 路径推出同名 .txt 路径；存在则返回。"""
    local = doc.get("local_path")
    if not local:
        return None
    txt = Path(local).with_suffix(".txt")
    return txt if txt.exists() else None


def _slice_sections(text: str) -> list[str]:
    """切出 MD&A / 变动原因相关段落，过滤页眉页脚与空行。"""
    lines = [line.strip() for line in text.splitlines()]
    start = None
    for i, line in enumerate(lines):
        if _SECTION_START.search(line):
            start = i
            break
    if start is None:
        section = lines
    else:
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if _SECTION_END.search(lines[i]):
                end = i
                break
        section = lines[start:end]

    # 合并成段落：以空行或章节标题断句，保留带数字/原因说明的行
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in section:
        if not line or re.fullmatch(r"\d+\s*/\s*\d+", line):
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        if re.match(r"^(第[一二三四五六七八九十]+节|[一二三四五六七八九十]+、|\(\d+\)|\d+\.)", line) and len(line) < 30:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            buf.append(line)
            continue
        buf.append(line)
    if buf:
        paragraphs.append(" ".join(buf))

    # 「XX变动原因说明：……」是公司对自己数据波动的逐条官方解释，最有价值。
    # 它们常被 PDF 提取后拼在变动表末尾，单独拆出来，避免被 420 字截断埋没。
    explanation_sentences: list[str] = []
    # 匹配「<科目名>变动原因说明：正文」；科目名是紧跟的一串中文/字母，边界处取完整词。
    # 用 finditer 而非 split，直接从"科目名首字"开始截取，避免把词首切掉。
    explain_pat = re.compile(r"[\u4e00-\u9fffA-Za-z]{2,20}变动原因说明[：:][^\n]*")
    for para in paragraphs:
        for m in explain_pat.finditer(para):
            explanation_sentences.append(m.group(0).strip())
        # 季报里的「变动的情况、原因」表：表头 + 原因行，整段保留
        if "变动的情况、原因" in para and len(para) <= 400:
            explanation_sentences.append(para.strip())
    # 官方说明段落优先排在前面，检索排序时同样得分能胜出
    return explanation_sentences + [p for p in paragraphs if len(p) >= 12]


# 公司解释句：港股公告常把「该下降主要由於…」埋在长段落末尾，
# 单独切出后才能在有限证据名额里被模型看到。
_EXPLANATION_SENTENCE = re.compile(
    r"[^。！？]{0,120}(?:該下降主要由於|主要由於|主要反映)[^。！？]{0,260}[。！？]?"
)


def _slice_explanations(text: str) -> list[str]:
    out: list[str] = []
    for match in _EXPLANATION_SENTENCE.finditer(text):
        sentence = re.sub(r"\s+", " ", match.group(0)).strip()
        if len(sentence) >= 16:
            out.append(sentence)
    return out


def _score_paragraph(paragraph: str, terms: tuple[str, ...]) -> int:
    """命中关键词数 + 黄金段落标记加权；精准的「变动原因说明」短句优先。

    大表格段落会命中很多关键词从而得分虚高，但它往往在截断后才出现原因说明。
    单条/少条的「XX变动原因说明：主要是……」才是公司最有价值的官方解释，
    因此给这类短句额外加权，确保它能进入 max_per_signal 的展示名额。
    """
    # 长段落关键词命中数天然虚高，封顶后让短解释句有机会胜出
    score = min(sum(1 for term in terms if term in paragraph), 6)
    if any(marker in paragraph for marker in _EXPLANATION_MARKERS):
        score += 3
    if "变动原因说明" in paragraph and len(paragraph) <= 260:
        score += 6
    if len(paragraph) <= 320 and any(
        marker in paragraph for marker in ("該下降主要由於", "主要由於", "主要反映")
    ):
        score += 8
    return score


def _pick_documents(
    docs: list[dict[str, Any]], year: str | None
) -> list[dict[str, Any]]:
    """选与报告期同年的财报；没有则退回最近的两年（年报优先）。"""
    if not docs:
        return []
    usable = [d for d in docs if _text_path(d)]
    if not usable:
        return []

    def doc_year(d: dict[str, Any]) -> int:
        try:
            return int(str(d.get("period") or "0")[:4])
        except ValueError:
            return 0

    if year:
        # 有报告期：按与目标的年份差排序，年报优先
        usable.sort(key=lambda d: (abs(doc_year(d) - int(year)), 0 if d.get("doc_type") == "annual_report" else 1))
        same = [d for d in usable if str(d.get("period")) == year]
        if same:
            # 同年的年报 + 季报都要（季报的「变动原因」表更细）
            return same[:3]
        return usable[:2]

    # 无报告期（如第三方/监管类信号）：取最近年份的年报，避免拿多年前的老解释
    usable.sort(key=lambda d: (-doc_year(d), 0 if d.get("doc_type") == "annual_report" else 1))
    return usable[:2]


def retrieve_evidence(
    symbol: str,
    signals: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
    max_per_signal: int = 2,
    max_chars: int = 420,
) -> dict[str, list[dict[str, str]]]:
    """为每个风险点检索公司原文证据。

    返回 ``{signal_id: [{text, source, period, keywords}]}``。
    检索失败 / 无文档时不抛错，返回空字典，由叙述层降级处理。
    """
    if not signals:
        return {}
    try:
        docs = _load_documents(symbol, db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财报原文检索加载文档失败 %s: %s", symbol, exc)
        return {}

    # 按年份缓存切片结果，避免重复读盘
    cache: dict[str, list[tuple[str, str, str]]] = {}
    result: dict[str, list[dict[str, str]]] = {}

    for signal in signals:
        sig_id = str(signal.get("id") or "")
        if not sig_id:
            continue
        year = _report_year(signal)
        terms = _terms_for(signal)
        hits: list[dict[str, str]] = []

        for doc in _pick_documents(docs, year):
            txt_path = _text_path(doc)
            if txt_path is None:
                continue
            period = str(doc.get("period") or "")
            # cache key 用文件路径：同一年可能有年报+多份季报，period 相同但内容不同
            cache_key = str(txt_path)
            if cache_key not in cache:
                try:
                    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    _logger.warning("读取财报文本失败 %s: %s", txt_path, exc)
                    cache[cache_key] = []
                    continue
                sections = _slice_sections(raw) + _slice_explanations(raw)
                seen_paras: set[str] = set()
                unique = []
                for para in sections:
                    if para in seen_paras:
                        continue
                    seen_paras.add(para)
                    unique.append((para, period, str(doc.get("title") or period)))
                cache[cache_key] = unique

            scored: list[tuple[int, str, str, str]] = []
            for para, p, title in cache[cache_key]:
                matched = [t for t in terms if t in para]
                # 必须有真实关键词命中；仅靠「变动原因说明」等标记加权不算证据，
                # 否则监管/股价类信号会被塞进无关的 MD&A 套话段落。
                if not matched:
                    continue
                score = _score_paragraph(para, terms)
                scored.append((score, para, p, ",".join(matched[:4])))
            scored.sort(key=lambda x: -x[0])
            for score, para, p, matched in scored[:max_per_signal]:
                if len(hits) >= max_per_signal:
                    break
                hits.append(
                    {
                        "text": para[:max_chars],
                        "source": title,
                        "period": p,
                        "keywords": matched,
                    }
                )
            if len(hits) >= max_per_signal:
                break

        if hits:
            result[sig_id] = hits

    return result
