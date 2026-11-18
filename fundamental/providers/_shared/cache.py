"""文件式响应缓存：data/cache 下按 URL 哈希存储，带 TTL。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from fundamental.utils import paths


class ResponseCache:
    def __init__(self, cache_dir: Path | None = None, default_ttl: int = 3600) -> None:
        self.cache_dir = cache_dir or paths.cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl

    @staticmethod
    def key(url: str, params: Optional[dict[str, Any]] = None) -> str:
        raw = url
        if params:
            raw = f"{url}?{urlencode(sorted(params.items()))}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, cache_key: str, ttl: Optional[int] = None) -> Optional[Any]:
        path = self.cache_dir / f"{cache_key}.json"
        if not path.exists():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(meta.get("fetched_at", 0)) > (ttl or self.default_ttl):
            return None
        return meta.get("body")

    def set(self, cache_key: str, body: Any, ttl: Optional[int] = None) -> None:
        meta = {
            "key": cache_key,
            "fetched_at": time.time(),
            "ttl": ttl or self.default_ttl,
            "body": body,
        }
        path = self.cache_dir / f"{cache_key}.json"
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
