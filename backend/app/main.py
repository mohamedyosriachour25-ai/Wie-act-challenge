"""EdNova API. Run from the backend folder:  uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, materials, review, rooms, routes_study, security
from .ai.gateway import Gateway
from .ai.mock_api import build_mock
from .config import Settings, load_settings
from .db import DB

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()

    security.setup_logging(s)
    log = logging.getLogger("ednova")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Documents whose indexing was cut short by a restart pick up where they stopped.
        for doc in app.state.db.all("SELECT id FROM docs WHERE status IN ('indexing', 'waiting')"):
            log.info("resuming indexing of document %s", doc["id"])
            asyncio.create_task(materials.index_document(app, doc["id"]))
        yield
        await app.state.gateway.aclose()
        app.state.db.close()

    app = FastAPI(title="EdNova", lifespan=lifespan, docs_url=None if s.is_production else "/docs", redoc_url=None, openapi_url=None if s.is_production else "/openapi.json")
    app.state.settings = s
    app.state.limiter = security.SlidingWindow()
    app.add_middleware(security.Guard)
    app.state.db = DB(s.db_path)
    app.state.db.seed_live_rooms(s.live_capacity)
    app.state.hub = rooms.Hub()
    app.state.index_lock = asyncio.Lock()

    transport = None
    app.state.mock_state = None
    if s.is_mock:
        mock_router, app.state.mock_state = build_mock(s)
        app.include_router(mock_router)
        transport = httpx.ASGITransport(app=app)  # the gateway talks to the fake service in-process
    app.state.gateway = Gateway(s, transport)
    from .ai.semcache import SemanticCache
    app.state.gateway.semcache = SemanticCache(app.state.gateway, s)

    if s.is_mock:
        log.warning("running on the built-in fake AI service (EDNOVA_BASE_URL=mock): lessons are canned")
    else:
        log.info("AI service: %s (compact=%s, mid=%s)", s.base_url, s.compact_model, s.mid_model)
    app.include_router(auth.router)
    app.include_router(routes_study.router)
    app.include_router(materials.router)
    app.include_router(rooms.router)
    app.include_router(review.router)

    @app.get("/api/health")
    def health():
        db_ok = bool(app.state.db.one("SELECT 1 AS ok"))
        return {"ok": db_ok, "mock": s.is_mock}

    if DIST.exists():  # single-process deployment: serve the built frontend too
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = DIST / path
            return FileResponse(target if path and target.is_file() else DIST / "index.html")

    return app


app = create_app()
