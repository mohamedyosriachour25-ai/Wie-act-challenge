"""Study spaces: several solo rooms, each with its own material and memory; staged tutor turns; web lookup."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings

PHOTO = (
    "Photosynthesis takes place in the chloroplasts of plant cells. Chlorophyll absorbs light energy, mostly red and blue. "
    "The light reactions split water and release oxygen. The Calvin cycle then builds glucose from carbon dioxide."
)


class Wiki:
    """Stands in for Wikipedia's API: records each query, answers with one article extract."""

    def __init__(self):
        self.queries = []

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            q = request.url.params.get("gsrsearch")
            self.queries.append((request.url.host, q))
            return httpx.Response(200, json={"query": {"pages": [{
                "index": 1, "title": "Jan Ingenhousz", "fullurl": "https://en.wikipedia.org/wiki/Jan_Ingenhousz",
                "extract": "Jan Ingenhousz was a Dutch physiologist who discovered photosynthesis in 1779, showing that light is essential."}]}})
        return httpx.MockTransport(handler)


@pytest.fixture
def env(tmp_path):
    wiki = Wiki()
    transport, state = build_fake()
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=transport, web_transport=wiki.transport())
    with TestClient(app) as c:
        yield c, state, wiki


def signup(client, pseudo="ada_l"):
    t = client.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def ask(client, h, space_id, message, web=True):
    r = client.post(f"/api/spaces/{space_id}/ask", json={"message": message, "web": web}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def first_space(client, h):
    return client.get("/api/spaces", headers=h).json()["spaces"][0]


def wait_ready(client, h, scope):
    end = time.time() + 6
    while time.time() < end:
        docs = client.get(f"/api/materials?scope={scope}", headers=h).json()["documents"]
        if docs and all(d["status"] == "ready" for d in docs):
            return
        time.sleep(0.05)
    raise AssertionError("material not ready")


def test_spaces_are_private_and_the_first_one_keeps_the_library(env):
    client, _, _ = env
    h = signup(client)
    spaces = client.get("/api/spaces", headers=h).json()["spaces"]
    assert len(spaces) == 1 and spaces[0]["primary"] is True
    s2 = client.post("/api/spaces", json={"name": "Chemistry"}, headers=h).json()
    assert s2["name"] == "Chemistry" and s2["primary"] is False
    assert client.patch(f"/api/spaces/{s2['id']}", json={"name": "Organic chemistry"}, headers=h).json()["name"] == "Organic chemistry"

    other = signup(client, "bob_k")
    assert client.get(f"/api/spaces/{s2['id']}/messages", headers=other).status_code == 404
    assert client.post(f"/api/spaces/{s2['id']}/ask", json={"message": "hello there"}, headers=other).status_code == 404
    assert client.get(f"/api/materials?scope=space-{s2['id']}", headers=other).status_code == 404

    assert client.delete(f"/api/spaces/{spaces[0]['id']}", headers=h).status_code == 400   # holds the personal library
    assert client.delete(f"/api/spaces/{s2['id']}", headers=h).json()["ok"] is True
    assert len(client.get("/api/spaces", headers=h).json()["spaces"]) == 1


def test_a_turn_arrives_as_stages_text_first_then_figure(env):
    client, _, _ = env
    h = signup(client)
    sp = first_space(client, h)
    events = ask(client, h, sp["id"], "Explain photosynthesis", web=False)
    kinds = [e["type"] for e in events]
    # partial results in order: intent, sources, a compact draft, the mid-size final answer, then the figure
    assert kinds.index("intent") < kinds.index("sources") < kinds.index("draft") < kinds.index("final") < kinds.index("visual") < kinds.index("done")
    stages = [e["stage"] for e in events if e["type"] == "stage"]
    assert stages[:3] == ["understanding", "reading", "drafting"] and "writing" in stages
    final = next(e for e in events if e["type"] == "final")
    assert final["text"].startswith("## ") and "revised from the draft" in final["text"]
    assert next(e for e in events if e["type"] == "visual")["visual"]["template"]
    done = events[-1]
    assert done["type"] == "done" and {c["task"] for c in done["meta"]["calls"]} >= {"intent", "draft", "tutor", "illustrate"}
    assert {c["task"]: c["cls"] for c in done["meta"]["calls"]} == {"intent": "compact", "draft": "compact", "tutor": "mid", "illustrate": "mid"}

    thread = client.get(f"/api/spaces/{sp['id']}/messages", headers=h).json()["messages"]
    assert [m["role"] for m in thread] == ["user", "assistant"]
    assert thread[1]["meta"]["visual"] and thread[1]["meta"]["intent"]["intent"] == "explain"
    metrics = client.get("/api/ai/metrics").json()
    assert metrics["first_text_ms_median"] is not None and metrics["final_text_ms_median"] >= metrics["first_text_ms_median"]


def test_each_space_answers_from_its_own_material(env):
    client, _, _ = env
    h = signup(client)
    bio = first_space(client, h)
    other = client.post("/api/spaces", json={"name": "History"}, headers=h).json()
    up = client.post(f"/api/materials/file?scope=space-{bio['id']}", content=PHOTO.encode(), headers={**h, "X-Filename": "bio.txt"})
    assert up.status_code == 200
    wait_ready(client, h, f"space-{bio['id']}")

    events = ask(client, h, bio["id"], "Where does photosynthesis take place?", web=False)
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources and sources[0]["kind"] == "material" and sources[0]["title"] == "bio"
    final = next(e for e in events if e["type"] == "final")["text"]
    assert "chloroplasts" in final and "[1]" in final

    events = ask(client, h, other["id"], "Where does photosynthesis take place?", web=False)
    assert next(e for e in events if e["type"] == "sources")["sources"] == []


def test_the_conversation_is_remembered_and_follow_ups_are_understood(env):
    client, state, _ = env
    h = signup(client)
    sp = first_space(client, h)
    ask(client, h, sp["id"], "Explain photosynthesis", web=False)
    state.log.clear()
    events = ask(client, h, sp["id"], "why?", web=False)
    intent = next(e for e in events if e["type"] == "intent")
    assert intent["intent"] == "follow_up" and "photosynthesis" in intent["standalone"].lower()
    intent_prompt = next(r for r in state.log if "TASK: intent" in r["messages"][0]["content"])["messages"][-1]["content"]
    assert "RECENT:" in intent_prompt and "Student: Explain photosynthesis" in intent_prompt
    tutor_prompt = next(r for r in state.log if "TASK: tutor" in r["messages"][0]["content"])["messages"][-1]["content"]
    assert "Student: Explain photosynthesis" in tutor_prompt     # the writer sees the conversation too


def test_older_turns_fold_into_the_space_summary(tmp_path):
    transport, _ = build_fake()
    s = load_settings(db_path=str(tmp_path / "t.db"), memory_window=2, memory_fold_after=1, **fake_ai_settings())
    with TestClient(create_app(s, ai_transport=transport)) as client:
        h = signup(client)
        sp = first_space(client, h)
        for msg in ("Explain photosynthesis", "Explain respiration", "Explain osmosis"):
            ask(client, h, sp["id"], msg, web=False)
        end = time.time() + 5
        while time.time() < end and not client.get(f"/api/spaces/{sp['id']}/messages", headers=h).json()["has_memory"]:
            time.sleep(0.05)
        db = client.app.state.db
        row = db.one("SELECT summary, folded_upto FROM spaces WHERE id=?", (sp["id"],))
        assert "photosynthesis" in row["summary"].lower() and row["folded_upto"] > 0
        client.delete(f"/api/spaces/{sp['id']}/messages", headers=h)
        assert db.one("SELECT summary FROM spaces WHERE id=?", (sp["id"],))["summary"] == ""


def test_web_lookup_is_declared_and_only_used_when_needed(env):
    client, _, wiki = env
    h = signup(client)
    sp = first_space(client, h)
    events = ask(client, h, sp["id"], "Who discovered photosynthesis?")
    web = [s for s in next(e for e in events if e["type"] == "sources")["sources"] if s["kind"] == "web"]
    assert web and web[0]["url"].startswith("https://en.wikipedia.org/") and web[0]["title"] == "Jan Ingenhousz"
    assert "1779" in next(e for e in events if e["type"] == "final")["text"]

    n = len(wiki.queries)
    events = ask(client, h, sp["id"], "Who discovered osmosis?", web=False)        # switched off by the learner
    assert len(wiki.queries) == n and all(s["kind"] != "web" for s in next(e for e in events if e["type"] == "sources")["sources"])
    ask(client, h, sp["id"], "hello", web=True)                                         # small talk never looks anything up
    assert len(wiki.queries) == n


def test_personal_details_are_never_sent(env):
    client, state, wiki = env
    h = signup(client)
    sp = first_space(client, h)
    before = state.calls
    r = client.post(f"/api/spaces/{sp['id']}/ask", json={"message": "mail me at ada@example.com about osmosis"}, headers=h)
    assert r.status_code == 400 and "personal" in r.json()["detail"]
    assert state.calls == before and wiki.queries == []


def test_small_talk_is_one_compact_reply_without_a_figure(env):
    client, _, _ = env
    h = signup(client)
    sp = first_space(client, h)
    events = ask(client, h, sp["id"], "hello", web=False)
    kinds = [e["type"] for e in events]
    assert "visual" not in kinds and "final" not in kinds and "draft" in kinds
    assert {c["cls"] for c in events[-1]["meta"]["calls"]} == {"compact"}


def test_a_busy_mid_model_still_gives_an_answer(env):
    client, state, _ = env
    h = signup(client)
    sp = first_space(client, h)
    state.simulate("saturate", 30)
    events = ask(client, h, sp["id"], "Explain photosynthesis", web=False)
    assert events[-1]["type"] == "done" and events[-1]["meta"]["degraded"] is True
    assert next(e for e in events if e["type"] == "final")["text"]
