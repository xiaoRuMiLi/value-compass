"""财务全景纯计算的单测：单季还原、年报口径与比率推导。"""

from __future__ import annotations

import unittest

from fundamental.analysis.financial_metrics import build_dashboard


def _report(date: str, report_type: str) -> dict:
    return {
        "report_date": date,
        "report_type": report_type,
        "revenue": {"Q1": 100.0, "H1": 250.0, "Q3": 400.0, "FY": 600.0}[report_type],
        "net_profit": {"Q1": 10.0, "H1": 30.0, "Q3": 50.0, "FY": 80.0}[report_type],
        "eps": {"Q1": 1.0, "H1": 3.0, "Q3": 5.0, "FY": 8.0}[report_type],
        "bps": 20.0,
        "gross_margin": 40.0,
        "net_margin": 10.0,
        "roe": 12.0,
        "debt_ratio": 40.0,
        "current_ratio": None,
    }


REPORTS = [
    _report("2024-03-31", "Q1"),
    _report("2024-06-30", "H1"),
    _report("2024-09-30", "Q3"),
    _report("2024-12-31", "FY"),
]

BALANCE = {
    "TOTAL_CURRENT_ASSETS": 200.0,
    "INVENTORY": 50.0,
    "TOTAL_CURRENT_LIAB": 100.0,
    "MONETARYFUNDS": 80.0,
    "TOTAL_NONCURRENT_LIAB": 100.0,
    "TOTAL_ASSETS": 500.0,
    "TOTAL_PARENT_EQUITY": 280.0,
    "TOTAL_EQUITY": 300.0,
    "TOTAL_LIABILITIES": 200.0,
    "FIXED_ASSET": 200.0,
    "ACCOUNTS_RECE": 50.0,
    "SHORT_LOAN": 0.0,
    "LONG_LOAN": 0.0,
}

STATEMENTS = [
    {
        "report_date": "2024-03-31",
        "report_type": "Q1",
        "statement_type": "balance",
        "item_code": code,
        "item_name": code,
        "item_value": value,
    }
    for code, value in BALANCE.items()
] + [
    {
        "report_date": "2024-03-31",
        "report_type": "Q1",
        "statement_type": "cashflow",
        "item_code": "NETCASH_OPERATE",
        "item_name": "经营现金流",
        "item_value": 12.0,
    }
]


class FinancialMetricsTest(unittest.TestCase):
    def test_quarter_restores_single_quarter_values(self):
        result = build_dashboard(REPORTS, STATEMENTS, frequency="quarter")
        self.assertEqual(result["labels"], ["2024Q1", "2024Q2", "2024Q3", "2024Q4"])
        net_profit = result["primary"][0]
        self.assertEqual(net_profit["key"], "net_profit")
        self.assertEqual(net_profit["values"], [10.0, 20.0, 20.0, 30.0])
        revenue = next(item for item in result["primary"] if item["key"] == "revenue")
        self.assertEqual(revenue["values"], [100.0, 150.0, 150.0, 200.0])

    def test_annual_uses_full_year_only(self):
        result = build_dashboard(REPORTS, STATEMENTS, frequency="annual")
        self.assertEqual(result["labels"], ["2024年报"])
        net_profit = result["primary"][0]
        self.assertEqual(net_profit["values"], [80.0])

    def test_drops_periods_without_any_primary_value(self):
        reports = REPORTS + [_report("2025-03-31", "Q1")]
        result = build_dashboard(reports, STATEMENTS, frequency="annual")
        self.assertEqual(result["labels"], ["2024年报"])

    def test_derived_ratios(self):
        result = build_dashboard(REPORTS, STATEMENTS, frequency="quarter")
        ratios = {
            group: {item["key"]: item for item in payload["items"]}
            for group, payload in result["ratios"].items()
        }
        quick = ratios["solvency"]["quick_ratio"]
        self.assertAlmostEqual(quick["values"][0], (200 - 50) / 100)
        cash = ratios["solvency"]["cash_to_liab"]
        self.assertAlmostEqual(cash["values"][0], 0.8)
        multiplier = ratios["structure"]["equity_multiplier"]
        self.assertAlmostEqual(multiplier["values"][0], 500 / 280)
        debt = ratios["structure"]["debt_to_capital"]
        self.assertAlmostEqual(debt["values"][0], 0.0)
        ocf = ratios["quality"]["ocf"]
        self.assertEqual(ocf["scale"], 1e8)
        self.assertEqual(ocf["values"][0], 12.0)

    def test_fcf_injection_lands_on_matching_period(self):
        result = build_dashboard(
            REPORTS, STATEMENTS, frequency="quarter", fcf_by_period={"2024Q2": 42.0}
        )
        fcf = result["ratios"]["quality"]["items"][1]
        self.assertEqual(fcf["values"], [None, 42.0, None, None])
        self.assertEqual(fcf["unit"], "亿元")


if __name__ == "__main__":
    unittest.main()
