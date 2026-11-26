"""FCFF/WACC 企业价值折现与 EV→股权桥接的回归测试。"""

from __future__ import annotations

import unittest

from fundamental.analysis.valuation import evaluate_fcff
from fundamental.models import StatementType
from fundamental.providers.financial.eastmoney import (
    _HK_ITEM_CODE_MAP,
    _STATEMENT_FIELDS,
)


class TestEvaluateFcff(unittest.TestCase):
    def setUp(self) -> None:
        self.common = dict(
            ocf=1000.0,
            shares=100.0,
            current_price=10.0,
            interest_expense=50.0,
            capex=200.0,
            growth=0.05,
            discount=0.10,
            terminal_growth=0.02,
            years=10,
        )

    def test_fcff_starts_from_ocf_plus_interest_minus_capex(self) -> None:
        result = evaluate_fcff(**self.common)
        self.assertEqual(result.method, "fcff")
        self.assertAlmostEqual(result.inputs["fcff"], 850.0)
        self.assertGreater(result.inputs["enterprise_value"], 0)
        self.assertAlmostEqual(result.inputs["equity_value"], result.inputs["enterprise_value"])
        self.assertAlmostEqual(
            result.fair_value_low / result.fair_value_high, 0.8 / 1.2, places=3
        )

    def test_bridge_adds_excess_cash_and_deducts_debt(self) -> None:
        base = evaluate_fcff(**self.common)
        with_cash = evaluate_fcff(**self.common, cash=300.0, revenue=1000.0)
        # 超额现金 = 300 − 1000×2% = 280，全部加回股权价值。
        self.assertAlmostEqual(
            with_cash.inputs["equity_value"] - base.inputs["equity_value"], 280.0
        )
        self.assertAlmostEqual(with_cash.inputs["excess_cash"], 280.0)

        with_debt = evaluate_fcff(**self.common, debt=120.0)
        self.assertAlmostEqual(
            with_debt.inputs["equity_value"] - base.inputs["equity_value"], -120.0
        )

    def test_nonoperating_invest_is_discounted(self) -> None:
        result = evaluate_fcff(**self.common, nonoperating_invest=500.0, nonoperating_discount=0.8)
        self.assertAlmostEqual(result.inputs["nonoperating_assets_discounted"], 400.0)
        self.assertAlmostEqual(
            result.inputs["equity_value"] - result.inputs["enterprise_value"], 400.0
        )

    def test_tiered_discounts_fair_value_and_equity(self) -> None:
        base = evaluate_fcff(**self.common)
        # 公允价值 500 按 0.8、权益 400 按 0.8，合计加回 720；现金不打折。
        tiered = evaluate_fcff(
            **self.common,
            fair_value_assets=500.0, equity_assets=400.0,
            fair_value_discount=0.8, equity_discount=0.8,
        )
        self.assertAlmostEqual(tiered.inputs["fair_value_discounted"], 400.0)
        self.assertAlmostEqual(tiered.inputs["equity_discounted"], 320.0)
        self.assertAlmostEqual(
            tiered.inputs["equity_value"] - base.inputs["equity_value"], 720.0
        )
        # 新参数优先于旧参数。
        overridden = evaluate_fcff(
            **self.common,
            fair_value_assets=500.0, equity_assets=400.0,
            marketable_securities=9999.0, nonoperating_invest=9999.0,
            fair_value_discount=0.8, equity_discount=0.8,
        )
        self.assertAlmostEqual(overridden.inputs["equity_value"], tiered.inputs["equity_value"])

    def test_missing_bridge_uses_effective_values(self) -> None:
        """新参数有值时不得误报“按 0 桥接”（校准重算只传新键）。"""
        full = dict(self.common, cash=100.0, debt=50.0, minority_equity=10.0, revenue=1000.0)
        tiered = evaluate_fcff(
            **full, fair_value_assets=500.0, equity_assets=400.0,
            fair_value_discount=0.85, equity_discount=0.75,
        )
        self.assertNotIn("missing_bridge", tiered.assumptions)
        self.assertAlmostEqual(tiered.inputs["nonoperating_assets_discounted"], 725.0)
        legacy = evaluate_fcff(
            **full, marketable_securities=500.0, nonoperating_invest=400.0,
        )
        self.assertNotIn("missing_bridge", legacy.assumptions)

    def test_deposit_cash_is_not_discounted(self) -> None:
        """存款/理财已并入 cash，按面值全额加回，不参与折价。"""
        result = evaluate_fcff(**self.common, cash=1000.0, revenue=1000.0)
        # 超额现金 = 1000 − 1000×2% = 980，全额加回。
        self.assertAlmostEqual(result.inputs["excess_cash"], 980.0)
        self.assertAlmostEqual(
            result.inputs["equity_value"] - result.inputs["enterprise_value"], 980.0
        )
        self.assertIn("不打折", result.assumptions["excess_cash_rule"])

    def test_custom_discount_rates_take_effect(self) -> None:
        """折价可由配置注入，不写死在 analysis。"""
        full = evaluate_fcff(
            **self.common, fair_value_assets=500.0, equity_assets=500.0,
            fair_value_discount=1.0, equity_discount=1.0,
        )
        half = evaluate_fcff(
            **self.common, fair_value_assets=500.0, equity_assets=500.0,
            fair_value_discount=0.5, equity_discount=0.5,
        )
        self.assertAlmostEqual(
            full.inputs["equity_value"] - half.inputs["equity_value"], 500.0
        )

    def test_minority_equity_deducted_and_missing_note(self) -> None:
        result = evaluate_fcff(**self.common, minority_equity=90.0)
        self.assertAlmostEqual(
            result.inputs["equity_value"] - result.inputs["enterprise_value"], -90.0
        )
        self.assertIn("货币资金", result.assumptions["missing_bridge"])
        self.assertIn("按 0 桥接", result.assumptions["missing_bridge"])

    def test_invalid_inputs_return_empty(self) -> None:
        self.assertIsNone(evaluate_fcff(None, 100.0, 10.0).fair_value_low)
        self.assertIsNone(
            evaluate_fcff(**dict(self.common, discount=0.01, terminal_growth=0.02)).fair_value_low
        )
        # 负股权价值不应给出误导性的「合理价」。
        self.assertIsNone(evaluate_fcff(**dict(self.common, debt=1e9)).fair_value_low)


class TestFcffFieldMapping(unittest.TestCase):
    def test_a_share_bridge_fields_mapped(self) -> None:
        balance = _STATEMENT_FIELDS[StatementType.BALANCE]
        income = _STATEMENT_FIELDS[StatementType.INCOME]
        cashflow = _STATEMENT_FIELDS[StatementType.CASHFLOW]
        for code in ("MONETARYFUNDS", "TRADE_FINASSET", "LONG_EQUITY_INVEST",
                     "SHORT_LOAN", "NONCURRENT_LIAB_1YEAR", "BOND_PAYABLE",
                     "LEASE_LIAB", "MINORITY_EQUITY"):
            self.assertIn(code, balance)
        for code in ("INCOME_TAX", "FE_INTEREST_EXPENSE", "FE_INTEREST_INCOME"):
            self.assertIn(code, income)
        for code in ("FA_IR_DEPR", "IA_AMORTIZE", "USERIGHT_ASSET_AMORTIZE"):
            self.assertIn(code, cashflow)

    def test_hk_bridge_codes_normalized(self) -> None:
        balance = _HK_ITEM_CODE_MAP[StatementType.BALANCE]
        income = _HK_ITEM_CODE_MAP[StatementType.INCOME]
        self.assertEqual(balance["004002010"], "MONETARYFUNDS")
        self.assertEqual(balance["004002009"], "RESTRICTED_CASH")
        self.assertEqual(balance["004001031"], "OTHER_NONCURRENT_FINASSET")
        self.assertEqual(balance["004002022"], "OTHER_CURRENT_FINASSET")
        self.assertEqual(balance["004020001"], "LONG_LOAN")
        self.assertEqual(balance["004020005"], "LEASE_LIAB")
        self.assertEqual(income["004011200"], "INTEREST_INCOME")
        self.assertEqual(income["004011201"], "INTEREST_EXPENSE")
        self.assertEqual(income["004012001"], "INCOME_TAX")


if __name__ == "__main__":
    unittest.main()
