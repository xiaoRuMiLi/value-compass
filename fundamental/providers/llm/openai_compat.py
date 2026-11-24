"""OpenAI 兼容 /chat/completions 实现，覆盖 OpenAI/DeepSeek/Moonshot/Qwen/Ollama/vLLM。"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from fundamental.models import ChatMessage, ChatResult, ChatUsage
from fundamental.providers.llm.port import LlmError
from fundamental.providers.registry import register

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _strip_fences(text: str) -> str:
    """去掉模型常见的 ```json ... ``` 包裹，保留内部原文。"""

    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    first_newline = stripped.find("\n")
    if first_newline < 0:
        return stripped.strip("`")
    body = stripped[first_newline + 1:]
    end = body.rfind("```")
    if end >= 0:
        body = body[:end]
    return body.strip()


def _extract_json(text: str) -> Any:
    """按「原样 → 去围栏 → 截取首尾 → 去尾逗号」逐级尝试解析。

    刻意**不**给截断的 JSON 补右括号/补字符串结尾：那等于凭空捏造半截数字或
    摘录，与「原文取证零改写」冲突。解析不了就明确抛 LlmError，由叙述层重试
    一次并把失败段写进报告，而不是制造看似合理的假证据。
    JSONDecodeError 是 ValueError，会绕过叙述层的 LlmError 降级分支，
    因此这里统一收敛异常类型。
    """

    original = (text or "").strip()
    candidates = [original, _strip_fences(original)]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
    # 正文里夹解释时，取最外层对象/数组再试；只做不改变内容的清洗
    for left, right in (("{", "}"), ("[", "]")):
        start = candidates[-1].find(left)
        end = candidates[-1].rfind(right)
        if start != -1 and end > start:
            sliced = candidates[-1][start: end + 1]
            for attempt in (sliced, re.sub(r",\s*([}\]])", r"\1", sliced)):
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError:
                    continue
    raise LlmError("模型未返回合法 JSON")


@register("llm", "openai_compat")
class OpenAICompatibleLlmProvider:
    is_live = True

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str | None = None,
        model: str = "gpt-4.1-mini",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout_seconds: float = 120,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"Content-Type": "application/json"})

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        data = self._post(self._payload(messages, temperature, max_tokens))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage")
        return ChatResult(
            content=str(message.get("content") or ""),
            model=str(data.get("model") or self.model),
            finish_reason=choice.get("finish_reason"),
            usage=(
                ChatUsage(
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
                if usage
                else None
            ),
        )

    def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        payload = self._payload(messages, temperature, max_tokens)
        payload["response_format"] = {"type": "json_object"}
        data = self._post(payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return _extract_json(str(message.get("content") or ""))

    def _payload(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        try:
            response = self._session.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout_seconds
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise LlmError(f"模型调用失败: {exc}") from exc
