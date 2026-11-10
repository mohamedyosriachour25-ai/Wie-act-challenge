"""Spaced-repetition revision. Quiz questions the learner meets become flashcards, scheduled with an
SM-2-style algorithm, so they come back for review just before they would be forgotten. No extra AI cost:
cards are built from the quiz already generated (and served from cache), not from a new model call."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import scoring, security
from .ai import pipeline
from .auth import current_user
from .i18n import lang_of, tr

router = APIRouter(prefix="/api/review")

DAY = 86400.0
AGAIN_DELAY = 600.0  # a lapsed card comes back in ~10 minutes, same session
GRADES = {"again": 0, "hard": 1, "good": 2, "easy": 3}


def schedule(card: dict, grade: int, now: float) -> dict:
    """SM-2-style update. Returns the new scheduling fields."""
    ease = card["ease"]
    reps = card["reps"]
    lapses = card["lapses"]
    interval = card["interval_days"]
    if grade == 0:  # "again": reset, show again shortly
        return {"ease": max(1.3, ease - 0.2), "reps": 0, "lapses": lapses + 1,
                "interval_days": 0.0, "due_at": now + AGAIN_DELAY}
    q = {1: 3, 2: 4, 3: 5}[grade]
    ease = max(1.3, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    if reps == 0:
        interval = 1.0
    elif reps == 1:
        interval = 6.0
    else:
        interval = interval * ease
    if grade == 1:          # "hard" learns slower
        interval = max(1.0, interval * 0.7)
    elif grade == 3:        # "easy" gets a bonus
        interval = interval * 1.3
    return {"ease": ease, "reps": reps + 1, "lapses": lapses,
            "interval_days": round(interval, 2), "due_at": now + interval * DAY}


class AddQuizBody(BaseModel):
    topic: str
    level: str = "beginner"
    lang: str | None = None


@router.post("/add_quiz")
async def add_quiz(body: AddQuizBody, request: Request):
    """Turn a topic's quiz into review cards (front = question, back = the correct answer and why)."""
    user = current_user(request)
    try:
        questions, _ = await pipeline.get_quiz(request.app.state.gateway, body.topic, body.level, body.lang or lang_of(request))
    except (ValueError, Exception) as exc:  # a quiz we cannot build just adds nothing
        if exc.__class__.__name__ == "AIError":
            return {"added": 0}
        raise
    db = request.app.state.db
    now = time.time()
    added = 0
    for q in questions:
        front = q["q"].strip()
        back = f"{q['options'][q['answer']]} — {q['why']}".strip()
        try:
            db.execute(
                "INSERT INTO cards (user_id, topic, front, back, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], body.topic.strip()[:120], front[:400], back[:600], now, now),
            )
            added += 1
        except Exception:
            pass  # UNIQUE(user, front): the card already exists
    return {"added": added}


@router.get("/due")
def due(request: Request, limit: int = 20):
    user = current_user(request)
    now = time.time()
    rows = request.app.state.db.all(
        "SELECT id, topic, front, back FROM cards WHERE user_id=? AND due_at<=? ORDER BY due_at LIMIT ?",
        (user["id"], now, max(1, min(limit, 50))),
    )
    return {"cards": [dict(r) for r in rows]}


class GradeBody(BaseModel):
    id: int
    grade: str


@router.post("/grade")
def grade(body: GradeBody, request: Request):
    user = current_user(request)
    if body.grade not in GRADES:
        raise HTTPException(400, tr(lang_of(request), "Unknown grade."))
    db = request.app.state.db
    card = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", (body.id, user["id"]))
    if not card:
        raise HTTPException(404, tr(lang_of(request), "That card does not exist."))
    upd = schedule(dict(card), GRADES[body.grade], time.time())
    db.execute("UPDATE cards SET ease=?, reps=?, lapses=?, interval_days=?, due_at=? WHERE id=?",
               (upd["ease"], upd["reps"], upd["lapses"], upd["interval_days"], upd["due_at"], body.id))
    scoring.track(db, user["id"], "review", {"card": body.id, "grade": body.grade})
    scoring.award(db, user["id"], "review_card", ref=str(body.id))
    return {"ok": True, "interval_days": upd["interval_days"]}


@router.get("/stats")
def stats(request: Request):
    user = current_user(request)
    now = time.time()
    db = request.app.state.db
    total = db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=?", (user["id"],))["n"]
    due_n = db.one("SELECT COUNT(*) AS n FROM cards WHERE user_id=? AND due_at<=?", (user["id"], now))["n"]
    return {"total": total, "due": due_n}
