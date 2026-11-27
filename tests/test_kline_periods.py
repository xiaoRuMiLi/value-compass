"""报告期 K 线聚合的单测：季度/年度区间、缺失区间、空值与边界。"""

from __future__ import annotations

import unittest

from fundamental.analysis.kline_periods import (
    align_close_at_period_end,
    align_kline_to_periods,
    period_range,
)


def _bar(date: str, o: float, c: float, low: float | None, high: float | None) -> dict:
    return {"trade_date": date, "open": o, "close": c, "low": low, "high": high}


class PeriodRangeTest(unittest.TestCase):
    def test_quarter_bounds(self) -> None:
        self.assertEqual(period_range("2026Q1"), ("2026-01-01", "2026-03-31"))
        self.assertEqual(period_range("2026Q2"), ("2026-04-01", "2026-06-30"))
        self.assertEqual(period_range("2026Q3"), ("2026-07-01", "2026-09-30"))
        self.assertEqual(period_range("2026Q4"), ("2026-10-01", "2026-12-31"))

    def test_iso_report_date_bounds(self) -> None:
        # 资产负债表拆解用 ISO 报告期末标签，K 线须落在同一季度区间
        self.assertEqual(period_range("2026-03-31"), ("2026-01-01", "2026-03-31"))
        self.assertEqual(period_range("2026-06-30"), ("2026-04-01", "2026-06-30"))
        self.assertEqual(period_range("2026-09-30"), ("2026-07-01", "2026-09-30"))
        self.assertEqual(period_range("2026-12-31"), ("2026-10-01", "2026-12-31"))

    def test_annual_bounds(self) -> None:
        self.assertEqual(period_range("2026年报"), ("2026-01-01", "2026-12-31"))

    def test_unknown_suffix_falls_back_to_full_year(self) -> None:
        self.assertEqual(period_range("2026TTM"), ("2026-01-01", "2026-12-31"))


class AlignKlineTest(unittest.TestCase):
    def setUp(self) -> None:
        # 覆盖三个季度：Q1 两根、Q2 一根、Q3 三根；区间外各放一根防止越界取用
        self.rows = [
            _bar("2025-12-31", 100, 105, 99, 106),
            _bar("2026-01-05", 110, 120, 108, 122),
            _bar("2026-02-10", 121, 130, 118, 133),
            _bar("2026-05-20", 200, 210, 195, 215),
            _bar("2026-07-01", 300, 310, 295, 315),
            _bar("2026-08-15", 311, 290, 285, 320),
            _bar("2026-09-30", 291, 305, 288, 308),
            _bar("2026-10-08", 306, 400, 300, 405),
        ]

    def test_quarter_aggregation(self) -> None:
        q1, q2, q3 = align_kline_to_periods(self.rows, ["2026Q1", "2026Q2", "2026Q3"])
        assert q1 is not None and q2 is not None and q3 is not None
        # Q1：开=1/5 开盘，收=2/10 收盘，低/高取两日极值
        self.assertEqual((q1["open"], q1["close"]), (110, 130))
        self.assertEqual((q1["low"], q1["high"]), (108, 133))
        self.assertEqual(q1["trading_days"], 2)
        self.assertEqual(q1["trade_date"], "2026-02-10")
        self.assertEqual((q1["period_start"], q1["period_end"]), ("2026-01-01", "2026-03-31"))
        # Q2：仅一根
        self.assertEqual((q2["open"], q2["close"], q2["low"], q2["high"]), (200, 210, 195, 215))
        # Q3：收=9/30 收盘，低=8/15 的 285，高=8/15 的 320，不含区间外的 10/8
        self.assertEqual((q3["open"], q3["close"]), (300, 305))
        self.assertEqual((q3["low"], q3["high"]), (285, 320))
        self.assertEqual(q3["trading_days"], 3)

    def test_iso_label_aggregation_matches_quarter(self) -> None:
        by_iso = align_kline_to_periods(self.rows, ["2026-09-30"])[0]
        by_quarter = align_kline_to_periods(self.rows, ["2026Q3"])[0]
        assert by_iso is not None and by_quarter is not None
        self.assertEqual(
            (by_iso["open"], by_iso["close"], by_iso["low"], by_iso["high"]),
            (by_quarter["open"], by_quarter["close"], by_quarter["low"], by_quarter["high"]),
        )

    def test_missing_period_returns_none(self) -> None:
        aligned = align_kline_to_periods(self.rows, ["2024Q1", "2026Q2"])
        self.assertIsNone(aligned[0])
        self.assertIsNotNone(aligned[1])

    def test_annual_aggregation_spans_full_year(self) -> None:
        bar = align_kline_to_periods(self.rows, ["2026年报"])[0]
        assert bar is not None
        self.assertEqual((bar["period_start"], bar["period_end"]), ("2026-01-01", "2026-12-31"))
        # 全年：开=1/5 开盘 110，收=10/8 收盘 400，极值覆盖全年
        self.assertEqual((bar["open"], bar["close"]), (110, 400))
        self.assertEqual((bar["low"], bar["high"]), (108, 405))

    def test_none_values_excluded_from_extremes(self) -> None:
        rows = [
            _bar("2026-07-02", 10, 12, None, None),
            _bar("2026-07-03", 13, 14, 9, 15),
        ]
        bar = align_kline_to_periods(rows, ["2026Q3"])[0]
        assert bar is not None
        self.assertEqual((bar["low"], bar["high"]), (9, 15))

    def test_all_none_extremes_are_none(self) -> None:
        rows = [_bar("2026-07-02", 10, 12, None, None)]
        bar = align_kline_to_periods(rows, ["2026Q3"])[0]
        assert bar is not None
        self.assertIsNone(bar["low"])
        self.assertIsNone(bar["high"])

    def test_empty_rows(self) -> None:
        self.assertEqual(align_kline_to_periods([], ["2026Q3"]), [None])


class CloseAtPeriodEndTest(unittest.TestCase):
    def test_iso_label_takes_last_trade_at_or_before_end(self) -> None:
        rows = [
            {"trade_date": "2026-06-29", "close": 100.0},
            {"trade_date": "2026-06-30", "close": 105.0},  # 期末当天有行情
            {"trade_date": "2026-07-01", "close": 110.0},
            {"trade_date": "2026-09-29", "close": 120.0},  # 9-30 休市，前向找
        ]
        result = align_close_at_period_end(rows, ["2026-06-30", "2026-09-30"])
        self.assertEqual(result, [105.0, 120.0])

    def test_year_label_uses_year_end(self) -> None:
        rows = [
            {"trade_date": "2025-12-30", "close": 300.0},
            {"trade_date": "2025-12-31", "close": 310.0},
        ]
        self.assertEqual(align_close_at_period_end(rows, ["2025年"]), [310.0])

    def test_no_price_before_period_returns_none(self) -> None:
        rows = [{"trade_date": "2026-03-02", "close": 50.0}]
        self.assertEqual(align_close_at_period_end(rows, ["2020-12-31", "2026-03-31"]), [None, 50.0])


if __name__ == "__main__":
    unittest.main()
