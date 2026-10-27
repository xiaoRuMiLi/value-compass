# value-compass 接口契约

本文档定义数据模型、Provider 的 Port 接口、分析计算模型和 Web API 端点。
它是 `fundamental/models.py`、`fundamental/providers/*/port.py`、`fundamental/web/routes/api.py` 的实现依据。

## 1. 通用约定

- 股票标识统一用 `symbol` 字符串，格式 `{code}.{market}`，如 `600519.SH`、`00700.HK`、`BABA.US`。
  由 `utils/symbols.py` 负责解析与归一化。
- 日期参数统一 `YYYY-MM-DD` 字符串，左闭右闭区间。
- 分页列表接口返回 `Page[T]`（含 `items、total、page、size`），全量列表返回 `list[T]`。
- Provider 只返回 `models.py` 里定义的数据模型，不得返回私有字典或第三方类型。
- Provider 出错抛统一异常 `ProviderError`，Web 层转成 `{ "error": { "code", "message" } }`。

## 2. 数据模型（models.py）

```text
Stock(code, market, name, industry, sector, list_date, is_st)
CompanyProfile(business, total_shares, float_shares, prospectus_url, official_website)
Snapshot(fetched_at, price, market_cap, turnover_rate)
KlineBar(trade_date, open, high, low, close, volume, amount, adj_factor)
ValuationPoint(trade_date, close_price, pe_ttm, pe_lar, pb_mrq, ps_ttm, peg, pcf_ocf_ttm, total_market_cap)
FinancialReport(report_date, report_type, revenue, net_profit, net_profit_deducted,
               gross_margin, net_margin, roe, roic, debt_ratio, current_ratio,
               ocf, fcf, eps, bps, revenue_yoy, net_profit_yoy)
StatementItem(report_date, report_type, statement_type, item_code, item_name, item_value, item_yoy)
RevenueComposition(report_date, dimension, item_name, revenue, ratio)
Announcement(title, category, publish_date, source, source_url, pdf_url)
Document(doc_type, period, title, source_url, local_path, file_size, sha256, status)
Litigation(title, publish_date, role, amount, status, source_url)
CirculatingHolder(report_date, rank, holder_name, holder_category, hold_num, hold_ratio,
                  change_num, change_ratio, is_new, market_value)
FundHolding(end_date, fund_code, fund_name, hold_num, hold_market_value, change_num)
Sentiment(stat_date, post_count, read_count, reply_count, hot_score, sentiment_score)
ResearchReport(publish_date, org_name, analyst, rating, rating_change, target_price, title, source_url)
IndustryBoard(industry_code, industry_name, trade_date, pct_change, capital_flow, pe, pb, leader_stock)
DividendRecord(plan_notice_date, ex_date, record_date, pay_date, cash_per_share, bonus_ratio, transfer_ratio)
CapitalFlow(trade_date, main_net, super_large_net, large_net, medium_net, small_net, main_net_ratio)
Hotspot(trade_date, board_code, board_name, pct_change, main_net, leader_stock_code, leader_pct_change)
OfficialUpdate(fetched_at, category, title, content, url)
StockProduct(stock_id, product_code, product_name, source, category)
ProductPrice(product_code, source, trade_date, open, high, low, close, volume)
EarningsForecast(report_date, forecast_type, notice_date, forecast_net_profit_low,
                 forecast_net_profit_high, net_profit_yoy_low, net_profit_yoy_high,
                 content, source_url)
ConsensusEstimate(report_date, est_revenue, est_net_profit, est_eps, target_price, rating, org_count)
ReportCalendarItem(report_type, report_period, planned_date, actual_date)
Unlock(unlock_date, unlock_shares, unlock_ratio, unlock_market_value, holder_type)
Pledge(holder_name, pledge_date, pledge_shares, pledge_ratio, pledge_total_ratio, status)
CorporateAction(action_date, action_type, amount, shares, price, status, source_url)
InsiderTrade(trade_date, insider_name, position, change_shares, change_ratio, avg_price, direction)
ShareholderCount(end_date, holder_count)
MacroIndicator(indicator_code, indicator_name, trade_date, value, source)
NorthboundHolding(trade_date, hold_shares, hold_ratio, change_shares, change_ratio)
IntegrityRecord(record_date, behavior_type, category, title, description, severity, source, source_url)
SyncState(provider, capability, stock_id, data_type, last_synced_at, watermark, status)
Page[T](items, total, page, size)
```

## 3. Provider Port 接口

每个能力目录的 `port.py` 定义协议，数据源实现它。`registry.py` 按 `config/providers.toml` 选择实现。

```text
QuotePort            capability="quote"
  fetch_snapshot(symbol) -> Snapshot
  fetch_kline(symbol, start, end) -> list[KlineBar]      # 不复权 OHLCV + adj_factor
  fetch_valuations(symbol, start=None, end=None) -> list[ValuationPoint]

FinancialPort        capability="financial"
  fetch_financials(symbol, start=None, end=None, report_types=None) -> list[FinancialReport]
  fetch_statement_items(symbol, statement_type, start=None, end=None) -> list[StatementItem]
  fetch_revenue_composition(symbol, dimension=None, start=None, end=None) -> list[RevenueComposition]

AnnouncementPort     capability="announcement"
  search_announcements(symbol, keyword=None, start=None, end=None, page=1, size=20) -> Page[Announcement]
  fetch_document(symbol, doc_type, period) -> Document     # 下载 PDF 并回填 local_path
  list_documents(symbol, doc_type=None, start=None, end=None) -> list[Document]

SentimentPort        capability="sentiment"
  fetch_sentiment(symbol, start=None, end=None) -> list[Sentiment]

HoldingPort          capability="holding"
  fetch_circulating_holders(symbol, report_date=None) -> list[CirculatingHolder]
  fetch_fund_holdings(symbol, end_date=None) -> list[FundHolding]

ResearchPort         capability="research"
  fetch_reports(symbol, start=None, end=None) -> list[ResearchReport]

IndustryPort         capability="industry"
  fetch_stock_industry(symbol) -> IndustryBoard
  list_industries(date=None) -> list[IndustryBoard]

LitigationPort       capability="litigation"
  search_litigations(symbol, start=None, end=None) -> list[Litigation]

DividendPort         capability="dividend"
  fetch_dividends(symbol, start=None, end=None) -> list[DividendRecord]

CapitalFlowPort      capability="capitalflow"
  fetch_capital_flows(symbol, start=None, end=None) -> list[CapitalFlow]
  fetch_northbound(symbol, start=None, end=None) -> list[NorthboundHolding]

HotspotPort          capability="hotspot"
  fetch_hotspots(date=None) -> list[Hotspot]

OfficialPort         capability="official"
  fetch_official_updates(symbol, start=None, end=None) -> list[OfficialUpdate]

CommodityPort        capability="commodity"
  fetch_product_prices(product_code, source="sina", start=None, end=None) -> list[ProductPrice]
  search_products(keyword) -> list[StockProduct]

ForecastPort         capability="forecast"
  fetch_earnings_forecasts(symbol, start=None, end=None) -> list[EarningsForecast]
  fetch_consensus_estimates(symbol) -> list[ConsensusEstimate]
  fetch_report_calendar(symbol) -> list[ReportCalendarItem]

GovernancePort       capability="governance"
  fetch_corporate_actions(symbol, start=None, end=None) -> list[CorporateAction]
  fetch_insider_trades(symbol, start=None, end=None) -> list[InsiderTrade]
  fetch_shareholder_count(symbol, start=None, end=None) -> list[ShareholderCount]
  fetch_pledges(symbol) -> list[Pledge]
  fetch_unlocks(symbol) -> list[Unlock]

MacroPort            capability="macro"
  fetch_macro_indicators(codes=None, start=None, end=None) -> list[MacroIndicator]

IntegrityPort        capability="integrity"
  fetch_integrity_records(symbol, start=None, end=None) -> list[IntegrityRecord]
```

## 4. 分析计算模型（analysis/）

```text
ReturnResult(start_date, end_date, price_return, dividend_return,
             total_return, total_return_reinvested, cagr, cagr_reinvested,
             cumulative_dividends, dividend_yield_series)
ValuationResult(method, fair_value_low, fair_value_high, current_price,
                margin_of_safety, assumptions, inputs)
DimensionScore(dimension, score, weight, detail)
CompositeScore(total, rating, dimensions, red_flags)
RedFlag(flag_type, severity, description)
MasterVerdict(symbol, master, score, verdict, signals, narrative)
PeerPosition(symbol, name, industry, market_cap, revenue, roe, gross_margin, moat_rating, x, y)
MoatAssessment(symbol, rating, evidence, width)
PriceCorrelation(product_code, period, correlation, beta, note)
PriceCompare(product_code, price_series, stock_series, correlation)
HolderPosition(holder_name, holder_category, symbol, report_date, hold_num, hold_ratio, change_num, change_ratio)
CompanyComparison(symbol, name, industry, revenue_cagr, market_share, market_share_trend,
                  gross_margin, net_margin, roe, dividend_payout_ratio, dividend_yield,
                  market_cap_change, moat_score, margin_of_safety, cycle_position)
IndustryReport(industry, as_of, companies, dimension_summary)
DelistingRisk(symbol, score, triggers, note)
FraudScore(symbol, score, signals, note)
FinancialReviewReport(symbol, summary, findings, integrity_summary, score)
```

## 5. Web API 端点

所有端点前缀 `/api`，返回 JSON。

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/stocks/search?q=` | `list[Stock]` |
| GET | `/api/stock/{symbol}/overview` | `Stock + CompanyProfile + Snapshot` |
| GET | `/api/stock/{symbol}/kline?start=&end=` | `list[KlineBar]`（前端派生复权价） |
| GET | `/api/stock/{symbol}/valuations?start=&end=` | `list[ValuationPoint]`（含分析层计算的历史分位） |
| GET | `/api/stock/{symbol}/financials?start=&end=&report_types=` | `list[FinancialReport]` |
| GET | `/api/stock/{symbol}/statement?type=income|balance|cashflow&start=&end=` | `list[StatementItem]` |
| GET | `/api/stock/{symbol}/revenue-composition?dimension=&start=&end=` | `list[RevenueComposition]` |
| GET | `/api/stock/{symbol}/announcements?keyword=&start=&end=&page=&size=` | `Page[Announcement]` |
| GET | `/api/stock/{symbol}/documents?doc_type=&start=&end=` | `list[Document]` |
| GET | `/api/stock/{symbol}/holders?report_date=` | `list[CirculatingHolder]` |
| GET | `/api/stock/{symbol}/fund-holdings?end_date=` | `list[FundHolding]` |
| GET | `/api/stock/{symbol}/sentiment?start=&end=` | `list[Sentiment]` |
| GET | `/api/stock/{symbol}/research?start=&end=` | `list[ResearchReport]` |
| GET | `/api/stock/{symbol}/litigations?start=&end=` | `list[Litigation]` |
| GET | `/api/stock/{symbol}/dividends?start=&end=` | `list[DividendRecord]` |
| GET | `/api/stock/{symbol}/capital-flows?start=&end=` | `list[CapitalFlow]` |
| GET | `/api/stock/{symbol}/return?start=&end=&reinvest=` | `ReturnResult` |
| GET | `/api/stock/{symbol}/valuation?method=&params=` | `list[ValuationResult]` |
| GET | `/api/stock/{symbol}/score` | `CompositeScore` |
| GET | `/api/stock/{symbol}/masters?names=` | `list[MasterVerdict]` |
| GET | `/api/stock/{symbol}/official-updates` | `list[OfficialUpdate]` |
| GET | `/api/stock/{symbol}/products` | `list[StockProduct]` |
| GET | `/api/stock/{symbol}/price-compare?product=&start=&end=` | `PriceCompare（价格序列 + 股价序列 + 相关性）` |
| GET | `/api/stock/{symbol}/earnings-forecasts` | `list[EarningsForecast]` |
| GET | `/api/stock/{symbol}/consensus` | `list[ConsensusEstimate]` |
| GET | `/api/stock/{symbol}/report-calendar` | `list[ReportCalendarItem]` |
| GET | `/api/stock/{symbol}/unlocks` | `list[Unlock]` |
| GET | `/api/stock/{symbol}/pledges` | `list[Pledge]` |
| GET | `/api/stock/{symbol}/corporate-actions` | `list[CorporateAction]` |
| GET | `/api/stock/{symbol}/insider-trades` | `list[InsiderTrade]` |
| GET | `/api/stock/{symbol}/shareholder-count` | `list[ShareholderCount]` |
| GET | `/api/stock/{symbol}/northbound` | `list[NorthboundHolding]` |
| GET | `/api/stock/{symbol}/integrity` | `list[IntegrityRecord]` |
| GET | `/api/stock/{symbol}/review` | `FinancialReviewReport` |
| GET | `/api/holders/track?name=&category=` | `list[HolderPosition]` |
| GET | `/api/macro?codes=&start=&end=` | `list[MacroIndicator]` |
| GET | `/api/calendar?start=&end=` | 聚合事件（财报/解禁/分红/股东大会） |
| GET | `/api/industry?date=` | `list[IndustryBoard]` |
| GET | `/api/industry/{industry}/peers` | `list[PeerPosition]` |
| GET | `/api/industry/{industry}/report` | `IndustryReport` |
| GET | `/api/hotspots?date=` | `list[Hotspot]` |
| GET | `/api/compare?symbols=a,b,c` | 多股对比汇总 |
| GET | `/api/screener?filters=` | `list[Stock + CompositeScore]` |
| GET | `/api/batch/report?industry=&symbols=` | `IndustryReport` |

## 6. 页面路由

| 路径 | 页面 |
|---|---|
| `/report?symbol=` | `pages/report.html` |
| `/compare` | `pages/compare.html` |
| `/screener` | `pages/screener.html` |
| `/calculator?symbol=` | `pages/calculator.html` |
| `/financials?symbol=` | `pages/financials.html` |
| `/industry?name=` | `pages/industry.html` |
| `/products?symbol=` | `pages/products.html` |
| `/holders?symbol=` | `pages/holders.html` |
| `/calendar` | `pages/calendar.html` |
| `/review?symbol=` | `pages/review.html` |
| `/integrity?symbol=` | `pages/integrity.html` |
| `/charts?symbol=` | `pages/charts.html`（可选） |

## 7. 已锁定决策

- Port 只返回 `models` 类型；复权价派生逻辑放在 `analysis`，不放在 provider。
- `fetch_kline` 返回不复权 + `adj_factor`；前/后复权由 `analysis.return` 或前端按因子派生。
- 评分、估值、回报均为实时计算，通过 `/api/*` 返回，不落库。
