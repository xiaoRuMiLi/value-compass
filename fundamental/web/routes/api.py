"""最小 API：从 SQLite 读取个股数据。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from fundamental.services.analyze import (
    analyze_stock,
    compare_stocks,
    compute_return_for,
    price_compare,
    screen_stocks,
)
from fundamental.services import document_reader, report_facts
from fundamental.services.balance_breakdown import build_balance_view
from fundamental.services.financial_dashboard import build_financial_dashboard
from fundamental.services.narrative import (
    generate_balance_breakdown_narrative,
    generate_financial_forensics,
    generate_financial_trend_narrative,
    generate_masters_narrative,
    generate_metric_explanation,
    list_cached_explanations,
    generate_overview_narrative,
    generate_score_narrative,
    generate_valuation_narrative,
    generate_valuation_params,
)
from fundamental.services.stock_directory import build_stock_directory
from fundamental.services.valuation_history import build_valuation_history
from fundamental.storage import db, repository
from fundamental.utils.symbols import parse_symbol

router = APIRouter(prefix="/api", tags=["stock"])


def _resolve_stock(conn, symbol: str) -> int:
    try:
        code, market = parse_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stock_id = repository.get_stock_id(conn, code, market)
    if stock_id is None:
        raise HTTPException(status_code=404, detail="股票不存在，请先运行 fetch_data")
    return stock_id


def _rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


@router.get("/stock/{symbol}/overview")
def get_overview(symbol: str) -> dict[str, Any]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        stock = repository.fetch_one(
            conn,
            "SELECT id, code, market, name, industry, sector, list_date, is_st FROM stocks WHERE id = ?",
            (stock_id,),
        )
        snapshot = repository.fetch_one(
            conn,
            "SELECT fetched_at, price, market_cap, turnover_rate FROM snapshots "
            "WHERE stock_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (stock_id,),
        )
        profile = repository.fetch_one(
            conn, "SELECT * FROM company_profile WHERE stock_id = ?", (stock_id,)
        )
        return {
            "stock": dict(stock) if stock else None,
            "profile": dict(profile) if profile else None,
            "snapshot": dict(snapshot) if snapshot else None,
        }
    finally:
        conn.close()


@router.get("/stock/{symbol}/financials")
def get_financials(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    report_types: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = "SELECT * FROM financials WHERE stock_id = ?"
        params: list[Any] = [stock_id]
        if start:
            sql += " AND report_date >= ?"
            params.append(start)
        if end:
            sql += " AND report_date <= ?"
            params.append(end)
        sql += " ORDER BY report_date DESC"
        rows = repository.fetch_all(conn, sql, params)
        wanted = set(report_types.split(",")) if report_types else None
        if wanted:
            rows = [row for row in rows if row["report_type"] in wanted]
        return _rows(rows)
    finally:
        conn.close()


@router.get("/stock/{symbol}/kline")
def get_kline(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = (
            "SELECT trade_date, open, high, low, close, volume, amount, adj_factor "
            "FROM kline WHERE stock_id = ?"
        )
        params: list[Any] = [stock_id]
        if start:
            sql += " AND trade_date >= ?"
            params.append(start)
        if end:
            sql += " AND trade_date <= ?"
            params.append(end)
        sql += " ORDER BY trade_date ASC"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/valuation-history")
def get_valuation_history(symbol: str) -> dict[str, Any]:
    """报告页 K 线叠加用：与交易日等长的 PE/PB/ROE 序列及口径标注。

    web 层只负责取连接与异常映射，对齐/推导由 services → analysis 完成。
    """
    conn = db.init_db()
    try:
        return build_valuation_history(conn, symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/stock/{symbol}/financial-dashboard")
def get_financial_dashboard(symbol: str, frequency: str = "quarter") -> dict[str, Any]:
    """财务页全景数据：主要指标、三大表、分组比率与报告期对齐的 K 线。"""

    try:
        return build_financial_dashboard(symbol, frequency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/stock/{symbol}/balance-breakdown")
def get_balance_breakdown(symbol: str) -> dict[str, Any]:
    """资产负债表拆解：科目分组、占比/同比、投资资产归类、历史 FCF。"""
    try:
        return build_balance_view(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/stock/{symbol}/balance-narrative")
def get_balance_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    """资产负债表分组解读：LLM 写文字，数字由代码算。"""
    return generate_balance_breakdown_narrative(symbol, refresh=refresh)


@router.get("/stock/{symbol}/metric-explanation-index")
def get_metric_explanation_index(symbol: str) -> dict[str, Any]:
    """图表悬停用：一次取回该标的全部已缓存解释的摘要。"""
    return {"items": list_cached_explanations(symbol)}


@router.get("/stock/{symbol}/metric-explanation")
def get_metric_explanation(
    symbol: str,
    period: str,
    keys: str,
    scope: str = "primary",
    stmt_type: str = "income",
    refresh: bool = False,
    cached_only: bool = False,
) -> dict[str, Any]:
    """单期指标解释：代码重建 facts，agent 检索财报原文并解释，按钮触发。"""
    try:
        return generate_metric_explanation(
            symbol,
            period,
            scope,
            [key for key in keys.split(",") if key.strip()],
            stmt_type=stmt_type,
            refresh=refresh,
            cached_only=cached_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/stock/{symbol}/financial-trend-narrative")
def get_financial_trend_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    """财务页趋势叙述：LLM 解释现金流与利润的长期趋势及近期波动。"""

    result = generate_financial_trend_narrative(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=str(result["error"]))
    return result


@router.get("/stock/{symbol}/analysis")
def get_analysis(symbol: str) -> dict[str, Any]:
    result = analyze_stock(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/peers")
def get_peers(symbol: str) -> list[dict[str, Any]]:
    """返回与给定股票同行业的其他个股（一对多关系）。"""

    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        rows = repository.fetch_all(
            conn,
            "SELECT s.code, s.market, s.name, s.industry FROM stocks s "
            "WHERE s.industry_id = (SELECT industry_id FROM stocks WHERE id = ?) "
            "AND s.id != ? ORDER BY s.code",
            (stock_id, stock_id),
        )
        return [
            {"symbol": f"{r['code']}.{r['market']}", "name": r["name"], "industry": r["industry"]}
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/stock/{symbol}/overview-narrative")
def get_overview_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    result = generate_overview_narrative(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/score-narrative")
def get_score_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    result = generate_score_narrative(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/valuation-narrative")
def get_valuation_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    result = generate_valuation_narrative(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/valuation-params")
def get_valuation_params(symbol: str, refresh: bool = False) -> dict[str, Any]:
    result = generate_valuation_params(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/masters-narrative")
def get_masters_narrative(symbol: str, refresh: bool = False) -> dict[str, Any]:
    result = generate_masters_narrative(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stock/{symbol}/financial-forensics")
def get_financial_forensics(symbol: str, refresh: bool = False) -> dict[str, Any]:
    """财报深度体检：确定性取证扫描 + LLM 逐条剖析（契约外扩展端点）。"""

    result = generate_financial_forensics(symbol, refresh=refresh)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/stocks")
def get_stocks() -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        return build_stock_directory(conn)
    finally:
        conn.close()


@router.get("/stock/{symbol}/statement")
def get_statement(
    symbol: str,
    type: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = (
            "SELECT report_date, report_type, statement_type, item_code, item_name, item_value, item_yoy "
            "FROM statement_items WHERE stock_id = ?"
        )
        params: list[Any] = [stock_id]
        if type:
            sql += " AND statement_type = ?"
            params.append(type)
        sql += " ORDER BY report_date DESC, item_code"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/revenue-composition")
def get_revenue_composition(
    symbol: str,
    dimension: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = "SELECT * FROM revenue_composition WHERE stock_id = ?"
        params: list[Any] = [stock_id]
        if dimension:
            sql += " AND dimension = ?"
            params.append(dimension)
        sql += " ORDER BY report_date DESC"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/holders")
def get_holders(symbol: str, report_date: str | None = None) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        sql = "SELECT * FROM circulating_holders WHERE stock_id = ?"
        params: list[Any] = [stock_id]
        if report_date:
            sql += " AND report_date = ?"
            params.append(report_date)
        sql += " ORDER BY report_date DESC, rank"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/shareholder-count")
def get_shareholder_count(symbol: str) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        return _rows(
            repository.fetch_all(
                conn, "SELECT * FROM shareholder_count WHERE stock_id = ? ORDER BY end_date DESC", (stock_id,)
            )
        )
    finally:
        conn.close()


@router.get("/industry")
def get_industries(date: str | None = None) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        rows = repository.fetch_all(conn, "SELECT * FROM industry ORDER BY pct_change DESC")
        return _rows(rows)
    finally:
        conn.close()


@router.get("/stock/{symbol}/report-facts")
def get_report_facts(symbol: str, report_date: str | None = None) -> dict[str, Any]:
    """体检页当期全量财务数据：核心指标 + 三大表 + 原文抽取字段。"""
    conn = db.init_db()
    try:
        try:
            return report_facts.build_for_symbol(conn, symbol, report_date)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/stock/{symbol}/documents")
def get_documents(symbol: str) -> list[dict[str, Any]]:
    """文档清单：只返回前端可读元数据，不下发服务端路径与 sha256。"""
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        rows = _rows(
            repository.fetch_all(
                conn,
                "SELECT id, doc_type, period, title, source_url, file_size, status "
                "FROM documents WHERE stock_id = ? ORDER BY period DESC",
                (stock_id,),
            )
        )
        for row in rows:
            # has_text / has_pdf 由服务层按库内路径判定，前端无需知道绝对路径。
            info = document_reader.metadata(int(row["id"]), conn)
            row["has_text"] = info["has_text"]
            row["has_pdf"] = info["has_pdf"]
        return rows
    finally:
        conn.close()


@router.get("/document/{doc_id}")
def get_document(doc_id: int) -> dict[str, Any]:
    """财报原文元数据 + 章节目录（不含全文正文）。"""
    conn = db.init_db()
    try:
        try:
            return document_reader.get_document(doc_id, conn)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/document/{doc_id}/text")
def get_document_text(doc_id: int, start: int = 0, length: int = 8000) -> dict[str, Any]:
    """按字符偏移分块读取原文，前端滚动到定位点后按需加载。"""
    conn = db.init_db()
    try:
        try:
            return document_reader.get_text(doc_id, start, length, conn)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/document/{doc_id}/locate")
def locate_document(
    doc_id: int,
    q: str | None = None,
    keywords: str | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    """引文定位：精确 → 归一化 → 关键词 → 章节，未命中给明确提示。"""
    conn = db.init_db()
    try:
        try:
            return document_reader.locate_document(doc_id, conn, query=q, keywords=keywords, section=section)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/document/{doc_id}/pdf-outline")
def get_document_pdf_outline(doc_id: int) -> dict[str, Any]:
    """PDF 阅读器目录：原生书签优先，无书签时按页首标题生成。"""
    conn = db.init_db()
    try:
        try:
            return document_reader.pdf_outline(doc_id, conn)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/document/{doc_id}/pdf-page")
def get_document_pdf_page(doc_id: int, page: int = 1, scale: float = 1.0):
    """服务端按页渲染 PDF，规避浏览器对部分嵌入字体的兼容问题。"""
    from fastapi.responses import Response

    conn = db.init_db()
    try:
        try:
            png, width, height = document_reader.render_pdf_page(doc_id, page, scale, conn)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Pdf-Page-Width": str(width),
                "X-Pdf-Page-Height": str(height),
            },
        )
    finally:
        conn.close()


@router.get("/document/{doc_id}/pdf-locate")
def locate_document_pdf(
    doc_id: int,
    q: str | None = None,
    keywords: str | None = None,
) -> dict[str, Any]:
    """在原始 PDF 中定位引文，返回页码与页面内矩形坐标。"""
    conn = db.init_db()
    try:
        try:
            return document_reader.locate_in_pdf(doc_id, conn, query=q, keywords=keywords)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/document/{doc_id}/pdf")
def download_document_pdf(doc_id: int):
    """下载归档的原始 PDF；文件不在库里时返回 404，不外链第三方站点。"""
    from fastapi.responses import FileResponse

    conn = db.init_db()
    try:
        path = document_reader.pdf_path(doc_id, conn)
        if path is None:
            raise HTTPException(status_code=404, detail="原始 PDF 未归档")
        info = document_reader.metadata(doc_id, conn)
        filename = f"{info['symbol']}_{info['period'] or 'report'}_{info['doc_type']}.pdf"
        return FileResponse(path, media_type="application/pdf", filename=filename)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/stock/{symbol}/integrity")
def get_integrity(symbol: str) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        return _rows(
            repository.fetch_all(
                conn,
                "SELECT record_date, behavior_type, category, title, description, severity, source, source_url "
                "FROM integrity_records WHERE stock_id = ? ORDER BY record_date DESC",
                (stock_id,),
            )
        )
    finally:
        conn.close()


@router.get("/stock/{symbol}/return")
def get_return(symbol: str, start: str | None = None, end: str | None = None) -> Any:
    return compute_return_for(symbol, start, end)


@router.get("/stock/{symbol}/calendar")
def get_calendar(symbol: str) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        events: list[dict[str, Any]] = []
        for row in repository.fetch_all(
            conn, "SELECT planned_date, report_type FROM report_calendar WHERE stock_id = ?", (stock_id,)
        ):
            events.append({"type": "财报", "date": row["planned_date"], "title": f"{row['report_type']} 财报披露"})
        for row in repository.fetch_all(
            conn, "SELECT ex_date, cash_per_share FROM dividends WHERE stock_id = ? AND ex_date IS NOT NULL", (stock_id,)
        ):
            cash = row["cash_per_share"] or 0
            events.append({"type": "分红", "date": row["ex_date"], "title": f"每 10 股派 {cash * 10:.2f} 元"})
        for row in repository.fetch_all(
            conn, "SELECT unlock_date, unlock_shares FROM unlocks WHERE stock_id = ?", (stock_id,)
        ):
            shares = (row["unlock_shares"] or 0) / 1e4
            events.append({"type": "解禁", "date": row["unlock_date"], "title": f"解禁 {shares:.0f} 万股"})
        for row in repository.fetch_all(
            conn,
            "SELECT publish_date, title FROM announcements WHERE stock_id = ? AND title LIKE '%股东大会%'",
            (stock_id,),
        ):
            events.append({"type": "股东大会", "date": row["publish_date"], "title": row["title"]})
        events.sort(key=lambda item: item["date"] or "")
        return events
    finally:
        conn.close()


@router.get("/compare")
def get_compare(symbols: str) -> list[dict[str, Any]]:
    return compare_stocks([s.strip() for s in symbols.split(",") if s.strip()])


@router.get("/screener")
def get_screener(
    min_roe: float | None = None,
    min_gross: float | None = None,
    max_debt: float | None = None,
    min_revenue_growth: float | None = None,
) -> list[dict[str, Any]]:
    return screen_stocks(min_roe, min_gross, max_debt, min_revenue_growth)


@router.get("/holders/track")
def get_holder_track(name: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        sql = (
            "SELECT h.report_date, h.holder_name, h.holder_category, h.hold_num, h.hold_ratio, "
            "s.code, s.name AS stock_name FROM circulating_holders h JOIN stocks s ON s.id = h.stock_id WHERE 1 = 1"
        )
        params: list[Any] = []
        if name:
            sql += " AND h.holder_name LIKE ?"
            params.append(f"%{name}%")
        if category:
            sql += " AND h.holder_category = ?"
            params.append(category)
        sql += " ORDER BY h.report_date DESC"
        return _rows(repository.fetch_all(conn, sql, params))
    finally:
        conn.close()


@router.get("/stock/{symbol}/products")
def get_products(symbol: str) -> list[dict[str, Any]]:
    conn = db.init_db()
    try:
        stock_id = _resolve_stock(conn, symbol)
        return _rows(
            repository.fetch_all(
                conn,
                "SELECT product_code, product_name, source, category FROM stock_products WHERE stock_id = ?",
                (stock_id,),
            )
        )
    finally:
        conn.close()


@router.get("/stock/{symbol}/price-compare")
def get_price_compare(symbol: str, product: str | None = None) -> dict[str, Any]:
    return price_compare(symbol, product)
