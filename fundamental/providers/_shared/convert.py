"""数值转换：东财原始值 → 统一 float/百分比。"""

from __future__ import annotations


def to_float(value: object) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_percent(value: object) -> float | None:
    """把 0-1 的小数比率转成百分比（如 0.862 -> 86.2）。"""
    number = to_float(value)
    if number is None:
        return None
    return round(number * 100, 4)
