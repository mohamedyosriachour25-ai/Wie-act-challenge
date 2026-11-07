"""Operational safeguards: limits, throttling, secrets, headers, logging, restart recovery."""
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import load_settings
from app.main import create_app

from .test_app import auth, recv_until, signup


def make_client(tmp_path, **overrides):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0, **overrides))
    return TestClient(app)


def test_one_learner_cannot_drain_the_shared_quota(tmp_path):
    with make_client(tmp_path, user_ai_per_min=3) as c:
        ta, tb = signup(c, "alice"), signup(c, "bob")
        body = lambda i: {"topic": "Thales theorem", "question": f"find DE, variant {i}", "level": 1}
        codes = [c.post("/api/study/hint", json=body(i), headers=auth(ta)).status_code for i in range(4)]
        assert codes == [200, 200, 200, 429]
        blocked = c.post("/api/study/hint", json=body(9), headers={**auth(ta), "X-Lang": "fr"})
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
        assert "Réessayez" in blocked.json()["detail"]
        assert c.post("/api/study/hint", json=body(5), headers=auth(tb)).status_code == 200  # someone else is unaffected
        assert c.get("/api/ai/metrics").json()["calls"]["compact"] == 4  # 3 + 1: the blocked calls never reached the service


def test_room_ai_actions_are_limited_per_person(tmp_path):
    with make_client(tmp_path, user_ai_per_min=1) as c:
        ta = signup(c, "alice")
        room = c.post("/api/rooms", json={"name": "Geometry", "capacity": 4}, headers=auth(ta)).json()
        with c.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}&lang=fr") as a:
            a.receive_json()
            a.send_json({"type": "topic", "topic": "Thales theorem"})
            recv_until(a, "topic")
            a.send_json({"type": "explain"})
            recv_until(a, "explanation")
            a.send_json({"type": "quiz_start"})
            err = recv_until(a, "ai_error")
            assert err["kind"] == "rate" and err["retry_after"] >= 1 and "Réessayez" in err["message"]


def test_login_is_throttled_and_success_resets_the_count(tmp_path):
    with make_client(tmp_path, login_attempts=3) as c:
        signup(c, "alice")
        bad = {"pseudo": "alice", "password": "wrong-one"}
        good = {"pseudo": "alice", "password": "secret1"}
        assert [c.post("/api/auth/login", json=bad).status_code for _ in range(2)] == [401, 401]
        assert c.post("/api/auth/login", json=good).status_code == 200  # a success clears the pseudo's failures
        assert [c.post("/api/auth/login", json=bad).status_code for _ in range(3)] == [401, 401, 401]
        locked = c.post("/api/auth/login", json=good)
        assert locked.status_code == 429 and "Retry-After" in locked.headers  # even the right password waits


def test_unknown_and_known_pseudos_fail_the_same_way(tmp_path):
    with make_client(tmp_path) as c:
        signup(c, "alice")
        a = c.post("/api/auth/login", json={"pseudo": "alice", "password": "nope-nope"})
        b = c.post("/api/auth/login", json={"pseudo": "ghost_1", "password": "nope-nope"})
        assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_account_creation_is_limited_per_connection(tmp_path):
    with make_client(tmp_path, register_per_hour=2) as c:
        signup(c, "user_a"), signup(c, "user_b")
        assert c.post("/api/auth/register", json={"pseudo": "user_c", "password": "secret1"}).status_code == 429


def test_production_refuses_weak_configuration(tmp_path):
    with pytest.raises(RuntimeError, match="EDNOVA_SECRET"):
        load_settings(env="production", db_path=str(tmp_path / "t.db"))
    with pytest.raises(RuntimeError, match="EDNOVA_API_KEY"):
        load_settings(env="production", secret="x" * 32, base_url="https://ai.example/v1", api_key="replace-me")
    ok = load_settings(env="production", secret="x" * 32, db_path=str(tmp_path / "t.db"))
    app = create_app(ok)
    with TestClient(app) as c:
        for path in ("/docs", "/openapi.json"):  # the single-page app answers unknown paths; the API docs must not
            r = c.get(path)
            assert "swagger" not in r.text.lower() and "openapi" not in r.text.lower()
        assert c.get("/api/health").json()["ok"] is True


def test_security_headers_and_request_id(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/api/health")
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Cache-Control"] == "no-store" and len(r.headers["X-Request-ID"]) == 10
        page = c.get("/")
        assert "default-src 'self'" in page.headers["Content-Security-Policy"]
        assert "camera=(self)" in page.headers["Permissions-Policy"]


def test_oversized_bodies_are_refused_even_when_chunked(tmp_path):
    with make_client(tmp_path) as c:
        token = signup(c, "alice")
        too_big = b"a" * (3 * 1024 * 1024 + 10)
        r = c.post("/api/materials/file", content=too_big, headers={**auth(token), "X-Filename": "big.txt"})
        assert r.status_code == 413
        chunks = (too_big[i : i + 65536] for i in range(0, len(too_big), 65536))  # no Content-Length: chunked
        r = c.post("/api/materials/file", content=chunks, headers={**auth(token), "X-Filename": "big.txt"})
        assert r.status_code == 413
        huge = c.post("/api/auth/login", content=b"x" * (5 * 1024 * 1024), headers={"Content-Type": "application/json"})
        assert huge.status_code == 413


def test_secrets_never_reach_the_logs():
    f = security.RedactFilter("team-key-123456", "signing-secret-abcdef")
    out = f.redact("call with team-key-123456 Authorization: Bearer abc.def.ghi-123456 url=/ws/rooms/X?token=abc.def&lang=fr s=signing-secret-abcdef")
    for leaked in ("team-key-123456", "abc.def.ghi-123456", "token=abc.def", "signing-secret-abcdef"):
        assert leaked not in out
    assert "lang=fr" in out


def test_application_log_is_redacted_end_to_end(tmp_path, caplog):
    with make_client(tmp_path, api_key="very-secret-team-key") as c:
        logging.getLogger("ednova").addHandler(caplog.handler)
        logging.getLogger("ednova").info("debug with very-secret-team-key inside")
        for h in logging.getLogger("ednova").handlers:
            if any(isinstance(x, security.RedactFilter) for x in h.filters):
                rec = logging.LogRecord("ednova", logging.INFO, "", 0, "x very-secret-team-key y", (), None)
                for flt in h.filters:
                    flt.filter(rec)
                assert "very-secret-team-key" not in rec.msg
                break
        else:
            pytest.fail("no redacting handler installed")


def test_unfinished_indexing_resumes_after_a_restart(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
    db = app.state.db
    user = db.execute("INSERT INTO users (pseudo, pw_hash, salt, created_at) VALUES ('ada_l', 'x', '00', ?)", (time.time(),)).lastrowid
    doc = db.execute("INSERT INTO docs (collection, title, owner_id, status, chunks_total, created_at) VALUES (?, 'Biology', ?, 'indexing', 3, ?)", (f"u:{user}", user, time.time())).lastrowid
    db.executemany("INSERT INTO chunks (doc_id, idx, text) VALUES (?, ?, ?)", [(doc, i, f"Photosynthesis passage number {i} about chloroplasts and light") for i in range(3)])
    with TestClient(app):  # startup finds the stuck document and finishes it
        for _ in range(100):
            row = db.one("SELECT status, chunks_done FROM docs WHERE id=?", (doc,))
            if row["status"] == "ready":
                break
            time.sleep(0.05)
        assert row == {"status": "ready", "chunks_done": 3}
        assert db.one("SELECT COUNT(*) AS n FROM chunks WHERE doc_id=? AND emb IS NOT NULL", (doc,))["n"] == 3


def test_health_reports_database_state(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/api/health").json() == {"ok": True, "mock": True}


def test_sliding_window_unit():
    w = security.SlidingWindow()
    assert [w.hit("k", 2, 10, now=t) for t in (0, 1)] == [0, 0]
    assert w.hit("k", 2, 10, now=2) == pytest.approx(8)
    assert w.hit("k", 2, 10, now=10.5) == 0  # the oldest hit left the window


def test_uvicorn_websocket_lines_are_redacted(tmp_path):
    """uvicorn logs 'WebSocket /ws/rooms/X?token=... [accepted]' to uvicorn.error: the token must be masked there."""
    make_client(tmp_path)
    rec = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]', ("127.0.0.1:1", "/ws/rooms/AB12?token=abc.def&lang=en"), None)
    for name in ("uvicorn.error", "uvicorn.access", "uvicorn"):
        flts = [f for f in logging.getLogger(name).filters if isinstance(f, security.RedactFilter)]
        assert flts, name
        r = logging.LogRecord(rec.name, rec.levelno, "", 0, rec.msg, rec.args, None)
        for f in flts:
            f.filter(r)
        assert "abc.def" not in r.msg and "lang=en" in r.msg
