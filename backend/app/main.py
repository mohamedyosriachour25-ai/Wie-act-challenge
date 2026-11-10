"""HerwieGO API. Run from the backend folder:  uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, materials, phonetree, review, rooms, routes_study, scoring, security
from .ai.gateway import Gateway
from .config import Settings, load_settings
from .db import DB

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(settings: Settings | None = None, ai_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`ai_transport` is a hook for the test suite only (an httpx transport that stands in for the
    network): the running app never passes it, so it always talks to the real service over HTTP."""
    s = settings or load_settings()

    security.setup_logging(s)
    log = logging.getLogger("herwiego")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Documents whose indexing was cut short by a restart pick up where they stopped.
        for doc in app.state.db.all("SELECT id FROM docs WHERE status IN ('indexing', 'waiting')"):
            log.info("resuming indexing of document %s", doc["id"])
            asyncio.create_task(materials.index_document(app, doc["id"]))
        yield
        await app.state.gateway.aclose()
        app.state.db.close()

    app = FastAPI(title="HerwieGO", lifespan=lifespan, docs_url=None if s.is_production else "/docs", redoc_url=None, openapi_url=None if s.is_production else "/openapi.json")
    app.state.settings = s
    app.state.limiter = security.SlidingWindow()
    app.add_middleware(security.Guard)
    app.state.db = DB(s.db_path)
    app.state.db.seed_live_rooms(s.live_capacity)
    app.state.hub = rooms.Hub()
    app.state.index_lock = asyncio.Lock()

    app.state.gateway = Gateway(s, ai_transport)
    from .ai.semcache import SemanticCache
    app.state.gateway.semcache = SemanticCache(app.state.gateway, s)

    log.info("AI service: %s (compact=%s, mid=%s)", s.base_url, s.compact_model, s.mid_model)
    app.include_router(auth.router)
    app.include_router(routes_study.router)
    app.include_router(materials.router)
    app.include_router(rooms.router)
    app.include_router(phonetree.router)
    app.include_router(review.router)
    app.include_router(scoring.router)

    @app.get("/api/health")
    def health():
        db_ok = bool(app.state.db.one("SELECT 1 AS ok"))
        return {"ok": db_ok}

    if DIST.exists():  # single-process deployment: serve the built frontend too
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = DIST / path
            return FileResponse(target if path and target.is_file() else DIST / "index.html")

    return app


def _not_configured_app(reason: str):
    """Stand-in ASGI app used when the real one cannot be built (e.g. missing HERWIEGO_* settings).
    Every request gets a clear 503 naming the problem, instead of uvicorn crashing on each request."""
    body = ("HerwieGO cannot start: " + reason + "\nFix backend/.env (see .env.example), then restart the server.\n").encode()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1011})
        else:
            logging.getLogger("herwiego").error("Cannot start HerwieGO: %s", reason)
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": body})

    return app


try:
    app = create_app()
except RuntimeError as exc:
    # Importing this module (e.g. to reuse create_app) must not require real credentials; only
    # actually running the server does — and then every request explains what is missing.
    logging.getLogger("herwiego").error("Cannot start HerwieGO: %s", exc)
    app = _not_configured_app(str(exc))
