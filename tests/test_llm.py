"""大模型能力单元与契约测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fundamental.config import Settings, load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.openai_compat import OpenAICompatibleLlmProvider
from fundamental.providers.llm.port import LlmConfigError
from fundamental.providers.registry import get_provider_class
from fundamental.services.llm import (
    _guard_input,
    estimate_tokens,
    get_llm,
    has_live_llm,
    input_budget,
)


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

    def test_explicit_zero_not_overridden_by_default(self):
        """显式配置的 0 / 0.0 / False 是合法值，不能被 or 回退成默认值。"""
        settings = Settings(
            {
                "llm": {
                    "provider": "deepseek",
                    "temperature": 0,
                    "max_tokens": 0,
                    "timeout_seconds": 0,
                    "providers": {
                        "deepseek": {
                            "adapter": "openai_compat",
                            "base_url": "https://api.deepseek.com/v1",
                            "api_key_env": "VC_TEST_ZERO_KEY",
                            "model": "deepseek-chat",
                        }
                    },
                }
            }
        )
        with patch.dict("os.environ", {"VC_TEST_ZERO_KEY": "fake"}):
            provider = get_llm(settings)
        self.assertEqual(provider.temperature, 0.0)
        self.assertEqual(provider.max_tokens, 0)
        self.assertEqual(provider.timeout_seconds, 0.0)

    def test_section_zero_beats_global_default(self):
        """专项配置里的 0 应优先于全局默认值。"""
        settings = Settings(
            {
                "llm": {
                    "provider": "deepseek",
                    "temperature": 0.2,
                    "providers": {
                        "deepseek": {
                            "adapter": "openai_compat",
                            "api_key_env": "VC_TEST_ZERO_KEY",
                            "temperature": 0,
                        }
                    },
                }
            }
        )
        with patch.dict("os.environ", {"VC_TEST_ZERO_KEY": "fake"}):
            provider = get_llm(settings)
        self.assertEqual(provider.temperature, 0.0)

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


class TestInputBudget(unittest.TestCase):
    def test_estimate_tokens_mixed(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("招商银行"), 4)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertGreater(estimate_tokens("银行 report 2026"), 4)

    def test_input_budget_reads_config(self):
        self.assertEqual(input_budget(Settings({"llm": {"max_input_tokens": 12345}})), 12345)
        self.assertEqual(input_budget(Settings({"llm": {}})), 32000)
        self.assertEqual(input_budget(Settings({"llm": {"max_input_tokens": "bad"}})), 32000)

    def test_guard_keeps_messages_within_budget(self):
        messages = [ChatMessage("system", "短提示"), ChatMessage("user", "短输入")]
        self.assertEqual(_guard_input(messages), messages)

    def test_guard_warns_but_keeps_single_overage(self):
        messages = [ChatMessage("user", "银" * 100)]
        with patch("fundamental.services.llm.input_budget", return_value=50), self.assertLogs(
            "fundamental.services.llm", level="WARNING"
        ) as logs:
            out = _guard_input(messages)
        self.assertEqual(out, messages)
        self.assertTrue(any("超过预算" in line for line in logs.output))

    def test_guard_truncates_beyond_double_budget(self):
        messages = [ChatMessage("user", "银" * 1000)]
        with patch("fundamental.services.llm.input_budget", return_value=100), self.assertLogs(
            "fundamental.services.llm", level="ERROR"
        ):
            out = _guard_input(messages)
        self.assertLess(len(out[0].content), 1000)
        self.assertIn("已省略中间部分", out[0].content)


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
