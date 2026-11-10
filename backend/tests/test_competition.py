"""Study-room competition: question validation, closeness + speed scoring, the server-timed game over the socket."""
import json

import pytest

from app import competition as comp
from app import scoring

from .test_app import auth, client, signup  # noqa: F401


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(comp, "REVEAL_S", 0.05)
    monkeypatch.setattr(comp, "LOBBY_MIN_S", 0.05)


def test_validation_keeps_only_well_formed_questions():
    raw = {"questions": [
        {"type": "mcq", "q": "Pick the right one", "options": ["a", "b", "c", "d"], "answer": 2, "why": "w"},
        {"type": "mcq", "q": "Duplicated options", "options": ["a", "a", "c", "d"], "answer": 0},
        {"type": "truefalse", "q": "Water boils at 100 C at sea level", "answer": "vrai"},
        {"type": "numeric", "q": "Speed of sound in air", "answer": "343", "tolerance": 5, "unit": "m/s"},
        {"type": "numeric", "q": "No number here", "answer": "many"},
        {"type": "short", "q": "Powerhouse of the cell", "answers": ["mitochondria", "mitochondrion"]},
        {"type": "essay", "q": "Unknown type"},
    ]}
    qs = comp.validate(raw)
    assert [q["type"] for q in qs] == ["mcq", "truefalse", "numeric", "short"]
    assert qs[1]["answer"] is True and qs[2]["answer"] == 343.0 and qs[2]["tolerance"] == 5
    with pytest.raises(ValueError):
        comp.validate({"questions": raw["questions"][:2]})


def test_closeness_rewards_being_right_or_near():
    num = {"type": "numeric", "answer": 20.0, "tolerance": 0.5, "unit": "V"}
    assert comp.closeness(num, 20.4) == 1.0                  # within tolerance
    assert 0.5 < comp.closeness(num, 23) < 1.0                # close
    assert comp.closeness(num, 40) == 0.0                     # far
    assert comp.closeness(num, "20,2") == 1.0                 # decimal comma accepted
    short = {"type": "short", "answers": ["mitochondria"]}
    assert comp.closeness(short, "Mitochondria") == 1.0 and comp.closeness(short, "mitocondria") == 0.5
    assert comp.closeness({"type": "mcq", "answer": 1}, 1) == 1.0 and comp.closeness({"type": "mcq", "answer": 1}, True) == 0.0
    assert comp.points_for(1.0, 0, 20) == 1000 and comp.points_for(1.0, 20000, 20) == 500 and comp.points_for(0.0, 0, 20) == 0
    assert comp.points_for(0.5, 10000, 20) == 375


def _until(ws, kind, limit=80):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind}")


RIGHT = {"mcq": 0, "truefalse": True, "numeric": 3, "exercise": 20, "short": "mitochondria"}
WRONG = {"mcq": 2, "truefalse": False, "numeric": 7, "exercise": 23, "short": "ribosome"}


def test_a_full_competition_over_the_room_socket(client):  # noqa: F811
    ta, tb = signup(client, "alice"), signup(client, "bob")
    code = client.post("/api/rooms", json={"name": "Electronics", "capacity": 4}, headers=auth(ta)).json()["code"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a, client.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"type": "topic", "topic": "Ohm's law"})
        _until(b, "topic")
        a.send_json({"type": "comp_start"})
        assert _until(b, "comp_preparing")["competition"]["by"] == "alice"
        assert _until(b, "comp_ready")["n"] == 5
        for i in range(5):
            q = _until(b, "comp_question")["question"]
            _until(a, "comp_question")
            assert q["i"] == i and not ({"answer", "answers", "tolerance", "why"} & set(q))     # nothing leaks before the reveal
            a.send_json({"type": "comp_answer", "i": i, "value": RIGHT[q["type"]]})
            assert _until(a, "comp_ack")["i"] == i
            b.send_json({"type": "comp_answer", "i": i, "value": WRONG[q["type"]]})
            b.send_json({"type": "comp_answer", "i": i, "value": RIGHT[q["type"]]})          # a second answer is ignored
            rev = _until(b, "comp_reveal")["reveal"]                                          # both answered: no waiting
            assert rev["i"] == i and rev["results"][0]["pseudo"] == "alice" and rev["results"][0]["points"] > 500
            if q["type"] in ("numeric", "exercise"):
                assert rev["results"][0]["closest"] is True                               # +100 to the nearest value
                assert rev["results"][1]["points"] > 0 if q["type"] == "exercise" else True  # 23 V for 20 V is close
            if q["type"] == "mcq":
                assert rev["counts"] == [1, 0, 1, 0]
        over = _until(b, "comp_over")
        assert [r["pseudo"] for r in over["leaderboard"]] == ["alice", "bob"] and over["leaderboard"][0]["correct"] == 5
        _until(a, "comp_over")
    db = client.app.state.db
    pts = {r["pseudo"]: r["s"] for r in db.all("SELECT u.pseudo, SUM(p.points) AS s FROM points p JOIN users u ON u.id=p.user_id WHERE p.rule='competition' GROUP BY u.pseudo")}
    assert pts["alice"] == 3 + 2 * 5 + 10 and pts["bob"] == 3 + 6          # take part + correct answers + podium (capped 40)
    saved = db.one("SELECT * FROM competitions")
    assert saved["players"] == 2 and json.loads(saved["questions"])[0]["right"] == 1
    assert scoring.RULES["competition"].kind == "social"


def test_late_joiner_sees_the_current_question_and_the_starter_can_stop(client):  # noqa: F811
    ta, tb = signup(client, "alice"), signup(client, "bob")
    code = client.post("/api/rooms", json={"name": "Bio", "capacity": 4}, headers=auth(ta)).json()["code"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a:
        a.receive_json()
        a.send_json({"type": "topic", "topic": "Cells"})
        _until(a, "topic")
        a.send_json({"type": "comp_start"})
        q = _until(a, "comp_question")["question"]
        with client.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b:
            snap = b.receive_json()
            assert snap["competition"]["status"] == "question" and snap["competition"]["question"]["i"] == q["i"]
            assert "answer" not in snap["competition"]["question"]
            b.send_json({"type": "comp_stop"})                     # not the starter or the owner: ignored
            a.send_json({"type": "comp_stop"})
            assert _until(b, "comp_cancelled")["by"] == "alice"


def test_step_focus_links_numbered_steps_to_figure_parts():
    from app.ai.tutor import numbered_steps
    from app.ai.visuals import step_focus

    text = "Intro\n1. Look at triangle ADE.\n2. Compare AD with AB.\n3. Conclude."
    steps = numbered_steps(text)
    assert len(steps) == 3
    assert step_focus(steps, {"template": "thales", "params": {}}) == ["ADE", "AD", ""]
    flow = {"template": "process_flow", "params": {"steps": [{"label": "Light reactions"}, {"label": "Calvin cycle"}]}}
    assert step_focus(["The light reactions split water", "Nothing here"], flow) == ["0", ""]
    assert step_focus(["x"], None) == [""]
