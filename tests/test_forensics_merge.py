"""摘录合并测试：信息密度排序让公司原因说明短句优先于裸数字表格行。"""

from __future__ import annotations

import unittest

from fundamental.services.narrative import _forensics as f


class ExcerptQualityTests(unittest.TestCase):
    def test_explanation_sentence_beats_numeric_row(self):
        explanation = (
            "营业收入变动原因说明：主要是本期销量增加及茅台酒主要产品销售价格调整。"
            "营业成本变动原因说明：主要是本期销量增加、原材料成本上涨。"
        )
        numeric_row = (
            "营业收入 170,899,152,276.34 147,693,604,994.14 15.71 "
            "124,099,843,771.99 归属于上市公司股东的净利润 86,228,146,421.62"
        )
        self.assertGreater(f._excerpt_quality(explanation), f._excerpt_quality(numeric_row))

    def test_merge_sorts_explanations_first(self):
        mined = {
            "SIG002": [
                {"quote": "营业收入 170,899,152,276.34 147,693,604,994.14 123.45", "source": "年报", "period": "2024"},
                {"quote": "财务费用变动原因说明：主要是本期存款利率下降。", "source": "年报", "period": "2024"},
            ]
        }
        merged = f._merge_evidence({"signal_evidence": mined}, {})
        texts = [item["text"] for item in merged["SIG002"]]
        self.assertIn("变动原因说明", texts[0])
        self.assertTrue(all(m["method"] == "llm_mining" for m in merged["SIG002"]))

    def test_merge_keeps_all_when_few(self):
        keyword_map = {
            "SIG003": [
                {"text": "合同负债 3,027,195,224.08 8,006,739,780.94", "source": "年报", "period": "2024"},
            ]
        }
        merged = f._merge_evidence({}, keyword_map)
        self.assertEqual(len(merged["SIG003"]), 1)
        self.assertEqual(merged["SIG003"][0]["method"], "keyword")


if __name__ == "__main__":
    unittest.main()
