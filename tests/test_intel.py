"""情报资料门面（个股研报/行业研报/机构调研）的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import fundamental.utils.intel as intel
from fundamental.models import ResearchReport, SurveyRecord


def _industry_row() -> dict[str, object]:
    return {
        "infoCode": "APIND1",
        "publishDate": "2026-11-18 00:00:00.000",
        "orgSName": "华宝证券",
        "title": "银行理财周度跟踪",
        "indvInduName": "银行Ⅱ",
    }


def _survey_row() -> dict[str, object]:
    return {
        "SECUCODE": "600036.SH",
        "SECURITY_CODE": "600036",
        "SECURITY_NAME_ABBR": "招商银行",
        "NOTICE_DATE": "2026-11-11 00:00:00",
        "RECEIVE_START_DATE": "2026-11-02 00:00:00",
        "RECEIVE_WAY_EXPLAIN": "业绩交流会",
        "RECEIVE_OBJECT": "投资者",
        "CONTENT": "公司就经营情况与投资者进行了交流，内容足够长可供摘录。",
        "URL": None,
    }


class TestNewProviders(unittest.TestCase):
    def test_industry_research_uses_qtype_and_industry_code(self):
        from fundamental.providers.industry_research.eastmoney import (
            EastmoneyIndustryResearchProvider,
        )

        with mock.patch(
            "fundamental.providers.industry_research.eastmoney.HttpClient"
        ) as Client:
            Client.return_value.get_json.return_value = {"data": [_industry_row()], "hits": 1}
            rows = EastmoneyIndustryResearchProvider().fetch_industry_reports("475", limit=2)

        params = Client.return_value.get_json.call_args.kwargs["params"]
        self.assertEqual(params["qType"], "1")
        self.assertEqual(params["industryCode"], "475")
        self.assertNotIn("code", params)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].info_code, "APIND1")
        self.assertEqual(rows[0].industry_code, "475")

    def test_survey_maps_public_content(self):
        from fundamental.providers.survey.eastmoney import EastmoneySurveyProvider

        with mock.patch("fundamental.providers.survey.eastmoney.HttpClient") as Client:
            Client.return_value.get_json.return_value = {
                "success": True,
                "result": {"count": 1, "data": [_survey_row()]},
            }
            rows = EastmoneySurveyProvider().fetch_surveys("600036.SH", limit=2)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company, "招商银行")
        self.assertEqual(rows[0].way, "业绩交流会")
        self.assertIn("经营情况", rows[0].content)
        self.assertEqual(rows[0].source_key, "600036.SH_2026-11-11_2026-11-02")


class TestIntelFacade(unittest.TestCase):
    def test_loads_all_kinds_and_writes_separate_caches(self):
        industry = ResearchReport(
            publish_date="2026-11-18",
            org_name="华宝证券",
            title="银行理财周度跟踪",
            source_url="http://example.com/industry",
            info_code="APIND1",
            industry_code="475",
        )
        survey = SurveyRecord(
            publish_date="2026-11-11",
            company="招商银行",
            title="业绩交流会（2026-11-11）",
            content="公司就经营情况与投资者进行了交流，内容足够长可供摘录。",
            source_key="k1",
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            intel.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(
            intel,
            "load_research_digest",
            return_value=[{"info_code": "AP1", "content": "c", "industry_code": "475"}],
        ), mock.patch.object(intel, "EastmoneyIndustryResearchProvider") as Industry, mock.patch.object(
            intel, "EastmoneySurveyProvider"
        ) as Survey, mock.patch.object(intel, "HttpClient") as Client:
            Industry.return_value.fetch_industry_reports.return_value = [industry]
            Survey.return_value.fetch_surveys.return_value = [survey]
            Client.return_value.get_text.return_value = (
                '<div id="ctx-content"><p>行业研报正文内容足够长可供摘录。</p></div>'
            )
            out = intel.load_intel_digest("600036.SH", refresh=True)
            self.assertEqual(len(out["company_reports"]), 1)
            self.assertEqual(out["industry_reports"][0]["info_code"], "APIND1")
            self.assertIn("行业研报正文", out["industry_reports"][0]["content"])
            self.assertEqual(out["surveys"][0]["org"], "招商银行")
            cache_dir = Path(tmp) / "intel"
            self.assertTrue((cache_dir / "600036.SH_industry.json").exists())
            self.assertTrue((cache_dir / "600036.SH_survey.json").exists())

    def test_provider_failure_falls_back_to_cached(self):
        cached = {
            "schema_version": 2,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "reports": [{"info_code": "OLD", "content": "旧正文"}],
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            intel.paths, "cache_dir", return_value=Path(tmp)
        ), mock.patch.object(intel, "EastmoneyIndustryResearchProvider") as Industry:
            Industry.return_value.fetch_industry_reports.side_effect = RuntimeError("offline")
            path = Path(tmp) / "intel" / "600036.SH_industry.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            out = intel.load_intel_digest(
                "600036.SH", kinds=("industry",), industry_code="475"
            )

        self.assertEqual(out["industry_reports"][0]["info_code"], "OLD")
        self.assertNotIn("company_reports", out)


if __name__ == "__main__":
    unittest.main()
