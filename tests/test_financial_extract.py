"""财报原文抽取：单位/币种换算、引文核对、按报告期回退的回归测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fundamental.models import FinancialReport, ReportType, StatementItem, StatementType
from fundamental.services.analyze import _fcff_inputs, _fcf_components
from fundamental.services import financial_extract as fe
from fundamental.storage import db, repository
from fundamental.models import Market, Stock


SOURCE_TEXT = "於二零二五年十二月三十一日，本集團持有按公允價值計量的金融資產合計人民幣6,140百萬元。"


class TestAmountNormalization(unittest.TestCase):
    def test_parse_number_handles_comma_and_space(self) -> None:
        self.assertEqual(fe.parse_number("6,140"), 6140.0)
        self.assertEqual(fe.parse_number("1 024.5"), 1024.5)
        self.assertIsNone(fe.parse_number("-"))

    def test_unit_scale_covers_traditional_and_modern(self) -> None:
        self.assertEqual(fe._unit_scale("百萬元")[0], 1e6)
        self.assertEqual(fe._unit_scale("億元")[0], 1e8)
        self.assertEqual(fe._unit_scale("十億元")[0], 1e9)
        self.assertEqual(fe._unit_scale("千元")[0], 1e3)
        self.assertEqual(fe._unit_scale("元")[0], 1.0)

    def test_cny_amount_to_yuan(self) -> None:
        amount = fe.normalize_amount("6,140", "百萬元", "CNY")
        self.assertAlmostEqual(amount["value_cny"], 6.14e9)
        self.assertEqual(amount["fx_rate"], 1.0)
        self.assertFalse(amount["fx_fallback"])

    def test_hkd_amount_uses_fx_rate_and_keeps_original(self) -> None:
        with patch.object(fe, "hkd_to_cny", return_value={
            "rate": 0.9, "source": "mock", "date": "2026-01-01", "is_fallback": False,
        }):
            amount = fe.normalize_amount("100", "億港元", "HKD")
        self.assertAlmostEqual(amount["value_cny"], 100e8 * 0.9)
        self.assertEqual(amount["currency"], "HKD")
        self.assertEqual(amount["original_value"], 100.0)

    def test_usd_amount_uses_configured_rate_with_fallback_flag(self) -> None:
        amount = fe.normalize_amount("10", "亿美元", "USD")
        self.assertAlmostEqual(amount["value_cny"], 10e8 * 7.2)
        self.assertTrue(amount["fx_fallback"])

    def test_unsupported_currency_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fe.normalize_amount("10", "亿元", "JPY")

    def test_quote_must_be_verbatim_after_whitespace_normalization(self) -> None:
        quote = "按公允價值計量的金融資產合計人民幣6,140百萬元"
        self.assertTrue(fe.quote_in_text(quote, SOURCE_TEXT))
        self.assertFalse(fe.quote_in_text("金融資產合計人民幣9,999百萬元", SOURCE_TEXT))

    def test_period_maps_to_real_report_date(self) -> None:
        self.assertEqual(fe.period_to_report_date("2025", "annual_report"), ("2025-12-31", ReportType.FY))
        self.assertEqual(fe.period_to_report_date("2025", "semi_report"), ("2025-06-30", ReportType.H1))


class TestNormalizeItems(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = [{
            "report_date": "2025-12-31", "report_type": ReportType.FY,
            "doc_type": "annual_report", "title": "2025 年报", "path": "/tmp/x.txt",
            "text": SOURCE_TEXT,
        }]

    def test_valid_item_is_normalized(self) -> None:
        raw = {"items": [{
            "field_code": "FAIR_VALUE_FIN_ASSETS", "value": "6,140", "unit": "百萬元",
            "currency": "CNY", "report_date": "2025-12-31",
            "quote": "按公允價值計量的金融資產合計人民幣6,140百萬元",
        }]}
        items, warnings = fe._normalize_items(raw, self.sources)
        self.assertEqual(len(items), 1)
        self.assertEqual(warnings, [])
        self.assertAlmostEqual(items[0]["value_cny"], 6.14e9)
        self.assertEqual(items[0]["bridge_key"], "fair_value")

    def test_unknown_field_and_bad_quote_are_dropped(self) -> None:
        raw = {"items": [
            {"field_code": "NOT_A_FIELD", "value": "1", "unit": "元", "currency": "CNY",
             "report_date": "2025-12-31", "quote": "按公允價值計量的金融資產合計人民幣6,140百萬元"},
            {"field_code": "FAIR_VALUE_FIN_ASSETS", "value": "9999", "unit": "百萬元",
             "currency": "CNY", "report_date": "2025-12-31",
             "quote": "金融資產合計人民幣9,999百萬元"},
        ]}
        items, warnings = fe._normalize_items(raw, self.sources)
        self.assertEqual(items, [])
        self.assertEqual(len(warnings), 2)

    def test_period_mismatch_is_dropped(self) -> None:
        raw = {"items": [{
            "field_code": "FAIR_VALUE_FIN_ASSETS", "value": "6,140", "unit": "百萬元",
            "currency": "CNY", "report_date": "2024-12-31",
            "quote": "按公允價值計量的金融資產合計人民幣6,140百萬元",
        }]}
        items, warnings = fe._normalize_items(raw, self.sources)
        self.assertEqual(items, [])
        self.assertIn("不在归档文档内", warnings[0])

    def test_cashflow_fields_take_current_period_column(self) -> None:
        sources = [{"report_date": "2025-12-31", "report_type": ReportType.FY,
                    "doc_type": "annual_report", "title": "2025 年报", "path": "/tmp/x.txt",
                    "text": "利息支出 28,573,949.37 40,436,892.68 元（本期/上期）"}]
        current = {"items": [{
            "field_code": "INTEREST_EXPENSE_DETAIL", "value": "28573949.37", "unit": "元",
            "currency": "CNY", "report_date": "2025-12-31",
            "quote": "利息支出 28,573,949.37 40,436,892.68 元",
        }]}
        items, warnings = fe._normalize_items(current, sources)
        self.assertEqual(len(items), 1)
        self.assertEqual(warnings, [])

        wrong = {"items": [{
            "field_code": "INTEREST_EXPENSE_DETAIL", "value": "40436892.68", "unit": "元",
            "currency": "CNY", "report_date": "2025-12-31",
            "quote": "利息支出 28,573,949.37 40,436,892.68 元",
        }]}
        items, warnings = fe._normalize_items(wrong, sources)
        self.assertEqual(items, [])
        self.assertTrue(any("本期列" in w for w in warnings))

    def test_fcf_fields_are_not_bridge_assets(self) -> None:
        for field in fe.FIELDS:
            if field.code in fe.FCF_CASH_FIELDS:
                self.assertTrue(field.bridge_key.startswith("fcf_"))
        self.assertIsNone(fe.bridge_value_from_extracted(
            {c: {"value_cny": 1e8} for c in fe.FCF_CASH_FIELDS}, "fair_value"
        ))


class TestCashflowFieldFallback(unittest.TestCase):
    """资本开支/利息支出回退原文；利息收入只披露、不重复加回。"""

    def setUp(self) -> None:
        self.items = [
            StatementItem("2025-12-31", ReportType.FY, StatementType.CASHFLOW,
                          "NETCASH_OPERATE", "经营活动现金流量净额", 297.06e8),
        ]

    def test_capex_and_interest_expense_fall_back(self) -> None:
        extracted = {
            "CAPEX_CASH_PAID": {"value_cny": 19.67e8, "report_date": "2025-12-31"},
            "INTEREST_EXPENSE_DETAIL": {"value_cny": 0.2857e8, "report_date": "2025-12-31"},
        }
        comps = _fcf_components(self.items, extracted=extracted)
        self.assertAlmostEqual(comps["capex"], 19.67e8)
        self.assertAlmostEqual(comps["interest_expense"], 0.2857e8)
        self.assertAlmostEqual(comps["fcf"], 297.06e8 - 19.67e8 - 0.2857e8)
        self.assertIn("资本开支改取财报原文抽取值", comps["note"])

    def test_interest_income_disclosed_but_not_added_back(self) -> None:
        extracted = {
            "INTEREST_INCOME_DETAIL": {"value_cny": 26.93e8, "report_date": "2025-12-31"},
        }
        comps = _fcf_components(self.items, extracted=extracted)
        # A 股利息收入不进公式（按 0），原文抽取值只写入披露字段与 note。
        self.assertEqual(comps["interest_income"], 0.0)
        self.assertAlmostEqual(comps["fcf"], 297.06e8)
        self.assertAlmostEqual(comps["interest_income_disclosed"], 26.93e8)
        self.assertIn("利息收入 26.93亿已含在经营现金流内，未重复加回", comps["note"])

    def test_mismatched_period_is_not_fallen_back(self) -> None:
        extracted = {
            "CAPEX_CASH_PAID": {"value_cny": 19.67e8, "report_date": "2024-12-31"},
        }
        comps = _fcf_components(self.items, extracted=extracted)
        self.assertIsNone(comps["capex"])
        self.assertIn("不一致，未回退", comps["note"])


class TestStoredExtractionFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.init_db(self.db_path)
        self.stock_id = repository.get_or_create_stock(
            self.conn,
            Stock("00700", Market.HK, "腾讯控股", "软件服务", "科技", "2004-06-16", False),
        )
        self.conn.execute(
            "INSERT INTO extracted_financial_items "
            "(stock_id, field_code, report_date, report_type, value_cny, original_value, "
            " currency, unit, fx_rate, fx_source, quote, source_doc, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'verified')",
            (self.stock_id, "FAIR_VALUE_FIN_ASSETS", "2025-12-31", "FY",
             300e8, 300, "CNY", "亿元", 1.0, "原币", "原文引文", "2025 年报"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_latest_extracted_values_and_bridge_sum(self) -> None:
        extracted = fe.latest_extracted_values(self.conn, self.stock_id)
        self.assertIn("FAIR_VALUE_FIN_ASSETS", extracted)
        self.assertAlmostEqual(fe.bridge_value_from_extracted(extracted, "fair_value"), 300e8)
        self.assertIsNone(fe.bridge_value_from_extracted(extracted, "equity"))
        self.assertEqual(
            fe.latest_extracted_for_bridge(extracted, "fair_value")["report_date"], "2025-12-31"
        )

    def test_fcff_inputs_falls_back_only_when_standard_missing(self) -> None:
        items = [
            StatementItem("2025-12-31", ReportType.FY, StatementType.BALANCE,
                          "MONETARYFUNDS", "货币资金", 100e8),
            StatementItem("2025-12-31", ReportType.FY, StatementType.BALANCE,
                          "TRADE_FINASSET", "公允价值金融资产", 500e8),
        ]
        financials = [FinancialReport("2025-12-31", ReportType.FY, revenue=1000e8)]
        fcfc = {"ocf": 200e8, "capex": 50e8, "interest_expense": 10e8}
        extracted = fe.latest_extracted_values(self.conn, self.stock_id)

        with_standard = _fcff_inputs("00700.HK", items, financials, fcfc, extracted=extracted)
        self.assertAlmostEqual(with_standard["fair_value_assets"], 500e8)
        self.assertNotIn("公允价值类金融资产改取", with_standard["note"])

        without_standard = _fcff_inputs("00700.HK", items[:1], financials, fcfc, extracted=extracted)
        self.assertAlmostEqual(without_standard["fair_value_assets"], 300e8)
        self.assertIn("公允价值类金融资产改取财报原文抽取值", without_standard["note"])
        self.assertIn("2025-12-31", without_standard["note"])
        self.assertIn("公允价值类金融资产", without_standard["extracted_sources"])


if __name__ == "__main__":
    unittest.main()
