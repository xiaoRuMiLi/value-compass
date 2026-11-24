"""财务戏法因子注册表（纯元数据，属于 analysis 层）。

一条因子 = 一份 ``FactorSpec``。注册表是**单一事实来源**，同时驱动：

- ``analysis/forensic_patterns.py`` 的 checks 列表与 coverage 模板（替掉手写 lambda）；
- ``services/narrative/_report_mining.py`` 的「取证任务单」（documentary 因子
  共用一次分段通读，不按因子数放大 LLM 调用）；
- 前端因子标签，以及 ``references/red-flags.md`` 的 ``RF-*`` 红旗编号映射。

分层约束：本模块**不含可执行判定逻辑**，不读文件/DB/配置，不 import
provider 或 services。判定函数由 ``forensic_patterns.py`` 按 ``handler`` 键
提供；LLM 只负责为 documentary 因子挑原文摘录并给出「触发建议」，是否触发
由代码按「引文可逐字定位 + 模型建议 + 风险语义标记」三个确定性条件确认。

新增因子的最小改动：在本文件 ``FACTOR_REGISTRY`` 追加一条 ``FactorSpec``；
若为 quantitative，再在 ``forensic_patterns.py`` 的 ``_HANDLERS`` 注册一个
模块级包装函数。不需要碰扫描循环、coverage 模板或前端标签表。
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

EvidenceKind = Literal["quantitative", "documentary", "hybrid"]

# 与 forensic_patterns._CATEGORIES 的 key 一致；新增类别需同步那里与前端。
CATEGORY_KEYS = ("earnings_quality", "big_bath", "related_party", "accounting")

# 与 FraudPatternConfig 的字段名一致，仅作展示/校验，不在此定义数值。
# 真实阈值仍只有 config/forensics.toml + FraudPatternConfig 两处（后者内置默认）。
_TH = "forensics.thresholds.fraud_patterns"


@dataclasses.dataclass(frozen=True)
class FactorSpec:
    """一条财务戏法检查的元数据。

    ``evidence_kind`` 决定判定权归属：quantitative 完全由代码按阈值算；
    documentary 由 LLM 到财报原文取证、代码按引文确认；hybrid 为预留类型
    （数值筛出可疑报告期后只把该期原文交给 LLM 核实，信号须标注「核实」）。

    ``trigger_markers`` 不是全局关键词检索，而是对 LLM 挑出的候选引文做
    二次确认：召回靠语义，触发靠代码，避免回到硬编码关键词匹配的老路。
    """

    id: str
    category: str
    title: str
    check_name: str
    evidence_kind: EvidenceKind
    # quantitative 用
    required_items: tuple[str, ...] = ()
    thresholds: tuple[str, ...] = ()
    handler: str = ""
    # documentary 用
    source_scope: tuple[str, ...] = ()
    probe_criteria: str = ""
    trigger_markers: tuple[str, ...] = ()
    # 共用
    markets: tuple[str, ...] = ("A", "HK")
    evidence_source: str = "statement_items"
    prompt_ref: str | None = None
    red_flag_refs: tuple[str, ...] = ()
    severity: str = "low"
    confidence: str = "low"


# ---------- 11 项既有确定性检查（判定逻辑不变，只补元数据） ----------

_QUANTITATIVE: tuple[FactorSpec, ...] = (
    FactorSpec(
        id="FRD001", category="earnings_quality",
        title="应收账款增速持续高于营收且占收入比抬升",
        check_name="应收账款增速背离", evidence_kind="quantitative",
        required_items=("ACCOUNTS_RECE", "OPERATE_INCOME"),
        thresholds=("ar_divergence_pp", "ar_divergence_high_pp", "ar_ratio_rise_pct", "min_periods"),
        handler="ar_divergence", red_flag_refs=("RF-AQ-04",),
        severity="high", confidence="medium",
    ),
    FactorSpec(
        id="FRD002", category="earnings_quality",
        title="利润含金量偏低：经营现金流未同步跟上净利",
        check_name="净现比持续偏低", evidence_kind="quantitative",
        required_items=("NETCASH_OPERATE", "PARENT_NETPROFIT"),
        thresholds=("cash_conversion_low", "cash_conversion_severe", "cash_conversion_periods"),
        handler="cash_conversion", red_flag_refs=("RF-AQ-03", "RF-OP-01"),
        severity="high", confidence="medium",
    ),
    FactorSpec(
        id="FRD003", category="earnings_quality",
        title="销售收现比下滑，收入变现能力走弱",
        check_name="销售收现比恶化", evidence_kind="quantitative",
        required_items=("SALES_SERVICES", "OPERATE_INCOME"),
        thresholds=("cash_receipt_low", "cash_receipt_drop_pp", "min_periods"),
        handler="cash_receipt", markets=("A",),
        red_flag_refs=("RF-AQ-04",), severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD004", category="earnings_quality",
        title="存货增速持续高于成本/营收",
        check_name="存货异常堆积", evidence_kind="quantitative",
        required_items=("INVENTORY", "OPERATE_COST"),
        thresholds=("inventory_divergence_pp", "min_periods"),
        handler="inventory", markets=("A",),
        red_flag_refs=("RF-AQ-04",), severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD005", category="big_bath",
        title="极端低利润期后出现净利大幅反弹",
        check_name="财务洗澡（巨亏后反弹）", evidence_kind="quantitative",
        required_items=("PARENT_NETPROFIT", "OPERATE_INCOME"),
        thresholds=("big_bath_margin_pct", "big_bath_rebound_pct"),
        handler="big_bath", red_flag_refs=("RF-OP-01",),
        severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD006", category="big_bath",
        title="商誉大额下降与净利骤降同期发生",
        check_name="商誉集中减值", evidence_kind="quantitative",
        required_items=("GOODWILL", "PARENT_NETPROFIT"),
        thresholds=("goodwill_drop_pct",),
        handler="goodwill", red_flag_refs=("RF-OP-02",),
        severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD007", category="related_party",
        title="关联方应收款占应收帐款比重偏高",
        check_name="关联方往来占比", evidence_kind="quantitative",
        required_items=("ACCOUNTS_RECE",),
        thresholds=("related_party_ratio_pct",),
        handler="related_party_balance", red_flag_refs=("RF-GV-01",),
        severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD008", category="related_party",
        title="客户高度集中且存在关联方销售",
        check_name="客户集中度与关联方销售", evidence_kind="quantitative",
        required_items=("top5_customer_pct", "related_party_sales_pct"),
        thresholds=("top5_customer_pct", "related_sales_pct"),
        handler="customer_concentration", evidence_source="财报原文事实卡",
        red_flag_refs=("RF-GV-01",), severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD009", category="accounting",
        title="研发投入资本化率抬升",
        check_name="研发资本化率骤升", evidence_kind="quantitative",
        required_items=("rd_capitalization_pct",),
        thresholds=("rd_cap_rate_rise_pp", "rd_cap_profit_pct"),
        handler="rd_capitalization", evidence_source="财报原文事实卡",
        red_flag_refs=("RF-AQ-03",), severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD010", category="accounting",
        title="会计政策 / 合并范围 / 审计意见发生变化",
        check_name="会计政策/合并范围/审计意见变化", evidence_kind="quantitative",
        required_items=("accounting_policy_change", "consolidation_change", "audit"),
        thresholds=("consolidation_entities",),
        handler="accounting_changes", evidence_source="财报原文事实卡",
        red_flag_refs=("RF-AQ-01", "RF-AQ-02"),
        severity="medium", confidence="medium",
    ),
    FactorSpec(
        id="FRD011", category="accounting",
        title="在建工程持续大幅增长而固定资产未同步增加",
        check_name="在建工程长期不转固", evidence_kind="quantitative",
        required_items=("CIP", "FIXED_ASSET"),
        thresholds=("cip_growth_pct", "cip_periods"),
        handler="cip", red_flag_refs=("RF-AQ-04",),
        severity="medium", confidence="medium",
    ),
)


# ---------- 原文取证型：有财报但科目没落库时，让规则反过来驱动通读 ----------
# trigger_markers 只收「能指向异常披露」的具体词组，绝不用常规科目名或单字。
# 实测教训：单字「受限」会命中港股「受限制現金」科目行、「质押」会命中银行
# 回购/央行借款的常规质押物、「保证金」是票据业务的例行附注——这些都不是
# 资金占用线索，却会让 5 只里 4 只误报。标记越窄，LLM 的宽松建议越伤不到结论。

_DOCUMENTARY: tuple[FactorSpec, ...] = (
    FactorSpec(
        id="FRD012", category="related_party",
        title="关联方资金占用与非经营性往来",
        check_name="关联方资金占用（原文取证）", evidence_kind="documentary",
        source_scope=("关联方及关联交易", "控股股东及其他关联方占用资金",
                      "非经营性资金占用", "其他应收款", "专项审计说明"),
        probe_criteria=(
            "财报披露控股股东、实际控制人或其关联方存在非经营性资金占用、代垫款项、"
            "无商业实质的资金往来，或专项说明确认占用余额与清偿安排。"
        ),
        trigger_markers=("占用", "非经营性", "代垫", "清偿", "拆借", "关联方资金"),
        evidence_source="财报原文（LLM 分段通读 + 引文逐字核验）",
        prompt_ref="report_mining", red_flag_refs=("RF-GV-01",),
        severity="medium", confidence="low",
    ),
    FactorSpec(
        id="FRD013", category="related_party",
        title="违规担保、承诺未履行与监管问询",
        check_name="违规担保与承诺未履行（原文取证）", evidence_kind="documentary",
        source_scope=("重大担保", "承诺事项履行情况", "诚信状况", "处罚及整改", "问询函"),
        probe_criteria=(
            "财报披露存在违规担保或担保代偿风险、公开承诺未按期履行、"
            "被监管处罚/问询且涉及信息披露，或存在逾期未清偿债务。"
        ),
        trigger_markers=("违规担保", "尚未履行", "超期未履行", "未履行", "问询函",
                         "行政处罚", "代偿", "逾期未清偿"),
        evidence_source="财报原文（LLM 分段通读 + 引文逐字核验）",
        prompt_ref="report_mining", red_flag_refs=("RF-GV-01", "RF-GV-02"),
        severity="low", confidence="low",
    ),
    FactorSpec(
        id="FRD014", category="accounting",
        title="前期差错更正、追溯调整与非标准审计意见",
        check_name="财务重述与会计差错更正（原文取证）", evidence_kind="documentary",
        source_scope=("会计政策变更", "前期差错更正", "追溯调整", "审计报告", "财务报表重述"),
        probe_criteria=(
            "财报披露前期会计差错更正、追溯调整或重述、会计政策/估计变更对可比期"
            "损益产生重大影响，或审计意见为非标准无保留。"
        ),
        trigger_markers=("差错更正", "追溯调整", "重述", "前期差错",
                         "保留意见", "无法表示意见", "否定意见"),
        evidence_source="财报原文（LLM 分段通读 + 引文逐字核验）",
        prompt_ref="report_mining", red_flag_refs=("RF-AQ-01", "RF-AQ-02"),
        severity="medium", confidence="low",
    ),
    FactorSpec(
        id="FRD015", category="earnings_quality",
        title="大额货币资金伴随资金受限或关联财务公司集中存款",
        check_name="资金受限与存贷结构（原文取证）", evidence_kind="documentary",
        source_scope=("货币资金", "所有权或使用权受到限制的资产",
                      "存放于关联财务公司款项", "短期借款", "利息收入"),
        probe_criteria=(
            "公司在披露大额货币资金的同时存在大比例资金受限、质押/冻结、"
            "存放于关联财务公司的集中存款或大额有息负债，且未给出合理商业解释。"
        ),
        trigger_markers=("财务公司", "存放财务公司", "资金受限", "受限资金",
                         "使用权受到限制", "冻结"),
        evidence_source="财报原文（LLM 分段通读 + 引文逐字核验）",
        prompt_ref="report_mining", red_flag_refs=("RF-GV-01", "RF-OP-01"),
        severity="low", confidence="low",
    ),
)

FACTOR_REGISTRY: tuple[FactorSpec, ...] = _QUANTITATIVE + _DOCUMENTARY


def all_specs() -> tuple[FactorSpec, ...]:
    return FACTOR_REGISTRY


def spec_by_id(factor_id: str) -> FactorSpec | None:
    return next((s for s in FACTOR_REGISTRY if s.id == factor_id), None)


def specs_by_kind(kind: str) -> tuple[FactorSpec, ...]:
    return tuple(s for s in FACTOR_REGISTRY if s.evidence_kind == kind)


def checks_total() -> int:
    """降级兜底与 coverage 都从这里取，杜绝 ``checks_total: 11`` 硬编码。"""
    return len(FACTOR_REGISTRY)


def factor_labels() -> list[dict[str, Any]]:
    """前端因子清单标签：注册表一处生成，页面不再维护第二份名单。"""
    return [
        {
            "id": s.id,
            "title": s.title,
            "check_name": s.check_name,
            "category": s.category,
            "evidence_kind": s.evidence_kind,
            "markets": list(s.markets),
            "required_items": list(s.required_items),
            "evidence_source": s.evidence_source,
            "red_flag_refs": list(s.red_flag_refs),
            "severity": s.severity,
        }
        for s in FACTOR_REGISTRY
    ]


def documentary_briefs() -> list[dict[str, Any]]:
    """「取证任务单」：一次性塞进分段挖掘，让一次通读给所有原文因子挂牌。

    刻意不下发 ``trigger_markers``：避免模型按关键词硬凑摘录。模型只看到
    规则描述与检索范围，是否触发由代码在引文上做确定性确认。
    """
    return [
        {
            "factor_id": s.id,
            "title": s.title,
            "category": s.category,
            "probe_criteria": s.probe_criteria,
            "source_scope": list(s.source_scope),
            "red_flag_refs": list(s.red_flag_refs),
        }
        for s in specs_by_kind("documentary")
    ]


def validate_registry() -> list[str]:
    """返回结构问题列表（空列表表示健康），供单测与启动时自检。"""
    problems: list[str] = []
    seen: set[str] = set()
    for s in FACTOR_REGISTRY:
        if s.id in seen:
            problems.append(f"重复因子 id: {s.id}")
        seen.add(s.id)
        if s.category not in CATEGORY_KEYS:
            problems.append(f"{s.id} 类别未在 _CATEGORIES: {s.category}")
        if not s.red_flag_refs:
            problems.append(f"{s.id} 缺少 red_flag_refs")
        if s.evidence_kind == "quantitative" and not s.handler:
            problems.append(f"{s.id} quantitative 因子缺少 handler")
        if s.evidence_kind == "documentary":
            if not s.probe_criteria:
                problems.append(f"{s.id} documentary 因子缺少 probe_criteria")
            if not s.trigger_markers:
                problems.append(f"{s.id} documentary 因子缺少 trigger_markers")
            if not s.source_scope:
                problems.append(f"{s.id} documentary 因子缺少 source_scope")
    return problems
