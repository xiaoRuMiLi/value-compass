"""离线确定性实现，用于测试与无 API Key 的本地开发。"""

from __future__ import annotations

from typing import Any

from fundamental.models import ChatMessage, ChatResult
from fundamental.providers.registry import register


@register("llm", "mock")
class MockLlmProvider:
    is_live = False

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str | None = None,
        model: str = "mock",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout_seconds: float = 120,
    ) -> None:
        self.model = model

    def _last_user_content(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                return message.content
        return ""

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return ChatResult(content=f"[mock] {self._last_user_content(messages)}", model=self.model)

    def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        return {"mock": True, "echo": self._last_user_content(messages)}
