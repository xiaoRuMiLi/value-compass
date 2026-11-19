"""数据同步：取数 → 归一化 → 入库。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from fundamental.config import load_settings
from fundamental.models import StatementType
from fundamental.providers.announcement.cninfo import CninfoAnnouncementProvider
from fundamental.providers.capitalflow.eastmoney import EastmoneyCapitalFlowProvider
from fundamental.providers.commodity.sina import SinaCommodityProvider
from fundamental.providers.dividend.eastmoney import EastmoneyDividendProvider
from fundamental.providers.financial.eastmoney import EastmoneyFinancialProvider
from fundamental.providers.forecast.eastmoney import EastmoneyForecastProvider
from fundamental.providers.governance.eastmoney import EastmoneyGovernanceProvider
from fundamental.providers.holding.eastmoney import EastmoneyHoldingProvider
from fundamental.providers.hotspot.eastmoney import EastmoneyHotspotProvider
from fundamental.providers.industry.eastmoney import EastmoneyIndustryProvider
from fundamental.providers.integrity.cninfo import CninfoIntegrityProvider
from fundamental.providers.macro.eastmoney import EastmoneyMacroProvider
from fundamental.providers.official.official import OfficialSiteProvider
from fundamental.providers.quote.eastmoney import EastmoneyQuoteProvider
from fundamental.providers.research.eastmoney import EastmoneyResearchProvider
from fundamental.providers.sentiment.guba import GubaSentimentProvider
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol


def _ev(value: Any) -> Any:
    return value.value if value is not None else None


def _insert(conn: Any, table: str, columns: list[str], models: list[Any], to_row: Callable) -> int:
    return repository.insert_many(conn, table, columns, [to_row(model) for model in models])


def sync_stock(
    symbol: str,
    db_path: Path | None = None,
    kline_start: str = "1990-01-01",
) -> dict[str, int]:
    """拉取单只股票全部数据并写入 SQLite，返回各表写入行数。"""
    conn = db.init_db(db_path)
    quote = EastmoneyQuoteProvider()
    financial = EastmoneyFinancialProvider()

    stock = quote.fetch_stock_info(symbol)
    stock_id = repository.get_or_create_stock(conn, stock)
    today = date.today().isoformat()
    stats: dict[str, int] = {}

    # 行情与估值
    snapshot = quote.fetch_snapshot(symbol)
    repository.insert_many(
        conn, "snapshots", ["stock_id", "fetched_at", "price", "market_cap", "turnover_rate", "pct_change"],
        [(stock_id, snapshot.fetched_at, snapshot.price, snapshot.market_cap, snapshot.turnover_rate, snapshot.pct_change)],
    )
    stats["snapshots"] = 1

    bars = quote.fetch_kline(symbol, kline_start, today)
    stats["kline"] = _insert(
        conn, "kline", ["stock_id", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj_factor"],
        bars, lambda b: (stock_id, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.amount, b.adj_factor),
    )
    valuations = quote.fetch_valuations(symbol)
    stats["valuations"] = _insert(
        conn, "valuations",
        ["stock_id", "trade_date", "close_price", "pe_ttm", "pe_lar", "pb_mrq", "ps_ttm", "peg", "pcf_ocf_ttm", "total_market_cap"],
        valuations, lambda v: (stock_id, v.trade_date, v.close_price, v.pe_ttm, v.pe_lar, v.pb_mrq, v.ps_ttm, v.peg, v.pcf_ocf_ttm, v.total_market_cap),
    )

    # 财务与三大报表
    financials = financial.fetch_financials(symbol)
    stats["financials"] = _insert(
        conn, "financials",
        ["stock_id", "report_date", "report_type", "revenue", "net_profit", "net_profit_deducted", "gross_margin", "net_margin", "roe", "roic", "debt_ratio", "current_ratio", "ocf", "fcf", "eps", "bps", "revenue_yoy", "net_profit_yoy", "audit_opinion"],
        financials, lambda f: (stock_id, f.report_date, f.report_type.value, f.revenue, f.net_profit, f.net_profit_deducted, f.gross_margin, f.net_margin, f.roe, f.roic, f.debt_ratio, f.current_ratio, f.ocf, f.fcf, f.eps, f.bps, f.revenue_yoy, f.net_profit_yoy, _ev(f.audit_opinion)),
    )
    statement_rows = []
    for statement_type in (StatementType.INCOME, StatementType.BALANCE, StatementType.CASHFLOW):
        statement_rows.extend(financial.fetch_statement_items(symbol, statement_type))
    stats["statement_items"] = _insert(
        conn, "statement_items",
        ["stock_id", "report_date", "report_type", "statement_type", "item_code", "item_name", "item_value", "item_yoy"],
        statement_rows, lambda s: (stock_id, s.report_date, s.report_type.value, s.statement_type.value, s.item_code, s.item_name, s.item_value, s.item_yoy),
    )
    composition = financial.fetch_revenue_composition(symbol)
    stats["revenue_composition"] = _insert(
        conn, "revenue_composition", ["stock_id", "report_date", "dimension", "item_name", "revenue", "ratio"],
        composition, lambda r: (stock_id, r.report_date, r.dimension.value, r.item_name, r.revenue, r.ratio),
    )

    # 分红 / 持仓 / 诚信（分析关键）
    dividends = EastmoneyDividendProvider().fetch_dividends(symbol)
    stats["dividends"] = _insert(
        conn, "dividends",
        ["stock_id", "plan_notice_date", "ex_date", "record_date", "pay_date", "cash_per_share", "bonus_ratio", "transfer_ratio"],
        dividends, lambda d: (stock_id, d.plan_notice_date, d.ex_date, d.record_date, d.pay_date, d.cash_per_share, d.bonus_ratio, d.transfer_ratio),
    )
    holders = EastmoneyHoldingProvider().fetch_circulating_holders(symbol)
    stats["circulating_holders"] = _insert(
        conn, "circulating_holders",
        ["stock_id", "report_date", "rank", "holder_name", "holder_category", "hold_num", "hold_ratio", "change_num", "change_ratio", "is_new", "market_value"],
        holders, lambda h: (stock_id, h.report_date, h.rank, h.holder_name, _ev(h.holder_category), h.hold_num, h.hold_ratio, h.change_num, h.change_ratio, int(h.is_new), h.market_value),
    )
    integrity = CninfoIntegrityProvider().fetch_integrity_records(symbol)
    stats["integrity_records"] = _insert(
        conn, "integrity_records",
        ["stock_id", "record_date", "behavior_type", "category", "title", "description", "severity", "source", "source_url"],
        integrity, lambda i: (stock_id, i.record_date, i.behavior_type.value, _ev(i.category), i.title, i.description, _ev(i.severity), i.source, i.source_url),
    )

    # 研报 / 宏观 / 行业 / 资金流 / 热点 / 预期差 / 治理 / 公告 / 情绪 / 官网
    reports = EastmoneyResearchProvider().fetch_reports(symbol)
    stats["research_reports"] = _insert(
        conn, "research_reports",
        ["stock_id", "publish_date", "org_name", "analyst", "rating", "rating_change", "target_price", "title", "source_url"],
        reports, lambda r: (stock_id, r.publish_date, r.org_name, r.analyst, _ev(r.rating), _ev(r.rating_change), r.target_price, r.title, r.source_url),
    )
    macro = EastmoneyMacroProvider().fetch_macro_indicators(["cpi", "pmi"])
    stats["macro_indicators"] = _insert(
        conn, "macro_indicators", ["indicator_code", "indicator_name", "trade_date", "value", "source"],
        macro, lambda m: (m.indicator_code, m.indicator_name, m.trade_date, m.value, m.source),
    )
    industries = EastmoneyIndustryProvider().list_industries()
    stats["industry"] = _insert(
        conn, "industry", ["industry_code", "industry_name", "trade_date", "pct_change", "capital_flow", "pe", "pb", "leader_stock"],
        industries, lambda i: (i.industry_code, i.industry_name, i.trade_date, i.pct_change, i.capital_flow, i.pe, i.pb, i.leader_stock),
    )
    flows = EastmoneyCapitalFlowProvider().fetch_capital_flows(symbol)
    stats["capital_flows"] = _insert(
        conn, "capital_flows", ["stock_id", "trade_date", "main_net", "super_large_net", "large_net", "medium_net", "small_net", "main_net_ratio"],
        flows, lambda f: (stock_id, f.trade_date, f.main_net, f.super_large_net, f.large_net, f.medium_net, f.small_net, f.main_net_ratio),
    )
    northbound = EastmoneyCapitalFlowProvider().fetch_northbound(symbol)
    stats["northbound"] = _insert(
        conn, "northbound", ["stock_id", "trade_date", "hold_shares", "hold_ratio", "change_shares", "change_ratio"],
        northbound, lambda n: (stock_id, n.trade_date, n.hold_shares, n.hold_ratio, n.change_shares, n.change_ratio),
    )
    hotspots = EastmoneyHotspotProvider().fetch_hotspots()
    stats["hotspots"] = _insert(
        conn, "hotspots", ["trade_date", "board_code", "board_name", "pct_change", "main_net", "leader_stock_code", "leader_pct_change"],
        hotspots, lambda h: (h.trade_date, h.board_code, h.board_name, h.pct_change, h.main_net, h.leader_stock_code, h.leader_pct_change),
    )

    forecast = EastmoneyForecastProvider()
    forecasts = forecast.fetch_earnings_forecasts(symbol)
    stats["earnings_forecasts"] = _insert(
        conn, "earnings_forecasts",
        ["stock_id", "report_date", "forecast_type", "notice_date", "forecast_net_profit_low", "forecast_net_profit_high", "net_profit_yoy_low", "net_profit_yoy_high", "content", "source_url"],
        forecasts, lambda f: (stock_id, f.report_date, _ev(f.forecast_type), f.notice_date, f.forecast_net_profit_low, f.forecast_net_profit_high, f.net_profit_yoy_low, f.net_profit_yoy_high, f.content, f.source_url),
    )
    consensus = forecast.fetch_consensus_estimates(symbol)
    stats["consensus_estimates"] = _insert(
        conn, "consensus_estimates", ["stock_id", "report_date", "est_revenue", "est_net_profit", "est_eps", "target_price", "rating", "org_count"],
        consensus, lambda c: (stock_id, c.report_date, c.est_revenue, c.est_net_profit, c.est_eps, c.target_price, _ev(c.rating), c.org_count),
    )
    calendar = forecast.fetch_report_calendar(symbol)
    stats["report_calendar"] = _insert(
        conn, "report_calendar", ["stock_id", "report_type", "report_period", "planned_date", "actual_date"],
        calendar, lambda c: (stock_id, c.report_type.value, c.report_period, c.planned_date, c.actual_date),
    )

    governance = EastmoneyGovernanceProvider()
    actions = governance.fetch_corporate_actions(symbol)
    stats["corporate_actions"] = _insert(
        conn, "corporate_actions", ["stock_id", "action_date", "action_type", "amount", "shares", "price", "status", "source_url"],
        actions, lambda a: (stock_id, a.action_date, a.action_type.value, a.amount, a.shares, a.price, a.status, a.source_url),
    )
    insiders = governance.fetch_insider_trades(symbol)
    stats["insider_trades"] = _insert(
        conn, "insider_trades", ["stock_id", "trade_date", "insider_name", "position", "change_shares", "change_ratio", "avg_price", "direction"],
        insiders, lambda i: (stock_id, i.trade_date, i.insider_name, i.position, i.change_shares, i.change_ratio, i.avg_price, _ev(i.direction)),
    )
    holder_counts = governance.fetch_shareholder_count(symbol)
    stats["shareholder_count"] = _insert(
        conn, "shareholder_count", ["stock_id", "end_date", "holder_count"],
        holder_counts, lambda h: (stock_id, h.end_date, h.holder_count),
    )
    pledges = governance.fetch_pledges(symbol)
    stats["pledges"] = _insert(
        conn, "pledges", ["stock_id", "holder_name", "pledge_date", "pledge_shares", "pledge_ratio", "pledge_total_ratio", "status"],
        pledges, lambda p: (stock_id, p.holder_name, p.pledge_date, p.pledge_shares, p.pledge_ratio, p.pledge_total_ratio, p.status),
    )
    unlocks = governance.fetch_unlocks(symbol)
    stats["unlocks"] = _insert(
        conn, "unlocks", ["stock_id", "unlock_date", "unlock_shares", "unlock_ratio", "unlock_market_value", "holder_type"],
        unlocks, lambda u: (stock_id, u.unlock_date, u.unlock_shares, u.unlock_ratio, u.unlock_market_value, u.holder_type),
    )

    announcements = CninfoAnnouncementProvider().search_announcements(symbol, page=1, size=500)
    stats["announcements"] = _insert(
        conn, "announcements", ["stock_id", "title", "category", "publish_date", "source", "source_url", "pdf_url"],
        announcements.items, lambda a: (stock_id, a.title, a.category, a.publish_date, a.source, a.source_url, a.pdf_url),
    )
    sentiment = GubaSentimentProvider().fetch_sentiment(symbol)
    stats["sentiment"] = _insert(
        conn, "sentiment", ["stock_id", "stat_date", "post_count", "read_count", "reply_count", "hot_score", "sentiment_score"],
        sentiment, lambda s: (stock_id, s.stat_date, s.post_count, s.read_count, s.reply_count, s.hot_score, s.sentiment_score),
    )
    official = OfficialSiteProvider().fetch_official_updates(symbol)
    stats["official_updates"] = _insert(
        conn, "official_updates", ["stock_id", "fetched_at", "category", "title", "content", "url"],
        official, lambda o: (stock_id, o.fetched_at, o.category, o.title, o.content, o.url),
    )

    conn.commit()
    conn.close()
    return stats


def sync_products(db_path: Path | None = None) -> dict[str, int]:
    """按 config/products.toml 同步股票-商品映射与商品价格。"""
    products = load_settings().get("products") or []
    conn = db.init_db(db_path)
    commodity = SinaCommodityProvider()
    stats = {"stock_products": 0, "product_prices": 0}
    seen: set[tuple[str, str]] = set()
    for item in products:
        code, market = parse_symbol(item.get("stock", ""))
        stock_id = repository.get_stock_id(conn, code, market)
        if stock_id is None:
            continue
        product_code = item.get("product_code")
        source = item.get("source", "sina")
        repository.insert_many(
            conn,
            "stock_products",
            ["stock_id", "product_code", "product_name", "source", "category"],
            [(stock_id, product_code, item.get("product_name"), source, item.get("category"))],
        )
        stats["stock_products"] += 1
        if (product_code, source) in seen:
            continue
        seen.add((product_code, source))
        prices = commodity.fetch_product_prices(product_code, source)
        stats["product_prices"] += _insert(
            conn,
            "product_prices",
            ["product_code", "source", "trade_date", "open", "high", "low", "close", "volume"],
            prices,
            lambda p: (p.product_code, p.source, p.trade_date, p.open, p.high, p.low, p.close, p.volume),
        )
    conn.commit()
    conn.close()
    return stats
