"""原文取证型因子：触发权在代码，LLM 只给证据与建议。

重点锁住三件事：① 三项确定性条件（引文可定位 + 建议 + 风险语义标记）缺一不触发；
② 覆盖度三分与 checks_total 由注册表生成；③ 输出措辞不下定性结论。
"""

from __future__ import annotations

import dataclasses
import pathlib
import unittest

from fundamental.analysis import factor_registry as reg
from fundamental.analysis.forensic_patterns import scan_fraud_patterns
from fundamental.services.narrative import _report_mining as mining

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _hit(quote: str, suggested: bool = True, **kw) -> dict:
    return {
        "quote": quote,
        "suggested": suggested,
        "reason": "原文披露相关事项",
        "source": "2025 年报",
        "period": "2025",
        **kw,
    }


def _evidence(hits=None, *, available=True, rejected=0) -> dict:
    """模拟 services 层已逐字核验后的输出（analysis 只吃纯 dict）。"""
    return {
        s.id: {
            "available": available,
            "verified_hits": list(hits or []),
            "rejected_quotes": rejected,
            "documents": ["2025 年报"],
        }
        for s in reg.specs_by_kind("documentary")
    }


def _scan(factor_evidence=None) -> dict:
    return dataclasses.asdict(scan_fraud_patterns(
        symbol="TEST", market="A", financials=[], statement_items=[],
        factor_evidence=factor_evidence, lookback_periods=12,
    ))


def _ids(result: dict) -> list[str]:
    return [p["id"] for p in result["patterns"]]


class TestTriggerConfirmation(unittest.TestCase):
    def test_all_three_conditions_trigger(self):
        result = _scan(_evidence(
            [_hit("控股股东非经营性资金占用余额及清偿安排如下")]
        ))
        self.assertIn("FRD012", _ids(result))
        pattern = next(p for p in result["patterns"] if p["id"] == "FRD012")
        self.assertEqual(pattern["severity"], "medium")
        self.assertEqual(pattern["confidence"], "low")
        self.assertTrue(pattern["evidence"][0]["quote"])
        self.assertIn("判定方式", pattern["facts"])
        self.assertIn("RF-GV-01", pattern["facts"]["红旗编号"])
        # 措辞守铁律：只说待核查；出现的定性词只能是否定式免责声明
        self.assertIn("待核查线索", pattern["explanation"])
        self.assertIn("不代表存在", pattern["explanation"])
        self.assertNotIn("确认存在", pattern["explanation"])
        self.assertNotIn("认定为", pattern["explanation"])

    def test_quote_without_risk_marker_does_not_trigger(self):
        result = _scan(_evidence([_hit("公司本报告期经营情况正常")]))
        self.assertNotIn("FRD012", _ids(result))

    def test_suggestion_false_does_not_trigger(self):
        result = _scan(_evidence(
            [_hit("控股股东非经营性资金占用余额及清偿安排如下", suggested=False)]
        ))
        self.assertNotIn("FRD012", _ids(result))

    def test_unavailable_degrades_to_data_missing(self):
        result = _scan(_evidence(available=False))
        skipped = result["coverage"]["skipped"]
        doc_skips = [
            s for s in skipped
            if s["check"] in {x.check_name for x in reg.specs_by_kind("documentary")}
        ]
        self.assertEqual(len(doc_skips), 4)
        self.assertTrue(all(s["reason"] == "data_missing" for s in doc_skips))

    def test_checked_but_not_triggered_counts_as_performed(self):
        result = _scan(_evidence())  # 有可读原文但零命中
        performed = result["coverage"]["performed"]
        for s in reg.specs_by_kind("documentary"):
            self.assertIn(s.check_name, performed)
        self.assertGreaterEqual(result["coverage"]["checked_no_trigger"], 4)

    def test_no_factor_evidence_degrades_all_documentary(self):
        result = _scan(None)
        self.assertEqual(result["coverage"]["checks_total"], reg.checks_total())
        doc_names = {s.check_name for s in reg.specs_by_kind("documentary")}
        doc_skips = [
            s for s in result["coverage"]["skipped"] if s["check"] in doc_names
        ]
        self.assertEqual(len(doc_skips), 4)
        self.assertTrue(all(s["reason"] == "data_missing" for s in doc_skips))

    def test_rejected_quotes_are_accounted(self):
        result = _scan(_evidence(rejected=3))
        self.assertEqual(result["coverage"]["documentary_rejected_quotes"], 12)

    def test_coverage_exposes_registry_labels(self):
        result = _scan(_evidence())
        cov = result["coverage"]
        self.assertEqual(len(cov["factor_specs"]), reg.checks_total())
        self.assertEqual(cov["by_evidence_kind"]["quantitative"], 11)
        self.assertEqual(cov["by_evidence_kind"]["documentary"], 4)
        self.assertEqual(cov["by_evidence_kind"]["hybrid"], 0)


class TestNoFalsePositivesFromRoutineDisclosure(unittest.TestCase):
    """实跑回归：港股/银行的常规科目行不是资金占用线索。"""

    def test_routine_restricted_cash_and_pledge_lines_do_not_trigger(self):
        quotes = [
            "受限制現金 8,406 6,977 現金及現金等價物 231,717 141,041",
            "本集团下列资产作为附有卖出回购协议、拆入资金协议或向中央银行借款协议的负债的质押物",
            "㌻受限制現金 20(b) 4,138,063 4,578,635",
            "其他货币资金 1,178,996,681.77 银行承兑汇票保证金、其他保证金",
        ]
        for quote in quotes:
            result = _scan(_evidence([_hit(quote)]))
            triggered = [p["id"] for p in result["patterns"] if p["id"] >= "FRD012"]
            self.assertEqual(triggered, [], f"常规披露误触发：{quote}")

    def test_real_disclosures_still_trigger(self):
        finance_company = (
            "公司于2026年与财务公司签署《金融服务协议》之补充协议，"
            "约定公司在财务公司每日存款余额最高不超过人民币550亿元"
        )
        self.assertIn("FRD015", _ids(_scan(_evidence([_hit(finance_company)]))))
        accounting_change = (
            "基于谨慎性原则，调整2025年部分业务收入确认相关核算，"
            "具体详见《关于前期会计差错更正的公告》"
        )
        self.assertIn("FRD014", _ids(_scan(_evidence([_hit(accounting_change)]))))
        unfulfilled = "截至本报告期末，控股股东尚未履行实施股权激励计划的承诺"
        self.assertIn("FRD013", _ids(_scan(_evidence([_hit(unfulfilled)]))))

    def test_markers_exclude_routine_subject_terms(self):
        s15 = reg.spec_by_id("FRD015")
        self.assertIsNotNone(s15)
        for word in ("受限", "质押", "保证金"):
            self.assertNotIn(word, s15.trigger_markers)
        s13 = reg.spec_by_id("FRD013")
        self.assertNotIn("承诺", s13.trigger_markers)


class TestQuoteVerification(unittest.TestCase):
    def test_exact_quote_with_whitespace_differences_passes(self):
        raw = "第一章  控股股东\n非经营性资金占用余额及清偿安排如下：\n单位：元"
        self.assertTrue(
            mining._quote_verified("控股股东非经营性资金占用余额及清偿安排如下", raw)
        )

    def test_rewritten_or_hallucinated_quote_is_rejected(self):
        raw = "公司不存在资金占用情况。"
        self.assertFalse(mining._quote_verified("控股股东非经营性资金占用余额 12 亿元", raw))

    def test_short_or_empty_quote_is_rejected(self):
        self.assertFalse(mining._quote_verified("占用", "占用"))
        self.assertFalse(mining._quote_verified("", "任意原文"))
        self.assertFalse(mining._quote_verified("一段原文", ""))

    def test_as_bool_only_accepts_explicit_affirmation(self):
        for value in (True, "true", "1", "yes", "是", "Y"):
            self.assertTrue(mining._as_bool(value))
        for value in (False, "false", "0", "no", "否", None, "", 2):
            self.assertFalse(mining._as_bool(value))

    def test_fingerprint_includes_factor_briefs(self):
        docs = [("p.txt", "原文内容")]
        plain = mining._fingerprint(docs, [], None)
        with_probes = mining._fingerprint(docs, [], [{"factor_id": "FRD012"}])
        self.assertNotEqual(plain, with_probes)
        self.assertEqual(plain, mining._fingerprint(docs, [], None))

    def test_mining_gate_allows_rule_driven_read_without_signals(self):
        src = (ROOT / "fundamental" / "services" / "narrative" /
               "_report_mining.py").read_text(encoding="utf-8")
        self.assertIn("if (not signals and not factor_briefs)", src)
        self.assertIn('"factor_probes": factor_briefs or []', src)


if __name__ == "__main__":
    unittest.main()
