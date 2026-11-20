"""仓储层行业一对多关系测试。"""

from __future__ import annotations

import sqlite3
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


class TestMigrationTransaction(unittest.TestCase):
    def test_fresh_db_has_schema_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.init_db(Path(tmp) / "fresh.db")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, db.SCHEMA_VERSION)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({"stocks", "snapshots", "industries"} <= tables)
            conn.close()

    def test_failed_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v2.db"
            conn = db.connect(path)
            conn.execute("PRAGMA user_version = 2")
            conn.execute("CREATE TABLE probe (id INTEGER)")
            conn.commit()
            conn.close()

            original = db._MIGRATIONS.get(2)
            db._MIGRATIONS[2] = [
                "ALTER TABLE probe ADD COLUMN added TEXT",
                "THIS IS NOT VALID SQL",
            ]
            try:
                with self.assertRaises(sqlite3.Error):
                    db.init_db(path)
            finally:
                if original is None:
                    db._MIGRATIONS.pop(2, None)
                else:
                    db._MIGRATIONS[2] = original

            conn = db.connect(path)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            columns = [row[1] for row in conn.execute("PRAGMA table_info(probe)")]
            conn.close()
            self.assertEqual(version, 2, "迁移失败后版本号必须回滚")
            self.assertNotIn("added", columns, "迁移失败后已执行的 ALTER 必须回滚")


if __name__ == "__main__":
    unittest.main()
