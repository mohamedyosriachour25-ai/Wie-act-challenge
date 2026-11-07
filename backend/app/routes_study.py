"""Solo study endpoints, progress, and the AI gateway status/metrics."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import materials, security
from .i18n import lang_of, tr
from .ai import pipeline
from .ai.gateway import AIError
from .auth import current_user

router = APIRouter(prefix="/api", tags=["study"])

DIAGNOSTIC = [
    {
        "q": "A new formula appears in class. What helps you most first?",
        "options": [
            ("A picture or graph that shows it", "visual"),
            ("A worked example, step by step", "stepwise"),
            ("A comparison with something I already know", "analogy"),
            ("A problem to try right away", "practice"),
        ],
    },
    {
        "q": "You are stuck on a problem. What do you usually do?",
        "options": [
            ("Sketch it or look at a diagram", "visual"),
            ("Follow a solution line by line", "stepwise"),
            ("Ask for an everyday comparison", "analogy"),
            ("Re-read the rule until it sticks", "repetition"),
        ],
    },
    {
        "q": "How do you prefer to revise?",
        "options": [
            ("Mind maps and colour", "visual"),
            ("A numbered checklist", "stepwise"),
            ("Stories and examples", "analogy"),
            ("Flashcards and quizzes", "practice"),
        ],
    },
    {
        "q": "Small exercise: 3 pencils cost 6 dinars. How would you find the price of 5?",
        "options": [
            ("Draw 3 groups of 2, then extend to 5", "visual"),
            ("Price of one is 2, then multiply by 5", "stepwise"),
            ("Like buying coffees: same price each", "analogy"),
            ("Try numbers until it fits", "practice"),
        ],
    },
]


class DiagnosticAnswers(BaseModel):
    answers: list[int]


class ExplainBody(BaseModel):
    topic: str
    level: str | None = None


class HintBody(BaseModel):
    topic: str
    question: str
    level: int = 1


class QuizBody(BaseModel):
    topic: str
    level: str = "beginner"
    lang: str | None = None   # language the quiz was written in (so checking answers hits the same cached quiz)


class AnswerBody(BaseModel):
    topic: str
    level: str = "beginner"
    lang: str | None = None
    question: int
    choice: int


class FinishBody(BaseModel):
    topic: str
    correct: int
    total: int


class SimulateBody(BaseModel):
    mode: str


def ai_error_message(exc: AIError, lang: str) -> str:
    if exc.kind in ("bad_request", "upstream"):
        return tr(lang, "The AI service could not handle this request. Try again later.")
    return tr(lang, exc.message)


def ai_error_response(exc: AIError, lang: str = "en") -> JSONResponse:
    code = {"quota": 429, "unavailable": 503, "bad_request": 502}.get(exc.kind, 502)
    headers = {"Retry-After": str(int(exc.retry_after) + 1)} if exc.retry_after else {}
    return JSONResponse(
        {"detail": ai_error_message(exc, lang), "kind": exc.kind, "retry_after": int(exc.retry_after) + 1 if exc.retry_after else None},
        status_code=code,
        headers=headers,
    )


def log_event(request: Request, user_id: int, kind: str, topic: str, correct: int = 0, total: int = 0) -> None:
    request.app.state.db.execute(
        "INSERT INTO events (user_id, ts, kind, topic, correct, total) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, time.time(), kind, topic[:160], correct, total),
    )


@router.get("/study/diagnostic")
def diagnostic(request: Request):
    current_user(request)
    lang = lang_of(request)
    return {"questions": [{"q": tr(lang, d["q"]), "options": [tr(lang, o[0]) for o in d["options"]]} for d in DIAGNOSTIC]}


@router.post("/study/diagnostic")
def submit_diagnostic(body: DiagnosticAnswers, request: Request):
    user = current_user(request)
    tally: dict[str, int] = {}
    for item, choice in zip(DIAGNOSTIC, body.answers):
        if 0 <= choice < len(item["options"]):
            style = item["options"][choice][1]
            tally[style] = tally.get(style, 0) + 1
    if not tally:
        raise HTTPException(400, tr(lang_of(request), "Answer at least one question."))
    top = sorted(tally.items(), key=lambda kv: -kv[1])
    style = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else "balanced"
    request.app.state.db.execute("UPDATE users SET learning_style=? WHERE id=?", (style, user["id"]))
    return {"learning_style": style, "scores": tally}


@router.post("/study/explain")
async def explain(body: ExplainBody, request: Request):
    user = current_user(request)
    security.spend_ai(request, user["id"])
    style = user.get("learning_style") or "balanced"
    try:
        # When the learner has added course material, ground the explanation in its most relevant passages.
        context, app = None, request.app
        collection = f"u:{user['id']}"
        if materials.collection_has_material(app.state.db, collection):
            found = await materials.retrieve(app, collection, body.topic, k=4)
            context = [c for c in found if c["score"] >= app.state.settings.min_similarity] or None
        result = await pipeline.explain(app.state.gateway, body.topic, style, body.level, context=context, lang=lang_of(request))
    except ValueError as exc:
        raise HTTPException(400, tr(lang_of(request), str(exc)))
    except AIError as exc:
        return ai_error_response(exc, lang_of(request))
    log_event(request, user["id"], "explain", result["topic"])
    return result


@router.post("/study/hint")
async def hint(body: HintBody, request: Request):
    user = current_user(request)
    security.spend_ai(request, user["id"])
    try:
        return await pipeline.hint(request.app.state.gateway, body.topic, body.question, body.level, lang_of(request))
    except ValueError as exc:
        raise HTTPException(400, tr(lang_of(request), str(exc)))
    except AIError as exc:
        return ai_error_response(exc, lang_of(request))


@router.post("/study/quiz")
async def quiz(body: QuizBody, request: Request):
    user = current_user(request)
    security.spend_ai(request, user["id"])
    try:
        questions, meta = await pipeline.get_quiz(request.app.state.gateway, body.topic, body.level, body.lang or lang_of(request))
    except ValueError as exc:
        raise HTTPException(400, tr(lang_of(request), str(exc)))
    except AIError as exc:
        return ai_error_response(exc, lang_of(request))
    return {"questions": pipeline.public_quiz(questions), "meta": meta}


@router.post("/study/quiz/answer")
async def quiz_answer(body: AnswerBody, request: Request):
    """Answers stay on the server until a question is answered; the quiz comes from the cache."""
    current_user(request)
    try:
        questions, _ = await pipeline.get_quiz(request.app.state.gateway, body.topic, body.level, body.lang or lang_of(request))
    except ValueError as exc:
        raise HTTPException(400, tr(lang_of(request), str(exc)))
    except AIError as exc:
        return ai_error_response(exc, lang_of(request))
    if not 0 <= body.question < len(questions):
        raise HTTPException(400, tr(lang_of(request), "Unknown question."))
    q = questions[body.question]
    return {"correct": body.choice == q["answer"], "answer": q["answer"], "why": q["why"]}


@router.post("/study/quiz/finish")
def quiz_finish(body: FinishBody, request: Request):
    user = current_user(request)
    total = max(1, min(body.total, 10))
    correct = max(0, min(body.correct, total))
    log_event(request, user["id"], "quiz", body.topic, correct, total)
    return {"ok": True}


@router.get("/progress")
def progress(request: Request):
    user = current_user(request)
    db = request.app.state.db
    events = db.all("SELECT ts, kind, topic, correct, total FROM events WHERE user_id=? ORDER BY ts DESC LIMIT 500", (user["id"],))
    quizzes = [e for e in events if e["kind"] in ("quiz", "group_quiz")]
    correct, total = sum(e["correct"] for e in quizzes), sum(e["total"] for e in quizzes)
    topics: dict[str, dict] = {}
    for e in reversed(events):
        t = topics.setdefault(e["topic"], {"topic": e["topic"], "explained": 0, "correct": 0, "total": 0, "last": e["ts"]})
        t["last"] = e["ts"]
        if e["kind"] == "explain":
            t["explained"] += 1
        else:
            t["correct"] += e["correct"]
            t["total"] += e["total"]
    for t in topics.values():
        t["mastery"] = round(t["correct"] / t["total"], 2) if t["total"] else None
    days = sorted({datetime.fromtimestamp(e["ts"], timezone.utc).date() for e in events}, reverse=True)
    streak, today = 0, datetime.now(timezone.utc).date()
    for i, day in enumerate(days):
        if (today - day).days == i or (i == 0 and (today - day).days == 1):
            streak += 1
        else:
            break
    return {
        "pseudo": user["pseudo"],
        "learning_style": user["learning_style"],
        "xp": correct * 10 + sum(1 for e in events if e["kind"] == "explain") * 5,
        "streak_days": streak,
        "quizzes": len(quizzes),
        "accuracy": round(correct / total, 2) if total else None,
        "topics": sorted(topics.values(), key=lambda t: -t["last"])[:12],
    }


# ---------------------------------------------------------------- AI gateway
@router.get("/ai/status")
def ai_status(request: Request):
    return request.app.state.gateway.status()


@router.get("/ai/metrics")
def ai_metrics(request: Request):
    gw = request.app.state.gateway
    return {**gw.metrics.to_dict(), "cache_entries": len(gw.cache), "mock": gw.s.is_mock, "models": {"compact": gw.s.compact_model, "mid": gw.s.mid_model}}


@router.post("/ai/simulate")
def ai_simulate(body: SimulateBody, request: Request):
    """Demo helper: only works against the built-in fake service."""
    state = request.app.state.mock_state
    if state is None:
        raise HTTPException(400, "Simulation is only available with the built-in fake service.")
    if body.mode not in ("quota", "saturate", "clear"):
        raise HTTPException(400, "Mode must be quota, saturate or clear.")
    state.simulate(body.mode)
    if body.mode == "clear":
        request.app.state.gateway.cooldown_until = 0.0
    return {"ok": True, "mode": body.mode}
