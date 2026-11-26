"""从财报原文抽取非标准口径字段，按真实报告期落库。

标准三大表数据走 fetch_data / backfill_statements；本脚本只补原文明确披露、
标准接口没有的桥接资产口径（公允价值类金融资产、权益类投资、长短期存款理财）。
抽取服务负责数字与引文，本脚本负责校验后按报告期写 extracted_financial_items。

用法：
    python scripts/backfill_extracted_fields.py 00700.HK
    python scripts/backfill_extracted_fields.py 00700.HK --dry-run
    python scripts/backfill_extracted_fields.py 000858.SZ --annual-only --dry-run
"""

from __future__ import annotations

import argparse

from fundamental.services.financial_extract import extract_financial_fields
from fundamental.storage import db, repository
from fundamental.utils import paths
from fundamental.utils.symbols import parse_symbol


def main() -> None:
    parser = argparse.ArgumentParser(description="财报原文非标准口径抽取落库")
    parser.add_argument("symbols", nargs="+", help="股票代码，如 00700.HK")
    parser.add_argument("--dry-run", action="store_true", help="只打印抽取结果，不写库")
    parser.add_argument("--max-docs", type=int, default=None, help="最多读几份年报/中报")
    parser.add_argument("--max-chars", type=int, default=None, help="每份文档送入 LLM 的字符预算")
    parser.add_argument("--annual-only", action="store_true", help="只读年报，供现金流明细对齐年报 FCF 基准")
    parser.add_argument("--include-interim", action="store_true", help="覆盖配置，允许读中报")
    args = parser.parse_args()

    from fundamental.config import load_settings
    annual_only = (
        True if args.annual_only
        else False if args.include_interim
        else bool(load_settings().get("financial_extract.annual_only", False))
    )

    conn = db.init_db()
    try:
        for symbol in args.symbols:
            code, market = parse_symbol(symbol)
            stock_id = repository.get_stock_id(conn, code, market)
            if stock_id is None:
                print(f"{symbol}: 库内无此股票，跳过")
                continue
            result = extract_financial_fields(
                symbol, paths.db_path(), max_docs=args.max_docs, max_chars=args.max_chars,
                annual_only=annual_only,
            )
            print(f"\n{symbol}: source={result['source']} 文档={len(result.get('sources') or [])}")
            if result.get("reason"):
                print(f"  跳过原因：{result['reason']}")
            for warning in result.get("warnings") or []:
                print(f"  警告：{warning}")
            written = 0
            for item in result.get("items") or []:
                print(
                    f"  {item['field_code']} {item['report_date']} "
                    f"{item['original_value']}{item['currency']}{item['unit']} "
                    f"→ {item['value_cny'] / 1e8:.2f}亿元 "
                    f"[{item['quote'][:40]}...]"
                )
                if args.dry_run:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO extracted_financial_items "
                    "(stock_id, field_code, report_date, report_type, value_cny, original_value, "
                    " currency, unit, fx_rate, fx_source, quote, source_doc, source_path, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified')",
                    (
                        stock_id, item["field_code"], item["report_date"], item["report_type"],
                        item["value_cny"], item["original_value"], item["currency"], item["unit"],
                        item["fx_rate"], item["fx_source"], item["quote"],
                        item["source_doc"], item["source_path"],
                    ),
                )
                written += 1
            if not args.dry_run:
                conn.commit()
                print(f"  落库 {written} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
