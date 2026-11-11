"""数据模型：所有跨模块传递的数据对象与枚举。

契约依据见 CONTRACTS.md 第 2 节与第 4 节，字段名与表结构（SCHEMA.md）一一对应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class Market(StrEnum):
    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"
    HK = "HK"
    US = "US"


class ReportType(StrEnum):
    Q1 = "Q1"
    H1 = "H1"
    Q3 = "Q3"
    FY = "FY"


class StatementType(StrEnum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"


class DocType(StrEnum):
    ANNUAL_REPORT = "annual_report"
    SEMI_REPORT = "semi_report"
    Q1_REPORT = "q1_report"
    Q3_REPORT = "q3_report"
    ANNOUNCEMENT = "announcement"
    PROSPECTUS = "prospectus"


class DocStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class HolderCategory(StrEnum):
    INDIVIDUAL = "individual"
    FAMOUS_TRADER = "famous_trader"
    INSTITUTION = "institution"
    FUND = "fund"
    SOCIAL_SECURITY = "social_security"
    INSURANCE = "insurance"
    QFII = "qfii"
    OTHER = "other"


class LitigationRole(StrEnum):
    PLAINTIFF = "plaintiff"
    DEFENDANT = "defendant"
    THIRD = "third"


class Rating(StrEnum):
    BUY = "buy"
    OVERWEIGHT = "overweight"
    NEUTRAL = "neutral"
    UNDERWEIGHT = "underweight"
    SELL = "sell"


class RatingChange(StrEnum):
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    MAINTAIN = "maintain"


class RevenueDimension(StrEnum):
    REGION = "region"
    PRODUCT = "product"


class AuditOpinion(StrEnum):
    STANDARD = "standard"
    QUALIFIED = "qualified"
    DISCLAIMER = "disclaimer"
    ADVERSE = "adverse"


class ForecastType(StrEnum):
    PREINCREASE = "preincrease"
    PREDECREASE = "predecrease"
    TURNLOSS = "turnloss"
    FIRSTLOSS = "firstloss"
    CONTINUEPROFIT = "continueprofit"
    CONTINUELOSS = "continueloss"
    SLIGHTINCREASE = "slightincrease"
    SLIGHTDECREASE = "slightdecrease"
    UNCERTAIN = "uncertain"


class CorporateActionType(StrEnum):
    SEO = "seo"
    RIGHTS = "rights"
    CONVERTIBLE = "convertible"
    BUYBACK = "buyback"
    INCENTIVE = "incentive"


class InsiderDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BehaviorType(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class IntegrityCategory(StrEnum):
    PROMISE_FULFILLED = "promise_fulfilled"
    BROKEN_PROMISE = "broken_promise"
    VIOLATION_GUARANTEE = "violation_guarantee"
    FUND_OCCUPATION = "fund_occupation"
    PENALTY = "penalty"
    INQUIRY = "inquiry"
    FINANCIAL_RESTATEMENT = "financial_restatement"
    MISLEADING_DISCLOSURE = "misleading_disclosure"
    HONEST_OPERATION = "honest_operation"
    OTHER = "other"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Stock:
    code: str
    market: Market
    name: str
    industry: Optional[str] = None
    sector: Optional[str] = None
    list_date: Optional[str] = None
    is_st: bool = False


@dataclass(frozen=True)
class CompanyProfile:
    business: Optional[str] = None
    total_shares: Optional[float] = None
    float_shares: Optional[float] = None
    prospectus_url: Optional[str] = None
    official_website: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    fetched_at: str
    price: Optional[float] = None
    market_cap: Optional[float] = None
    turnover_rate: Optional[float] = None


@dataclass(frozen=True)
class KlineBar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    adj_factor: float = 1.0


@dataclass(frozen=True)
class ValuationPoint:
    trade_date: str
    close_price: Optional[float] = None
    pe_ttm: Optional[float] = None
    pe_lar: Optional[float] = None
    pb_mrq: Optional[float] = None
    ps_ttm: Optional[float] = None
    peg: Optional[float] = None
    pcf_ocf_ttm: Optional[float] = None
    total_market_cap: Optional[float] = None


@dataclass(frozen=True)
class FinancialReport:
    report_date: str
    report_type: ReportType
    revenue: Optional[float] = None
    net_profit: Optional[float] = None
    net_profit_deducted: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    roic: Optional[float] = None
    debt_ratio: Optional[float] = None
    current_ratio: Optional[float] = None
    ocf: Optional[float] = None
    fcf: Optional[float] = None
    eps: Optional[float] = None
    bps: Optional[float] = None
    revenue_yoy: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    audit_opinion: Optional[AuditOpinion] = None


@dataclass(frozen=True)
class StatementItem:
    report_date: str
    report_type: ReportType
    statement_type: StatementType
    item_code: str
    item_name: str
    item_value: Optional[float] = None
    item_yoy: Optional[float] = None


@dataclass(frozen=True)
class RevenueComposition:
    report_date: str
    dimension: RevenueDimension
    item_name: str
    revenue: Optional[float] = None
    ratio: Optional[float] = None


@dataclass(frozen=True)
class Announcement:
    title: str
    category: Optional[str] = None
    publish_date: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None


@dataclass(frozen=True)
class Document:
    doc_type: DocType
    period: Optional[str] = None
    title: str = ""
    source_url: Optional[str] = None
    local_path: Optional[str] = None
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    status: DocStatus = DocStatus.PENDING


@dataclass(frozen=True)
class Litigation:
    title: str
    publish_date: Optional[str] = None
    role: Optional[LitigationRole] = None
    amount: Optional[float] = None
    status: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class CirculatingHolder:
    report_date: str
    rank: Optional[int] = None
    holder_name: str = ""
    holder_category: Optional[HolderCategory] = None
    hold_num: Optional[float] = None
    hold_ratio: Optional[float] = None
    change_num: Optional[float] = None
    change_ratio: Optional[float] = None
    is_new: bool = False
    market_value: Optional[float] = None


@dataclass(frozen=True)
class FundHolding:
    end_date: str
    fund_code: str
    fund_name: Optional[str] = None
    hold_num: Optional[float] = None
    hold_market_value: Optional[float] = None
    change_num: Optional[float] = None


@dataclass(frozen=True)
class Sentiment:
    stat_date: str
    post_count: Optional[int] = None
    read_count: Optional[int] = None
    reply_count: Optional[int] = None
    hot_score: Optional[float] = None
    sentiment_score: Optional[float] = None


@dataclass(frozen=True)
class ResearchReport:
    publish_date: Optional[str] = None
    org_name: Optional[str] = None
    analyst: Optional[str] = None
    rating: Optional[Rating] = None
    rating_change: Optional[RatingChange] = None
    target_price: Optional[float] = None
    title: str = ""
    source_url: Optional[str] = None


@dataclass(frozen=True)
class IndustryBoard:
    industry_code: str
    industry_name: str
    trade_date: str
    pct_change: Optional[float] = None
    capital_flow: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    leader_stock: Optional[str] = None


@dataclass(frozen=True)
class DividendRecord:
    plan_notice_date: Optional[str] = None
    ex_date: Optional[str] = None
    record_date: Optional[str] = None
    pay_date: Optional[str] = None
    cash_per_share: Optional[float] = None
    bonus_ratio: Optional[float] = None
    transfer_ratio: Optional[float] = None


@dataclass(frozen=True)
class CapitalFlow:
    trade_date: str
    main_net: Optional[float] = None
    super_large_net: Optional[float] = None
    large_net: Optional[float] = None
    medium_net: Optional[float] = None
    small_net: Optional[float] = None
    main_net_ratio: Optional[float] = None


@dataclass(frozen=True)
class Hotspot:
    trade_date: str
    board_code: str
    board_name: str
    pct_change: Optional[float] = None
    main_net: Optional[float] = None
    leader_stock_code: Optional[str] = None
    leader_pct_change: Optional[float] = None


@dataclass(frozen=True)
class OfficialUpdate:
    fetched_at: str
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None


@dataclass(frozen=True)
class StockProduct:
    stock_id: int
    product_code: str
    product_name: str
    source: str = "sina"
    category: Optional[str] = None


@dataclass(frozen=True)
class ProductPrice:
    product_code: str
    source: str
    trade_date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


@dataclass(frozen=True)
class EarningsForecast:
    report_date: str
    forecast_type: Optional[ForecastType] = None
    notice_date: Optional[str] = None
    forecast_net_profit_low: Optional[float] = None
    forecast_net_profit_high: Optional[float] = None
    net_profit_yoy_low: Optional[float] = None
    net_profit_yoy_high: Optional[float] = None
    content: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class ConsensusEstimate:
    report_date: str
    est_revenue: Optional[float] = None
    est_net_profit: Optional[float] = None
    est_eps: Optional[float] = None
    target_price: Optional[float] = None
    rating: Optional[Rating] = None
    org_count: Optional[int] = None


@dataclass(frozen=True)
class ReportCalendarItem:
    report_type: ReportType
    report_period: Optional[str] = None
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None


@dataclass(frozen=True)
class Unlock:
    unlock_date: str
    unlock_shares: Optional[float] = None
    unlock_ratio: Optional[float] = None
    unlock_market_value: Optional[float] = None
    holder_type: Optional[str] = None


@dataclass(frozen=True)
class Pledge:
    holder_name: Optional[str] = None
    pledge_date: Optional[str] = None
    pledge_shares: Optional[float] = None
    pledge_ratio: Optional[float] = None
    pledge_total_ratio: Optional[float] = None
    status: Optional[str] = None


@dataclass(frozen=True)
class CorporateAction:
    action_date: Optional[str] = None
    action_type: CorporateActionType = CorporateActionType.BUYBACK
    amount: Optional[float] = None
    shares: Optional[float] = None
    price: Optional[float] = None
    status: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class InsiderTrade:
    trade_date: str
    insider_name: Optional[str] = None
    position: Optional[str] = None
    change_shares: Optional[float] = None
    change_ratio: Optional[float] = None
    avg_price: Optional[float] = None
    direction: Optional[InsiderDirection] = None


@dataclass(frozen=True)
class ShareholderCount:
    end_date: str
    holder_count: Optional[int] = None


@dataclass(frozen=True)
class MacroIndicator:
    indicator_code: str
    indicator_name: str
    trade_date: str
    value: Optional[float] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class NorthboundHolding:
    trade_date: str
    hold_shares: Optional[float] = None
    hold_ratio: Optional[float] = None
    change_shares: Optional[float] = None
    change_ratio: Optional[float] = None


@dataclass(frozen=True)
class IntegrityRecord:
    record_date: Optional[str] = None
    behavior_type: BehaviorType = BehaviorType.NEUTRAL
    category: Optional[IntegrityCategory] = None
    title: str = ""
    description: Optional[str] = None
    severity: Optional[Severity] = None
    source: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class SyncState:
    provider: str
    capability: str
    stock_id: Optional[int] = None
    data_type: Optional[str] = None
    last_synced_at: Optional[str] = None
    watermark: Optional[str] = None
    status: str = "ok"


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


# ---------- 分析结果模型 ----------


@dataclass(frozen=True)
class ReturnResult:
    start_date: str
    end_date: str
    price_return: Optional[float] = None
    dividend_return: Optional[float] = None
    total_return: Optional[float] = None
    total_return_reinvested: Optional[float] = None
    cagr: Optional[float] = None
    cagr_reinvested: Optional[float] = None
    cumulative_dividends: Optional[float] = None
    dividend_yield_series: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ValuationResult:
    method: str
    fair_value_low: Optional[float] = None
    fair_value_high: Optional[float] = None
    current_price: Optional[float] = None
    margin_of_safety: Optional[float] = None
    assumptions: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    score: float
    weight: float
    detail: str = ""


@dataclass(frozen=True)
class RedFlag:
    flag_type: str
    severity: Severity
    description: str


@dataclass(frozen=True)
class CompositeScore:
    total: float
    rating: str
    dimensions: list[DimensionScore]
    red_flags: list[RedFlag]


@dataclass(frozen=True)
class MasterVerdict:
    symbol: str
    master: str
    score: float
    verdict: str
    signals: list[dict[str, Any]]
    narrative: str = ""


@dataclass(frozen=True)
class PeerPosition:
    symbol: str
    name: str
    industry: str
    market_cap: Optional[float] = None
    revenue: Optional[float] = None
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    moat_rating: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None


@dataclass(frozen=True)
class MoatAssessment:
    symbol: str
    rating: str
    evidence: list[str]
    width: str = ""


@dataclass(frozen=True)
class PriceCorrelation:
    product_code: str
    period: str
    correlation: Optional[float] = None
    beta: Optional[float] = None
    note: str = ""


@dataclass(frozen=True)
class PriceCompare:
    product_code: str
    price_series: list[dict[str, Any]]
    stock_series: list[dict[str, Any]]
    correlation: Optional[PriceCorrelation] = None


@dataclass(frozen=True)
class HolderPosition:
    holder_name: str
    symbol: str
    report_date: str
    holder_category: Optional[HolderCategory] = None
    hold_num: Optional[float] = None
    hold_ratio: Optional[float] = None
    change_num: Optional[float] = None
    change_ratio: Optional[float] = None


@dataclass(frozen=True)
class CompanyComparison:
    symbol: str
    name: str
    industry: str
    revenue_cagr: Optional[float] = None
    market_share: Optional[float] = None
    market_share_trend: Optional[str] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    dividend_payout_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap_change: Optional[float] = None
    moat_score: Optional[float] = None
    margin_of_safety: Optional[float] = None
    cycle_position: Optional[str] = None


@dataclass(frozen=True)
class IndustryReport:
    industry: str
    as_of: str
    companies: list[CompanyComparison]
    dimension_summary: dict[str, Any]


@dataclass(frozen=True)
class DelistingRisk:
    symbol: str
    score: float
    triggers: list[str]
    note: str = ""


@dataclass(frozen=True)
class FraudScore:
    symbol: str
    score: float
    signals: list[str]
    note: str = ""


@dataclass(frozen=True)
class FinancialReviewReport:
    symbol: str
    summary: str
    findings: list[dict[str, Any]]
    integrity_summary: str = ""
    score: Optional[float] = None
