"""估值参数校准服务与研报正文抓取的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import fundamental.services.narrative._common as _common
import fundamental.services.narrative._valuation_params as vp
import fundamental.utils.research_text as rt
from fundamental.services.narrative._valuation_params import (
    _dividend_reinvestment,
    _citations,
    _fallback_params,
    _normalize_params,
    _overall_range,
    _recompute,
    _sellside_sentiment,
)


class TestCalibrationInjection(unittest.TestCase):
    def test_load_calibration_params_rejects_stale_version(self):
        stale = {
            "params_version": vp._VALUATION_PARAMS_VERSION - 1,
            "params": {"ddm": {"growth": 0.03}, "dcf": {"growth": 0.05}},
        }
        with mock.patch.object(vp, "_read_cache", return_value=stale):
            self.assertIsNone(vp.load_calibration_params("00700.HK"))

    def test_load_calibration_params_accepts_current_version(self):
        cached = {
            "params_version": vp._VALUATION_PARAMS_VERSION,
            "params": {"ddm": {"growth": 0.03}, "dcf": {"growth": 0.05}},
        }
        with mock.patch.object(vp, "_read_cache", return_value=cached):
            self.assertEqual(vp.load_calibration_params("00700.HK"), cached["params"])

    def test_recompute_emits_full_valuation_for_ui_override(self):
        facts = {
            "model_inputs": {"dps": 10.0, "dcf_cash": 1e10, "shares": 1e9, "price": 100.0}
        }
        params = vp._normalize_params(
            {
                "ddm": {"growth": 0.03, "discount": 0.09},
                "dcf": {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.02},
            }
        )
        out = vp._recompute(facts, params)
        for method in ("ddm", "dcf"):
            item = out[method]
            self.assertEqual(item["method"], method)
            self.assertEqual(item["basis"], "calibrated")
            self.assertIn("growth", item["assumptions"])
            self.assertTrue(item["inputs"])
            self.assertIsNotNone(item["fair_value_low"])


class TestValuationScenarios(unittest.TestCase):
    def test_normalize_keeps_llm_scenarios_and_clamps(self):
        raw = {
            "ddm": {"growth": 0.03, "discount": 0.09},
            "dcf": {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.02},
            "scenarios": {
                "optimistic": {"ddm": {"growth": 0.99, "discount": 0.01},
                               "dcf": {"growth": 0.08, "discount": 0.09, "terminal_growth": 0.03}},
                "pessimistic": {"ddm": {"growth": 0.01, "discount": 0.11},
                                "dcf": {"growth": 0.02, "discount": 0.12, "terminal_growth": 0.01}},
            },
        }
        p = vp._normalize_params(raw)
        opt = p["scenarios"]["optimistic"]["ddm"]
        self.assertLessEqual(opt["growth"], vp._GROWTH_MAX)
        self.assertGreaterEqual(opt["discount"] - opt["growth"], vp._GAP - 1e-6)
        self.assertEqual(p["scenarios"]["pessimistic"]["dcf"]["growth"], 0.02)

    def test_derive_scenarios_when_llm_omits_them(self):
        p = vp._normalize_params(
            {"ddm": {"growth": 0.03, "discount": 0.09},
             "dcf": {"growth": 0.05, "discount": 0.10, "terminal_growth": 0.02}}
        )
        self.assertLess(p["scenarios"]["pessimistic"]["ddm"]["growth"], p["ddm"]["growth"])
        self.assertGreater(p["scenarios"]["pessimistic"]["ddm"]["discount"], p["ddm"]["discount"])
        self.assertGreater(p["scenarios"]["optimistic"]["dcf"]["growth"], p["dcf"]["growth"])

    def test_recompute_returns_three_tiers(self):
        facts = {"model_inputs": {"dps": 10.0, "dcf_cash": 1e10, "shares": 1e9, "price": 100.0}}
        out = vp._recompute(facts, vp._normalize_params({}))
        self.assertEqual(set(out["scenarios"]), {"optimistic", "pessimistic"})
        base_dcf = out["dcf"]["fair_value_low"]
        opt_dcf = out["scenarios"]["optimistic"]["dcf"]["fair_value_low"]
        pess_dcf = out["scenarios"]["pessimistic"]["dcf"]["fair_value_low"]
        self.assertLess(pess_dcf, base_dcf)
        self.assertLess(base_dcf, opt_dcf)
        self.assertIn("overall", out["scenarios"]["optimistic"])

    def test_adjusted_fcf_has_three_tiers_and_joins_overall(self):
        facts = {"model_inputs": {
            "dps": 2.58, "dcf_cash": 2.97e10, "shares": 3.88e9, "price": 72.55,
            "fcf_components": {"ocf": 2.97e10, "capex": 1.97e9,
                               "interest_income": 0.0, "interest_expense": 0.0},
        }}
        out = vp._recompute(facts, vp._normalize_params({}))
        base = out["adjusted_fcf"]["fair_value_low"]
        opt = out["scenarios"]["optimistic"]["adjusted_fcf"]["fair_value_low"]
        pess = out["scenarios"]["pessimistic"]["adjusted_fcf"]["fair_value_low"]
        self.assertLess(pess, base)
        self.assertLess(base, opt)
        self.assertIn("adjusted_fcf", out["overall"]["methods"])

    def test_fcff_has_three_tiers_and_joins_overall(self):
        facts = {"model_inputs": {
            "dps": 2.58, "dcf_cash": 2.97e10, "shares": 3.88e9, "price": 72.55,
            "fcff_inputs": {
                "ocf": 2.97e10, "capex": 1.97e9, "interest_expense": 2.9e7,
                "cash": 1.27e11, "marketable_securities": None,
                "nonoperating_invest": 2.2e9, "debt": 4.1e8,
                "minority_equity": 2.2e9, "revenue": 4.05e10,
            },
        }}
        out = vp._recompute(facts, vp._normalize_params({}))
        base = out["fcff"]["fair_value_low"]
        opt = out["scenarios"]["optimistic"]["fcff"]["fair_value_low"]
        pess = out["scenarios"]["pessimistic"]["fcff"]["fair_value_low"]
        self.assertLess(pess, base)
        self.assertLess(base, opt)
        self.assertIn("fcff", out["overall"]["methods"])
        self.assertGreater(out["fcff"]["inputs"]["equity_value"],
                           out["fcff"]["inputs"]["enterprise_value"])

    def test_derive_scenarios_includes_adjusted_fcf(self):
        p = vp._normalize_params({})
        self.assertIn("adjusted_fcf", p["scenarios"]["optimistic"])
        self.assertLess(
            p["scenarios"]["pessimistic"]["adjusted_fcf"]["growth"],
            p["adjusted_fcf"]["growth"],
        )
        self.assertGreater(
            p["scenarios"]["optimistic"]["adjusted_fcf"]["growth"],
            p["adjusted_fcf"]["growth"],
        )
        self.assertIn("fcff", p["scenarios"]["optimistic"])
        self.assertLess(p["scenarios"]["pessimistic"]["fcff"]["growth"], p["fcff"]["growth"])


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
        facts = {"model_inputs": {"dps": 2.0, "price": 40.0, "dcf_cash": 1e9, "shares": 1e8}}
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
        overall = out["overall"]
        self.assertEqual(overall["methods"], ["ddm", "dcf"])
        self.assertLessEqual(
            overall["low"], min(out["ddm"]["fair_value_low"], out["dcf"]["fair_value_low"])
        )
        self.assertGreaterEqual(
            overall["high"], max(out["ddm"]["fair_value_high"], out["dcf"]["fair_value_high"])
        )
        self.assertAlmostEqual(overall["margin_of_safety"], overall["center"] / 40.0 - 1, places=3)

    def test_overall_with_only_ddm(self):
        facts = {"model_inputs": {"dps": 2.0, "price": 40.0, "dcf_cash": None, "shares": None}}
        params = {
            "ddm": {"growth": 0.03, "discount": 0.09, "rationale": ""},
            "dcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02, "rationale": ""},
        }
        out = _recompute(facts, params)
        overall = out["overall"]
        self.assertEqual(overall["methods"], ["ddm"])
        self.assertEqual(overall["low"], out["ddm"]["fair_value_low"])
        self.assertIn("仅基于", overall["note"])

    def test_empty_inputs_yield_none(self):
        facts = {"model_inputs": {"dps": None, "price": None, "dcf_cash": None, "shares": None}}
        params = {
            "ddm": {"growth": 0.03, "discount": 0.09, "rationale": ""},
            "dcf": {"growth": 0.04, "discount": 0.10, "terminal_growth": 0.02, "rationale": ""},
        }
        out = _recompute(facts, params)
        self.assertIsNone(out["ddm"]["fair_value_low"])
        self.assertIsNone(out["dcf"]["fair_value_low"])
        self.assertIsNone(out["dividend_reinvestment"])
        self.assertIsNone(out["overall"])

    def test_overall_range_rejects_invalid_values(self):
        self.assertIsNone(_overall_range({"ddm": {"fair_value_low": -1, "fair_value_high": 5}}))
        self.assertIsNone(_overall_range({"ddm": {"fair_value_low": 10, "fair_value_high": 5}}))


class TestSellsideAndCitations(unittest.TestCase):
    def test_summarizes_ratings_changes_and_targets(self):
        today = date.today().isoformat()
        reports = [
            {
                "date": today,
                "rating": "buy",
                "rating_change": "maintain",
                "target_price": 50.0,
            },
            {
                "date": today,
                "rating": "overweight",
                "rating_change": "upgrade",
                "target_price": 60.0,
            },
            {"date": "2020-01-01", "rating": "buy", "rating_change": "downgrade"},
        ]
        result = vp._sellside_sentiment(reports)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["recent_count"], 2)
        self.assertEqual(result["rating_distribution"]["buy"], 2)
        self.assertEqual(result["rating_changes"]["upgrade"], 1)
        self.assertEqual(result["target_price_low"], 50.0)
        self.assertEqual(result["target_price_high"], 60.0)
        self.assertIn("买入", result["summary"])

    def test_empty_reports_are_explicit(self):
        result = _sellside_sentiment([])
        self.assertEqual(result["total"], 0)
        self.assertIn("暂无研报覆盖", result["summary"])

    def test_citations_cover_all_kinds(self):
        intel = {
            "company_reports": [
                {"date": "2026-11-03", "org": "东海证券", "title": "公司简评", "url": "http://a"}
            ],
            "industry_reports": [
                {"date": "2026-11-18", "org": "华宝证券", "title": "行业跟踪", "url": "http://b"}
            ],
            "surveys": [
                {"date": "2026-11-11", "org": "招商银行", "title": "业绩交流会", "url": ""}
            ],
        }
        citations = _citations(intel)
        self.assertEqual([item["kind"] for item in citations], ["个股研报", "行业研报", "机构调研"])
        self.assertEqual(citations[0]["url"], "http://a")
        self.assertEqual(citations[-1]["url"], "")


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
            vp, "load_intel_digest", return_value={"company_reports": []}
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
            vp, "load_intel_digest", return_value={
                "company_reports": [{"date": "x", "org": "y", "title": "z", "content": "c"}]
            }
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
            vp, "load_intel_digest", return_value={"company_reports": []}
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

    def test_load_digest_uses_fresh_cache(self):
        reports = [
            {
                "date": "2026-01-01",
                "org": "x",
                "title": "t",
                "content": "c",
                "info_code": "AP1",
                "rating": "buy",
                "target_price": 50.0,
                "eps_forecast": {"this_year": 6.1},
            }
        ]
        payload = {
            "schema_version": rt._CACHE_SCHEMA_VERSION,
            "symbol": "600036.SH",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "max_chars": rt._default_chars(),
            "reports": reports,
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ):
            path = rt._research_cache_path("600036.SH")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            out = rt.load_research_digest("600036.SH")
            self.assertEqual(out, reports)
            self.assertEqual(out[0]["rating"], "buy")
            self.assertEqual(out[0]["eps_forecast"]["this_year"], 6.1)

    def test_stale_legacy_cache_is_refreshed_and_migrated(self):
        legacy = [{"date": "2020-01-01", "org": "x", "title": "t", "content": "old"}]
        meta = {
            "date": "2026-11-20",
            "org": "y",
            "title": "new",
            "url": "http://example.com/report",
            "info_code": "AP2",
            "content": "",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(rt, "_fetch_recent_reports", return_value=[meta]), mock.patch(
            "fundamental.providers._shared.HttpClient"
        ) as Client:
            Client.return_value.get_text.return_value = (
                '<div id="ctx-content"><p>三季度息差企稳，资产质量稳健。</p></div>'
            )
            path = rt._research_cache_path("600036.SH")
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            out = rt.load_research_digest("600036.SH")
            self.assertEqual(out[0]["info_code"], "AP2")
            self.assertIn("息差企稳", out[0]["content"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], rt._CACHE_SCHEMA_VERSION)
            self.assertIn("fetched_at", saved)

    def test_reuses_cached_body_by_info_code(self):
        reports = [
            {
                "date": "2026-11-20",
                "org": "y",
                "title": "t",
                "url": "http://old",
                "info_code": "AP3",
                "content": "cached body",
            }
        ]
        meta = dict(reports[0], url="http://new", content="")
        payload = {
            "schema_version": rt._CACHE_SCHEMA_VERSION,
            "fetched_at": "2020-01-01T00:00:00+00:00",
            "max_chars": rt._default_chars(),
            "reports": reports,
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(rt, "_fetch_recent_reports", return_value=[meta]), mock.patch(
            "fundamental.providers._shared.HttpClient"
        ) as Client:
            Client.return_value.get_text.return_value = (
                '<div id="ctx-content"><p>不应被抓取的新正文内容。</p></div>'
            )
            path = rt._research_cache_path("600036.SH")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            out = rt.load_research_digest("600036.SH")
            self.assertEqual(out[0]["content"], "cached body")
            self.assertFalse(Client.return_value.get_text.called)

    def test_smaller_cache_budget_triggers_refetch(self):
        """缓存记录的正文上限低于当前配置时，说明预算被调大，应重抓而非复用短正文。"""

        reports = [
            {
                "date": "2026-11-20",
                "org": "y",
                "title": "t",
                "url": "http://old",
                "info_code": "AP9",
                "content": "短正文",
            }
        ]
        meta = dict(reports[0], url="http://new", content="")
        payload = {
            "schema_version": rt._CACHE_SCHEMA_VERSION,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "max_chars": 1,
            "reports": reports,
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(rt, "_fetch_recent_reports", return_value=[meta]), mock.patch(
            "fundamental.providers._shared.HttpClient"
        ) as Client:
            Client.return_value.get_text.return_value = (
                '<div id="ctx-content"><p>' + "新正文" * 100 + "</p></div>"
            )
            path = rt._research_cache_path("600036.SH")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            out = rt.load_research_digest("600036.SH")
            self.assertTrue(Client.return_value.get_text.called)
            self.assertGreater(len(out[0]["content"]), len("短正文"))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["max_chars"], rt._default_chars())

    def test_graceful_degrade_on_provider_failure(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            rt.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(rt, "EastmoneyResearchProvider") as Provider:
            Provider.return_value.fetch_reports.side_effect = RuntimeError("network down")
            out = rt.load_research_digest("000000.SZ", refresh=True)
            self.assertEqual(out, [])


class TestResearchProviderMapping(unittest.TestCase):
    def test_maps_rating_target_and_eps(self):
        from fundamental.providers.research.eastmoney import EastmoneyResearchProvider

        row = {
            "infoCode": "AP9",
            "publishDate": "2026-11-03 00:00:00.000",
            "orgSName": "测试证券",
            "researcher": "张三",
            "emRatingName": "买入",
            "indvAimPriceT": "50.5",
            "title": "测试研报",
            "predictThisYearEps": "6.13",
            "predictNextYearEps": "6.34",
            "indvInduName": "银行Ⅱ",
        }
        with mock.patch("fundamental.providers.research.eastmoney.HttpClient") as Client:
            Client.return_value.get_json.return_value = {"data": [row], "hits": 1}
            reports = EastmoneyResearchProvider().fetch_reports("600036.SH")

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.info_code, "AP9")
        self.assertEqual(report.rating.value, "buy")
        self.assertEqual(report.target_price, 50.5)
        self.assertEqual(report.eps_forecast["next_year"], 6.34)
        self.assertEqual(report.analyst, "张三")
        self.assertEqual(report.industry_name, "银行Ⅱ")


if __name__ == "__main__":
    unittest.main()
