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
from fundamental.analysis.valuation import (
    evaluate_dcf, evaluate_ddm, evaluate_fcf, evaluate_graham,
)
from fundamental.models import (
    DividendRecord, FinancialReport, KlineBar, ReportType, StatementItem, StatementType,
)
from fundamental.services.analyze import (
    _cashflow_quality_note, _cashflow_wc_changes, _fcf_components, _working_capital_changes,
)


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


class TestFcfDcf(unittest.TestCase):
    def test_standard_fcf_formula(self):
        base = evaluate_fcf(1e10, 1e9, 10.0)
        with_capex = evaluate_fcf(1e10, 1e9, 10.0, capex=1e9)
        with_interest = evaluate_fcf(
            1e10, 1e9, 10.0, interest_income=2e8, interest_expense=5e7
        )
        self.assertAlmostEqual(base.inputs["fcf"], 1e10)
        self.assertAlmostEqual(with_capex.inputs["fcf"], 9e9)
        self.assertAlmostEqual(with_interest.inputs["fcf"], 1e10 + 1.5e8)
        self.assertLess(with_capex.fair_value_low, base.fair_value_low)
        self.assertGreater(with_interest.fair_value_low, base.fair_value_low)

    def test_working_capital_is_prompt_only(self):
        wc = {
            "inventory": 2e8, "receivable": 1e8,
            "payable": 5e7, "prepaid": 3e7,
        }
        plain = evaluate_fcf(1e10, 1e9, 10.0)
        prompted = evaluate_fcf(
            1e10, 1e9, 10.0,
            working_capital=wc, working_capital_alert="应付项目阶段性增加，注意现金流质量",
        )
        self.assertEqual(prompted.method, "adjusted_fcf")
        self.assertEqual(prompted.fair_value_low, plain.fair_value_low)
        self.assertEqual(prompted.fair_value_high, plain.fair_value_high)
        self.assertEqual(prompted.inputs["working_capital_changes"], wc)
        self.assertIn("应付", prompted.assumptions["working_capital_alert"])
        self.assertNotIn("营运资本", prompted.assumptions["formula"].split("；")[0])

    def test_missing_or_invalid_inputs_return_empty(self):
        self.assertIsNone(evaluate_fcf(None, 1e9, 10.0).fair_value_low)
        self.assertIsNone(evaluate_fcf(1e10, 1e9, 10.0, discount=0.02, terminal_growth=0.03).fair_value_low)

    def test_component_note_is_exposed_without_changing_value(self):
        plain = evaluate_fcf(1e10, 1e9, 10.0, capex=1e9)
        noted = evaluate_fcf(
            1e10, 1e9, 10.0, capex=1e9, component_note="港股原文拆分：OCF/资本开支来自财报"
        )
        self.assertEqual(noted.fair_value_low, plain.fair_value_low)
        self.assertIn("港股原文拆分", noted.assumptions["component_note"])

    def test_dcf_and_fcf_keep_distinct_bases(self):
        """同参数下 DCF(OCF) 与 FCF(OCF−capex±利息) 必须给出不同合理价。"""
        ocf, capex, shares, price = 2.97e10, 1.97e9, 3.88e9, 72.55
        params = dict(growth=0.03, discount=0.10, terminal_growth=0.02)
        dcf = evaluate_dcf(ocf, shares, price, **params)
        fcf = evaluate_fcf(ocf, shares, price, capex=capex, **params)
        self.assertGreater(dcf.fair_value_low, fcf.fair_value_low)
        self.assertAlmostEqual(dcf.inputs["fcf"], ocf)
        self.assertAlmostEqual(fcf.inputs["fcf"], ocf - capex)


class TestFcfSourcing(unittest.TestCase):
    """验证 FCF 分项从落库字段取数、以及披露为 0 与整列缺失的区分。"""

    @staticmethod
    def _cf(code, value, date="2025-12-31"):
        return StatementItem(date, ReportType.FY, StatementType.CASHFLOW, code, code, value)

    def test_capex_and_interest_are_read_from_landed_fields(self):
        items = [
            self._cf("NETCASH_OPERATE", 100.0),
            self._cf("CONSTRUCT_LONG_ASSET", 20.0),
            self._cf("RECEIVE_INTEREST_COMMISSION", 3.0),
            self._cf("PAY_INTEREST_COMMISSION", 1.0),
        ]
        comps = _fcf_components(items)
        self.assertAlmostEqual(comps["capex"], 20.0)
        self.assertAlmostEqual(comps["fcf"], 82.0)  # 100 - 20 + 3 - 1
        self.assertNotIn("库内缺少", comps["note"])

    def test_zero_disclosed_is_not_reported_as_missing(self):
        items = [
            self._cf("NETCASH_OPERATE", 100.0),
            self._cf("CONSTRUCT_LONG_ASSET", 0.0),
            self._cf("RECEIVE_INTEREST_COMMISSION", None),
            self._cf("PAY_INTEREST_COMMISSION", None),
        ]
        comps = _fcf_components(items)
        self.assertNotIn("库内缺少", comps["note"])
        self.assertIn("披露为 0 / 未单列", comps["note"])
        self.assertAlmostEqual(comps["fcf"], 100.0)

    def test_entirely_missing_column_is_flagged(self):
        comps = _fcf_components([self._cf("NETCASH_OPERATE", 100.0)])
        self.assertIn("库内整列未采集", comps["note"])
        self.assertIn("资本开支", comps["note"])

    def test_cashflow_wc_uses_supplementary_disclosure(self):
        items = [
            self._cf("OPERATE_PAYABLE_ADD", 206.0),
            self._cf("OPERATE_RECE_REDUCE", 18.0),
            self._cf("INVENTORY_REDUCE", -18.0),
        ]
        wc = _cashflow_wc_changes(items)
        self.assertAlmostEqual(wc["payable_increase"], 206.0)
        self.assertAlmostEqual(wc["receivable_decrease"], 18.0)
        self.assertAlmostEqual(wc["inventory_decrease"], -18.0)

    def test_working_capital_uses_two_most_recent_adjacent_years(self):
        def bal(date, code, value):
            return StatementItem(date, ReportType.FY, StatementType.BALANCE, code, code, value)
        items = [
            bal("2023-12-31", "INVENTORY", 100.0), bal("2024-12-31", "INVENTORY", 150.0),
            bal("2025-12-31", "INVENTORY", 200.0),
        ]
        values, note = _working_capital_changes(items)
        self.assertAlmostEqual(values["inventory"], 50.0)  # 200 - 150，不是 200 - 100
        self.assertIn("2024-12-31", note)
        self.assertIn("2025-12-31", note)
        self.assertIn("上一期", note)

    def test_contract_liability_change_is_included_as_hint(self):
        def bal(date, code, value):
            return StatementItem(date, ReportType.FY, StatementType.BALANCE, code, code, value)
        items = [
            bal("2024-12-31", "CONTRACT_LIAB", 100e8),
            bal("2025-12-31", "CONTRACT_LIAB", 130e8),
        ]
        values, _ = _working_capital_changes(items)
        self.assertAlmostEqual(values["contract_liability"], 30e8)
        note = _cashflow_quality_note(200.0, 100.0, values, "", None)
        self.assertIn("合同负债（经销商预付款）增加 +30.00亿", note)
        self.assertIn("经销商预付款", note)
        self.assertNotIn("-30.00亿", note)

    def test_prepayment_change_is_hinted_with_direction(self):
        def bal(date, code, value):
            return StatementItem(date, ReportType.FY, StatementType.BALANCE, code, code, value)
        items = [
            bal("2024-12-31", "PREPAYMENT", 2e8),
            bal("2025-12-31", "PREPAYMENT", 5e8),
        ]
        values, _ = _working_capital_changes(items)
        self.assertAlmostEqual(values["prepaid"], 3e8)
        note = _cashflow_quality_note(200e8, 100e8, values, "", None)
        self.assertIn("预付款项增加 +3.00亿", note)
        self.assertIn("压低当期经营现金流", note)


if __name__ == "__main__":
    unittest.main()
