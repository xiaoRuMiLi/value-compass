"""财报原文结构化事实卡的回归测试。

重点锁住四个容易出错的地方：勾选符号判定、港股核数师报告条件句不误报非标、
按文本体积选最完整年报（绕过 documents 表登记不全）、抽不到时结构仍完整。
测试自包含，不依赖线上数据库或已归档 PDF。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fundamental.services.narrative import _report_facts as rf


class TestApplicable(unittest.TestCase):
    """年报里「□适用 ☑不适用」与「☑适用 □不适用」的判定不能反。"""

    def test_checked_not_applicable_means_section_does_not_apply(self):
        section = "□适用  ☑不适用  会计政策、会计估计变更或重大会计差错更正的说明"
        self.assertIs(rf._applicable(section), False)

    def test_checked_applicable_means_section_applies(self):
        section = "☑适用  □不适用  会计政策、会计估计变更或重大会计差错更正的说明"
        self.assertIs(rf._applicable(section), True)

    def test_no_checkbox_returns_unknown(self):
        self.assertIsNone(rf._applicable("本节无相关勾选信息"))


class TestHkAuditOpinion(unittest.TestCase):
    """核数师报告的「假若…則應當發表非無保留意見」是条件句，不是非标结论。"""

    def test_conditional_sentence_is_unknown_not_qualified(self):
        text = "假若披露不足，則我們應當發表非無保留意見，故需額外披露。"
        audit = rf._extract_hk(text, "2025", "2025 年报")["audit"][0]
        self.assertIsNone(audit["non_standard"])
        self.assertEqual(audit["opinion"], "unknown")
        self.assertEqual(audit["quote"], "")

    def test_unqualified_conclusion_sentence_is_recognised(self):
        text = "我們認為綜合財務報表已真實而公平地反映本集團的財務狀況。"
        audit = rf._extract_hk(text, "2025", "2025 年报")["audit"][0]
        self.assertIs(audit["non_standard"], False)
        self.assertEqual(audit["opinion"], "unqualified")

    def test_hk_facts_extract_customer_and_buyback(self):
        text = (
            "五大客戶佔本集團總收入約 6.5 %，五大供應商佔本集團總採購額約 18.8 %。"
            "年內進行人民幣 196 億元股份回購。"
        )
        out = rf._extract_hk(text, "2025", "2025 年报")
        self.assertEqual(out["top5_customer_pct"][0]["value"], 6.5)
        self.assertEqual(out["top5_supplier_pct"][0]["value"], 18.8)
        self.assertEqual(out["share_buyback"][0]["value"], 196.0)


class TestAShareExtraction(unittest.TestCase):
    def test_key_tables_are_extracted_with_numbers(self):
        text = (
            "主要客户情况\n"
            "前五名客户合计销售金额占年度销售总额比例 75.98 %\n"
            "前五名客户销售额中关联方销售额占年度销售总额比例 8.10 %\n"
            "前五名供应商合计采购金额占年度采购总额比例 51.50 %\n"
            "八、非经常性损益项目及金额\n"
            "合计 8,720.11\n"
            "资本化研发支出占研发投入 的比例 8.65 % 6.64 % 3.62 %\n"
            "资本化研发支出占当期净利 润的比重 3.62 %\n"
        )
        out = rf._extract_a(text, "2025", "2025 年报")
        self.assertEqual(out["top5_customer_pct"][0]["value"], 75.98)
        self.assertEqual(out["related_party_sales_pct"][0]["value"], 8.10)
        self.assertEqual(out["top5_supplier_pct"][0]["value"], 51.50)
        self.assertEqual(out["nonrecurring_total"][0]["value"], 8720.11)
        self.assertEqual(len(out["rd_capitalization_pct"]), 3)
        self.assertEqual(out["rd_capitalization_pct"][0]["period"], "2025")
        self.assertEqual(out["rd_capitalization_pct"][2]["value"], 3.62)

    def test_missing_sections_are_simply_absent_not_guessed(self):
        out = rf._extract_a("本公司报告期内经营正常。", "2025", "2025 年报")
        self.assertEqual(out, {})


class TestDocumentSelection(unittest.TestCase):
    def test_prefers_full_report_over_results_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "300308" / "2025"
            base.mkdir(parents=True)
            shell = base / "annual_report_2025_results.txt"
            shell.write_text("壳" * 100, encoding="utf-8")
            full = base / "annual_report_2025_report.txt"
            full.write_text("完整年报正文" * 5000, encoding="utf-8")
            with patch.object(rf.paths, "documents_dir", return_value=Path(tmp)):
                picked = rf._disk_annual_text("300308", "2025")
            self.assertEqual(picked, full)

    def test_no_annual_text_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rf.paths, "documents_dir", return_value=Path(tmp)):
                self.assertIsNone(rf._disk_annual_text("300308", "2025"))


class TestExtractReportFactsContract(unittest.TestCase):
    def test_empty_archive_returns_complete_structure_with_missing_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rf, "_load_documents", return_value=[]), \
                 patch.object(rf.paths, "documents_dir", return_value=Path(tmp)):
                out = rf.extract_report_facts("300308.SZ")
        self.assertEqual(out["source_docs"], [])
        self.assertEqual(len(out["missing_facts"]), 8)
        for key in ("top5_customer_pct", "related_party_sales_pct", "audit",
                    "share_buyback", "rd_capitalization_pct", "consolidation_change"):
            self.assertIn(key, out)

    def test_disk_full_annual_overrides_db_shell_record(self):
        """documents 表只登记业绩公告壳时，事实卡仍要读到磁盘上的完整年报。"""
        with tempfile.TemporaryDirectory() as tmp:
            code = rf.parse_code("300308.SZ")[0]
            base = Path(tmp) / code / "2025"
            base.mkdir(parents=True)
            shell = base / "annual_report_2025_results.txt"
            shell.write_text("壳" * 50, encoding="utf-8")
            full = base / "annual_report_2025_report.txt"
            full.write_text(
                "前五名客户合计销售金额占年度销售总额比例 75.98 %\n" * 100,
                encoding="utf-8",
            )
            docs = [{
                "doc_type": "annual_report", "period": "2025",
                "title": "2025 年度报告", "local_path": str(shell),
            }]
            with patch.object(rf, "_load_documents", return_value=docs), \
                 patch.object(rf.paths, "documents_dir", return_value=Path(tmp)):
                out = rf.extract_report_facts("300308.SZ")
            self.assertEqual(len(out["source_docs"]), 1)
            self.assertEqual(out["source_docs"][0]["local_path"], str(full))
            self.assertEqual(out["top5_customer_pct"][0]["value"], 75.98)

    def test_max_reports_limits_annual_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = rf.parse_code("300308.SZ")[0]
            for year in ("2023", "2024", "2025", "2026"):
                base = Path(tmp) / code / year
                base.mkdir(parents=True)
                (base / f"annual_report_{year}_report.txt").write_text(
                    "年报正文" * 100, encoding="utf-8"
                )
            with patch.object(rf, "_load_documents", return_value=[]), \
                 patch.object(rf.paths, "documents_dir", return_value=Path(tmp)):
                out = rf.extract_report_facts("300308.SZ", max_reports=2)
            self.assertEqual([d["period"] for d in out["source_docs"]],
                             ["2026", "2025"])


if __name__ == "__main__":
    unittest.main()
