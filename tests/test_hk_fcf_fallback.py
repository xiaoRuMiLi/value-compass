"""港股 FCF 分项回退测试：原文抽取不可用时必须回退库内年报现金流表。

小米等公司只归档中期报告、财报原文用「經營活動所得現金淨額」表述，
没有腾讯式「第N季自由现金流」句式；此前港股链路直接返回全空，导致
FCFF 触发 early return、估值整档为空。本测试锁定回退行为。
"""

from __future__ import annotations

import unittest
from unittest import mock

from fundamental.models import ReportType, StatementItem, StatementType
from fundamental.services import analyze, hk_financial_supplement


def _cashflow_item(code: str, value: float) -> StatementItem:
    return StatementItem(
        report_date="2025-12-31", report_type=ReportType.FY,
        statement_type=StatementType.CASHFLOW, item_code=code,
        item_name=code, item_value=value,
    )


class HkFcfFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            _cashflow_item("NETCASH_OPERATE", 341.42e8),
            _cashflow_item("CONSTRUCT_LONG_ASSET", 50.0e8),
        ]

    def test_falls_back_to_statements_when_text_extraction_empty(self) -> None:
        """原文抽取全空时回退库内：OCF 有值、口径说明标注回退。"""
        with (
            mock.patch.object(hk_financial_supplement, "extract_cashflow_ttm", return_value={"available": False}),
            mock.patch.object(hk_financial_supplement, "extract_fcf_ttm", return_value={"available": False}),
        ):
            comps = analyze._resolve_fcf_components("01810.HK", self.items)
        self.assertIsNotNone(comps["ocf"])
        self.assertAlmostEqual(comps["ocf"], 341.42e8)
        self.assertAlmostEqual(comps["capex"], 50.0e8)
        self.assertIn("回退库内", comps["note"])

    def test_text_extraction_wins_when_available(self) -> None:
        """原文抽取可用时优先用它，库内值不覆盖（腾讯口径不受影响）。"""
        parts = {
            "available": True, "ocf_ttm_yi": 100.0, "capex_ttm_yi": 20.0,
            "interest_income_yi": 1.0, "interest_expense_yi": 2.0,
            "reported_fcf_ttm_yi": 79.0, "note": "原文口径",
        }
        with (
            mock.patch.object(hk_financial_supplement, "extract_cashflow_ttm", return_value=parts),
        ):
            comps = analyze._resolve_fcf_components("00700.HK", self.items)
        self.assertAlmostEqual(comps["ocf"], 100.0e8)
        self.assertAlmostEqual(comps["capex"], 20.0e8)
        self.assertNotIn("回退库内", comps["note"] or "")


if __name__ == "__main__":
    unittest.main()
