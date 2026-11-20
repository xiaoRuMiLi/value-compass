"""财报原文证据检索测试：章节切片、变动原因说明提取、关键词匹配与降级。"""

from __future__ import annotations

import unittest

from fundamental.services.narrative import _evidence as ev


class SliceSectionsTests(unittest.TestCase):
    def test_excludes_toc_line(self):
        # 目录行（标题后跟点线）不应被当成章节起点导致整章被跳过
        text = (
            "第三节 管理层讨论与分析.................7\n"
            "第四节 公司治理........................20\n"
            "第三节 管理层讨论与分析\n"
            "一、经营情况讨论与分析 本期营收增长。\n"
            "第四节 公司治理\n"
        )
        paras = ev._slice_sections(text)
        self.assertTrue(any("本期营收增长" in p for p in paras))

    def test_extracts_explanation_sentences_intact(self):
        text = (
            "第三节 管理层讨论与分析\n"
            "1、利润表相关科目变动分析表 营业收入 100 80 25%\n"
            "营业收入变动原因说明：主要是本期销量增加及产品销售价格调整。\n"
            "销售费用变动原因说明：主要是市场推广费增加。\n"
        )
        paras = ev._slice_sections(text)
        joined = " ".join(paras)
        self.assertIn("营业收入变动原因说明：主要是本期销量增加", joined)
        self.assertIn("销售费用变动原因说明", joined)


class TermsAndScoringTests(unittest.TestCase):
    def test_terms_exact_match(self):
        terms = ev._terms_for({"title": "增收不增利"})
        self.assertIn("营业收入", terms)

    def test_terms_fallback(self):
        terms = ev._terms_for({"title": "完全陌生的信号名"})
        self.assertEqual(terms, ev._FALLBACK_TERMS)

    def test_explanation_short_sentence_scores_higher(self):
        terms = ("营业收入", "变动原因")
        short = "营业收入变动原因说明：主要是本期销量增加。"
        long = "科目 本期 上期 " * 30 + "营业收入 " * 8
        self.assertGreater(ev._score_paragraph(short, terms), ev._score_paragraph(long, terms))

    def test_report_year_extraction(self):
        self.assertEqual(ev._report_year({"period": "2024-12-31 年报"}), "2024")
        self.assertIsNone(ev._report_year({"period": "共 6 条"}))


class RetrieveEvidenceTests(unittest.TestCase):
    def test_empty_signals(self):
        self.assertEqual(ev.retrieve_evidence("600519.SH", []), {})

    def test_no_documents_returns_empty(self):
        # 指向一个不存在的股票，文档列表为空，应静默返回空字典而非抛错
        result = ev.retrieve_evidence("000000.SZ", [{"id": "S1", "title": "增收不增利"}])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
