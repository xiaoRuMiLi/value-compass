"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fundamental.utils import paths
from fundamental.web.routes.api import router as api_router

app = FastAPI(title="value-compass", version="0.1.0")
app.include_router(api_router)

_PAGES_DIR = paths.project_root() / "pages"
app.mount("/static", StaticFiles(directory=_PAGES_DIR / "static"), name="static")


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/report")
def report_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "report.html")


@app.get("/financials")
def financials_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "financials.html")


@app.get("/industry")
def industry_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "industry.html")


@app.get("/review")
def review_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "review.html")


@app.get("/document")
def document_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "document.html")


@app.get("/integrity")
def integrity_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "integrity.html")


@app.get("/calendar")
def calendar_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "calendar.html")


@app.get("/calculator")
def calculator_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "calculator.html")


@app.get("/compare")
def compare_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "compare.html")


@app.get("/screener")
def screener_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "screener.html")


@app.get("/holders")
def holders_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "holders.html")


@app.get("/holders-track")
def holders_track_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "holders-track.html")


@app.get("/products")
def products_page() -> FileResponse:
    return FileResponse(_PAGES_DIR / "products.html")
