"""研报正文抓取：从 research provider 拿研报链接，抓详情页正文，清洗后供叙述层使用。

设计原则：
- 这是「叙述增强」的补充资料，不是核心数据链路，任何失败都优雅降级为空列表，绝不打断主流程。
- 正文带本地文件缓存（研报详情短期稳定），避免每次生成叙述都联网。
- 只保留最近若干篇、每篇截断到固定长度，控制送给 LLM 的体量。
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from fundamental.providers.research.eastmoney import EastmoneyResearchProvider
from fundamental.utils import paths

_TAG_RE = re.compile(r"<[^>]+>")
_CTX_RE = re.compile(
    r'<div[^>]*id=["\']ctx-content["\'][^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE
)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


def _research_cache_path(symbol: str) -> Path:
    directory = paths.cache_dir() / "research"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{symbol}.json"


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
    """拿最近 limit 篇研报的日期/机构/标题/链接（失败返回空）。"""

    try:
        reports = EastmoneyResearchProvider().fetch_reports(symbol)
    except Exception:
        return []
    recent = [r for r in reports if r.source_url][:limit]
    return [
        {
            "date": r.publish_date,
            "org": r.org_name,
            "title": r.title,
            "url": r.source_url,
        }
        for r in recent
    ]


def load_research_digest(
    symbol: str,
    *,
    limit: int = 4,
    max_chars_per_report: int = 1200,
    refresh: bool = False,
) -> list[dict[str, str]]:
    """返回最近若干篇研报的正文摘要列表，每项含 date/org/title/content。

    优先读缓存；任何异常（网络、解析、配置）都降级为空列表。
    """

    cache_path = _research_cache_path(symbol)
    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            pass

    reports = _fetch_recent_reports(symbol, limit)
    if not reports:
        return []

    try:
        from fundamental.providers._shared import HttpClient

        client = HttpClient()
    except Exception:
        return []

    digest: list[dict[str, str]] = []
    for report in reports:
        try:
            page = client.get_text(report["url"])
            body = _extract_body(page)
            if not body:
                continue
            digest.append(
                {
                    "date": report.get("date") or "",
                    "org": report.get("org") or "",
                    "title": report.get("title") or "",
                    "content": body[:max_chars_per_report],
                }
            )
        except Exception:
            # 单篇抓取失败不影响其他篇
            continue

    if digest:
        try:
            cache_path.write_text(json.dumps(digest, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return digest
