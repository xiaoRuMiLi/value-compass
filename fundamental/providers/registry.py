"""能力注册表：capability + source → 实现类，按 config/providers.toml 选源。"""

from __future__ import annotations

import importlib.util
from typing import Any, Callable, Optional

from fundamental.providers import ProviderConfigError

_REGISTRY: dict[str, dict[str, type]] = {}


def _import_if_exists(module_name: str) -> None:
    if importlib.util.find_spec(module_name) is not None:
        importlib.import_module(module_name)


def _ensure_loaded(capability: str, source: Optional[str] = None) -> None:
    """按需导入适配器模块，避免调用方忘记 import 导致“能力未注册”。"""
    if capability in _REGISTRY and (source is None or source in _REGISTRY[capability]):
        return
    if source is not None:
        _import_if_exists(f"fundamental.providers.{capability}.{source}")
    if capability not in _REGISTRY:
        _import_if_exists(f"fundamental.providers.{capability}")


def register(capability: str, source: str) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        _REGISTRY.setdefault(capability, {})[source] = cls
        return cls

    return decorator


def get_provider_class(capability: str, source: Optional[str] = None) -> type:
    _ensure_loaded(capability, source)
    sources = _REGISTRY.get(capability)
    if not sources:
        raise ProviderConfigError(f"能力未注册: {capability}")
    if source is None:
        return next(iter(sources.values()))
    if source not in sources:
        raise ProviderConfigError(f"能力 {capability} 未注册数据源: {source}")
    return sources[source]


def registered_capabilities() -> list[str]:
    return sorted(_REGISTRY)


def registered_sources(capability: str) -> list[str]:
    return list(_REGISTRY.get(capability, {}))


def primary_source(settings: Any, capability: str) -> str:
    source = settings.get(f"providers.{capability}.primary")
    if not source:
        raise ProviderConfigError(f"providers.toml 缺少 providers.{capability}.primary")
    return str(source)


def make_provider(settings: Any, capability: str) -> Any:
    source = primary_source(settings, capability)
    return get_provider_class(capability, source)()


def make_provider_chain(settings: Any, capability: str) -> list[Any]:
    """按 primary → fallback 顺序实例化 provider；未配置的 fallback 自动忽略。

    数据源实现类通过模块导入时的 @register 装饰器注册，因此调用方需确保相应
    provider 模块已被导入（例如 sync.py 中显式 import）。
    """
    sources = [primary_source(settings, capability)]
    fallback = settings.get(f"providers.{capability}.fallback")
    if fallback:
        sources.append(str(fallback))
    providers: list[Any] = []
    seen: set[str] = set()
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        providers.append(get_provider_class(capability, source)())
    return providers
