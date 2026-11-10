"""Generated work is never lost: a study-space answer keeps being written after the learner leaves the page,
can be picked up again, survives a restart as a partial answer, and a study room's explanation, answers and
quiz are saved with the room and come back after a restart. A late joiner sees an explanation mid-way."""
import asyncio
import json
import time

from fastapi.testclient import TestClient

from app import spaces
from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings


def make_app(path):
    return create_app(load_settings(db_path=str(path), **fake_ai_settings()), ai_transport=build_fake()[0])


def slow_gateway(app, delay=0.15):
    """Each AI call takes a moment, like the real CPU service, so a turn is still running when we leave."""
    gw = app.state.gateway
    real = gw.chat

    async def chat(*a, **kw):
        await asyncio.sleep(delay)
        return await real(*a, **kw)

    gw.chat = chat


def signup(c, pseudo="ada_l"):
    t = c.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]
    return t, {"Authorization": f"Bearer {t}"}


def first_space(c, h):
    return c.get("/api/spaces", headers=h).json()["spaces"][0]


def messages(c, h, sid):
    return c.get(f"/api/spaces/{sid}/messages", headers=h).json()


def events_of(text):
    return [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]


def test_an_answer_keeps_being_written_after_the_learner_leaves(tmp_path):
    app = make_app(tmp_path / "t.db")
    with TestClient(app) as c:
        slow_gateway(app)
        _, h = signup(c)
        sid = first_space(c, h)["id"]
        # start a turn and leave as soon as the first event arrives (closing the page)
        with c.stream("POST", f"/api/spaces/{sid}/ask", json={"message": "Explain photosynthesis", "web": False}, headers=h) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    break
        data = messages(c, h, sid)
        assert data["running"] is True
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"] and data["messages"][1]["meta"]["pending"]
        # a second message while it runs is refused instead of starting a parallel turn
        assert c.post(f"/api/spaces/{sid}/ask", json={"message": "and why?"}, headers=h).status_code == 409
        # coming back: reconnect, the turn is replayed from the start and followed to the end
        evs = events_of(c.get(f"/api/spaces/{sid}/turn", headers=h).text)
        kinds = [e["type"] for e in evs]
        assert kinds[0] == "stage" and "draft" in kinds and "final" in kinds and kinds[-1] == "done"
        data = messages(c, h, sid)
        assert data["running"] is False
        final = data["messages"][1]
        assert final["content"].startswith("## ") and final["meta"]["visual"] and "pending" not in final["meta"]
        assert c.get(f"/api/spaces/{sid}/turn", headers=h).status_code == 204       # nothing running any more


def test_progress_is_saved_while_writing_and_a_stopped_answer_keeps_its_text(tmp_path):
    app = make_app(tmp_path / "t.db")
    with TestClient(app) as c:
        slow_gateway(app, 0.3)
        _, h = signup(c)
        sid = first_space(c, h)["id"]
        with c.stream("POST", f"/api/spaces/{sid}/ask", json={"message": "Explain photosynthesis", "web": False}, headers=h) as r:
            for line in r.iter_lines():
                if line.startswith("data: ") and json.loads(line[6:])["type"] == "draft":
                    break
        saved = messages(c, h, sid)["messages"][1]
        assert saved["meta"]["pending"] and saved["content"].startswith("Quick draft")   # the draft is already stored
        assert c.delete(f"/api/spaces/{sid}/turn", headers=h).json()["ok"]
        end = time.time() + 5
        while messages(c, h, sid)["running"] and time.time() < end:
            time.sleep(0.05)
        stopped = messages(c, h, sid)["messages"][1]
        assert stopped["meta"].get("stopped") and stopped["content"]


def test_a_restart_keeps_the_partial_answer_and_marks_it_interrupted(tmp_path):
    db_path = tmp_path / "t.db"
    app = make_app(db_path)
    with TestClient(app) as c:
        _, h = signup(c)
        sid = first_space(c, h)["id"]
        db = app.state.db
        db.execute("INSERT INTO space_messages (space_id, role, content, ts) VALUES (?, 'user', 'Explain osmosis', ?)", (sid, time.time()))
        events = [{"type": "stage", "stage": "drafting"}, {"type": "draft", "text": "Quick draft: water moves across a membrane."}]
        db.execute("INSERT INTO space_messages (space_id, role, content, meta, ts) VALUES (?, 'assistant', '', ?, ?)",
                   (sid, json.dumps({"pending": True, "events": events}), time.time()))
    app2 = make_app(db_path)        # the server restarts
    with TestClient(app2) as c:
        t = c.post("/api/auth/login", json={"pseudo": "ada_l", "password": "secret1"}).json()["token"]
        h = {"Authorization": f"Bearer {t}"}
        data = messages(c, h, sid)
        last = data["messages"][-1]
        assert data["running"] is False
        assert last["meta"]["interrupted"] and last["content"] == "Quick draft: water moves across a membrane."


def _until(ws, kind, limit=120):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind}")


def test_a_study_room_keeps_its_explanation_answers_and_quiz_after_a_restart(tmp_path):
    db_path = tmp_path / "t.db"
    app = make_app(db_path)
    with TestClient(app) as c:
        ta, h = signup(c, "alice")
        code = c.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=h).json()["code"]
        notes = ("Photosynthesis takes place in the chloroplasts of plant cells. Chlorophyll absorbs light energy. "
                 "The Calvin cycle builds glucose from carbon dioxide.")
        with c.websocket_connect(f"/ws/rooms/{code}?token={ta}") as ws:
            ws.receive_json()
            # room material: only members may add it, and it is stored with the room
            assert c.post(f"/api/materials/file?scope={code}", content=notes.encode(), headers={**h, "X-Filename": "bio.txt"}).status_code == 200
            end = time.time() + 6
            while time.time() < end and c.get(f"/api/materials?scope={code}", headers=h).json()["documents"][0]["status"] != "ready":
                time.sleep(0.05)
            ws.send_json({"type": "topic", "topic": "Photosynthesis"})
            _until(ws, "topic")
            ws.send_json({"type": "explain"})
            explanation = _until(ws, "explanation")["data"]
            _until(ws, "busy")
            ws.send_json({"type": "ask", "question": "Where does photosynthesis take place?"})
            _until(ws, "answer")
            _until(ws, "busy")
            ws.send_json({"type": "quiz_start"})
            _until(ws, "quiz")
    app2 = make_app(db_path)        # restart: the room is rebuilt from the database
    with TestClient(app2) as c:
        ta = c.post("/api/auth/login", json={"pseudo": "alice", "password": "secret1"}).json()["token"]
        with c.websocket_connect(f"/ws/rooms/{code}?token={ta}") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot" and snap["topic"] == "Photosynthesis"
            assert snap["explanation"]["text"] == explanation["text"] and snap["explanation"]["visual"]
            assert snap["qa"] and snap["qa"][0]["question"] == "Where does photosynthesis take place?"
            assert snap["quiz"] and snap["quiz"]["questions"]
            docs = c.get(f"/api/materials?scope={code}", headers={"Authorization": f"Bearer {ta}"}).json()["documents"]
            assert docs and docs[0]["status"] == "ready"           # the room's material is still in the room


def test_someone_who_comes_back_sees_the_explanation_being_generated(tmp_path):
    app = make_app(tmp_path / "t.db")
    with TestClient(app) as c:
        slow_gateway(app, 0.25)
        ta, h = signup(c, "alice")
        tb, _ = signup(c, "bob")
        code = c.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=h).json()["code"]
        with c.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a:
            a.receive_json()
            a.send_json({"type": "topic", "topic": "Photosynthesis"})
            _until(a, "topic")
            a.send_json({"type": "explain"})
            while True:     # wait until the draft exists
                m = a.receive_json()
                if m["type"] == "tutor" and m["event"]["type"] == "draft":
                    break
            with c.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b:     # joins mid-way
                snap = b.receive_json()
                assert snap["busy"] == "explain"
                kinds = [e["type"] for e in snap["live_explain"]]
                assert "sources" in kinds and "draft" in kinds              # the progress so far is replayed
                assert _until(b, "explanation")["data"]["text"]           # and the rest arrives
            assert app.state.hub.rooms[code].live_explain is None
