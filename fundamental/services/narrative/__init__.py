"""叙述生成门面：对外暴露五个生成入口。

内部按职责拆分：_common（格式化/提示词/缓存）、_data（补充数据加载）、
_overview / _score / _valuation / _masters / _valuation_params（事实构建、渲染与生成）。
"""

from __future__ import annotations

from ._masters import generate_masters_narrative
from ._overview import generate_overview_narrative
from ._score import generate_score_narrative
from ._valuation import generate_valuation_narrative
from ._valuation_params import generate_valuation_params

__all__ = [
    "generate_masters_narrative",
    "generate_overview_narrative",
    "generate_score_narrative",
    "generate_valuation_narrative",
    "generate_valuation_params",
]
