"""资产负债表拆解纯计算单测：去重、占比、同比、投资归类。"""

from __future__ import annotations

import unittest

from fundamental.analysis.balance_breakdown import (
    aggregate_fcf_yearly,
    aggregate_item_yearly,
    build_balance_breakdown,
    cumulative_to_single_quarter,
    yearly_labels_from,
)


def _row(date: str, name: str, value: float, stmt: str = "balance") -> dict:
    return {"report_date": date, "item_name": name, "item_value": value, "statement_type": stmt}


class BalanceBreakdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _row("2024-12-31", "总资产", 1000),
            _row("2025-12-31", "总资产", 1200),
            _row("2024-12-31", "流动资产合计", 400),
            _row("2025-12-31", "流动资产合计", 480),
            # 联营公司权益：数字码与语义码同值，不能重复加总
            _row("2024-12-31", "联营公司权益", 100),
            _row("2024-12-31", "LONG_EQUITY_INVEST", 100),
            _row("2025-12-31", "联营公司权益", 150),
            _row("2025-12-31", "LONG_EQUITY_INVEST", 150),
            # 公允价值投资
            _row("2024-12-31", "指定以公允价值记账之金融资产", 200),
            _row("2025-12-31", "指定以公允价值记账之金融资产", 240),
            # 非流动负债
            _row("2025-12-31", "长期贷款", 300),
        ]

    def test_labels_sorted(self) -> None:
        result = build_balance_breakdown(self.rows)
        self.assertEqual(result["labels"], ["2024-12-31", "2025-12-31"])

    def test_dedup_alias_no_double_count(self) -> None:
        result = build_balance_breakdown(self.rows)
        assoc = next(
            i for i in result["sections"]["assets_noncurrent"]["items"] if i["name"] == "联营公司权益"
        )
        self.assertEqual(assoc["values"], [100, 150])
        self.assertEqual(assoc["latest"], 150)

    def test_share_and_yoy(self) -> None:
        result = build_balance_breakdown(self.rows)
        assoc = next(
            i for i in result["sections"]["assets_noncurrent"]["items"] if i["name"] == "联营公司权益"
        )
        # 150 / 1200 = 12.5%；同比 150/100-1 = 50%
        self.assertEqual(assoc["share_of_assets"], 12.5)
        self.assertEqual(assoc["yoy"], 50.0)

    def test_investment_class_equity_method(self) -> None:
        result = build_balance_breakdown(self.rows)
        eq = next(c for c in result["investment_classes"] if c["key"] == "equity_method")
        self.assertEqual(eq["values"], [100, 150])
        self.assertEqual(eq["latest"], 150)

    def test_investment_class_fair_value(self) -> None:
        result = build_balance_breakdown(self.rows)
        fv = next(c for c in result["investment_classes"] if c["key"] == "fair_value")
        self.assertEqual(fv["values"], [200, 240])

    def test_missing_item_marked(self) -> None:
        result = build_balance_breakdown(self.rows)
        missing = [
            i for i in result["sections"]["liabilities_current"]["items"] if i["latest"] is None
        ]
        self.assertTrue(len(missing) > 0)  # 前端据此提示"财报未单列"

    def test_yearly_series_exposed_for_detail_modal(self) -> None:
        result = build_balance_breakdown(self.rows)
        self.assertEqual(result["year_labels"], ["2024年", "2025年"])
        assoc = next(
            i for i in result["sections"]["assets_noncurrent"]["items"] if i["name"] == "联营公司权益"
        )
        self.assertEqual(assoc["yearly_values"], [100, 150])

    def test_empty_rows(self) -> None:
        result = build_balance_breakdown([])
        self.assertEqual(result["labels"], [])
        self.assertTrue(any("尚未入库" in n for n in result["notes"]))


class YearlyItemTest(unittest.TestCase):
    def test_point_in_time_value_takes_last_available_period_not_sum(self) -> None:
        labels = ["2024-03-31", "2024-06-30", "2024-12-31", "2025-06-30", "2025-12-31"]
        series = [100.0, 110.0, None, 200.0, 230.0]
        year_labels = yearly_labels_from(labels)
        self.assertEqual(year_labels, ["2024年", "2025年"])
        # 资产负债表是时点值：2024 取 6-30 的 110，不累加 100+110；缺年报不硬凑
        self.assertEqual(aggregate_item_yearly(labels, series, year_labels), [110.0, 230.0])

    def test_missing_year_is_none(self) -> None:
        labels = ["2024-12-31", "2026-12-31"]
        year_labels = ["2024年", "2025年", "2026年"]
        self.assertEqual(
            aggregate_item_yearly(labels, [10.0, 30.0], year_labels), [10.0, None, 30.0]
        )


class OcfSingleQuarterTest(unittest.TestCase):
    def test_cumulative_diff_to_single_quarter(self) -> None:
        labels = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
        cumulative = [768.89, 1512.65, 2365.77, 3030.52]
        result = cumulative_to_single_quarter(labels, cumulative)
        # Q1 直接取累计；Q2/Q3/Q4 为相邻差分
        self.assertEqual(result[0], 768.89)
        self.assertEqual(result[1], 743.76)   # 1512.65-768.89
        self.assertEqual(result[2], 853.12)   # 2365.77-1512.65
        self.assertEqual(result[3], 664.75)   # 3030.52-2365.77

    def test_missing_base_returns_none_not_zero(self) -> None:
        labels = ["2025-03-31", "2025-06-30", "2025-09-30"]
        result = cumulative_to_single_quarter(labels, [None, 500.0, 900.0])
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])  # 缺 Q1 基数，不硬凑
        self.assertEqual(result[2], 400.0)


class FcfYearlyTest(unittest.TestCase):
    def test_fcf_single_quarters_summed_ocf_takes_year_end_cumulative(self) -> None:
        labels = [
            "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
            "2026-03-31", "2026-06-30",
        ]
        # FCF 单季值；OCF 年内累计（Q1→H1→前三季→全年）
        fcf = [100.0, 200.0, 150.0, 250.0, 120.0, None]
        ocf = [300.0, 700.0, 1100.0, 1600.0, 400.0, 900.0]
        result = aggregate_fcf_yearly(labels, fcf, ocf)
        self.assertEqual(result["labels"], ["2025年", "2026年"])
        self.assertEqual(result["values"], [700.0, 120.0])  # 2025 四季相加；2026 只有 Q1
        self.assertEqual(result["ocf"], [1600.0, 900.0])    # 取各年最后一个有值期（年末累计）
        self.assertEqual(result["fcf_quarters"], [4, 1])    # 标注参与求和的季度数

    def test_missing_fcf_quarter_keeps_partial_sum_and_count(self) -> None:
        labels = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
        result = aggregate_fcf_yearly(labels, [100.0, None, None, 200.0], [None] * 4)
        self.assertEqual(result["values"], [300.0])
        self.assertEqual(result["fcf_quarters"], [2])
        self.assertIsNone(result["ocf"][0])  # 无 OCF 不编造

    def test_empty(self) -> None:
        result = aggregate_fcf_yearly([], [], [])
        self.assertEqual(result["labels"], [])
        self.assertEqual(result["values"], [])


if __name__ == "__main__":
    unittest.main()
