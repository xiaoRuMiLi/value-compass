"""概览叙述单元测试：分段渲染、模板回退、本地缓存与提示词加载。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fundamental.services.narrative as narrative
import fundamental.services.narrative._common as _common
import fundamental.services.narrative._masters as _masters
import fundamental.services.narrative._overview as _overview
import fundamental.services.narrative._score as _score
import fundamental.services.narrative._valuation as _valuation
from fundamental.models import (
    ChatResult,
    CompositeScore,
    DimensionScore,
    Market,
    MasterVerdict,
    Stock,
    ValuationResult,
)
from fundamental.services.narrative._data import _year_metric, _year_summary
from fundamental.services.narrative._overview import _render_template
from fundamental.services.narrative._score import _score_facts, _score_template
from fundamental.services.narrative._valuation import _valuation_facts, _valuation_template
from fundamental.utils.prompts import load_prompt


def _make_facts() -> dict:
    return {
        "name": "测试股份",
        "industry": "白酒",
        "business": None,
        "score_total": 65.6,
        "score_rating": "观望",
        "dimensions": [{"name": "盈利能力", "score": 100.0, "weight": 25}],
        "moat": {"rating": "宽", "evidence": ["毛利率均值 89.2%"]},
        "cycle": {"position": "下行", "evidence": ["近 3 年净利同比: -4.5%"]},
        "fraud": {"score": 50.0, "signals": ["增收不增利"]},
        "delisting": {"score": 0.0, "triggers": []},
        "valuation": [
            {"method": "dcf", "fair_value_low": 100.0, "fair_value_high": 200.0, "margin_of_safety": 0.1}
        ],
        "review_summary": "",
        "integrity_summary": "诚信记录 0 条",
        "red_flags": [],
        "masters": [{"master": "buffett", "verdict": "持有"}],
        "latest_financials": {
            "report_date": "2026-09-30",
            "revenue": 100e8,
            "net_profit": 20e8,
            "roe": 20.0,
            "gross_margin": 60.0,
            "net_margin": 30.0,
            "revenue_yoy": 5.0,
            "net_profit_yoy": 3.0,
            "debt_ratio": 30.0,
        },
        "revenue_region": [{"item": "国内", "ratio": 90.0}],
        "revenue_products": [{"item": "白酒", "ratio": 80.0}],
        "insider_trades": [{"direction": "buy"}],
        "pledge_count": 0,
        "integrity_records": [],
        "announcements": [],
        "litigations": [],
        "sentiment": None,
        "research_reports": [],
        "capital_flows": [],
    }


class TestRenderTemplate(unittest.TestCase):
    def test_renders_ordered_sections(self):
        sections = _render_template(_make_facts())
        self.assertEqual(
            [s["title"] for s in sections],
            ["企业基本信息", "护城河", "经营与周期", "估值与安全边际", "风险与治理"],
        )
        joined = "".join(s["content"] for s in sections)
        self.assertIn("测试股份", joined)
        self.assertIn("现金流折现", joined)
        self.assertIn("安全边际 10.00%", joined)
        self.assertIn("增持 1 条", joined)


class TestGenerateOverviewNarrative(unittest.TestCase):
    def _result(self):
        return {
            "stock": Stock(code="600519", market=Market.SH, name="贵州茅台"),
            "snapshot": None,
            "score": None,
            "moat": None,
            "cycle": None,
            "fraud": None,
            "delisting": None,
            "valuation": [],
            "review": None,
            "red_flags": [],
            "masters": [],
        }

    def test_template_fallback_and_cache(self):
        fake_analyze = mock.MagicMock(return_value=self._result())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_overview, "analyze_stock", fake_analyze), mock.patch.object(
            _overview, "_load_extra", return_value={"latest_financials": None}
        ), mock.patch.object(_overview, "has_live_llm", return_value=False):
            first = narrative.generate_overview_narrative("600519.SH")
            second = narrative.generate_overview_narrative("600519.SH")
            third = narrative.generate_overview_narrative("600519.SH", refresh=True)

        self.assertEqual(first["source"], "template")
        self.assertGreaterEqual(len(first["sections"]), 5)
        self.assertEqual(second["generated_at"], first["generated_at"])
        self.assertEqual(fake_analyze.call_count, 2)


class TestScoreNarrative(unittest.TestCase):
    def _score(self):
        return CompositeScore(
            total=65.6,
            rating="观望",
            dimensions=[
                DimensionScore("盈利能力", 97.1, 25, "净利率 50.94% → 30.0/30"),
                DimensionScore("成长能力", 9.0, 25, "营收同比 1.82%"),
            ],
            red_flags=[],
        )

    def test_score_template(self):
        text = _score_template(_score_facts(self._score()))
        self.assertIn("观望", text)
        self.assertIn("盈利能力", text)
        self.assertIn("成长能力", text)

    def test_strongest_by_score_not_score_over_weight(self):
        # 得分最高的维度应被判为最强，而非 score/weight 比值最大的
        # 例：盈利能力 80/25=3.2、股东回报 96/15=6.4，但最强应是得分 96 的股东回报
        score = CompositeScore(
            total=70.0,
            rating="推荐",
            dimensions=[
                DimensionScore("盈利能力", 80.0, 25, ""),
                DimensionScore("股东回报", 96.0, 15, ""),
                DimensionScore("成长能力", 20.0, 25, ""),
            ],
            red_flags=[],
        )
        text = _score_template(_score_facts(score))
        self.assertIn("最强维度是股东回报", text)
        self.assertIn("最弱维度是成长能力", text)
        self.assertIn("96 分，权重 15%", text)
        self.assertNotIn("96/15", text)

    def test_generate_score_narrative_and_cache(self):
        fake_analyze = mock.MagicMock(return_value={"score": self._score()})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_score, "analyze_stock", fake_analyze), mock.patch.object(
            _score, "_load_score_financials", return_value={}
        ), mock.patch.object(
            _score, "has_live_llm", return_value=False
        ):
            first = narrative.generate_score_narrative("600519.SH")
            second = narrative.generate_score_narrative("600519.SH")
            third = narrative.generate_score_narrative("600519.SH", refresh=True)

        self.assertEqual(first["source"], "template")
        self.assertIn("观望", first["text"])
        self.assertEqual(second["generated_at"], first["generated_at"])
        self.assertEqual(fake_analyze.call_count, 2)


class TestValuationNarrative(unittest.TestCase):
    def _result(self):
        return {
            "stock": Stock("600519", Market.SH, "贵州茅台"),
            "snapshot": None,
            "valuation": [
                ValuationResult(
                    "relative", 100.0, 120.0, 110.0, 0.05,
                    assumptions={"formula": "历史PE中位数×EPS"}, inputs={"eps": 10},
                ),
                ValuationResult(
                    "dcf", 90.0, 130.0, 110.0, 0.1,
                    assumptions={"formula": "两阶段FCFE", "growth": 0.1, "discount": 0.09},
                    inputs={"fcf": 1e9, "shares": 1e8},
                ),
            ],
        }

    def test_valuation_template(self):
        facts = _valuation_facts(self._result(), {})
        sections = _valuation_template(facts)
        titles = [section["title"] for section in sections]
        self.assertIn("相对估值", titles)
        self.assertIn("现金流折现", titles)
        self.assertIn("综合判断", titles)
        joined = "".join(section["content"] for section in sections)
        self.assertIn("历史PE中位数×EPS", joined)

    def test_generate_valuation_narrative_and_cache(self):
        fake_analyze = mock.MagicMock(return_value=self._result())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_valuation, "analyze_stock", fake_analyze), mock.patch.object(
            _valuation, "_load_score_financials", return_value={}
        ), mock.patch.object(_valuation, "has_live_llm", return_value=False):
            first = narrative.generate_valuation_narrative("600519.SH")
            second = narrative.generate_valuation_narrative("600519.SH")
            third = narrative.generate_valuation_narrative("600519.SH", refresh=True)

        self.assertEqual(first["source"], "template")
        self.assertGreaterEqual(len(first["sections"]), 2)
        self.assertIn("相对估值", [section["title"] for section in first["sections"]])
        self.assertEqual(second["generated_at"], first["generated_at"])
        self.assertEqual(fake_analyze.call_count, 2)


class TestMastersNarrative(unittest.TestCase):
    def _verdict(self) -> MasterVerdict:
        return MasterVerdict(
            symbol="000858.SZ",
            master="buffett",
            score=50.0,
            verdict="回避",
            signals=[
                {"name": "ROE 连续 3 年 ≥ 15%", "passed": False, "detail": "6.89%"},
                {"name": "毛利率 ≥ 30%", "passed": True, "detail": "77.54%"},
            ],
            narrative="旧模板",
        )

    def _result(self) -> dict:
        return {
            "stock": Stock("000858", Market.SZ, "五粮液"),
            "snapshot": None,
            "score": None,
            "valuation": [],
            "moat": None,
            "cycle": None,
            "red_flags": [],
            "review": None,
            "masters": [self._verdict()],
        }

    def _financials(self) -> dict:
        return {
            "years": [
                {
                    "year": "2025",
                    "annual": None,
                    "quarterly": [],
                    "summary": "2025 年报：营收同比 -54.55%。",
                }
            ]
        }

    def _context(self) -> dict:
        return {
            "company_basics": {
                "name": "五粮液",
                "industry": "酿酒行业",
                "sector": "白酒",
                "business": "白酒生产与销售",
                "profile_summary": "五粮液：行业“酿酒行业”、赛道“白酒”；主营业务：白酒生产与销售。",
                "revenue_mix": [
                    {
                        "dimension": "product",
                        "report_date": "2026-06-30",
                        "items": [{"item": "酒类产品", "ratio_pct": 94.55}],
                    }
                ],
            },
            "industry": {"summary": "匹配板块“白酒Ⅱ”，板块 PE 22.57 倍。"},
            "peer_comparison": {"summary": "同行业最近年报 ROE 中位数 19.71%，该公司 6.89%。"},
            "latest_report": {
                "summary": "2026-09-30 三季报：营收同比 +7.25%；经营现金流/净利 0.5%。"
            },
            "market_valuation": {"summary": "PE(TTM) 23.36 倍，PB 2.4 倍。"},
        }

    def test_year_summary_has_annual_and_quarter(self):
        row = {
            "report_date": "2025-03-31",
            "report_type": "Q1",
            "revenue": 100e8,
            "net_profit": 20e8,
            "net_profit_deducted": 19e8,
            "gross_margin": 70.0,
            "net_margin": 20.0,
            "roe": 5.0,
            "debt_ratio": 30.0,
            "revenue_yoy": -10.0,
            "net_profit_yoy": -15.0,
            "ocf": None,
            "audit_opinion": None,
        }
        metric = _year_metric(row, {})
        summary = _year_summary(
            {"year": "2025", "annual": metric, "quarterly": [metric]}
        )
        self.assertIn("2025 年报", summary)
        self.assertIn("一季报", summary)
        self.assertIn("-10.0%", summary)

    def test_template_keeps_verdict_and_signals(self):
        facts = _masters._master_facts(
            self._result(), self._financials(), self._verdict(), self._context()
        )
        text = _masters._master_template(facts)
        self.assertIn("巴菲特", text)
        self.assertIn("回避", text)
        self.assertIn("ROE 连续 3 年 ≥ 15%", text)
        self.assertIn("建议", text)
        self.assertNotIn("公式", text)

    def test_facts_include_business_industry_and_latest_report(self):
        facts = _masters._master_facts(
            self._result(), self._financials(), self._verdict(), self._context()
        )
        self.assertEqual(facts["company"]["business"], "白酒生产与销售")
        self.assertIn("酒类产品", str(facts["company"]["revenue_mix"]))
        self.assertIn("板块 PE", facts["industry"]["summary"])
        self.assertIn("中位数", facts["peer_comparison"]["summary"])
        self.assertIn("三季报", facts["latest_report"]["summary"])
        self.assertIn("PE(TTM)", facts["market_valuation"]["summary"])

    def test_template_uses_latest_report_and_peer_comparison(self):
        facts = _masters._master_facts(
            self._result(), self._financials(), self._verdict(), self._context()
        )
        text = _masters._master_template(facts)
        self.assertIn("2026-09-30 三季报", text)
        self.assertIn("中位数", text)
        self.assertIn("PE(TTM)", text)

    def test_year_summary_without_annual_says_not_disclosed(self):
        row = {
            "report_date": "2026-09-30",
            "report_type": "Q3",
            "revenue": 100e8,
            "net_profit": 20e8,
            "net_profit_deducted": 19e8,
            "gross_margin": 70.0,
            "net_margin": 20.0,
            "roe": 5.0,
            "debt_ratio": 30.0,
            "revenue_yoy": 7.25,
            "net_profit_yoy": 49.98,
            "ocf": None,
            "audit_opinion": None,
        }
        summary = _year_summary(
            {"year": "2026", "annual": None, "quarterly": [_year_metric(row, {})]}
        )
        self.assertIn("年报尚未披露", summary)
        self.assertNotIn("年报[数据缺失]", summary)

    def test_system_prompt_includes_master_profile(self):
        system = _masters._system_prompt("buffett")
        self.assertIn("量化结论", system)
        self.assertIn("长期持有者", system)
        self.assertIn("巴菲特", system)

    def test_generate_masters_narrative_and_cache(self):
        fake_analyze = mock.MagicMock(return_value=self._result())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_masters, "analyze_stock", fake_analyze), mock.patch.object(
            _masters, "_load_master_financials", return_value=self._financials()
        ), mock.patch.object(_masters, "_load_master_context", return_value=self._context()
        ), mock.patch.object(_masters, "has_live_llm", return_value=False):
            first = narrative.generate_masters_narrative("000858.SZ")
            second = narrative.generate_masters_narrative("000858.SZ")
            third = narrative.generate_masters_narrative("000858.SZ", refresh=True)

        self.assertEqual(first["source"], "template")
        self.assertEqual(len(first["masters"]), 1)
        self.assertIn("回避", first["masters"][0]["text"])
        self.assertEqual(second["generated_at"], first["generated_at"])
        self.assertEqual(third["masters"][0]["master"], "buffett")
        self.assertEqual(fake_analyze.call_count, 2)

    def test_llm_text_replaces_template(self):
        llm_text = "从巴菲特视角看，护城河仍在，但量化结论维持回避，建议继续验证。"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_masters, "analyze_stock", return_value=self._result()), mock.patch.object(
            _masters, "_load_master_financials", return_value=self._financials()
        ), mock.patch.object(_masters, "_load_master_context", return_value=self._context()
        ), mock.patch.object(_masters, "has_live_llm", return_value=True), mock.patch.object(
            _masters, "chat", return_value=ChatResult(content=f"  {llm_text}  ")
        ) as fake_chat:
            output = narrative.generate_masters_narrative("000858.SZ")

        self.assertEqual(output["source"], "llm")
        self.assertEqual(output["masters"][0]["text"], llm_text)
        self.assertEqual(fake_chat.call_count, 1)

    def test_old_masters_version_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            _common.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(_masters, "analyze_stock", return_value=self._result()), mock.patch.object(
            _masters, "_load_master_financials", return_value=self._financials()
        ), mock.patch.object(_masters, "_load_master_context", return_value=self._context()), mock.patch.object(
            _masters, "has_live_llm", return_value=False
        ):
            first = narrative.generate_masters_narrative("000858.SZ")
            cache_path = _common._masters_cache_path("000858.SZ")
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["masters_version"] = _common._MASTERS_PROMPT_VERSION - 1
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            second = narrative.generate_masters_narrative("000858.SZ")

        self.assertEqual(second["masters_version"], _common._MASTERS_PROMPT_VERSION)
        self.assertNotEqual(second["generated_at"], first["generated_at"])


class TestPrompt(unittest.TestCase):
    def test_load_overview_prompt(self):
        text = load_prompt("overview_narrative")
        self.assertIn("护城河", text)
        self.assertIn("sections", text)

    def test_master_prompts_cover_full_framework(self):
        buffett = load_prompt("masters/buffett")
        for keyword in ("股东盈余", "护城河", "资本配置", "一美元", "长期持有者"):
            self.assertIn(keyword, buffett)
        duan = load_prompt("masters/duan_yongping")
        for keyword in ("生意模式", "本分", "能力圈", "经营现金流"):
            self.assertIn(keyword, duan)
        base = load_prompt("masters/_base")
        for keyword in ("peer_comparison", "latest_report", "market_valuation"):
            self.assertIn(keyword, base)


if __name__ == "__main__":
    unittest.main()
