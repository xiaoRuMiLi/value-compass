"""港股现金流补数：从财报原文拆分 OCF/资本开支/利息收支，落库到 statement_items。

港股现金流量表长表在东财不存在，但腾讯等公司的业绩公告以固定句式披露季度
「自由现金流 = 经营现金流 − 资本开支 − 媒体内容 − 租赁负债」。本脚本把最近四季
拆分结果按 TTM 口径写入库内，供 analyze / 估值校准作为可追溯的分项来源。

用法：python scripts/backfill_hk_cashflow.py 00700.HK
"""

from __future__ import annotations

import argparse
import sqlite3

from fundamental.config import paths
from fundamental.models import ReportType, StatementType
from fundamental.services.hk_financial_supplement import extract_cashflow_ttm
from fundamental.utils.symbols import parse_symbol

# (item_code, item_name, 抽取结果字段)
_FIELDS = [
    ("NETCASH_OPERATE", "经营活动现金流量净额（财报原文四季汇总）", "ocf_ttm_yi"),
    ("CONSTRUCT_LONG_ASSET", "购建固定资产等支付的现金（资本开支，财报原文四季汇总）", "capex_ttm_yi"),
    ("RECEIVE_INTEREST_COMMISSION", "利息收入（年报损益表）", "interest_income_yi"),
    ("PAY_INTEREST_COMMISSION", "财务成本/利息支出（年报损益表）", "interest_expense_yi"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="港股现金流原文拆分补数")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 00700.HK")
    args = parser.parse_args()

    conn = sqlite3.connect(paths.db_path())
    for symbol in args.symbols:
        code, _market = parse_symbol(symbol)
        row = conn.execute("SELECT id FROM stocks WHERE code = ?", (code,)).fetchone()
        if not row:
            print(f"{symbol}: 库内无此股票，跳过")
            continue
        stock_id = row[0]

        parts = extract_cashflow_ttm(symbol)
        if not parts.get("available"):
            print(f"{symbol}: 原文拆分失败：{parts.get('reason') or parts.get('note')}")
            continue

        report_date = "TTM"
        written = 0
        for item_code, item_name, key in _FIELDS:
            yi = parts.get(key)
            if yi is None:
                print(f"  {item_code}: 未取到，不写 0")
                continue
            conn.execute(
                "INSERT OR REPLACE INTO statement_items "
                "(stock_id, report_date, report_type, statement_type, item_code, item_name, item_value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    stock_id, report_date, ReportType.FY.value,
                    StatementType.CASHFLOW.value, item_code, item_name,
                    float(yi) * 1e8,
                ),
            )
            written += 1
            print(f"  {item_code}: {yi}亿 → 已落库")
        conn.commit()
        print(
            f"{symbol}: 写入 {written} 项；OCF={parts.get('ocf_ttm_yi')}亿 "
            f"资本开支={parts.get('capex_ttm_yi')}亿 "
            f"利息收入={parts.get('interest_income_yi')}亿 "
            f"利息支出={parts.get('interest_expense_yi')}亿"
        )


if __name__ == "__main__":
    main()
