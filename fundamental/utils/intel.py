"""情报资料门面：个股研报 + 行业研报 + 机构调研。

设计原则：
- 按资料类型分缓存、分级 TTL（个股研报 7 天、行业研报 3 天、调研纪要 30 天），互不阻塞。
- 任一来源失败都降级为空列表或旧缓存，不打断主叙述链路。
- 对外只暴露 load_intel_digest()，消费端不需要知道资料来自哪家数据源。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.providers._shared import HttpClient
from fundamental.providers.industry_research.eastmoney import EastmoneyIndustryResearchProvider
from fundamental.providers.survey.eastmoney import EastmoneySurveyProvider
from fundamental.utils import paths
from fundamental.utils.research_text import (
    extract_report_body,
    load_research_digest,
    read_research_cache,
)

# 与 research_text 的 {schema_version, fetched_at, reports} 格式对齐，复用其 TTL 读取。
_CACHE_SCHEMA_VERSION = 2
_ALL_KINDS = ("company", "industry", "survey")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path(symbol: str, kind: str) -> Path:
    directory = paths.cache_dir() / "intel"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{symbol}_{kind}.json"


def _read(path: Path, ttl_days: float, *, min_chars: int = 0) -> list[dict[str, Any]]:
    return read_research_cache(path, ttl_days, min_chars=min_chars) or []


def _write(path: Path, items: list[dict[str, Any]], max_chars: int) -> None:
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "fetched_at": _now(),
        "max_chars": max_chars,
        "reports": items,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _http_client() -> Any:
    try:
        return HttpClient()
    except Exception:
        return None


def _load_industry(
    symbol: str,
    industry_code: str | None,
    *,
    refresh: bool,
    limit: int,
    max_chars: int,
    ttl_days: float,
) -> list[dict[str, Any]]:
    path = _cache_path(symbol, "industry")
    cached = _read(path, ttl_days, min_chars=max_chars)
    if not refresh and cached:
        return cached
    if not industry_code:
        return cached

    try:
        reports = EastmoneyIndustryResearchProvider().fetch_industry_reports(
            industry_code, limit=limit
        )
    except Exception:
        return cached
    if not reports:
        return cached

    client = _http_client()
    items: list[dict[str, Any]] = []
    for report in reports:
        old = next(
            (
                item
                for item in cached
                if item.get("info_code") and item.get("info_code") == report.info_code
            ),
            None,
        )
        content = old.get("content", "") if old else ""
        if not content and report.source_url and client is not None:
            try:
                body = extract_report_body(client.get_text(report.source_url))
                if body:
                    content = body[:max_chars]
            except Exception:
                pass
        items.append(
            {
                "date": report.publish_date or "",
                "org": report.org_name or "",
                "title": report.title,
                "url": report.source_url or "",
                "info_code": report.info_code or "",
                "content": content,
            }
        )

    if items:
        _write(path, items, max_chars)
    return items or cached


def _load_survey(
    symbol: str,
    *,
    refresh: bool,
    limit: int,
    max_chars: int,
    ttl_days: float,
) -> list[dict[str, Any]]:
    path = _cache_path(symbol, "survey")
    cached = _read(path, ttl_days, min_chars=max_chars)
    if not refresh and cached:
        return cached

    try:
        records = EastmoneySurveyProvider().fetch_surveys(symbol, limit=limit)
    except Exception:
        return cached

    items = [
        {
            "date": record.publish_date or "",
            "org": record.company or "",
            "title": record.title,
            "url": record.source_url or "",
            "source_key": record.source_key or "",
            "way": record.way or "",
            "content": record.content[:max_chars],
        }
        for record in records
        if record.content
    ]
    if items:
        _write(path, items, max_chars)
    return items or cached


def load_intel_digest(
    symbol: str,
    *,
    kinds: tuple[str, ...] | list[str] | None = None,
    refresh: bool = False,
    industry_code: str | None = None,
    research_limit: int | None = None,
) -> dict[str, Any]:
    """按股票返回情报资料；kinds 可选 company/industry/survey，默认全部。"""

    selected = tuple(kinds) if kinds else _ALL_KINDS
    settings = load_settings()
    research_limit = research_limit or int(settings.get("research.max_reports", 6))
    industry_limit = int(settings.get("intel.industry_limit", 5))
    survey_limit = int(settings.get("intel.survey_limit", 5))
    industry_ttl = float(settings.get("intel.industry_ttl_days", 3))
    survey_ttl = float(settings.get("intel.survey_ttl_days", 30))

    output: dict[str, Any] = {"symbol": symbol, "generated_at": _now()}
    company: list[dict[str, Any]] = []
    if "company" in selected:
        company = load_research_digest(symbol, limit=research_limit, refresh=refresh)
        output["company_reports"] = company
    if "industry" in selected:
        code = industry_code or next(
            (item.get("industry_code") for item in company if item.get("industry_code")), None
        )
        output["industry_reports"] = _load_industry(
            symbol,
            code,
            refresh=refresh,
            limit=industry_limit,
            max_chars=int(settings.get("intel.industry_chars", 2000)),
            ttl_days=industry_ttl,
        )
    if "survey" in selected:
        output["surveys"] = _load_survey(
            symbol,
            refresh=refresh,
            limit=survey_limit,
            max_chars=int(settings.get("intel.survey_chars", 2500)),
            ttl_days=survey_ttl,
        )
    return output
