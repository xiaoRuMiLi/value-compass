"""审计视角事实抽取 + 例行合规披露降噪的回归测试。

前者验证「财报原文里的硬事实确实被抽出来、且抽不到时不猜」，
后者验证「年度资金占用专项说明/独董声明与承诺」这类例行文件不会再被
计成 high 风险写进结论（实测五粮液 18 条里 17 条属于此类）。
"""

from __future__ import annotations

import unittest
from typing import Any

from fundamental.analysis import forensics as fx
from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.models import IntegrityCategory, IntegrityRecord, Severity
from fundamental.services.narrative import _audit_evidence as ae

_SAMPLE = """
第八节 财务报告
一、审计报告
审计意见类型
标准的无保留意见
审计报告签署日期
2026 年04 月28 日
审计机构名称
天职国际会计师事务所（特殊普通合伙）
三、关键审计事项
（一）货币资金的存在性和完整性
截至2025 年12 月31 日，货币资金余额为人民币12,701,444.30 万元，占资产总额66.86%。
（二）营业收入的确认
2025 年度，营业收入发生额为人民币4,052,850.98 万元。
四、其他信息
上述财务指标或其加总数是否与公司已披露季度报告、半年度报告相关财务指标存在重大差异
☑是 □否
公司对2025 年业务模式进行梳理，基于谨慎性原则，调整2025 年部分业务收入确认相关核算。
六、与上年度财务报告相比，会计政策、会计估计变更或重大会计差错更正的情况说明
□适用 ☑不适用
公司报告期无会计政策、会计估计变更或重大会计差错更正的情况。
5、与存在关联关系的财务公司的往来情况
财务公司 联营企业 5,500,000 0.3%～3.5% 4,714,637.03 2,812,514.91 2,729,427.42 4,797,724.52
六、资产及负债状况分析
货币资金 127,014,443,016.86 66.86% 127,398,915,484.11 67.67%
截至报告期末的资产权利受限情况
合计 5,241,457,972.53
累计实施现金分红223.08 亿元，以真金白银回报广大投资者。
归属于上市公司股东的
净利润（元）
8,954,257,202.51
内控审计报告意见类型
标准无保留意见
财务报告重大缺陷数量（个）
0
本报告期内，公司及其控股股东、实际控制人诚信状况良好。
公司报告期不存在控股股东及其他关联方对上市公司的非经营性占用资金。
公司报告期无违规对外担保情况。
本报告期公司无重大诉讼、仲裁事项。
连续11 年荣获深圳证券交易所信息披露考核最高等级A 级。
"""


class AuditEvidenceExtractionTests(unittest.TestCase):
    def test_audit_opinion_and_key_audit_matters(self):
        opinion = ae._audit_opinion(_SAMPLE, "2025", "2025 年报")
        self.assertEqual(len(opinion), 1)
        self.assertEqual(opinion[0]["opinion"], "标准的无保留意见")
        self.assertFalse(opinion[0]["non_standard"])
        kam = ae._key_audit_matters(_SAMPLE, "2025", "2025 年报")
        self.assertEqual(kam[0]["count"], 2)
        self.assertIn("货币资金的存在性和完整性", [x["title"] for x in kam[0]["items"]])
        self.assertIn("营业收入的确认", [x["title"] for x in kam[0]["items"]])

    def test_quarterly_discrepancy_and_accounting_change(self):
        qd = ae._quarterly_discrepancy(_SAMPLE, "2025", "2025 年报")
        self.assertTrue(qd[0]["applicable"])
        self.assertIn("收入确认", qd[0]["reason"])
        ac = ae._accounting_change(_SAMPLE, "2025", "2025 年报")
        self.assertFalse(ac[0]["applicable"])

    def test_finance_company_deposit_share(self):
        rows = ae._finance_company_deposit(_SAMPLE, "2025", "2025 年报")
        self.assertEqual(rows[0]["balance_yi"], 479.77)
        self.assertEqual(rows[0]["cash_yi"], 1270.14)
        self.assertAlmostEqual(rows[0]["share_of_cash_pct"], 37.77, places=1)
        self.assertEqual(rows[0]["limit_yi"], 550.0)

    def test_dividend_coverage_ignores_payable_dividend(self):
        rows = ae._dividend_coverage(_SAMPLE, "2025", "2025 年报")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["payout_ratio_pct"], 249.13, delta=0.1)
        # 只有「应付普通股股利」不是明确派现口径，必须留空而不是算错
        self.assertEqual(
            ae._dividend_coverage("应付普通股股利 10,006,785,436.88", "2026", "2026 中报"),
            [],
        )

    def test_counter_evidence_and_internal_control(self):
        counter = ae._counter_evidence(_SAMPLE, "2025", "2025 年报")
        labels = {x["item"] for x in counter}
        self.assertIn("诚信状况良好", labels)
        self.assertIn("无非经营性资金占用", labels)
        self.assertIn("无违规对外担保", labels)
        ic = ae._internal_control(_SAMPLE, "2025", "2025 年报")
        self.assertEqual(ic[0]["major_defects"], 0)

    def test_alerts_respect_configured_thresholds(self):
        result: dict[str, Any] = {k: [] for k, _ in ae._EXTRACTORS}
        result["quarterly_discrepancy"] = ae._quarterly_discrepancy(_SAMPLE, "2025", "2025 年报")
        result["audit_opinion"] = ae._audit_opinion(_SAMPLE, "2025", "2025 年报")
        result["key_audit_matters"] = ae._key_audit_matters(_SAMPLE, "2025", "2025 年报")
        result["finance_company_deposit"] = ae._finance_company_deposit(_SAMPLE, "2025", "2025 年报")
        result["dividend_coverage"] = ae._dividend_coverage(_SAMPLE, "2025", "2025 年报")
        strict = {"deposit_share_pct": 90.0, "payout_alert_pct": 500.0, "capacity_util_pct": 0.0}
        loose = {"deposit_share_pct": 20.0, "payout_alert_pct": 100.0, "capacity_util_pct": 70.0}
        strict_titles = {a["title"] for a in ae._alerts(result, strict)}
        loose_titles = {a["title"] for a in ae._alerts(result, loose)}
        self.assertIn("年报分季度数据与已披露季报/半年报存在重大差异", strict_titles)
        self.assertNotIn("大额资金存放于关联财务公司", strict_titles)
        self.assertIn("大额资金存放于关联财务公司", loose_titles)
        self.assertIn("现金分红超出当年归母净利", loose_titles)

    def test_no_source_docs_yields_missing_not_exception(self):
        # 用不存在的股票代码，磁盘上没有归档：返回空结构 + missing，不抛异常
        out = ae.extract_audit_evidence("999999.SH")
        self.assertIsInstance(out["alerts"], list)
        self.assertEqual(out["alerts"], [])
        self.assertTrue(out["missing"])


class RoutineDisclosureTests(unittest.TestCase):
    @staticmethod
    def _record(title: str, category: IntegrityCategory, severity: Severity) -> IntegrityRecord:
        return IntegrityRecord(
            record_date="2026-04-30", category=category, severity=severity,
            title=title, description="", source="cninfo", source_url="",
        )

    def test_routine_disclosures_are_excluded_from_high_severity(self):
        records = [
            self._record("2025年度关联方资金占用专项审计报告", IntegrityCategory.FUND_OCCUPATION, Severity.HIGH),
            self._record("2026年半年度非经营性资金占用及其他关联资金往来情况汇总表",
                         IntegrityCategory.FUND_OCCUPATION, Severity.HIGH),
            self._record("独立董事候选人声明与承诺（丁南）", IntegrityCategory.BROKEN_PROMISE, Severity.MEDIUM),
            self._record("关于公司持股5%以上股东自愿承诺不减持的公告",
                         IntegrityCategory.BROKEN_PROMISE, Severity.MEDIUM),
            self._record("关于前期会计差错更正的公告",
                         IntegrityCategory.FINANCIAL_RESTATEMENT, Severity.MEDIUM),
        ]
        signals: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {"performed": [], "skipped": []}
        result = fx._scan_regulatory(records, [], signals, coverage, ForensicsConfig())
        reg = result["regulatory"]
        self.assertEqual(sum(1 for r in reg if r["routine"]), 4)
        self.assertEqual(sum(1 for r in reg if not r["routine"]), 1)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["severity"], "medium")
        facts = {f["label"]: f["value"] for f in signals[0]["facts"]}
        self.assertEqual(facts["实质性记录条数"], "1 条")
        self.assertEqual(facts["高严重度"], "0 条")
        self.assertEqual(facts["例行合规披露（已剔除）"], "4 条")

    def test_only_routine_records_produce_no_signal(self):
        records = [
            self._record("年度关联方资金占用专项审计报告", IntegrityCategory.FUND_OCCUPATION, Severity.HIGH),
        ]
        signals: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {"performed": [], "skipped": []}
        fx._scan_regulatory(records, [], signals, coverage, ForensicsConfig())
        self.assertEqual(signals, [])
        self.assertTrue(any("例行合规披露" in s["reason"] for s in coverage["skipped"]))

    def test_announcement_keyword_hit_on_routine_form_is_excluded(self):
        class Ann:
            title = "2026年半年度非经营性资金占用及其他关联资金往来情况汇总表"
            publish_date = "2026-08-29"
            source = "cninfo"
            source_url = ""
            pdf_url = ""

        signals: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {"performed": [], "skipped": []}
        result = fx._scan_regulatory([], [Ann()], signals, coverage, ForensicsConfig())
        self.assertTrue(result["announcement_hits"][0]["routine"])
        self.assertFalse(any(s["id"] == "REG002" for s in signals))

    def test_routine_patterns_are_configurable(self):
        cfg = ForensicsConfig(regulatory_routine_patterns=("^自定义例行文件$",))
        records = [
            self._record("2025年度关联方资金占用专项审计报告", IntegrityCategory.FUND_OCCUPATION, Severity.HIGH),
        ]
        signals: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {"performed": [], "skipped": []}
        result = fx._scan_regulatory(records, [], signals, coverage, cfg)
        # 覆盖配置后，原来的例行标题不再被识别，会按实质性记录出信号
        self.assertFalse(result["regulatory"][0]["routine"])
        self.assertEqual(signals[0]["severity"], "high")


if __name__ == "__main__":
    unittest.main()
