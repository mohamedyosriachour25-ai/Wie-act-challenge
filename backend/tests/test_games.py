"""Break games in live rooms: per-session turn timing, private words for everyone up front, the full order,
server-side guess judging (exact / close-and-blurred), turns ending on time, pass or everyone guessing,
and AI-written words + trivia with a built-in fallback."""
import asyncio
import random as _random

import pytest

from app import games, rooms

from .test_app import client, recv_until, signup  # noqa: F401  (client is a fixture)


def _drain_until(ws, kind, store=None, limit=200):
    store = [] if store is None else store
    for _ in range(limit):
        m = ws.receive_json()
        store.append(m)
        if m["type"] == kind:
            return m
    raise AssertionError(f"{kind} not received")


@pytest.fixture
def fast(monkeypatch):
    """Tiny timings, a pinned break phase, and a fixed (unshuffled) player order."""
    def setup(cycle, turn=0.4, turns=None):
        """`turns`: how many full turns the break has room for (None = plenty; the test ends the game)."""
        monkeypatch.setattr(rooms, "TURN_S", turn)
        monkeypatch.setattr(rooms, "TURN_LONG_S", turn)
        monkeypatch.setattr(rooms, "FIRST_PREP_S", 0.05)
        monkeypatch.setattr(rooms, "PREROLL_S", 0.05)
        monkeypatch.setattr(rooms, "TURN_GAP_S", 0.01)
        monkeypatch.setattr(rooms, "MIN_TURN_S", turn * 0.6)
        budget = 600.0 if turns is None else 0.05 + turns * (turn + 0.01) + turn * 0.3
        monkeypatch.setattr(rooms, "break_left", lambda room, now: budget)
        monkeypatch.setattr(rooms, "live_cycle", lambda room, now: {"phase": "break", "cycles": cycle, "into": 1.0})
        monkeypatch.setattr(_random, "shuffle", lambda seq: None)
    return setup


def _two_players(client, room, game, cycle):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    a = client.websocket_connect(f"/ws/rooms/{room}?token=" + ta).__enter__()
    b = client.websocket_connect(f"/ws/rooms/{room}?token=" + tb).__enter__()
    a.receive_json(); b.receive_json()
    a.send_json({"type": "event_optin", "cycle": cycle, "game": game}); _drain_until(b, "event_participants")
    b.send_json({"type": "event_optin", "cycle": cycle, "game": game}); _drain_until(a, "event_participants")
    a.send_json({"type": "event_start", "cycle": cycle})
    return a, b


# ------------------------------------------------------------------ timing by session
def test_turn_length_follows_the_rooms_break():
    long_room = rooms.Room({"code": "L", "kind": "live", "name": "Long", "topic": "", "capacity": 10, "focus_min": 50, "break_min": 10})
    short_room = rooms.Room({"code": "S", "kind": "live", "name": "Deep", "topic": "", "capacity": 10, "focus_min": 25, "break_min": 5})
    assert rooms.turn_timing(long_room) == (60, rooms.PREROLL_S)      # 50/10: a minute each
    assert rooms.turn_timing(short_room) == (30, 20)                  # shorter: 30 s each, 20 s to prepare first


# ------------------------------------------------------------------ words, order, private turn
def test_everyone_gets_a_private_word_and_the_full_order_up_front(client, fast):
    fast(21, turns=2)
    a, b = _two_players(client, "LIVE-DEEP", "pictionary", 21)
    ma, mb = [], []
    start = _drain_until(b, "game_start", mb)
    assert start["order"] == ["alice", "bob"] and start["per_turn"] == rooms.TURN_S
    wa = _drain_until(a, "game_word", ma); wb = _drain_until(b, "game_word", mb)
    assert wa["round"] == 0 and wa["swaps_left"] == rooms.MAX_SWAPS
    assert wa["word"] and wb["word"] and wa["word"] != wb["word"]
    _drain_until(a, "game_over", ma); _drain_until(b, "game_over", mb)
    a.__exit__(None, None, None); b.__exit__(None, None, None)
    # the word only ever reaches its owner (game_word / game_your_turn) until the turn ends
    assert not any(m.get("word") == wa["word"] for m in mb if m["type"] in ("game_start", "game_turn", "game_word", "game_your_turn"))
    assert [m["pseudo"] for m in mb if m["type"] == "game_turn"] == ["alice", "bob"]
    ends = [m for m in mb if m["type"] == "game_turn_end"]
    assert [e["word"] for e in ends] == [wa["word"], wb["word"]]          # revealed when each turn ends


def test_a_correct_guess_is_hidden_scored_and_ends_the_turn_when_everyone_has_it(client, fast):
    fast(22, turn=5)
    a, b = _two_players(client, "LIVE-DEEP", "mime", 22)
    word = _drain_until(a, "game_your_turn")["word"]                    # alice performs first
    _drain_until(b, "game_turn")
    b.send_json({"type": "chat", "text": f"is it {word}?"})
    got = _drain_until(b, "game_guessed")
    assert got["pseudo"] == "bob" and got["leaderboard"][0]["pseudo"] == "bob" and got["leaderboard"][0]["score"] > 100
    end = _drain_until(b, "game_turn_end")
    assert end["reason"] == "all" and end["pseudo"] == "alice"           # everyone guessed: next player, no waiting
    a.__exit__(None, None, None); b.__exit__(None, None, None)
    game_pts = {r["pseudo"]: r["s"] for r in client.app.state.db.all(
        "SELECT u.pseudo, SUM(p.points) AS s FROM points p JOIN users u ON u.id=p.user_id WHERE p.rule='game' GROUP BY u.pseudo")}
    assert game_pts["bob"] >= 5 and game_pts["alice"] == 2              # game points count toward the account score (v2: 1 per 20)


def test_a_close_guess_is_blurred_for_others_and_flagged_to_the_guesser(client, fast):
    fast(23, turn=5)
    a, b = _two_players(client, "LIVE-DEEP", "pictionary", 23)
    word = _drain_until(a, "game_your_turn")["word"]
    _drain_until(b, "game_turn")
    near = word[:-1] + ("x" if word[-1] != "x" else "y")                # one character off
    b.send_json({"type": "chat", "text": near})
    assert _drain_until(b, "game_close")["game"] == "pictionary"
    mine = _drain_until(b, "chat")["msg"]
    assert mine["text"] == near and mine.get("close") is True            # the guesser sees their own line
    theirs = _drain_until(a, "chat")["msg"]
    assert theirs["blur"] is True and theirs["text"] == "" and theirs["len"] == len(near)
    a.__exit__(None, None, None); b.__exit__(None, None, None)


def test_the_performer_can_pass_their_turn(client, fast):
    fast(24, turn=5)
    a, b = _two_players(client, "LIVE-DEEP", "mime", 24)
    _drain_until(a, "game_your_turn")
    a.send_json({"type": "turn_pass"})
    end = _drain_until(b, "game_turn_end")
    assert end["reason"] == "pass" and end["pseudo"] == "alice"
    nxt = _drain_until(b, "game_your_turn")
    assert nxt["round"] == 0 and nxt["index"] == 1                       # straight on to bob
    a.__exit__(None, None, None); b.__exit__(None, None, None)


def test_ordinary_chat_still_flows_during_a_game(client, fast):
    fast(25, turn=5)
    a, b = _two_players(client, "LIVE-DEEP", "mime", 25)
    _drain_until(a, "game_your_turn")
    b.send_json({"type": "chat", "text": "no idea, keep going"})
    msg = _drain_until(a, "chat")["msg"]
    assert msg["text"] == "no idea, keep going" and not msg.get("blur")
    a.__exit__(None, None, None); b.__exit__(None, None, None)


# ------------------------------------------------------------------ trivia content
def test_trivia_is_generated_per_break_and_scored_by_the_server(client, monkeypatch):
    monkeypatch.setattr(rooms, "live_cycle", lambda room, now: {"phase": "break", "cycles": 31, "into": 1.0})
    ta = signup(client, "alice")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a:
        a.receive_json()
        a.send_json({"type": "event_optin", "cycle": 31, "game": "trivia_race"})
        content = _drain_until(a, "game_content")
        assert content["cycle"] == 31 and len(content["trivia"]) >= 3 and content["source"] == "ai"
        q0 = content["trivia"][0]
        assert len(q0["o"]) == 4 and 0 <= q0["a"] <= 3
        wrong = (q0["a"] + 1) % 4
        a.send_json({"type": "game_result", "cycle": 31, "round": 0, "pick": wrong, "correct": True, "ms": 500})
        board = _drain_until(a, "event_scores")["leaderboard"]
        assert board[0]["score"] == 0                                    # the client's "correct" is not trusted
        a.send_json({"type": "game_result", "cycle": 31, "round": 1, "pick": content["trivia"][1]["a"], "ms": 500})
        assert _drain_until(a, "event_scores")["leaderboard"][0]["score"] > 0


def test_when_the_model_does_not_answer_the_built_in_banks_are_used(monkeypatch):
    async def silent(*a, **k):
        return None
    monkeypatch.setattr(games, "_ask", silent)
    data = asyncio.run(games.make_content(None, {"fr"}, 6, 5))
    assert data["source"] == {"trivia": "bank", "mime": "bank", "pictionary": "bank"}
    assert len(data["trivia"]) == 6 and len(data["words"]["mime"]) == 5
    for q in data["trivia"]:                                             # shuffled options keep the right answer
        original = next(x for x in games.TRIVIA_FALLBACK if x["q"] == q["q"])
        assert q["o"][q["a"]] == original["o"][original["a"]]
    assert games.localize(data["words"]["pictionary"][0], "fr") != ""


def test_the_banks_are_big_and_fully_translated():
    assert len(games.WORDS_FALLBACK["mime"]) >= 20 and len(games.WORDS_FALLBACK["pictionary"]) >= 20
    assert len(games.TRIVIA_FALLBACK) >= 20
    for w in games.WORDS_FALLBACK["mime"] + games.WORDS_FALLBACK["pictionary"]:
        assert all(w[l] for l in games.LANGS)
    for q in games.TRIVIA_FALLBACK:
        assert all(q["q"][l] for l in games.LANGS) and len(q["o"]) == 4 and all(all(o[l] for l in games.LANGS) for o in q["o"])


# ------------------------------------------------------------------ the judge
@pytest.mark.parametrize("guess,word,expected", [
    ("cat", games._w("a cat", "un chat", "قطة"), "exact"),
    ("I think it's a CAT!", games._w("a cat", "un chat", "قطة"), "exact"),
    ("chat", games._w("a cat", "un chat", "قطة"), "exact"),                 # any language counts
    ("القطة", games._w("a cat", "un chat", "قطة"), "exact"),               # Arabic article stripped
    ("bycicle", games._w("a bicycle", "un vélo", "دراجة"), "close"),        # two letters off
    ("umbrela", games._w("an umbrella", "un parapluie", "مظلة"), "close"),
    ("rocket ship", games._w("a rocket", "une fusée", "صاروخ"), "exact"),
    ("pocket", games._w("a rocket", "une fusée", "صاروخ"), "close"),
    ("dog", games._w("a cat", "un chat", "قطة"), None),
    ("a", games._w("a cat", "un chat", "قطة"), None),
    ("elephant", games._w("a bicycle", "un vélo", "دراجة"), None),
])
def test_judge(guess, word, expected):
    assert games.judge(guess, word) == expected


# ------------------------------------------------------------------ looping rounds, word swaps, watchers
def test_turns_loop_in_new_rounds_with_new_words_until_the_break_ends(client, fast):
    fast(41, turn=0.6, turns=4)
    a, b = _two_players(client, "LIVE-DEEP", "mime", 41)
    mb = []
    _drain_until(b, "game_over", mb)
    a.__exit__(None, None, None); b.__exit__(None, None, None)
    turns = [m for m in mb if m["type"] == "game_turn"]
    assert [m["pseudo"] for m in turns] == ["alice", "bob", "alice", "bob"]          # round 2 starts again
    assert any(m["type"] == "game_round" and m["round"] == 1 for m in mb)
    played = [m["word"] for m in mb if m["type"] == "game_turn_end"]
    assert len(played) == 4 and len(set(played)) == 4                             # never the same word twice


def test_a_new_round_never_opens_with_the_last_performer(client, fast, monkeypatch):
    fast(42, turn=0.6, turns=3)
    calls = [0]
    def shuffle(seq):
        calls[0] += 1
        if calls[0] == 2:          # round 2 would start with bob, who just played: he goes later instead
            seq.reverse()
    monkeypatch.setattr(_random, "shuffle", shuffle)
    a, b = _two_players(client, "LIVE-DEEP", "mime", 42)
    mb = []
    _drain_until(b, "game_over", mb)
    a.__exit__(None, None, None); b.__exit__(None, None, None)
    assert [m["pseudo"] for m in mb if m["type"] == "game_turn"] == ["alice", "bob", "alice"]


def test_the_last_turn_is_shortened_to_what_is_left_of_the_break(client, fast, monkeypatch):
    fast(43, turn=1.0)
    monkeypatch.setattr(rooms, "break_left", lambda room, now: 0.05 + 1.01 + 0.75)   # one full turn, then 0.7 s
    a, b = _two_players(client, "LIVE-DEEP", "mime", 43)
    mb = []
    _drain_until(b, "game_over", mb)
    a.__exit__(None, None, None); b.__exit__(None, None, None)
    secs = [m["seconds"] for m in mb if m["type"] == "game_turn"]
    assert len(secs) == 2 and secs[0] == 1.0 and 0.6 <= secs[1] < 1.0


def test_a_player_can_swap_their_word_a_few_times_and_words_stay_unique(client, fast):
    fast(44, turn=5)
    a, b = _two_players(client, "LIVE-DEEP", "pictionary", 44)
    first = _drain_until(a, "game_word")["word"]
    bobs = _drain_until(b, "game_word")["word"]
    seen = {first}
    for left in range(rooms.MAX_SWAPS - 1, -1, -1):
        a.send_json({"type": "word_swap"})
        w = _drain_until(a, "game_word")
        assert w["swaps_left"] == left and w["word"] not in seen and w["word"] != bobs
        seen.add(w["word"])
    a.send_json({"type": "word_swap"})
    assert _drain_until(a, "game_swap_denied")["reason"] == "limit"
    a.__exit__(None, None, None); b.__exit__(None, None, None)


def test_no_word_swap_during_your_turn_once_someone_guessed(client, fast):
    fast(45, turn=5)
    ta, tb, tc = signup(client, "alice"), signup(client, "bob"), signup(client, "carl")
    a = client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta).__enter__()
    b = client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb).__enter__()
    c = client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tc).__enter__()
    for ws in (a, b, c): ws.receive_json()
    for ws in (a, b, c): ws.send_json({"type": "event_optin", "cycle": 45, "game": "mime"})
    a.send_json({"type": "event_start", "cycle": 45})
    word = _drain_until(a, "game_your_turn")["word"]
    _drain_until(b, "game_turn")
    b.send_json({"type": "chat", "text": word})
    _drain_until(a, "game_guessed")
    a.send_json({"type": "word_swap"})
    assert _drain_until(a, "game_swap_denied")["reason"] == "guessed"
    for ws in (a, b, c): ws.__exit__(None, None, None)


def test_mime_watchers_join_the_camera_mesh_without_showing_as_camera_on(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        a.send_json({"type": "rtc_join"})                       # alice shares her camera
        b.send_json({"type": "rtc_join", "watch": True})        # bob only watches
        peers = _drain_until(b, "rtc_peers")["peers"]
        assert [p["pseudo"] for p in peers] == ["alice"]        # bob is connected to alice's video
        for _ in range(10):
            pres = _drain_until(a, "presence")["presence"]["members"]
            video = {m["pseudo"]: m["video"] for m in pres}
            if video.get("alice"):
                break
        assert video == {"alice": True, "bob": False}
