"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI

from fundamental.web.routes.api import router as api_router

app = FastAPI(title="value-compass", version="0.1.0")
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
