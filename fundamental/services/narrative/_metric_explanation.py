"""单期指标解释：两阶段 agent + 代码算数，为公司财报中的指标波动给出可核对解释。

流程（services 编排，analysis 不参与）：
1. 代码按 symbol + 报告期 + scope + 指标键从原始服务**重建 facts**，不信任前端传来的数字；
   当期值、上期/去年同期、同比/环比、历史序列全部由代码算好，LLM 不得改写。
2. 阶段一（agent 规划）：模型根据 facts 决定去财报里检索哪些关键词（含港股繁体口径）；
   模型不可用时用词表降级，不阻断。
3. 代码用这些关键词从该报告期财报原文切证据段落（只检索，不判断）。
4. 阶段二（解释生成）：模型结合 facts + 原文证据写解释，必须区分「公司原文说明」与
   「分析推断」，不得把波动写成财务造假定性结论。失败回退模板。

缓存按 symbol + 报告期 + scope + stmt_type + 指标键 分文件，并带 facts 指纹，
底层财报数据变化后自动失效；refresh=true 强制重算。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fundamental.config import load_settings
from fundamental.models import ChatMessage
from fundamental.providers.llm.port import LlmConfigError, LlmError
from fundamental.services.financial_dashboard import build_financial_dashboard
from fundamental.services.llm import chat, chat_json, has_live_llm
from fundamental.services.narrative._evidence import retrieve_for_terms
from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.logging import get_logger
from fundamental.utils.prompts import load_prompt

from ._common import _NARRATIVE_VERSION, _max_tokens, _now, _write_cache

# 检索器按季度选文档后，旧缓存里的证据期次不可信；递增此值让旧缓存自动失效
_EVIDENCE_SCHEMA = 2

_logger = get_logger(__name__)

_ALLOWED_SCOPE = {"primary", "statement", "balance"}
_FALLBACK_TERMS = (
    "变动原因", "主要原因", "主要是", "管理层讨论与分析", "经营情况讨论与分析",
    "主要由於", "主要反映", "管理層討論與分析", "净利润", "营业收入",
)
_HISTORY_LIMIT = 8
_SNAPSHOT_LIMIT = 14


def _cache_key(symbol: str, period: str, scope: str, stmt_type: str, keys: list[str]) -> str:
    raw = "|".join([symbol, period, scope, stmt_type, ",".join(keys)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _cache_path(symbol: str, period: str, scope: str, stmt_type: str, keys: list[str]) -> Path:
    directory = paths.cache_dir() / "narrative"
    directory.mkdir(parents=True, exist_ok=True)
    digest = _cache_key(symbol, period, scope, stmt_type, keys)
    return directory / f"metric_explanation_{symbol}_{digest}.json"


def _read_cache(path: Path, fingerprint: str, refresh: bool) -> dict[str, Any] | None:
    if refresh or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != _NARRATIVE_VERSION:
        return None
    if data.get("fingerprint") != fingerprint or not data.get("available"):
        return None
    return data


def _read_db_cache(cache_key: str, fingerprint: str, refresh: bool) -> dict[str, Any] | None:
    """优先从数据库表读缓存；文件缓存只在表中没有时回退。"""
    if refresh:
        return None
    conn = db.init_db()
    try:
        data = repository.get_metric_explanation(conn, cache_key)
    finally:
        conn.close()
    if not data or data.get("version") != _NARRATIVE_VERSION:
        return None
    if data.get("fingerprint") != fingerprint or not data.get("available"):
        return None
    return data


def _write_db_cache(cache_key: str, output: dict[str, Any]) -> None:
    summary = re.sub(r"\s+", " ", str(output.get("text") or "")).strip()[:240]
    try:
        conn = db.init_db()
        try:
            repository.save_metric_explanation(
                conn,
                cache_key=cache_key,
                symbol=str(output.get("symbol") or ""),
                period=str(output.get("period") or ""),
                scope=str(output.get("scope") or ""),
                stmt_type=str(output.get("stmt_type") or ""),
                keys=list(output.get("keys") or []),
                fingerprint=str(output.get("fingerprint") or ""),
                version=int(output.get("version") or 0),
                summary=summary,
                payload=output,
                generated_at=str(output.get("generated_at") or ""),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 缓存落库失败不能阻断解释返回
        _logger.warning("指标解释缓存写入数据库失败 %s: %s", cache_key, exc)


def list_cached_explanations(symbol: str) -> list[dict[str, Any]]:
    """图表悬停用的缓存索引：仅返回当前版本且仍可用的记录。"""
    conn = db.init_db()
    try:
        rows = repository.list_metric_explanations(conn, symbol)
    finally:
        conn.close()
    return [
        row for row in rows
        if row.get("version") == _NARRATIVE_VERSION and row.get("summary")
    ]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 2) if result == result else None


def _change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _items_and_labels(
    symbol: str, scope: str, stmt_type: str, period: str
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """返回 (同表科目, 对齐的报告期标签, 视图)。

    primary / statement 走 dashboard（period 形如 2026Q3 / 2025年报），
    balance 走拆解视图（period 为 ISO 报告期末 或 2025年）；由代码判断单季/年度，
    前端只传标签，不传数值。
    """
    if scope in ("primary", "statement"):
        frequency = "annual" if "年报" in period else "quarter"
        view = build_financial_dashboard(symbol, frequency)
        labels = list(view["labels"])
        items = (
            list(view["primary"])
            if scope == "primary"
            else list(view["statements"].get(stmt_type, []))
        )
        return items, labels, view

    # 延迟导入，避免 services.balance_breakdown 与 narrative 包模块级循环导入
    from fundamental.services.balance_breakdown import build_balance_view

    view = build_balance_view(symbol)
    if period in view["year_labels"]:
        labels = list(view["year_labels"])
        items = []
        for section in view["sections"].values():
            for item in section["items"]:
                items.append(
                    {
                        "key": item["name"],
                        "name": item["name"],
                        "unit": "亿元",
                        "scale": 1e8,
                        "values": item["yearly_values"],
                    }
                )
    else:
        labels = list(view["labels"])
        items = []
        for section in view["sections"].values():
            for item in section["items"]:
                items.append(
                    {
                        "key": item["name"],
                        "name": item["name"],
                        "unit": "亿元",
                        "scale": 1e8,
                        "values": item["values"],
                    }
                )
    return items, labels, view


def _scaled_series(item: dict[str, Any]) -> list[float | None]:
    scale = float(item.get("scale") or 1.0) or 1.0
    return [_number(value / scale) if value is not None else None for value in item.get("values", [])]


def _selected_facts(
    items: list[dict[str, Any]], labels: list[str], keys: list[str], index: int
) -> list[dict[str, Any]]:
    """选中指标的当期/上期/去年同期值与近 N 期序列，全部代码计算。"""
    by_key = {str(item.get("key")): item for item in items}
    by_name = {str(item.get("name")): item for item in items}
    result: list[dict[str, Any]] = []
    for key in keys:
        item = by_key.get(key) or by_name.get(key)
        if item is None:
            continue
        series = _scaled_series(item)
        if index >= len(series):
            continue
        current = series[index]
        prior = series[index - 1] if index > 0 else None
        prior_label = labels[index - 1] if index > 0 else None
        year_ago_label = f"{int(period_year(labels[index])) - 1}{labels[index][4:]}"
        yoy_index = labels.index(year_ago_label) if year_ago_label in labels else None
        year_ago = series[yoy_index] if yoy_index is not None else None
        history = [
            {"报告期": labels[i], "数值": series[i]}
            for i in range(max(0, index - _HISTORY_LIMIT + 1), index + 1)
            if series[i] is not None
        ]
        result.append(
            {
                "指标": item.get("name") or key,
                "单位": item.get("unit") or "",
                "当期报告期": labels[index],
                "当期值": current,
                "上期值": prior,
                "上期报告期": prior_label,
                "环比%": _change(current, prior),
                "去年同期值": year_ago,
                "同比%": _change(current, year_ago),
                "历史序列": history,
            }
        )
    return result


def period_year(label: str) -> str:
    match = re.search(r"(20\d{2})", str(label))
    return match.group(1) if match else "0"


def _snapshot(
    items: list[dict[str, Any]], labels: list[str], index: int
) -> dict[str, Any]:
    """当期同表科目快照 + 近几期快照，供模型在需要时对照其他指标。

    只保留金额绝对值最大的若干科目，控制上下文；数值已换算为展示单位。
    """
    series_by_name: dict[str, list[float | None]] = {}
    unit_by_name: dict[str, str] = {}
    ranked: list[tuple[float, str, float | None]] = []
    for item in items:
        series = _scaled_series(item)
        name = str(item.get("name") or item.get("key") or "")
        if index >= len(series) or name in series_by_name:
            continue
        series_by_name[name] = series
        unit_by_name[name] = str(item.get("unit") or "")
        value = series[index]
        if value is not None:
            ranked.append((abs(value), name, value))
    ranked.sort(key=lambda row: -row[0])
    current = [
        {"科目": name, "数值": value, "单位": unit_by_name[name]}
        for _abs, name, value in ranked[:_SNAPSHOT_LIMIT]
    ]
    # 历史快照：近 3 期、同样本科目，总量受控
    names = [row[1] for row in ranked[: min(8, len(ranked))]]
    history: list[dict[str, Any]] = []
    for i in range(max(0, index - 2), index + 1):
        snap: dict[str, Any] = {"报告期": labels[i], "科目": {}}
        for name in names:
            series = series_by_name[name]
            if i < len(series) and series[i] is not None:
                snap["科目"][name] = series[i]
        history.append(snap)
    return {"当期同表科目": current, "历史同表科目": history}


def _plan_keywords(facts: dict[str, Any]) -> tuple[list[str], str]:
    """阶段一：让模型决定检索词；失败按词表降级。"""
    names = [item["指标"] for item in facts["选中指标"]]
    fallback = list(dict.fromkeys(names + list(_FALLBACK_TERMS)))[:10]
    if not has_live_llm():
        return fallback, "fallback"
    payload = json.dumps(
        {
            "标的": facts["标的"],
            "报告期": facts["报告期"],
            "选中指标": [
                {"指标": item["指标"], "同比%": item["同比%"], "环比%": item["环比%"]}
                for item in facts["选中指标"]
            ],
            "当期同表科目": facts["当期同表科目"],
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        raw = chat_json(
            [
                ChatMessage("system", load_prompt("metric_explanation_keywords")),
                ChatMessage("user", payload),
            ],
            max_tokens=_max_tokens("metric_explanation_keywords", 400),
        )
        if isinstance(raw, dict):
            words = raw.get("keywords")
            if isinstance(words, list):
                cleaned = [str(word).strip() for word in words if str(word).strip()][:10]
                if cleaned:
                    return cleaned, "llm"
    except Exception:  # noqa: BLE001 - 规划失败不影响解释主流程
        pass
    return fallback, "fallback"


def _template(facts: dict[str, Any], evidence: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for item in facts["选中指标"]:
        parts.append(
            f"【{item.get('指标')}】{item.get('当期报告期')} {item.get('当期值')} {item.get('单位', '')}；"
            f"上期 {item.get('上期值')}（环比 {item.get('环比%')}%），"
            f"去年同期 {item.get('去年同期值')}（同比 {item.get('同比%')}%）。"
        )
    if evidence:
        parts.append(
            "财报原文相关段落：" + "；".join(
                f"{e['source']}（{e['period']}）：{e['text'][:160]}" for e in evidence
            )
        )
    else:
        parts.append("未检索到与该指标相关的财报原文段落，以上为数据层面的对照。")
    parts.append("如需判断波动原因，请结合公司管理层讨论与分析及财报附注人工核对。")
    return "\n\n".join(parts)


def _finalize(
    facts: dict[str, Any]
) -> tuple[str, str, list[str], str, list[dict[str, str]]]:
    """两阶段：模型定检索词 → 代码检索原文 → 模型解释。

    返回 (解释文本, source, 检索词, 检索词来源, 原文证据)。
    """
    keywords, keyword_source = _plan_keywords(facts)
    evidence = retrieve_for_terms(
        facts["标的"], keywords, facts["报告期"], max_snippets=5, max_chars=460
    )
    template = _template(facts, evidence)
    if not has_live_llm():
        return template, "template", keywords, keyword_source, evidence
    payload = json.dumps(
        {**facts, "财报原文证据": evidence}, ensure_ascii=False, indent=2, default=str
    )
    try:
        result = chat(
            [
                ChatMessage("system", load_prompt("metric_explanation")),
                ChatMessage("user", payload),
            ],
            max_tokens=_max_tokens("metric_explanation", 1800),
        )
        text = result.content.strip()
    except (LlmError, LlmConfigError):
        text = ""
    return (text, "llm", keywords, keyword_source, evidence) if text else (
        template, "template", keywords, keyword_source, evidence
    )


def generate_metric_explanation(
    symbol: str,
    period: str,
    scope: str,
    keys: list[str],
    *,
    stmt_type: str = "income",
    refresh: bool = False,
    cached_only: bool = False,
) -> dict[str, Any]:
    """生成某报告期、某组指标的解释；代码算数，模型只写文字。

    ``cached_only=True`` 只读取已缓存结果，绝不触发 LLM；供弹窗打开时秒显
    历史解释，用户仍可用「重新生成」强制走两阶段 agent。
    """
    scope = str(scope or "primary")
    if scope not in _ALLOWED_SCOPE:
        raise ValueError("scope 只支持 primary / statement / balance")
    keys = [str(key).strip() for key in keys if str(key).strip()]
    if not period or not keys:
        raise ValueError("period 与 keys 不能为空")
    stmt_type = str(stmt_type or "income")

    items, labels, _view = _items_and_labels(symbol, scope, stmt_type, period)
    if period not in labels:
        return {
            "symbol": symbol,
            "period": period,
            "scope": scope,
            "available": False,
            "text": "该报告期不在当前指标序列中，请选择图中已有的财报周期。",
            "source": "template",
            "generated_at": _now(),
        }
    index = labels.index(period)
    selected = _selected_facts(items, labels, keys, index)
    if not selected:
        return {
            "symbol": symbol,
            "period": period,
            "scope": scope,
            "available": False,
            "text": "未找到对应的指标科目，请重新选择。",
            "source": "template",
            "generated_at": _now(),
        }
    if all(item["当期值"] is None for item in selected):
        return {
            "symbol": symbol,
            "period": period,
            "scope": scope,
            "available": False,
            "text": "所选指标在该报告期没有数值（财报未单列或尚未披露），无法生成解释。",
            "source": "template",
            "generated_at": _now(),
        }
    snapshot = _snapshot(items, labels, index)
    facts: dict[str, Any] = {
        "标的": symbol,
        "报告期": period,
        "口径": scope,
        "选中指标": selected,
        **snapshot,
        "说明": "金额单位见各指标；同比/环比、历史序列均由代码从财报数据计算，请勿改写。",
    }
    fingerprint = hashlib.sha1(
        json.dumps(
            {"labels": labels, "selected": selected, "evidence_schema": _EVIDENCE_SCHEMA},
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    cache_key = _cache_key(symbol, period, scope, stmt_type, keys)
    cached = _read_db_cache(cache_key, fingerprint, refresh)
    if cached is None:
        # 兼容早期的文件缓存：命中后回填到表，之后只读表
        cached = _read_cache(_cache_path(symbol, period, scope, stmt_type, keys), fingerprint, refresh)
        if cached:
            _write_db_cache(cache_key, dict(cached, version=_NARRATIVE_VERSION))
    if cached:
        return dict(cached, cached=True)
    if cached_only:
        return {
            "symbol": symbol,
            "period": period,
            "scope": scope,
            "available": False,
            "cached": False,
            "text": "该组合尚无缓存解释，点击「生成解释」后才会调用模型。",
            "source": "none",
            "generated_at": _now(),
        }

    text, source, keywords, keyword_source, evidence = _finalize(facts)
    output: dict[str, Any] = {
        "symbol": symbol,
        "period": period,
        "scope": scope,
        "stmt_type": stmt_type,
        "keys": keys,
        "available": True,
        "指标": selected,
        "财报原文证据": evidence,
        "检索词": keywords,
        "检索词来源": keyword_source,
        "text": text,
        "source": source,
        "cached": False,
        "generated_at": _now(),
        "fingerprint": fingerprint,
        "version": _NARRATIVE_VERSION,
    }
    _write_cache(_cache_path(symbol, period, scope, stmt_type, keys), output)
    _write_db_cache(cache_key, output)
    return output
