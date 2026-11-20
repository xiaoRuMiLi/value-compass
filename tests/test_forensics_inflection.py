"""业绩拐点（跨期模式识别）测试。

拐点是"多年高增长平台结束并转入持续负增长"的跨期模式，不是单点规则：
测试覆盖平台判定、持续性判定、不重复触发与配置边界。
"""

from __future__ import annotations

import unittest

from fundamental.analysis.forensics import _detect_growth_inflection
from fundamental.analysis.forensics_config import ForensicsConfig


def s(period: str, yoy: float | None) -> dict:
    """构造一个最小 series 条目，拐点检测只消费 net_profit_yoy 与 period/report_date。"""
    return {
        "period": period,
        "report_date": period[:10],
        "net_profit_yoy": yoy,
    }


class TestGrowthInflection(unittest.TestCase):
    def setUp(self):
        self.cfg = ForensicsConfig(
            profit_turn_high_pct=10.0,
            inflection_platform_periods=4,
            inflection_platform_floor_pct=3.0,
            inflection_min_negative_periods=2,
        )

    def test_detects_high_growth_to_sustained_decline(self):
        # 茅台式：连续 4 期高增长（15/12/9/6），随后转负并持续
        series = [
            s("2023-12-31 年报", 19.0),
            s("2024-12-31 年报", 15.38),
            s("2025-03-31 一季报", 11.56),
            s("2025-06-30 中报", 8.89),
            s("2025-09-30 三季报", 6.25),
            s("2025-12-31 年报", -4.53),   # 拐点期
            s("2026-03-31 一季报", 1.47),
            s("2026-06-30 中报", -1.95),
            s("2026-09-30 三季报", -0.25),
        ]
        got = _detect_growth_inflection(series, 0, self.cfg)
        self.assertIsNotNone(got)
        # 拐点确认期是 2025FY，而非后续已转负的各期
        self.assertEqual(got["turn"]["period"], "2025-12-31 年报")
        self.assertAlmostEqual(round(got["platform_avg"], 2), 10.52)
        self.assertEqual(got["neg_count"], 3)  # 2025FY/H1/Q3 为负，Q1 转正

    def test_no_inflection_when_growth_continues(self):
        series = [s(f"20{20+i}-12-31 年报", 12.0 + i) for i in range(6)]
        self.assertIsNone(_detect_growth_inflection(series, 0, self.cfg))

    def test_no_inflection_on_single_negative_blip(self):
        # 单期转负后立即恢复高增长：持续性不足，不算拐点
        series = [
            s("2021-12-31", 15.0), s("2022-12-31", 14.0),
            s("2023-12-31", 13.0), s("2024-12-31", 12.0),
            s("2025-12-31", -5.0), s("2026-03-31", 11.0),
            s("2026-06-30", 12.0),
        ]
        self.assertIsNone(_detect_growth_inflection(series, 0, self.cfg))

    def test_no_inflection_when_platform_not_high_enough(self):
        # 平台期增速仅 4~5%，均值未达 10% 门槛
        series = [
            s("2021-12-31", 4.0), s("2022-12-31", 5.0),
            s("2023-12-31", 4.5), s("2024-12-31", 5.0),
            s("2025-12-31", -6.0), s("2026-06-30", -3.0),
        ]
        self.assertIsNone(_detect_growth_inflection(series, 0, self.cfg))

    def test_platform_floor_rejects_low_growth_period(self):
        # 平台期内有一期仅 +1%（低于 floor 3%），平台不成立
        series = [
            s("2021-12-31", 15.0), s("2022-12-31", 14.0),
            s("2023-12-31", 1.0), s("2024-12-31", 13.0),
            s("2025-12-31", -6.0), s("2026-06-30", -3.0),
        ]
        self.assertIsNone(_detect_growth_inflection(series, 0, self.cfg))

    def test_window_start_limits_candidate_turn(self):
        # 拐点期落在窗口之外时不应被检出
        series = [
            s("2020-12-31", 15.0), s("2021-12-31", 14.0),
            s("2022-12-31", 13.0), s("2023-12-31", 12.0),
            s("2024-12-31", -8.0), s("2025-12-31", -5.0),
        ]
        # window_start=5 → 只考察 index>=5，2024 的拐点( index 4 )被排除；
        # index 5 虽为负但其前方平台含 -8.0，不成立
        self.assertIsNone(_detect_growth_inflection(series, 5, self.cfg))


if __name__ == "__main__":
    unittest.main()
