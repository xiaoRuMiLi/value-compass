"""财报分段挖掘模块单测：分段结构与证据合并。"""

import unittest

from fundamental.services.narrative._quality import is_numeric_quote
from fundamental.services.narrative._forensics import _merge_evidence
from fundamental.services.narrative._report_mining import (
    _pick_diverse,
    _pick_documents,
    _report_label,
    _report_seq,
    _split_by_paragraphs,
    _split_large_block,
    _split_sections,
    split_report,
)


class TestSplitSections(unittest.TestCase):
    def test_splits_by_chapter_headings(self):
        text = "前置说明\n第一节 释义\n内容A\n第二节 公司简介\n内容B\n第三节 管理层讨论与分析\n内容C"
        sections = _split_sections(text)
        names = [name for name, _ in sections]
        self.assertIn("第一节 释义", names)
        self.assertIn("第二节 公司简介", names)
        self.assertIn("第三节 管理层讨论与分析", names)
        self.assertTrue(any("内容C" in body for _, body in sections))

    def test_no_heading_returns_single_section(self):
        sections = _split_sections("没有任何章节标题的纯文本。\n第二行内容。")
        self.assertEqual(len(sections), 1)

    def test_toc_line_not_treated_as_heading(self):
        # 目录行带点线且较长，不应被当成章节正文标题
        text = "第三节 管理层讨论与分析...........12\n第三节 管理层讨论与分析\n正文内容"
        sections = _split_sections(text)
        self.assertEqual(len(sections), 2)


class TestSplitParagraphs(unittest.TestCase):
    def test_splits_at_paragraph_boundaries_when_over_budget(self):
        # 每段都超预算时，必须沿空行边界切，不能把一段劈两半
        p1 = "第一段内容" * 12
        p2 = "第二段内容" * 12
        p3 = "第三段内容" * 12
        text = f"{p1}\n\n{p2}\n\n{p3}"
        chunks = _split_by_paragraphs(text, max_chars=80)
        self.assertGreaterEqual(len(chunks), 3)
        # 没有任何一个 chunk 跨越两段（不会同时含"第一段"和"第二段"）
        for chunk in chunks:
            self.assertFalse("第一段" in chunk and "第二段" in chunk)
            self.assertFalse("第二段" in chunk and "第三段" in chunk)

    def test_merges_small_paragraphs_under_budget(self):
        text = "甲段。\n\n乙段。\n\n丙段。"
        chunks = _split_by_paragraphs(text, max_chars=1000)
        self.assertEqual(len(chunks), 1)
        self.assertIn("甲段", chunks[0])
        self.assertIn("丙段", chunks[0])

    def test_splits_when_over_budget(self):
        text = "段落一内容较长。\n\n段落二内容较长。\n\n段落三内容较长。"
        chunks = _split_by_paragraphs(text, max_chars=12)
        self.assertGreater(len(chunks), 1)
        # 每段都不超预算太多（允许单个 block 单独成段的边界情况）
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 12)


class TestSplitLargeBlock(unittest.TestCase):
    def test_splits_at_sentence_boundary(self):
        block = "第一句话内容足够长。第二句话内容足够长。第三句话内容足够长。"
        chunks = _split_large_block(block, max_chars=11)
        self.assertGreater(len(chunks), 1)
        joined = "".join(chunks)
        # 不丢字：所有汉字仍在（仅切分点）
        self.assertEqual(joined.count("内"), block.count("内"))

    def test_splits_multiline_block_by_lines(self):
        block = "科目A 100\n科目B 200\n科目C 300\n科目D 400"
        chunks = _split_large_block(block, max_chars=12)
        self.assertGreater(len(chunks), 1)
        self.assertIn("科目A", chunks[0])


class TestSplitReportIntegration(unittest.TestCase):
    def test_realistic_report_keeps_chapters_intact(self):
        text = (
            "第一节 释义\n术语说明。\n\n"
            "第二节 财务指标\n营业收入 100。\n\n"
            "第三节 管理层讨论与分析\n" + ("经营情况说明内容。" * 200) + "\n\n"
            "第四节 公司治理\n治理内容。"
        )
        segments = split_report(text, max_chars=500)
        self.assertGreater(len(segments), 3)
        # 每段的 section 标签都带章节名，且按章节顺序出现
        sections = [s["section"] for s in segments]
        self.assertTrue(any("第三节" in name for name in sections))
        self.assertTrue(any("第四节" in name for name in sections))
        # 超长章节被二次切分成多段，标签含 (1/N)
        third_parts = [name for name in sections if "第三节" in name]
        self.assertGreater(len(third_parts), 1)
        self.assertTrue(any("（1/" in name for name in third_parts))
        # 每段文本非空
        for seg in segments:
            self.assertTrue(seg["text"].strip())


class TestPickDocuments(unittest.TestCase):
    """通读池：年报组（权威）+ 最新季报组（反映当下）的分层选择。"""

    def _doc(self, doc_type, year, title=""):
        return {
            "doc_type": doc_type,
            "period": str(year),
            "title": title or f"{year} {doc_type}",
            "local_path": f"/tmp/{doc_type}_{year}.pdf",
        }

    def test_selects_latest_annual_and_recent_quarterlies(self):
        docs = [
            self._doc("annual_report", 2025),
            self._doc("annual_report", 2024),
            self._doc("annual_report", 2023),
            self._doc("q1_report", 2026),
            self._doc("semi_report", 2026),
            self._doc("q3_report", 2026),
            self._doc("q1_report", 2025),  # 更早的季报不应挤掉 2026 季报
        ]
        # 用临时 text 文件让 _text_path 命中
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            for d in docs:
                p = d["local_path"].lstrip("/")
                full = os.path.join(td, p)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fp:
                    fp.write("x")
                with open(full[:-4] + ".txt", "w", encoding="utf-8") as fp:
                    fp.write("正文")
                d["local_path"] = full
            sel = _pick_documents(docs, {"2025"}, count=2, quarterly_count=3)

        types = [d["doc_type"] for d in sel]
        # 最新在前：2026Q3/H1/Q1 三个季报 + 2025/2024 两个年报，共 5 份
        self.assertEqual(types[:3], ["q3_report", "semi_report", "q1_report"])
        self.assertEqual(types[3:], ["annual_report", "annual_report"])
        self.assertEqual(sel[3]["period"], "2025")
        self.assertEqual(sel[4]["period"], "2024")

    def test_quarterlies_can_be_disabled(self):
        docs = [
            self._doc("annual_report", 2025),
            self._doc("q3_report", 2026),
        ]
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            for d in docs:
                full = os.path.join(td, os.path.basename(d["local_path"]))
                with open(full, "w", encoding="utf-8") as fp:
                    fp.write("pdf")  # with_suffix 需要真实文件
                with open(full[:-4] + ".txt", "w", encoding="utf-8") as fp:
                    fp.write("正文")
                d["local_path"] = full
            sel = _pick_documents(docs, {"2025"}, count=2, quarterly_count=0)
        self.assertEqual([d["doc_type"] for d in sel], ["annual_report"])

    def test_report_seq_and_label(self):
        self.assertGreater(
            _report_seq({"doc_type": "q3_report", "period": "2026"}),
            _report_seq({"doc_type": "annual_report", "period": "2025"}),
        )
        self.assertEqual(
            _report_label({"doc_type": "semi_report", "period": "2026"}), "2026 中报"
        )
        self.assertEqual(
            _report_label({"doc_type": "annual_report", "period": "2025"}), "2025 年报"
        )


class TestPickDiverse(unittest.TestCase):
    """证据截断：信息密度优先 + 单一报告期限流，防止季报数字行刷屏。"""

    def test_explanation_beats_numeric_and_no_single_source_flood(self):
        items = [
            {"quote": "营业收入 40,267,482,684.56 3.08 130,970,743,649.04 1.96", "report_label": "2026 三季报", "seq": 20263},
            {"quote": "净利润 19,950,545,560.09 3.78 64,467,425,981.95 -0.25", "report_label": "2026 三季报", "seq": 20263},
            {"quote": "营业利润 88,979,846,257.79 89,489,694,566.48", "report_label": "2026 三季报", "seq": 20263},
            {"quote": "合同负债由年初80.07亿元降至20.84亿元", "report_label": "2026 三季报", "seq": 20263},
            {"quote": "营业收入变动原因说明：主要是本期销量增加所致", "report_label": "2025 年报", "seq": 20254},
            {"quote": "销售费用增加主要是广告宣传及市场投入增加", "report_label": "2025 年报", "seq": 20254},
        ]
        picked = _pick_diverse(items, 6)
        # 年报的解释性短句必须入选
        texts = [p["quote"] for p in picked]
        self.assertIn("营业收入变动原因说明：主要是本期销量增加所致", texts)
        # 单一报告期最多占 (6+1)//2 = 3 条，三季报不能 4 条全占
        q3 = sum(1 for p in picked if p["report_label"] == "2026 三季报")
        self.assertLessEqual(q3, 3)

    def test_empty(self):
        self.assertEqual(_pick_diverse([], 8), [])


class TestNumericQuote(unittest.TestCase):
    """新线索证据判定：裸数字表格行 vs 带文字结论。"""

    def test_numeric_table_row_detected(self):
        self.assertTrue(is_numeric_quote("合同负债 2,084,485,744.75 8,006,739,780.94"))
        self.assertTrue(is_numeric_quote("营业收入 40,267,482,684.56 3.08 130,970,743,649.04"))

    def test_text_conclusion_not_flagged(self):
        self.assertFalse(is_numeric_quote("营业收入变动原因说明：主要是本期销量增加所致"))
        self.assertFalse(is_numeric_quote("合同负债大幅收缩，或反映渠道打款意愿下降"))


class TestMergeEvidence(unittest.TestCase):
    def test_mined_takes_priority_and_keyword_fills_gap(self):
        mined = {
            "signal_evidence": {
                "S1": [
                    {
                        "quote": "公司官方说明原文",
                        "section": "管理层讨论",
                        "source": "2024年报",
                        "period": "2024",
                        "why": "直接解释营收变动",
                    }
                ]
            }
        }
        keyword = {
            "S1": [{"text": "关键词命中的另一句", "source": "2024年报", "period": "2024", "keywords": "营业收入"}],
            "S2": [{"text": "仅关键词命中", "source": "2024年报", "period": "2024", "keywords": "净利润"}],
        }
        merged = _merge_evidence(mined, keyword)
        self.assertEqual(len(merged["S1"]), 2)
        self.assertEqual(merged["S1"][0]["method"], "llm_mining")
        self.assertEqual(merged["S1"][1]["method"], "keyword")
        self.assertIn("S2", merged)
        self.assertEqual(merged["S2"][0]["method"], "keyword")

    def test_dedups_same_quote(self):
        mined = {
            "signal_evidence": {
                "S1": [
                    {"quote": "重复的原文句子", "section": "A", "source": "x", "period": "2024"},
                    {"quote": "重复的原文句子", "section": "B", "source": "y", "period": "2024"},
                ]
            }
        }
        merged = _merge_evidence(mined, {})
        self.assertEqual(len(merged["S1"]), 1)

    def test_empty_inputs(self):
        self.assertEqual(_merge_evidence({}, {}), {})


if __name__ == "__main__":
    unittest.main()
