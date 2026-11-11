-- value-compass 数据库结构，与 SCHEMA.md 保持一致。

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

CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  fetched_at TEXT NOT NULL,
  price REAL,
  market_cap REAL,
  turnover_rate REAL
);

CREATE INDEX idx_snapshots_stock_time ON snapshots(stock_id, fetched_at);

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

CREATE TABLE stock_products (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'sina',
  category TEXT,
  UNIQUE(stock_id, product_code, source)
);

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

CREATE TABLE report_calendar (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  report_type TEXT NOT NULL,
  report_period TEXT,
  planned_date TEXT,
  actual_date TEXT,
  UNIQUE(stock_id, report_type, report_period)
);

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

CREATE TABLE shareholder_count (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  end_date TEXT NOT NULL,
  holder_count INTEGER,
  UNIQUE(stock_id, end_date)
);

CREATE TABLE macro_indicators (
  id INTEGER PRIMARY KEY,
  indicator_code TEXT NOT NULL,
  indicator_name TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  value REAL,
  source TEXT,
  UNIQUE(indicator_code, trade_date)
);

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
