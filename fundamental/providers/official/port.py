"""OfficialPort：官网数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import OfficialUpdate


class OfficialPort(Protocol):
    def fetch_official_updates(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[OfficialUpdate]: ...
