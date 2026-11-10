"""User tracking + scoring.

Two append-only tables:
  activity — what each user did and when (sign-up, sign-in, explain, quiz, room joins, games…). Pure tracking, no points.
  points   — the score ledger: one row per award, tagged with the rule that produced it. A score is SUM(points).

The rules live in RULES below, so the scoring system can grow over time: add a rule, call `award()` where it happens,
and old rows keep their meaning because each row records the rule name and the points it gave at that time.
Daily caps keep the score honest (you cannot farm points by spamming one action).

Scoring v2 (Lot 15) — productivity first. Per UTC day d:
  focused minutes   F(d) = solo-study minutes + live-room focus minutes          (1 point a minute, cap 240)
  group minutes     G(d) = minutes in a study room with friends                   (0.6 point a minute, cap 120 points)
  actions           A(d) = productive actions (tutor turn, correct quiz answer, flashcard, revision sheet, material, tree)
  effective minutes E(d) = F(d) + 0.6 x G(d) + 2 x A(d)
  working day       E(d) >= 25 (one Pomodoro of real work)  ->  +20 points, once a day
  working streak    S = consecutive working days ending today  ->  +5 x min(S, 10) points that day
  social play       break games 1 point per 20 game points (cap 30), room competitions (cap 40), room quizzes 4 a
                    correct answer (cap 40): it counts, but less than working alone or in focus.
  weekly productivity index (0-100) = 50 x min(1, focused hours / 10) + 30 x min(1, working days / 5)
                                      + 20 x quiz accuracy (0.5 when fewer than 3 answers this week)
Minutes come from a heartbeat the page sends once a minute while it is visible and in use (`/api/score/heartbeat`);
the server credits at most one minute per 55 s per learner, so leaving tabs open does not count twice.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts);
CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    rule TEXT NOT NULL,
    points INTEGER NOT NULL,
    ref TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_points_user ON points(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_points_ts ON points(ts);
CREATE TABLE IF NOT EXISTS study_time (
    user_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    context TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day, context)
);
"""

SCORING_VERSION = 2


@dataclass(frozen=True)
class Rule:
    points: int            # points per unit
    daily_cap: int | None  # max points from this rule per user per UTC day (None = no cap)
    label: str             # English label (translated on the client)
    kind: str = "productive"   # productive | rhythm | social  (weekly breakdown + what makes a working day)


RULES: dict[str, Rule] = {
    # productive work, alone or in focus
    "study_time":     Rule(1, 240, "Focused study minute"),
    "explain":        Rule(5, 50, "Explanation studied"),
    "quiz_correct":   Rule(10, 200, "Correct quiz answer"),
    "quiz_perfect":   Rule(20, 60, "Perfect quiz"),
    "resume":         Rule(5, 15, "Revision sheet"),
    "review_card":    Rule(2, 60, "Flashcard reviewed"),
    "material":       Rule(5, 20, "Course material added"),
    "pocket_tree":    Rule(15, 60, "Pocket tree grown"),
    # rhythm: working days and their streak
    "work_day":       Rule(20, 20, "Working day reached", "rhythm"),
    "work_streak":    Rule(5, 50, "Working-day streak", "rhythm"),     # 5 per streak day, up to 10 days
    "signup":         Rule(10, None, "Welcome bonus", "rhythm"),
    "daily_login":    Rule(2, 2, "Daily visit", "rhythm"),
    "streak":         Rule(2, 20, "Day streak bonus", "rhythm"),          # v1 only: kept so old rows keep their label
    # social: counts, but less
    "group_time":     Rule(1, 120, "Study minutes with friends", "social"),  # 0.6 point per minute
    "group_quiz":     Rule(4, 40, "Correct answer in a study room", "social"),
    "competition":    Rule(1, 40, "Room competition", "social"),
    "game":           Rule(1, 30, "Break game", "social"),                # 1 point per 20 game points
}

PRODUCTIVE_ACTIONS = ("explain", "quiz_correct", "quiz_perfect", "resume", "review_card", "material", "pocket_tree")
CONTEXTS = ("solo", "live", "room")      # live = a live room during its focus phase
WORK_DAY_MIN = 25                        # effective minutes that make a working day
GROUP_RATE = 0.6
ACTION_MIN = 2
BEAT_MIN_S = 55
_last_beat: dict[int, float] = {}        # one credited minute per 55 s per learner (single process)

# Level n starts at LEVELS[n-1] points.
LEVELS = [0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000, 4000, 5200, 6600, 8200, 10000]


def init(db) -> None:
    """Create the tables and add the tracking columns to `users` (safe on existing databases)."""
    with db._lock:
        db.conn.executescript(SCHEMA)
        cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(users)")}
        if "last_seen" not in cols:
            db.conn.execute("ALTER TABLE users ADD COLUMN last_seen REAL NOT NULL DEFAULT 0")
        if "login_count" not in cols:
            db.conn.execute("ALTER TABLE users ADD COLUMN login_count INTEGER NOT NULL DEFAULT 0")
        db.conn.commit()


def _day_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts, timezone.utc).date()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def track(db, user_id: int, kind: str, detail: dict | str | None = None, ts: float | None = None) -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False) if detail else ""
    db.execute("INSERT INTO activity (user_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
               (user_id, ts or time.time(), kind, text[:400]))


def award(db, user_id: int, rule: str, units: int = 1, ref: str = "", ts: float | None = None) -> int:
    """Give `units` × the rule's points, clipped to what is left of today's cap. Returns the points given."""
    r = RULES.get(rule)
    if r is None or units <= 0:
        return 0
    ts = ts or time.time()
    pts = r.points * int(units)
    if r.daily_cap is not None:
        day = _day_start(ts)
        got = db.one("SELECT COALESCE(SUM(points), 0) AS s FROM points WHERE user_id=? AND rule=? AND ts>=? AND ts<?",
                     (user_id, rule, day, day + 86400))["s"]
        pts = min(pts, max(0, r.daily_cap - got))
    if pts <= 0:
        return 0
    db.execute("INSERT INTO points (user_id, ts, rule, points, ref) VALUES (?, ?, ?, ?, ?)",
               (user_id, ts, rule, pts, ref[:120]))
    if r.kind == "productive":
        check_work_day(db, user_id, ts)
    return pts


def streak_days(db, user_id: int, now: float | None = None) -> int:
    """Consecutive UTC days (ending today or yesterday) on which the user was active."""
    now = now or time.time()
    rows = db.all("SELECT DISTINCT CAST(ts / 86400 AS INTEGER) AS d FROM activity WHERE user_id=? ORDER BY d DESC LIMIT 400",
                  (user_id,))
    days = [r["d"] for r in rows]
    today = int(now // 86400)
    if not days or today - days[0] > 1:
        return 0
    n = 1
    for a, b in zip(days, days[1:]):
        if a - b != 1:
            break
        n += 1
    return n


def seen(db, user_id: int, now: float | None = None, *, login: bool = False) -> None:
    """Mark the user as active. The first time each UTC day: track a visit and award the daily + streak bonus."""
    now = now or time.time()
    row = db.one("SELECT last_seen FROM users WHERE id=?", (user_id,))
    if row is None:
        return
    last = row["last_seen"] or 0
    if login:
        db.execute("UPDATE users SET last_seen=?, login_count=login_count+1 WHERE id=?", (now, user_id))
    elif now - last < 60:          # cheap: at most one write a minute per user on ordinary requests
        return
    else:
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (now, user_id))
    if _day_start(last) != _day_start(now) or last == 0:
        track(db, user_id, "visit", ts=now)
        award(db, user_id, "daily_login", ts=now)   # v2: the streak that pays is the WORKING-day streak


def level_of(total: int) -> dict:
    lvl = 1
    for i, floor in enumerate(LEVELS):
        if total >= floor:
            lvl = i + 1
    floor = LEVELS[lvl - 1]
    nxt = LEVELS[lvl] if lvl < len(LEVELS) else None
    return {"level": lvl, "floor": floor, "next": nxt,
            "progress": round((total - floor) / (nxt - floor), 3) if nxt else 1.0}


def total(db, user_id: int, since: float = 0) -> int:
    return db.one("SELECT COALESCE(SUM(points), 0) AS s FROM points WHERE user_id=? AND ts>=?", (user_id, since))["s"]


def leaderboard(db, since: float = 0, limit: int = 20) -> list[dict]:
    rows = db.all(
        "SELECT u.pseudo, SUM(p.points) AS score FROM points p JOIN users u ON u.id = p.user_id"
        " WHERE p.ts >= ? GROUP BY p.user_id ORDER BY score DESC, MIN(p.ts) ASC LIMIT ?", (since, limit))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def rank_of(db, user_id: int, since: float = 0) -> int | None:
    mine = total(db, user_id, since)
    if mine <= 0:
        return None
    above = db.one("SELECT COUNT(*) AS n FROM (SELECT user_id, SUM(points) AS s FROM points WHERE ts >= ?"
                   " GROUP BY user_id HAVING s > ?)", (since, mine))["n"]
    return above + 1


def week_start(now: float | None = None) -> float:
    d = datetime.fromtimestamp(now or time.time(), timezone.utc).date()
    monday = d - timedelta(days=d.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp()


def summary(db, user_id: int, now: float | None = None) -> dict:
    now = now or time.time()
    tot = total(db, user_id)
    by_rule = db.all("SELECT rule, SUM(points) AS points, COUNT(*) AS times FROM points WHERE user_id=?"
                     " GROUP BY rule ORDER BY points DESC", (user_id,))
    for r in by_rule:
        r["label"] = RULES[r["rule"]].label if r["rule"] in RULES else r["rule"]
        r["kind"] = RULES[r["rule"]].kind if r["rule"] in RULES else "productive"
    recent = db.all("SELECT ts, rule, points FROM points WHERE user_id=? ORDER BY ts DESC LIMIT 8", (user_id,))
    for r in recent:
        r["label"] = RULES[r["rule"]].label if r["rule"] in RULES else r["rule"]
    wk = week_start(now)
    return {
        "total": tot,
        "today_points": total(db, user_id, _day_start(now)),
        "week": total(db, user_id, wk),
        **level_of(tot),
        "rank": rank_of(db, user_id),
        "week_rank": rank_of(db, user_id, wk),
        "streak_days": streak_days(db, user_id, now),
        **week_report(db, user_id, now),
        "by_rule": by_rule,
        "recent": recent,
        "version": SCORING_VERSION,
    }


# ---------------------------------------------------------------- v2: study time, working days, productivity
def _day(ts: float) -> int:
    return int(ts // 86400)


def minutes_of(db, user_id: int, day: int) -> dict:
    rows = db.all("SELECT context, minutes FROM study_time WHERE user_id=? AND day=?", (user_id, day))
    out = {c: 0 for c in CONTEXTS}
    out.update({r["context"]: r["minutes"] for r in rows})
    return out


def actions_of(db, user_id: int, day: int) -> int:
    start = day * 86400
    return db.one(f"SELECT COUNT(*) AS n FROM points WHERE user_id=? AND ts>=? AND ts<? AND rule IN ({','.join('?' * len(PRODUCTIVE_ACTIONS))})",
                  (user_id, start, start + 86400, *PRODUCTIVE_ACTIONS))["n"]


def effective_minutes(db, user_id: int, day: int) -> float:
    m = minutes_of(db, user_id, day)
    return m["solo"] + m["live"] + GROUP_RATE * m["room"] + ACTION_MIN * actions_of(db, user_id, day)


def work_days(db, user_id: int, since: float = 0) -> list[int]:
    rows = db.all("SELECT DISTINCT CAST(ts / 86400 AS INTEGER) AS d FROM points WHERE user_id=? AND rule='work_day' AND ts>=? ORDER BY d DESC",
                  (user_id, since))
    return [r["d"] for r in rows]


def work_streak(db, user_id: int, now: float | None = None) -> int:
    """Consecutive working days ending today (or yesterday: today may not be done yet)."""
    days = work_days(db, user_id)
    today = _day(now or time.time())
    if not days or today - days[0] > 1:
        return 0
    n = 1
    for a, b in zip(days, days[1:]):
        if a - b != 1:
            break
        n += 1
    return n


def check_work_day(db, user_id: int, ts: float | None = None) -> bool:
    """Award the working day (and its streak) the first time today's effective minutes reach the bar."""
    ts = ts or time.time()
    day = _day(ts)
    if db.one("SELECT 1 AS x FROM points WHERE user_id=? AND rule='work_day' AND ts>=? AND ts<? LIMIT 1", (user_id, day * 86400, day * 86400 + 86400)):
        return False
    if effective_minutes(db, user_id, day) < WORK_DAY_MIN:
        return False
    award(db, user_id, "work_day", ts=ts)
    track(db, user_id, "work_day", ts=ts)
    s = work_streak(db, user_id, ts)
    if s >= 2:
        award(db, user_id, "work_streak", units=min(s, 10), ts=ts)
    return True


def heartbeat(db, user_id: int, context: str, now: float | None = None) -> dict:
    """One minute of active use in `context`. Credits at most one minute per BEAT_MIN_S per learner."""
    now = now or time.time()
    if context not in CONTEXTS:
        return {"credited": False}
    last = _last_beat.get(user_id, 0.0)
    if now - last < BEAT_MIN_S:
        return {"credited": False}
    _last_beat[user_id] = now
    day = _day(now)
    db.execute("INSERT INTO study_time (user_id, day, context, minutes) VALUES (?, ?, ?, 1)"
               " ON CONFLICT(user_id, day, context) DO UPDATE SET minutes = minutes + 1", (user_id, day, context))
    if context == "room":
        m = minutes_of(db, user_id, day)["room"]
        got = db.one("SELECT COALESCE(SUM(points), 0) AS s FROM points WHERE user_id=? AND rule='group_time' AND ts>=? AND ts<?",
                     (user_id, day * 86400, day * 86400 + 86400))["s"]
        due = int(m * GROUP_RATE) - got
        if due > 0:
            award(db, user_id, "group_time", units=due, ts=now)
        check_work_day(db, user_id, now)
    else:
        award(db, user_id, "study_time", ts=now)    # productive: also checks the working day
    return {"credited": True, "work_day": bool(work_days(db, user_id, day * 86400))}


def week_report(db, user_id: int, now: float | None = None) -> dict:
    now = now or time.time()
    today = _day(now)
    wk = week_start(now)
    days = []
    for d in range(today - 6, today + 1):
        m = minutes_of(db, user_id, d)
        days.append({"day": d, "focused": m["solo"] + m["live"], "group": m["room"],
                     "work_day": bool(db.one("SELECT 1 AS x FROM points WHERE user_id=? AND rule='work_day' AND ts>=? AND ts<? LIMIT 1",
                                              (user_id, d * 86400, d * 86400 + 86400)))})
    wk_day = _day(wk)
    rows = db.all("SELECT context, SUM(minutes) AS m FROM study_time WHERE user_id=? AND day>=? GROUP BY context", (user_id, wk_day))
    week_min = {c: 0 for c in CONTEXTS}
    week_min.update({r["context"]: r["m"] for r in rows})
    focused_h = (week_min["solo"] + week_min["live"]) / 60
    n_work = len(work_days(db, user_id, wk))
    q = db.one("SELECT COALESCE(SUM(correct), 0) AS c, COALESCE(SUM(total), 0) AS t FROM events WHERE user_id=? AND kind='quiz' AND ts>=?", (user_id, wk))
    accuracy = q["c"] / q["t"] if q["t"] >= 3 else 0.5
    index = round(50 * min(1, focused_h / 10) + 30 * min(1, n_work / 5) + 20 * accuracy)
    kinds = {"productive": 0, "rhythm": 0, "social": 0}
    for r in db.all("SELECT rule, SUM(points) AS p FROM points WHERE user_id=? AND ts>=? GROUP BY rule", (user_id, wk)):
        kinds[RULES[r["rule"]].kind if r["rule"] in RULES else "productive"] += r["p"]
    today_m = minutes_of(db, user_id, today)
    return {
        "today": {"focused": today_m["solo"] + today_m["live"], "group": today_m["room"],
                  "effective": round(effective_minutes(db, user_id, today)), "goal": WORK_DAY_MIN,
                  "work_day": days[-1]["work_day"]},
        "days": days,
        "week": {"focused": week_min["solo"] + week_min["live"], "group": week_min["room"], "work_days": n_work,
                 "accuracy": round(accuracy, 2) if q["t"] >= 3 else None, "productivity": index, "points_by_kind": kinds},
        "work_streak": work_streak(db, user_id, now),
    }


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/score", tags=["score"])


@router.get("/me")
def me(request: Request):
    from .auth import current_user
    user = current_user(request)
    return summary(request.app.state.db, user["id"])


@router.get("/leaderboard")
def board(request: Request, period: str = "week"):
    from .auth import current_user
    user = current_user(request)
    db = request.app.state.db
    since = week_start() if period == "week" else 0
    return {"period": "week" if since else "all", "top": leaderboard(db, since, 10),
            "you": {"pseudo": user["pseudo"], "score": total(db, user["id"], since), "rank": rank_of(db, user["id"], since)}}


@router.get("/rules")
def rules():
    return {"version": SCORING_VERSION, "levels": LEVELS,
            "work_day_minutes": WORK_DAY_MIN, "group_rate": GROUP_RATE, "action_minutes": ACTION_MIN,
            "rules": [{"rule": k, "points": r.points, "daily_cap": r.daily_cap, "label": r.label, "kind": r.kind}
                      for k, r in RULES.items() if k != "streak"]}


class Beat(BaseModel):
    context: str


@router.post("/heartbeat")
def beat(body: Beat, request: Request):
    """Sent by the page once a minute while it is visible and in use; see the module docstring."""
    from .auth import current_user
    user = current_user(request)
    return heartbeat(request.app.state.db, user["id"], body.context)
