"""RAG index: the platform vector database (one team collection), the local mirror, caches, hybrid ranking."""
import json
import math
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import materials
from app.ai.tutor import narrow_sources
from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings

PHOTO = (
    "Photosynthesis takes place in the chloroplasts of plant cells. Chlorophyll absorbs light energy, mostly red and blue. "
    "The light reactions split water and release oxygen. The Calvin cycle then builds glucose from carbon dioxide."
)


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class FakeQdrant:
    """Minimal Qdrant REST: collections, payload indexes, points upsert/search/delete with `must` filters."""

    def __init__(self):
        self.collections: dict[str, dict] = {}
        self.down = False
        self.requests = []

    def _match(self, payload, flt):
        return all(payload.get(c["key"]) == c["match"]["value"] for c in (flt or {}).get("must", []))

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("qdrant down")
            self.requests.append((req.method, req.url.path, dict(req.headers)))
            parts = req.url.path.strip("/").split("/")
            body = json.loads(req.content) if req.content else {}
            name = parts[1] if len(parts) > 1 else ""
            col = self.collections.get(name)
            if req.method == "GET" and len(parts) == 2:
                return httpx.Response(200, json={"result": {"status": "green"}}) if col is not None else httpx.Response(404, json={"status": {"error": "Not found"}})
            if req.method == "PUT" and len(parts) == 2:
                self.collections[name] = {"size": body["vectors"]["size"], "points": {}}
                return httpx.Response(200, json={"result": True})
            if col is None:
                return httpx.Response(404, json={"status": {"error": "Not found"}})
            if parts[2:] == ["index"]:
                return httpx.Response(200, json={"result": {}})
            if req.method == "PUT" and parts[2:] == ["points"]:
                for p in body["points"]:
                    assert len(p["vector"]) == col["size"]
                    col["points"][p["id"]] = (p["vector"], p["payload"])
                return httpx.Response(200, json={"result": {"status": "completed"}})
            if parts[2:] == ["points", "search"]:
                hits = [{"id": pid, "score": _cos(body["vector"], v), "payload": pl} for pid, (v, pl) in col["points"].items() if self._match(pl, body.get("filter"))]
                hits.sort(key=lambda h: -h["score"])
                return httpx.Response(200, json={"result": hits[: body["limit"]]})
            if parts[2:] == ["points", "delete"]:
                for pid in [pid for pid, (_, pl) in col["points"].items() if self._match(pl, body.get("filter"))]:
                    del col["points"][pid]
                return httpx.Response(200, json={"result": {"status": "completed"}})
            return httpx.Response(400, json={"status": {"error": "unexpected"}})
        return httpx.MockTransport(handler)

    def points(self, collection="team-7"):
        return self.collections.get(collection, {}).get("points", {})


class FakeChroma:
    """Minimal Chroma v2: get_or_create a collection, upsert, query with a $and where, delete."""

    def __init__(self):
        self.items: dict[str, tuple[list[float], dict]] = {}

    def _where(self, meta, where):
        return all(meta.get(k) == v for term in where["$and"] for k, v in term.items())

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content) if req.content else {}
            path = req.url.path
            if path.endswith("/collections"):
                return httpx.Response(200, json={"id": "c-123", "name": body["name"]})
            if path.endswith("/upsert"):
                for i, e, m in zip(body["ids"], body["embeddings"], body["metadatas"]):
                    self.items[i] = (e, m)
                return httpx.Response(200, json={})
            if path.endswith("/query"):
                hits = sorted(((1 - _cos(body["query_embeddings"][0], e), m) for e, m in self.items.values() if self._where(m, body["where"])), key=lambda h: h[0])[: body["n_results"]]
                return httpx.Response(200, json={"metadatas": [[m for _, m in hits]], "distances": [[d for d, _ in hits]]})
            if path.endswith("/delete"):
                for i in [i for i, (_, m) in self.items.items() if self._where(m, body["where"])]:
                    del self.items[i]
                return httpx.Response(200, json={})
            return httpx.Response(404)
        return httpx.MockTransport(handler)


def make(tmp_path, vstore=None, backend="qdrant", name="a.db", **over):
    transport, state = build_fake()
    s = load_settings(db_path=str(tmp_path / name), vector_backend=backend if vstore else "local", vector_url="http://vectors.test" if vstore else "",
                      vector_collection="team-7", vector_api_key="team-key", **fake_ai_settings(), **over)
    return create_app(s, ai_transport=transport, vector_transport=vstore.transport() if vstore else None), state


def signup(client, pseudo="ada_l"):
    t = client.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def upload(client, h, text=PHOTO, name="bio.txt", scope="me"):
    r = client.post(f"/api/materials/file?scope={scope}", content=text.encode(), headers={**h, "X-Filename": name})
    assert r.status_code == 200, r.text
    end = time.time() + 6
    while time.time() < end:
        docs = client.get(f"/api/materials?scope={scope}", headers=h).json()["documents"]
        if docs and all(d["status"] == "ready" for d in docs):
            return docs
        time.sleep(0.05)
    raise AssertionError("not ready")


def ask(client, h, q, scope="me"):
    return client.post("/api/materials/ask", json={"scope": scope, "question": q}, headers=h).json()


def test_vectors_go_to_the_team_collection_and_search_uses_it(tmp_path):
    q = FakeQdrant()
    app, _ = make(tmp_path, q)
    with TestClient(app) as c:
        h = signup(c)
        upload(c, h)
        pts = q.points()
        assert pts and q.collections["team-7"]["size"] == 256
        payload = next(iter(pts.values()))[1]
        assert set(payload) == {"app", "scope", "doc_id", "idx"} and payload["scope"].startswith("u:")   # ids only, no text
        assert any(hdr.get("api-key") == "team-key" for _, _, hdr in q.requests)
        assert c.app.state.db.one("SELECT vsync FROM docs")["vsync"] == 1

        out = ask(c, h, "Where does photosynthesis take place?")
        assert out["grounded"] and "chloroplasts" in out["answer"].lower()
        m = c.get("/api/ai/metrics").json()
        assert m["vector_searches"] >= 1 and m["vector_fallbacks"] == 0 and m["vector_index"]["backend"] == "qdrant"

        doc_id = c.get("/api/materials?scope=me", headers=h).json()["documents"][0]["id"]
        c.delete(f"/api/materials/{doc_id}?scope=me", headers=h)
        assert q.points() == {}


def test_two_installations_share_the_collection_without_mixing(tmp_path):
    q = FakeQdrant()
    a, _ = make(tmp_path, q, name="a.db")
    b, _ = make(tmp_path, q, name="b.db")
    with TestClient(a) as ca, TestClient(b) as cb:
        ha, hb = signup(ca), signup(cb)          # both learners are user 1 -> same scope name "u:1"
        upload(ca, ha)
        upload(cb, hb, "Mitochondria produce ATP through cellular respiration in animal and plant cells.", "cell.txt")
        apps = {pl["app"] for _, pl in q.points().values()}
        assert len(apps) == 2
        out = ask(cb, hb, "Where does photosynthesis take place?")
        assert "chloroplasts" not in out["answer"].lower()     # A's notes never answer B


def test_platform_outage_falls_back_to_the_local_mirror_then_resyncs(tmp_path):
    q = FakeQdrant()
    q.down = True
    app, _ = make(tmp_path, q)
    with TestClient(app) as c:
        h = signup(c)
        upload(c, h)                                              # still ready: the mirror has the vectors
        assert c.app.state.db.one("SELECT vsync FROM docs")["vsync"] == 0
        out = ask(c, h, "Where does photosynthesis take place?")
        assert out["grounded"] and "chloroplasts" in out["answer"].lower()
        assert c.get("/api/ai/metrics").json()["vector_fallbacks"] >= 1
    q.down = False
    app2, _ = make(tmp_path, q)                                    # next start pushes what was missed
    with TestClient(app2) as c2:
        end = time.time() + 5
        while time.time() < end and not q.points():
            time.sleep(0.05)
        assert q.points() and c2.app.state.db.one("SELECT vsync FROM docs")["vsync"] == 1


def test_the_same_passage_is_never_embedded_twice(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        h = signup(c)
        upload(c, h)
        sent = c.get("/api/ai/metrics").json()["embeddings"]
        sp = c.post("/api/spaces", json={"name": "Copy"}, headers=h).json()
        upload(c, h, scope=f"space-{sp['id']}")                # same notes in a second library
        c.app.state.gateway.cache.clear()                       # even after the in-memory cache is gone
        m = c.get("/api/ai/metrics").json()
        assert m["embeddings"] == sent and m["embed_cache_hits"] >= 1


def test_chroma_backend(tmp_path):
    ch = FakeChroma()
    app, _ = make(tmp_path, ch, backend="chroma")
    with TestClient(app) as c:
        h = signup(c)
        upload(c, h)
        assert ch.items and all(m["scope"].startswith("u:") for _, m in ch.items.values())
        assert "chloroplasts" in ask(c, h, "Where does photosynthesis take place?")["answer"].lower()
        doc_id = c.get("/api/materials?scope=me", headers=h).json()["documents"][0]["id"]
        c.delete(f"/api/materials/{doc_id}?scope=me", headers=h)
        assert ch.items == {}


def test_neighbouring_chunks_merge_without_repeating_the_overlap():
    a = {"doc_id": 1, "title": "t", "idx": 0, "text": "First idea here. Shared sentence."}
    b = {"doc_id": 1, "title": "t", "idx": 1, "text": "Shared sentence. Second idea."}
    c = {"doc_id": 2, "title": "u", "idx": 0, "text": "Other doc."}
    out = materials._merge_neighbours([(b, 0.9), (c, 0.8), (a, 0.7)])
    assert len(out) == 2 and out[0]["score"] == 0.9
    assert out[0]["text"].count("Shared sentence.") == 1 and out[0]["text"].startswith("First idea")


def test_the_mid_size_call_only_gets_the_passages_the_draft_used():
    src = [{"n": i, "kind": "material" if i < 4 else "web", "title": f"s{i}", "text": "x"} for i in range(1, 6)]
    assert [s["n"] for s in narrow_sources(src, "Answer [3] and [5].")] == [1, 3, 5]
    assert [s["n"] for s in narrow_sources(src, "No citation.")] == [1, 2, 3]   # MID_SOURCES = 4: room for a full list
    assert [s["n"] for s in narrow_sources(src, "")] == [1, 2, 3, 4]
    assert narrow_sources([], "x") == []


def test_keyword_overlap_still_counts_with_embeddings(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        h = signup(c)
        upload(c, h, "The enzyme RuBisCO fixes carbon dioxide in the Calvin cycle. " * 2 + "\n\n" + "Plants are green because chlorophyll reflects green light. " * 2, "notes.txt")
        found = c.portal.call(materials.retrieve, c.app, "u:1", "What does RuBisCO do?", 2)
        assert found and "RuBisCO" in found[0]["text"]
