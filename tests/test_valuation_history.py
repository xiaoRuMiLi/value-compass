"""K 线估值叠加纯计算的单元测试。"""

from __future__ import annotations

import unittest

from fundamental.analysis.valuation_history import build_aligned_series, ttm_eps_by_report


class TestValuationHistory(unittest.TestCase):
    def test_ttm_eps_uses_prior_fy_and_same_period(self):
        reports = [
            {"report_date": "2024-03-31", "report_type": "Q1", "eps": 2.0},
            {"report_date": "2024-12-31", "report_type": "FY", "eps": 10.0},
            {"report_date": "2025-03-31", "report_type": "Q1", "eps": 3.0},
        ]
        result = ttm_eps_by_report(reports)
        self.assertEqual(result["2024-12-31"], 10.0)
        self.assertAlmostEqual(result["2025-03-31"], 11.0)

    def test_ttm_eps_annualizes_when_prior_year_missing(self):
        reports = [{"report_date": "2025-06-30", "report_type": "H1", "eps": 4.0}]
        result = ttm_eps_by_report(reports)
        self.assertAlmostEqual(result["2025-06-30"], 8.0)

    def test_official_values_win_and_gaps_are_derived(self):
        reports = [
            {"report_date": "2024-12-31", "report_type": "FY", "eps": 10.0, "bps": 50.0, "roe": 0.2},
            {"report_date": "2025-12-31", "report_type": "FY", "eps": 12.0, "bps": 60.0, "roe": 0.22},
        ]
        series = build_aligned_series(
            ["2025-01-02", "2025-06-02"],
            [100.0, 120.0],
            {"2025-06-02": {"pe_ttm": 9.0, "pb_mrq": 1.5}},
            reports,
        )
        self.assertAlmostEqual(series["pe"][0], 10.0)
        self.assertEqual(series["pe_source"][0], "财报推导")
        self.assertAlmostEqual(series["pe"][1], 9.0)
        self.assertEqual(series["pe_source"][1], "日频")
        self.assertIsNone(series["pe_period"][1])
        self.assertAlmostEqual(series["roe"][1], 0.2)
        self.assertEqual(series["roe_period"][1], "2024-12-31")

    def test_hk_fx_applies_only_to_derived_series(self):
        reports = [{"report_date": "2024-12-31", "report_type": "FY", "eps": 10.0, "bps": 50.0, "roe": 0.2}]
        series = build_aligned_series(
            ["2025-01-02"], [100.0], {}, reports, fx_rate=0.85, prefer_official=False
        )
        self.assertAlmostEqual(series["pe"][0], 8.5)
        self.assertAlmostEqual(series["pb"][0], 1.7)

    def test_reported_eps_ttm_prevents_false_spike(self):
        # 2014Q1 累计 EPS 混用了拆股前后口径：0.7 + 1.693 - 2.204 会得到近零分母。
        reports = [
            {"report_date": "2013-12-31", "report_type": "FY", "eps": 1.693, "eps_ttm": 8.325, "bps": 6.37, "roe": 0.31},
            {"report_date": "2014-03-31", "report_type": "Q1", "eps": 0.7, "eps_ttm": 9.61, "bps": 7.095, "roe": 0.105},
        ]
        series = build_aligned_series(
            ["2014-04-15"], [500.0], {}, reports, fx_rate=0.85, prefer_official=False
        )
        self.assertAlmostEqual(series["pe"][0], 500.0 * 0.85 / 9.61, places=4)
        self.assertEqual(series["pe_source"][0], "财报EPS_TTM")

    def test_split_keeps_pe_and_pb_continuous(self):
        reports = [
            {"report_date": "2014-03-31", "report_type": "Q1", "eps": 0.7, "eps_ttm": 9.61, "bps": 7.095, "roe": 0.105},
            {"report_date": "2014-06-30", "report_type": "H1", "eps": 1.332, "eps_ttm": 2.148, "bps": 7.696, "roe": 0.192},
        ]
        dates = [f"2014-05-{day:02d}" for day in range(1, 31)]
        closes = [500.0] * 14 + [100.0] * 16
        series = build_aligned_series(dates, closes, {}, reports, fx_rate=0.85, prefer_official=False)
        before = series["pe"][13]
        after = series["pe"][14]
        self.assertAlmostEqual(before, 500.0 * 0.85 / 9.61, places=4)
        self.assertAlmostEqual(after, before, places=4)
        self.assertAlmostEqual(series["pb"][13], series["pb"][14], places=4)
        self.assertIn("拆股调整", series["pe_source"][14])


if __name__ == "__main__":
    unittest.main()
