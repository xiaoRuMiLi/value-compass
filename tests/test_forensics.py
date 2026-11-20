"""财报深度体检测试：量化扫描口径、误报防护、趋势聚合、LLM 输出校验与缓存。

设计意图见 analysis/forensics.py 模块注释：数字由代码算、判断由 LLM 做，
因此测试重点放在「口径正确性」与「不可信输出必须被降级/补齐」两处。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fundamental.analysis import forensics as fx
from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import (
    EarningsForecast,
    FinancialReport,
    IntegrityRecord,
    ReportType,
    StatementItem,
    StatementType,
)
from fundamental.services.narrative import _common, _forensics as narrative_fx
from fundamental.web.app import app
from fundamental.web.routes import api


def fr(day: str, rt: ReportType = ReportType.FY, **kw) -> FinancialReport:
    return FinancialReport(report_date=day, report_type=rt, **kw)


def titles(scan: dict) -> list[str]:
    return [s["title"] for s in scan["signals"]]


class TestPeriodAlignment(unittest.TestCase):
    def test_yoy_requires_positive_base(self):
        self.assertAlmostEqual(fx._yoy(120, 100), 20.0)
        self.assertIsNone(fx._yoy(120, 0))
        self.assertIsNone(fx._yoy(120, -100))

    def test_period_key_separates_quarterly_and_annual(self):
        self.assertEqual(fx._period_key(fr("2025-03-31", ReportType.Q1)), (2025, 1))
        self.assertEqual(fx._period_key(fr("2025-12-31", ReportType.FY)), (2025, 4))
        self.assertNotEqual(
            fx._period_key(fr("2025-06-30", ReportType.H1)),
            fx._period_key(fr("2025-12-31", ReportType.FY)),
        )

    def test_mad_outlier_detects_spike_but_not_flat_history(self):
        self.assertFalse(fx._mad_outlier([10, 10, 10, 10, 10, 10], 10)[0])
        self.assertTrue(fx._mad_outlier([10, 10, 10, 10, 10, 10], 50)[0])
        self.assertFalse(fx._mad_outlier([10, 11], 50)[0])  # 样本不足不算离群


class TestFinancialSeries(unittest.TestCase):
    def test_empty_inputs_report_coverage_not_clean_bill(self):
        scan = fx.scan_forensics("TEST.SH", [])
        self.assertEqual(scan["signals"], [])
        self.assertEqual(scan["summary"]["risk_level"], "clear")
        self.assertTrue(scan["coverage"]["skipped"])
        self.assertIn("window", scan)

    def test_divergence_between_revenue_and_profit(self):
        # 同报告期去年对齐：2025 年营收 +20%，净利 -20%
        rows = [
            fr("2024-12-31", revenue=100.0, net_profit=100.0),
            fr("2025-12-31", revenue=120.0, net_profit=80.0),
        ]
        scan = fx.scan_forensics("TEST.SH", rows)
        self.assertIn("增收不增利", titles(scan))
        sig = next(s for s in scan["signals"] if s["title"] == "增收不增利")
        self.assertEqual(sig["severity"], "high")
        self.assertTrue(any("营业收入同比" in f["label"] for f in sig["facts"]))

    def test_deducted_profit_gap(self):
        rows = [
            fr("2024-12-31", net_profit=100.0, net_profit_deducted=100.0),
            fr("2025-12-31", net_profit=100.0, net_profit_deducted=50.0),
        ]
        scan = fx.scan_forensics("TEST.SH", rows)
        sig = next((s for s in scan["signals"] if "扣非" in s["title"]), None)
        self.assertIsNotNone(sig)
        self.assertIn("非经常性损益占比", "".join(f["label"] for f in sig["facts"]))

    def test_only_recent_periods_produce_signals(self):
        # 20 年老数据里的异常不应出现在结论里
        rows = [fr(f"{y}-12-31", revenue=100.0, net_profit=100.0) for y in range(2000, 2024)]
        rows += [
            fr("2024-12-31", net_profit=100.0, net_profit_deducted=10.0),
            fr("2025-12-31", net_profit=100.0, net_profit_deducted=10.0),
        ]
        scan = fx.scan_forensics("TEST.SH", rows, lookback_periods=4)
        periods = {s["period"][:4] for s in scan["signals"]}
        self.assertTrue(periods <= {"2022", "2023", "2024", "2025"})
        self.assertGreaterEqual(scan["window"]["conclusion_from"][:4], "2022")

    def test_bank_high_debt_ratio_is_not_flagged_by_itself(self):
        # 银行常态 90% 负债率，自身历史几乎不变 → 不应报「资产负债率突变」
        rows = [fr(f"{y}-12-31", debt_ratio=90.0 + (y % 2) * 0.1) for y in range(2018, 2026)]
        scan = fx.scan_forensics("BANK.SH", rows)
        self.assertNotIn("资产负债率突变", titles(scan))


class TestStatementItems(unittest.TestCase):
    def test_expense_ratio_shift_is_flagged(self):
        items = []
        for year, sale in ((2024, 10.0), (2025, 20.0)):
            day = f"{year}-12-31"
            items += [
                StatementItem(day, ReportType.FY, StatementType.INCOME, "OPERATE_INCOME", "营业收入", 100.0),
                StatementItem(day, ReportType.FY, StatementType.INCOME, "SALE_EXPENSE", "销售费用", sale),
            ]
        scan = fx.scan_forensics("TEST.SH", [fr("2025-12-31")], statement_items=items)
        self.assertTrue(any("销售费用率异动" in t for t in titles(scan)))
        self.assertTrue(scan["anomalies"])


class TestForecastGuards(unittest.TestCase):
    def test_revenue_forecast_does_not_create_fake_deviation(self):
        """东财把营收预告塞进净利金额字段：必须按公告正文剔除，否则造出 -50% 假偏离。"""

        rows = [
            fr("2024-12-31", net_profit=100.0),
            fr("2025-12-31", net_profit=105.0),
        ]
        forecast = EarningsForecast(
            report_date="2025-12-31",
            forecast_net_profit_low=200.0,
            forecast_net_profit_high=200.0,
            net_profit_yoy_low=5.0,
            net_profit_yoy_high=5.0,
            content="预计2025年1-12月营业收入约:20,000万元，同比上年增长5%左右。",
        )
        scan = fx.scan_forensics("TEST.SH", rows, forecasts=[forecast])
        self.assertNotIn("业绩预告与实际显著偏离", titles(scan))
        self.assertEqual(scan["evidence"]["forecast_checks"], [])

    def test_profit_forecast_with_tolerance_hits(self):
        rows = [
            fr("2024-12-31", net_profit=100.0),
            fr("2025-12-31", net_profit=104.0),
        ]
        forecast = EarningsForecast(
            report_date="2025-12-31",
            net_profit_yoy_low=5.0,
            net_profit_yoy_high=5.0,
            content="预计2025年度归属于上市公司股东的净利润同比增长5%左右。",
        )
        scan = fx.scan_forensics("TEST.SH", rows, forecasts=[forecast])
        checks = scan["evidence"]["forecast_checks"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["hit"])  # 4% 实际 vs 5%「左右」，在容差内
        self.assertNotIn("业绩预告与实际显著偏离", titles(scan))


class TestConsolidation(unittest.TestCase):
    def test_repeated_signals_become_one_trend_with_occurrences(self):
        rows = [fr(f"{y}-12-31", roe=30.0 - 5.0 * (y - 2021)) for y in range(2021, 2026)]
        # 显式指定低阈值与聚合门限：本例只验证趋势聚合，不依赖当前默认阈值
        cfg = ForensicsConfig(roe_pp=3.0, consolidate_min_occurrences=3)
        scan = fx.scan_forensics("TEST.SH", rows, config=cfg)
        roe = [s for s in scan["signals"] if s["title"] == "ROE 显著变化"]
        self.assertEqual(len(roe), 1)
        self.assertTrue(roe[0]["consolidated"])
        self.assertGreaterEqual(len(roe[0]["occurrences"]), 3)
        self.assertGreater(scan["summary"]["consolidated"], 0)
        self.assertIn("趋势", roe[0]["description"])

    def test_consolidate_keeps_short_groups_unmerged(self):
        base = {
            "id": "X", "category": "c", "severity": "medium", "title": "t",
            "period": "p", "facts": [], "trigger": "", "description": "d", "evidence_refs": [],
        }
        signals = [dict(base, id=f"X{i}") for i in range(2)]
        out, merged = fx._consolidate_signals(signals)
        self.assertEqual(len(out), 2)
        self.assertEqual(merged, 0)


class TestLlmNormalization(unittest.TestCase):
    def setUp(self):
        self.scan = fx.scan_forensics(
            "TEST.SH",
            [
                fr("2024-12-31", revenue=100.0, net_profit=100.0),
                fr("2025-12-31", revenue=120.0, net_profit=80.0, net_profit_deducted=40.0),
            ],
        )
        self.assertGreaterEqual(len(self.scan["signals"]), 2)

    def test_non_dict_output_is_rejected(self):
        self.assertIsNone(narrative_fx._normalize("not json", self.scan, 16))

    def test_missing_analysis_is_filled_and_bad_enum_downgraded(self):
        raw = {
            "verdict": {"level": "nonsense", "headline": "h", "essence": "e"},
            "analyses": [
                {
                    "id": self.scan["signals"][0]["id"],
                    "substance": "s", "benign": "b", "risk": "r", "action": "a",
                    "judgement": "guilty", "confidence": "very-high",
                }
            ],
            "cross_checks": ["c1", 123, None],
            "caveats": ["caveat"],
        }
        out = narrative_fx._normalize(raw, self.scan, 16)
        self.assertIsNotNone(out)
        self.assertEqual(out["verdict"]["level"], self.scan["summary"]["risk_level"])
        self.assertEqual(len(out["analyses"]), len(self.scan["signals"]))
        self.assertEqual(out["analyses"][0]["judgement"], "unverifiable")
        self.assertEqual(out["analyses"][0]["confidence"], "low")
        self.assertTrue(out["analyses"][0]["analyzed"])
        self.assertFalse(out["analyses"][1]["analyzed"])
        self.assertEqual(len(out["cross_checks"]), 1)

    def test_template_is_objective_and_carries_disclaimer(self):
        out = narrative_fx._template("TEST.SH", self.scan)
        self.assertIn("未启用", out["verdict"]["headline"])
        self.assertTrue(all(a["judgement"] == "unverifiable" for a in out["analyses"]))
        self.assertTrue(any("不构成" in c for c in out["caveats"]))

    def test_citations_dedupe_and_keep_url(self):
        scan = dict(self.scan)
        scan["evidence"] = dict(self.scan["evidence"])
        scan["evidence"]["regulatory"] = [
            {"date": "2025-01-01", "category": "penalty", "severity": "high",
             "title": "关于收到行政处罚的公告", "description": "", "source": "cninfo",
             "url": "https://example.com/a", "kind": "已披露监管记录"},
            {"date": "2025-01-01", "category": "penalty", "severity": "high",
             "title": "关于收到行政处罚的公告", "description": "", "source": "cninfo",
             "url": "https://example.com/a", "kind": "已披露监管记录"},
        ]
        cites = narrative_fx._citations(scan, None)
        self.assertEqual(sum(1 for c in cites if c["title"] == "关于收到行政处罚的公告"), 1)
        self.assertEqual(cites[0]["url"], "https://example.com/a")


class TestGenerateAndCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(self._tmp.name)
        )
        self._patch.start()
        self._inputs = {
            "name": "测试公司", "industry": "测试行业",
            "financials": [
                fr("2024-12-31", revenue=100.0, net_profit=100.0),
                fr("2025-12-31", revenue=120.0, net_profit=80.0),
            ],
            "statement_items": [], "kline": [], "integrity": [], "announcements": [],
            "forecasts": [], "insider_trades": [], "pledges": [], "corporate_actions": [],
        }

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _run(self, **kwargs):
        with mock.patch.object(narrative_fx, "_load_inputs", return_value=self._inputs), mock.patch.object(
            narrative_fx, "load_intel_digest", return_value=None
        ), mock.patch.object(narrative_fx, "has_live_llm", return_value=False):
            return narrative_fx.generate_financial_forensics("TEST.SH", **kwargs)

    def test_cache_hit_and_refresh(self):
        first = self._run()
        self.assertEqual(first["source"], "template")
        path = narrative_fx._forensics_cache_path("TEST.SH")
        self.assertTrue(path.exists())
        second = self._run()
        self.assertEqual(first["generated_at"], second["generated_at"])
        third = self._run(refresh=True)
        self.assertGreaterEqual(third["generated_at"], first["generated_at"])

    def test_stale_version_invalidates_cache(self):
        first = self._run()
        path = narrative_fx._forensics_cache_path("TEST.SH")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["forensics_version"] = 0
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        again = self._run()
        self.assertEqual(again["forensics_version"], narrative_fx._FORENSICS_VERSION)

    def test_missing_stock_returns_error(self):
        with mock.patch.object(narrative_fx, "_load_inputs", return_value=None):
            out = narrative_fx.generate_financial_forensics("NOPE.SH")
        self.assertIn("error", out)

    def test_llm_path_uses_normalized_report(self):
        raw = {
            "verdict": {"level": "medium", "headline": "h", "essence": "e"},
            "analyses": [], "cross_checks": ["x"], "caveats": [],
        }
        with mock.patch.object(narrative_fx, "_load_inputs", return_value=self._inputs), mock.patch.object(
            narrative_fx, "load_intel_digest", return_value=None
        ), mock.patch.object(narrative_fx, "has_live_llm", return_value=True), mock.patch.object(
            narrative_fx, "load_prompt", return_value="system"
        ), mock.patch.object(narrative_fx, "chat_json", return_value=raw):
            out = narrative_fx.generate_financial_forensics("TEST.SH", refresh=True)
        self.assertEqual(out["source"], "llm")
        self.assertTrue(any("不构成" in c for c in out["caveats"]))  # 免责硬兜底
        self.assertEqual(out["verdict"]["headline"], "h")


class TestApiRoute(unittest.TestCase):
    def test_route_registered(self):
        paths = set()

        def collect(routes):
            for route in routes:
                paths.add(getattr(route, "path", ""))

        collect(app.routes)
        # include_router 的路由在 _IncludedRouter.original_router 下
        for route in app.routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                collect(getattr(original, "routes", []))
        self.assertIn("/api/stock/{symbol}/financial-forensics", paths)

    def test_route_returns_payload(self):
        with mock.patch.object(api, "generate_financial_forensics", return_value={"symbol": "X", "source": "template"}):
            out = api.get_financial_forensics("X")
        self.assertEqual(out["symbol"], "X")

    def test_route_raises_on_error(self):
        from fastapi import HTTPException

        with mock.patch.object(api, "generate_financial_forensics", return_value={"error": "股票不存在"}):
            with self.assertRaises(HTTPException) as ctx:
                api.get_financial_forensics("X")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
