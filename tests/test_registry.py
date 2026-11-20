"""registry 契约测试：换源与装配机制。"""

from __future__ import annotations

import unittest

import fundamental.providers.announcement.cninfo  # noqa: F401
import fundamental.providers.capitalflow.eastmoney  # noqa: F401
import fundamental.providers.commodity.sina  # noqa: F401
import fundamental.providers.dividend.eastmoney  # noqa: F401
import fundamental.providers.financial.eastmoney  # noqa: F401
import fundamental.providers.forecast.eastmoney  # noqa: F401
import fundamental.providers.governance.eastmoney  # noqa: F401
import fundamental.providers.holding.eastmoney  # noqa: F401
import fundamental.providers.hotspot.eastmoney  # noqa: F401
import fundamental.providers.industry.eastmoney  # noqa: F401
import fundamental.providers.integrity.cninfo  # noqa: F401
import fundamental.providers.litigation.cninfo  # noqa: F401
import fundamental.providers.llm  # noqa: F401
import fundamental.providers.macro.eastmoney  # noqa: F401
import fundamental.providers.official.official  # noqa: F401
import fundamental.providers.quote.eastmoney  # noqa: F401
import fundamental.providers.research.eastmoney  # noqa: F401
import fundamental.providers.sentiment.guba  # noqa: F401

from fundamental.config import Settings, load_settings
from fundamental.providers import registry


class TestRegistry(unittest.TestCase):
    def test_all_capabilities_registered(self):
        capabilities = registry.registered_capabilities()
        self.assertIn("llm", capabilities)
        self.assertEqual(len(capabilities), 18)

    def test_primary_source_from_config(self):
        settings = load_settings()
        for capability in registry.registered_capabilities():
            if capability == "llm":
                continue  # llm 的 primary 在 config/llm.toml，不走 providers.toml
            source = registry.primary_source(settings, capability)
            self.assertTrue(source)

    def test_make_provider(self):
        settings = load_settings()
        provider = registry.make_provider(settings, "quote")
        self.assertTrue(callable(provider.fetch_snapshot))

    def test_get_provider_class_unknown_source(self):
        with self.assertRaises(KeyError):
            registry.get_provider_class("quote", "not-exist")


if __name__ == "__main__":
    unittest.main()
