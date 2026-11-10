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


def test_daily_visit_is_counted_once_a_day_and_pays_little(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 3600
    for day in range(3):
        for k in range(4):
            scoring.seen(db, uid, t0 + day * DAY + k * 120)
    rules = {r["rule"]: r["s"] for r in db.all("SELECT rule, SUM(points) AS s FROM points GROUP BY rule")}
    assert rules == {"daily_login": 3 * 2}           # v2: showing up is not working; no streak bonus for visits
    assert scoring.streak_days(db, uid, t0 + 2 * DAY) == 3      # still tracked (progress panel), just not paid


@pytest.fixture(autouse=True)
def _fresh_beats():
    scoring._last_beat.clear()
    yield
    scoring._last_beat.clear()


def _minutes(db, uid, context, n, start):
    for k in range(n):
        scoring.heartbeat(db, uid, context, start + k * 60)


def test_focused_minutes_make_a_working_day_and_working_days_make_a_streak(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 8 * 3600
    assert scoring.heartbeat(db, uid, "solo", t0)["credited"] is True
    assert scoring.heartbeat(db, uid, "solo", t0 + 20)["credited"] is False           # one minute per 55 s, no double counting
    _minutes(db, uid, "solo", 23, t0 + 60)                                               # 24 focused minutes: not yet
    assert not scoring.work_days(db, uid)
    _minutes(db, uid, "live", 1, t0 + 25 * 60)                                           # the 25th minute (live focus counts too)
    assert len(scoring.work_days(db, uid)) == 1
    pts = {r["rule"]: r["s"] for r in db.all("SELECT rule, SUM(points) AS s FROM points GROUP BY rule")}
    assert pts == {"study_time": 25, "work_day": 20}                                     # no streak on day one
    _minutes(db, uid, "solo", 25, t0 + DAY)                                              # day 2
    _minutes(db, uid, "solo", 25, t0 + 2 * DAY)                                          # day 3
    streak = db.all("SELECT points FROM points WHERE rule='work_streak' ORDER BY ts")
    assert [r["points"] for r in streak] == [10, 15]                                     # 5 x streak length
    assert scoring.work_streak(db, uid, t0 + 2 * DAY) == 3
    assert scoring.work_streak(db, uid, t0 + 4 * DAY) == 0                               # a missed day breaks it


def test_studying_with_friends_counts_but_less(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 8 * 3600
    _minutes(db, uid, "room", 10, t0)
    assert db.one("SELECT SUM(points) AS s FROM points WHERE rule='group_time'")["s"] == 6   # 0.6 point a minute
    _minutes(db, uid, "room", 32, t0 + 11 * 60)                                         # 42 room minutes = 25.2 effective
    assert len(scoring.work_days(db, uid)) == 1
    scoring.heartbeat(db, uid, "break", t0 + DAY)                                        # unknown context: nothing
    assert db.one("SELECT COUNT(*) AS n FROM study_time WHERE context='break'")["n"] == 0


def test_productive_actions_also_count_toward_the_working_day(db):
    uid = _user(db)
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 8 * 3600
    for k in range(10):
        scoring.award(db, uid, "explain", ts=t0 + k)                                     # 10 actions = 20 effective minutes
    assert not scoring.work_days(db, uid)
    _minutes(db, uid, "solo", 5, t0 + 60)
    assert len(scoring.work_days(db, uid)) == 1
    scoring.award(db, uid, "game", units=30, ts=t0 + DAY)                                # social play never makes a working day
    assert len(scoring.work_days(db, uid)) == 1


def test_week_report_and_productivity_index(db):
    uid = _user(db)
    now = time.time()
    _minutes(db, uid, "solo", 30, now - 40 * 60)
    _minutes(db, uid, "room", 10, now - 9 * 60)
    scoring.award(db, uid, "game", units=5, ts=now)
    rep = scoring.week_report(db, uid, now)
    assert rep["today"]["focused"] == 30 and rep["today"]["group"] == 10 and rep["today"]["work_day"] is True
    assert len(rep["days"]) == 7 and rep["days"][-1]["work_day"] is True
    # 0.5 h of 10 h -> 2.5 ; 1 of 5 working days -> 6 ; no quizzes -> neutral 0.5 x 20 = 10
    assert rep["week"]["productivity"] == round(50 * 0.05 + 30 * 0.2 + 10)
    kinds = rep["week"]["points_by_kind"]
    assert kinds["productive"] == 30 and kinds["rhythm"] == 20 and kinds["social"] == 6 + 5


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
    assert me["total"] == 10 + 2 and me["level"] == 1 and me["rank"] == 1          # welcome + first visit
    assert me["today"]["goal"] == 25 and len(me["days"]) == 7 and me["work_streak"] == 0
    client.post("/api/auth/login", json={"pseudo": "ada_l", "password": "secret1"})
    db = client.app.state.db
    kinds = [r["kind"] for r in db.all("SELECT kind FROM activity ORDER BY id")]
    assert kinds[:3] == ["signup", "visit", "login"]
    assert db.one("SELECT login_count FROM users WHERE pseudo='ada_l'")["login_count"] == 2
    r = client.post("/api/study/explain", json={"topic": "Pythagorean theorem"}, headers=auth(token))
    assert r.status_code == 200, r.text
    me = client.get("/api/score/me", headers=auth(token)).json()
    assert me["total"] == 17 and any(x["rule"] == "explain" and x["kind"] == "productive" for x in me["by_rule"])
    assert client.get("/api/progress", headers=auth(token)).json()["xp"] == 17
    beat = client.post("/api/score/heartbeat", json={"context": "solo"}, headers=auth(token)).json()
    assert beat["credited"] is True
    assert client.post("/api/score/heartbeat", json={"context": "solo"}, headers=auth(token)).json()["credited"] is False
    assert client.get("/api/score/me", headers=auth(token)).json()["today"]["focused"] == 1


def test_leaderboard_endpoint_ranks_players_and_shows_you(client):  # noqa: F811
    ta, tb = signup(client, "alice"), signup(client, "bob")
    client.post("/api/study/explain", json={"topic": "Ohm's law"}, headers=auth(tb))
    board = client.get("/api/score/leaderboard?period=all", headers=auth(ta)).json()
    assert [r["pseudo"] for r in board["top"]] == ["bob", "alice"]
    assert board["you"] == {"pseudo": "alice", "score": 12, "rank": 2}
    assert client.get("/api/score/leaderboard", headers=auth(ta)).json()["period"] == "week"
    assert client.get("/api/score/me").status_code == 401
    rules = client.get("/api/score/rules").json()
    assert rules["version"] == 2 and rules["work_day_minutes"] == 25
    kinds = {r["rule"]: r["kind"] for r in rules["rules"]}
    assert kinds["game"] == kinds["competition"] == kinds["group_time"] == "social" and kinds["study_time"] == "productive"


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
