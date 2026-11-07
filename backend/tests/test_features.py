"""Image resources and the WebRTC signalling relay for Live-room camera sharing."""
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

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
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
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
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
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
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0, video_capacity=2))
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
