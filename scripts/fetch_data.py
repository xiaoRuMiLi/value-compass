"""只拉数并入库。用法：python scripts/fetch_data.py 600519.SH"""

from __future__ import annotations

import argparse

from fundamental.services.sync import sync_stock


def main() -> None:
    parser = argparse.ArgumentParser(description="拉取股票数据并写入 SQLite")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 600519.SH")
    parser.add_argument("--kline-start", default="1990-01-01", help="K 线起始日期")
    args = parser.parse_args()

    for symbol in args.symbols:
        stats = sync_stock(symbol, kline_start=args.kline_start)
        print(f"{symbol}: {stats}")


if __name__ == "__main__":
    main()
