"""体检页财务数据聚合：核心指标、来源标注、报告期回退、原文入口脱敏。"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fundamental.services import report_facts as rf
from fundamental.storage import db


class TestReportFacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "annual_report_2025.pdf").write_bytes(b"%PDF mock")
        self.db_path = root / "test.db"
        conn = db.init_db(self.db_path)
        conn.execute("INSERT INTO stocks (code, market, name) VALUES ('000858', 'SZ', '五粮液')")
        stock_id = conn.execute("SELECT id FROM stocks WHERE code='000858'").fetchone()[0]
        conn.execute(
            "INSERT INTO financials (stock_id, report_date, report_type, revenue, net_profit, "
            "roe, gross_margin, debt_ratio, revenue_yoy) VALUES (?, '2025-12-31', 'FY', "
            "4.05e10, 8.95e9, 6.89, 77.5, 35.6, -54.5)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO financials (stock_id, report_date, report_type, revenue, net_profit, "
            "roe, gross_margin, debt_ratio, revenue_yoy) VALUES (?, '2026-09-30', 'Q3', "
            "3.28e10, 9.71e9, 8.09, 79.9, 29.6, 7.2)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO financials (stock_id, report_date, report_type, revenue, net_profit, "
            "roe, gross_margin, debt_ratio, revenue_yoy) VALUES (?, '2025-09-30', 'Q3', "
            "3.00e10, 8.80e9, 7.50, 78.0, 30.0, 5.0)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO statement_items (stock_id, report_date, report_type, statement_type, "
            "item_code, item_name, item_value) VALUES (?, '2025-12-31', 'FY', 'cashflow', "
            "'NETCASH_OPERATE', '经营活动现金流量净额', 2.97e10)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO statement_items (stock_id, report_date, report_type, statement_type, "
            "item_code, item_name, item_value) VALUES (?, '2025-09-30', 'Q3', 'cashflow', "
            "'NETCASH_OPERATE', '经营活动现金流量净额', 3.10e10)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO statement_items (stock_id, report_date, report_type, statement_type, "
            "item_code, item_name, item_value) VALUES (?, '2026-09-30', 'Q3', 'cashflow', "
            "'NETCASH_OPERATE', '经营活动现金流量净额', 2.80e10)",
            (stock_id,),
        )
        conn.execute(
            "INSERT INTO documents (stock_id, doc_type, period, title, local_path, status) "
            "VALUES (?, 'annual_report', '2025', '2025年年度报告', ?, 'downloaded')",
            (stock_id, str(root / "annual_report_2025.pdf")),
        )
        conn.execute(
            "INSERT INTO extracted_financial_items (stock_id, field_code, report_date, report_type, "
            "value_cny, original_value, currency, unit, quote, source_doc, source_path) "
            "VALUES (?, 'INTEREST_INCOME_DETAIL', '2025-12-31', 'FY', 2.69e9, 2.69e9, 'CNY', '元', "
            "'利息收入 2,693,164,763.58', '2025年年度报告', ?)",
            (stock_id, str(root / "annual_report_2025.txt")),
        )
        conn.commit()
        conn.close()
        self._env = patch.dict(os.environ, {"VALUE_COMPASS_DB": str(self.db_path)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_latest_period_and_core_metrics(self):
        conn = self._conn()
        data = rf.build_for_symbol(conn, "000858.SZ")
        conn.close()
        self.assertEqual(data["report_date"], "2026-09-30")
        self.assertEqual(data["report_type"], "Q3")
        self.assertIn("季报", data["note"])
        core = {c["key"]: c for c in data["core"]}
        self.assertAlmostEqual(core["revenue"]["value"], 3.28e10)
        self.assertAlmostEqual(core["roe"]["value"], 8.09)
        self.assertAlmostEqual(core["revenue"]["yoy"], 7.2)
        self.assertEqual(core["revenue"]["source"], "东财标准口径")

    def test_explicit_report_date(self):
        conn = self._conn()
        data = rf.build_for_symbol(conn, "000858.SZ", "2025-12-31")
        conn.close()
        self.assertEqual(data["report_date"], "2025-12-31")
        cashflow = next(s for s in data["statements"] if s["type"] == "cashflow")
        item = next(i for i in cashflow["items"] if i["code"] == "NETCASH_OPERATE")
        self.assertEqual(item["source"], "东财标准科目")
        self.assertTrue(item["key"])

    def test_comparison_columns_have_prior_full_and_delta(self):
        conn = self._conn()
        data = rf.build_for_symbol(conn, "000858.SZ")
        conn.close()
        self.assertEqual(data["comparison"]["prior_period"], "2025-09-30")
        self.assertEqual(data["comparison"]["prior_full_year"], "2025-12-31")
        self.assertIn("累计数", data["comparison"]["note"])
        core = {c["key"]: c for c in data["core"]}
        self.assertAlmostEqual(core["revenue"]["prior_value"], 3.00e10)
        self.assertAlmostEqual(core["revenue"]["prior_full_year"], 4.05e10)
        self.assertAlmostEqual(core["revenue"]["change"], 0.28e10)
        self.assertAlmostEqual(core["roe"]["change"], 0.59)
        self.assertAlmostEqual(core["roe"]["yoy"], 0.59 / 7.50 * 100)
        cashflow = next(s for s in data["statements"] if s["type"] == "cashflow")
        item = next(i for i in cashflow["items"] if i["code"] == "NETCASH_OPERATE")
        self.assertAlmostEqual(item["prior_value"], 3.10e10)
        self.assertAlmostEqual(item["prior_full_year"], 2.97e10)
        self.assertAlmostEqual(item["value"], 2.80e10)
        self.assertAlmostEqual(item["change"], -0.30e10)

    def test_missing_prior_period_leaves_blanks(self):
        conn = self._conn()
        data = rf.build_for_symbol(conn, "000858.SZ", "2025-12-31")
        conn.close()
        core = {c["key"]: c for c in data["core"]}
        self.assertIsNone(core["revenue"]["prior_value"])
        self.assertIsNone(core["revenue"]["change"])

    def test_extracted_latest_period_has_doc_id_and_no_path(self):
        conn = self._conn()
        data = rf.build_for_symbol(conn, "000858.SZ")
        conn.close()
        self.assertEqual(data["extracted"], [])  # 抽取字段属于 2025 年报，不是当期 Q3
        latest = data["latest_extracted"]
        self.assertEqual(latest["report_date"], "2025-12-31")
        item = latest["items"][0]
        self.assertEqual(item["label"], "利息收入")
        self.assertEqual(item["doc_id"], 1)
        self.assertIn("利息收入", item["quote"])
        self.assertNotIn("local_path", item)
        self.assertNotIn("source_path", item)

    def test_unknown_symbol_raises(self):
        conn = self._conn()
        with self.assertRaises(LookupError):
            rf.build_for_symbol(conn, "999999.SZ")
        conn.close()


if __name__ == "__main__":
    unittest.main()
