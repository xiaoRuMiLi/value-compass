"""单期指标解释的纯逻辑单测：同比/环比、当期快照、缓存指纹与不可用边界。

不依赖真实 LLM 与财报归档；涉及 DB/LLM 的分支用 mock 或纯函数验证。
"""

from __future__ import annotations

import unittest
from typing import Any

from fundamental.services.narrative import _metric_explanation as me


def _item(key: str, name: str, values: list[float | None], scale: float = 1e8) -> dict[str, Any]:
    return {"key": key, "name": name, "unit": "亿元", "scale": scale, "values": values}


class RatioTest(unittest.TestCase):
    def test_change_handles_missing_and_zero_base(self) -> None:
        self.assertEqual(me._change(120.0, 100.0), 20.0)
        self.assertEqual(me._change(80.0, 100.0), -20.0)
        self.assertIsNone(me._change(100.0, None))
        self.assertIsNone(me._change(100.0, 0))
        self.assertIsNone(me._change(None, 100.0))

    def test_period_year(self) -> None:
        self.assertEqual(me.period_year("2025年报"), "2025")
        self.assertEqual(me.period_year("2026-09-30"), "2026")
        self.assertEqual(me.period_year("无年份"), "0")


class SelectedFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.labels = ["2024年报", "2025年报", "2026Q3"]
        self.items = [
            _item("net_profit", "归母净利润", [100e8, 120e8, 30e8]),
            _item("revenue", "营业收入", [None, 500e8, 150e8]),
        ]

    def test_current_prior_yoy_qoq_and_history(self) -> None:
        facts = me._selected_facts(self.items, self.labels, ["net_profit"], 1)
        self.assertEqual(len(facts), 1)
        row = facts[0]
        self.assertEqual(row["指标"], "归母净利润")
        self.assertEqual(row["当期值"], 120.0)
        self.assertEqual(row["上期值"], 100.0)
        self.assertEqual(row["环比%"], 20.0)
        # 2024 年报为去年同期，同比即 20%
        self.assertEqual(row["同比%"], 20.0)
        self.assertEqual(len(row["历史序列"]), 2)

    def test_missing_current_kept_for_guard(self) -> None:
        facts = me._selected_facts(self.items, self.labels, ["net_profit"], 2)
        self.assertEqual(facts[0]["当期值"], 30.0)
        # 目标期若为空，值保留 None，供上层判为不可用
        empty = me._selected_facts(self.items, self.labels, ["revenue"], 0)
        self.assertIsNone(empty[0]["当期值"])

    def test_lookup_by_name_when_key_differs(self) -> None:
        facts = me._selected_facts(self.items, self.labels, ["归母净利润"], 1)
        self.assertEqual(facts[0]["指标"], "归母净利润")
        self.assertEqual(facts[0]["当期值"], 120.0)


class SnapshotTest(unittest.TestCase):
    def test_snapshot_caps_items_and_keeps_history(self) -> None:
        labels = ["2024年报", "2025年报"]
        items = [_item(f"k{i}", f"科目{i}", [(i + 1) * 1e8, (i + 2) * 1e8]) for i in range(30)]
        snap = me._snapshot(items, labels, 1)
        self.assertLessEqual(len(snap["当期同表科目"]), me._SNAPSHOT_LIMIT)
        # 金额最大的排前面
        self.assertEqual(snap["当期同表科目"][0]["科目"], "科目29")
        self.assertEqual(len(snap["历史同表科目"]), 2)
        self.assertEqual(snap["历史同表科目"][0]["报告期"], "2024年报")


class CacheAndFallbackTest(unittest.TestCase):
    def test_cache_path_is_stable_and_separates_requests(self) -> None:
        a = me._cache_path("600519.SH", "2025年报", "primary", "", ["net_profit"])
        b = me._cache_path("600519.SH", "2025年报", "primary", "", ["net_profit"])
        c = me._cache_path("600519.SH", "2025年报", "primary", "", ["revenue"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_read_cache_checks_fingerprint_and_refresh(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            good = {"fingerprint": "abc", "available": True, "version": me._NARRATIVE_VERSION}
            path.write_text(json.dumps(good), encoding="utf-8")
            self.assertIsNotNone(me._read_cache(path, "abc", False))
            self.assertIsNone(me._read_cache(path, "changed", False))
            self.assertIsNone(me._read_cache(path, "abc", True))
            path.write_text(json.dumps({**good, "version": -1}), encoding="utf-8")
            self.assertIsNone(me._read_cache(path, "abc", False))

    def test_template_lists_facts_and_evidence(self) -> None:
        facts = {
            "选中指标": [{
                "指标": "归母净利润", "单位": "亿元", "当期报告期": "2025年报",
                "当期值": 120.0, "上期值": 100.0, "去年同期值": 100.0,
                "同比%": 20.0, "环比%": 20.0,
            }],
            "当期同表科目": [],
        }
        evidence = [{"text": "公司原文", "source": "年报", "period": "2025", "keywords": "净利润"}]
        text = me._template(facts, evidence)
        self.assertIn("120.0", text)
        self.assertIn("公司原文", text)
        self.assertIn("人工核对", text)

    def test_invalid_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            me.generate_metric_explanation("600519.SH", "2025年报", "unknown", ["x"])

    def test_invalid_period_returns_unavailable(self) -> None:
        result = me.generate_metric_explanation("600519.SH", "1900年报", "primary", ["net_profit"])
        self.assertFalse(result["available"])
        self.assertIn("不在当前指标序列", result["text"])

    def test_cached_only_never_generates(self) -> None:
        result = me.generate_metric_explanation(
            "600519.SH", "2025Q3", "primary", ["net_profit"], cached_only=True
        )
        self.assertFalse(result["available"])
        self.assertFalse(result["cached"])
        self.assertIn("尚无缓存", result["text"])


class DbCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        self._tmp = tempfile.TemporaryDirectory()
        from fundamental.storage import db

        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = db.init_db(self.db_path)
        self.conn.close()
        # 服务层每次调用都会关闭自己的连接，patch 必须每次返回新连接
        self._patcher = patch.object(
            me.db, "init_db", side_effect=lambda: db.connect(self.db_path)
        )
        self._patcher.start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._patcher.stop)

    def test_migration_creates_cache_table(self) -> None:
        from fundamental.storage import db

        conn = db.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 8)
            names = [row[1] for row in conn.execute("PRAGMA table_info(metric_explanation_cache)")]
            self.assertIn("cache_key", names)
            self.assertIn("summary", names)
        finally:
            conn.close()

    def test_db_cache_roundtrip_and_index(self) -> None:
        output = {
            "symbol": "600519.SH", "period": "2025Q3", "scope": "primary",
            "stmt_type": "", "keys": ["net_profit"], "available": True,
            "text": "公司原文说明：本期盈利增长。", "source": "llm",
            "fingerprint": "fp-1", "version": me._NARRATIVE_VERSION,
            "generated_at": "2026-11-27T09:00:00+00:00",
        }
        me._write_db_cache("key-1", output)
        cached = me._read_db_cache("key-1", "fp-1", False)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["text"], output["text"])
        self.assertIsNone(me._read_db_cache("key-1", "wrong-fp", False))
        self.assertIsNone(me._read_db_cache("key-1", "fp-1", True))
        index = me.list_cached_explanations("600519.SH")
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["period"], "2025Q3")
        self.assertEqual(index[0]["keys"], ["net_profit"])
        self.assertIn("本期盈利增长", index[0]["summary"])

    def test_index_skips_stale_version(self) -> None:
        from fundamental.storage import db, repository

        conn = db.connect(self.db_path)
        try:
            repository.save_metric_explanation(
                conn, cache_key="old", symbol="600519.SH", period="2025Q3",
                scope="primary", stmt_type="", keys=["net_profit"], fingerprint="fp",
                version=-1, summary="旧版本缓存", payload={"available": True},
                generated_at="2026-01-01T00:00:00+00:00",
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(me.list_cached_explanations("600519.SH"), [])


if __name__ == "__main__":
    unittest.main()
