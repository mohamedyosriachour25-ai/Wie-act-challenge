"""User tracking + scoring (app/scoring.py) and the test-database script (scripts/test_db.py)."""
import csv
import importlib.util
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import scoring
from app.config import load_settings
from app.db import DB
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings
from .test_app import auth, client, signup  # noqa: F401

DAY = 86400.0


@pytest.fixture
def db(tmp_path):
    d = DB(str(tmp_path / "s.db"))
    yield d
    d.close()


def _user(db, pseudo="ada"):
    return db.execute("INSERT INTO users (pseudo, pw_hash, salt, created_at) VALUES (?, 'x', 'x', ?)", (pseudo, time.time())).lastrowid


def test_tracking_columns_are_added_to_an_old_users_table(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, pseudo TEXT NOT NULL UNIQUE COLLATE NOCASE,"
                " pw_hash TEXT NOT NULL, salt TEXT NOT NULL, learning_style TEXT, created_at REAL NOT NULL)")
    con.commit(); con.close()
    d = DB(path)
    cols = {r["name"] for r in d.all("PRAGMA table_info(users)")}
    assert {"last_seen", "login_count"} <= cols
    assert d.one("SELECT COUNT(*) AS n FROM points")["n"] == 0


def test_award_respects_the_daily_cap_and_resets_the_next_day(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 3600        # 01:00 UTC
    given = [scoring.award(db, uid, "explain", ts=t0 + i) for i in range(15)]
    assert sum(given) == scoring.RULES["explain"].daily_cap == 50
    assert scoring.award(db, uid, "explain", ts=t0 + DAY) == 5
    assert scoring.award(db, uid, "nope") == 0 and scoring.award(db, uid, "explain", units=0) == 0


def test_daily_visit_is_counted_once_a_day_and_streaks_add_a_bonus(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 3600
    for day in range(3):
        for k in range(4):
            scoring.seen(db, uid, t0 + day * DAY + k * 120)
    rules = {r["rule"]: r["s"] for r in db.all("SELECT rule, SUM(points) AS s FROM points GROUP BY rule")}
    assert rules["daily_login"] == 3 * 5
    assert rules["streak"] == 2 * 2 + 3 * 2          # day 2: 2-day streak, day 3: 3-day streak
    assert scoring.streak_days(db, uid, t0 + 2 * DAY) == 3
    assert scoring.streak_days(db, uid, t0 + 5 * DAY) == 0       # broken


def test_levels_and_ranks(db):
    a, b, c = _user(db, "a"), _user(db, "b"), _user(db, "c")
    scoring.award(db, a, "signup"); scoring.award(db, b, "quiz_correct", units=20); scoring.award(db, c, "explain")
    assert [r["pseudo"] for r in scoring.leaderboard(db)] == ["b", "a", "c"]
    assert scoring.rank_of(db, b) == 1 and scoring.rank_of(db, c) == 3
    assert scoring.level_of(0)["level"] == 1 and scoring.level_of(160) == {"level": 3, "floor": 150, "next": 300, "progress": 0.067}
    assert scoring.level_of(10**6)["next"] is None


def test_the_app_tracks_signup_login_and_scores_study(client):  # noqa: F811
    token = signup(client, "ada_l")
    me = client.get("/api/score/me", headers=auth(token)).json()
    assert me["total"] == 10 + 5 and me["level"] == 1 and me["rank"] == 1          # welcome + first visit
    client.post("/api/auth/login", json={"pseudo": "ada_l", "password": "secret1"})
    db = client.app.state.db
    kinds = [r["kind"] for r in db.all("SELECT kind FROM activity ORDER BY id")]
    assert kinds[:3] == ["signup", "visit", "login"]
    assert db.one("SELECT login_count FROM users WHERE pseudo='ada_l'")["login_count"] == 2
    r = client.post("/api/study/explain", json={"topic": "Pythagorean theorem"}, headers=auth(token))
    assert r.status_code == 200, r.text
    me = client.get("/api/score/me", headers=auth(token)).json()
    assert me["total"] == 20 and any(x["rule"] == "explain" for x in me["by_rule"])
    assert client.get("/api/progress", headers=auth(token)).json()["xp"] == 20


def test_leaderboard_endpoint_ranks_players_and_shows_you(client):  # noqa: F811
    ta, tb = signup(client, "alice"), signup(client, "bob")
    client.post("/api/study/explain", json={"topic": "Ohm's law"}, headers=auth(tb))
    board = client.get("/api/score/leaderboard?period=all", headers=auth(ta)).json()
    assert [r["pseudo"] for r in board["top"]] == ["bob", "alice"]
    assert board["you"] == {"pseudo": "alice", "score": 15, "rank": 2}
    assert client.get("/api/score/leaderboard", headers=auth(ta)).json()["period"] == "week"
    assert client.get("/api/score/me").status_code == 401
    assert any(r["rule"] == "game" for r in client.get("/api/score/rules").json()["rules"])


def _script():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "test_db.py")
    spec = importlib.util.spec_from_file_location("test_db_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_test_database_has_unique_users_whose_passwords_work(tmp_path):
    path, creds = str(tmp_path / "herwiego-test.db"), str(tmp_path / "users.csv")
    script = _script()
    script.main(["create", "--users", "12", "--days", "10", "--seed", "3", "--db", path, "--csv", creds])
    with open(creds, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 12 and len({r["pseudo"].lower() for r in rows}) == 12
    d = DB(path)
    assert d.one("SELECT COUNT(*) AS n FROM users")["n"] == 12
    assert d.one("SELECT COUNT(*) AS n FROM points")["n"] > 12 and d.one("SELECT COUNT(*) AS n FROM activity")["n"] > 12
    assert not d.one("SELECT 1 AS x FROM users WHERE pw_hash IN (%s)" % ",".join("?" * 12), tuple(r["password"] for r in rows))
    d.close()
    script.main(["add", "--pseudo", "manual_one", "--db", path, "--csv", creds])
    with pytest.raises(SystemExit):
        script.main(["add", "--pseudo", "MANUAL_ONE", "--db", path, "--csv", creds])      # unique, case-insensitive
    with open(creds, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    transport, _ = build_fake()
    app = create_app(load_settings(db_path=path, **fake_ai_settings()), ai_transport=transport)
    with TestClient(app) as c:
        for r in (rows[0], rows[-1]):
            assert c.post("/api/auth/login", json={"pseudo": r["pseudo"], "password": r["password"]}).status_code == 200
        assert c.post("/api/auth/login", json={"pseudo": rows[0]["pseudo"], "password": "wrong-pass"}).status_code == 401
    with pytest.raises(SystemExit):
        script.main(["create", "--db", path, "--csv", creds])                               # no accidental overwrite
