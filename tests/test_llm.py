"""大模型能力单元与契约测试。"""

from __future__ import annotations

import unittest

from fundamental.config import Settings, load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.openai_compat import OpenAICompatibleLlmProvider
from fundamental.providers.llm.port import LlmConfigError
from fundamental.providers.registry import get_provider_class
from fundamental.services.llm import get_llm, has_live_llm


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, data):
        self._data = data
        self.trust_env = True
        self.headers = {}
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(self._data)


class TestRegistry(unittest.TestCase):
    def test_sources_registered(self):
        self.assertIs(get_provider_class("llm", "mock"), get_provider_class("llm", "mock"))
        self.assertTrue(hasattr(get_provider_class("llm", "openai_compat"), "chat"))


class TestMockProvider(unittest.TestCase):
    def _settings(self):
        return Settings(
            {
                "llm": {
                    "provider": "mock",
                    "providers": {"mock": {"adapter": "mock", "model": "mock"}},
                }
            }
        )

    def test_chat(self):
        result = get_llm(self._settings()).chat([ChatMessage("user", "你好")])
        self.assertEqual(result.content, "[mock] 你好")

    def test_chat_json(self):
        result = get_llm(self._settings()).chat_json([ChatMessage("user", "你好")])
        self.assertEqual(result, {"mock": True, "echo": "你好"})


class TestConfig(unittest.TestCase):
    def test_has_live_llm_false_for_mock(self):
        settings = Settings(
            {"llm": {"provider": "mock", "providers": {"mock": {"adapter": "mock"}}}}
        )
        self.assertFalse(has_live_llm(settings))

    def test_llm_toml_merged(self):
        settings = load_settings()
        self.assertEqual(settings.get("llm.provider"), "deepseek")
        self.assertEqual(settings.get("llm.providers.mock.adapter"), "mock")
        self.assertEqual(settings.get("llm.providers.deepseek.base_url"), "https://api.deepseek.com/v1")

    def test_missing_key_raises(self):
        settings = Settings(
            {
                "llm": {
                    "provider": "deepseek",
                    "providers": {
                        "deepseek": {
                            "adapter": "openai_compat",
                            "base_url": "https://api.deepseek.com/v1",
                            "api_key_env": "VC_MISSING_KEY",
                            "model": "deepseek-chat",
                        }
                    },
                }
            }
        )
        with self.assertRaises(LlmConfigError):
            get_llm(settings)


class TestOpenAICompatProvider(unittest.TestCase):
    def test_chat_payload_and_parse(self):
        session = _FakeSession(
            {
                "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
                "model": "fake-model",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        )
        provider = OpenAICompatibleLlmProvider(
            base_url="https://example.com/v1",
            api_key="secret",
            model="m1",
            temperature=0.5,
            max_tokens=100,
            timeout_seconds=10,
            session=session,
        )
        result = provider.chat([ChatMessage("system", "s"), ChatMessage("user", "u")])

        self.assertEqual(result.content, "你好")
        self.assertEqual(result.model, "fake-model")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage.total_tokens, 3)
        self.assertFalse(session.trust_env)

        call = session.calls[0]
        self.assertEqual(call["url"], "https://example.com/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(call["json"]["model"], "m1")
        self.assertEqual(call["json"]["temperature"], 0.5)
        self.assertEqual(call["json"]["messages"], [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ])

    def test_chat_json(self):
        session = _FakeSession({"choices": [{"message": {"content": '{"ok": true}'}}]})
        provider = OpenAICompatibleLlmProvider(
            base_url="https://example.com/v1",
            api_key="secret",
            model="m1",
            session=session,
        )
        self.assertEqual(provider.chat_json([ChatMessage("user", "u")]), {"ok": True})
        self.assertEqual(session.calls[0]["json"]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
