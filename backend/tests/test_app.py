"""End-to-end through the HTTP and WebSocket API, with the fake service in-process."""
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
    with TestClient(app) as c:
        yield c


def signup(client, pseudo="ada_l", password="secret1"):
    r = client.post("/api/auth/register", json={"pseudo": pseudo, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def recv_until(ws, kind, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"never received {kind}")


def test_accounts_are_pseudonymous_and_validated(client):
    assert client.post("/api/auth/register", json={"pseudo": "a b", "password": "secret1"}).status_code == 400
    signup(client)
    assert client.post("/api/auth/register", json={"pseudo": "ADA_L", "password": "secret1"}).status_code == 409
    assert client.post("/api/auth/login", json={"pseudo": "ada_l", "password": "wrong"}).status_code == 401
    token = client.post("/api/auth/login", json={"pseudo": "ada_l", "password": "secret1"}).json()["token"]
    assert client.get("/api/auth/me", headers=auth(token)).json()["pseudo"] == "ada_l"
    assert client.get("/api/auth/me").status_code == 401


def test_diagnostic_sets_a_learning_style(client):
    h = auth(signup(client))
    assert len(client.get("/api/study/diagnostic", headers=h).json()["questions"]) == 4
    r = client.post("/api/study/diagnostic", json={"answers": [0, 0, 0, 1]}, headers=h).json()
    assert r["learning_style"] == "visual"
    assert client.get("/api/auth/me", headers=h).json()["learning_style"] == "visual"


def test_every_explanation_comes_with_an_illustration(client):
    h = auth(signup(client))
    thales = client.post("/api/study/explain", json={"topic": "Thales theorem"}, headers=h).json()
    assert thales["visual"]["template"] == "thales" and len(thales["steps"]) >= 3
    other = client.post("/api/study/explain", json={"topic": "French revolution causes"}, headers=h).json()
    assert other["visual"]["template"] == "concept_map"
    assert client.post("/api/study/explain", json={"topic": " "}, headers=h).status_code == 400


def test_second_identical_explanation_costs_nothing(client):
    h = auth(signup(client))
    client.post("/api/study/explain", json={"topic": "Photosynthesis"}, headers=h)
    before = client.get("/api/ai/metrics").json()["calls"]["total"]
    again = client.post("/api/study/explain", json={"topic": "  photosynthesis ".strip().title()}, headers=h).json()
    assert again["meta"]["cached"] is True
    assert client.get("/api/ai/metrics").json()["calls"]["total"] == before


def test_hint_ladder_and_quiz_flow_and_progress(client):
    h = auth(signup(client))
    topic = "Thales theorem"
    hints = [client.post("/api/study/hint", json={"topic": topic, "question": "Find DE", "level": n}, headers=h).json() for n in (1, 2, 3)]
    assert len({x["hint"] for x in hints}) == 3
    quiz = client.post("/api/study/quiz", json={"topic": topic}, headers=h).json()
    assert len(quiz["questions"]) == 4 and "answer" not in quiz["questions"][0]
    ans = client.post("/api/study/quiz/answer", json={"topic": topic, "question": 1, "choice": 1}, headers=h).json()
    assert ans["correct"] is True and ans["why"]
    client.post("/api/study/quiz/finish", json={"topic": topic, "correct": 3, "total": 4}, headers=h)
    p = client.get("/api/progress", headers=h).json()
    assert p["quizzes"] == 1 and p["accuracy"] == 0.75 and p["xp"] == 30


def test_group_room_capacity_is_enforced(client):
    tokens = [signup(client, f"user_{i}") for i in range(3)]
    room = client.post("/api/rooms", json={"name": "Maths", "capacity": 2}, headers=auth(tokens[0])).json()
    assert client.post("/api/rooms", json={"name": "Too big", "capacity": 11}, headers=auth(tokens[0])).status_code == 400
    assert client.post("/api/rooms", json={"name": "Solo", "capacity": 1}, headers=auth(tokens[0])).status_code == 400
    url = lambda t: f"/ws/rooms/{room['code']}?token={t}"
    with client.websocket_connect(url(tokens[0])) as a, client.websocket_connect(url(tokens[1])) as b:
        assert a.receive_json()["type"] == "snapshot" and b.receive_json()["type"] == "snapshot"
        with client.websocket_connect(url(tokens[2])) as c:
            assert c.receive_json()["code"] == "room_full"


def test_group_chat_anonymous_messages_hide_the_author(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    room = client.post("/api/rooms", json={"name": "Physics", "capacity": 4}, headers=auth(ta)).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}") as a, client.websocket_connect(f"/ws/rooms/{room['code']}?token={tb}") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"type": "chat", "text": "I do not get step 2", "anonymous": True})
        msg = recv_until(b, "chat")["msg"]
        assert msg["author"] == "Anonymous" and msg["anon"] is True
        b.send_json({"type": "chat", "text": "Same here"})
        seen = [recv_until(a, "chat")["msg"] for _ in range(2)]  # a also receives its own anonymous message first
        assert seen[0]["author"] == "Anonymous" and seen[1]["author"] == "bob"


def test_group_room_shares_one_explanation_and_a_quiz(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    room = client.post("/api/rooms", json={"name": "Geometry", "capacity": 4}, headers=auth(ta)).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}") as a, client.websocket_connect(f"/ws/rooms/{room['code']}?token={tb}") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"type": "topic", "topic": "Thales theorem"})
        assert recv_until(b, "topic")["topic"] == "Thales theorem"
        a.send_json({"type": "explain"})
        assert recv_until(b, "explanation")["data"]["visual"]["template"] == "thales"
        a.send_json({"type": "quiz_start"})
        assert len(recv_until(b, "quiz")["quiz"]["questions"]) == 4
        a.send_json({"type": "answer", "q": 0, "choice": 0})
        b.send_json({"type": "answer", "q": 0, "choice": 2})
        recv_until(a, "quiz_progress")
        a.send_json({"type": "quiz_reveal"})
        results = recv_until(b, "quiz_results")["results"]
        assert results["questions"][0]["picks"][:3] == [1, 0, 1]
        assert results["scores"][0] == {"pseudo": "alice", "correct": 1, "total": 4}
    assert client.get("/api/ai/metrics").json()["calls"]["mid"] == 3  # figure + explanation text + quiz draft all use mid


def test_live_room_follows_a_fixed_schedule_and_counts_focus(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    rooms = client.get("/api/rooms?kind=live").json()["rooms"]
    assert {r["code"] for r in rooms} >= {"LIVE-DEEP"}
    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={ta}") as a, client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={tb}") as b:
        snap = a.receive_json()
        assert snap["timer"]["started_at"] == 0 and snap["timer"]["focus_s"] == 1500
        b.receive_json()
        a.send_json({"type": "focus", "focused": True})
        b.send_json({"type": "focus", "focused": False})
        for _ in range(10):
            p = recv_until(a, "presence")["presence"]
            if p["tracking"] == 2:
                break
        assert p["online"] == 2 and p["focused"] == 1
        a.send_json({"type": "chat", "text": "ignored in live"})
        a.send_json({"type": "reaction", "emoji": "🔥"})
        assert recv_until(b, "reaction")["emoji"] == "🔥"


def test_simulation_endpoint_drives_the_resilience_demo(client):
    h = auth(signup(client))
    assert client.post("/api/ai/simulate", json={"mode": "saturate"}).status_code == 200
    r = client.post("/api/study/explain", json={"topic": "Photosynthesis"}, headers=h).json()
    assert r["meta"]["degraded"] is True
    m = client.get("/api/ai/metrics").json()
    assert m["fallbacks"] == 2  # both mid calls (figure + explanation text) fall back to compact
