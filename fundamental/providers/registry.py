"""能力注册表：capability + source → 实现类，按 config/providers.toml 选源。"""

from __future__ import annotations

from typing import Any, Callable, Optional

_REGISTRY: dict[str, dict[str, type]] = {}


def register(capability: str, source: str) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        _REGISTRY.setdefault(capability, {})[source] = cls
        return cls

    return decorator


def get_provider_class(capability: str, source: Optional[str] = None) -> type:
    sources = _REGISTRY.get(capability)
    if not sources:
        raise KeyError(f"能力未注册: {capability}")
    if source is None:
        return next(iter(sources.values()))
    if source not in sources:
        raise KeyError(f"能力 {capability} 未注册数据源: {source}")
    return sources[source]


def registered_capabilities() -> list[str]:
    return sorted(_REGISTRY)


def registered_sources(capability: str) -> list[str]:
    return list(_REGISTRY.get(capability, {}))


def primary_source(settings: Any, capability: str) -> str:
    source = settings.get(f"providers.{capability}.primary")
    if not source:
        raise ValueError(f"providers.toml 缺少 providers.{capability}.primary")
    return str(source)


def make_provider(settings: Any, capability: str) -> Any:
    source = primary_source(settings, capability)
    return get_provider_class(capability, source)()
