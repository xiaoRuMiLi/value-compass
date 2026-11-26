"""无风险利率市场锚点：provider 解析、按市场映射、折现率下限约束。"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from fundamental.analysis.valuation import evaluate_fcff
from fundamental.providers.macro.eastmoney import EastmoneyMacroProvider
from fundamental.services import risk_free
from fundamental.services.risk_free import resolve_risk_free


def _bond_payload() -> dict:
    """模拟东财 RPTA_WEB_TREASURYYIELD：美方数据比中方晚一日。"""
    return {
        "result": {
            "data": [
                {
                    "SOLAR_DATE": "2026-11-25 00:00:00",
                    "EMM00166466": 1.5914,   # 中国10Y
                    "EMG00001310": None,       # 美方当日未更新
                },
                {
                    "SOLAR_DATE": "2026-11-24 00:00:00",
                    "EMM00166466": 1.5853,
                    "EMG00001310": 4.91,
                },
                {
                    "SOLAR_DATE": "2026-11-21 00:00:00",
                    "EMM00166466": 1.58,
                    "EMG00001310": 4.88,
                },
            ]
        }
    }


class TestBondYieldProvider(unittest.TestCase):
    def test_parses_cn_and_us_10y_and_skips_null(self) -> None:
        client = mock.Mock()
        client.get_json.return_value = _bond_payload()
        provider = EastmoneyMacroProvider(client=client)
        rows = provider.fetch_macro_indicators(["cn10y", "us10y"])
        by_code = {(m.indicator_code, m.trade_date): m for m in rows}
        cn = by_code[("cn10y", "2026-11-25")]
        self.assertAlmostEqual(cn.value, 1.5914)
        self.assertIn("中国债券信息网", cn.source)
        # 美方 11-25 为空，应跳过并保留 11-24 的有效值。
        self.assertNotIn(("us10y", "2026-11-25"), by_code)
        us = by_code[("us10y", "2026-11-24")]
        self.assertAlmostEqual(us.value, 4.91)
        self.assertIn("美联储", us.source)

    def test_codes_filter_excludes_cpi(self) -> None:
        client = mock.Mock()
        client.get_json.return_value = _bond_payload()
        provider = EastmoneyMacroProvider(client=client)
        rows = provider.fetch_macro_indicators(["cn10y"])
        self.assertTrue(all(m.indicator_code == "cn10y" for m in rows))
        self.assertEqual(client.get_json.call_count, 1)

    def test_empty_payload_returns_no_rows(self) -> None:
        client = mock.Mock()
        client.get_json.return_value = {"result": None}
        provider = EastmoneyMacroProvider(client=client)
        self.assertEqual(provider.fetch_macro_indicators(["cn10y", "us10y"]), [])


def _memory_conn(rows: list[tuple[str, str, float, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE macro_indicators ("
        "indicator_code TEXT, indicator_name TEXT, trade_date TEXT, value REAL, source TEXT)"
    )
    conn.executemany(
        "INSERT INTO macro_indicators (indicator_code, indicator_name, trade_date, value, source) "
        "VALUES (?, ?, ?, ?, ?)",
        [("cn10y", "中国10Y", d, v, s) if c == "cn10y" else ("us10y", "美国10Y", d, v, s)
         for c, d, v, s in rows],
    )
    return conn


class TestResolveRiskFree(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_conn([
            ("cn10y", "2026-11-25", 1.5914, "中国债券信息网"),
            ("cn10y", "2026-11-24", 1.5853, "中国债券信息网"),
            ("us10y", "2026-11-24", 4.91, "美联储"),
        ])

    def tearDown(self) -> None:
        self.conn.close()

    def test_a_share_uses_china_10y(self) -> None:
        a = resolve_risk_free("000858.SZ", self.conn)
        self.assertEqual(a.rf_code, "cn10y")
        self.assertAlmostEqual(a.rf_pct, 1.5914)
        self.assertEqual(a.rf_date, "2026-11-25")
        self.assertFalse(a.currency_mismatch)
        self.assertFalse(a.missing)

    def test_hk_uses_us_10y_and_flags_mismatch(self) -> None:
        a = resolve_risk_free("00700.HK", self.conn)
        self.assertEqual(a.rf_code, "us10y")
        self.assertAlmostEqual(a.rf_pct, 4.91)
        self.assertEqual(a.rf_currency, "USD")
        self.assertEqual(a.cashflow_currency, "CNY")
        self.assertTrue(a.currency_mismatch)
        self.assertIn("币种错配", a.note)

    def test_us_listing_no_mismatch(self) -> None:
        a = resolve_risk_free("AAPL.US", self.conn)
        self.assertEqual(a.rf_code, "us10y")
        self.assertFalse(a.currency_mismatch)

    def test_debt_cost_and_floor_are_rf_plus_premium(self) -> None:
        a = resolve_risk_free("000858.SZ", self.conn)
        self.assertAlmostEqual(a.debt_cost_pct, 1.5914 + 1.5)
        self.assertAlmostEqual(a.discount_floor_pct, 1.5914 + 4.0)
        self.assertAlmostEqual(a.discount_floor, (1.5914 + 4.0) / 100, places=6)

    def test_missing_data_does_not_constrain(self) -> None:
        empty = _memory_conn([])
        try:
            a = resolve_risk_free("000858.SZ", empty)
            self.assertTrue(a.missing)
            self.assertIsNone(a.discount_floor)
            self.assertIn("未对 LLM 折现率施加利率下限", a.note)
        finally:
            empty.close()

    def test_note_lists_value_date_source(self) -> None:
        a = resolve_risk_free("000858.SZ", self.conn)
        for token in ("1.5914", "2026-11-25", "中国债券信息网", "债务成本", "下限"):
            self.assertIn(token, a.note)


class TestFcffDiscountFloor(unittest.TestCase):
    def setUp(self) -> None:
        self.common = dict(
            ocf=1000.0, shares=100.0, current_price=10.0,
            interest_expense=50.0, capex=200.0,
            growth=0.05, terminal_growth=0.02, years=10,
        )
        self.rf = {
            "rf_code": "us10y", "rf_pct": 4.91, "rf_rate": 0.0491,
            "rf_date": "2026-11-24", "rf_source": "美联储",
            "debt_cost_pct": 6.41, "discount_floor": 0.0891,
            "discount_floor_pct": 8.91, "note": "锚点说明",
            "currency_mismatch": True, "missing": False,
        }

    def test_llm_rate_above_floor_unchanged(self) -> None:
        result = evaluate_fcff(**self.common, discount=0.10, risk_free=self.rf)
        self.assertAlmostEqual(result.assumptions["discount"], 0.10)
        self.assertFalse(result.assumptions["discount_floor_applied"])
        self.assertEqual(result.assumptions["llm_discount"], 0.10)
        self.assertEqual(result.assumptions["risk_free"]["rf_code"], "us10y")

    def test_llm_rate_below_floor_is_raised(self) -> None:
        result = evaluate_fcff(**self.common, discount=0.075, risk_free=self.rf)
        self.assertAlmostEqual(result.assumptions["discount"], 0.0891)
        self.assertTrue(result.assumptions["discount_floor_applied"])
        self.assertAlmostEqual(result.assumptions["llm_discount"], 0.075)
        self.assertIn("已按下限执行", result.assumptions["wacc_note"])
        # 折现率被抬高，合理价应低于未约束的同一输入。
        unconstrained = evaluate_fcff(**self.common, discount=0.075)
        self.assertLess(result.fair_value_low, unconstrained.fair_value_low)

    def test_no_risk_free_keeps_original_behavior(self) -> None:
        result = evaluate_fcff(**self.common)
        self.assertNotIn("risk_free", result.assumptions)
        self.assertNotIn("discount_floor_applied", result.assumptions)


if __name__ == "__main__":
    unittest.main()
