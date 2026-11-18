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
