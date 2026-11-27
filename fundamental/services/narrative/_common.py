"""叙述生成共享工具：数值格式化、LLM 输入构造、本地缓存。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.utils import paths

_NARRATIVE_VERSION = 10

# 大师档案/输入结构升级时递增，只让大师解读缓存失效，不影响其他叙述缓存。
_MASTERS_PROMPT_VERSION = 6


def _plain(value: Any) -> Any:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else value


def _fmt_yi(value: Any) -> str:
    return "数据缺失" if value is None else f"{value / 1e8:.2f} 亿元"


def _fmt_pct(value: Any) -> str:
    return "数据缺失" if value is None else f"{value:.2f}%"


def _fmt_ratio(value: Any) -> str:
    return "数据缺失" if value is None else f"{value * 100:.2f}%"


def _fmt_price(value: Any) -> str:
    return "数据缺失" if value is None else f"{value:.2f} 元"


def _build_prompt(facts: dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2, default=str)


def _max_tokens(name: str, default: int) -> int:
    """输出 token 上限，读 ``[narrative] {name}_max_tokens``；配置缺失时用默认值。"""

    try:
        return int(load_settings().get(f"narrative.{name}_max_tokens", default))
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_dir() -> Path:
    directory = paths.cache_dir() / "narrative"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _overview_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"{symbol}.json"


def _score_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"score_{symbol}.json"


def _valuation_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"valuation_{symbol}.json"


def _masters_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"masters_{symbol}.json"


def _valuation_params_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"valuation_params_{symbol}.json"


def _forensics_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"forensics_{symbol}.json"


def _financial_trend_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"financial_trend_{symbol}.json"


def _balance_breakdown_cache_path(symbol: str) -> Path:
    return _cache_dir() / f"balance_breakdown_{symbol}.json"


def _read_cache(path: Path, ttl_days: float) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != _NARRATIVE_VERSION:
        return None
    generated_at = data.get("generated_at")
    if generated_at:
        try:
            generated = datetime.fromisoformat(generated_at)
        except ValueError:
            return None
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - generated).total_seconds() > ttl_days * 86400:
            return None
    return data


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["version"] = _NARRATIVE_VERSION
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
