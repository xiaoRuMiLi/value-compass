"""仓储层行业一对多关系测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fundamental.models import Market, Stock
from fundamental.storage import db, repository


class TestIndustryRelation(unittest.TestCase):
    def test_one_industry_many_stocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.init_db(Path(tmp) / "t.db")
            a = repository.get_or_create_stock(
                conn, Stock("600519", Market.SH, "贵州茅台", industry="酿酒行业")
            )
            b = repository.get_or_create_stock(
                conn, Stock("000858", Market.SZ, "五粮液", industry="酿酒行业")
            )
            c = repository.get_or_create_stock(
                conn, Stock("600036", Market.SH, "招商银行", industry="银行")
            )

            baijiu = repository.get_or_create_industry(conn, "酿酒行业")
            repository.set_stock_industry(conn, a, baijiu, "酿酒行业", None)
            repository.set_stock_industry(conn, b, baijiu, "酿酒行业", None)
            bank = repository.get_or_create_industry(conn, "银行")
            repository.set_stock_industry(conn, c, bank, "银行", None)

            peers = conn.execute(
                "SELECT code FROM stocks WHERE industry_id = ? AND id != ?", (baijiu, a)
            ).fetchall()
            self.assertEqual([row["code"] for row in peers], ["000858"])
            self.assertEqual(repository.get_or_create_industry(conn, "酿酒行业"), baijiu)
            conn.close()


if __name__ == "__main__":
    unittest.main()
