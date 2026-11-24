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

    def test_postponement_notice_is_rejected_for_both_report_types(self):
        """延期披露公告标题同时含“年度报告/季度报告”，两种类型都必须拒绝。"""
        from fundamental.models import DocType
        from fundamental.providers.announcement.cninfo import _match_doc_type
        from fundamental.utils.pdf import is_source_title_usable

        title = "关于延期披露2025年度报告及2026年第一季度报告的公告"
        self.assertFalse(_match_doc_type(title, DocType.ANNUAL_REPORT))
        self.assertFalse(_match_doc_type(title, DocType.Q1_REPORT))
        self.assertFalse(_match_doc_type(title, DocType.SEMI_REPORT))
        self.assertFalse(is_source_title_usable(title))

    def test_pdf_quality_helpers_reject_shells_and_short_text(self):
        from fundamental.utils.pdf import is_source_text_usable, is_source_title_usable

        body = "营业收入与净利润变动说明，产品结构升级带动毛利率提升。" * 200
        self.assertTrue(is_source_text_usable(body, "2025年年度报告"))
        self.assertFalse(is_source_text_usable(body, "2025年度报告披露的提示性公告"))
        self.assertFalse(is_source_text_usable("仅说明报告已披露", "2025年年度报告"))
        self.assertFalse(is_source_title_usable("2026年第三季度报告披露的提示性公告"))
        self.assertTrue(is_source_title_usable("2026年第三季度报告"))


class ArchiveAntiOverwriteTests(unittest.TestCase):
    """归档层：同期多公告不互相覆盖，延期公告空壳不入库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = db.init_db(self.db_path)
        stock = SimpleNamespace(
            code="000858", market=Market.SZ, name="测试", industry=None,
            sector=None, list_date=None, is_st=False,
        )
        self.stock_id = repository.get_or_create_stock(self.conn, stock)
        self.conn.commit()
        self.conn.close()
        self.docs_root = Path(self._tmp.name) / "docs"

    def tearDown(self):
        self._tmp.cleanup()

    def test_same_period_different_announcements_do_not_overwrite(self):
        """真年报与延期公告同期：各用独立文件名，延期公告因正文过短被拒。"""
        from fundamental.models import Document, DocStatus, DocType
        from fundamental.services import archive as arch

        body = "管理层讨论与分析：报告期内营业收入与净利润变动说明。" * 200

        def fake_doc(title, url):
            return Document(
                doc_type=DocType.ANNUAL_REPORT, title=title,
                source_url=url, status=DocStatus.PENDING,
            )

        docs = [
            fake_doc("2025年年度报告", "https://static.cninfo.com.cn/finalpage/2026-04-30/1225273091.PDF"),
            fake_doc("关于延期披露2025年度报告的公告", "https://static.cninfo.com.cn/finalpage/2026-03-01/1225270000.PDF"),
        ]
        provider = mock.Mock()
        provider.list_documents.side_effect = (
            lambda symbol, doc_type: docs if doc_type == "annual_report" else []
        )

        def fake_get(url):
            # 真报告返回长正文，延期公告返回短空壳
            data = body.encode("utf-8") if "1225273091" in url else b"report postponed"
            return SimpleNamespace(content=data)

        provider.client.get.side_effect = fake_get
        with mock.patch.object(arch, "CninfoAnnouncementProvider", return_value=provider), \
             mock.patch.object(arch.paths, "documents_dir", return_value=self.docs_root), \
             mock.patch.object(arch, "extract_pdf_text", side_effect=lambda p: body if "1225273091" in str(p) else "report postponed"):
            stats = arch.archive_documents("000858.SZ", db_path=self.db_path, per_type=3)

        # 只有真报告入库；延期公告被正文校验挡掉
        self.assertEqual(stats["annual_report"], 1)
        conn = db.init_db(self.db_path)
        rows = conn.fetch_all("SELECT title, local_path FROM documents WHERE stock_id = ?", (self.stock_id,)) \
            if hasattr(conn, "fetch_all") else conn.execute(
                "SELECT title, local_path FROM documents WHERE stock_id = ?", (self.stock_id,)
            ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertIn("1225273091", rows[0]["local_path"])  # 文件名带公告 ID


if __name__ == "__main__":
    unittest.main()
