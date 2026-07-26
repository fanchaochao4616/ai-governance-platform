"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fastapi import Depends

from app.api import (
    routes_auth,
    routes_export,
    routes_jobs,
    routes_prompts,
    routes_rounds,
    routes_templates,
)
from app.api.deps import get_current_user
from app.db import init_db
from config import ensure_data_dirs

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    init_db()
    yield


app = FastAPI(
    title="内容风控文本分类标注平台",
    description="PRD v0.5 — Dual-Agent Prompt Optimization + Human-led Multi-round QC",
    version="0.5.0",
    lifespan=lifespan,
)

# Public auth routes
app.include_router(routes_auth.router)

# Protected business routes (require login)
_auth = [Depends(get_current_user)]
app.include_router(routes_jobs.router, dependencies=_auth)
app.include_router(routes_rounds.router, dependencies=_auth)
app.include_router(routes_prompts.router, dependencies=_auth)
app.include_router(routes_templates.router, dependencies=_auth)
app.include_router(routes_export.router, dependencies=_auth)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": "0.5.0"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    return FileResponse(index_path)


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    """Browsers request /favicon.ico by default; serve static icon."""
    ico = STATIC_DIR / "favicon.ico"
    png = STATIC_DIR / "favicon.png"
    path = ico if ico.exists() else png
    return FileResponse(path, media_type="image/png")
