"""财务 provider 单元测试：A 股主表与港股主要指标映射。"""

from __future__ import annotations

import unittest
from unittest import mock

from fundamental.models import ReportType, StatementType
from fundamental.providers.financial.eastmoney import (
    EastmoneyFinancialProvider,
    _hk_report_type,
)
from fundamental.providers.quote.eastmoney import EastmoneyQuoteProvider


def _hk_row(date_type: str, date: str) -> dict:
    return {
        "STD_REPORT_DATE": f"{date} 00:00:00",
        "DATE_TYPE_CODE": date_type,
        "OPERATE_INCOME": 751766000000,
        "HOLDER_PROFIT": 224842000000,
        "GROSS_PROFIT_RATIO": 56.21,
        "NET_PROFIT_RATIO": 30.57,
        "ROE_AVG": 21.13,
        "ROIC_YEARLY": 15.25,
        "DEBT_ASSET_RATIO": 39.13,
        "CURRENT_RATIO": 1.44,
        "NETCASH_OPERATE": 303052000000,
        "BASIC_EPS": 24.75,
        "BPS": 126.93,
        "OPERATE_INCOME_YOY": 13.86,
        "HOLDER_PROFIT_YOY": 15.85,
    }


class TestHkReportType(unittest.TestCase):
    def test_maps_hk_date_type_codes(self):
        cases = {
            "001": ReportType.FY,
            "003": ReportType.Q1,
            "002": ReportType.H1,
            "004": ReportType.Q3,
        }
        for code, expected in cases.items():
            self.assertEqual(_hk_report_type({"DATE_TYPE_CODE": code, "STD_REPORT_DATE": ""}), expected)

    def test_falls_back_to_date_when_code_missing(self):
        self.assertEqual(
            _hk_report_type({"STD_REPORT_DATE": "2025-06-30 00:00:00"}), ReportType.H1
        )


class TestHkFinancials(unittest.TestCase):
    def test_hk_symbol_uses_hk_endpoint_and_maps_fields(self):
        provider = EastmoneyFinancialProvider()
        rows = [_hk_row("001", "2025-12-31"), _hk_row("004", "2026-09-30")]
        with mock.patch.object(
            provider.client,
            "get_json",
            return_value={"result": {"data": rows}},
        ) as fake:
            reports = provider.fetch_financials("00700.HK")

        self.assertEqual(fake.call_count, 1)
        self.assertEqual(
            fake.call_args.kwargs["params"]["reportName"], "RPT_HKF10_FN_MAININDICATOR"
        )
        self.assertIn('SECUCODE="00700.HK"', fake.call_args.kwargs["params"]["filter"])
        self.assertEqual(len(reports), 2)
        annual = reports[0]
        self.assertEqual(annual.report_date, "2025-12-31")
        self.assertEqual(annual.report_type, ReportType.FY)
        self.assertEqual(annual.revenue, 751766000000)
        self.assertEqual(annual.net_profit, 224842000000)
        self.assertEqual(annual.roe, 21.13)
        self.assertEqual(annual.gross_margin, 56.21)
        self.assertEqual(annual.debt_ratio, 39.13)
        self.assertEqual(annual.ocf, 303052000000)
        self.assertEqual(annual.revenue_yoy, 13.86)
        self.assertEqual(reports[1].report_type, ReportType.Q3)

    def test_a_share_symbol_keeps_original_endpoint(self):
        provider = EastmoneyFinancialProvider()
        with mock.patch.object(
            provider.client,
            "get_json",
            return_value={"result": {"data": []}},
        ) as fake:
            provider.fetch_financials("600519.SH")

        self.assertEqual(
            fake.call_args.kwargs["params"]["reportName"], "RPT_F10_FINANCE_MAINFINADATA"
        )
        self.assertIn('SECUCODE="600519.SH"', fake.call_args.kwargs["params"]["filter"])


class TestHkValuations(unittest.TestCase):
    def test_hk_symbol_uses_mainfin_for_pe_pb(self):
        provider = EastmoneyQuoteProvider()
        rows = [
            {
                "STD_REPORT_DATE": "2026-09-30 00:00:00",
                "PE_TTM": 11.58,
                "PB_TTM": 2.24,
                "TOTAL_MARKET_CAP": 3.1e12,
            }
        ]
        with mock.patch.object(
            provider.client, "get_json", return_value={"result": {"data": rows}}
        ) as fake:
            points = provider.fetch_valuations("00700.HK")

        self.assertEqual(fake.call_args.kwargs["params"]["reportName"], "RPT_HKF10_FN_MAININDICATOR")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].trade_date, "2026-09-30")
        self.assertEqual(points[0].pe_ttm, 11.58)
        self.assertEqual(points[0].pb_mrq, 2.24)

    def test_a_share_valuation_endpoint_unchanged(self):
        provider = EastmoneyQuoteProvider()
        with mock.patch.object(
            provider.client, "get_json", return_value={"result": {"data": []}}
        ) as fake:
            provider.fetch_valuations("600519.SH")
        self.assertEqual(fake.call_args.kwargs["params"]["reportName"], "RPT_VALUEANALYSIS_DET")


class TestHkStatementItems(unittest.TestCase):
    """港股逐科目报表：数字编码归一化为项目语义码，现金流量表由主指标构造。"""

    def test_hk_income_balance_codes_are_normalized(self):
        provider = EastmoneyFinancialProvider()
        rows = [
            {
                "STD_REPORT_DATE": "2026-09-30 00:00:00",
                "DATE_TYPE_CODE": "004",
                "STD_ITEM_CODE": "004001999",
                "ITEM_NAME": "营运收入",
                "AMOUNT": 608579000000,
            },
            {
                "STD_REPORT_DATE": "2026-09-30 00:00:00",
                "DATE_TYPE_CODE": "004",
                "STD_ITEM_CODE": "004099999",  # 未映射科目保留原码
                "ITEM_NAME": "非运算项目",
                "AMOUNT": 1,
            },
        ]
        with mock.patch.object(
            provider.client, "get_json", return_value={"result": {"data": rows}}
        ):
            items = provider.fetch_statement_items("00700.HK", StatementType.INCOME)
        codes = {item.item_code for item in items}
        self.assertIn("OPERATE_INCOME", codes)
        self.assertIn("004099999", codes)
        self.assertEqual(len(items), 2)

    def test_hk_balance_uses_balance_specific_mapping(self):
        provider = EastmoneyFinancialProvider()
        # 同一个数字码在利润表是「营运收入」，在资产负债表是「非流动资产合计」。
        rows = [
            {
                "STD_REPORT_DATE": "2026-09-30 00:00:00",
                "DATE_TYPE_CODE": "004",
                "STD_ITEM_CODE": "004001999",
                "ITEM_NAME": "非流动资产合计",
                "AMOUNT": 900000000000,
            }
        ]
        with mock.patch.object(
            provider.client, "get_json", return_value={"result": {"data": rows}}
        ):
            items = provider.fetch_statement_items("00700.HK", StatementType.BALANCE)
        self.assertEqual(items[0].item_code, "TOTAL_NONCURRENT_ASSETS")

    def test_hk_cashflow_built_from_main_indicator(self):
        provider = EastmoneyFinancialProvider()
        rows = [
            {
                "STD_REPORT_DATE": "2026-09-30 00:00:00",
                "DATE_TYPE_CODE": "004",
                "NETCASH_OPERATE": 156713000000,
                "NETCASH_INVEST": -122357000000,
                "NETCASH_FINANCE": 60910000000,
                "END_CASH": 231717000000,  # 时点余额，不应被当成期间流量
            }
        ]
        with mock.patch.object(
            provider.client, "get_json", return_value={"result": {"data": rows}}
        ) as fake:
            items = provider.fetch_statement_items("00700.HK", StatementType.CASHFLOW)
        self.assertEqual(
            fake.call_args.kwargs["params"]["reportName"], "RPT_HKF10_FN_MAININDICATOR"
        )
        self.assertEqual({item.item_code for item in items}, {
            "NETCASH_OPERATE", "NETCASH_INVEST", "NETCASH_FINANCE"
        })
        self.assertTrue(all(item.statement_type == StatementType.CASHFLOW for item in items))


if __name__ == "__main__":
    unittest.main()
