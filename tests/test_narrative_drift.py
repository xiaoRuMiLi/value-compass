"""跨期叙事对照单元测试：文档切分、基线 reduce、当期 reduce、缓存与降级。

不调用真实 LLM：只验证纯逻辑（切分/核验/归一化/限流），
LLM 调用与逐字引文核验的契约由 test_report_mining 覆盖同一套 _quote_verified。
"""

from __future__ import annotations

import unittest

from fundamental.services.narrative import _drift


def _doc(period: str, doc_type: str = "annual_report", title: str = "") -> dict:
    return {"period": period, "doc_type": doc_type, "title": title or period}


class SplitPeriodsTests(unittest.TestCase):
    def test_history_excludes_latest_annual(self):
        docs = [
            _doc("2026-03-31", "q1_report"),
            _doc("2025-12-31", "annual_report"),
            _doc("2024-12-31", "annual_report"),
            _doc("2023-12-31", "annual_report"),
            _doc("2022-12-31", "annual_report"),
        ]
        # 注入一个假的 _text_path 让 split_periods 认为都有文本
        orig = _drift._text_path
        _drift._text_path = lambda d: object()
        try:
            history, current = _drift.split_periods(docs, history_count=2)
        finally:
            _drift._text_path = orig
        self.assertEqual([d["period"] for d in history], ["2024-12-31", "2023-12-31"])
        # 当期 = 最新年报 + 其后所有季报
        self.assertIn(_doc("2025-12-31"), current)
        self.assertIn(_doc("2026-03-31", "q1_report"), current)
        self.assertNotIn(_doc("2022-12-31"), current)

    def test_no_history_when_only_one_annual(self):
        docs = [_doc("2025-12-31"), _doc("2025-06-30", "semi_report")]
        orig = _drift._text_path
        _drift._text_path = lambda d: object()
        try:
            history, current = _drift.split_periods(docs, history_count=3)
        finally:
            _drift._text_path = orig
        self.assertEqual(history, [])
        self.assertEqual(len(current), 2)

    def test_fallback_without_annual(self):
        docs = [_doc("2026-03-31", "q1_report"), _doc("2025-12-31", "q3_report")]
        orig = _drift._text_path
        _drift._text_path = lambda d: object()
        try:
            history, current = _drift.split_periods(docs, history_count=3)
        finally:
            _drift._text_path = orig
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["period"], "2026-03-31")
        self.assertEqual(len(history), 1)


class ReduceTests(unittest.TestCase):
    def test_baseline_groups_filters_unknown_factor_and_dedups(self):
        results = [
            {"hits": [
                {"factor_id": "DRF05", "quote": "关联交易逐字摘录说明这里是公司自己的解释文字", "note": "关联", "report_label": "2024 年报", "period": "2024"},
                {"factor_id": "DRF05", "quote": "关联交易逐字摘录说明这里是公司自己的解释文字", "note": "重复", "report_label": "2024 年报", "period": "2024"},
                {"factor_id": "BAD", "quote": "不该出现的因素xxxxxxxxxxxxxxxx", "note": "", "report_label": "", "period": "2024"},
            ], "summary": "往年以预收款为主"},
            {"hits": [], "summary": "", "failed": True},
        ]
        by_factor, summaries, rejected = _drift.reduce_baseline(results, _drift.RISK_FACTOR_PROBES)
        self.assertEqual(len(by_factor.get("DRF05", [])), 1)
        self.assertNotIn("BAD", by_factor)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(rejected, 1)

    def test_baseline_text_respects_budget(self):
        by_factor = {
            "DRF01": [
                {"quote": "销售商品确认收入的主要说明" * 30, "note": "", "report_label": "2024 年报"}
            ]
        }
        text = _drift.baseline_text(by_factor, _drift.RISK_FACTOR_PROBES)
        self.assertLessEqual(len(text), _drift._MAX_BASELINE_CHARS + 1)
        self.assertIn("DRF01", text)

    def test_reduce_findings_limits_per_factor_and_sorts(self):
        results = [{"findings": [], "failed": False}]
        for i in range(5):
            results[0]["findings"].append({
                "factor_id": "DRF04", "title": f"线索{i}", "severity": "low",
                "shift_type": "表述变化", "judgement": f"判断内容{i}", "quote": "经营活动现金流主要说明文字",
            })
        results[0]["findings"].append({
            "factor_id": "DRF05", "title": "高", "severity": "high",
            "shift_type": "口径突变", "judgement": "高风险判断", "quote": "关联交易主要说明文字",
        })
        findings, rejected = _drift.reduce_findings(results)
        self.assertEqual(findings[0]["severity"], "high")  # 高严重度排前
        self.assertLessEqual(sum(1 for f in findings if f["factor_id"] == "DRF04"), 2)
        self.assertEqual(rejected, 0)


class SanityTests(unittest.TestCase):
    def test_probes_unique_ids(self):
        ids = [p["id"] for p in _drift.RISK_FACTOR_PROBES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_result_shape(self):
        r = _drift.empty_result("600519.SH", "no llm")
        self.assertFalse(r["available"])
        self.assertEqual(r["symbol"], "600519.SH")
        self.assertIn("history", r)
        self.assertIn("current", r)

    def test_shift_type_normalized(self):
        # 非法 shift_type 归一化为「表述变化」（在 _map_current 内完成，这里验证枚举表）
        self.assertIn("口径突变", _drift._SHIFT_TYPES)
        self.assertIn("承诺未兑现", _drift._SHIFT_TYPES)


if __name__ == "__main__":
    unittest.main()
