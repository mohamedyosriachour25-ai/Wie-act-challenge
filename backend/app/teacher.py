"""Teacher dashboard: learning analytics for the study rooms a person created (the spec suggests a teacher
dashboard and learning analytics; "the AI supports teachers and mentors, it does not replace them").

Only the room's owner sees it, and it only shows what the room itself already shows its members, aggregated:
pseudonyms (never names), who takes part and who stays quiet (the peer-learning imbalance the challenge is
about), what was asked, and which competition questions most learners missed. Anonymous messages stay
anonymous: they are counted for the room, never attributed. No AI call.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Request

from .auth import current_user
from .i18n import lang_of, tr

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _owned(request: Request, user: dict, code: str) -> dict:
    row = request.app.state.db.one("SELECT code, name, topic, created_at, owner_id, kind FROM rooms WHERE code=?", (code.upper(),))
    if row is None or row["owner_id"] != user["id"] or row["kind"] != "group":
        raise HTTPException(404, tr(lang_of(request), "This room does not exist or is not yours."))
    return row


def _joined(db, code: str) -> dict[int, dict]:
    """user_id -> {pseudo, joins, last} from the tracked room joins."""
    rows = db.all(
        "SELECT a.user_id, u.pseudo, COUNT(*) AS joins, MAX(a.ts) AS last FROM activity a JOIN users u ON u.id=a.user_id"
        " WHERE a.kind='room_join' AND a.detail=? GROUP BY a.user_id", (json.dumps({"room": code}, ensure_ascii=False),))
    return {r["user_id"]: r for r in rows}


@router.get("/rooms")
def my_rooms(request: Request):
    user = current_user(request)
    db = request.app.state.db
    out = []
    for r in db.all("SELECT code, name, topic, created_at FROM rooms WHERE owner_id=? AND kind='group' ORDER BY created_at DESC", (user["id"],)):
        msgs = db.one("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM room_messages WHERE code=?", (r["code"],))
        comps = db.one("SELECT COUNT(*) AS n FROM competitions WHERE room_code=?", (r["code"],))["n"]
        out.append({**r, "learners": len(_joined(db, r["code"])), "messages": msgs["n"], "last_activity": msgs["last"] or r["created_at"],
                    "competitions": comps})
    return {"rooms": out}


@router.get("/rooms/{code}")
def room_report(code: str, request: Request):
    user = current_user(request)
    room = _owned(request, user, code)
    db = request.app.state.db
    code = room["code"]
    joined = _joined(db, code)
    msgs = db.all("SELECT kind, author, text, created_at FROM room_messages WHERE code=? ORDER BY id", (code,))
    chat = [m for m in msgs if m["kind"] == "chat"]
    per_author: dict[str, int] = {}
    anonymous = 0
    for m in chat:
        if m["author"] in ("", "Anonymous"):
            anonymous += 1
        else:
            per_author[m["author"]] = per_author.get(m["author"], 0) + 1

    comps = db.all("SELECT topic, ts, players, leaderboard, questions FROM competitions WHERE room_code=? ORDER BY ts DESC", (code,))
    comp_points: dict[str, dict] = {}
    hardest = []
    for c in comps:
        for r in json.loads(c["leaderboard"]):
            p = comp_points.setdefault(r["pseudo"], {"points": 0, "correct": 0, "played": 0, "wins": 0})
            p["points"] += r["points"]
            p["correct"] += r["correct"]
            p["played"] += 1
            p["wins"] += 1 if r["rank"] == 1 else 0
        for q in json.loads(c["questions"]):
            if q["answered"]:
                hardest.append({"q": q["q"], "type": q["type"], "topic": c["topic"], "right": q["right"], "answered": q["answered"],
                                "rate": round(q["right"] / q["answered"], 2)})
    hardest.sort(key=lambda q: (q["rate"], -q["answered"]))

    total_named = sum(per_author.values())
    learners = []
    pseudos = {j["pseudo"] for j in joined.values()} | set(per_author) | set(comp_points)
    for pseudo in sorted(pseudos, key=str.lower):
        j = next((x for x in joined.values() if x["pseudo"] == pseudo), None)
        n = per_author.get(pseudo, 0)
        cp = comp_points.get(pseudo)
        learners.append({
            "pseudo": pseudo, "joins": j["joins"] if j else 0, "last_seen": j["last"] if j else None, "messages": n,
            "share": round(n / total_named, 2) if total_named else 0.0,
            "questions_asked": sum(1 for m in msgs if m["kind"] == "answer" and m["author"] == pseudo),
            "competition": cp, "accuracy": round(cp["correct"] / max(1, cp["played"] * 5), 2) if cp else None,
        })
    shares = sorted((l["share"] for l in learners), reverse=True)
    return {
        "room": {"code": code, "name": room["name"], "topic": room["topic"], "created_at": room["created_at"]},
        "totals": {"learners": len(learners), "messages": len(chat), "anonymous_messages": anonymous,
                   "explanations": sum(1 for m in msgs if m["kind"] == "explanation"),
                   "questions": sum(1 for m in msgs if m["kind"] == "answer"), "competitions": len(comps)},
        # one voice taking most of the conversation is the imbalance the challenge describes
        "balance": {"top_share": shares[0] if shares else 0.0, "quiet": [l["pseudo"] for l in learners if l["joins"] and l["messages"] == 0],
                    "dominated": bool(shares and shares[0] >= 0.6 and len(learners) >= 3)},
        "learners": learners,
        "topics": [{"topic": m["text"], "at": m["created_at"]} for m in msgs if m["kind"] == "topic"][-12:],
        "questions": [{"by": m["author"], "text": m["text"].split(" → ")[0], "at": m["created_at"]} for m in msgs if m["kind"] == "answer"][-15:],
        "hardest": hardest[:8],
        "competitions": [{"topic": c["topic"], "at": c["ts"], "players": c["players"], "podium": json.loads(c["leaderboard"])[:3]} for c in comps[:10]],
        "generated_at": time.time(),
    }
