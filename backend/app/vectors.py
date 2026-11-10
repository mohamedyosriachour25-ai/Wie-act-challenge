"""Vector index for RAG: the platform's vector database (one dedicated collection per team, per the
challenge guide), with a local mirror that keeps search working if the platform index is unreachable.

Design
* The team has ONE collection on the platform. Every logical library of the app (a learner's study
  space `s:<id>` / `u:<uid>`, a room `r:<code>`) lives in that one collection and is separated by a
  `scope` payload filter. Each installation also stamps an `app` id (random, stored in the database),
  so several teammates' machines can share the team collection without overwriting each other.
* Only vectors and ids go to the vector database. Passage text stays in the app's own database
  (`chunks.text`), so nothing more than the embeddings leaves the app.
* The local mirror (`chunks.emb`) is always written. Search asks the platform index first; on any
  error it answers from the mirror and counts a fallback, so the learner never sees the outage.
  Documents whose vectors did not reach the platform are re-sent on the next start (`docs.vsync`).

Backends: `local` (default until the platform database is known), `qdrant`, `chroma`.
Run `python scripts/probe_platform.py` to find out which one the platform exposes.
"""
from __future__ import annotations

import logging
import secrets
import uuid

import httpx

log = logging.getLogger("herwiego")


class VectorError(Exception):
    pass


def install_id(db) -> str:
    """Stable random id of this installation (separates its points inside the shared team collection)."""
    with db._lock:
        db.conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = db.conn.execute("SELECT value FROM app_meta WHERE key='install_id'").fetchone()
        if row:
            return row[0]
        value = "hw-" + secrets.token_hex(6)
        db.conn.execute("INSERT INTO app_meta (key, value) VALUES ('install_id', ?)", (value,))
        db.conn.commit()
        return value


class RemoteStore:
    """Shared plumbing for HTTP vector databases."""

    name = "remote"

    def __init__(self, url: str, collection: str, app_id: str, api_key: str = "", transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 5.0):
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers.update(self._auth(api_key))
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=httpx.Timeout(timeout_s, connect=3.0), transport=transport)
        self.collection = collection
        self.app_id = app_id
        self.ready = False          # collection known to exist
        self.dim: int | None = None
        self.calls = 0
        self.errors = 0
        self.last_error = ""

    @staticmethod
    def _auth(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def point_id(self, doc_id: int, idx: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"herwiego:{self.app_id}:{doc_id}:{idx}"))

    async def _req(self, method: str, path: str, **kw) -> dict:
        self.calls += 1
        try:
            resp = await self.client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            self.errors += 1
            self.last_error = f"{type(exc).__name__}"
            raise VectorError(self.last_error) from exc
        if resp.status_code >= 400:
            self.errors += 1
            self.last_error = f"HTTP {resp.status_code}"
            raise VectorError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            return {}

    async def aclose(self) -> None:
        await self.client.aclose()

    def info(self) -> dict:
        return {"backend": self.name, "collection": self.collection, "ready": self.ready, "dim": self.dim,
                "calls": self.calls, "errors": self.errors, "last_error": self.last_error}


class QdrantStore(RemoteStore):
    """Qdrant REST API (points with a payload filter)."""

    name = "qdrant"

    @staticmethod
    def _auth(key: str) -> dict:
        return {"api-key": key, "Authorization": f"Bearer {key}"}

    async def ensure(self, dim: int) -> None:
        if self.ready:
            return
        path = f"/collections/{self.collection}"
        try:
            await self._req("GET", path)
        except VectorError as exc:
            if "404" not in str(exc):
                raise
            await self._req("PUT", path, json={"vectors": {"size": dim, "distance": "Cosine"}})
        for field in ("app", "scope"):
            try:
                await self._req("PUT", f"{path}/index", json={"field_name": field, "field_schema": "keyword"})
            except VectorError:
                pass   # indexes are an optimisation; an existing one or a read-only key is fine
        self.ready, self.dim = True, dim

    def _filter(self, **match) -> dict:
        must = [{"key": "app", "match": {"value": self.app_id}}]
        must += [{"key": k, "match": {"value": v}} for k, v in match.items()]
        return {"must": must}

    async def upsert(self, scope: str, doc_id: int, items: list[tuple[int, list[float]]]) -> None:
        if not items:
            return
        await self.ensure(len(items[0][1]))
        points = [{"id": self.point_id(doc_id, idx), "vector": vec,
                   "payload": {"app": self.app_id, "scope": scope, "doc_id": doc_id, "idx": idx}} for idx, vec in items]
        for i in range(0, len(points), 64):
            await self._req("PUT", f"/collections/{self.collection}/points", params={"wait": "true"}, json={"points": points[i : i + 64]})

    async def search(self, scope: str, vector: list[float], k: int) -> list[tuple[int, int, float]]:
        await self.ensure(len(vector))
        data = await self._req("POST", f"/collections/{self.collection}/points/search",
                               json={"vector": vector, "limit": k, "filter": self._filter(scope=scope), "with_payload": True})
        out = []
        for hit in data.get("result") or []:
            p = hit.get("payload") or {}
            if "doc_id" in p and "idx" in p:
                out.append((int(p["doc_id"]), int(p["idx"]), float(hit.get("score", 0.0))))
        return out

    async def delete_doc(self, doc_id: int) -> None:
        if not self.ready and self.dim is None:
            try:
                await self._req("GET", f"/collections/{self.collection}")
            except VectorError:
                return
        await self._req("POST", f"/collections/{self.collection}/points/delete", params={"wait": "true"},
                        json={"filter": self._filter(doc_id=doc_id)})


class ChromaStore(RemoteStore):
    """Chroma HTTP API v2 (tenant / database / collection), metadata `where` filter, cosine space."""

    name = "chroma"

    def __init__(self, *a, tenant: str = "default_tenant", database: str = "default_database", **kw):
        super().__init__(*a, **kw)
        self.base = f"/api/v2/tenants/{tenant}/databases/{database}/collections"
        self.cid: str | None = None

    @staticmethod
    def _auth(key: str) -> dict:
        return {"Authorization": f"Bearer {key}", "X-Chroma-Token": key}

    async def ensure(self, dim: int | None = None) -> None:
        if self.ready:
            return
        data = await self._req("POST", self.base, json={"name": self.collection, "get_or_create": True, "metadata": {"hnsw:space": "cosine"}})
        self.cid = data.get("id")
        if not self.cid:
            raise VectorError("Chroma did not return a collection id")
        self.ready, self.dim = True, dim

    def _where(self, **match) -> dict:
        terms = [{"app": self.app_id}] + [{k: v} for k, v in match.items()]
        return {"$and": terms}

    async def upsert(self, scope: str, doc_id: int, items: list[tuple[int, list[float]]]) -> None:
        if not items:
            return
        await self.ensure(len(items[0][1]))
        for i in range(0, len(items), 64):
            part = items[i : i + 64]
            await self._req("POST", f"{self.base}/{self.cid}/upsert", json={
                "ids": [self.point_id(doc_id, idx) for idx, _ in part],
                "embeddings": [vec for _, vec in part],
                "metadatas": [{"app": self.app_id, "scope": scope, "doc_id": doc_id, "idx": idx} for idx, _ in part],
            })

    async def search(self, scope: str, vector: list[float], k: int) -> list[tuple[int, int, float]]:
        await self.ensure(len(vector))
        data = await self._req("POST", f"{self.base}/{self.cid}/query", json={
            "query_embeddings": [vector], "n_results": k, "where": self._where(scope=scope), "include": ["metadatas", "distances"],
        })
        metas = (data.get("metadatas") or [[]])[0] or []
        dists = (data.get("distances") or [[]])[0] or []
        return [(int(m["doc_id"]), int(m["idx"]), 1.0 - float(d)) for m, d in zip(metas, dists) if m and "doc_id" in m]

    async def delete_doc(self, doc_id: int) -> None:
        await self.ensure()
        await self._req("POST", f"{self.base}/{self.cid}/delete", json={"where": self._where(doc_id=doc_id)})


BACKENDS = {"qdrant": QdrantStore, "chroma": ChromaStore}


def make_store(settings, db, transport: httpx.AsyncBaseTransport | None = None) -> RemoteStore | None:
    """The platform vector store, or None for the local index only."""
    backend = settings.vector_backend
    if backend not in BACKENDS:
        return None
    if not settings.vector_url:
        log.warning("HERWIEGO_VECTOR_BACKEND=%s but HERWIEGO_VECTOR_URL is empty: using the local index", backend)
        return None
    store = BACKENDS[backend](settings.vector_url, settings.vector_collection, install_id(db),
                              api_key=settings.vector_api_key, transport=transport)
    log.info("Vector index: %s collection '%s' at %s (local mirror kept as fallback)", backend, settings.vector_collection, settings.vector_url)
    return store
