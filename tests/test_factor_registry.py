"""因子注册表契约测试：单一事实来源、红旗编号可解析、任务单不泄漏触发标记。"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

from fundamental.analysis import factor_registry as reg

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestRegistryContract(unittest.TestCase):
    def test_validate_registry_clean(self):
        self.assertEqual(reg.validate_registry(), [])

    def test_ids_unique_and_kind_counts(self):
        ids = [s.id for s in reg.all_specs()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(reg.specs_by_kind("quantitative")), 11)
        self.assertEqual(len(reg.specs_by_kind("documentary")), 4)
        self.assertEqual(reg.checks_total(), len(ids))
        self.assertGreaterEqual(reg.checks_total(), 15)

    def test_every_spec_has_handler_or_probe(self):
        self.assertEqual(
            [s.id for s in reg.specs_by_kind("quantitative") if not s.handler], []
        )
        for s in reg.specs_by_kind("documentary"):
            self.assertTrue(s.probe_criteria)
            self.assertTrue(s.trigger_markers)
            self.assertTrue(s.source_scope)

    def test_red_flag_refs_resolve_in_document(self):
        text = (ROOT / "references" / "red-flags.md").read_text(encoding="utf-8")
        doc_ids = set(re.findall(r"`(RF-[A-Z]+-\d+)`", text))
        self.assertTrue(doc_ids, "红旗清单必须带 RF- 稳定编号")
        for s in reg.all_specs():
            self.assertTrue(s.red_flag_refs, f"{s.id} 未映射红旗")
            for ref in s.red_flag_refs:
                self.assertIn(ref, doc_ids, f"{s.id} 指向不存在的红旗 {ref}")

    def test_documentary_briefs_do_not_leak_trigger_markers(self):
        briefs = reg.documentary_briefs()
        self.assertEqual(len(briefs), 4)
        blob = json.dumps(briefs, ensure_ascii=False)
        self.assertNotIn("trigger_markers", blob)
        for b in briefs:
            self.assertTrue(b["factor_id"])
            self.assertTrue(b["probe_criteria"])
            self.assertTrue(b["source_scope"])
            self.assertTrue(b["red_flag_refs"])

    def test_factor_labels_cover_all_specs(self):
        labels = reg.factor_labels()
        self.assertEqual(len(labels), reg.checks_total())
        self.assertEqual(
            {x["id"] for x in labels}, {s.id for s in reg.all_specs()}
        )
        for label in labels:
            self.assertIn(
                label["evidence_kind"], ("quantitative", "documentary", "hybrid")
            )
            self.assertTrue(label["red_flag_refs"])

    def test_registry_is_pure_metadata(self):
        src = (ROOT / "fundamental" / "analysis" / "factor_registry.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("fundamental.services", src)
        self.assertNotIn("fundamental.providers", src)
        self.assertNotIn("chat_json", src)
        # 注册表不得含判定函数或匿名函数（docstring 里提到 lambda 不算）
        self.assertNotIn("def _check_", src)
        self.assertIsNone(re.search(r"=\s*lambda\b|\blambda\s+\w+\s*:", src))

    def test_scanner_has_no_handwritten_check_lambdas(self):
        src = (ROOT / "fundamental" / "analysis" / "forensic_patterns.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("checks: list[tuple[str, Any]]", src)
        self.assertIn("for spec in FACTOR_REGISTRY", src)
        # 降级兜底也不再写死 11
        forensics_src = (
            ROOT / "fundamental" / "services" / "narrative" / "_forensics.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"checks_total": 11', forensics_src)


if __name__ == "__main__":
    unittest.main()
