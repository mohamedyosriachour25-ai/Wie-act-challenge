"""HerwieGO test database: fake users with unique pseudos and assigned passwords, plus a believable activity history
(tracking + score ledger) so the score, levels and leaderboards have something to show.

Usage (from backend/):
    python scripts/test_db.py create                    # herwiego-test.db + test-users.csv (30 users, 21 days)
    python scripts/test_db.py create --users 50 --days 30 --seed 7 --db my-test.db --csv my-users.csv
    python scripts/test_db.py add --pseudo amine_t       # one more user (password generated, appended to the CSV)
    python scripts/test_db.py report                     # who is tracked: last seen, logins, actions, score, level, rank
    python scripts/test_db.py report --user amine_t      # one user's activity and points in detail

Then run the app on it:  HERWIEGO_DB=herwiego-test.db uvicorn app.main:app --reload
Passwords are stored only as scrypt hashes in the database (same as real sign-ups); the plain passwords are written
to the CSV so testers can sign in. Both files are git-ignored. Never use this on the production database.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import secrets
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import scoring  # noqa: E402
from app.auth import PSEUDO_RE, _hash  # noqa: E402
from app.db import DB  # noqa: E402

ADJ = ["curious", "brave", "calm", "swift", "bright", "clever", "quiet", "bold", "lucky", "keen", "sunny", "witty",
       "gentle", "rapid", "sharp", "noble", "happy", "eager", "cosmic", "polar"]
NOUN = ["fox", "owl", "lynx", "falcon", "otter", "panda", "tiger", "koala", "heron", "wolf", "dolphin", "gazelle",
        "eagle", "comet", "atom", "pixel", "robot", "quark", "nova", "orbit"]
TOPICS = ["Pythagorean theorem", "Thales theorem", "Ohm's law", "Photosynthesis", "Newton's laws", "Derivatives",
          "Integrals", "French Revolution", "Cell division", "Probability basics", "Python loops", "Binary numbers",
          "Chemical bonds", "Electric circuits", "Linear equations", "World War I", "Vectors", "Logarithms"]
GAMES = ["trivia_race", "mime", "pictionary"]
LIVE = ["LIVE-DEEP", "LIVE-DEMO"]

# How often each kind of tester shows up and how much they do on an active day.
PROFILES = {   # actions per active day, and focused study minutes per active day (scoring v2 heartbeats)
    "assidu":     {"p_day": 0.8, "actions": (4, 9), "minutes": (35, 110)},
    "regulier":   {"p_day": 0.5, "actions": (2, 6), "minutes": (15, 60)},
    "occasionnel": {"p_day": 0.25, "actions": (1, 4), "minutes": (5, 30)},
    "rare":       {"p_day": 0.08, "actions": (1, 2), "minutes": (0, 15)},
}


def unique_pseudos(db: DB, n: int, rng: random.Random) -> list[str]:
    taken = {r["pseudo"].lower() for r in db.all("SELECT pseudo FROM users")}
    out = []
    while len(out) < n:
        p = f"{rng.choice(ADJ)}_{rng.choice(NOUN)}{rng.randint(1, 99)}"
        if PSEUDO_RE.match(p) and p.lower() not in taken:
            taken.add(p.lower())
            out.append(p)
    return out


def new_password() -> str:
    return secrets.token_urlsafe(9)          # 12 characters, URL-safe; the app needs at least 6


def add_user(db: DB, pseudo: str, password: str, created_at: float) -> int:
    if not PSEUDO_RE.match(pseudo):
        raise SystemExit(f"Invalid pseudo {pseudo!r}: 3-20 characters, letters, digits, _ or -.")
    if db.one("SELECT id FROM users WHERE pseudo=?", (pseudo,)):
        raise SystemExit(f"The pseudo {pseudo!r} is already taken.")
    salt = os.urandom(16)
    uid = db.execute("INSERT INTO users (pseudo, pw_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                     (pseudo, _hash(password, salt), salt.hex(), created_at)).lastrowid
    scoring.track(db, uid, "signup", ts=created_at)
    scoring.award(db, uid, "signup", ts=created_at)
    scoring.seen(db, uid, created_at, login=True)
    return uid


def _event(db: DB, uid: int, ts: float, kind: str, topic: str, correct: int = 0, total: int = 0) -> None:
    db.execute("INSERT INTO events (user_id, ts, kind, topic, correct, total) VALUES (?, ?, ?, ?, ?, ?)",
               (uid, ts, kind, topic, correct, total))


def simulate(db: DB, uid: int, profile: str, start: float, end: float, rng: random.Random) -> None:
    """Replay a plausible history through the same scoring calls the app makes."""
    prof = PROFILES[profile]
    day = start
    skill = rng.uniform(0.45, 0.95)
    while day < end:
        if day > start and rng.random() > prof["p_day"]:
            day += 86400
            continue
        t = day + rng.uniform(8, 20) * 3600 if day > start else start + 60
        if t >= end:
            break
        if day > start:
            scoring.track(db, uid, "login", ts=t)
            scoring.seen(db, uid, t, login=True)
        # a block of focused minutes (solo or a live room's focus), sometimes a session with friends
        scoring._last_beat.pop(uid, None)
        context = rng.choices(["solo", "live"], weights=[70, 30])[0]
        for _ in range(rng.randint(*prof["minutes"])):
            t += 60
            scoring.heartbeat(db, uid, context, t)
        if rng.random() < 0.3:
            for _ in range(rng.randint(10, 45)):
                t += 60
                scoring.heartbeat(db, uid, "room", t)
        for _ in range(rng.randint(*prof["actions"])):
            t += rng.uniform(3, 25) * 60
            if t >= end:
                break
            scoring.seen(db, uid, t)
            topic = rng.choice(TOPICS)
            what = rng.choices(["explain", "quiz", "review", "resume", "live", "game", "tree"],
                               weights=[30, 25, 15, 4, 10, 10, 6])[0]
            if what == "explain":
                _event(db, uid, t, "explain", topic)
                scoring.track(db, uid, "explain", {"topic": topic}, ts=t)
                scoring.award(db, uid, "explain", ref=topic, ts=t)
            elif what == "quiz":
                total = 4
                correct = sum(rng.random() < skill for _ in range(total))
                _event(db, uid, t, "quiz", topic, correct, total)
                scoring.track(db, uid, "quiz", {"topic": topic, "correct": correct, "total": total}, ts=t)
                scoring.award(db, uid, "quiz_correct", units=correct, ref=topic, ts=t)
                if correct == total:
                    scoring.award(db, uid, "quiz_perfect", ref=topic, ts=t)
            elif what == "review":
                for _ in range(rng.randint(3, 15)):
                    scoring.award(db, uid, "review_card", ts=t)
                scoring.track(db, uid, "review", {"session": True}, ts=t)
            elif what == "resume":
                _event(db, uid, t, "resume", "revision sheet")
                scoring.track(db, uid, "resume", ts=t)
                scoring.award(db, uid, "resume", ts=t)
            elif what == "live":
                scoring.track(db, uid, "live_join", {"room": rng.choice(LIVE)}, ts=t)
            elif what == "game":
                game = rng.choice(GAMES)
                pts = rng.choice([0, 40, 110, 150, 260, 300, 420])
                scoring.award(db, uid, "game", units=pts // 20, ref=game, ts=t)
            elif what == "tree":
                state = "grown" if rng.random() < skill else "wilted"
                scoring.track(db, uid, "pocket_tree", {"room": rng.choice(LIVE), "state": state}, ts=t)
                if state == "grown":
                    scoring.award(db, uid, "pocket_tree", ts=t)
        day += 86400


def cmd_create(a) -> None:
    if os.path.exists(a.db) and not a.force:
        raise SystemExit(f"{a.db} already exists. Use --force to rebuild it, or 'add' to add users.")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(a.db + suffix):
            os.remove(a.db + suffix)
    rng = random.Random(a.seed)
    db = DB(a.db)
    db.seed_live_rooms(40)
    now = time.time()
    names = list(PROFILES)
    rows = []
    for i, pseudo in enumerate(unique_pseudos(db, a.users, rng)):
        profile = names[i % len(names)] if i < len(names) else rng.choices(names, weights=[2, 3, 3, 2])[0]
        created = now - rng.uniform(0.3, 1.0) * a.days * 86400
        password = new_password()
        uid = add_user(db, pseudo, password, created)
        simulate(db, uid, profile, created, now, rng)
        rows.append({"pseudo": pseudo, "password": password, "profile": profile})
    with open(a.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pseudo", "password", "profile"])
        w.writeheader()
        w.writerows(rows)
    db.close()
    print(f"Created {a.db}: {len(rows)} users, {a.days} days of history. Credentials: {a.csv}")
    print(f"Run the app on it:  HERWIEGO_DB={a.db} uvicorn app.main:app --reload")


def cmd_add(a) -> None:
    if not os.path.exists(a.db):
        raise SystemExit(f"{a.db} does not exist. Run 'create' first.")
    db = DB(a.db)
    pseudo = a.pseudo or unique_pseudos(db, 1, random.Random())[0]
    password = a.password or new_password()
    add_user(db, pseudo, password, time.time())
    new_file = not os.path.exists(a.csv)
    with open(a.csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pseudo", "password", "profile"])
        if new_file:
            w.writeheader()
        w.writerow({"pseudo": pseudo, "password": password, "profile": "manuel"})
    db.close()
    print(f"Added {pseudo} / {password}  (appended to {a.csv})")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "-"


def cmd_report(a) -> None:
    db = DB(a.db)
    if a.user:
        u = db.one("SELECT * FROM users WHERE pseudo=?", (a.user,))
        if not u:
            raise SystemExit(f"No user {a.user!r}.")
        s = scoring.summary(db, u["id"])
        print(f"{u['pseudo']}: {s['total']} pts, level {s['level']} ({s['progress']:.0%} to next), rank #{s['rank']},"
              f" working-day streak {s['work_streak']} d, {u['login_count']} logins, last seen {_when(u['last_seen'])}")
        w = s["week"]
        print(f"This week: {w['focused']} min focused, {w['group']} min with friends, {w['work_days']} working days,"
              f" productivity {w['productivity']}/100, points {w['points_by_kind']}")
        print("\nPoints by rule:")
        for r in s["by_rule"]:
            print(f"  {r['rule']:<14} {r['points']:>6}  ({r['times']}x)  {r['label']}")
        print("\nLast activity:")
        for r in db.all("SELECT ts, kind, detail FROM activity WHERE user_id=? ORDER BY ts DESC LIMIT 15", (u["id"],)):
            print(f"  {_when(r['ts'])}  {r['kind']:<12} {r['detail']}")
        return
    rows = db.all(
        "SELECT u.id, u.pseudo, u.created_at, u.last_seen, u.login_count,"
        " (SELECT COUNT(*) FROM activity a WHERE a.user_id=u.id) AS actions,"
        " (SELECT COALESCE(SUM(points),0) FROM points p WHERE p.user_id=u.id) AS score"
        " FROM users u ORDER BY score DESC, u.pseudo")
    print(f"{'#':>3}  {'pseudo':<20} {'score':>6} {'lvl':>3} {'wstreak':>7} {'logins':>6} {'actions':>7}  {'created':<16}  last seen")
    for i, r in enumerate(rows, 1):
        lvl = scoring.level_of(r["score"])["level"]
        print(f"{i:>3}  {r['pseudo']:<20} {r['score']:>6} {lvl:>3} {scoring.work_streak(db, r['id']):>7} {r['login_count']:>6}"
              f" {r['actions']:>7}  {_when(r['created_at']):<16}  {_when(r['last_seen'])}")
    print(f"\n{len(rows)} users · {db.one('SELECT COUNT(*) AS n FROM activity')['n']} tracked actions"
          f" · {db.one('SELECT COALESCE(SUM(points),0) AS n FROM points')['n']} points given")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="HerwieGO test database")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="build a fresh test database")
    c.add_argument("--users", type=int, default=30)
    c.add_argument("--days", type=int, default=21)
    c.add_argument("--seed", type=int, default=42)
    c.add_argument("--force", action="store_true")
    ad = sub.add_parser("add", help="add one user")
    ad.add_argument("--pseudo")
    ad.add_argument("--password")
    r = sub.add_parser("report", help="show who is tracked and their scores")
    r.add_argument("--user")
    for p in (c, ad, r):
        p.add_argument("--db", default="herwiego-test.db")
        p.add_argument("--csv", default="test-users.csv")
    a = ap.parse_args(argv)
    {"create": cmd_create, "add": cmd_add, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
