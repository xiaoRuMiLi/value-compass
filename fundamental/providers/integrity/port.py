"""IntegrityPort：诚信记录契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import IntegrityRecord


class IntegrityPort(Protocol):
    def fetch_integrity_records(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[IntegrityRecord]: ...
