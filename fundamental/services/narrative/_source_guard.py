"""财报原文前置守卫：分析之前先保证多期原文已经归档。

这个板块的价值是「用公司财报原文印证偏离原因」。本地没有原文时，LLM 只能
按结构化数字推断，很容易把营业利润以下的一次性扰动误归为经营端问题。
因此把「先有原文、再分析」做成流程里的硬约束：

- 覆盖不足时按市场自动补档（A 股巨潮 / 港股披露易，复用同一归档服务）；
- 补齐后才进入扫描、原文检索与逐条剖析；
- 下载失败不阻断分析，但明确写出状态与原因，不静默降级。

本模块只依赖既有 Port/Adapter 与文档表，不引入新的存储结构。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fundamental.utils.logging import get_logger
from fundamental.utils.pdf import is_source_text_usable

from ._evidence import _load_documents, _text_path

_logger = get_logger(__name__)

_QUARTERLY_TYPES = ("semi_report", "q1_report", "q3_report")


def _is_usable(doc: dict[str, Any]) -> bool:
    """文档抽得出财报正文才算可用——只有 PDF、空文本、披露提示壳都不算。

    非空文本曾让「XX报告披露的提示性公告」混入覆盖度，守卫因此误判原文充足，
    实际逐段通读时一句经营数据都拿不到。这里用文本长度与标题双重把关。
    """
    path = _text_path(doc)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return is_source_text_usable(text, str(doc.get("title") or ""))


def source_document_status(symbol: str, db_path: Path | None = None) -> dict[str, Any]:
    """统计可读原文的份数、覆盖年份与类型分布。"""
    try:
        docs = _load_documents(symbol, db_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("原文覆盖度检查失败 %s: %s", symbol, exc)
        return {"documents": 0, "years": [], "annual": 0, "quarterly": 0, "error": str(exc)}

    usable = [doc for doc in docs if _is_usable(doc)]
    years = sorted({str(doc.get("period") or "")[:4] for doc in usable} - {""})
    return {
        "documents": len(usable),
        "years": years,
        "annual": sum(1 for doc in usable if doc.get("doc_type") == "annual_report"),
        "quarterly": sum(1 for doc in usable if str(doc.get("doc_type")) in _QUARTERLY_TYPES),
    }


def _sufficient(status: dict[str, Any], min_documents: int, min_years: int) -> bool:
    return (
        int(status.get("documents") or 0) >= min_documents
        and len(status.get("years") or []) >= min_years
    )


def ensure_source_documents(
    symbol: str,
    *,
    db_path: Path | None = None,
    min_documents: int = 3,
    min_years: int = 2,
    per_type: int = 6,
) -> dict[str, Any]:
    """缺档时自动下载多期财报原文，返回可写进报告的守卫结果。

    ``per_type`` 是补档时每种报告类型尝试下载的上限，不是目标总量——
    归档服务按报告期幂等，已存在的期次不会重复下载。
    """
    before = source_document_status(symbol, db_path)
    guard: dict[str, Any] = {
        "status": "ok",
        "required": {"documents": min_documents, "years": min_years},
        "before": before,
        "after": before,
        "downloaded": {},
    }
    if _sufficient(before, min_documents, min_years):
        return guard

    guard["status"] = "repairing"
    try:
        # 延迟导入：归档服务属于 services 层，守卫属于 narrative 层，
        # 这里保持单向依赖，也方便测试直接替换归档实现。
        from fundamental.services.archive import archive_documents

        stats = archive_documents(symbol, db_path=db_path, per_type=per_type)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("财报原文自动补档失败 %s: %s", symbol, exc)
        guard["status"] = "error"
        guard["reason"] = f"{type(exc).__name__}: {exc}"[:300]
        return guard

    guard["downloaded"] = stats if isinstance(stats, dict) else {}
    if "error" in guard["downloaded"]:
        guard["status"] = "error"
        guard["reason"] = str(guard["downloaded"]["error"])
        return guard

    after = source_document_status(symbol, db_path)
    guard["after"] = after
    guard["status"] = "repaired" if _sufficient(after, min_documents, min_years) else "insufficient"
    if guard["status"] == "insufficient":
        guard["reason"] = "补档后原文仍不足，解释将以已归档原文和结构化数据为准"
    return guard
