"""研报正文抓取：从 research provider 拿研报链接，抓详情页正文，清洗后供叙述层使用。

设计原则：
- 这是「叙述增强」的补充资料，不是核心数据链路，任何失败都优雅降级为空列表或旧缓存，绝不打断主流程。
- 正文带本地文件缓存：带 schema 版本与抓取时间，TTL 由 config/research 控制，避免每次生成叙述都联网。
- 缓存按 infoCode 逐篇复用正文，单篇抓取失败不影响其他篇；元数据（评级/目标价/盈利预测）一并保留。
- 只保留最近若干篇、每篇截断到固定长度，控制送给 LLM 的体量。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.providers.research.eastmoney import EastmoneyResearchProvider
from fundamental.utils import paths

_CACHE_SCHEMA_VERSION = 2

_TAG_RE = re.compile(r"<[^>]+>")
_CTX_RE = re.compile(
    r'<div[^>]*id=["\']ctx-content["\'][^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE
)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


def _research_cache_path(symbol: str) -> Path:
    directory = paths.cache_dir() / "research"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{symbol}.json"


def _cache_ttl_days() -> float:
    return float(load_settings().get("research.cache_ttl_days", 7))


def _default_limit() -> int:
    return int(load_settings().get("research.max_reports", 4))


def _default_chars() -> int:
    return int(load_settings().get("research.chars_per_report", 1200))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_fresh(fetched_at: Any, ttl_days: float) -> bool:
    if not isinstance(fetched_at, str):
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds() <= ttl_days * 86400


def _read_cache(
    path: Path, ttl_days: float, *, legacy_as_stale: bool = True
) -> list[dict[str, Any]] | None:
    """读研报缓存。旧版裸数组默认视为过期，触发一次迁移重抓。

    legacy_as_stale=False 用于联网失败时的降级：此时宁可用旧资料，也不要空结果。
    """

    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(payload, dict):
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        reports = payload.get("reports")
        if not isinstance(reports, list) or not _is_fresh(payload.get("fetched_at"), ttl_days):
            return None
        return [item for item in reports if isinstance(item, dict)]

    if isinstance(payload, list):
        if legacy_as_stale:
            return None
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None
        if (datetime.now(timezone.utc) - modified).total_seconds() > ttl_days * 86400:
            return None
        return [item for item in payload if isinstance(item, dict)]

    return None


def _read_any_cache(path: Path) -> list[dict[str, Any]] | None:
    """忽略 TTL 读取缓存正文，仅用于刷新时复用单篇内容、以及联网失败降级。"""

    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reports = payload.get("reports") if isinstance(payload, dict) else payload
    if not isinstance(reports, list):
        return None
    return [item for item in reports if isinstance(item, dict)]


def _clean(text: str) -> str:
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(page_html: str) -> str:
    """从详情页 HTML 中抽取 #ctx-content 内的段落文本。"""

    match = _CTX_RE.search(page_html)
    segment = match.group(1) if match else page_html
    paragraphs = [_clean(p) for p in _P_RE.findall(segment)]
    paragraphs = [p for p in paragraphs if len(p) > 8]
    # 去掉重复的标题行（正文第一段常是「招商银行(600036)」这类）
    return "\n".join(paragraphs)


def _fetch_recent_reports(symbol: str, limit: int) -> list[dict[str, Any]]:
    """拿最近 limit 篇研报的元数据与链接（失败返回空）。"""

    try:
        reports = EastmoneyResearchProvider().fetch_reports(symbol)
    except Exception:
        return []
    recent = [r for r in reports if r.source_url][:limit]
    return [
        {
            "date": r.publish_date or "",
            "org": r.org_name or "",
            "title": r.title,
            "url": r.source_url or "",
            "info_code": r.info_code or "",
            "analyst": r.analyst or "",
            "rating": r.rating.value if r.rating else "",
            "target_price": r.target_price,
            "industry": r.industry_name or "",
            "eps_forecast": dict(r.eps_forecast or {}),
            "content": "",
        }
        for r in recent
    ]


def _dedupe_key(report: dict[str, Any]) -> str:
    return str(
        report.get("info_code")
        or report.get("url")
        or report.get("title")
        or report.get("date")
        or ""
    )


def load_research_digest(
    symbol: str,
    *,
    limit: int | None = None,
    max_chars_per_report: int | None = None,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """返回最近若干篇研报摘要，每项含元数据、正文与来源链接。

    优先读 TTL 内的缓存；任何异常（网络、解析、配置）都降级为旧缓存或空列表。
    """

    limit = limit if limit is not None else _default_limit()
    max_chars = max_chars_per_report if max_chars_per_report is not None else _default_chars()
    cache_path = _research_cache_path(symbol)
    ttl_days = _cache_ttl_days()
    cached = _read_cache(cache_path, ttl_days)

    if not refresh and cached:
        return cached

    stale = _read_any_cache(cache_path)
    reports = _fetch_recent_reports(symbol, limit)
    if not reports:
        return _read_cache(cache_path, ttl_days, legacy_as_stale=False) or stale or []

    # 已在缓存里的正文按 infoCode 复用，只有新研报或强制刷新才联网抓正文。
    reusable = {
        _dedupe_key(item): item.get("content")
        for item in (cached or stale or [])
        if item.get("content")
    }

    try:
        from fundamental.providers._shared import HttpClient

        client: Any = HttpClient()
    except Exception:
        client = None

    digest: list[dict[str, Any]] = []
    for report in reports:
        key = _dedupe_key(report)
        if not refresh and key in reusable:
            report["content"] = reusable[key]
            digest.append(report)
            continue
        if client is not None:
            try:
                body = _extract_body(client.get_text(report["url"]))
                if body:
                    report["content"] = body[:max_chars]
            except Exception:
                pass
        if not report["content"] and key in reusable:
            report["content"] = reusable[key]
        digest.append(report)

    if digest:
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "symbol": symbol,
            "fetched_at": _now(),
            "reports": digest,
        }
        try:
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return digest
