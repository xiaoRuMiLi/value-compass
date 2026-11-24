"""财报取证扫描的触发阈值配置对象。

本模块只依赖标准库，保持 ``analysis`` 层的纯粹性：不读配置文件、不 import
``fundamental.config``。真正的「文件 → 配置对象」转换放在 services 层
（``services/forensics_config.py``），扫描器只接受本对象注入。

这样 ``scan_forensics()`` 在测试里不传配置时，用的是这里的内置默认值；
线上则由叙述层把 ``config/forensics.toml`` 的内容注入进来。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from typing import Any

# 线索级关键词的内置默认值（可被配置文件整段覆盖）
_DEFAULT_RISK_TITLE_KEYWORDS: tuple[str, ...] = (
    "立案", "处罚", "罚没", "诉讼", "仲裁", "问询函", "关注函", "监管函",
    "警示函", "冻结", "查封", "会计差错", "前期差错", "更正", "追溯调整",
    "退市", "风险警示", "违规担保", "资金占用", "被执行", "重整", "涉嫌",
    "调查", "通报批评", "公开谴责", "减值", "业绩预告修正", "业绩变脸",
)
_DEFAULT_NEGATIVE_VIEW_KEYWORDS: tuple[str, ...] = (
    "下调", "低于预期", "不及预期", "减值", "问询", "诉讼", "处罚", "风险",
    "承压", "下滑", "缩水", "争议", "质疑", "违规",
)
_DEFAULT_SEVERE_CATEGORIES: tuple[str, ...] = (
    "penalty", "financial_restatement", "misleading_disclosure", "inquiry",
    "violation_guarantee", "fund_occupation", "broken_promise",
)
# 「例行合规披露」标题正则：这类文件是上市公司按格式要求定期披露的合规材料
# （年度资金占用专项说明/汇总表、独董声明与承诺、差错责任追究制度等），
# 标题里的「资金占用/差错/承诺」不等于发生了占用、差错或违规。
# 实测五粮液 34 条诚信档案里绝大多数属于此类，曾被误计为 high 风险写进结论。
_DEFAULT_REGULATORY_ROUTINE_PATTERNS: tuple[str, ...] = (
    r"(?:资金占用|关联资金往来).*(?:专项说明|专项审核|专项审计|汇总表|情况汇总)",
    r"非经营性资金占用.*汇总表",
    r"(?:关联方|控股股东).*资金占用.*(?:专项|汇总表)",
    r"独立董事.*(?:声明|承诺|提名人)",
    r"(?:候选人|提名人)声明与承诺",
    r"自愿承诺不减持",
    r"现金分红承诺",
    r"年报信息披露重大差错责任追究制度",
    r"年度报告.*(?:股东户数|相关期间表述)的更正公告",
)

# 资本运作/治理线索的公告标题关键词（只列已披露事实，不代表存在问题）
_DEFAULT_CAPITAL_ACTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "新增收购": ("收购", "并购", "重大资产购买", "吸收合并"),
    "跨界投资": ("跨界", "对外投资", "子公司", "增资", "参股"),
    "异常股权激励": ("股权激励", "限制性股票", "股票期权", "激励计划"),
    "关联交易": ("关联交易", "关联方", "日常关联交易"),
}

_RATE_KEYS = ("gross_margin", "net_margin", "roe", "debt_ratio")


@dataclasses.dataclass(frozen=True)
class FraudPatternConfig:
    """财务戏法检查的全部触发阈值。

    字段名与 ``config/forensics.toml`` 的 ``[forensics.thresholds.fraud_patterns]``
    一一对应。和 ``ForensicsConfig`` 一样：这里只放内置默认值，不读配置文件；
    线上值由 services 层注入，``ForensicsConfig.fingerprint()`` 会自动把本对象
    递归纳入指纹，改阈值即让体检缓存失效。

    单独成类而不是平铺进 ``ForensicsConfig``，是为了让新增检查项的阈值不把
    主扫描的 50+ 字段继续撑长，也让「不要留第二份硬编码」在扫描器侧可验证。
    """

    # 虚增利润 / 收入质量
    ar_divergence_pp: float = 30.0
    ar_divergence_high_pp: float = 60.0
    ar_ratio_rise_pct: float = 20.0
    cash_conversion_low: float = 0.70
    cash_conversion_severe: float = 0.50
    cash_conversion_periods: int = 2
    cash_receipt_low: float = 0.80
    cash_receipt_drop_pp: float = 10.0
    inventory_divergence_pp: float = 30.0
    # 财务洗澡
    big_bath_margin_pct: float = -5.0
    big_bath_rebound_pct: float = 100.0
    goodwill_drop_pct: float = 50.0
    # 关联交易
    related_party_ratio_pct: float = 10.0
    top5_customer_pct: float = 70.0
    related_sales_pct: float = 5.0
    # 会计估计与资本运作
    rd_cap_rate_rise_pp: float = 5.0
    rd_cap_profit_pct: float = 3.0
    consolidation_entities: int = 5
    cip_growth_pct: float = 100.0
    cip_periods: int = 3
    min_periods: int = 4

    @classmethod
    def from_mapping(cls, section: dict[str, Any] | None) -> "FraudPatternConfig":
        """从 toml 子表构造；非法值逐项回退内置默认，不影响其他字段。"""

        if not isinstance(section, dict):
            return cls()
        base = cls()
        out: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            default = getattr(base, f.name)
            raw = section.get(f.name, default)
            try:
                if isinstance(default, bool):
                    value = default
                elif isinstance(default, int):
                    value = max(int(raw), 1)  # 期数/家数至少为 1
                elif isinstance(default, float):
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError("non-finite")
                else:
                    value = default
            except (TypeError, ValueError):
                value = default
            out[f.name] = value
        return cls(**out)

    def as_dict(self) -> dict[str, Any]:
        """供扫描器注入与页面展示（JSON 可序列化）。"""

        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ForensicsConfig:
    """财报体检的全部可调参数。字段名与 ``config/forensics.toml`` 的键一一对应。"""

    # 窗口与输出预算
    lookback_periods: int = 12
    recent_years: int = 6
    max_signals: int = 16
    max_anomalies: int = 24

    # 财报原文前置守卫：分析前至少要有这么多期可读原文 / 覆盖年份，
    # 不足则自动补档（每类报告最多下载 source_archive_per_type 份）。
    min_source_documents: int = 3
    min_source_years: int = 2
    source_archive_per_type: int = 6

    # 离群统计
    mad_multiplier: float = 3.0
    mad_min_samples: int = 4
    rate_require_outlier: tuple[str, ...] = ("debt_ratio",)

    # 盈余质量
    divergence_revenue_yoy_pct: float = 15.0
    divergence_profit_yoy_pct: float = -15.0
    profit_growth_gap_pp: float = 50.0
    deducted_gap_pct: float = 30.0
    deducted_gap_high_pct: float = 50.0

    # 业绩波动 / 率类异动（百分点）
    revenue_yoy_swing_pp: float = 40.0
    # 「业绩拐点」：由长周期高增长转入负增长。拐点前平台期的净利同比均值 > N%
    # （注意是"平台均值"而非"上年单点"，避免一次性高增长造成的误判）
    profit_turn_high_pct: float = 10.0
    # 平台期长度：拐点前连续 N 个报告期维持高增长才算"多年高增长"（默认 4 期 ≈ 一年）
    inflection_platform_periods: int = 4
    # 平台期每期净利同比的下限（%）：低于此值不计入高增长平台
    inflection_platform_floor_pct: float = 3.0
    # 转负后负增长期数门槛：最近 N 期内净利同比为负的期数 >= 该值才算"持续转负"（默认 2）
    inflection_min_negative_periods: int = 2
    gross_margin_pp: float = 6.0
    net_margin_pp: float = 8.0
    roe_pp: float = 5.0
    debt_ratio_pp: float = 8.0
    roe_drop_high_pp: float = -5.0

    # 现金质量
    ocf_profit_ratio: float = 0.65
    ocf_consecutive_periods: int = 3

    # 费用与成本
    expense_ratio_pp: float = 5.0
    expense_cluster_min_items: int = 2

    # 业绩预告
    forecast_deviation: float = 25.0
    forecast_amount_tolerance_pct: float = 5.0
    forecast_yoy_tolerance_pp: float = 5.0

    # 资金动向
    insider_sell_count: int = 5
    pledge_ratio_pct: float = 35.0

    # 资本运作与治理线索（相对自身历史离群 + 公告关键词）
    # 突击分红：每股派现相对历史中位数的 MAD 倍数超过该值
    dividend_mad_multiplier: float = 3.0
    # 扩充股本：股本同比增幅超过该百分比
    share_capital_growth_pct: float = 10.0
    # 异常资本支出：投资活动现金流出净额相对历史中位数的 MAD 倍数
    capex_mad_multiplier: float = 3.0
    # 商誉突增：相对历史中位数的 MAD 倍数（收购的会计痕迹）
    goodwill_mad_multiplier: float = 3.0
    # 资本运作公告关键词（按风险类型分组）
    capital_action_keywords: dict[str, tuple[str, ...]] = dataclasses.field(
        default_factory=lambda: {k: tuple(v) for k, v in _DEFAULT_CAPITAL_ACTION_KEYWORDS.items()}
    )

    # 市场反应
    market_profit_yoy_up_pct: float = 15.0
    market_return_down_pct: float = -5.0
    market_profit_yoy_down_pct: float = -15.0
    market_return_up_pct: float = 10.0
    price_min_bars: int = 12
    price_reaction_periods: int = 8
    price_window_days: int = 5
    price_publish_window_days: int = 75
    external_scan_chars: int = 600
    external_excerpt_chars: int = 200

    # 聚合与风险等级
    consolidate_min_occurrences: int = 2
    loss_lookback_periods: int = 8
    loss_min_count: int = 3
    risk_high_min_high: int = 2
    risk_medium_min_medium: int = 3

    # 线索级关键词
    risk_title_keywords: tuple[str, ...] = _DEFAULT_RISK_TITLE_KEYWORDS
    negative_view_keywords: tuple[str, ...] = _DEFAULT_NEGATIVE_VIEW_KEYWORDS
    regulatory_severe_categories: tuple[str, ...] = _DEFAULT_SEVERE_CATEGORIES
    regulatory_routine_patterns: tuple[str, ...] = _DEFAULT_REGULATORY_ROUTINE_PATTERNS

    # 财务戏法检查阈值（独立子表，随 fingerprint 一起变更）
    fraud_patterns: FraudPatternConfig = dataclasses.field(
        default_factory=FraudPatternConfig
    )

    @classmethod
    def from_mapping(cls, section: dict[str, Any] | None) -> "ForensicsConfig":
        """从 ``settings['forensics']`` 构造配置；非法值逐项回退到内置默认值。

        顶层键（窗口/预算）与 ``thresholds`` 子表合并，子表优先。任何类型错误
        都只影响该字段，不会让整个体检功能崩掉。
        """

        if not isinstance(section, dict):
            return cls()
        thresholds = section.get("thresholds")
        merged: dict[str, Any] = {
            k: v for k, v in section.items() if k != "thresholds"
        }
        if isinstance(thresholds, dict):
            merged.update(thresholds)

        base = cls()

        def pick(key: str, cast: Any, *, minimum: float | None = None,
                 maximum: float | None = None) -> Any:
            default = getattr(base, key)
            try:
                value = cast(merged.get(key, default))
            except (TypeError, ValueError):
                return default
            if isinstance(value, float) and not math.isfinite(value):
                return default
            if minimum is not None:
                value = max(value, minimum)
            if maximum is not None:
                value = min(value, maximum)
            return value

        def pick_terms(key: str, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
            default = getattr(base, key)
            raw = merged.get(key, default)
            if not isinstance(raw, (list, tuple)):
                return default
            terms = [
                str(v).strip() for v in raw
                if isinstance(v, (str, int, float)) and str(v).strip()
            ]
            if allowed is not None:
                terms = [t for t in terms if t in allowed]
            # 去重且保持顺序；空列表视为「不配置」，回退默认值
            return tuple(dict.fromkeys(terms)) or default

        def pick_keyword_groups(key: str) -> dict[str, tuple[str, ...]]:
            """解析 {风险类型: [关键词...]} 映射；未配置/非法时回退默认。"""
            default = {k: tuple(v) for k, v in base.capital_action_keywords.items()}
            raw = merged.get(key)
            if not isinstance(raw, dict):
                return default
            out: dict[str, tuple[str, ...]] = {}
            for group, terms in raw.items():
                if not isinstance(terms, (list, tuple)):
                    continue
                cleaned = [
                    str(v).strip() for v in terms
                    if isinstance(v, (str, int, float)) and str(v).strip()
                ]
                if cleaned:
                    out[str(group).strip()] = tuple(dict.fromkeys(cleaned))
            return out or default

        return cls(
            lookback_periods=pick("lookback_periods", int, minimum=2),
            recent_years=pick("recent_years", int, minimum=1),
            max_signals=pick("max_signals", int, minimum=1),
            max_anomalies=pick("max_anomalies", int, minimum=1),
            min_source_documents=pick("min_source_documents", int, minimum=0),
            min_source_years=pick("min_source_years", int, minimum=0),
            source_archive_per_type=pick("source_archive_per_type", int, minimum=1),
            mad_multiplier=pick("mad_multiplier", float, minimum=0.1),
            mad_min_samples=pick("mad_min_samples", int, minimum=2),
            rate_require_outlier=pick_terms("rate_require_outlier", _RATE_KEYS),
            divergence_revenue_yoy_pct=pick("divergence_revenue_yoy_pct", float),
            divergence_profit_yoy_pct=pick("divergence_profit_yoy_pct", float),
            profit_growth_gap_pp=pick("profit_growth_gap_pp", float, minimum=0.0),
            deducted_gap_pct=pick("deducted_gap_pct", float, minimum=0.0),
            deducted_gap_high_pct=pick("deducted_gap_high_pct", float, minimum=0.0),
            revenue_yoy_swing_pp=pick("revenue_yoy_swing_pp", float, minimum=0.0),
            profit_turn_high_pct=pick("profit_turn_high_pct", float, minimum=0.0),
            inflection_platform_periods=pick("inflection_platform_periods", int, minimum=2),
            inflection_platform_floor_pct=pick("inflection_platform_floor_pct", float, minimum=0.0),
            inflection_min_negative_periods=pick("inflection_min_negative_periods", int, minimum=1),
            gross_margin_pp=pick("gross_margin_pp", float, minimum=0.0),
            net_margin_pp=pick("net_margin_pp", float, minimum=0.0),
            roe_pp=pick("roe_pp", float, minimum=0.0),
            debt_ratio_pp=pick("debt_ratio_pp", float, minimum=0.0),
            roe_drop_high_pp=pick("roe_drop_high_pp", float),
            ocf_profit_ratio=pick("ocf_profit_ratio", float, minimum=0.0),
            ocf_consecutive_periods=pick("ocf_consecutive_periods", int, minimum=1),
            expense_ratio_pp=pick("expense_ratio_pp", float, minimum=0.0),
            expense_cluster_min_items=pick("expense_cluster_min_items", int, minimum=0),
            forecast_deviation=pick("forecast_deviation", float, minimum=0.0),
            forecast_amount_tolerance_pct=pick("forecast_amount_tolerance_pct", float, minimum=0.0),
            forecast_yoy_tolerance_pp=pick("forecast_yoy_tolerance_pp", float, minimum=0.0),
            insider_sell_count=pick("insider_sell_count", int, minimum=1),
            pledge_ratio_pct=pick("pledge_ratio_pct", float, minimum=0.0),
            dividend_mad_multiplier=pick("dividend_mad_multiplier", float, minimum=0.0),
            share_capital_growth_pct=pick("share_capital_growth_pct", float, minimum=0.0),
            capex_mad_multiplier=pick("capex_mad_multiplier", float, minimum=0.0),
            goodwill_mad_multiplier=pick("goodwill_mad_multiplier", float, minimum=0.0),
            capital_action_keywords=pick_keyword_groups("capital_action_keywords"),
            market_profit_yoy_up_pct=pick("market_profit_yoy_up_pct", float),
            market_return_down_pct=pick("market_return_down_pct", float),
            market_profit_yoy_down_pct=pick("market_profit_yoy_down_pct", float),
            market_return_up_pct=pick("market_return_up_pct", float),
            price_min_bars=pick("price_min_bars", int, minimum=2),
            price_reaction_periods=pick("price_reaction_periods", int, minimum=1),
            price_window_days=pick("price_window_days", int, minimum=1),
            price_publish_window_days=pick("price_publish_window_days", int, minimum=1),
            external_scan_chars=pick("external_scan_chars", int, minimum=0),
            external_excerpt_chars=pick("external_excerpt_chars", int, minimum=0),
            consolidate_min_occurrences=pick("consolidate_min_occurrences", int, minimum=0),
            loss_lookback_periods=pick("loss_lookback_periods", int, minimum=1),
            loss_min_count=pick("loss_min_count", int, minimum=1),
            risk_high_min_high=pick("risk_high_min_high", int, minimum=1),
            risk_medium_min_medium=pick("risk_medium_min_medium", int, minimum=1),
            risk_title_keywords=pick_terms("risk_title_keywords"),
            negative_view_keywords=pick_terms("negative_view_keywords"),
            regulatory_severe_categories=pick_terms("regulatory_severe_categories"),
            regulatory_routine_patterns=pick_terms("regulatory_routine_patterns"),
            fraud_patterns=FraudPatternConfig.from_mapping(merged.get("fraud_patterns")),
        )

    def fingerprint(self) -> str:
        """配置指纹：用于让叙述层缓存随阈值改动自动失效。"""

        payload = json.dumps(
            dataclasses.asdict(self), sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def as_dict(self) -> dict[str, Any]:
        """供页面/日志展示当前生效的阈值（JSON 可序列化）。"""

        out = dataclasses.asdict(self)
        result: dict[str, Any] = {}
        for k, v in out.items():
            if isinstance(v, tuple):
                result[k] = list(v)
            elif isinstance(v, dict):
                # capital_action_keywords: {str: tuple} → {str: list}
                result[k] = {
                    sk: list(sv) if isinstance(sv, tuple) else sv
                    for sk, sv in v.items()
                }
            else:
                result[k] = v
        return result
