"""PDF CID 乱码还原的单元测试。

小米集团 2022~2026 中期报告的嵌入字体是 Identity-H 子集、没有 /ToUnicode，
文本层输出的是字形 ID（CID），表现为「㛮暈⛚㕾2024⸶3㛊28㖧」。这类乱码曾被
送进 LLM，模型据此生成「完成对某公司收购」等看似合理的中文结论（幻觉式解读）。

本测试用真实摘录片段验证检测、偏移推断、还原与可用性守卫，并确认正常
PDF / 英文版财报不会被误判或改写。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fundamental.utils.pdf import (
    apply_cid_offset,
    extract_pdf_text,
    infer_cid_offset,
    is_garbled_text,
    is_source_text_usable,
)

# 取自小米 2024 中报「重大事项」段的真实 CID 乱码（用户反馈的原文摘录）
_GARBLED = (
    "㛮暈⛚㕾2024⸶3㛊28㖧㬥⻑㍪↼㘼傿暽⊗㰿庌灱"
    "⏎㘄㛮暈⛚Ṩ㍪↼ℸẘ㕲㤯⊛灱ầ䵯㋃ℸ⎱㋃乎䘾Ⱇ傿"
)

# 同一套字体在 2021 中报（有 ToUnicode）里的正常写法，用作还原对照
_PLAIN = "本集團於2024年3月28日正式推出智能電動汽車"

# 英文版财报：正文几乎全是 ASCII，只有少量弯引号 / 方框符号。
# 早期版本按「非 ASCII 字符数」做分母会把它误判成乱码。
_ENGLISH_REPORT = (
    "Wuliangye Yibin Co., Ltd. Annual Report 2023 " * 200
    + "“Important Notes” ’ □ — "
)


class CidTextTest(unittest.TestCase):
    def test_garbled_detection(self):
        self.assertTrue(is_garbled_text(_GARBLED * 20))
        self.assertFalse(is_garbled_text(_PLAIN * 50))
        self.assertFalse(is_garbled_text(_ENGLISH_REPORT))
        # 短文本不误判
        self.assertFalse(is_garbled_text("㛮暈⛚㕾"))
        self.assertFalse(is_garbled_text(""))

    def test_infer_offset(self):
        self.assertEqual(infer_cid_offset(_GARBLED * 20), 12350)

    def test_apply_offset_restores_chinese(self):
        repaired = apply_cid_offset(_GARBLED, 12350)
        self.assertIn("本集團於2024", repaired)
        self.assertIn("3月28日", repaired)
        self.assertIn("智能電動汽車", repaired)
        self.assertIn("2024", repaired)  # ASCII 不受影响
        self.assertFalse(is_garbled_text(repaired * 20))

    def test_infer_then_apply(self):
        sample = _GARBLED * 20
        offset = infer_cid_offset(sample)
        self.assertIsNotNone(offset)
        self.assertFalse(is_garbled_text(apply_cid_offset(sample, offset)))

    def test_source_text_usable_rejects_garbled(self):
        self.assertFalse(is_source_text_usable(_GARBLED * 200, "2024年中期報告"))
        self.assertTrue(is_source_text_usable(_PLAIN * 500, "2024年中期報告"))
        # 披露提示性公告壳仍被拒绝
        self.assertFalse(
            is_source_text_usable(_PLAIN * 500, "年度报告披露的提示性公告")
        )

    def test_normal_pdf_output_unchanged(self):
        """正常 PDF（有 ToUnicode）走原路径，不触发修复、输出逐字不变。"""
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            self.skipTest("pymupdf 未安装")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "normal.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text(
                (72, 72), "2024 年中期報告 Annual Report", fontname="china-s"
            )
            doc.save(str(path))
            doc.close()
            text = extract_pdf_text(path)
            self.assertIn("年中期報告", text)
            self.assertIn("Annual Report", text)
            self.assertFalse(is_garbled_text(text))


if __name__ == "__main__":
    unittest.main()
