"""分析模块单元测试。"""

from __future__ import annotations

import unittest

from fundamental.analysis.cycle import assess_cycle
from fundamental.analysis.delisting import check_delisting
from fundamental.analysis.financial import score_financial
from fundamental.analysis.fraud_score import score_fraud
from fundamental.analysis.masters.buffett import BuffettModel
from fundamental.analysis.moat import assess_moat
from fundamental.analysis.price_correlation import compute_correlation
from fundamental.analysis.returns import compute_return
from fundamental.analysis.valuation import evaluate_dcf, evaluate_ddm, evaluate_graham
from fundamental.models import DividendRecord, FinancialReport, KlineBar, ReportType


class TestFinancialScore(unittest.TestCase):
    def test_strong_company(self):
        report = FinancialReport(
            "2026-09-30", ReportType.Q3, roe=25, gross_margin=60, net_margin=20,
            debt_ratio=20, current_ratio=2, revenue_yoy=15, net_profit_yoy=20,
        )
        result = score_financial(report, dividend_yield=2.5, pe_ttm=20)
        self.assertGreater(result.total, 80)
        self.assertEqual(result.rating, "强烈推荐")
        self.assertEqual(len(result.dimensions), 5)

    def test_weak_company(self):
        report = FinancialReport(
            "2026-09-30", ReportType.Q3, roe=5, gross_margin=10, net_margin=2,
            debt_ratio=80, current_ratio=0.5, revenue_yoy=-10, net_profit_yoy=-20,
        )
        result = score_financial(report)
        self.assertLess(result.total, 50)
        self.assertEqual(result.rating, "回避")

    def test_profitability_detail_shows_formula(self):
        report = FinancialReport(
            "2026-09-30", ReportType.Q3, revenue=100e8, net_profit=50e8,
            net_profit_deducted=48e8, gross_margin=80, net_margin=50, roe=25,
        )
        result = score_financial(report, selling_expense_ratio=5.0)
        dimension = result.dimensions[0]
        self.assertEqual(dimension.dimension, "盈利能力")
        self.assertIn("销售费用率", dimension.detail)
        self.assertIn("min(净利率/20%,1)×30", dimension.detail)

    def test_high_marketing_penalizes_profitability(self):
        report = FinancialReport(
            "2026-09-30", ReportType.Q3, revenue=100e8, net_profit=6e8,
            net_profit_deducted=5e8, gross_margin=68, net_margin=6, roe=10,
        )
        result = score_financial(report, selling_expense_ratio=38.0)
        self.assertLess(result.dimensions[0].score, 50)


class TestReturn(unittest.TestCase):
    def test_no_change(self):
        kline = [
            KlineBar("2026-01-01", 100, 100, 100, 100, 0, 0),
            KlineBar("2026-12-31", 100, 100, 100, 100, 0, 0),
        ]
        result = compute_return(kline, [], "2026-01-01", "2026-12-31")
        self.assertAlmostEqual(result.price_return, 0.0)
        self.assertAlmostEqual(result.total_return, 0.0)

    def test_gain_with_dividend(self):
        kline = [
            KlineBar("2026-01-01", 100, 100, 100, 100, 0, 0),
            KlineBar("2026-12-31", 110, 110, 110, 110, 0, 0),
        ]
        dividend = [DividendRecord(ex_date="2026-06-01", cash_per_share=10)]
        result = compute_return(kline, dividend, "2026-01-01", "2026-12-31")
        self.assertAlmostEqual(result.price_return, 0.1)
        self.assertAlmostEqual(result.dividend_return, 0.1)
        self.assertAlmostEqual(result.total_return, 0.2, places=5)


class TestValuation(unittest.TestCase):
    def test_graham(self):
        result = evaluate_graham(eps=5, bps=50, current_price=100)
        self.assertAlmostEqual(result.fair_value_low / 0.8, 75.0, places=2)

    def test_ddm(self):
        result = evaluate_ddm(dividend_per_share=2, current_price=100, growth=0.05, discount=0.08)
        self.assertAlmostEqual(result.fair_value_low / 0.8, 70.0, places=2)

    def test_dcf_invalid_growth(self):
        result = evaluate_dcf(fcf=1, shares=1, current_price=10, discount=0.08, terminal_growth=0.1)
        self.assertIsNone(result.margin_of_safety)


class TestCycleAndMoat(unittest.TestCase):
    def test_cycle_up(self):
        reports = [
            FinancialReport("2024-12-31", ReportType.FY, net_profit_yoy=10),
            FinancialReport("2025-12-31", ReportType.FY, net_profit_yoy=15),
        ]
        self.assertEqual(assess_cycle(reports).position, "上行")

    def test_moat_wide(self):
        reports = [
            FinancialReport("2024-12-31", ReportType.FY, gross_margin=60, roe=25),
            FinancialReport("2025-12-31", ReportType.FY, gross_margin=61, roe=26),
            FinancialReport("2026-12-31", ReportType.FY, gross_margin=62, roe=27),
        ]
        result = assess_moat("T", reports)
        self.assertEqual(result.rating, "宽")


class TestOthers(unittest.TestCase):
    def test_correlation_positive(self):
        result = compute_correlation("RB0", "1y", [1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
        self.assertAlmostEqual(result.correlation, 1.0)
        self.assertAlmostEqual(result.beta, 10.0)

    def test_fraud_clean(self):
        reports = [
            FinancialReport("2025-12-31", ReportType.FY, net_profit=100, net_profit_deducted=95, gross_margin=40),
            FinancialReport("2024-12-31", ReportType.FY, gross_margin=39),
        ]
        result = score_fraud("T", reports)
        self.assertEqual(result.score, 0)

    def test_delisting_price(self):
        result = check_delisting("T", 0.8, 1e10, [])
        self.assertIn("面值低于 1 元", result.triggers)

    def test_buffett_verdict(self):
        reports = [
            FinancialReport("2024-12-31", ReportType.FY, roe=20, gross_margin=50, debt_ratio=20, revenue_yoy=15),
            FinancialReport("2025-12-31", ReportType.FY, roe=21, gross_margin=51, debt_ratio=20, revenue_yoy=16),
            FinancialReport("2026-12-31", ReportType.FY, roe=22, gross_margin=52, debt_ratio=20, revenue_yoy=17),
        ]
        result = BuffettModel().evaluate("T", reports)
        self.assertEqual(result.verdict, "买入")


if __name__ == "__main__":
    unittest.main()
