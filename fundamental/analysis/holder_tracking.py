"""牛散/机构持仓变化追踪。"""

from __future__ import annotations

from fundamental.models import CirculatingHolder, HolderPosition


def track_holder(
    holder_name: str,
    symbol: str,
    positions: list[CirculatingHolder],
) -> list[HolderPosition]:
    ordered = sorted(positions, key=lambda item: item.report_date)
    results: list[HolderPosition] = []
    previous: CirculatingHolder | None = None
    for position in ordered:
        change_num = (
            position.hold_num - previous.hold_num
            if position.hold_num is not None
            and previous is not None
            and previous.hold_num is not None
            else None
        )
        change_ratio = (
            position.hold_ratio - previous.hold_ratio
            if position.hold_ratio is not None
            and previous is not None
            and previous.hold_ratio is not None
            else None
        )
        results.append(
            HolderPosition(
                holder_name=holder_name,
                holder_category=position.holder_category,
                symbol=symbol,
                report_date=position.report_date,
                hold_num=position.hold_num,
                hold_ratio=position.hold_ratio,
                change_num=change_num,
                change_ratio=change_ratio,
            )
        )
        previous = position
    return results
