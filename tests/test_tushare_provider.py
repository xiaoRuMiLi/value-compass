"""Tushare provider 契约测试：请求封装、字段映射与降级链。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fundamental.config import Settings
from fundamental.models import Market
from fundamental.providers import (
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
)
from fundamental.providers._shared.cache import ResponseCache
from fundamental.providers._shared.http import HttpClient
from fundamental.providers._shared.tushare import TushareClient, load_token
from fundamental.providers.quote.eastmoney import EastmoneyQuoteProvider
from fundamental.providers.quote.tushare import TushareQuoteProvider
from fundamental.providers.registry import make_provider_chain
from fundamental.services.sync import fetch_with_fallback


class TestTushareClient(unittest.TestCase):
    def test_load_token_prefers_env(self) -> None:
        with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": " env-token "}):
            self.assertEqual(load_token(), "env-token")

    def test_load_token_reads_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text("TUSHARE_TOKEN=file-token\n", encoding="utf-8")
            env = dict(os.environ)
            env.pop("TUSHARE_TOKEN", None)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "fundamental.providers._shared.tushare.paths.project_root", return_value=Path(tmp)
            ):
                self.assertEqual(load_token(), "file-token")

    def test_call_maps_rows(self) -> None:
        client = mock.Mock()
        client.post_json.return_value = {
            "code": 0,
            "data": {"fields": ["ts_code", "close"], "items": [["000858.SZ", 72.55]]},
        }
        result = TushareClient(token="t", client=client).call("daily", {"ts_code": "000858.SZ"}, "close")
        self.assertEqual(result, [{"ts_code": "000858.SZ", "close": 72.55}])
        self.assertEqual(client.post_json.call_args.args[0], "https://api.tushare.pro")
        self.assertEqual(client.post_json.call_args.args[1]["api_name"], "daily")

    def test_call_maps_rate_limit_and_permission(self) -> None:
        client = mock.Mock()
        client.post_json.return_value = {"code": 40203, "msg": "抱歉，您访问接口频率超限(1次/小时)"}
        with self.assertRaises(ProviderRateLimitError):
            TushareClient(token="t", client=client).call("daily_basic", {}, "")

        client.post_json.return_value = {"code": 40203, "msg": "抱歉，您没有接口(income)访问权限"}
        with self.assertRaises(ProviderConfigError):
            TushareClient(token="t", client=client).call("income", {}, "")

    def test_post_json_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = mock.Mock()
            response.json.return_value = {"code": 0, "data": {"fields": [], "items": []}}
            request = mock.Mock(return_value=response)
            client = HttpClient(cache=ResponseCache(Path(tmp)))
            with mock.patch.object(client, "_request", request):
                client.post_json("https://api.tushare.pro", {"api_name": "daily"})
                client.post_json("https://api.tushare.pro", {"api_name": "daily"})
            self.assertEqual(request.call_count, 1)


class TestTushareQuoteProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.client = mock.Mock()
        self.provider = TushareQuoteProvider(self.client)

    def test_stock_info_a_share(self) -> None:
        self.client.call.return_value = [
            {"ts_code": "000858.SZ", "name": "五粮液", "industry": "白酒", "list_date": "19980427"}
        ]
        stock = self.provider.fetch_stock_info("000858.SZ")
        self.assertEqual(stock.name, "五粮液")
        self.assertEqual(stock.industry, "白酒")
        self.assertEqual(stock.list_date, "1998-04-27")
        self.assertEqual(stock.market, Market.SZ)

    def test_stock_info_rate_limited_keeps_placeholder(self) -> None:
        self.client.call.side_effect = ProviderRateLimitError("rate limited")
        stock = self.provider.fetch_stock_info("000858.SZ")
        self.assertEqual(stock.code, "000858")
        self.assertEqual(stock.name, "000858")

    def _respond(self, mapping: dict[str, list]) -> None:
        def call(api_name: str, params: dict, fields: str, ttl: int | None = None) -> list:
            return mapping.get(api_name, [])

        self.client.call.side_effect = call

    def test_snapshot_a_share(self) -> None:
        self._respond({
            "daily": [{"trade_date": "20261120", "close": 72.55, "pct_chg": 1.2}],
            "daily_basic": [{"trade_date": "20261120", "turnover_rate": 0.83, "total_mv": 28161066.04}],
        })
        snap = self.provider.fetch_snapshot("000858.SZ")
        self.assertEqual(snap.price, 72.55)
        self.assertEqual(snap.pct_change, 1.2)
        self.assertEqual(snap.turnover_rate, 0.83)
        self.assertAlmostEqual(snap.market_cap, 28161066.04 * 1e4)

    def test_kline_a_share_amount_scaled_and_adj_applied(self) -> None:
        self._respond({
            "daily": [{
                "trade_date": "20250102", "open": 130.0, "close": 132.0,
                "high": 133.0, "low": 129.0, "vol": 100.0, "amount": 250.0,
            }],
            "adj_factor": [{"trade_date": "20250102", "adj_factor": 21.1682}],
        })
        bars = self.provider.fetch_kline("000858.SZ", "2025-01-01", "2025-12-31")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].trade_date, "2025-01-02")
        self.assertEqual(bars[0].amount, 250000.0)
        self.assertEqual(bars[0].adj_factor, 21.1682)

    def test_kline_rate_limit_propagates(self) -> None:
        self.client.call.side_effect = ProviderRateLimitError("rate limited")
        with self.assertRaises(ProviderRateLimitError):
            self.provider.fetch_kline("000858.SZ", "2025-01-01", "2025-12-31")

    def test_valuations_a_share(self) -> None:
        self._respond({
            "daily_basic": [{
                "trade_date": "20261120", "close": 72.55, "pe": 25.1, "pe_ttm": 23.1011,
                "pb": 2.3777, "ps_ttm": 6.5875, "total_mv": 28161066.04,
            }]
        })
        points = self.provider.fetch_valuations("000858.SZ", "2026-11-01", "2026-11-30")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].pe_lar, 25.1)
        self.assertEqual(points[0].pb_mrq, 2.3777)
        self.assertAlmostEqual(points[0].total_market_cap, 28161066.04 * 1e4)

    def test_hk_kline_and_valuation(self) -> None:
        rows = [{
            "trade_date": "20261120", "open": 341.0, "high": 349.6, "low": 339.6,
            "close": 344.0, "pre_close": 340.0, "pct_chg": 1.18,
            "vol": 41094469.0, "amount": 14089992865.68,
        }]
        self._respond({"hk_daily": rows})
        bars = self.provider.fetch_kline("00700.HK", "2026-11-01", "2026-11-30")
        self.assertEqual(bars[0].amount, rows[0]["amount"])
        self.assertEqual(bars[0].volume, rows[0]["vol"])
        points = self.provider.fetch_valuations("00700.HK", "2026-11-01", "2026-11-30")
        self.assertEqual(points[0].close_price, 344.0)
        self.assertIsNone(points[0].pe_ttm)


class TestFallbackFetch(unittest.TestCase):
    def test_switches_on_error_and_empty_list(self) -> None:
        first = mock.Mock()
        first.fetch_kline.side_effect = ProviderRateLimitError("rate limited")
        second = mock.Mock()
        second.fetch_kline.return_value = ["bar"]
        self.assertEqual(fetch_with_fallback([first, second], "fetch_kline", "000858.SZ"), ["bar"])

        first.fetch_valuations.return_value = []
        second.fetch_valuations.return_value = ["point"]
        self.assertEqual(
            fetch_with_fallback([first, second], "fetch_valuations", "000858.SZ"), ["point"]
        )

    def test_all_fail_returns_empty_for_lists(self) -> None:
        provider = mock.Mock()
        provider.fetch_kline.side_effect = ProviderRateLimitError("rate limited")
        self.assertEqual(fetch_with_fallback([provider], "fetch_kline", "000858.SZ"), [])

    def test_all_fail_raises_for_single_value(self) -> None:
        provider = mock.Mock()
        provider.fetch_snapshot.side_effect = ProviderError("down")
        with self.assertRaises(ProviderError):
            fetch_with_fallback([provider], "fetch_snapshot", "000858.SZ")


class TestProviderChain(unittest.TestCase):
    def test_chain_respects_fallback_order(self) -> None:
        settings = Settings({
            "providers": {"quote": {"primary": "eastmoney", "fallback": "tushare"}}
        })
        providers = make_provider_chain(settings, "quote")
        self.assertEqual(
            [type(p) for p in providers], [EastmoneyQuoteProvider, TushareQuoteProvider]
        )

    def test_chain_dedupes_same_source(self) -> None:
        settings = Settings({
            "providers": {"quote": {"primary": "tushare", "fallback": "tushare"}}
        })
        self.assertEqual(len(make_provider_chain(settings, "quote")), 1)

    def test_chain_without_fallback(self) -> None:
        settings = Settings({"providers": {"quote": {"primary": "tushare"}}})
        providers = make_provider_chain(settings, "quote")
        self.assertEqual([type(p) for p in providers], [TushareQuoteProvider])


if __name__ == "__main__":
    unittest.main()
