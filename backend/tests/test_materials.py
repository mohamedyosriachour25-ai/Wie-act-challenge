"""Course material: chunking, upload guard, indexing, grounded answers, room scope."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings
from app.materials import chunk_text, cosine, pack, personal_data_reason, unpack

PHOTO = (
    "Photosynthesis takes place in the chloroplasts of plant cells. Chlorophyll absorbs light energy, mostly red and blue. "
    "The light reactions split water and release oxygen. The Calvin cycle then uses the stored energy to build glucose from carbon dioxide.\n\n"
    "Cellular respiration is the opposite exchange. Mitochondria break glucose down with oxygen to release usable energy as ATP. "
    "Respiration happens day and night, while photosynthesis needs light."
)
HISTORY = (
    "The French Revolution began in 1789 with the meeting of the Estates General. Financial crisis and unequal taxation created anger. "
    "The storming of the Bastille on 14 July became a symbol of popular power. The monarchy was abolished in 1792."
)


@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=build_fake()[0])
    with TestClient(app) as c:
        yield c


def signup(client, pseudo):
    return client.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def upload(client, token, text, name="bio.txt", scope="me"):
    return client.post(f"/api/materials/file?scope={scope}", content=text.encode(), headers={**auth(token), "X-Filename": name})


def wait_ready(client, token, scope="me", timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        docs = client.get(f"/api/materials?scope={scope}", headers=auth(token)).json()["documents"]
        if docs and all(d["status"] == "ready" for d in docs):
            return docs
        time.sleep(0.05)
    raise AssertionError(f"not ready: {docs}")


def stats(client, token):
    return client.get("/api/ai/metrics").json()


def test_chunker_respects_size_and_keeps_all_text():
    long = " ".join(f"Sentence number {i} explains one small idea about the topic." for i in range(120))
    chunks = chunk_text(long)
    assert len(chunks) > 3 and all(len(c) <= 1100 for c in chunks)
    joined = " ".join(chunks)
    assert "Sentence number 0 " in joined and "Sentence number 119 " in joined
    assert chunk_text("tiny") == []


def test_vector_roundtrip_and_cosine():
    v = [0.5, -1.25, 3.0]
    assert list(unpack(pack(v))) == v
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == 0.0 and cosine([0, 0], [1, 1]) == 0.0


@pytest.mark.parametrize("text", ["Contact me at ada@example.com for the notes.", "Call +33 6 12 34 56 78 tonight.", "IDs 123456789 and 987654321 and 111222333444."])
def test_personal_data_is_detected(text):
    assert personal_data_reason(text)


def test_plain_course_text_passes_the_guard():
    assert personal_data_reason(PHOTO) is None and personal_data_reason(HISTORY) is None


def test_upload_rules(client):
    t = signup(client, "ada_l")
    # personal details no longer refuse the document: they are removed before anything is indexed or sent
    r = upload(client, t, "Write to me at a@b.com " + PHOTO)
    assert r.status_code == 200 and r.json()["note"] == {"removed": 1}
    assert upload(client, t, "too short").status_code == 400
    assert upload(client, t, PHOTO, name="slides.pptx").status_code == 400
    assert client.post("/api/materials/file", content=PHOTO.encode()).status_code == 401
    assert upload(client, t, "x" * 3_100_000).status_code == 413


def test_upload_is_indexed_in_the_background_then_answers_with_sources(client):
    t = signup(client, "ada_l")
    r = upload(client, t, PHOTO)
    assert r.status_code == 200 and r.json()["chunks_total"] >= 1
    docs = wait_ready(client, t)
    assert docs[0]["chunks_done"] == docs[0]["chunks_total"]

    out = client.post("/api/materials/ask", json={"scope": "me", "question": "Where does photosynthesis take place?"}, headers=auth(t)).json()
    assert out["grounded"] is True and out["sources"] and out["sources"][0]["title"] == "bio"
    assert "[1]" in out["answer"] and "chloroplasts" in out["answer"].lower()


def test_documents_work_without_embeddings(client):
    """If the embeddings endpoint is unavailable, a document is still added (keyword search)
    and grounded answers still work, instead of the upload being marked failed."""
    from app.ai.gateway import AIError

    async def no_embeddings(texts, batch=32):
        raise AIError("bad_request", "No embeddings model.", status=400)

    client.app.state.gateway.embed = no_embeddings
    t = signup(client, "lex_learner")
    assert upload(client, t, PHOTO).status_code == 200
    docs = wait_ready(client, t)  # ready via keyword fallback, not "failed"
    assert docs[0]["status"] == "ready"

    out = client.post("/api/materials/ask", json={"scope": "me", "question": "Where does photosynthesis take place?"}, headers=auth(t)).json()
    assert out["grounded"] is True and out["sources"] and "chloroplasts" in out["answer"].lower()


def test_unrelated_question_makes_no_generation_call(client):
    t = signup(client, "ada_l")
    upload(client, t, PHOTO)
    wait_ready(client, t)
    before = stats(client, t)["calls"]["total"]
    out = client.post("/api/materials/ask", json={"scope": "me", "question": "quantum chromodynamics lattice gauge"}, headers=auth(t)).json()
    assert out["grounded"] is False and out["sources"] == []
    assert stats(client, t)["calls"]["total"] == before


def test_asking_without_material_is_a_clear_error(client):
    t = signup(client, "ada_l")
    r = client.post("/api/materials/ask", json={"scope": "me", "question": "anything at all"}, headers=auth(t))
    assert r.status_code == 400 and "Add a document" in r.json()["detail"]


def test_libraries_are_private_and_can_be_deleted(client):
    ta, tb = signup(client, "alice"), signup(client, "bob")
    upload(client, ta, PHOTO)
    doc = wait_ready(client, ta)[0]
    assert client.get("/api/materials", headers=auth(tb)).json()["documents"] == []
    assert client.delete(f"/api/materials/{doc['id']}", headers=auth(tb)).status_code == 404
    assert client.delete(f"/api/materials/{doc['id']}", headers=auth(ta)).status_code == 200
    assert client.get("/api/materials", headers=auth(ta)).json()["documents"] == []


def test_document_limit(client):
    t = signup(client, "ada_l")
    for i in range(6):
        assert upload(client, t, PHOTO + f" Extra note {i}.", name=f"d{i}.txt").status_code == 200
    assert upload(client, t, HISTORY, name="d7.txt").status_code == 400


def test_room_material_needs_membership_and_is_shared(client):
    ta, tb, tc = signup(client, "alice"), signup(client, "bob"), signup(client, "carol")
    room = client.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=auth(ta)).json()
    code = room["code"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a, client.websocket_connect(f"/ws/rooms/{code}?token={tb}") as b:
        a.receive_json(), b.receive_json()
        assert upload(client, tc, PHOTO, scope=code).status_code == 403  # not a member
        assert upload(client, ta, PHOTO, scope=code).status_code == 200
        wait_ready(client, tb, scope=code)  # bob sees alice's document

        # Everyone in the room gets the same answer from one call.
        a.send_json({"type": "ask", "question": "What do mitochondria do?"})
        seen = []
        for ws in (a, b):
            for _ in range(15):
                m = ws.receive_json()
                if m["type"] == "answer":
                    seen.append(m["data"])
                    break
        assert len(seen) == 2 and seen[0] == seen[1]
        assert seen[0]["grounded"] and seen[0]["asker"] == "alice" and "ATP" in seen[0]["text"]
        assert seen[0]["sources"][0]["kind"] == "material"


def test_room_explanation_is_grounded_in_its_material(client):
    ta = signup(client, "alice")
    room = client.post("/api/rooms", json={"name": "Biology", "capacity": 4}, headers=auth(ta)).json()
    code = room["code"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={ta}") as a:
        a.receive_json()
        upload(client, ta, PHOTO, scope=code)
        wait_ready(client, ta, scope=code)
        a.send_json({"type": "topic", "topic": "photosynthesis"})
        a.send_json({"type": "explain"})
        staged = []
        for _ in range(60):
            m = a.receive_json()
            if m["type"] == "tutor":
                ev = m["event"]
                staged.append("stage:" + ev["stage"] if ev["type"] == "stage" else ev["type"])
            if m["type"] == "explanation":
                assert [s["title"] for s in m["data"]["sources"] if s["kind"] == "material"] == ["bio"]
                assert "chloroplasts" in m["data"]["text"] and m["data"]["visual"]
                # the room sees partial results before the final explanation, text before figure
                assert staged.index("sources") < staged.index("draft") < staged.index("final") < staged.index("visual")
                assert "intent" in staged and "stage:understanding" not in staged   # the topic is explicit: no intent call
                return
        raise AssertionError("no explanation")


def test_personal_explanation_uses_context_when_given(client):
    t = signup(client, "ada_l")
    plain = client.post("/api/study/explain", json={"topic": "photosynthesis"}, headers=auth(t)).json()
    assert not plain.get("grounded_in")


def test_personal_explanation_is_grounded_once_material_is_ready(client):
    t = signup(client, "ada_l")
    upload(client, t, PHOTO)
    wait_ready(client, t)
    out = client.post("/api/study/explain", json={"topic": "photosynthesis"}, headers=auth(t)).json()
    assert out["grounded_in"] == ["bio"] and out["visual"]["template"]
