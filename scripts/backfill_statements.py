"""只重拉三大报表并覆盖入库，修复分块漏年 / 新增字段未落库的问题。

用法：python scripts/backfill_statements.py 000858.SZ 600519.SH
"""

from __future__ import annotations

import argparse
import sqlite3

from fundamental.config import paths
from fundamental.models import StatementType
from fundamental.providers.financial.eastmoney import EastmoneyFinancialProvider
from fundamental.utils.symbols import parse_symbol

COLUMNS = [
    "stock_id", "report_date", "report_type", "statement_type",
    "item_code", "item_name", "item_value", "item_yoy",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="重拉三大报表覆盖入库")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 000858.SZ")
    args = parser.parse_args()

    conn = sqlite3.connect(paths.db_path())
    provider = EastmoneyFinancialProvider()
    for symbol in args.symbols:
        code, _market = parse_symbol(symbol)
        row = conn.execute("SELECT id FROM stocks WHERE code = ?", (code,)).fetchone()
        if not row:
            print(f"{symbol}: 库内无此股票，跳过")
            continue
        stock_id = row[0]
        collected: list[tuple] = []
        for statement_type in (StatementType.INCOME, StatementType.BALANCE, StatementType.CASHFLOW):
            for item in provider.fetch_statement_items(symbol, statement_type):
                collected.append((
                    stock_id, item.report_date, item.report_type.value,
                    item.statement_type.value, item.item_code, item.item_name,
                    item.item_value, item.item_yoy,
                ))
        # REPLACE：同一 (stock_id,report_date,report_type,statement_type,item_code) 覆盖写入，
        # 既补齐缺失年份，也刷新新增的资本开支/利息/经营性营运资本字段。
        conn.executemany(
            "INSERT OR REPLACE INTO statement_items "
            "(" + ",".join(COLUMNS) + ") VALUES (" + ",".join("?" for _ in COLUMNS) + ")",
            collected,
        )
        conn.commit()
        dates = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT report_date FROM statement_items WHERE stock_id=? "
            "AND statement_type='balance' AND report_type='FY'", (stock_id,)
        )})
        has_2024 = "2024-12-31" in dates
        capex = conn.execute(
            "SELECT item_value FROM statement_items WHERE stock_id=? AND report_date='2025-12-31' "
            "AND item_code='CONSTRUCT_LONG_ASSET'", (stock_id,)
        ).fetchone()
        print(f"{symbol}: 写入 {len(collected)} 行；最新年报资产负债表年份 {dates[-4:]}；"
              f"含2024={has_2024}；2025资本开支={capex[0] if capex else None}")


if __name__ == "__main__":
    main()
