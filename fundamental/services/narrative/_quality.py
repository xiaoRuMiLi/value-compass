"""摘录信息密度打分（叙述层共享）。

财报摘录排序的单一口径：公司原因说明类短句优先，裸数字表格行沉底。
被 _forensics（证据合并）与 _report_mining（挖掘结果截断）共同使用，
保证两处截断顺序一致，避免低质量数字行占满名额。
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DIGIT_RE = re.compile(r"\d")

# 公司解释经营波动的措辞：命中越多，摘录的信息密度越高
_EXPLANATION_HINTS = (
    "变动原因说明", "变动的情况、原因", "主要原因", "主要是", "主要系",
    "得益于", "受……影响", "由于", "影响", "同比", "增长", "下降", "增加", "减少",
)


def excerpt_quality(text: str) -> int:
    """给摘录打分：公司原因说明类短句优先，裸数字表格行沉底。

    排序只影响展示顺序与送入 LLM 的先后，不改写、不丢弃任何一条原文。
    """
    cjk = len(_CJK_RE.findall(text))
    digits = len(_DIGIT_RE.findall(text))
    score = 0
    for hint in _EXPLANATION_HINTS:
        if hint in text:
            score += 3
    # 中文叙述成分越多越像"公司自己的解释"；纯数字行中文极少
    score += min(cjk // 6, 8)
    # 数字扎堆（逗号分隔的大额数字）是表格行的典型特征
    if digits >= 8 or text.count(",") >= 3:
        score -= 12
    # 过短的碎片几乎无信息
    if cjk < 6:
        score -= 6
    return score


def is_numeric_quote(text: str) -> bool:
    """判定是否为裸数字表格行（几乎无中文叙述、数字扎堆）。

    用于新线索筛选：带文字结论的线索优先，纯数字行只作辅助证据保留，
    避免季报表格数字占满名额、把年报里带说明的实质线索挤掉。
    """
    cjk = len(_CJK_RE.findall(text))
    digits = len(_DIGIT_RE.findall(text))
    return digits >= 8 or text.count(",") >= 3 or (cjk < 6 and digits >= 4)
