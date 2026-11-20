"""财报体检阈值配置化测试。

覆盖：配置文件 → 配置对象的加载链路、非法值逐项兜底、配置指纹、
阈值确实改变扫描结果、同源科目聚类与聚合开关、缓存随阈值改动自动失效。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fundamental.analysis import forensics as fx
from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import FinancialReport, ReportType, StatementItem, StatementType
from fundamental.services import forensics_config as svc_cfg
from fundamental.services.narrative import _common, _forensics as narrative_fx


def fr(day: str, **kw) -> FinancialReport:
    return FinancialReport(report_date=day, report_type=ReportType.FY, **kw)


def titles(scan: dict) -> list[str]:
    return [s["title"] for s in scan["signals"]]


class TestConfigLoading(unittest.TestCase):
    def test_service_injects_settings_into_config(self):
        fake_settings = mock.Mock()
        fake_settings.get.return_value = {
            "lookback_periods": 7,
            "thresholds": {"net_margin_pp": 42.0, "consolidate_min_occurrences": 4},
        }
        with mock.patch.object(svc_cfg, "load_settings", return_value=fake_settings):
            cfg = svc_cfg.load_forensics_config()
        self.assertEqual(cfg.net_margin_pp, 42.0)
        self.assertEqual(cfg.lookback_periods, 7)
        self.assertEqual(cfg.consolidate_min_occurrences, 4)
        # 未配置的字段回退到内置默认值，而不是变成 None
        self.assertEqual(cfg.roe_pp, ForensicsConfig().roe_pp)
        fake_settings.get.assert_called_once_with("forensics")

    def test_default_toml_is_merged_into_settings(self):
        from fundamental.config import load_settings

        settings = load_settings()
        self.assertIsInstance(settings.get("forensics.thresholds.net_margin_pp"), float)
        self.assertEqual(settings.get("forensics.lookback_periods"), 12)
        self.assertEqual(settings.get("forensics.thresholds.expense_cluster_min_items"), 2)

    def test_bad_values_fall_back_item_by_item(self):
        cfg = ForensicsConfig.from_mapping(
            {
                "thresholds": {
                    "net_margin_pp": "oops",        # 类型错误 → 默认值
                    "roe_pp": -5,                   # 越界 → 钳到 0
                    "lookback_periods": None,       # None → 默认值
                    "rate_require_outlier": ["net_margin", "bogus", 123],
                    "risk_title_keywords": [],
                }
            }
        )
        base = ForensicsConfig()
        self.assertEqual(cfg.net_margin_pp, base.net_margin_pp)
        self.assertEqual(cfg.roe_pp, 0.0)
        self.assertEqual(cfg.lookback_periods, base.lookback_periods)
        self.assertEqual(cfg.rate_require_outlier, ("net_margin",))
        self.assertEqual(cfg.risk_title_keywords, base.risk_title_keywords)

    def test_fingerprint_is_stable_and_sensitive(self):
        a = ForensicsConfig()
        self.assertEqual(a.fingerprint(), ForensicsConfig().fingerprint())
        self.assertNotEqual(a.fingerprint(), ForensicsConfig(net_margin_pp=9.0).fingerprint())
        self.assertIn("net_margin_pp", a.as_dict())
        self.assertIsInstance(a.as_dict()["rate_require_outlier"], list)


class TestThresholdsDriveScan(unittest.TestCase):
    def test_higher_threshold_suppresses_signal_and_window_records_fingerprint(self):
        rows = [fr(f"{y}-12-31", roe=30.0 - 6.0 * (y - 2021)) for y in range(2021, 2026)]
        loose = ForensicsConfig(roe_pp=3.0, consolidate_min_occurrences=3)
        strict = ForensicsConfig(roe_pp=20.0, consolidate_min_occurrences=0)
        loose_scan = fx.scan_forensics("T.SH", rows, config=loose)
        strict_scan = fx.scan_forensics("T.SH", rows, config=strict)
        self.assertIn("ROE 显著变化", titles(loose_scan))
        self.assertNotIn("ROE 显著变化", titles(strict_scan))
        self.assertEqual(loose_scan["window"]["config_fingerprint"], loose.fingerprint())
        self.assertEqual(strict_scan["window"]["config_fingerprint"], strict.fingerprint())

    def test_rate_require_outlier_filters_normal_historical_swings(self):
        # 净利率在 10% 与 30% 之间来回，是这家公司自己的常态；
        # 同比变化虽大，但相对自身历史并不离群。
        rows = [
            fr(f"{2018 + i}-12-31", net_margin=10.0 if i % 2 == 0 else 30.0)
            for i in range(8)
        ]
        plain = ForensicsConfig(net_margin_pp=5.0)
        gated = ForensicsConfig(net_margin_pp=5.0, rate_require_outlier=("net_margin",))
        self.assertIn("净利率异动", titles(fx.scan_forensics("T.SH", rows, config=plain)))
        self.assertNotIn("净利率异动", titles(fx.scan_forensics("T.SH", rows, config=gated)))

    def test_expense_cluster_merges_same_period_hits(self):
        items: list[StatementItem] = []
        for year, sale, manage in ((2024, 10.0, 10.0), (2025, 20.0, 20.0)):
            day = f"{year}-12-31"
            items += [
                StatementItem(day, ReportType.FY, StatementType.INCOME, "OPERATE_INCOME", "营业收入", 100.0),
                StatementItem(day, ReportType.FY, StatementType.INCOME, "SALE_EXPENSE", "销售费用", sale),
                StatementItem(day, ReportType.FY, StatementType.INCOME, "MANAGE_EXPENSE", "管理费用", manage),
            ]
        merged = fx.scan_forensics(
            "T.SH", [fr("2025-12-31")], statement_items=items,
            config=ForensicsConfig(expense_ratio_pp=5.0, expense_cluster_min_items=2),
        )
        split = fx.scan_forensics(
            "T.SH", [fr("2025-12-31")], statement_items=items,
            config=ForensicsConfig(expense_ratio_pp=5.0, expense_cluster_min_items=0),
        )
        self.assertIn("费用与成本结构同步异动", titles(merged))
        self.assertNotIn("销售费用率异动", titles(merged))
        self.assertIn("销售费用率异动", titles(split))
        self.assertIn("管理费用率异动", titles(split))
        # 合并只影响信号条数，异常波动明细一条都不能丢
        self.assertEqual(len(merged["anomalies"]), len(split["anomalies"]))

    def test_consolidation_can_be_disabled(self):
        rows = [fr(f"{y}-12-31", roe=30.0 - 6.0 * (y - 2021)) for y in range(2021, 2026)]
        cfg = ForensicsConfig(roe_pp=3.0, consolidate_min_occurrences=0)
        scan = fx.scan_forensics("T.SH", rows, config=cfg)
        self.assertEqual(scan["summary"]["consolidated"], 0)
        self.assertGreaterEqual(len([t for t in titles(scan) if t == "ROE 显著变化"]), 3)

    def test_risk_level_thresholds_are_configurable(self):
        # 这批数据会产出一条 high（增收不增利）；把 high 门槛调高后总体不应再判 high
        rows = [
            fr("2024-12-31", revenue=100.0, net_profit=100.0),
            fr("2025-12-31", revenue=130.0, net_profit=70.0),
        ]
        scan = fx.scan_forensics("T.SH", rows, config=ForensicsConfig(risk_high_min_high=99))
        self.assertNotEqual(scan["summary"]["risk_level"], "high")


class TestCacheFingerprint(unittest.TestCase):
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

    def _run(self):
        with mock.patch.object(narrative_fx, "_load_inputs", return_value=self._inputs), mock.patch.object(
            narrative_fx, "load_intel_digest", return_value=None
        ), mock.patch.object(narrative_fx, "has_live_llm", return_value=False):
            return narrative_fx.generate_financial_forensics("TEST.SH")

    def test_threshold_change_invalidates_cache_without_refresh(self):
        first = self._run()
        path = narrative_fx._forensics_cache_path("TEST.SH")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["thresholds_fingerprint"], first["thresholds_fingerprint"])
        self.assertIn("net_margin_pp", payload["thresholds"])

        changed = ForensicsConfig(net_margin_pp=99.0)
        with mock.patch.object(narrative_fx, "load_forensics_config", return_value=changed):
            again = self._run()
        self.assertEqual(again["thresholds"]["net_margin_pp"], 99.0)
        self.assertNotEqual(again["thresholds_fingerprint"], first["thresholds_fingerprint"])


if __name__ == "__main__":
    unittest.main()
