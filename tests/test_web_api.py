"""Web API 层测试：路由函数成功/错误路径 + 路由注册契约。

不依赖 httpx/TestClient：路由函数本身即 web 层逻辑，直接调用并断言
返回结构与 HTTPException 状态码；同时检查 FastAPI app 已注册关键路由。
数据库通过 VALUE_COMPASS_DB 环境变量隔离到临时文件。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from fundamental.storage import db
from fundamental.web.app import app
from fundamental.web.routes import api


def _seed_db(path: Path) -> None:
    conn = db.init_db(path)
    conn.execute(
        "INSERT INTO stocks (code, market, name, industry, sector) VALUES (?, ?, ?, ?, ?)",
        ("600519", "SH", "贵州茅台", "酿酒行业", "食品饮料"),
    )
    stock_id = conn.execute("SELECT id FROM stocks WHERE code = '600519'").fetchone()[0]
    conn.execute(
        "INSERT INTO company_profile (stock_id, business) VALUES (?, ?)",
        (stock_id, "白酒生产与销售"),
    )
    conn.execute(
        "INSERT INTO snapshots (stock_id, fetched_at, price, market_cap, turnover_rate, pct_change) "
        "VALUES (?, '2026-11-20 10:00:00', 1688.0, 2120000000000, 0.35, 1.25)",
        (stock_id,),
    )
    conn.commit()
    conn.close()


class TestApiRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "test.db"
        _seed_db(self._db_path)
        self._env = patch.dict(os.environ, {"VALUE_COMPASS_DB": str(self._db_path)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_get_stocks_returns_list_with_snapshot(self):
        rows = api.get_stocks()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "600519.SH")
        self.assertEqual(row["name"], "贵州茅台")
        self.assertEqual(row["price"], 1688.0)
        self.assertEqual(row["pct_change"], 1.25)

    def test_get_overview_returns_stock_profile_snapshot(self):
        data = api.get_overview("600519.SH")
        self.assertEqual(data["stock"]["code"], "600519")
        self.assertEqual(data["profile"]["business"], "白酒生产与销售")
        self.assertEqual(data["snapshot"]["price"], 1688.0)

    def test_overview_symbol_without_suffix_defaults_sh(self):
        # parse_symbol 接受裸代码，按 A 股规则解析
        data = api.get_overview("600519")
        self.assertEqual(data["stock"]["name"], "贵州茅台")

    def test_unknown_stock_raises_404(self):
        with self.assertRaises(HTTPException) as ctx:
            api.get_overview("999999.SH")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_symbol_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            api.get_overview("not-a-symbol")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_get_financials_empty_for_seeded_stock(self):
        self.assertEqual(api.get_financials("600519.SH"), [])

    def test_get_masters_narrative_returns_payload(self):
        payload = {
            "symbol": "600519.SH",
            "masters": [{"master": "buffett", "label": "巴菲特", "text": "解读", "source": "llm"}],
            "source": "llm",
            "generated_at": "2026-11-20T00:00:00+00:00",
        }
        with patch.object(api, "generate_masters_narrative", return_value=payload) as fake:
            data = api.get_masters_narrative("600519.SH")
        self.assertEqual(data, payload)
        fake.assert_called_once_with("600519.SH", refresh=False)

    def test_get_masters_narrative_error_raises_404(self):
        with patch.object(
            api, "generate_masters_narrative", return_value={"error": "暂无大师观点"}
        ):
            with self.assertRaises(HTTPException) as ctx:
                api.get_masters_narrative("600519.SH")
        self.assertEqual(ctx.exception.status_code, 404)


class TestAppRoutes(unittest.TestCase):
    """验证 FastAPI app 已挂载关键路由与静态资源。"""

    def setUp(self):
        # FastAPI 0.162 里 include_router 的路由挂在 _IncludedRouter.routes 下，
        # 需递归展开才能拿到 /api/* 的实际路径
        paths = set()

        def collect(routes):
            for route in routes:
                path = getattr(route, "path", None)
                if path:
                    paths.add(path)
                nested = getattr(route, "routes", None)
                if nested:
                    collect(nested)

        collect(app.routes)
        # include_router 的路由在 _IncludedRouter.original_router 下
        for route in app.routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                collect(getattr(original, "routes", []))
        self.paths = paths

    def test_health_and_index_registered(self):
        self.assertIn("/health", self.paths)
        self.assertIn("/", self.paths)

    def test_api_and_page_routes_registered(self):
        for path in (
            "/api/stocks",
            "/api/stock/{symbol}/overview",
            "/api/stock/{symbol}/analysis",
            "/api/stock/{symbol}/masters-narrative",
            "/api/compare",
            "/api/screener",
            "/report",
            "/financials",
            "/calculator",
        ):
            self.assertIn(path, self.paths, f"路由未注册: {path}")

    def test_static_mounted(self):
        self.assertTrue(any("/static" in p for p in self.paths))


if __name__ == "__main__":
    unittest.main()
