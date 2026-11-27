"""把港股财报原文披露的单季自由现金流按报告期落库。

用法：python scripts/backfill_fcf_history.py 00700.HK

FCF 由 hk_financial_supplement.extract_fcf_ttm(all_quarters=True) 从归档原文逐季抽取，
不经 TTM 截断。落库到 extracted_financial_items 的 FCF_DISCLOSED 字段，供资产负债表
拆解板块展示历史自由现金流序列。A 股无披露句式，给出提示跳过。
"""

from __future__ import annotations

import argparse
import sqlite3

from fundamental.config import paths
from fundamental.utils.symbols import parse_symbol


def main() -> None:
    parser = argparse.ArgumentParser(description="回填单季自由现金流")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 00700.HK")
    args = parser.parse_args()

    conn = sqlite3.connect(paths.db_path())
    try:
        from fundamental.services.hk_financial_supplement import extract_fcf_ttm

        for symbol in args.symbols:
            code, market = parse_symbol(symbol)
            if market.value != "HK":
                print(f"{symbol}: A 股现金流表按科目计算，原文披露句式回填仅支持港股，跳过")
                continue
            row = conn.execute("SELECT id FROM stocks WHERE code = ?", (code,)).fetchone()
            if not row:
                print(f"{symbol}: 库内无此股票，跳过")
                continue
            stock_id = row[0]

            data = extract_fcf_ttm(symbol, all_quarters=True)
            quarters = data.get("quarters") or []
            if not quarters:
                print(f"{symbol}: 未抽到自由现金流披露句式，跳过")
                continue

            count = 0
            for q in quarters:
                fcf_yi = q.get("fcf_yi")
                report_date = q.get("report_date")
                report_type = q.get("report_type")
                if fcf_yi is None or not report_date or not report_type:
                    continue
                # 公司披露为人民币亿元；value_cny 存元，original_value 存原文亿元
                value_cny = fcf_yi * 1e8
                conn.execute(
                    "INSERT OR REPLACE INTO extracted_financial_items "
                    "(stock_id, field_code, report_date, report_type, value_cny, original_value, "
                    " currency, unit, fx_rate, fx_source, quote, source_doc, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        stock_id, "FCF_DISCLOSED", report_date, report_type,
                        value_cny, fcf_yi, "CNY", "亿元", 1.0, "财报原文人民币披露",
                        q.get("quote"), q.get("source"), "verified",
                    ),
                )
                count += 1
            conn.commit()
            print(
                f"{symbol}: 写入 {count} 个季度 FCF，"
                f"区间 {quarters[0]['report_date']} ~ {quarters[-1]['report_date']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
