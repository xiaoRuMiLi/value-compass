"""HotspotPort：市场热点契约。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import Hotspot


class HotspotPort(Protocol):
    def fetch_hotspots(self, date: str | None = None) -> list[Hotspot]: ...
