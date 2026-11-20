"""估值参数校准服务与研报正文抓取的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fundamental.services.narrative._common as _common
import fundamental.services.narrative._valuation_params as vp
import fundamental.utils.research_text as rt
from fundamental.services.narrative._valuation_params import (
    _dividend_reinvestment,
    _fallback_params,
    _normalize_params,
    _recompute,
)


class TestNormalizeParams(unittest.TestCase):
    def test_clamps_out_of_range_values(self):
        raw = {
            "ddm": {"growth": 0.9, "discount": 0.01},
            "dcf": {"growth": -0.9, "discount": 0.5, "terminal_growth": 0.2},
        }
        p = _normalize_params(raw)
        self.assertLessEqual(p["ddm"]["growth"], vp._GROWTH_MAX)
        self.assertLessEqual(p["ddm"]["discount"], vp._DISCOUNT_MAX)
        self.assertGreaterEqual(p["dcf"]["growth"], vp._GROWTH_MIN)
        self.assertLessEqual(p["dcf"]["discount"], vp._DISCOUNT_MAX)
        self.assertLessEqual(p["dcf"]["terminal_growth"], vp._TERMINAL_MAX)

    def test_enforces_discount_growth_gap(self):
        raw = {"ddm": {"growth": 0.08, "discount": 0.09}}
        p = _normalize_params(raw)
        self.assertGreaterEqual(p["ddm"]["discount"] - p["ddm"]["growth"], vp._GAP - 1e-6)

    def test_enforces_terminal_gap(self):
        raw = {"dcf": {"growth": 0.03, "discount": 0.05, "terminal_growth": 0.045}}
        p = _normalize_params(raw)
        self.assertGreaterEqual(p["dcf"]["discount"] - p["dcf"]["terminal_growth"], vp._GAP - 1e-6)

    def test_handles_garbage_input(self):
        p = _normalize_params({"ddm": {"growth": "abc"}})
        self.assertIsInstance(p["ddm"]["growth"], float)
        self.assertIsInstance(p["summary"], str)


class TestFallbackParams(unittest.TestCase):
    def test_uses_history_and_is_conservative(self):
        facts = {
            "annual_growth": [
                {"net_profit_yoy": 20},
                {"net_profit_yoy": 10},
                {"net_profit_yoy": 2},
            ]
        }
        p = _fallback_params(facts)
        self.assertLessEqual(p["ddm"]["growth"], 0.06)
        self.assertGreaterEqual(p["ddm"]["discount"] - p["ddm"]["growth"], 0.03)
        self.assertLess(p["dcf"]["terminal_growth"], p["dcf"]["growth"])


class TestRecompute(unittest.TestCase):
    def test_recomputes_with_custom_params(self):
        facts = {"model_inputs": {"dps": 2.0, "price": 40.0, "fcf": 1e9, "shares": 1e8}}
        params = {
            "ddm": {"growth": 0.03, "discount": 0.09, "rationale": ""},
            "dcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02, "rationale": ""},
        }
        out = _recompute(facts, params)
        # DPS=2, g=3%, r=9% → FV=35.77, 低于价格 40 → 安全边际为负
        self.assertIsNotNone(out["ddm"]["fair_value_low"])
        self.assertLess(out["ddm"]["margin_of_safety"], 0)
        self.assertIsNotNone(out["dcf"]["fair_value_low"])
        self.assertIsNotNone(out["dividend_reinvestment"])

    def test_empty_inputs_yield_none(self):
        facts = {"model_inputs": {"dps": None, "price": None, "fcf": None, "shares": None}}
        params = {
            "ddm": {"growth": 0.03, "discount": 0.09, "rationale": ""},
            "dcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02, "rationale": ""},
        }
        out = _recompute(facts, params)
        self.assertIsNone(out["ddm"]["fair_value_low"])
        self.assertIsNone(out["dcf"]["fair_value_low"])
        self.assertIsNone(out["dividend_reinvestment"])


class TestDividendReinvestment(unittest.TestCase):
    def test_compounds_dividends(self):
        r = _dividend_reinvestment(dps=2.0, price=40.0, growth=0.03, years=10)
        self.assertEqual(r["years"], 10)
        self.assertGreater(r["ending_shares"], 1.0)  # 股息再买入更多股
        self.assertGreater(r["total_dividend"], 0)
        self.assertGreater(r["total_return"], 0)
        self.assertIsNotNone(r["annualized"])

    def test_returns_none_without_inputs(self):
        self.assertIsNone(_dividend_reinvestment(None, 40.0, 0.03))
        self.assertIsNone(_dividend_reinvestment(2.0, None, 0.03))


class TestGenerateValuationParams(unittest.TestCase):
    def _result(self):
        from fundamental.models import Market, Snapshot, Stock, ValuationResult

        ddm = ValuationResult(
            method="ddm",
            inputs={"dividend_per_share": 2.0},
            current_price=40.0,
        )
        return {
            "symbol": "600519.SH",
            "stock": Stock(code="600519", market=Market.SH, name="测试"),
            "snapshot": Snapshot(fetched_at="2026-01-01T00:00:00+00:00", price=40.0),
            "valuation": [ddm],
        }

    def test_template_fallback_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(vp, "analyze_stock", return_value=self._result()), mock.patch.object(
            vp, "_load_score_financials", return_value={"annual_growth": [{"net_profit_yoy": 8}]}
        ), mock.patch.object(vp, "_load_master_context", return_value={}), mock.patch.object(
            vp, "load_research_digest", return_value=[]
        ), mock.patch.object(vp, "has_live_llm", return_value=False):
            out = vp.generate_valuation_params("600519.SH", refresh=True)

        self.assertEqual(out["source"], "template")
        self.assertEqual(out["params_version"], vp._VALUATION_PARAMS_VERSION)
        self.assertIsNotNone(out["calibrated"]["ddm"]["fair_value_low"])

    def test_llm_pipeline_and_cache(self):
        llm_out = {
            "ddm": {"growth": 0.03, "discount": 0.09, "rationale": "保守"},
            "dcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02, "rationale": "保守"},
            "summary": "总结",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(vp, "analyze_stock", return_value=self._result()), mock.patch.object(
            vp, "_load_score_financials", return_value={}
        ), mock.patch.object(vp, "_load_master_context", return_value={}), mock.patch.object(
            vp, "load_research_digest", return_value=[{"date": "x", "org": "y", "title": "z", "content": "c"}]
        ), mock.patch.object(vp, "has_live_llm", return_value=True), mock.patch.object(
            vp, "chat_json", return_value=llm_out
        ) as fake_chat:
            first = vp.generate_valuation_params("600519.SH", refresh=True)
            self.assertEqual(first["source"], "llm")
            self.assertEqual(first["params"]["summary"], "总结")
            self.assertEqual(first["research_count"], 1)
            self.assertEqual(fake_chat.call_count, 1)

            second = vp.generate_valuation_params("600519.SH")
            self.assertEqual(second["generated_at"], first["generated_at"])
            self.assertEqual(fake_chat.call_count, 1)

    def test_cache_version_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(vp, "analyze_stock", return_value=self._result()), mock.patch.object(
            vp, "_load_score_financials", return_value={}
        ), mock.patch.object(vp, "_load_master_context", return_value={}), mock.patch.object(
            vp, "load_research_digest", return_value=[]
        ), mock.patch.object(vp, "has_live_llm", return_value=False):
            first = vp.generate_valuation_params("600519.SH", refresh=True)
            cache_path = vp._valuation_params_cache_path("600519.SH")
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["params_version"] = vp._VALUATION_PARAMS_VERSION - 1
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            second = vp.generate_valuation_params("600519.SH")
            self.assertNotEqual(second["generated_at"], first["generated_at"])


class TestResearchText(unittest.TestCase):
    def test_extract_body_from_html(self):
        page = '<html><div id="ctx-content"><p>招商银行(600036)</p><p>事件：营收同比增长5%。</p><p>短句</p></div></html>'
        body = rt._extract_body(page)
        self.assertIn("营收同比增长5%", body)
        self.assertNotIn("短句", body)  # 太短被过滤

    def test_load_digest_uses_cache(self):
        payload = [{"date": "2026-01-01", "org": "x", "title": "t", "content": "c"}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ):
            path = rt._research_cache_path("600036.SH")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            out = rt.load_research_digest("600036.SH")
            self.assertEqual(out, payload)

    def test_graceful_degrade_on_provider_failure(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(rt, "EastmoneyResearchProvider") as Provider:
            Provider.return_value.fetch_reports.side_effect = RuntimeError("network down")
            out = rt.load_research_digest("000000.SZ", refresh=True)
            self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
