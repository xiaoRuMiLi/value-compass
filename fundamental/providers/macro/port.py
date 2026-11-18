"""MacroPort：宏观指标数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import MacroIndicator


class MacroPort(Protocol):
    def fetch_macro_indicators(
        self, codes: list[str] | None = None, start: str | None = None, end: str | None = None
    ) -> list[MacroIndicator]: ...
