"""财务戏法检查的回归测试：重点锁住「准确度」而不是「能不能报出来」。

覆盖：结论窗口裁剪（不让老数据下结论）、组合条件防误报、最新年份取值、
制度不适用显式标注、核数师报告条件句不误判、ocf 回填边界。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from fundamental.analysis.forensic_patterns import (
    DEFAULT_THRESHOLDS,
    scan_fraud_patterns,
)
from fundamental.models import FinancialReport, ReportType
from fundamental.services.narrative._forensics import _fill_ocf_from_items
from fundamental.services.narrative._report_facts import _extract_hk


def _financial(date: str, **kw):
    base = dict(
        report_date=date, report_type=ReportType.FY, revenue=None, net_profit=None,
        net_profit_deducted=None, operating_profit=None, pretax_profit=None,
        gross_margin=None, net_margin=None, roe=None, roic=None,
        debt_ratio=None, current_ratio=None, ocf=None, fcf=None,
        eps=None, bps=None, revenue_yoy=None, net_profit_yoy=None,
        audit_opinion=None,
    )
    base.update(kw)
    return FinancialReport(**base)


def _item(date: str, code: str, value: float):
    return SimpleNamespace(
        report_date=date, report_type="FY", statement_type="BALANCE",
        item_code=code, item_name=code, item_value=value, item_yoy=None,
    )


def _scan(financials, items=(), *, market="A", facts=None, lookback=12):
    return scan_fraud_patterns(
        symbol="TEST", market=market, financials=list(financials),
        statement_items=list(items), report_facts=facts or {},
        lookback_periods=lookback,
    )


def _ids(result):
    return [p["id"] for p in result["patterns"]]


class TestConclusionWindow(unittest.TestCase):
    def test_old_periods_never_produce_conclusions(self):
        # 2008 年出现极端低净现比，2024-2026 一切正常：不允许拿 2008 报警
        fin = [
            _financial("2007-12-31", ocf=10, net_profit=100, net_margin=12),
            _financial("2008-12-31", ocf=5, net_profit=100, net_margin=10),
        ]
        for year in range(2022, 2027):
            fin.append(_financial(f"{year}-12-31", ocf=200, net_profit=100,
                                  net_margin=20))
        result = _scan(fin, lookback=5)
        for p in result["patterns"]:
            self.assertGreaterEqual(p["period"][:4], "2022")
        self.assertNotIn("FRD002", _ids(result))

    def test_window_keeps_prior_year_for_yoy(self):
        # 同比必须能配对上一年的同月同日，窗口不能把基期一起裁掉
        fin = [_financial(f"{y}-12-31", ocf=100, net_profit=100, net_margin=15)
               for y in range(2021, 2027)]
        result = _scan(fin)
        cov = result["coverage"]
        self.assertIsNotNone(cov["window_from"])
        self.assertIsNotNone(cov["window_to"])
        self.assertLessEqual(cov["window_from"], "2024-12-31")


class TestFalsePositiveGuards(unittest.TestCase):
    def test_high_growth_with_normal_cash_is_not_flagged(self):
        # 营收与应收同步增长、净现比健康：不该报虚增收入或利润含金量
        fin = []
        items = []
        ar, rev, ocf, profit = 100.0, 200.0, 120.0, 100.0
        for year in range(2021, 2027):
            fin.append(_financial(f"{year}-12-31", revenue=rev, net_profit=profit,
                                  ocf=ocf, net_margin=15))
            items.append(_item(f"{year}-12-31", "OPERATE_INCOME", rev))
            items.append(_item(f"{year}-12-31", "ACCOUNTS_RECE", ar))
            ar *= 1.3
            rev *= 1.3
            profit *= 1.3
            ocf *= 1.3
        result = _scan(fin, items)
        self.assertNotIn("FRD001", _ids(result))
        self.assertNotIn("FRD002", _ids(result))

    def test_ar_divergence_needs_two_conditions_and_two_periods(self):
        fin = []
        items = []
        ar, rev = 100.0, 100.0
        for year in range(2021, 2027):
            fin.append(_financial(f"{year}-12-31", revenue=rev, net_profit=50,
                                  ocf=50, net_margin=15))
            items.append(_item(f"{year}-12-31", "OPERATE_INCOME", rev))
            items.append(_item(f"{year}-12-31", "ACCOUNTS_RECE", ar))
            ar *= 1.8     # 应收 +80%
            rev *= 1.1    # 营收 +10%
        result = _scan(fin, items)
        self.assertIn("FRD001", _ids(result))
        p = next(x for x in result["patterns"] if x["id"] == "FRD001")
        self.assertGreaterEqual(p["facts"]["confirm_periods"], 2)
        self.assertEqual(p["period"], "2026-12-31")

    def test_single_low_cash_period_does_not_trigger(self):
        fin = [_financial(f"{y}-12-31", ocf=120, net_profit=100, net_margin=15)
               for y in range(2021, 2026)]
        fin.append(_financial("2026-12-31", ocf=40, net_profit=100, net_margin=15))
        result = _scan(fin)
        # 单期偏低且无应收/存货同步膨胀证据，不应报
        self.assertNotIn("FRD002", _ids(result))

    def test_consecutive_low_cash_triggers(self):
        fin = [_financial(f"{y}-12-31", ocf=120, net_profit=100, net_margin=15)
               for y in range(2021, 2024)]
        for year in range(2024, 2027):
            fin.append(_financial(f"{year}-12-31", ocf=40, net_profit=100,
                                  net_margin=15))
        result = _scan(fin)
        self.assertIn("FRD002", _ids(result))

    def test_big_bath_requires_nearby_rebound(self):
        fin = [
            _financial("2015-12-31", net_margin=20, net_profit=100),
            _financial("2016-12-31", net_margin=-60, net_profit=-50),
            # 低谷次年仍亏损，反弹幅度不足；再往后的高增长与低谷相隔多年，
            # 不能拿十年前的低谷去解释当下的利润跳升。
            _financial("2017-12-31", net_margin=-5, net_profit=-10),
        ]
        for year in range(2018, 2026):
            fin.append(_financial(f"{year}-12-31", net_margin=15, net_profit=100))
        fin.append(_financial("2026-12-31", net_margin=18, net_profit=400))
        result = _scan(fin)
        self.assertNotIn("FRD005", _ids(result))


class TestReportFactsChecks(unittest.TestCase):
    def test_customer_concentration_uses_latest_year(self):
        facts = {
            "top5_customer_pct": [
                {"period": "2025", "value": 75.98, "quote": "75.98%"},
                {"period": "2024", "value": 74.74, "quote": "74.74%"},
                {"period": "2023", "value": 73.71, "quote": "73.71%"},
            ],
            "related_party_sales_pct": [
                {"period": "2025", "value": 8.10, "quote": "8.10%"},
                {"period": "2024", "value": 0.0, "quote": "0.00%"},
                {"period": "2023", "value": 0.0, "quote": "0.00%"},
            ],
        }
        result = _scan([], facts=facts)
        self.assertIn("FRD008", _ids(result))
        p = next(x for x in result["patterns"] if x["id"] == "FRD008")
        self.assertEqual(p["period"], "2025")
        self.assertEqual(p["severity"], "high")
        self.assertIn("8.1%", p["explanation"])
        self.assertTrue(p["evidence"])

    def test_consolidation_change_requires_entity_count(self):
        facts = {
            "accounting_policy_change": [{"period": "2025", "applicable": False}],
            "consolidation_change": [
                {"period": "2025", "applicable": True, "entities": ["A", "B", "C"]},
            ],
            "audit": [{"period": "2025", "non_standard": False}],
        }
        result = _scan([], facts=facts)
        self.assertNotIn("FRD010", _ids(result))
        facts["consolidation_change"][0]["entities"] = [str(i) for i in range(9)]
        result = _scan([], facts=facts)
        self.assertIn("FRD010", _ids(result))

    def test_no_qualitative_conclusion_in_explanations(self):
        facts = {
            "top5_customer_pct": [{"period": "2025", "value": 75.98, "quote": "x"}],
            "related_party_sales_pct": [{"period": "2025", "value": 8.1, "quote": "x"}],
        }
        result = _scan([], facts=facts)
        for p in result["patterns"]:
            for forbidden in ("财务造假认定", "已确认造假", "属于操纵", "构成粉饰"):
                self.assertNotIn(forbidden, p["explanation"])
            self.assertIn("不构成", p["disclaimer"])


class TestMarketApplicability(unittest.TestCase):
    def test_hk_inventory_is_not_applicable_without_cost(self):
        fin = [_financial(f"{y}-12-31", revenue=100, net_profit=50,
                          net_margin=15, ocf=60) for y in range(2021, 2027)]
        items = []
        for year in range(2021, 2027):
            items.append(_item(f"{year}-12-31", "004001001", 100 * (1.3 ** (year - 2021))))
            items.append(_item(f"{year}-12-31", "004002001", 100 * (1.8 ** (year - 2021))))
        result = _scan(fin, items, market="HK")
        reasons = {s["check"]: s["reason"] for s in result["coverage"]["skipped"]}
        self.assertEqual(reasons.get("存货异常堆积"), "not_applicable")
        self.assertEqual(reasons.get("销售收现比恶化"), "not_applicable")
        self.assertNotIn("FRD004", _ids(result))

    def test_skipped_reasons_are_three_way(self):
        result = _scan([], market="HK")
        allowed = {"data_missing", "not_applicable", "rule_not_triggered"}
        for s in result["coverage"]["skipped"]:
            self.assertIn(s["reason"], allowed)

    def test_hk_auditor_conditional_sentence_is_not_a_qualified_opinion(self):
        # 「假若披露不足，則我們應當發表非無保留意見」是条件句，不能判非标
        text = ("我們認為該等綜合財務報表真實而公平地反映本集團狀況。"
                "假若有關的披露不足，則我們應當發表非無保留意見。")
        out = _extract_hk(text, "2025", "2025 年报")
        self.assertFalse(out["audit"][0]["non_standard"])
        self.assertEqual(out["audit"][0]["opinion"], "unqualified")

    def test_hk_unknown_opinion_is_not_asserted(self):
        out = _extract_hk("假若披露不足，則我們應當發表非無保留意見。", "2025", "x")
        self.assertIsNone(out["audit"][0]["non_standard"])
        self.assertEqual(out["audit"][0]["opinion"], "unknown")


class TestOcfBackfill(unittest.TestCase):
    def test_fills_only_missing_and_keeps_existing(self):
        fin = [
            _financial("2024-12-31", ocf=None),
            _financial("2025-12-31", ocf=999.0),
        ]
        items = [
            _item("2024-12-31", "NETCASH_OPERATE", 123.0),
            _item("2025-12-31", "NETCASH_OPERATE", 456.0),
        ]
        filled = _fill_ocf_from_items(fin, items)
        self.assertEqual(filled[0].ocf, 123.0)
        self.assertEqual(filled[1].ocf, 999.0)

    def test_no_matching_items_is_noop(self):
        fin = [_financial("2024-12-31", ocf=None)]
        self.assertIsNone(_fill_ocf_from_items(fin, [])[0].ocf)


class TestThresholds(unittest.TestCase):
    def test_defaults_are_explicit_and_overridable(self):
        self.assertIn("ar_divergence_pp", DEFAULT_THRESHOLDS)
        self.assertGreater(DEFAULT_THRESHOLDS["cash_conversion_low"], 0)


if __name__ == "__main__":
    unittest.main()
