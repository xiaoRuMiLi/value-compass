"""大模型调用薄封装：配置驱动的懒加载单例，面向全项目提供统一入口。"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

from fundamental.config import Settings, load_settings
from fundamental.models import ChatMessage, ChatResult
from fundamental.providers import ProviderConfigError
from fundamental.providers.llm.port import LlmConfigError, LlmError, LlmPort
from fundamental.providers.registry import get_provider_class

_llm: LlmPort | None = None
_logger = logging.getLogger(__name__)

_CJK_RANGES = (("\u3400", "\u4dbf"), ("\u4e00", "\u9fff"), ("\uf900", "\ufaff"))


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 约 1 字 1 token，其余约 4 字符 1 token。

    只服务输入预算监控。真实 tokenizer 依模型而异，这里不做精确依赖，
    宁可粗估也不引入额外分词库。
    """

    if not text:
        return 0
    cjk = sum(1 for ch in text if any(lo <= ch <= hi for lo, hi in _CJK_RANGES))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def input_budget(settings: Settings | None = None) -> int:
    """单次请求的输入 token 软预算，由 ``llm.max_input_tokens`` 配置。"""

    settings = settings or load_settings()
    try:
        return int(settings.get("llm.max_input_tokens", 32000))
    except (TypeError, ValueError):
        return 32000


def _messages_tokens(messages: Sequence[ChatMessage]) -> int:
    # 每条消息另有约 4 token 的角色标记与分隔开销
    return sum(estimate_tokens(m.content) + 4 for m in messages)


def _truncate_middle(text: str, ratio: float) -> str:
    target = max(200, int(len(text) * ratio))
    if target >= len(text):
        return text
    head = int(target * 0.75)
    tail = target - head
    return text[:head] + "\n...[输入过长，已省略中间部分]...\n" + text[-tail:]


def _guard_input(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """输入预算软约束：超预算只告警；超 2 倍才做中段截断兜底。

    不在预算处硬截断，是因为叙述层传入的是 JSON，截断会破坏结构、诱发幻觉，
    与「可靠可信」的目标冲突。超 2 倍时 API 本就会拒绝，此时截断仅作兜底。
    """

    items = list(messages)
    budget = input_budget()
    estimated = _messages_tokens(items)
    if estimated <= budget:
        return items

    _logger.warning(
        "LLM 输入估算 %d token，超过预算 %d（llm.max_input_tokens）；"
        "如需更长上下文请调大该值，或调小 research/intel/masters/valuation 的字数配置。",
        estimated,
        budget,
    )
    if estimated <= budget * 2:
        return items

    index = max(range(len(items)), key=lambda i: len(items[i].content))
    target = items[index]
    ratio = (budget * 0.9) / max(1, estimated)
    items[index] = ChatMessage(target.role, _truncate_middle(target.content, ratio))
    _logger.error(
        "LLM 输入估算 %d token 已超 2 倍预算 %d，对最长消息做中段截断；"
        "若频繁出现请调大 llm.max_input_tokens。",
        estimated,
        budget,
    )
    return items


def _resolve_api_key(api_key_env: str) -> str | None:
    return os.environ.get(api_key_env) if api_key_env else None


def _setting(section: dict[str, Any], settings: Settings, name: str, default: Any) -> Any:
    """按 专项配置 → 全局配置 → 默认值 的顺序取值。

    用 ``is not None`` 而非 ``or`` 判断，避免把合法的 0 / 0.0 / False 当成缺失值
    （例如 temperature=0 是常见的确定性输出配置，不能被回退成 0.2）。
    """

    for value in (section.get(name), settings.get(f"llm.{name}")):
        if value is not None:
            return value
    return default


def _build(settings: Settings) -> LlmPort:
    provider_name = str(_setting({}, settings, "provider", "mock"))
    section = settings.get(f"llm.providers.{provider_name}")
    if not isinstance(section, dict):
        raise LlmConfigError(f"llm.toml 缺少 [llm.providers.{provider_name}] 配置")

    adapter = str(section.get("adapter") or "openai_compat")
    try:
        provider_cls = get_provider_class("llm", adapter)
    except ProviderConfigError as exc:
        raise LlmConfigError(f"未知的大模型适配器: {adapter}") from exc

    api_key = _resolve_api_key(str(section.get("api_key_env") or ""))
    if adapter != "mock" and not api_key:
        raise LlmConfigError(
            f"模型 {provider_name} 需要 API Key，请设置环境变量 {section.get('api_key_env')}"
        )

    return provider_cls(
        base_url=str(section.get("base_url") or ""),
        api_key=api_key,
        model=str(_setting(section, settings, "model", "gpt-4.1-mini")),
        temperature=float(_setting(section, settings, "temperature", 0.2)),
        max_tokens=int(_setting(section, settings, "max_tokens", 2048)),
        timeout_seconds=float(_setting(section, settings, "timeout_seconds", 120)),
    )


def get_llm(settings: Settings | None = None) -> LlmPort:
    """返回当前配置下的大模型实例；默认走懒加载单例。"""

    global _llm
    if settings is not None:
        return _build(settings)
    if _llm is None:
        _llm = _build(load_settings())
    return _llm


def chat(
    messages: Sequence[ChatMessage],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatResult:
    return get_llm().chat(_guard_input(messages), temperature=temperature, max_tokens=max_tokens)


def chat_json(
    messages: Sequence[ChatMessage],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Any:
    return get_llm().chat_json(_guard_input(messages), temperature=temperature, max_tokens=max_tokens)


def chat_optional(
    messages: Sequence[ChatMessage],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatResult | None:
    """失败即返回 None，供叙述增强场景做降级，不打断主流程。"""

    try:
        return chat(messages, temperature=temperature, max_tokens=max_tokens)
    except (LlmError, LlmConfigError):
        return None


def has_live_llm(settings: Settings | None = None) -> bool:
    """当前配置是否为可用的真实模型（非 mock，且已配置密钥）。"""

    try:
        provider = get_llm(settings)
    except LlmConfigError:
        return False
    return bool(getattr(provider, "is_live", False))
