"""配置加载：config/*.toml 合并 + 环境变量覆盖。"""

from __future__ import annotations

import os
import tomllib
from typing import Any

from fundamental.utils import paths

_CONFIG_ORDER = (
    "default.toml",
    "dev.toml",
    "providers.toml",
    "products.toml",
    "official.toml",
    "famous_traders.toml",
)

_ENV_OVERRIDES = {
    "VALUE_COMPASS_HOST": ("web", "host"),
    "VALUE_COMPASS_PORT": ("web", "port"),
}


class Settings:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def as_dict(self) -> dict[str, Any]:
        return self._data


def load_settings() -> Settings:
    data: dict[str, Any] = {}
    for filename in _CONFIG_ORDER:
        path = paths.config_dir() / filename
        if path.exists():
            with path.open("rb") as handle:
                _deep_merge(data, tomllib.load(handle))
    _apply_env_overrides(data)
    return Settings(data)


def _deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _apply_env_overrides(data: dict) -> None:
    for env_name, path in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        node: dict = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = _cast(raw)


def _cast(raw: str) -> Any:
    if raw.isdigit():
        return int(raw)
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return raw
