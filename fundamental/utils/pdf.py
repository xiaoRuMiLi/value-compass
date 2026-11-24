"""PDF 文本提取（含港交所中文财报 CID 乱码还原）。

部分港交所中文财报（如小米集团中期报告）的嵌入字体是 Identity-H 子集字体，
**没有 /ToUnicode CMap**。MuPDF / pypdf 只能把字形 ID（CID）直接当作 Unicode
码点输出，正文会变成「㛮暈⛚㕾2024⸶3㛊28㖧」这类 CJK 扩展区/符号区乱码。
此时 PDF 内嵌字体连 cmap 表都没有，靠字体本身无法还原。

实测这类 CID 与真实 Unicode 之间是**固定线性偏移**（``Unicode = CID + offset``）：
同一份 PDF 内所有子集字体共用一个 offset（例：小米中报为 12350，
``㛮`` U+36EE + 12350 = U+672C「本」）。有 ToUnicode 的封面字体、西文
DIN 字体输出正常，不能被误改。因此本模块按 span 的字体分组：先识别乱码
字体、自动推断 offset，再只还原这些 span；正常 PDF / 正常字体完全走原路径，
输出逐字不变。

偏移不硬编码：优先用「四位年份后紧跟的字符＝年」锚点推断，再用高频汉字
打分校验；锚点缺失时才做小区间扫描。这样换一家公司、换一套字体也能自适应。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# 正常中文文本里几乎不会出现的码点区间（CJK 扩展 A、符号与部首区等）。
# U+3000-U+303F 是正常中文标点，U+FF00-U+FFEF 是全角标点，均不计入。
_GARBLED_RATIO = 0.15


def _suspect_ratio(text: str) -> float:
    """疑似 CID 字符占**全部**字符的比例。

    分母必须包含 ASCII：英文版财报正文几乎全是 ASCII，若只按非 ASCII 计，
    几个弯引号就会把比例放大成「乱码」，导致正常原文被错误拒绝。
    """
    if not text:
        return 0.0
    bad = 0
    for c in text:
        o = ord(c)
        if 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            continue  # 正常中文标点 / 全角标点
        if 0x2000 <= o < 0x4E00:
            bad += 1
    return bad / len(text)


def is_garbled_text(text: str) -> bool:
    """文本层是否为「CID 被当成 Unicode」的乱码。

    同时要求疑似字符的绝对数量，避免短文本里几个符号就触发误判。
    """
    value = text or ""
    if len(value) < 200:
        return False
    return _suspect_ratio(value) > _GARBLED_RATIO and sum(
        1 for c in value
        if 0x2000 <= ord(c) < 0x4E00
        and not (0x3000 <= ord(c) <= 0x303F)
    ) >= 200


# 高频汉字，用于校验/兜底推断 offset。乱码文本的字符加错偏移后虽也落在
# CJK 区，但几乎全是生僻字；正确偏移会大量命中这些常用字。
_COMMON_HANZI = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发"
    "年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如"
    "水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然"
    "前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但"
    "质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五"
    "果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组"
    "见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即"
    "保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清"
    "己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八"
    "离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观"
    "越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响"
    "约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯"
    "院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听"
    "该铁价严龙飞"
)

_YEAR_ANCHOR = re.compile(r"(\d{4})\s*([^\x00-\x7f])")


def _score_offset(sample: str, offset: int) -> int:
    hit = 0
    for c in sample:
        o = ord(c)
        if o < 128:
            continue
        n = o + offset
        if 0x4E00 <= n <= 0x9FFF and chr(n) in _COMMON_HANZI:
            hit += 1
    return hit


def infer_cid_offset(text: str) -> int | None:
    """从乱码文本推断 CID→Unicode 偏移；推断不出返回 None。"""
    sample = "".join(
        c for c in text[:6000]
        if ord(c) >= 128 and not (0x3000 <= ord(c) <= 0x303F)
    )
    if len(sample) < 20:
        return None
    threshold = max(8, len(sample) // 50)

    # 1) 「2024年」锚点：紧跟四位年份、反复出现的同一个字符通常就是「年」。
    #    财报里年报标题、报告期、日期都会大量出现，命中率极高且开销很小。
    followers = Counter(m.group(2) for m in _YEAR_ANCHOR.finditer(text))
    for ch, count in followers.most_common(5):
        if count < 2:
            break
        offset = ord("年") - ord(ch)
        if 0 < offset < 30000 and _score_offset(sample, offset) >= threshold:
            return offset

    # 2) 兜底：在经验区间内扫描，用高频汉字命中数选最优。样本截短以控开销。
    short = sample[:1200]
    threshold = max(5, len(short) // 50)
    best_offset: int | None = None
    best_score = 0
    for offset in range(8000, 16001):
        score = _score_offset(short, offset)
        if score > best_score:
            best_offset, best_score = offset, score
    return best_offset if best_score >= threshold else None


def apply_cid_offset(text: str, offset: int) -> str:
    """按偏移还原 CID 乱码；ASCII 与无法落到中文区的字符保持原样。"""
    out: list[str] = []
    for c in text:
        o = ord(c)
        if o < 128:
            out.append(c)
            continue
        n = o + offset
        out.append(chr(n) if 0x3000 <= n <= 0x9FFF else c)
    return "".join(out)


def _page_font_texts(page: Any) -> dict[str, str]:
    """按字体汇总一页的文本，用于识别哪些字体缺 ToUnicode。"""
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return {}
    by_font: dict[str, list[str]] = defaultdict(list)
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                by_font[span.get("font", "")].append(span.get("text", ""))
    return {font: "".join(parts) for font, parts in by_font.items()}


def _apply_char_offsets(text: str, offsets: dict[str, int]) -> str:
    """在原始文本上原地替换 CID 字符，保留 get_text() 的换行与空白结构。"""
    out: list[str] = []
    for c in text:
        offset = offsets.get(c)
        if not offset:
            out.append(c)
            continue
        n = ord(c) + offset
        out.append(chr(n) if 0x3000 <= n <= 0x9FFF else c)
    return "".join(out)


def extract_pdf_text(path: str | Path) -> str:
    """提取 PDF 正文。港交所公告的嵌入字体 pypdf 解不出中文，优先 PyMuPDF。

    正常 PDF 直接返回 ``page.get_text()``，输出逐字不变；仅当整份文本被判定
    为 CID 乱码时，才按字体推断偏移并建立「字符→偏移」映射，在原始文本上
    原地替换。这样既不会误伤有 ToUnicode 的封面/西文字体，也不会破坏原有
    分行结构（分段挖掘依赖它切段）。
    """
    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            raws: list[str] = []
            for page in doc:
                try:
                    raws.append(page.get_text() or "")
                except Exception:  # noqa: BLE001
                    raws.append("")
            joined = "\n".join(raws)
            if not joined.strip():
                raise ValueError("PyMuPDF 未提取到文本")

            if not is_garbled_text(joined):
                return joined

            # 按字体缓存推断结果（整份文档只算几次），但字符映射要**按页独立**：
            # 封面正常字体的汉字码点可能与正文 CID 值撞车，若用全局映射会把
            # 封面的「中」误改成「繫」。逐页只收集该页乱码字体的字符即可避开。
            font_offsets: dict[str, int | None] = {}
            repaired_pages: list[str] = []
            for page, raw in zip(doc, raws):
                page_offsets: dict[str, int] = {}
                for font, text in _page_font_texts(page).items():
                    if font not in font_offsets:
                        before = _suspect_ratio(text)
                        offset = infer_cid_offset(text) if before > _GARBLED_RATIO else None
                        if offset and _suspect_ratio(apply_cid_offset(text, offset)) >= before * 0.5:
                            offset = None  # 修复无效，宁可不改
                        font_offsets[font] = offset
                    offset = font_offsets[font]
                    if not offset:
                        continue
                    for c in text:
                        if ord(c) >= 128:
                            page_offsets.setdefault(c, offset)
                repaired_pages.append(
                    _apply_char_offsets(raw, page_offsets) if page_offsets else raw
                )
            if not any(font_offsets.values()):
                return joined
            return "\n".join(repaired_pages)
    except Exception:  # noqa: BLE001
        pass

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


# 巨潮检索里「XX年度报告披露的提示性公告」只有一两页"报告已于X日披露"的声明，
# 不含任何财务数据或管理层讨论。若把它当财报正文，逐段通读只能读到空壳：
# 既拿不到原文证据，还会让原文守卫误判覆盖充足。
_SHELL_TITLE_WORDS = ("提示性公告", "披露的提示", "披露提示", "提示公告")

# 财报正文抽出后至少有数千字；低于此阈值视为抽取失败或公告壳
MIN_SOURCE_TEXT_CHARS = 2000


def is_source_title_usable(title: str) -> bool:
    """标题级过滤：披露提示性公告不作为可取证原文。"""
    return not any(word in (title or "") for word in _SHELL_TITLE_WORDS)


def is_source_text_usable(text: str, title: str = "") -> bool:
    """文本级过滤：必须是财报正文，且不能是 CID 乱码。

    乱码文本即使长度够也不能入库：否则会进分段挖掘，让 LLM 对着
    「㛮暈⛚㕾」生成看似合理的中文结论（幻觉式解读）。
    """
    return (
        is_source_title_usable(title)
        and len((text or "").strip()) >= MIN_SOURCE_TEXT_CHARS
        and not is_garbled_text(text or "")
    )
