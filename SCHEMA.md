# value-compass 数据库设计

本文档是 SQLite 建库的唯一标准，字段定义与 `ARCHITECTURE.md` 第 11 节对应。

## 1. 通用约定

- 主键统一 `id INTEGER PRIMARY KEY`（自增）。
- 外键统一 `stock_id INTEGER REFERENCES stocks(id) ON DELETE CASCADE`。
- 日期用 `TEXT`，格式 `YYYY-MM-DD`；时间戳用 `TEXT`，ISO-8601 UTC。
- 金额单位：元；持股数量单位：股；比率单位：百分比（如 15.5 表示 15.5%）。
- 所有表用 `UNIQUE` 约束保证重复入库幂等；增量拉取靠 `sync_state` + 唯一约束。
- 数据库文件：`data/db/fundamental.db`。

## 2. 枚举值约定

| 字段 | 取值 |
|---|---|
| `stocks.market` | `SH` / `SZ` / `BJ` / `HK` / `US` |
| `financials.report_type` | `Q1` / `H1` / `Q3` / `FY`（年初至今累计值） |
| `documents.doc_type` | `annual_report` / `semi_report` / `q1_report` / `q3_report` / `announcement` / `prospectus` |
| `documents.status` | `pending` / `downloaded` / `failed` |
| `circulating_holders.holder_category` | `individual` / `famous_trader` / `institution` / `fund` / `social_security` / `insurance` / `qfii` / `other` |
| `litigations.role` | `plaintiff` / `defendant` / `third` |
| `research_reports.rating` | `buy` / `overweight` / `neutral` / `underweight` / `sell` |
| `research_reports.rating_change` | `upgrade` / `downgrade` / `maintain` |
| `revenue_composition.dimension` | `region` / `product` |
| `statement_items.statement_type` | `income` / `balance` / `cashflow` |
| `financials.audit_opinion` | `standard` / `qualified` / `disclaimer` / `adverse` |
| `earnings_forecasts.forecast_type` | `preincrease` / `predecrease` / `turnloss` / `firstloss` / `continueprofit` / `continueloss` / `slightincrease` / `slightdecrease` / `uncertain` |
| `corporate_actions.action_type` | `seo` / `rights` / `convertible` / `buyback` / `incentive` |
| `insider_trades.direction` | `buy` / `sell` |
| `integrity_records.behavior_type` | `positive` / `negative` / `neutral` |
| `integrity_records.category` | `promise_fulfilled` / `broken_promise` / `violation_guarantee` / `fund_occupation` / `penalty` / `inquiry` / `financial_restatement` / `misleading_disclosure` / `honest_operation` / `other` |

## 3. 表结构

### stocks

```sql
CREATE TABLE stocks (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  market TEXT NOT NULL,
  name TEXT NOT NULL,
  industry TEXT,
  sector TEXT,
  list_date TEXT,
  is_st INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(code, market)
);
```

### company_profile

```sql
CREATE TABLE company_profile (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  business TEXT,
  total_shares REAL,
  float_shares REAL,
  prospectus_url TEXT,
  official_website TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id)
);
```

### kline

存储不复权 OHLCV + 复权因子，前复权/后复权价格在分析层派生。

```sql
CREATE TABLE kline (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  trade_date TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  amount REAL NOT NULL,
  adj_factor REAL NOT NULL DEFAULT 1.0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, trade_date)
);

CREATE INDEX idx_kline_stock_date ON kline(stock_id, trade_date);
```

### snapshots

实时行情快照，按时间追加。

```sql
CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  fetched_at TEXT NOT NULL,
  price REAL,
  market_cap REAL,
  turnover_rate REAL
);

CREATE INDEX idx_snapshots_stock_time ON snapshots(stock_id, fetched_at);
```

### valuations

逐日历史估值数据，来自东财 `RPT_VALUEANALYSIS_DET`，历史分位在分析层由全序列计算。

```sql
CREATE TABLE valuations (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  trade_date TEXT NOT NULL,
  close_price REAL,
  pe_ttm REAL,
  pe_lar REAL,
  pb_mrq REAL,
  ps_ttm REAL,
  peg REAL,
  pcf_ocf_ttm REAL,
  total_market_cap REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, trade_date)
);

CREATE INDEX idx_valuations_stock_date ON valuations(stock_id, trade_date);
```

### financials

```sql
CREATE TABLE financials (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  report_type TEXT NOT NULL,
  revenue REAL,
  net_profit REAL,
  net_profit_deducted REAL,
  gross_margin REAL,
  net_margin REAL,
  roe REAL,
  roic REAL,
  debt_ratio REAL,
  current_ratio REAL,
  ocf REAL,
  fcf REAL,
  eps REAL,
  bps REAL,
  revenue_yoy REAL,
  net_profit_yoy REAL,
  audit_opinion TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, report_date, report_type)
);
```

### statement_items

三大报表（利润表/资产负债表/现金流量表）统一用行式存储，新增报表项目无需改表结构。

```sql
CREATE TABLE statement_items (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  report_type TEXT NOT NULL,
  statement_type TEXT NOT NULL,
  item_code TEXT NOT NULL,
  item_name TEXT NOT NULL,
  item_value REAL,
  item_yoy REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, report_date, report_type, statement_type, item_code)
);

CREATE INDEX idx_statement_items_lookup
  ON statement_items(stock_id, statement_type, report_date);
```

常用项目代码（东财字段名 → 中文名）：

| 报表 | item_code | 中文名 |
|---|---|---|
| 现金流量表 | `NETCASH_OPERATE` | 经营活动现金流量净额 |
| 现金流量表 | `NETCASH_INVEST` | 投资活动现金流量净额 |
| 现金流量表 | `NETCASH_FINANCE` | 筹资活动现金流量净额 |
| 现金流量表 | `SALES_SERVICES` | 销售商品、提供劳务收到的现金 |
| 现金流量表 | `CONSTRUCT_LONG_ASSET` | 购建固定资产等支付的现金 |
| 现金流量表 | `ASSIGN_DIVIDEND_PORFIT` | 分配股利、偿付利息支付的现金 |
| 现金流量表 | `CCE_ADD` | 现金及现金等价物净增加额 |
| 资产负债表 | `MONETARYFUNDS` | 货币资金 |
| 资产负债表 | `TOTAL_ASSETS` | 资产总计 |
| 资产负债表 | `TOTAL_LIABILITIES` | 负债合计 |
| 资产负债表 | `TOTAL_PARENT_EQUITY` | 归属母公司股东权益 |
| 利润表 | `TOTAL_OPERATE_INCOME` | 营业总收入 |
| 利润表 | `PARENT_NETPROFIT` | 归母净利润 |
| 利润表 | `DEDUCT_PARENT_NETPROFIT` | 扣非归母净利润 |

### revenue_composition

```sql
CREATE TABLE revenue_composition (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  dimension TEXT NOT NULL,
  item_name TEXT NOT NULL,
  revenue REAL,
  ratio REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, report_date, dimension, item_name)
);
```

### announcements

```sql
CREATE TABLE announcements (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  category TEXT,
  publish_date TEXT,
  source TEXT,
  source_url TEXT,
  pdf_url TEXT,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, source, title)
);
```

### documents

文档本地存档的清单，`local_path` 指向 `data/archive/documents/`。

```sql
CREATE TABLE documents (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  doc_type TEXT NOT NULL,
  period TEXT,
  title TEXT NOT NULL,
  source_url TEXT,
  local_path TEXT,
  file_size INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, doc_type, period)
);
```

### litigations

```sql
CREATE TABLE litigations (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  publish_date TEXT,
  role TEXT,
  amount REAL,
  status TEXT,
  source_url TEXT,
  UNIQUE(stock_id, title, publish_date)
);
```

### circulating_holders

十大流通股东专用表，用于追踪牛散与机构的持仓变化方向。

```sql
CREATE TABLE circulating_holders (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  rank INTEGER,
  holder_name TEXT NOT NULL,
  holder_category TEXT,
  hold_num REAL,
  hold_ratio REAL,
  change_num REAL,
  change_ratio REAL,
  is_new INTEGER NOT NULL DEFAULT 0,
  market_value REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, report_date, rank)
);

CREATE INDEX idx_circulating_holders_name
  ON circulating_holders(holder_name, report_date);
```

`holder_category` 在入库时派生：个人股东、牛散（命中 `config/famous_traders.toml`）、机构、基金、社保、保险、QFII、其他。

### fund_holdings

专存基金重仓明细。

```sql
CREATE TABLE fund_holdings (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  end_date TEXT NOT NULL,
  fund_code TEXT NOT NULL,
  fund_name TEXT,
  hold_num REAL,
  hold_market_value REAL,
  change_num REAL,
  UNIQUE(stock_id, end_date, fund_code)
);
```

### sentiment

同时存原始热度与多空情绪分。

```sql
CREATE TABLE sentiment (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  stat_date TEXT NOT NULL,
  post_count INTEGER,
  read_count INTEGER,
  reply_count INTEGER,
  hot_score REAL,
  sentiment_score REAL,
  UNIQUE(stock_id, stat_date)
);
```

### research_reports

```sql
CREATE TABLE research_reports (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  publish_date TEXT,
  org_name TEXT,
  analyst TEXT,
  rating TEXT,
  rating_change TEXT,
  target_price REAL,
  title TEXT,
  source_url TEXT,
  UNIQUE(stock_id, org_name, publish_date, title)
);
```

### industry

```sql
CREATE TABLE industry (
  id INTEGER PRIMARY KEY,
  industry_code TEXT NOT NULL,
  industry_name TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  pct_change REAL,
  capital_flow REAL,
  pe REAL,
  pb REAL,
  leader_stock TEXT,
  UNIQUE(industry_code, trade_date)
);
```

### dividends

```sql
CREATE TABLE dividends (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  plan_notice_date TEXT,
  ex_date TEXT,
  record_date TEXT,
  pay_date TEXT,
  cash_per_share REAL,
  bonus_ratio REAL,
  transfer_ratio REAL,
  UNIQUE(stock_id, ex_date, pay_date)
);
```

### capital_flows

```sql
CREATE TABLE capital_flows (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  trade_date TEXT NOT NULL,
  main_net REAL,
  super_large_net REAL,
  large_net REAL,
  medium_net REAL,
  small_net REAL,
  main_net_ratio REAL,
  UNIQUE(stock_id, trade_date)
);
```

### hotspots

```sql
CREATE TABLE hotspots (
  id INTEGER PRIMARY KEY,
  trade_date TEXT NOT NULL,
  board_code TEXT NOT NULL,
  board_name TEXT NOT NULL,
  pct_change REAL,
  main_net REAL,
  leader_stock_code TEXT,
  leader_pct_change REAL,
  UNIQUE(trade_date, board_code)
);
```

### stock_products

维护上市公司与商品/产品价格的映射关系，种子数据来自 `config/products.toml`。

```sql
CREATE TABLE stock_products (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'sina',
  category TEXT,
  UNIQUE(stock_id, product_code, source)
);
```

### product_prices

商品/产品价格序列，`product_code + source + trade_date` 唯一。

```sql
CREATE TABLE product_prices (
  id INTEGER PRIMARY KEY,
  product_code TEXT NOT NULL,
  source TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(product_code, source, trade_date)
);

CREATE INDEX idx_product_prices_lookup
  ON product_prices(product_code, source, trade_date);
```

### official_updates

```sql
CREATE TABLE official_updates (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  fetched_at TEXT NOT NULL,
  category TEXT,
  title TEXT,
  content TEXT,
  url TEXT,
  UNIQUE(stock_id, url)
);
```

### earnings_forecasts

业绩预告/快报，用于预期差分析。

```sql
CREATE TABLE earnings_forecasts (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  forecast_type TEXT,
  notice_date TEXT,
  forecast_net_profit_low REAL,
  forecast_net_profit_high REAL,
  net_profit_yoy_low REAL,
  net_profit_yoy_high REAL,
  content TEXT,
  source_url TEXT,
  UNIQUE(stock_id, report_date, notice_date)
);
```

### consensus_estimates

机构一致预期，用于预期差与估值锚。

```sql
CREATE TABLE consensus_estimates (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_date TEXT NOT NULL,
  est_revenue REAL,
  est_net_profit REAL,
  est_eps REAL,
  target_price REAL,
  rating TEXT,
  org_count INTEGER,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(stock_id, report_date)
);
```

### report_calendar

财报披露日历。

```sql
CREATE TABLE report_calendar (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_type TEXT NOT NULL,
  report_period TEXT,
  planned_date TEXT,
  actual_date TEXT,
  UNIQUE(stock_id, report_type, report_period)
);
```

### unlocks

限售解禁。

```sql
CREATE TABLE unlocks (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  unlock_date TEXT NOT NULL,
  unlock_shares REAL,
  unlock_ratio REAL,
  unlock_market_value REAL,
  holder_type TEXT,
  UNIQUE(stock_id, unlock_date)
);
```

### pledges

股权质押。

```sql
CREATE TABLE pledges (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  holder_name TEXT,
  pledge_date TEXT,
  pledge_shares REAL,
  pledge_ratio REAL,
  pledge_total_ratio REAL,
  status TEXT,
  UNIQUE(stock_id, holder_name, pledge_date)
);
```

### corporate_actions

公司行动：增发、配股、可转债、回购、股权激励。

```sql
CREATE TABLE corporate_actions (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  action_date TEXT,
  action_type TEXT NOT NULL,
  amount REAL,
  shares REAL,
  price REAL,
  status TEXT,
  source_url TEXT,
  UNIQUE(stock_id, action_type, action_date)
);
```

### insider_trades

高管/内部人增减持。

```sql
CREATE TABLE insider_trades (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  trade_date TEXT NOT NULL,
  insider_name TEXT,
  position TEXT,
  change_shares REAL,
  change_ratio REAL,
  avg_price REAL,
  direction TEXT,
  UNIQUE(stock_id, insider_name, trade_date)
);
```

### shareholder_count

股东户数变化，反映筹码集中度。

```sql
CREATE TABLE shareholder_count (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  end_date TEXT NOT NULL,
  holder_count INTEGER,
  UNIQUE(stock_id, end_date)
);
```

### macro_indicators

宏观指标：无风险利率、CPI、PPI、PMI 等。

```sql
CREATE TABLE macro_indicators (
  id INTEGER PRIMARY KEY,
  indicator_code TEXT NOT NULL,
  indicator_name TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  value REAL,
  source TEXT,
  UNIQUE(indicator_code, trade_date)
);
```

### northbound

沪深港通持股变化。

```sql
CREATE TABLE northbound (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  trade_date TEXT NOT NULL,
  hold_shares REAL,
  hold_ratio REAL,
  change_shares REAL,
  change_ratio REAL,
  UNIQUE(stock_id, trade_date)
);
```

### integrity_records

诚信记录：用文字描述上市公司的道德行为，正面与负面都记录。

```sql
CREATE TABLE integrity_records (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  record_date TEXT,
  behavior_type TEXT NOT NULL,
  category TEXT,
  title TEXT NOT NULL,
  description TEXT,
  severity TEXT,
  source TEXT,
  source_url TEXT,
  UNIQUE(stock_id, category, title)
);
```

### sync_state

```sql
CREATE TABLE sync_state (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  capability TEXT NOT NULL,
  stock_id INTEGER,
  data_type TEXT,
  last_synced_at TEXT,
  watermark TEXT,
  status TEXT DEFAULT 'ok',
  UNIQUE(provider, capability, stock_id, data_type)
);
```

## 4. 已锁定决策

1. `kline` 存不复权 OHLCV + `adj_factor`，前/后复权在分析层派生。
2. `financials` 存四个报告期：一季报、中报、三季报、年报，数值为年初至今累计。
3. `sentiment` 同时存原始热度（发帖/阅读/回复）与多空情绪分。
4. `circulating_holders` 专存十大流通股东（追踪牛散/机构持仓变化），`fund_holdings` 专存基金重仓明细。
5. 三大报表明细统一存 `statement_items` 行式表，新增报表项目不改表结构。
6. `valuations` 存逐日 PE/PB/PS，历史分位在分析层由全序列计算，不落库。
7. 评分、估值、回报、大师研判、行业对比均实时计算，不落库。
8. `integrity_records` 用文字描述正负面道德行为。
