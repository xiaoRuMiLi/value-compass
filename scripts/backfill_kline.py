"""补齐个股 K 线历史（幂等，只写 kline 表）。

用法：python scripts/backfill_kline.py 00700.HK [--start 1990-01-01]

背景：早期某些拉取走了单次上限 1000 条的降级源，导致 K 线只覆盖近端窗口
（例如腾讯库里只有 2022-11 之后的 1000 根），而财报数据从 2001 年就有，
财务页按报告期聚合 K 线时早期报告期全部无股价。本脚本用当前 provider 链
重新拉全区间，INSERT OR IGNORE 幂等写回，只补历史、不改其它表。
"""

from __future__ import annotations

import argparse
from datetime import date

from fundamental.config import load_settings
from fundamental.providers.registry import make_provider_chain
from fundamental.services.sync import fetch_with_fallback
from fundamental.utils.paths import db_path
from fundamental.utils.symbols import parse_symbol
from fundamental.storage import db, repository

KLINE_COLS = [
    "stock_id", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj_factor"
]


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐个股 K 线历史")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 00700.HK")
    parser.add_argument("--start", default="1990-01-01", help="K 线起始日期")
    args = parser.parse_args()

    conn = db.init_db(db_path())
    quote_chain = make_provider_chain(load_settings(), "quote")
    today = date.today().isoformat()
    try:
        for symbol in args.symbols:
            code, market = parse_symbol(symbol)
            stock_id = repository.get_stock_id(conn, code, market)
            if stock_id is None:
                print(f"{symbol}: 库内无此股票，跳过（请先运行 fetch_data）")
                continue
            before = conn.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM kline WHERE stock_id = ?",
                (stock_id,),
            ).fetchone()

            bars = fetch_with_fallback(quote_chain, "fetch_kline", symbol, args.start, today)
            if not bars:
                print(f"{symbol}: 行情源未返回 K 线，跳过")
                continue
            inserted = repository.insert_many(
                conn, "kline", KLINE_COLS,
                [
                    (stock_id, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.amount, b.adj_factor)
                    for b in bars
                ],
            )
            conn.commit()  # insert_many 不提交事务，需显式 commit 才会落库
            after = conn.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM kline WHERE stock_id = ?",
                (stock_id,),
            ).fetchone()
            print(
                f"{symbol}: 拉取 {len(bars)} 根 / 新增 {inserted} 根；"
                f"覆盖 {before[1]}~{before[2]} ({before[0]}) → {after[1]}~{after[2]} ({after[0]})"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
