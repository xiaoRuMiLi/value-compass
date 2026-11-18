"""诚信记录精化：把朴素规则里的正面承诺纠正为正类。"""

from __future__ import annotations

from fundamental.models import BehaviorType, IntegrityCategory, IntegrityRecord, Severity

_POSITIVE_PROMISE_KEYWORDS = ("不减持", "不质押", "分红", "回购", "增持", "稳定股价")


def refine_integrity(records: list[IntegrityRecord]) -> list[IntegrityRecord]:
    refined: list[IntegrityRecord] = []
    for record in records:
        if record.category == IntegrityCategory.BROKEN_PROMISE and any(
            keyword in record.title for keyword in _POSITIVE_PROMISE_KEYWORDS
        ):
            refined.append(
                IntegrityRecord(
                    record_date=record.record_date,
                    behavior_type=BehaviorType.POSITIVE,
                    category=IntegrityCategory.PROMISE_FULFILLED,
                    title=record.title,
                    description=record.description,
                    severity=Severity.LOW,
                    source=record.source,
                    source_url=record.source_url,
                )
            )
        else:
            refined.append(record)
    return refined
