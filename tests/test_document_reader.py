"""财报原文阅读服务：章节切分、分块读取、分级定位与脱敏契约。"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fundamental.services import document_reader as dr
from fundamental.storage import db

TEXT = (
    "第一节 重要提示\n"
    "本公司董事会保证本报告真实、准确、完整。\n"
    "营业收入 1,234 万元，同比增长 12%。\n"
    "第二节 管理层讨论与分析\n"
    "报告期内，公司实现净利润 5,678 万元，主要系毛利率提升。\n"
    "第三节 财务报表附注\n"
    "利息收入 26,931 万元，已计入经营活动现金流。\n"
)


class TestDocumentReader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.txt = root / "annual_report_2025.txt"
        self.txt.write_text(TEXT, encoding="utf-8")
        (root / "annual_report_2025.pdf").write_bytes(b"%PDF-1.4 mock")
        self.db_path = root / "test.db"
        conn = db.init_db(self.db_path)
        conn.execute(
            "INSERT INTO stocks (code, market, name) VALUES ('000858', 'SZ', '五粮液')"
        )
        stock_id = conn.execute("SELECT id FROM stocks WHERE code='000858'").fetchone()[0]
        conn.execute(
            "INSERT INTO documents (stock_id, doc_type, period, title, local_path, status) "
            "VALUES (?, 'annual_report', '2025', '2025年年度报告', ?, 'downloaded')",
            (stock_id, str(root / "annual_report_2025.pdf")),
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

    def test_metadata_is_sanitized(self):
        conn = self._conn()
        info = dr.metadata(1, conn)
        conn.close()
        self.assertNotIn("local_path", info)
        self.assertNotIn("sha256", info)
        self.assertEqual(info["symbol"], "000858.SZ")
        self.assertTrue(info["has_text"])
        self.assertTrue(info["has_pdf"])

    def test_outline_has_headings_with_offsets(self):
        sections = dr.build_outline(TEXT)
        self.assertGreaterEqual(len(sections), 3)
        self.assertEqual(sections[0]["start"], 0)
        self.assertTrue(any("管理层讨论" in s["heading"] for s in sections))
        for s in sections:
            self.assertLessEqual(s["start"], s["end"])
        self.assertEqual(sections[-1]["end"], len(TEXT))

    def test_read_chunk_clamps_bounds(self):
        chunk = dr.read_chunk(TEXT, -10, 20)
        self.assertEqual(chunk["start"], 0)
        self.assertEqual(chunk["end"], 20)
        self.assertEqual(chunk["total_chars"], len(TEXT))
        tail = dr.read_chunk(TEXT, len(TEXT) - 5, 9999)
        self.assertEqual(tail["text"], TEXT[-5:])

    def test_locate_exact_and_normalized(self):
        conn = self._conn()
        sections = dr.build_outline(TEXT)
        exact = dr.locate(TEXT, query="净利润 5,678 万元", sections=sections)
        self.assertEqual(exact["strategy"], "exact")
        normalized = dr.locate(TEXT, query="利息收入 26,931 万元", sections=sections)
        self.assertEqual(normalized["strategy"], "exact")
        # 引文里夹带换行/空格，归一化后仍能命中
        broken = dr.locate(TEXT, query="净利润 5,678\n万元", sections=sections)
        self.assertEqual(broken["strategy"], "normalized")
        self.assertIn("净利润", broken["matches"][0]["snippet"])
        conn.close()

    def test_locate_keyword_and_section_fallback(self):
        sections = dr.build_outline(TEXT)
        by_keyword = dr.locate(TEXT, query="不存在的长句子", keywords="毛利率", sections=sections)
        self.assertEqual(by_keyword["strategy"], "keyword")
        none = dr.locate(TEXT, query="xyz不存在", keywords="qqq不存在", section="财务报表附注", sections=sections)
        self.assertEqual(none["strategy"], "none")
        self.assertIsNotNone(none["section_index"])
        self.assertIn("未定位", none["note"])

    def test_locate_in_pdf_returns_page_and_rect(self):
        class _Rect:
            x0, y0, x1, y1 = 10, 20, 50, 30

        class _Page:
            def search_for(self, text):
                return [_Rect()] if "利息收入" in text else []

        class _Pdf:
            def __init__(self):
                self.pages = [_Page(), _Page()]

            def __len__(self):
                return len(self.pages)

            def __iter__(self):
                return iter(self.pages)

            def close(self):
                pass

        fake_pymupdf = SimpleNamespace(open=lambda _path: _Pdf())
        with patch.object(dr, "pdf_path", return_value=Path("mock.pdf")), \
                patch.dict("sys.modules", {"pymupdf": fake_pymupdf}):
            conn = self._conn()
            result = dr.locate_in_pdf(1, conn, query="利息收入 26,931 万元")
            conn.close()
        self.assertEqual(result["strategy"], "exact")
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(result["matches"][0]["page"], 1)
        self.assertEqual(result["matches"][0]["rect"], [10, 20, 50, 30])

    def test_locate_in_pdf_missing_file_returns_none(self):
        with patch.object(dr, "pdf_path", return_value=None):
            conn = self._conn()
            result = dr.locate_in_pdf(1, conn, query="利息收入")
            conn.close()
        self.assertEqual(result["strategy"], "none")
        self.assertEqual(result["matches"], [])
        self.assertIn("未归档", result["note"])

    def test_pdf_outline_prefers_clean_bookmarks(self):
        class _Pdf:
            def __len__(self):
                return 6

            def get_toc(self, simple=True):
                return [
                    (1, "OLE_LINK18", 2),
                    (1, "第一节 释义 .......... 5", 5),
                    (2, "一、公司信息", 6),
                    (2, "二、联系人和联系方式", 6),
                    (1, "本公司董事会保证本报告内容真实、准确、完整，不存在虚假记载。", 6),
                ]

            def __iter__(self):
                return iter([])

            def close(self):
                pass

        fake_pymupdf = SimpleNamespace(open=lambda _path: _Pdf())
        with patch.object(dr, "pdf_path", return_value=Path("mock.pdf")), \
                patch.dict("sys.modules", {"pymupdf": fake_pymupdf}):
            conn = self._conn()
            result = dr.pdf_outline(1, conn)
            conn.close()
        self.assertEqual(result["source"], "bookmark")
        self.assertEqual(result["total_pages"], 6)
        self.assertEqual(
            result["items"],
            [
                {"level": 1, "title": "第一节 释义", "page": 5},
                {"level": 2, "title": "一、公司信息", "page": 6},
                {"level": 2, "title": "二、联系人和联系方式", "page": 6},
            ],
        )

    def test_pdf_outline_falls_back_to_page_headings(self):
        class _Page:
            def __init__(self, text):
                self.text = text

            def get_text(self, mode):
                return self.text

        class _Pdf:
            def __init__(self):
                self.pages = [
                    _Page("1\n香港交易及結算所有限公司對本公佈的內容概不負責\n"),
                    _Page("2\n未經審核截至二零二五年九月三十日止三個月\n截至下列日期止九個月\n二零二五年\n"),
                    _Page("3\n經營資料\n於二零二五年九月三十日\n"),
                    _Page("4\n管理層討論及分析\n二零二五年第三季與二零二四年第三季的比較\n"),
                ]

            def __len__(self):
                return len(self.pages)

            def __iter__(self):
                return iter(self.pages)

            def get_toc(self, simple=True):
                return []

            def close(self):
                pass

        fake_pymupdf = SimpleNamespace(open=lambda _path: _Pdf())
        with patch.object(dr, "pdf_path", return_value=Path("mock.pdf")), \
                patch.dict("sys.modules", {"pymupdf": fake_pymupdf}):
            conn = self._conn()
            result = dr.pdf_outline(1, conn)
            conn.close()
        self.assertEqual(result["source"], "pages")
        titles = [item["title"] for item in result["items"]]
        self.assertIn("截至下列日期止九個月", titles)
        self.assertIn("經營資料", titles)
        self.assertIn("管理層討論及分析", titles)
        self.assertNotIn("未經審核", titles)
        self.assertEqual(result["items"][-1]["page"], 4)

    def test_render_pdf_page_uses_mupdf_and_clamps_scale(self):
        class _Rect:
            width, height = 595, 842

        class _Pixmap:
            def tobytes(self, fmt):
                return b"PNG" + fmt.encode()

        class _Page:
            rect = _Rect()

            def get_pixmap(self, matrix, alpha=False):
                self.matrix = matrix
                return _Pixmap()

        class _Pdf:
            def __init__(self):
                self.page = _Page()

            def __len__(self):
                return 2

            def __getitem__(self, index):
                return self.page

            def close(self):
                pass

        rendered = _Pdf()
        fake_pymupdf = SimpleNamespace(
            open=lambda _path: rendered,
            Matrix=lambda a, b: (a, b),
        )
        with patch.object(dr, "pdf_path", return_value=Path("mock.pdf")), \
                patch.dict("sys.modules", {"pymupdf": fake_pymupdf}):
            conn = self._conn()
            png, width, height = dr.render_pdf_page(1, 99, 9.0, conn)
            conn.close()
        self.assertEqual(png, b"PNGpng")
        self.assertEqual((width, height), (595, 842))
        # page=99 收敛到最后一页，scale=9.0 收敛到上限 3.0。
        self.assertEqual(rendered.page.matrix, (3.0, 3.0))

    def test_missing_document_raises(self):
        conn = self._conn()
        with self.assertRaises(LookupError):
            dr.get_document(999, conn)
        conn.close()

    def test_text_missing_raises(self):
        self.txt.unlink()
        conn = self._conn()
        with self.assertRaises(LookupError):
            dr.load_text(1, conn)
        conn.close()


if __name__ == "__main__":
    unittest.main()
