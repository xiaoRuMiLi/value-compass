"""原文检索的报告期对齐单测：季度标签解析、同季文档选择、段落评分。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from fundamental.services.narrative import _evidence as ev


def _doc(doc_type: str, title: str, tmp: Path, period: str = "2022") -> dict[str, Any]:
    path = tmp / f"{doc_type}.txt"
    path.write_text("示例财报正文", encoding="utf-8")
    return {"doc_type": doc_type, "period": period, "title": title, "local_path": str(path)}


class PeriodParsingTest(unittest.TestCase):
    def test_target_quarter_from_labels(self) -> None:
        self.assertEqual(ev._target_period_key("2022Q3"), (2022, 3))
        self.assertEqual(ev._target_period_key("2026-09-30"), (2026, 3))
        self.assertEqual(ev._target_period_key("2025年报"), (2025, 4))
        self.assertEqual(ev._target_period_key("2025年"), (2025, 4))

    def test_document_quarter_from_type_and_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                ev._doc_period_key(_doc("q3_report", "截至九月三十日止三個月業績公佈", root)),
                (2022, 3),
            )
            self.assertEqual(
                ev._doc_period_key(_doc("annual_report", "全年業績公佈", root)), (2022, 4)
            )


class DocumentSelectionTest(unittest.TestCase):
    def test_quarter_target_only_picks_same_quarter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = [
                _doc("annual_report", "截至二零二二年十二月三十一日止年度全年業績公佈", root),
                _doc("q1_report", "截至二零二二年三月三十一日止三個月業績公佈", root),
                _doc("q3_report", "截至二零二二年九月三十日止三個月及九個月業績公佈", root),
            ]
            picked = ev._pick_documents(docs, "2022", "2022Q3")
            self.assertEqual(len(picked), 1)
            self.assertEqual(picked[0]["doc_type"], "q3_report")
            # 旧调用方式（风险点扫描）保持年报优先取三份
            legacy = ev._pick_documents(docs, "2022")
            self.assertEqual(len(legacy), 3)
            self.assertEqual(legacy[0]["doc_type"], "annual_report")


class ScoringTest(unittest.TestCase):
    def test_same_quarter_and_specific_term_outrank_generic(self) -> None:
        terms = ("歸母淨利潤", "主要由於")
        q3 = ev._score_paragraph("二零二二年第三季本公司權益持有人應佔盈利主要由於收益增加", terms, 3)
        q1 = ev._score_paragraph("二零二二年第一季主要由於市場推廣開支減少", terms, 3)
        generic = ev._score_paragraph("主要由於若干因素", terms, 3)
        self.assertGreater(q3, q1)
        self.assertGreater(q3, generic)


if __name__ == "__main__":
    unittest.main()
