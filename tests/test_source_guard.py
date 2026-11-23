"""财报原文前置守卫测试：覆盖度统计、自动补档与失败降级。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fundamental.models import Market
from fundamental.services.narrative import _source_guard as guard
from fundamental.storage import db, repository


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = db.init_db(self.db_path)
        stock = SimpleNamespace(
            code="000001", market=Market.SH, name="测试", industry=None,
            sector=None, list_date=None, is_st=False,
        )
        self.stock_id = repository.get_or_create_stock(self.conn, stock)
        self.docs_root = Path(self._tmp.name) / "docs"

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    # 真实财报正文抽出后至少数千字，测试文本需达到可用性阈值
    _BODY_TEXT = "管理层讨论与分析：报告期内公司营业收入与净利润变动情况。" * 100

    def _add_doc(
        self,
        doc_type: str,
        period: str,
        *,
        text: str | None = _BODY_TEXT,
        title: str | None = None,
    ) -> None:
        pdf = self.docs_root / period / f"{doc_type}_{period}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        if text is not None:
            pdf.with_suffix(".txt").write_text(text, encoding="utf-8")
        repository.insert_many(
            self.conn,
            "documents",
            ["stock_id", "doc_type", "period", "title", "source_url", "local_path", "file_size", "sha256", "status"],
            [
                (
                    self.stock_id, doc_type, period, title or (doc_type + period),
                    "", str(pdf), 14, "hash", "downloaded",
                )
            ],
        )
        self.conn.commit()

    def test_status_counts_only_nonempty_extracted_text(self):
        self._add_doc("annual_report", "2023")
        self._add_doc("annual_report", "2024")
        self._add_doc("q1_report", "2025", text="")          # 空文本不算
        self._add_doc("semi_report", "2026", text=None)      # 只有 PDF 不算
        status = guard.source_document_status("000001.SH", self.db_path)
        self.assertEqual(status["documents"], 2)
        self.assertEqual(status["years"], ["2023", "2024"])
        self.assertEqual(status["annual"], 2)
        self.assertEqual(status["quarterly"], 0)

    def test_ensure_skips_when_coverage_is_enough(self):
        self._add_doc("annual_report", "2023")
        self._add_doc("annual_report", "2024")
        self._add_doc("q1_report", "2025")
        with mock.patch("fundamental.services.archive.archive_documents") as download:
            result = guard.ensure_source_documents("000001.SH", db_path=self.db_path)
        download.assert_not_called()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["before"]["documents"], 3)

    def test_ensure_downloads_multiple_periods_then_analyzes(self):
        self._add_doc("annual_report", "2023")

        def fake_download(symbol, *, db_path=None, per_type=6):
            self._add_doc("annual_report", "2022")
            self._add_doc("semi_report", "2024")
            return {"annual_report": 1, "semi_report": 1}

        with mock.patch(
            "fundamental.services.archive.archive_documents", side_effect=fake_download
        ) as download:
            result = guard.ensure_source_documents(
                "000001.SH", db_path=self.db_path, per_type=6
            )
        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["per_type"], 6)
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(result["before"]["documents"], 1)
        self.assertEqual(result["after"]["documents"], 3)
        self.assertEqual(len(result["after"]["years"]), 3)

    def test_download_failure_is_reported_not_swallowed(self):
        with mock.patch(
            "fundamental.services.archive.archive_documents",
            side_effect=RuntimeError("network down"),
        ):
            result = guard.ensure_source_documents("000001.SH", db_path=self.db_path)
        self.assertEqual(result["status"], "error")
        self.assertIn("network down", result["reason"])

    def test_archive_error_payload_is_propagated(self):
        with mock.patch(
            "fundamental.services.archive.archive_documents",
            return_value={"error": "股票不存在，请先 fetch_data"},
        ):
            result = guard.ensure_source_documents("000001.SH", db_path=self.db_path)
        self.assertEqual(result["status"], "error")
        self.assertIn("股票不存在", result["reason"])

    def test_shell_title_and_short_text_are_not_usable(self):
        self._add_doc("annual_report", "2023", title="2023年年度报告")
        self._add_doc("annual_report", "2024", title="2024年度报告披露的提示性公告")
        self._add_doc("q1_report", "2025", text="仅说明报告已披露，无任何经营数据")
        status = guard.source_document_status("000001.SH", self.db_path)
        self.assertEqual(status["documents"], 1)
        self.assertEqual(status["years"], ["2023"])


class SourceShellFilterTests(unittest.TestCase):
    """披露提示公告壳在归档入口与可用性判定上都要被挡掉。"""

    def test_cninfo_matches_only_full_report_titles(self):
        from fundamental.models import DocType
        from fundamental.providers.announcement.cninfo import _match_doc_type

        self.assertFalse(_match_doc_type("2025年度报告披露的提示性公告", DocType.ANNUAL_REPORT))
        self.assertFalse(_match_doc_type("2026年半年度报告披露的提示性公告", DocType.SEMI_REPORT))
        self.assertFalse(_match_doc_type("2025年年度报告摘要", DocType.ANNUAL_REPORT))
        self.assertFalse(_match_doc_type("2025年年度报告英文版", DocType.ANNUAL_REPORT))
        self.assertTrue(_match_doc_type("2025年年度报告", DocType.ANNUAL_REPORT))
        self.assertTrue(_match_doc_type("2025年年度报告（更正后）", DocType.ANNUAL_REPORT))
        self.assertTrue(_match_doc_type("2026年第一季度报告", DocType.Q1_REPORT))

    def test_pdf_quality_helpers_reject_shells_and_short_text(self):
        from fundamental.utils.pdf import is_source_text_usable, is_source_title_usable

        body = "营业收入与净利润变动说明，产品结构升级带动毛利率提升。" * 200
        self.assertTrue(is_source_text_usable(body, "2025年年度报告"))
        self.assertFalse(is_source_text_usable(body, "2025年度报告披露的提示性公告"))
        self.assertFalse(is_source_text_usable("仅说明报告已披露", "2025年年度报告"))
        self.assertFalse(is_source_title_usable("2026年第三季度报告披露的提示性公告"))
        self.assertTrue(is_source_title_usable("2026年第三季度报告"))


if __name__ == "__main__":
    unittest.main()
