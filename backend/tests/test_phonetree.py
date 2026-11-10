"""Pocket tree: an independent, link-based anti-distraction tree (app.phonetree), separate from the
study-forest tab tracking in app.rooms. Growth/wither timing is pure and tested directly (no sleeping);
one end-to-end test exercises the WebSocket + REST surface with tiny, monkeypatched timings."""
import time

import pytest
from fastapi.testclient import TestClient

from app import phonetree, rooms
from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings
from app.phonetree import PhoneTree, make_tree_token, read_tree_token

from .test_app import auth, recv_until, signup

T0 = 1_000_000.0


def tree(**over):
    base = dict(room_code="LIVE-DEEP", owner_id=1, pseudo="ada", started_at=T0, last=T0)
    base.update(over)
    return PhoneTree(**base)


# ------------------------------------------------------------------ token
def test_a_token_round_trips_and_rejects_tampering():
    tok = make_tree_token("s3cret-enough-for-tests", "LIVE-DEEP", 7, "ada")
    data = read_tree_token("s3cret-enough-for-tests", tok)
    assert data == {**data, "room": "LIVE-DEEP", "uid": 7, "pseudo": "ada"}
    assert read_tree_token("s3cret-enough-for-tests", tok + "x") is None
    assert read_tree_token("wrong-secret-entirely", tok) is None
    assert read_tree_token("s3cret-enough-for-tests", "not-a-token") is None


def test_an_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(phonetree, "PHONE_TREE_TTL", -1)  # already expired the instant it is minted
    tok = make_tree_token("s3cret-enough-for-tests", "LIVE-DEEP", 7, "ada")
    assert read_tree_token("s3cret-enough-for-tests", tok) is None


# ------------------------------------------------------------------ pure growth/wither logic
def test_a_visible_tree_grows_and_finishes_grown_once_full():
    t = tree()
    assert phonetree._advance(t, T0 + 10) is None
    assert t.grown == 10 and t.state == "growing"
    event = phonetree._advance(t, T0 + phonetree.PHONE_TREE_FULL_S)
    assert event == "grown" and t.state == "grown"


def test_hiding_for_less_than_the_grace_does_not_wither_it():
    t = tree()
    phonetree._advance(t, T0 + 5)
    phonetree._set_visible(t, False, T0 + 5)
    assert phonetree._advance(t, T0 + 5 + phonetree.PHONE_TREE_GRACE_S - 1) is None
    assert t.state == "growing"


def test_hiding_past_the_grace_withers_it():
    t = tree()
    phonetree._advance(t, T0 + 5)
    phonetree._set_visible(t, False, T0 + 5)
    event = phonetree._advance(t, T0 + 5 + phonetree.PHONE_TREE_GRACE_S)
    assert event == "wilted" and t.state == "wilted"


def test_coming_back_before_the_grace_resets_the_away_clock():
    t = tree()
    phonetree._advance(t, T0 + 5)
    phonetree._set_visible(t, False, T0 + 5)
    phonetree._advance(t, T0 + 5 + phonetree.PHONE_TREE_GRACE_S - 1)
    phonetree._set_visible(t, True, T0 + 5 + phonetree.PHONE_TREE_GRACE_S - 1)   # back in time
    assert t.hidden_since is None
    # a further absence shorter than the full grace, measured from the NEW hidden_since, must not wither it
    phonetree._set_visible(t, False, T0 + 100)
    assert phonetree._advance(t, T0 + 100 + phonetree.PHONE_TREE_GRACE_S - 1) is None
    assert t.state == "growing"


def test_a_wilted_tree_stays_wilted_and_stops_counting():
    t = tree(state="wilted", hidden_since=T0)
    assert phonetree._advance(t, T0 + 1000) is None
    assert t.state == "wilted" and t.grown == 0.0


def test_finishing_logs_into_the_rooms_shared_forest_log():
    room = rooms.Room({"code": "LIVE-DEEP", "kind": "live", "name": "Deep", "topic": "", "capacity": 50, "focus_min": 25, "break_min": 5})
    t = tree(grown=42.0)
    phonetree._log_forest(room, t, "wilted", T0)
    assert room.forest == [{"pseudo": "ada", "cycle": "phone", "state": "wilted", "at": T0, "source": "phone", "grown": 42}]
    assert room._tree_events == room.forest  # announce_trees() picks these up and broadcasts them


# ------------------------------------------------------------------ end to end
@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=build_fake()[0])
    with TestClient(app) as c:
        yield c


def test_minting_a_link_requires_login_and_a_live_room(client):
    assert client.get("/api/rooms/LIVE-DEEP/phone-tree").status_code == 401
    h = auth(signup(client, "ada_pt"))
    assert client.get("/api/rooms/LIVE-DEEP/phone-tree", headers=h).status_code == 200
    group = client.post("/api/rooms", json={"name": "A study room"}, headers=h).json()
    assert client.get(f"/api/rooms/{group['code']}/phone-tree", headers=h).status_code == 404
    assert client.get("/api/rooms/NOSUCH1/phone-tree", headers=h).status_code == 404


def test_the_socket_rejects_a_bad_token(client):
    with client.websocket_connect("/ws/phone-tree/not-a-real-token") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_a_pocket_tree_withers_logs_to_the_forest_and_alerts_the_owners_desktop(client, monkeypatch):
    monkeypatch.setattr(phonetree, "PHONE_TREE_TICK_S", 0.01)
    monkeypatch.setattr(phonetree, "PHONE_TREE_GRACE_S", 0.03)
    monkeypatch.setattr(phonetree, "_in_break", lambda room, now: False)   # don't depend on the wall clock's phase
    h = auth(signup(client, "ada_pt2"))
    token_desktop = h["Authorization"][7:]

    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token_desktop}") as desktop:
        recv_until(desktop, "snapshot")
        link = client.get("/api/rooms/LIVE-DEEP/phone-tree", headers=h).json()["token"]

        with client.websocket_connect(f"/ws/phone-tree/{link}") as phone:
            snap = phone.receive_json()
            assert {k: snap[k] for k in ("type", "state", "p", "grown", "visible", "away_for", "grace_s")} == \
                {"type": "phone_tree", "state": "growing", "p": 0.0, "grown": 0, "visible": True, "away_for": 0, "grace_s": 0.03}
            assert snap["room"] == "Deep work" and snap["phase"] in ("focus", "break") and snap["remaining"] >= 0
            forest = recv_until(desktop, "phone_trees")["trees"]   # the room is told its forest has a new tree
            assert forest == [{"pseudo": "ada_pt2", "state": "growing", "p": 0.0, "visible": True}]

            phone.send_json({"type": "visible", "visible": False})
            msg = None
            for _ in range(500):
                msg = phone.receive_json()
                if msg["state"] == "wilted":
                    break
            assert msg["state"] == "wilted"

        alert = recv_until(desktop, "phone_tree_alert")
        assert alert["pseudo"] == "ada_pt2"


# ------------------------------------------------------------------ rooms' clock: breaks pause the tree
def test_during_a_break_a_hidden_tree_neither_withers_nor_grows():
    t = tree()
    phonetree._set_visible(t, False, T0)
    assert phonetree._advance(t, T0 + phonetree.PHONE_TREE_GRACE_S * 5, paused=True) is None
    assert t.state == "growing" and t.grown == 0


def test_the_grace_restarts_when_focus_resumes_after_a_break():
    t = tree()
    phonetree._set_visible(t, False, T0)
    phonetree._advance(t, T0 + 200, paused=True)                  # break ends at T0 + 200
    assert phonetree._advance(t, T0 + 200 + phonetree.PHONE_TREE_GRACE_S - 1) is None
    assert phonetree._advance(t, T0 + 200 + phonetree.PHONE_TREE_GRACE_S) == "wilted"


def test_the_phone_view_names_the_room_and_the_time_left_in_the_phase():
    room = rooms.Room({"code": "LIVE-X", "kind": "live", "name": "Deep work", "topic": "", "capacity": 10, "focus_min": 25, "break_min": 5})
    t = tree()
    view = phonetree._view(t, room)
    assert view["room"] == "Deep work"
    assert view["phase"] in ("focus", "break")
    assert 0 <= view["remaining"] <= (25 * 60 if view["phase"] == "focus" else 5 * 60)


def test_the_room_forest_lists_only_that_rooms_pocket_trees(monkeypatch):
    monkeypatch.setattr(phonetree, "_trees", {
        ("LIVE-A", 1): tree(room_code="LIVE-A", owner_id=1, pseudo="zoe", grown=90),
        ("LIVE-A", 2): tree(room_code="LIVE-A", owner_id=2, pseudo="ada"),
        ("LIVE-B", 3): tree(room_code="LIVE-B", owner_id=3, pseudo="bob"),
    })
    assert phonetree.room_pocket_trees("LIVE-A") == [
        {"pseudo": "ada", "state": "growing", "p": 0.0, "visible": True},
        {"pseudo": "zoe", "state": "growing", "p": 0.5, "visible": True},
    ]


def test_a_live_room_snapshot_carries_its_pocket_forest(client):
    token = signup(client, "ada_snap")
    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token}") as ws:
        snap = recv_until(ws, "snapshot")
        assert isinstance(snap["pocket_trees"], list)
