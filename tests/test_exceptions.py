"""provider 异常体系统一与向后兼容测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

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

    def test_legacy_builtin_compatibility(self):
        self.assertIsInstance(ProviderConfigError("x"), KeyError)
        self.assertIsInstance(ProviderConfigError("x"), ValueError)
        self.assertIsInstance(ProviderDataError("x"), ValueError)
        self.assertIsInstance(ProviderNotImplementedError("x"), NotImplementedError)


class TestProviderExceptionRaising(unittest.TestCase):
    def test_registry_unknown_source(self):
        with self.assertRaises(ProviderConfigError) as ctx:
            registry.get_provider_class("quote", "not-exist")
        self.assertIsInstance(ctx.exception, KeyError)

    def test_registry_missing_primary(self):
        with self.assertRaises(ProviderConfigError) as ctx:
            registry.primary_source(_EmptySettings(), "quote")
        self.assertIsInstance(ctx.exception, ValueError)

    def test_http_failure_wrapped(self):
        client = HttpClient(retries=0, backoff=0)
        with patch.object(
            client._session, "request", side_effect=requests.ConnectionError("boom")
        ):
            with self.assertRaises(ProviderRequestError) as ctx:
                client.get("http://example.invalid/x")
        self.assertIsInstance(ctx.exception.__cause__, requests.ConnectionError)


if __name__ == "__main__":
    unittest.main()
