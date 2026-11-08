"""Spaced-repetition revision: quiz -> cards, due scheduling, SM-2 grading."""
import time
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.review import schedule

from .test_app import auth, signup


@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
    with TestClient(app) as c:
        yield c


def test_quiz_becomes_review_cards(client):
    token = signup(client, "ada_l")
    assert client.get("/api/review/stats", headers=auth(token)).json() == {"total": 0, "due": 0}
    added = client.post("/api/review/add_quiz", json={"topic": "Thales theorem"}, headers=auth(token)).json()["added"]
    assert added >= 3
    # the same quiz again adds nothing (cards are de-duplicated by their question)
    assert client.post("/api/review/add_quiz", json={"topic": "Thales theorem"}, headers=auth(token)).json()["added"] == 0
    stats = client.get("/api/review/stats", headers=auth(token)).json()
    assert stats["total"] == added and stats["due"] == added  # brand-new cards are due now
    cards = client.get("/api/review/due", headers=auth(token)).json()["cards"]
    assert len(cards) == added and all(c["front"] and c["back"] and c["topic"] == "Thales theorem" for c in cards)


def test_grading_reschedules_and_removes_from_due(client):
    token = signup(client, "ada_l")
    client.post("/api/review/add_quiz", json={"topic": "Photosynthesis"}, headers=auth(token))
    cards = client.get("/api/review/due", headers=auth(token)).json()["cards"]
    first = cards[0]["id"]
    r = client.post("/api/review/grade", json={"id": first, "grade": "good"}, headers=auth(token)).json()
    assert r["ok"] and r["interval_days"] >= 1  # a good card is pushed at least a day out
    due_ids = [c["id"] for c in client.get("/api/review/due", headers=auth(token)).json()["cards"]]
    assert first not in due_ids  # no longer due today

    # "again" keeps a card in the session (due within minutes, not days)
    other = due_ids[0]
    r2 = client.post("/api/review/grade", json={"id": other, "grade": "again"}, headers=auth(token)).json()
    assert r2["interval_days"] == 0


def test_grade_validation_and_ownership(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    client.post("/api/review/add_quiz", json={"topic": "Thales theorem"}, headers=auth(ta))
    cid = client.get("/api/review/due", headers=auth(ta)).json()["cards"][0]["id"]
    assert client.post("/api/review/grade", json={"id": cid, "grade": "nope"}, headers=auth(ta)).status_code == 400
    assert client.post("/api/review/grade", json={"id": cid, "grade": "good"}, headers=auth(tb)).status_code == 404  # not bob's card


def test_sm2_schedule_unit():
    base = {"ease": 2.5, "reps": 0, "lapses": 0, "interval_days": 0.0, "due_at": 0}
    now = 1000.0
    good1 = schedule(base, 2, now)
    assert good1["reps"] == 1 and good1["interval_days"] == 1 and good1["due_at"] == now + 86400
    good2 = schedule({**base, "reps": 1, "interval_days": 1}, 2, now)
    assert good2["interval_days"] == 6
    again = schedule({**base, "reps": 5, "interval_days": 30}, 0, now)
    assert again["reps"] == 0 and again["lapses"] == 1 and again["due_at"] == now + 600
    hard = schedule({**base, "reps": 3, "interval_days": 10, "ease": 2.5}, 1, now)
    easy = schedule({**base, "reps": 3, "interval_days": 10, "ease": 2.5}, 3, now)
    assert hard["interval_days"] < easy["interval_days"]  # hard learns slower than easy
