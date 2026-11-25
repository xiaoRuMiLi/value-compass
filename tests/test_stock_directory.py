"""个股库列表服务的单元测试。"""

from __future__ import annotations

import types
import unittest
from unittest import mock

import fundamental.services.stock_directory as sd


class TestStockDirectory(unittest.TestCase):
    def setUp(self) -> None:
        sd._CACHE.clear()

    def test_builds_rows_with_rating(self):
        row = {"id": 1, "code": "600519", "market": "SH", "name": "贵州茅台"}
        with (
            mock.patch.object(sd.repository, "fetch_all", return_value=[row]),
            mock.patch.object(sd.repository, "fetch_one", return_value={"price": 1500.0, "pct_change": 0.01}),
            mock.patch.object(sd, "analyze_stock", return_value={"score": types.SimpleNamespace(total=64.9, rating="观望")}),
        ):
            result = sd.build_stock_directory(object())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "600519.SH")
        self.assertEqual(result[0]["rating"], "观望")
        self.assertAlmostEqual(result[0]["score"], 64.9)

    def test_rating_is_cached(self):
        with mock.patch.object(sd, "analyze_stock", return_value={"score": types.SimpleNamespace(total=70, rating="推荐")}) as analyze:
            self.assertEqual(sd._rating("00700.HK"), (70, "推荐"))
            self.assertEqual(sd._rating("00700.HK"), (70, "推荐"))
        self.assertEqual(analyze.call_count, 1)

    def test_failed_analysis_does_not_break_listing(self):
        with mock.patch.object(sd, "analyze_stock", side_effect=RuntimeError("boom")):
            self.assertEqual(sd._rating("000001.SZ"), (None, None))


if __name__ == "__main__":
    unittest.main()
