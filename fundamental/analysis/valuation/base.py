"""估值方法接口。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import ValuationResult


class ValuationMethod(Protocol):
    name: str

    def evaluate(self, **inputs: object) -> ValuationResult: ...
