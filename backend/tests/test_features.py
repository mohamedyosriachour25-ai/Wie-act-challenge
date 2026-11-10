"""Image resources and the WebRTC signalling relay for Live-room camera sharing."""
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings
from .test_app import auth, recv_until, signup

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
NOTES = ("Photosynthesis happens in the chloroplasts of a plant cell. Chlorophyll absorbs light, "
         "the light reactions split water and release oxygen, and the Calvin cycle builds glucose.")


@pytest.fixture
def client(tmp_path):
    transport, _ = build_fake()
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=transport)
    with TestClient(app) as c:
        yield c


def upload(client, token, data, filename, scope="me"):
    return client.post(f"/api/materials/file?scope={scope}", content=data, headers={**auth(token), "X-Filename": filename})


def test_images_are_stored_as_resources_and_served_back(client):
    token = signup(client, "ada_l")
    r = upload(client, token, PNG, "diagram.png")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["kind"] == "image" and doc["status"] == "ready" and doc["chunks_total"] == 0
    listed = client.get("/api/materials", headers=auth(token)).json()["documents"]
    assert [d["kind"] for d in listed] == ["image"] and listed[0]["title"] == "diagram"
    raw = client.get(f"/api/materials/{doc['id']}/raw?token={token}")
    assert raw.status_code == 200 and raw.headers["content-type"] == "image/png" and raw.content == PNG
    assert "max-age" in raw.headers["cache-control"]  # images may be cached, unlike the JSON API


def test_a_disguised_or_unsupported_file_is_rejected(client):
    token = signup(client, "ada_l")
    assert upload(client, token, b"not really an image", "fake.png").status_code == 400
    assert upload(client, token, PNG, "art.bmp").status_code == 400  # unsupported extension


def test_images_do_not_make_a_library_askable(client):
    token = signup(client, "ada_l")
    upload(client, token, PNG, "photo.png")
    # a library with only an image cannot be questioned: no text to retrieve from
    assert client.post("/api/materials/ask", json={"scope": "me", "question": "what is this"}, headers=auth(token)).status_code == 400
    upload(client, token, NOTES.encode(), "biology.txt")
    for _ in range(40):
        if all(d["status"] == "ready" for d in client.get("/api/materials", headers=auth(token)).json()["documents"]):
            break
    ask = client.post("/api/materials/ask", json={"scope": "me", "question": "where does photosynthesis happen"}, headers=auth(token))
    assert ask.status_code == 200 and ask.json()["grounded"] is True


def test_image_count_is_capped(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=build_fake()[0])
    app.state.settings  # images limited to MAX_IMAGES (12)
    with TestClient(app) as c:
        token = signup(c, "ada_l")
        from app import materials
        codes = [upload(c, token, PNG, f"img{i}.png").status_code for i in range(materials.MAX_IMAGES + 1)]
        assert codes.count(200) == materials.MAX_IMAGES and codes[-1] == 400


def test_room_images_need_membership_to_view(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    room = client.post("/api/rooms", json={"name": "Geometry", "capacity": 4}, headers=auth(ta)).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}") as a:
        a.receive_json()
        doc = upload(client, ta, PNG, "board.png", scope=room["code"]).json()
        assert doc["kind"] == "image"
        assert client.get(f"/api/materials/{doc['id']}/raw?token={ta}&scope={room['code']}").status_code == 200
        # bob is not in the room: he cannot pull its image
        assert client.get(f"/api/materials/{doc['id']}/raw?token={tb}&scope={room['code']}").status_code == 403


# ---------------------------------------------------------------- WebRTC relay
def live_ws(client, token):
    return client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token}")


def test_camera_signalling_is_relayed_between_two_people(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with live_ws(client, ta) as a, live_ws(client, tb) as b:
        snap_a, snap_b = a.receive_json(), b.receive_json()
        assert "you_id" in snap_a  # the browser needs its own id for the connection roles
        a.send_json({"type": "rtc_join"})
        assert recv_until(a, "rtc_peers")["peers"] == []  # first camera on: nobody to connect to yet
        b.send_json({"type": "rtc_join"})
        assert recv_until(b, "rtc_peers")["peers"][0]["pseudo"] == "alice"  # b sees a already sharing
        assert recv_until(a, "rtc_peer_join")["pseudo"] == "bob"           # a is told b joined
        # an SDP offer from a is relayed only to b, tagged with a's id
        a.send_json({"type": "rtc_signal", "to": snap_b["you_id"], "data": {"desc": {"type": "offer", "sdp": "x"}}})
        relayed = recv_until(b, "rtc_signal")
        assert relayed["from"] == snap_a["you_id"] and relayed["data"]["desc"]["type"] == "offer"
        # when a turns the camera off, b is told to drop the tile
        a.send_json({"type": "rtc_leave"})
        assert recv_until(b, "rtc_peer_leave")["id"] == snap_a["you_id"]


def test_leaving_the_room_drops_the_camera_tile(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with live_ws(client, tb) as b:
        b.receive_json()
        with live_ws(client, ta) as a:
            a.receive_json()
            b.send_json({"type": "rtc_join"}); recv_until(b, "rtc_peers")
            a.send_json({"type": "rtc_join"}); recv_until(a, "rtc_peers")
            recv_until(b, "rtc_peer_join")
        assert recv_until(b, "rtc_peer_leave")["id"] is not None  # a disconnected


def test_camera_wall_capacity_is_enforced(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), video_capacity=2, **fake_ai_settings()), ai_transport=build_fake()[0])
    with TestClient(app) as client:
        toks = [signup(client, f"user{i}") for i in range(3)]
        import contextlib
        with contextlib.ExitStack() as stack:
            sockets = [stack.enter_context(client.websocket_connect(f"/ws/rooms/LIVE-LONG?token={t}")) for t in toks]
            for s in sockets:
                s.receive_json()
            for s in sockets[:2]:
                s.send_json({"type": "rtc_join"}); recv_until(s, "rtc_peers")
            sockets[2].send_json({"type": "rtc_join"})
            assert recv_until(sockets[2], "rtc_full")["capacity"] == 2  # only two cameras fit


# ---------------------------------------------------------------- PDF/text kept as openable resources
def _sample_pdf(text: str) -> bytes:
    objs = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        5: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    }
    stream = b"BT /F1 18 Tf 40 720 Td (" + text.encode() + b") Tj ET"
    objs[4] = b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream)
    buf = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for i in range(1, 6):
        offsets[i] = len(buf)
        buf += b"%d 0 obj" % i + objs[i] + b"endobj\n"
    xref = len(buf)
    buf += b"xref\n0 6\n0000000000 65535 f \n"
    for i in range(1, 6):
        buf += b"%010d 00000 n \n" % offsets[i]
    buf += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref
    return bytes(buf)


def test_pdf_is_both_searchable_and_openable(client):
    token = signup(client, "ada_l")
    pdf = _sample_pdf("Photosynthesis happens in the chloroplasts of a plant cell. Chlorophyll absorbs light and makes glucose.")
    doc = upload(client, token, pdf, "biology.pdf").json()
    assert doc["kind"] == "text" and doc["has_file"] is True and doc["mime"] == "application/pdf"
    # the original file can be reopened, inline, with the document's name
    raw = client.get(f"/api/materials/{doc['id']}/raw?token={token}")
    assert raw.status_code == 200 and raw.headers["content-type"] == "application/pdf"
    assert raw.content == pdf and 'filename="biology"' in raw.headers["content-disposition"]
    # and it is still indexed, so the library is askable from it
    for _ in range(60):
        if client.get("/api/materials", headers=auth(token)).json()["documents"][0]["status"] == "ready":
            break
    ask = client.post("/api/materials/ask", json={"scope": "me", "question": "where does photosynthesis happen"}, headers=auth(token))
    assert ask.status_code == 200 and ask.json()["grounded"] is True


def test_a_pdf_resource_works_inside_a_study_room(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    room = client.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=auth(ta)).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}") as a:
        a.receive_json()
        pdf = _sample_pdf("Mitosis divides one cell into two identical daughter cells through prophase, metaphase, anaphase and telophase.")
        doc = upload(client, ta, pdf, "mitosis.pdf", scope=room["code"]).json()
        assert doc["has_file"] is True
        # a member can open the room's PDF; a non-member cannot
        assert client.get(f"/api/materials/{doc['id']}/raw?token={ta}&scope={room['code']}").status_code == 200
        assert client.get(f"/api/materials/{doc['id']}/raw?token={tb}&scope={room['code']}").status_code == 403


# ---------------------------------------------------------------- saved study-room conversation + summary
def test_study_room_is_saved_as_a_conversation_with_a_summary(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    room = client.post("/api/rooms", json={"name": "Geometry", "capacity": 4}, headers=auth(ta)).json()
    code = room["code"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a, client.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b:
        a.receive_json(); b.receive_json()
        a.send_json({"type": "topic", "topic": "Thales theorem"}); recv_until(b, "topic")
        a.send_json({"type": "chat", "text": "Why are the triangles similar?"}); recv_until(b, "chat")
        b.send_json({"type": "chat", "text": "Because DE is parallel to BC."}); recv_until(a, "chat")
        a.send_json({"type": "explain"}); recv_until(b, "explanation")
        # generate the session summary
        a.send_json({"type": "summarize"})
        summary = recv_until(b, "summary")["summary"]
        assert summary["text"].strip() and summary["at"] > 0

    # a member rejoining sees the saved conversation and the summary in the snapshot
    with client.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b2:
        snap = b2.receive_json()
        kinds = [h["kind"] for h in snap["history"]]
        assert "topic" in kinds and kinds.count("chat") == 2 and "explanation" in kinds
        texts = [h["text"] for h in snap["history"]]
        assert any("parallel to BC" in t for t in texts)
        assert snap["summary"]["text"].strip()

    # and the REST transcript matches
    conv = client.get(f"/api/rooms/{code}/conversation", headers=auth(ta)).json()
    assert conv["topic"] == "Thales theorem" and len(conv["history"]) >= 4 and conv["summary"]["text"].strip()


def test_conversation_persists_across_a_restart(tmp_path):
    from app.config import load_settings
    from app.main import create_app

    db = str(tmp_path / "t.db")
    app = create_app(load_settings(db_path=db, **fake_ai_settings()), ai_transport=build_fake()[0])
    with TestClient(app) as c:
        ta = signup(c, "alice")
        room = c.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=auth(ta)).json()
        with c.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}") as a:
            a.receive_json()
            a.send_json({"type": "chat", "text": "Mitochondria make ATP."}); recv_until(a, "chat")
    # fresh app on the same database: the conversation is still there
    app2 = create_app(load_settings(db_path=db, **fake_ai_settings()), ai_transport=build_fake()[0])
    with TestClient(app2) as c2:
        ta2 = signup(c2, "carol")
        conv = c2.get(f"/api/rooms/{room['code']}/conversation", headers=auth(ta2)).json()
        assert any("ATP" in h["text"] for h in conv["history"])


def test_live_rooms_are_not_saved_as_conversations(client):
    token = signup(client, "ada_l")
    assert client.get("/api/rooms/LIVE-DEEP/conversation", headers=auth(token)).status_code == 400
    with client.websocket_connect(f"/ws/rooms/LIVE-DEEP?token={token}") as ws:
        snap = ws.receive_json()
        assert "history" not in snap  # live rooms stay ephemeral


# ---------------------------------------------------------------- public-room group game
def _live_cycle_now(client, code="LIVE-DEEP"):
    # mirror the server's cycle math so the test opts into the right cycle
    import time as _t
    info = client.get(f"/api/rooms/{code}").json()
    total = info["focus_min"] * 60 + info["break_min"] * 60
    return int(_t.time() // total)


def test_group_game_optin_and_shared_leaderboard(client):
    ta, tb, tc = signup(client, "alice"), signup(client, "bob"), signup(client, "carol")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tc) as c:
        snap = a.receive_json(); b.receive_json(); c.receive_json()
        assert snap["event"]["games"][0] == "trivia_race" and snap["event"]["min_players"] == 2 and snap["event"]["lead_s"] > 0
        cyc = _live_cycle_now(client)

        a.send_json({"type": "event_optin", "cycle": cyc})
        assert recv_until(b, "event_participants")["participants"] == ["alice"]
        b.send_json({"type": "event_optin", "cycle": cyc})
        assert set(recv_until(b, "event_participants")["participants"]) == {"alice", "bob"}  # b already drained alice's

        # alice answers correctly and fast, bob correctly but slow
        a.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 1000})
        recv_until(b, "event_scores")
        b.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 20000})
        board = []
        for _ in range(6):  # drain to the latest leaderboard (both players scored); scores go to the game's players
            board = recv_until(a, "event_scores")["leaderboard"]
            if len(board) == 2 and all(r["score"] > 0 for r in board):
                break
        assert [r["pseudo"] for r in board] == ["alice", "bob"]  # faster correct answer ranks first
        assert board[0]["score"] > board[1]["score"] and all(r["correct"] == 1 for r in board)


def test_a_player_can_opt_out_of_the_break_games(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        cyc = _live_cycle_now(client)
        a.send_json({"type": "event_optin", "cycle": cyc, "game": "mime"}); recv_until(b, "event_participants")
        b.send_json({"type": "event_optin", "cycle": cyc, "game": "mime"})
        assert set(recv_until(b, "event_participants")["participants"]) == {"alice", "bob"}  # b already drained alice's
        a.send_json({"type": "event_optout", "cycle": cyc})       # alice would rather just rest
        parts = recv_until(b, "event_participants")
        assert parts["game"] is None and parts["participants"] == [] and parts["counts"].get("mime") == 1
        # alice reporting a game result now is ignored: she is in no game
        a.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 500})
        b.send_json({"type": "event_optin", "cycle": cyc, "game": "trivia_race"})  # drains cleanly, no error from alice


def test_only_opted_in_players_score(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        cyc = _live_cycle_now(client)
        a.send_json({"type": "event_optin", "cycle": cyc}); recv_until(b, "event_participants")
        # bob never opted in: his result is ignored, no score broadcast changes him in
        b.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 500})
        a.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 500})
        board = recv_until(a, "event_scores")["leaderboard"]  # scores go to the game's players (alice)
        assert [r["pseudo"] for r in board] == ["alice"]


def test_double_answer_in_a_round_counts_once(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        cyc = _live_cycle_now(client)
        a.send_json({"type": "event_optin", "cycle": cyc}); recv_until(b, "event_participants")
        a.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 1000})
        first = recv_until(a, "event_scores")["leaderboard"][0]["score"]
        a.send_json({"type": "game_result", "cycle": cyc, "round": 0, "correct": True, "ms": 1000})
        a.send_json({"type": "game_result", "cycle": cyc, "round": 1, "correct": True, "ms": 1000})
        board = recv_until(a, "event_scores")["leaderboard"]
        assert board[0]["score"] == first * 2  # round 0 ignored the second time; round 1 added once


# ---------------------------------------------------------------- turn-based party games (karaoke / mime)
def _drain_until(ws, kind, store, limit=60):
    for _ in range(limit):
        m = ws.receive_json()
        store.append(m)
        if m["type"] == kind:
            return m
    raise AssertionError(f"{kind} not received")


def test_players_can_pick_different_games_and_play_in_parallel(client, monkeypatch):
    from app import rooms
    monkeypatch.setattr(rooms, "TURN_S", 0.1)
    monkeypatch.setattr(rooms, "FIRST_PREP_S", 0.05)
    monkeypatch.setattr(rooms, "TURN_GAP_S", 0.01)
    monkeypatch.setattr(rooms, "live_cycle", lambda room, now: {"phase": "break", "cycles": 11, "into": 1.0})
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        a.send_json({"type": "event_optin", "cycle": 11, "game": "mime"})
        parts = _drain_until(b, "event_participants", [])              # per-game counts are public
        assert parts["counts"].get("mime") == 1
        b.send_json({"type": "event_optin", "cycle": 11, "game": "trivia_race"}); _drain_until(a, "event_participants", [])
        a.send_json({"type": "event_start", "cycle": 11})              # starts mime (alice) only; trivia is clock-driven
        store_b = []
        b.send_json({"type": "game_result", "cycle": 11, "round": 0, "correct": True, "ms": 500})
        sc = _drain_until(b, "event_scores", store_b)
        assert sc["game"] == "trivia_race" and sc["leaderboard"][0]["pseudo"] == "bob"
        assert not any(m["type"] in ("game_start", "game_your_turn", "game_turn", "game_word") for m in store_b)  # bob never sees mime
        aturn = _drain_until(a, "game_your_turn", [])
        assert aturn["game"] == "mime"


def test_trivia_game_is_not_turn_driven(client, monkeypatch):
    from app import rooms
    monkeypatch.setattr(rooms, "live_cycle", lambda room, now: {"phase": "break", "cycles": 9, "into": 1.0})
    ta = signup(client, "alice")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a:
        a.receive_json()
        a.send_json({"type": "event_optin", "cycle": 9, "game": "trivia_race"})
        _drain_until(a, "event_participants", [])
        a.send_json({"type": "event_start", "cycle": 9})  # ignored for trivia
        a.send_json({"type": "game_result", "cycle": 9, "round": 0, "correct": True, "ms": 1000})
        board = _drain_until(a, "event_scores", [])["leaderboard"]
        assert board[0]["pseudo"] == "alice" and board[0]["score"] > 0  # trivia still scores normally


# ---------------------------------------------------------------- revision sheet (student-triggered)
def test_resume_builds_a_sheet_from_studied_topics(client):
    h = auth(signup(client, "lena"))
    client.post("/api/study/explain", json={"topic": "Thales theorem"}, headers=h)
    client.post("/api/study/explain", json={"topic": "Photosynthesis"}, headers=h)
    r = client.post("/api/study/resume", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Thales theorem" in body["topics"] and "Photosynthesis" in body["topics"]
    assert "## " in body["sheet"] and "Thales" in body["sheet"]


def test_resume_needs_some_study_first(client):
    h = auth(signup(client, "newbie"))
    assert client.post("/api/study/resume", headers=h).status_code == 400


# ---------------------------------------------------------------- live chat and private messages
def test_live_room_has_a_text_chat(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-DEEP?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        a.send_json({"type": "chat", "text": "hello everyone"})
        msg = recv_until(b, "chat")["msg"]
        assert msg["author"] == "alice" and msg["text"] == "hello everyone"


def test_private_messages_reach_both_parties(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    with client.websocket_connect("/ws/rooms/LIVE-LONG?token=" + ta) as a, \
         client.websocket_connect("/ws/rooms/LIVE-LONG?token=" + tb) as b:
        a.receive_json(); b.receive_json()
        a.send_json({"type": "dm", "to": "bob", "text": "psst"})
        got = recv_until(b, "dm")                            # the recipient gets it
        assert got["text"] == "psst" and got["from"] == "alice" and got["to"] == "bob"
        assert recv_until(a, "dm")["from"] == "alice"        # the sender keeps a copy
