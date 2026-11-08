"""Study forest: a tree grows while a member's device reports "focused" during the focus phase; stepping away
for too long wilts it; finishing the focus phase plants it. Rules live in app.rooms (Room.update_trees)."""
import types

import pytest
from fastapi.testclient import TestClient

from app import rooms
from app.config import load_settings
from app.main import create_app
from app.rooms import Member, Room, live_cycle

from .test_app import recv_until, signup

ROW = {"code": "T-FOREST", "kind": "live", "name": "Forest", "topic": "", "capacity": 50, "focus_min": 25, "break_min": 5}
FOCUS, TOTAL = 25 * 60, 30 * 60
BASE = 100 * TOTAL  # the start of cycle 100 (rooms are epoch aligned)


def make_room(row=ROW):
    return Room(dict(row))


def join(room, uid, pseudo, focused=True, forest=True):
    m = Member(uid, pseudo, None, 0.0, focused=focused, forest=forest)
    room.members[uid] = m
    return m


def report(room, m, value, now):
    """What the 'focus' message handler does: settle the old state first, then apply the new one."""
    room.update_trees(now)
    m.focused = value
    room.update_trees(now)


def view(room, uid, now):
    return room.tree_view(uid, live_cycle(room, now))


def test_a_focused_member_plants_a_tree_that_grows_with_focused_time():
    room = make_room()
    ada = join(room, 1, "ada")
    room.update_trees(BASE + 10)
    assert room.trees[1].t0 == 10 and view(room, 1, BASE + 10)["state"] == "growing"
    room.update_trees(BASE + 100)                   # 90 s of focus
    v = view(room, 1, BASE + 100)
    assert v["state"] == "growing" and v["avail"] == rooms.TREE_FULL_S   # looks full-grown after TREE_FULL_S of focus
    assert v["p"] == pytest.approx(90 / rooms.TREE_FULL_S, abs=1e-3)
    assert ada.focused is True


def test_only_forest_members_get_a_tree():
    room = make_room()
    join(room, 1, "ada", forest=False)
    join(room, 2, "bob", forest=True)
    room.update_trees(BASE + 5)
    assert set(room.trees) == {2}


def test_a_member_who_never_reports_focus_gets_no_tree():
    room = make_room()
    join(room, 1, "ada", focused=None)
    room.update_trees(BASE + 5)
    assert room.trees == {}


def test_a_short_step_away_is_fine_a_long_one_wilts_the_tree():
    room = make_room()
    ada = join(room, 1, "ada")
    room.update_trees(BASE)
    report(room, ada, False, BASE + 100)           # leaves the tab
    report(room, ada, True, BASE + 120)            # back after 20 s (< 30 s grace)
    assert view(room, 1, BASE + 120)["state"] == "growing"
    report(room, ada, False, BASE + 200)           # leaves again: 20 s used, 10 s left
    room.update_trees(BASE + 205)                   # 25 s away in total
    assert view(room, 1, BASE + 205)["state"] == "growing"
    room.update_trees(BASE + 211)                   # 31 s away in total
    assert view(room, 1, BASE + 211)["state"] == "wilted"
    assert [(e["pseudo"], e["state"]) for e in room.forest] == [("ada", "wilted")]


def test_someone_who_only_peeks_leaves_no_wilted_tree_behind():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + 10)                    # 10 s of growth, then the page is closed
    del room.members[1]
    room.update_trees(BASE + 60)                    # long past the grace period
    assert 1 not in room.trees and room.forest == [] and room.pop_tree_events() == []


def test_a_member_who_peeks_can_plant_again_when_they_return():
    room = make_room()
    ada = join(room, 1, "ada")
    room.update_trees(BASE)
    report(room, ada, False, BASE + 10)
    room.update_trees(BASE + 50)                    # away 40 s after only 10 s of growth: dropped
    assert 1 not in room.trees
    report(room, ada, True, BASE + 100)             # back: a fresh tree, planted later in the phase
    assert room.trees[1].t0 == 100 and view(room, 1, BASE + 100)["state"] == "growing"


def test_a_wilted_tree_stays_wilted_keeps_its_progress_and_is_not_replanted_this_cycle():
    room = make_room()
    ada = join(room, 1, "ada")
    room.update_trees(BASE)
    report(room, ada, False, BASE + 90)             # 90 s of growth (half of TREE_FULL_S), then away
    room.update_trees(BASE + 130)                   # 40 s away: past the 30 s grace → wilts, keeping its progress
    frozen = view(room, 1, BASE + 130)
    assert frozen["state"] == "wilted" and 0.4 < frozen["p"] < 0.6
    report(room, ada, True, BASE + 800)             # comes back, but this focus phase is lost for the tree
    assert view(room, 1, BASE + 800) == frozen
    assert len(room.forest) == 1


def test_finishing_the_focus_phase_plants_the_tree_and_it_stays_visible_during_the_break():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + FOCUS + 1)             # the break has started
    v = view(room, 1, BASE + FOCUS + 1)
    assert v == {"state": "grown", "p": 1.0, "avail": rooms.TREE_FULL_S}
    assert [e["state"] for e in room.forest] == ["grown"]
    assert room.pop_tree_events() and room.pop_tree_events() == []  # each finished tree is announced once
    room.update_trees(BASE + FOCUS + 100)           # still the break: no second entry, no new tree
    assert len(room.forest) == 1 and view(room, 1, BASE + FOCUS + 100)["state"] == "grown"


def test_the_next_focus_phase_plants_a_fresh_tree_and_the_old_one_is_hidden_until_then():
    room = make_room()
    ada = join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + FOCUS + 1)
    nxt = BASE + TOTAL                              # start of the next cycle
    ada.focused = None                              # not reporting yet
    room.update_trees(nxt + 1)
    assert view(room, 1, nxt + 1) is None
    report(room, ada, True, nxt + 5)
    v = view(room, 1, nxt + 5)
    assert v["state"] == "growing" and room.trees[1].cycle == 101


def test_joining_too_late_in_a_focus_phase_plants_nothing():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE + FOCUS - 60)            # only 60 s left (< 120 s minimum)
    assert room.trees == {}


def test_nothing_is_planted_during_the_break():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE + FOCUS + 30)
    assert room.trees == {}


def test_disconnecting_counts_as_being_away_and_the_tree_wilts():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + 200)                   # what the socket's finally-block does before removing the member
    del room.members[1]
    room.update_trees(BASE + 220)
    assert view(room, 1, BASE + 220)["state"] == "growing"
    room.update_trees(BASE + 235)
    assert view(room, 1, BASE + 235)["state"] == "wilted"


def test_reconnecting_quickly_keeps_the_same_tree():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + 200)
    del room.members[1]
    join(room, 1, "ada")                            # page refresh: same person, new socket
    room.update_trees(BASE + 210)
    assert view(room, 1, BASE + 210)["state"] == "growing" and room.trees[1].t0 == 0


def test_short_rooms_scale_the_rules_down():
    demo = make_room({**ROW, "focus_min": 1, "break_min": 1})
    assert demo.tree_rules() == (15, 30)
    assert make_room().tree_rules() == (30, 120)


def test_old_finished_trees_are_dropped_but_stay_in_the_forest_log():
    room = make_room()
    join(room, 1, "ada")
    room.update_trees(BASE)
    room.update_trees(BASE + FOCUS + 1)
    room.members[1].focused = None
    room.update_trees(BASE + 4 * TOTAL)
    assert 1 not in room.trees and len(room.forest) == 1


def test_group_rooms_have_no_trees():
    room = make_room({**ROW, "kind": "group"})
    join(room, 1, "ada")
    room.update_trees(BASE + 5)
    assert room.trees == {}


# ----------------------------------------------------------------- over the WebSocket
@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
    with TestClient(app) as c:
        yield c


def test_forest_over_the_socket_grows_then_wilts_and_announces_it(client, monkeypatch):
    now = [BASE + 100.0]                              # pin the room clock inside a focus phase (LIVE-DEEP is 25/5)
    monkeypatch.setattr(rooms, "time", types.SimpleNamespace(time=lambda: now[0]))
    token = signup(client, "ada_l")
    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token}") as ws:
        snap = recv_until(ws, "snapshot")
        assert snap["forest"] == []                   # nothing planted yet
        ws.send_json({"type": "forest_join"})
        ws.send_json({"type": "focus", "focused": True})
        p = recv_until(ws, "presence")["presence"]
        mine = next(m for m in p["members"] if m["pseudo"] == "ada_l")
        assert mine["tree"]["state"] == "growing" and p["at"] == now[0]

        now[0] += 90
        ws.send_json({"type": "focus", "focused": True})
        p = recv_until(ws, "presence")["presence"]
        mine = next(m for m in p["members"] if m["pseudo"] == "ada_l")
        assert 0.4 < mine["tree"]["p"] < 0.6          # ~90 s of the 180 s of focus needed to look full-grown

        ws.send_json({"type": "focus", "focused": False})
        now[0] += 40                                   # away for longer than the 30 s grace
        ws.send_json({"type": "focus", "focused": False})
        event = recv_until(ws, "tree", limit=40)["entry"]
        assert event["pseudo"] == "ada_l" and event["state"] == "wilted"


def test_a_plain_live_room_view_is_unaffected_by_the_forest(client):
    token = signup(client, "ada_l")
    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token}") as ws:
        recv_until(ws, "snapshot")
        ws.send_json({"type": "focus", "focused": True})   # no forest_join
        p = recv_until(ws, "presence")["presence"]
        assert all("tree" not in m for m in p["members"])


def test_the_forest_room_is_seeded_and_listed(client):
    token = signup(client, "ada_l")
    r = client.get("/api/rooms?kind=live", headers={"Authorization": f"Bearer {token}"}).json()["rooms"]
    forest = next(x for x in r if x["code"] == "LIVE-FOREST")
    assert (forest["focus_min"], forest["break_min"]) == (25, 5)
