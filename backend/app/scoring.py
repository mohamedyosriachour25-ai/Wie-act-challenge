"""User tracking + scoring.

Two append-only tables:
  activity — what each user did and when (sign-up, sign-in, explain, quiz, room joins, games…). Pure tracking, no points.
  points   — the score ledger: one row per award, tagged with the rule that produced it. A score is SUM(points).

The rules live in RULES below, so the scoring system can grow over time: add a rule, call `award()` where it happens,
and old rows keep their meaning because each row records the rule name and the points it gave at that time.
Daily caps keep the score honest (you cannot farm points by spamming one action).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

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
"""

SCORING_VERSION = 1


@dataclass(frozen=True)
class Rule:
    points: int            # points per unit
    daily_cap: int | None  # max points from this rule per user per UTC day (None = no cap)
    label: str             # English label (translated on the client)


RULES: dict[str, Rule] = {
    "signup":         Rule(10, None, "Welcome bonus"),
    "daily_login":    Rule(5, 5, "Daily visit"),
    "streak":         Rule(2, 20, "Day streak bonus"),        # 2 points per streak day (from day 2), max 20 a day
    "explain":        Rule(5, 50, "Explanation studied"),
    "quiz_correct":   Rule(10, 200, "Correct quiz answer"),
    "quiz_perfect":   Rule(20, 60, "Perfect quiz"),
    "group_quiz":     Rule(10, 150, "Correct answer in a study room"),
    "resume":         Rule(5, 15, "Revision sheet"),
    "review_card":    Rule(2, 60, "Flashcard reviewed"),
    "game":           Rule(1, 150, "Break game"),             # 1 point per 10 game points
    "pocket_tree":    Rule(15, 60, "Pocket tree grown"),
}

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
        award(db, user_id, "daily_login", ts=now)
        s = streak_days(db, user_id, now)
        if s >= 2:
            award(db, user_id, "streak", units=s, ts=now)


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
    recent = db.all("SELECT ts, rule, points FROM points WHERE user_id=? ORDER BY ts DESC LIMIT 8", (user_id,))
    for r in recent:
        r["label"] = RULES[r["rule"]].label if r["rule"] in RULES else r["rule"]
    wk = week_start(now)
    return {
        "total": tot,
        "today": total(db, user_id, _day_start(now)),
        "week": total(db, user_id, wk),
        **level_of(tot),
        "rank": rank_of(db, user_id),
        "week_rank": rank_of(db, user_id, wk),
        "streak_days": streak_days(db, user_id, now),
        "by_rule": by_rule,
        "recent": recent,
        "version": SCORING_VERSION,
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
            "rules": [{"rule": k, "points": r.points, "daily_cap": r.daily_cap, "label": r.label} for k, r in RULES.items()]}
