"""补数：把中/美 10 年期国债收益率落库，供 FCFF/WACC 无风险利率锚点使用。

宏观指标是全市场共用数据，不按个股存储（macro_indicators 无 stock_id）。
可重复执行：macro_indicators 对 (indicator_code, trade_date) 有唯一约束，
重复行会被 INSERT OR IGNORE 跳过。

用法：
    .venv/Scripts/python scripts/backfill_bond_yields.py
"""

from __future__ import annotations

from fundamental.providers.macro.eastmoney import EastmoneyMacroProvider
from fundamental.storage import db, repository


def main() -> None:
    provider = EastmoneyMacroProvider()
    rows = provider.fetch_macro_indicators(["cn10y", "us10y"])
    conn = db.init_db()
    try:
        count = repository.insert_many(
            conn,
            "macro_indicators",
            ["indicator_code", "indicator_name", "trade_date", "value", "source"],
            [
                (m.indicator_code, m.indicator_name, m.trade_date, m.value, m.source)
                for m in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()

    by_code: dict[str, list[str]] = {}
    for m in rows:
        by_code.setdefault(m.indicator_code, []).append(f"{m.trade_date}={m.value}%")
    for code in ("cn10y", "us10y"):
        seq = by_code.get(code) or []
        print(f"{code}: {len(seq)} 条，最新 {seq[0] if seq else '无'}")
    print(f"本次新写入 {count} 行")


if __name__ == "__main__":
    main()
