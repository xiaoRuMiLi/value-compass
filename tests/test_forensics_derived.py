"""单季还原 / 原文事实趋势信号 / 非法 JSON 重试的回归测试。

覆盖三层边界：
1. analysis 层只吃纯数据 + 配置对象，就能算出单季与现金含量对照；
2. services 层抽取的原文事实（纯 dict）注入扫描器后按阈值出线索，缺失写 coverage；
3. provider 与分段挖掘对非法 JSON 的处理不能静默吞掉或捏造内容。
"""

from __future__ import annotations

import unittest
from unittest import mock

from fundamental.analysis import forensics as fx
from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import ChatMessage, FinancialReport, ReportType
from fundamental.providers.llm.openai_compat import _extract_json
from fundamental.providers.llm.port import LlmError
from fundamental.services.forensics_config import load_forensics_config
from fundamental.services.narrative import _audit_evidence as ae
from fundamental.services.narrative import _report_facts as rf
from fundamental.services.narrative import _report_mining as mining
from fundamental.services.narrative import _forensics as narrative_fx


def fr(day: str, rt: ReportType = ReportType.FY, **kw) -> FinancialReport:
    return FinancialReport(report_date=day, report_type=rt, **kw)


def wly_rows() -> list[FinancialReport]:
    """五粮液 2025/2026 累计口径（单位：元），Q2 单季利润仅 2.08 亿元。"""

    return [
        fr("2025-03-31", ReportType.Q1, net_profit=44.16e8),
        fr("2025-06-30", ReportType.H1, net_profit=46.24e8),
        fr("2025-09-30", ReportType.Q3, net_profit=64.75e8, ocf=282.47e8),
        fr("2025-12-31", ReportType.FY, net_profit=89.54e8, ocf=297.06e8),
        fr("2026-03-31", ReportType.Q1, net_profit=80.63e8, ocf=-25.35e8),
        fr("2026-06-30", ReportType.H1, net_profit=87.53e8, ocf=-21.54e8),
        fr("2026-09-30", ReportType.Q3, net_profit=97.11e8, ocf=0.47e8),
    ]


def titles(scan: dict) -> list[str]:
    return [s["title"] for s in scan["signals"]]


class TestQuarterDerivation(unittest.TestCase):
    def test_cumulative_rows_become_single_quarters(self):
        derived = fx._derive_quarters(wly_rows(), lookback=12)
        q2025 = derived[2025]["quarters"]
        self.assertEqual([q["label"] for q in q2025],
                         ["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4"])
        self.assertAlmostEqual(q2025[1]["net_profit"] / 1e8, 2.08, places=2)
        self.assertAlmostEqual(q2025[3]["net_profit"] / 1e8, 24.79, places=2)
        self.assertAlmostEqual(q2025[3]["ocf"] / 1e8, 14.59, places=2)
        # 2025 前两期没有累计现金流，Q3 不能凭空差分出单季值
        self.assertIsNone(q2025[2]["ocf"])
        q2026 = derived[2026]["quarters"]
        self.assertAlmostEqual(q2026[0]["ocf"] / 1e8, -25.35, places=2)
        self.assertAlmostEqual(q2026[1]["ocf"] / 1e8, 3.81, places=2)

    def test_no_cross_year_subtraction(self):
        rows = [
            fr("2024-12-31", net_profit=100e8),
            fr("2025-03-31", ReportType.Q1, net_profit=30e8),
        ]
        q = fx._derive_quarters(rows, lookback=12)[2025]["quarters"][0]
        self.assertEqual(q["label"], "2025 Q1")
        self.assertEqual(q["net_profit"], 30e8)
        self.assertTrue(q["is_single"])

    def test_missing_quarter_is_marked_as_interval(self):
        rows = [
            fr("2025-03-31", ReportType.Q1, net_profit=10e8),
            fr("2025-09-30", ReportType.Q3, net_profit=40e8),
        ]
        q = fx._derive_quarters(rows, lookback=12)[2025]["quarters"][-1]
        self.assertEqual(q["label"], "2025 Q2~Q3")
        self.assertFalse(q["is_single"])


class TestQuarterlySignals(unittest.TestCase):
    def test_three_new_clues_are_produced_from_real_shaped_data(self):
        scan = fx.scan_forensics("000858.SZ", wly_rows())
        self.assertIn("单季利润分布极不均衡", titles(scan))
        self.assertIn("单季经营现金流大额净流出", titles(scan))
        self.assertIn("最新累计期现金含量显著低于上一完整年度", titles(scan))
        annual = scan["quarterly"]["annual_cash_conversion"]
        latest = scan["quarterly"]["latest_cumulative_cash_conversion"]
        self.assertAlmostEqual(annual["ratio"], 3.317, places=2)
        self.assertEqual(latest["period"], "2026-09-30")
        self.assertLess(latest["ratio"], 0.01)
        # 新线索都是待解释项，不得抬高 high 计数
        for sig in scan["signals"]:
            if sig["title"].startswith(("单季", "最新累计期")):
                self.assertIn(sig["severity"], ("low", "medium"))

    def test_thresholds_are_configurable_and_still_in_fingerprint(self):
        loose = ForensicsConfig(quarter_profit_gap_x=99.0,
                               quarter_ocf_outflow_pct=999.0,
                               cash_conversion_healthy=999.0)
        scan = fx.scan_forensics("000858.SZ", wly_rows(), config=loose)
        for title in ("单季利润分布极不均衡", "单季经营现金流大额净流出",
                      "最新累计期现金含量显著低于上一完整年度"):
            self.assertNotIn(title, titles(scan))
        self.assertNotEqual(ForensicsConfig().fingerprint(), loose.fingerprint())
        self.assertEqual(scan["window"]["config_fingerprint"], loose.fingerprint())

    def test_new_config_section_is_loaded_from_toml(self):
        cfg = load_forensics_config()
        self.assertEqual(cfg.quarter_min_periods, 4)
        self.assertEqual(cfg.related_trend_pp, 1.0)
        self.assertEqual(cfg.consolidation_min_entities, 1)
        self.assertEqual(cfg.as_dict()["volume_sales_drop_pct"], 20.0)

    def test_empty_financials_declare_skipped(self):
        scan = fx.scan_forensics("T.SH", [])
        checks = [x["check"] for x in scan["coverage"]["skipped"]]
        self.assertIn("单季还原与分布异常", checks)
        self.assertEqual(scan["quarterly"]["quarters"], [])


class TestEvidenceFactSignals(unittest.TestCase):
    def setUp(self):
        self.report_facts = {
            "related_party_sales_pct": [
                {"period": "2024", "value": 0.0, "source": "2024 年报", "quote": "0.00%"},
                {"period": "2025", "value": 2.06, "source": "2025 年报", "quote": "2.06%"},
            ],
            "related_party_purchase_pct": [
                {"period": "2024", "value": 11.74, "source": "2024 年报", "quote": "11.74%"},
                {"period": "2025", "value": 13.38, "source": "2025 年报", "quote": "13.38%"},
            ],
        }
        self.audit_evidence = {
            "operating_volume": [{
                "period": "2025", "source": "2025 年报",
                "sales_volume_yoy_pct": -30.26, "inventory_volume_yoy_pct": 45.18,
                "dealer_change": -90, "sales_volume_ton": 112960,
                "sales_volume_prev_ton": 161984,
                "quote_volume": "酒类 销售量 吨 112,960 161,984 -30.26%",
            }],
            "consolidation_scope": [{
                "period": "2025", "source": "2025 年报", "applicable": True,
                "entities": ["宜宾五粮液科技创新有限公司"], "entity_count": 1,
                "reason": "出资设立宜宾五粮液科技创新有限公司", "quote": "☑是 □否",
            }],
        }

    def test_scan_forensics_accepts_pure_fact_dicts(self):
        scan = fx.scan_forensics(
            "000858.SZ", wly_rows(),
            report_facts=self.report_facts, audit_evidence=self.audit_evidence,
        )
        self.assertIn("关联方交易占比连续抬升", titles(scan))
        self.assertIn("实物销量下滑与库存、渠道同步承压", titles(scan))
        self.assertIn("合并范围新增主体影响同比可比性", titles(scan))
        structured = scan["evidence"]["structured"]
        self.assertEqual(len(structured["related_party_trend"]), 2)
        self.assertEqual(structured["consolidation_scope"]["entity_count"], 1)

    def test_stricter_thresholds_suppress_signals(self):
        cfg = ForensicsConfig(related_trend_pp=99.0, volume_sales_drop_pct=99.0,
                              consolidation_min_entities=0)
        scan = fx.scan_forensics(
            "000858.SZ", wly_rows(), config=cfg,
            report_facts=self.report_facts, audit_evidence=self.audit_evidence,
        )
        for title in ("关联方交易占比连续抬升", "实物销量下滑与库存、渠道同步承压",
                      "合并范围新增主体影响同比可比性"):
            self.assertNotIn(title, titles(scan))
        checks = [x["check"] for x in scan["coverage"]["skipped"]]
        self.assertIn("合并范围新增主体", checks)

    def test_missing_facts_are_declared_not_silently_ignored(self):
        scan = fx.scan_forensics("T.SH", wly_rows())
        checks = [x["check"] for x in scan["coverage"]["skipped"]]
        self.assertIn("关联方交易占比趋势", checks)
        self.assertIn("实物量组合信号", checks)
        self.assertIn("合并范围新增主体", checks)


class TestNewExtractors(unittest.TestCase):
    def test_related_party_purchase_is_extracted(self):
        text = (
            "公司主要供应商情况 前五名供应商合计采购金额占年度采购总额比例 29.57% "
            "前五名供应商采购额中关联方采购额占年度采购总额比例 13.38% "
            "公司前5 名供应商资料"
        )
        out = rf._extract_a(text, "2025", "2025 年报")
        self.assertEqual(out["related_party_purchase_pct"][0]["value"], 13.38)

    def test_citation_orders_related_party_series_by_year(self):
        scan = {"evidence": {"regulatory": [], "announcement_hits": [],
                             "external_views": [], "price_reactions": []}}
        facts = {
            "related_party_purchase_pct": [
                {"period": "2025", "value": 13.38, "source": "2025 年报"},
                {"period": "2024", "value": 11.74, "source": "2024 年报"},
            ],
        }
        cites = narrative_fx._citations(scan, None, {}, facts)
        title = next(c["title"] for c in cites if "关联方采购占比" in c["title"])
        self.assertIn("11.74% → 13.38%", title)

    def test_volume_table_and_consolidation_scope(self):
        text = (
            "（3）公司实物销售收入是否大于劳务收入 \n\uf052是 □否 \n"
            "酒类 \n销售量 \n吨 \n 112,960  \n161,984 \n-30.26%（注） \n"
            "生产量 \n吨 \n 127,777  \n146,461 \n-12.76% \n"
            "库存量 \n吨 \n 47,616  \n32,799 \n45.18%(注） \n"
            "2、经销商数量情况 小计 \n2,562 \n-90 \n"
            "（6）报告期内合并范围是否发生变动 \n\uf052是 □否 \n"
            "同意由本公司出资10,000.00 万元，设立宜宾\n五粮液科技创新有限公司，持股比例100%。\n"
            "（7）公司报告期内业务、产品或服务发生重大变化"
        )
        volume = ae._operating_volume(text, "2025", "2025 年报")[0]
        self.assertEqual(volume["sales_volume_yoy_pct"], -30.26)
        self.assertEqual(volume["inventory_volume_yoy_pct"], 45.18)
        self.assertEqual(volume["dealer_change"], -90)
        scope = ae._consolidation_scope(text, "2025", "2025 年报")[0]
        self.assertTrue(scope["applicable"])
        self.assertEqual(scope["entities"], ["宜宾五粮液科技创新有限公司"])


class TestJsonRobustness(unittest.TestCase):
    def test_fences_and_trailing_comma_are_parsed(self):
        self.assertEqual(_extract_json('```json\n{"a": 1,}\n```'), {"a": 1})
        self.assertEqual(_extract_json('说明：{"a": 1} 以上'), {"a": 1})

    def test_truncated_json_raises_llm_error_not_valueerror(self):
        with self.assertRaises(LlmError):
            _extract_json('{"excerpts": [{"quote": "半截')
        with self.assertRaises(LlmError):
            _extract_json("完全没有 JSON")

    def test_segment_retries_once_then_marks_failure(self):
        messages = [ChatMessage("system", "s"), ChatMessage("user", "u")]
        with mock.patch.object(mining, "chat_json", side_effect=[
                ValueError("bad json"), {"excerpts": [], "summary": "ok", "new_findings": []}]):
            out = mining._chat_segment_json(messages)
        self.assertEqual(out["summary"], "ok")
        with mock.patch.object(mining, "chat_json", side_effect=ValueError("still bad")):
            with self.assertRaises(ValueError):
                mining._chat_segment_json(messages)
        segment = {"section": "第一节", "text": "正文"}
        with mock.patch.object(mining, "chat_json", side_effect=ValueError("bad")):
            result = mining._map_segment(segment, [], "sys", {"source": "2025 年报"})
        self.assertTrue(result["parse_failed"])
        self.assertEqual(result["excerpts"], [])


if __name__ == "__main__":
    unittest.main()
