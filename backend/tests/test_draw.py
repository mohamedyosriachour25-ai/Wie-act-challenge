"""Pictionary drawing relay and the break-game opt-out.

Drawing: only the current performer may draw; strokes reach that game's players and are kept for the turn so a late
watcher can be caught up with op 'sync'. The server relays normalised points, it never renders. Opt-out: a player
who would rather rest is dropped from every game that break (handled in handle_event)."""
import asyncio

from app.rooms import Member, Room, handle_draw

ROW = {"code": "T-PIC", "kind": "live", "name": "Pic", "topic": "", "capacity": 50, "focus_min": 25, "break_min": 5}


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def run(coro):
    asyncio.run(coro)


def _room_with_pictionary(current_uid, players):
    """A live room with a started pictionary sub-event and `current_uid` performing, each player on a fake socket."""
    room = Room(dict(ROW))
    members = {}
    for uid, name in players:
        members[uid] = room.members[uid] = Member(uid, name, FakeWS(), 0.0)
    sv = {"game": "pictionary", "optins": {uid: name for uid, name in players},
          "scores": {}, "started": True, "current": current_uid}
    room.events[5] = {"games": {"pictionary": sv}}
    return room, members, sv


def test_only_the_performer_draws_and_players_receive_it():
    room, members, sv = _room_with_pictionary(1, [(1, "ada"), (2, "bob")])
    run(handle_draw(room, members[1], {"op": "stroke", "pts": [[0.1, 0.1], [0.2, 0.2]], "c": 3, "w": 0.01}))
    assert len(sv["strokes"]) == 1 and sv["strokes"][0]["c"] == 3
    for m in members.values():  # the stroke reaches everyone in this game (the watcher, and the drawer's own echo)
        assert any(p["type"] == "draw" and p["op"] == "stroke" for p in m.ws.sent)


def test_a_non_performer_cannot_draw():
    room, members, sv = _room_with_pictionary(1, [(1, "ada"), (2, "bob")])
    run(handle_draw(room, members[2], {"op": "stroke", "pts": [[0.5, 0.5]], "c": 0}))
    assert sv.get("strokes", []) == [] and members[1].ws.sent == []


def test_clear_empties_the_buffer_and_is_broadcast():
    room, members, sv = _room_with_pictionary(1, [(1, "ada"), (2, "bob")])
    run(handle_draw(room, members[1], {"op": "stroke", "pts": [[0.1, 0.1]]}))
    run(handle_draw(room, members[1], {"op": "clear"}))
    assert sv["strokes"] == []
    assert any(p["op"] == "clear" for p in members[2].ws.sent)


def test_a_late_watcher_is_caught_up_with_sync():
    room, members, sv = _room_with_pictionary(1, [(1, "ada"), (2, "bob")])
    run(handle_draw(room, members[1], {"op": "stroke", "pts": [[0.1, 0.1], [0.2, 0.2]]}))
    members[2].ws.sent.clear()
    run(handle_draw(room, members[2], {"op": "sync"}))   # the watcher asks for the strokes so far
    replay = next(p for p in members[2].ws.sent if p["op"] == "replay")
    assert len(replay["strokes"]) == 1


def test_points_and_style_are_clamped_to_a_safe_range():
    room, members, sv = _room_with_pictionary(1, [(1, "ada")])
    run(handle_draw(room, members[1], {"op": "stroke", "pts": [[2.0, -1.0]], "c": 99, "w": 5.0}))
    s = sv["strokes"][0]
    assert s["pts"][0] == [1.0, 0.0] and s["c"] == 11 and s["w"] == 0.08


def test_a_garbage_stroke_is_ignored():
    room, members, sv = _room_with_pictionary(1, [(1, "ada")])
    run(handle_draw(room, members[1], {"op": "stroke", "pts": "not-a-list"}))
    run(handle_draw(room, members[1], {"op": "stroke"}))
    assert sv["strokes"] == []
