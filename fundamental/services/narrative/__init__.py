"""叙述生成门面：对外暴露三个生成入口。

内部按职责拆分：_common（格式化/提示词/缓存）、_data（补充数据加载）、
_overview / _score / _valuation（三类叙述的事实构建、渲染与生成）。
"""

from __future__ import annotations

from ._overview import _render_template, generate_overview_narrative
from ._score import _score_facts, _score_template, generate_score_narrative
from ._valuation import (
    _valuation_facts,
    _valuation_template,
    generate_valuation_narrative,
)

__all__ = [
    "generate_overview_narrative",
    "generate_score_narrative",
    "generate_valuation_narrative",
]
