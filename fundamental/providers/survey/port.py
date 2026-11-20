"""SurveyPort：机构调研数据契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import SurveyRecord


class SurveyPort(Protocol):
    def fetch_surveys(self, symbol: str, limit: int = 10) -> list[SurveyRecord]: ...
