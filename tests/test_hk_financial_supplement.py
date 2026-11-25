"""港股财务补充数据回归测试：FCF 抽取 / 港股分红 / 回购注销 / DDM 口径合成。

覆盖：
1. 从财报原文抽取自由现金流，排除「若剔除…」调整口径；
2. 港股分红 provider 只取现金分红、标 HKD，排除实物分派；
3. 股东回报每股 = 现金分红 + 回购注销，按汇率换算；
4. 分析层口径注入不改变 A 股原逻辑。
"""

from __future__ import annotations

import unittest
from unittest import mock

from fundamental.models import DividendRecord, Market
from fundamental.providers.dividend import eastmoney as div_mod
from fundamental.services import hk_financial_supplement as hk


def _doc(period: str, doc_type: str, text: str) -> dict:
    return {"period": period, "doc_type": doc_type, "title": f"{doc_type} {period}", "text": text, "path": "/tmp/x.txt"}


class FcfExtractionTests(unittest.TestCase):
    def test_excludes_adjusted_fcf_phrase(self) -> None:
        """「若剔除算力预付款，自由现金流376亿」是调整口径，不能被当成标准季度值。"""
        docs = [
            _doc("2026", "semi_report",
                 "若剔 除算力採購的預付款項，我們的自由現金流為人民幣376 億元。"),
            _doc("2026", "q1_report",
                 "二零二六年第一季的自由現金流為人民幣567 億元，反映經營活動現金淨額。"),
            _doc("2025", "annual_report",
                 "二零二五年第四季的自由現金流為人民幣340 億元。"),
        ]
        with mock.patch.object(hk, "_load_report_texts", return_value=docs):
            result = hk.extract_fcf_ttm("00700.HK")
        self.assertTrue(result["available"])
        values = {q["quarter"]: q["fcf_yi"] for q in result["quarters"]}
        self.assertEqual(values.get(1), 567.0)
        self.assertEqual(values.get(4), 340.0)
        # 调整口径的 376 不应出现
        self.assertNotIn(376.0, values.values())

    def test_no_text_returns_unavailable(self) -> None:
        with mock.patch.object(hk, "_load_report_texts", return_value=[]):
            result = hk.extract_fcf_ttm("00700.HK")
        self.assertFalse(result["available"])
        self.assertIn("reason", result)


class BuybackExtractionTests(unittest.TestCase):
    def test_extracts_amount_shares_and_cancelled(self) -> None:
        text = (
            "截至二零二五年十二月三十一日止年度內，本公司於聯交所以總代價約800 億港元"
            "（未計開支）購回合共153,415,000 股股份。購回的股份其後已被註銷。"
        )
        doc = _doc("2025", "annual_report", text)
        with mock.patch.object(hk, "_load_report_texts", return_value=[doc]):
            result = hk.extract_buyback_cancellation("00700.HK")
        self.assertTrue(result["available"])
        self.assertEqual(result["amount_hkd_yi"], 800.0)
        self.assertEqual(result["shares"], 153415000)
        self.assertTrue(result["cancelled"])


class HkDividendProviderTests(unittest.TestCase):
    def test_parses_cash_dividend_and_marks_hkd(self) -> None:
        rows = [
            {"IS_DIVIDEND": "1", "PLAN_EXPLAIN": "每股派港币5.3元",
             "EX_DIVIDEND_DATE": "2026-05-15 00:00:00", "NOTICE_DATE": "2026-05-13 00:00:00"},
            {"IS_DIVIDEND": "1", "PLAN_EXPLAIN": "特殊说明:每10股分派1股美团B类普通股股份",
             "EX_DIVIDEND_DATE": "2023-01-05 00:00:00", "NOTICE_DATE": "2022-11-16 00:00:00"},
        ]
        provider = div_mod.EastmoneyDividendProvider()
        with mock.patch.object(provider.client, "get_json", return_value={"result": {"data": rows}}):
            result = provider.fetch_dividends("00700.HK")
        # 只取到 1 条现金分红，实物分派被排除
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cash_per_share, 5.3)
        self.assertEqual(result[0].currency, "HKD")


class ShareholderReturnTests(unittest.TestCase):
    def test_combines_dividend_and_buyback_with_fx(self) -> None:
        divs = [DividendRecord(ex_date="2026-05-15", cash_per_share=5.3, currency="HKD")]
        shares = 100e8
        bb = {"available": True, "amount_hkd_yi": 800.0, "period": "2025"}
        with mock.patch.object(hk, "extract_buyback_cancellation", return_value=bb):
            result = hk.total_shareholder_return_per_share(
                "00700.HK", divs, shares, fx_rate=0.85
            )
        cash = 5.3 * 0.85                       # 4.505
        buyback = 800e8 * 0.85 / 100e8         # 6.8
        self.assertAlmostEqual(result["cash_dividend_per_share_cny"], round(cash, 4))
        self.assertAlmostEqual(result["buyback_per_share_cny"], round(buyback, 4))
        self.assertAlmostEqual(result["total_per_share_cny"], round(cash + buyback, 4))


if __name__ == "__main__":
    unittest.main()
