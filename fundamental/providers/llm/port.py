"""LlmPort：大模型对话与结构化输出的输入输出契约。"""

from __future__ import annotations

from typing import Any, Protocol

from fundamental.models import ChatMessage, ChatResult
from fundamental.providers import ProviderConfigError, ProviderError


class LlmError(ProviderError):
    """大模型调用失败（网络、限流、服务端错误等）。"""


class LlmConfigError(ProviderConfigError):
    """大模型配置错误（缺少 API Key、未知 provider 等）。"""


class LlmPort(Protocol):
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...

    def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any: ...
