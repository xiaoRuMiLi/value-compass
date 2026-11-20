"""资本运作与治理线索扫描测试。

覆盖：突击分红/扩充股本/异常资本支出/商誉突增的自身历史离群判定，
公告关键词线索（收购/跨界/股权激励/关联交易），以及样本不足时的安全降级。
"""

from __future__ import annotations

import unittest

from fundamental.analysis.forensics import _scan_capital_operations
from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import (
    Announcement,
    DividendRecord,
    ReportType,
    StatementItem,
    StatementType,
)


def stmt(code: str, date: str, value: float, report_type: ReportType = ReportType.FY) -> StatementItem:
    return StatementItem(
        report_date=date, report_type=report_type, statement_type=StatementType.BALANCE,
        item_code=code, item_name=code, item_value=value, item_yoy=None,
    )


def title_of(signals: list[dict]) -> list[str]:
    return [s["title"] for s in signals]


class TestCapitalOperations(unittest.TestCase):
    def setUp(self):
        self.cfg = ForensicsConfig(
            dividend_mad_multiplier=3.0,
            share_capital_growth_pct=10.0,
            capex_mad_multiplier=3.0,
            goodwill_mad_multiplier=3.0,
            mad_min_samples=3,
        )

    def _run(self, dividends=None, items=None, anns=None):
        signals: list[dict] = []
        anomalies: list[dict] = []
        coverage: dict = {"performed": [], "skipped": []}
        result = _scan_capital_operations(
            dividends or [], items or [], anns or [], signals, anomalies, coverage, self.cfg
        )
        return signals, anomalies, coverage, result

    # ---- 突击分红 ----
    def test_dividend_outlier_detected(self):
        # 历史稳定在 1.0 元，最新年度突然 5.0 元
        divs = [
            DividendRecord(plan_notice_date=f"{y}-04-01", cash_per_share=1.0)
            for y in range(2020, 2025)
        ] + [DividendRecord(plan_notice_date="2025-04-01", cash_per_share=5.0)]
        signals, _, _, result = self._run(dividends=divs)
        self.assertIn("异常突击分红", title_of(signals))
        self.assertEqual(result["dividend_outliers"][0]["year"], 2025)

    def test_stable_dividend_not_flagged(self):
        divs = [
            DividendRecord(plan_notice_date=f"{y}-04-01", cash_per_share=1.0 + 0.01 * i)
            for i, y in enumerate(range(2020, 2026))
        ]
        signals, _, _, _ = self._run(dividends=divs)
        self.assertNotIn("异常突击分红", title_of(signals))

    def test_dividend_same_year_summed(self):
        # 同年两次派现应合并求和（0.5 + 0.6 = 1.1），不能当成两个年度
        divs = [
            DividendRecord(plan_notice_date=f"{y}-04-01", cash_per_share=1.0)
            for y in range(2020, 2025)
        ] + [
            DividendRecord(plan_notice_date="2025-04-01", cash_per_share=0.5),
            DividendRecord(plan_notice_date="2025-10-01", cash_per_share=0.6),
            DividendRecord(plan_notice_date="2025-11-01", cash_per_share=4.0),
        ]
        _, _, _, result = self._run(dividends=divs)
        self.assertEqual(len(result["dividend_outliers"]), 1)
        self.assertAlmostEqual(result["dividend_outliers"][0]["value"], 5.1)

    # ---- 扩充股本 ----
    def test_share_capital_expansion(self):
        items = [
            stmt("SHARE_CAPITAL", f"{y}-12-31", 10e8) for y in range(2020, 2025)
        ] + [stmt("SHARE_CAPITAL", "2025-12-31", 12e8)]  # +20%
        signals, _, _, result = self._run(items=items)
        self.assertIn("扩充股本", title_of(signals))
        self.assertGreater(result["share_capital_jumps"][0]["growth"], 10.0)

    def test_share_capital_stable_not_flagged(self):
        items = [stmt("SHARE_CAPITAL", f"{y}-12-31", 10e8) for y in range(2020, 2026)]
        signals, _, _, _ = self._run(items=items)
        self.assertNotIn("扩充股本", title_of(signals))

    # ---- 异常资本支出 ----
    def test_capex_outlier(self):
        items = []
        for y in range(2020, 2025):
            items.append(stmt("NETCASH_INVEST", f"{y}-12-31", -1e8))  # 净流出 1 亿
            items.append(stmt("CIP", f"{y}-12-31", 2e8))
        items.append(stmt("NETCASH_INVEST", "2025-12-31", -8e8))  # 突然 8 亿
        items.append(stmt("CIP", "2025-12-31", 2e8))
        signals, anomalies, _, result = self._run(items=items)
        self.assertIn("异常资本支出", title_of(signals))
        self.assertTrue(any(a["metric"] == "投资活动现金净流出" for a in anomalies))
        self.assertEqual(len(result["capex_outliers"]), 1)

    def test_quarterly_ignored_for_capex(self):
        # 季报累计口径不能混入年报基线
        items = [stmt("NETCASH_INVEST", f"{y}-12-31", -1e8) for y in range(2020, 2025)]
        items.append(stmt("NETCASH_INVEST", "2025-09-30", -99e8, ReportType.Q3))
        signals, _, _, _ = self._run(items=items)
        self.assertNotIn("异常资本支出", title_of(signals))

    # ---- 商誉突增 ----
    def test_goodwill_spike(self):
        items = [stmt("GOODWILL", f"{y}-12-31", 1e8) for y in range(2020, 2025)]
        items.append(stmt("GOODWILL", "2025-12-31", 9e8))
        signals, _, _, result = self._run(items=items)
        self.assertIn("商誉显著增加（收购线索）", title_of(signals))
        self.assertEqual(result["goodwill_outliers"][0]["year"], 2025)

    # ---- 公告关键词线索 ----
    def test_announcement_keywords(self):
        anns = [
            Announcement(title="关于收购XX公司100%股权的公告", publish_date="2026-01-10"),
            Announcement(title="2026年限制性股票激励计划草案", publish_date="2026-02-10"),
            Announcement(title="关于日常关联交易的公告", publish_date="2026-03-10"),
            Announcement(title="关于投资设立境外子公司的公告", publish_date="2026-04-10"),
            Announcement(title="关于召开股东会的通知", publish_date="2026-05-10"),
        ]
        signals, _, _, result = self._run(anns=anns)
        titles = title_of(signals)
        self.assertIn("新增收购", titles)
        self.assertIn("异常股权激励", titles)
        self.assertIn("关联交易", titles)
        self.assertIn("跨界投资", titles)
        # 无关公告不应命中
        self.assertEqual(len(result["action_hits"]), 4)

    # ---- 安全降级 ----
    def test_no_data_no_crash(self):
        signals, anomalies, coverage, result = self._run()
        self.assertEqual(signals, [])
        self.assertEqual(anomalies, [])
        self.assertTrue(any(x["check"] == "异常突击分红" for x in coverage["skipped"]))
        self.assertTrue(any(x["check"] == "扩充股本" for x in coverage["skipped"]))
        self.assertFalse(any(result.values()))

    def test_insufficient_samples_skipped(self):
        # 只有 2 年分红、1 年股本，不应触发也不应崩
        divs = [DividendRecord(plan_notice_date="2024-04-01", cash_per_share=1.0),
                DividendRecord(plan_notice_date="2025-04-01", cash_per_share=9.0)]
        items = [stmt("SHARE_CAPITAL", "2025-12-31", 10e8)]
        signals, _, coverage, _ = self._run(dividends=divs, items=items)
        self.assertEqual(signals, [])
        self.assertTrue(any(x["check"] == "异常突击分红" for x in coverage["skipped"]))
        self.assertTrue(any(x["check"] == "扩充股本" for x in coverage["skipped"]))


if __name__ == "__main__":
    unittest.main()
