"""provider 异常体系统一测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from fundamental.providers import (
    ProviderConfigError,
    ProviderDataError,
    ProviderError,
    ProviderNotImplementedError,
    ProviderRequestError,
)
from fundamental.providers import registry
from fundamental.providers._shared.http import HttpClient
from fundamental.providers.llm.port import LlmConfigError, LlmError


class _EmptySettings:
    def get(self, key):
        return None


class TestProviderExceptionHierarchy(unittest.TestCase):
    def test_all_subclasses_of_provider_error(self):
        for exc_type in (
            ProviderConfigError,
            ProviderRequestError,
            ProviderDataError,
            ProviderNotImplementedError,
            LlmError,
            LlmConfigError,
        ):
            self.assertTrue(issubclass(exc_type, ProviderError), exc_type.__name__)
        self.assertTrue(issubclass(LlmConfigError, ProviderConfigError))

    def test_no_builtin_inheritance(self):
        # 异常层次必须干净，避免宽泛的 except ValueError/KeyError 误吞 provider 错误
        for exc_type in (ProviderConfigError, ProviderDataError, ProviderNotImplementedError):
            self.assertEqual(exc_type.__bases__, (ProviderError,), exc_type.__name__)


class TestProviderExceptionRaising(unittest.TestCase):
    def test_registry_unknown_source(self):
        with self.assertRaises(ProviderConfigError):
            registry.get_provider_class("quote", "not-exist")

    def test_registry_missing_primary(self):
        with self.assertRaises(ProviderConfigError):
            registry.primary_source(_EmptySettings(), "quote")

    def test_http_failure_wrapped(self):
        client = HttpClient(retries=0, backoff=0)
        with patch.object(
            client._session, "request", side_effect=requests.ConnectionError("boom")
        ):
            with self.assertRaises(ProviderRequestError) as ctx:
                client.get("http://example.invalid/x")
        self.assertIsInstance(ctx.exception.__cause__, requests.ConnectionError)

    def test_json_parse_failure_wrapped(self):
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("bad json")
        response.raise_for_status = Mock()
        client = HttpClient(retries=0, backoff=0)
        with patch.object(client._session, "request", return_value=response):
            with self.assertRaises(ProviderDataError):
                client.get_json("http://example.invalid/x")


if __name__ == "__main__":
    unittest.main()
