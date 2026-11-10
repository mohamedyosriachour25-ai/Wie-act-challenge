"""Study spaces: several solo rooms per learner, each with its own course material and its own memory.

* Material: each space is one collection (`s:<id>`); the first space reuses the personal library (`u:<uid>`)
  so material already added, and the live-room notepad that reads it, keep working unchanged.
* Memory: every turn sends the last `memory_window` messages verbatim plus the space's running summary;
  once more than `memory_fold_after` messages sit beyond that window, the oldest are folded into the
  summary by one compact call in the background (never on the learner's critical path).
* A turn (`POST /api/spaces/{id}/ask`) answers as Server-Sent Events, one event per finished stage
  (see ai/tutor.py). The service itself answers whole; the stream is between this server and the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import materials, security
from .ai import prompts, tutor
from .ai.gateway import AIError
from .auth import current_user
from .i18n import lang_of, tr

log = logging.getLogger("herwiego")
router = APIRouter(prefix="/api/spaces", tags=["spaces"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    collection TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    folded_upto INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spaces_user ON spaces(user_id, updated_at);
CREATE TABLE IF NOT EXISTS space_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    space_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_space_messages ON space_messages(space_id, id);
"""


def init(db) -> None:
    with db._lock:
        db.conn.executescript(SCHEMA)
        db.conn.commit()


# ----------------------------------------------------------------------------- helpers
def _out(db, sp: dict) -> dict:
    docs = db.one("SELECT COUNT(*) AS n FROM docs WHERE collection=?", (sp["collection"],))["n"]
    msgs = db.one("SELECT COUNT(*) AS n FROM space_messages WHERE space_id=?", (sp["id"],))["n"]
    return {"id": sp["id"], "name": sp["name"], "docs": docs, "messages": msgs, "primary": sp["collection"].startswith("u:"),
            "updated_at": sp["updated_at"]}


def ensure_default(db, user: dict, lang: str) -> None:
    if not db.one("SELECT 1 AS x FROM spaces WHERE user_id=? LIMIT 1", (user["id"],)):
        now = time.time()
        db.execute("INSERT INTO spaces (user_id, name, collection, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (user["id"], tr(lang, "My study space"), f"u:{user['id']}", now, now))


def get_space(request: Request, user: dict, space_id: int) -> dict:
    sp = request.app.state.db.one("SELECT * FROM spaces WHERE id=? AND user_id=?", (space_id, user["id"]))
    if sp is None:   # another learner's space is indistinguishable from a missing one
        raise HTTPException(404, tr(lang_of(request), "This study space does not exist."))
    return sp


def collection_for_scope(request: Request, user: dict, scope: str) -> str | None:
    """`space-<id>` material scope -> its collection (None when the scope is not a space)."""
    if not scope.startswith("space-"):
        return None
    try:
        space_id = int(scope[6:])
    except ValueError:
        raise HTTPException(404, tr(lang_of(request), "This study space does not exist."))
    return get_space(request, user, space_id)["collection"]


def _clean_name(request: Request, name: str) -> str:
    name = " ".join((name or "").split())[:40]
    if len(name) < 1:
        raise HTTPException(400, tr(lang_of(request), "Give the space a name."))
    return name


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ------------------------------------------------------------------------------ routes
class NameBody(BaseModel):
    name: str


class AskBody(BaseModel):
    message: str
    web: bool = True


@router.get("")
def list_spaces(request: Request):
    user = current_user(request)
    db = request.app.state.db
    ensure_default(db, user, lang_of(request))
    rows = db.all("SELECT * FROM spaces WHERE user_id=? ORDER BY collection LIKE 'u:%' DESC, updated_at DESC", (user["id"],))
    return {"spaces": [_out(db, r) for r in rows], "web_search": request.app.state.settings.web_search, "max": request.app.state.settings.max_spaces}


@router.post("")
def create_space(body: NameBody, request: Request):
    user = current_user(request)
    db, lang = request.app.state.db, lang_of(request)
    ensure_default(db, user, lang)
    limit = request.app.state.settings.max_spaces
    if db.one("SELECT COUNT(*) AS n FROM spaces WHERE user_id=?", (user["id"],))["n"] >= limit:
        raise HTTPException(400, tr(lang, "You can keep {n} study spaces. Delete one first.", n=limit))
    now = time.time()
    cur = db.execute("INSERT INTO spaces (user_id, name, collection, created_at, updated_at) VALUES (?, ?, '', ?, ?)",
                     (user["id"], _clean_name(request, body.name), now, now))
    db.execute("UPDATE spaces SET collection=? WHERE id=?", (f"s:{cur.lastrowid}", cur.lastrowid))
    return _out(db, db.one("SELECT * FROM spaces WHERE id=?", (cur.lastrowid,)))


@router.patch("/{space_id}")
def rename_space(space_id: int, body: NameBody, request: Request):
    user = current_user(request)
    get_space(request, user, space_id)
    db = request.app.state.db
    db.execute("UPDATE spaces SET name=?, updated_at=? WHERE id=?", (_clean_name(request, body.name), time.time(), space_id))
    return _out(db, db.one("SELECT * FROM spaces WHERE id=?", (space_id,)))


@router.delete("/{space_id}")
async def delete_space(space_id: int, request: Request):
    user = current_user(request)
    sp = get_space(request, user, space_id)
    if sp["collection"].startswith("u:"):
        raise HTTPException(400, tr(lang_of(request), "Your first space holds your personal library and cannot be deleted. Clear its conversation instead."))
    db = request.app.state.db
    col = sp["collection"]
    doc_ids = [r["id"] for r in db.all("SELECT id FROM docs WHERE collection=?", (col,))]
    db.execute("DELETE FROM chunks WHERE doc_id IN (SELECT id FROM docs WHERE collection=?)", (col,))
    db.execute("DELETE FROM blobs WHERE doc_id IN (SELECT id FROM docs WHERE collection=?)", (col,))
    db.execute("DELETE FROM docs WHERE collection=?", (col,))
    db.execute("DELETE FROM space_messages WHERE space_id=?", (space_id,))
    db.execute("DELETE FROM spaces WHERE id=?", (space_id,))
    await materials.forget_vectors(request.app, doc_ids)
    return {"ok": True}


@router.get("/{space_id}/messages")
def messages(space_id: int, request: Request):
    user = current_user(request)
    sp = get_space(request, user, space_id)
    rows = request.app.state.db.all("SELECT id, role, content, meta, ts FROM space_messages WHERE space_id=? ORDER BY id DESC LIMIT 60", (space_id,))
    return {
        "space": _out(request.app.state.db, sp),
        "has_memory": bool(sp["summary"]),
        "running": sp["id"] in _turns(request.app),
        "messages": [{**r, "meta": json.loads(r["meta"] or "{}")} for r in reversed(rows)],
    }


@router.delete("/{space_id}/messages")
def clear_messages(space_id: int, request: Request):
    user = current_user(request)
    get_space(request, user, space_id)
    db = request.app.state.db
    turn = _turns(request.app).get(space_id)
    if turn and turn.task:
        turn.task.cancel()
    db.execute("DELETE FROM space_messages WHERE space_id=?", (space_id,))
    db.execute("UPDATE spaces SET summary='', folded_upto=0, updated_at=? WHERE id=?", (time.time(), space_id))
    return {"ok": True}


# ------------------------------------------------------------------------------ turns
# A tutor turn runs as a server task, not inside the request: leaving the page, switching tabs or losing the
# connection does not stop it. Its progress is written to the assistant message as it goes (meta.pending +
# the events so far), so a learner who comes back sees the answer where it is and reconnects to the rest.
class Turn:
    def __init__(self, space_id: int, row_id: int):
        self.space_id, self.row_id = space_id, row_id
        self.events: list[dict] = []
        self.done = False
        self.cond = asyncio.Condition()
        self.task: asyncio.Task | None = None

    async def push(self, ev: dict) -> None:
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    async def stream(self):
        """Every event so far, then the rest as it arrives. Ends when the turn ends or the client goes away."""
        i = 0
        while True:
            async with self.cond:
                while i >= len(self.events) and not self.done:
                    await self.cond.wait()
                batch, finished = self.events[i:], self.done
                i += len(batch)
            for ev in batch:
                yield _sse(ev)
            if finished and i >= len(self.events):
                return


def _turns(app) -> dict[int, Turn]:
    if not hasattr(app.state, "space_turns"):
        app.state.space_turns = {}
    return app.state.space_turns


def _partial_text(events: list[dict]) -> str:
    final = next((e["text"] for e in reversed(events) if e.get("type") == "final"), "")
    return final or next((e["text"] for e in reversed(events) if e.get("type") == "draft"), "")


def recover_interrupted(db) -> int:
    """At start: turns cut by a restart keep what they had produced and are marked interrupted."""
    rows = db.all("SELECT id, meta FROM space_messages WHERE role='assistant' AND meta LIKE '%\"pending\": true%'")
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except ValueError:
            meta = {}
        events = meta.get("events") or []
        db.execute("UPDATE space_messages SET content=?, meta=? WHERE id=?",
                   (_partial_text(events), json.dumps({"interrupted": True, "events": events}, ensure_ascii=False), r["id"]))
    return len(rows)


async def _run(app, turn: Turn, user: dict, sp: dict, message: str, recent: list[dict], lang: str, web_on: bool) -> None:
    from types import SimpleNamespace

    from .routes_study import ai_error_message, log_event   # late import: routes_study imports materials

    s, db = app.state.settings, app.state.db
    collection = sp["collection"]
    space_id, row_id = sp["id"], turn.row_id
    last_save = 0.0

    def save_progress(force: bool = False) -> None:
        nonlocal last_save
        if force or time.time() - last_save > 0.5:
            last_save = time.time()
            db.execute("UPDATE space_messages SET content=?, meta=? WHERE id=?",
                       (_partial_text(turn.events), json.dumps({"pending": True, "events": turn.events}, ensure_ascii=False), row_id))

    async def retrieve(query: str) -> list[dict]:
        if not materials.collection_has_material(db, collection):
            return []
        return await materials.retrieve(app, collection, query, k=6)

    try:
        async for ev in tutor.run_turn(
            gw=app.state.gateway, message=message, summary=sp["summary"], recent=recent,
            style=user.get("learning_style") or "balanced", lang=lang, retrieve=retrieve,
            min_similarity=s.min_similarity, web=app.state.web if s.web_search else None,
            use_web=web_on, quick_draft=s.quick_draft, personal_data=materials.personal_data_reason,
            library=materials.library_overview(db, collection),
        ):
            if ev["type"] == "done":
                result = ev["result"]
                meta = {k: result[k] for k in ("intent", "sources", "used", "visual", "visual_fallback", "figure_note", "meta", "step_focus")}
                db.execute("UPDATE space_messages SET content=?, meta=?, ts=? WHERE id=?",
                           (result["text"], json.dumps(meta, ensure_ascii=False), time.time(), row_id))
                db.execute("UPDATE spaces SET updated_at=? WHERE id=?", (time.time(), space_id))
                if result["intent"]["intent"] != "chat":
                    log_event(SimpleNamespace(app=app), user["id"], "explain", result["intent"]["topic"])
                _schedule_fold(app, space_id, lang)
                await turn.push({"type": "done", "message_id": row_id, "meta": result["meta"], "used": result["used"]})
            else:
                await turn.push(ev)
                save_progress(ev["type"] in ("draft", "final", "visual"))
    except asyncio.CancelledError:   # stopped by the learner: keep what was produced
        db.execute("UPDATE space_messages SET content=?, meta=? WHERE id=?",
                   (_partial_text(turn.events), json.dumps({"stopped": True, "events": turn.events}, ensure_ascii=False), row_id))
        await turn.push({"type": "error", "kind": "stopped", "detail": tr(lang, "Stopped.")})
    except Exception as exc:
        if isinstance(exc, AIError):
            err = {"type": "error", "kind": exc.kind, "detail": ai_error_message(exc, lang),
                   "retry_after": int(exc.retry_after) + 1 if exc.retry_after else None}
        else:
            log.exception("study-space turn failed space=%s", space_id)
            err = {"type": "error", "kind": "server", "detail": tr(lang, "Something went wrong on our side. Try again in a moment.")}
        db.execute("UPDATE space_messages SET content=?, meta=? WHERE id=?",
                   (_partial_text(turn.events), json.dumps({"error": err, "events": turn.events}, ensure_ascii=False), row_id))
        await turn.push(err)
    finally:
        _turns(app).pop(space_id, None)
        await turn.finish()


@router.post("/{space_id}/ask")
async def ask(space_id: int, body: AskBody, request: Request):
    user = current_user(request)
    sp = get_space(request, user, space_id)
    lang, app = lang_of(request), request.app
    s, db = app.state.settings, app.state.db
    try:
        message = tutor.clean_message(body.message)
    except ValueError as exc:
        raise HTTPException(400, tr(lang, str(exc)))
    reason = materials.personal_data_reason(message)
    if reason:   # the guide: only anonymised data may be sent to the service
        raise HTTPException(400, f"{tr(lang, reason)} {tr(lang, 'Messages are sent to the AI service, so remove personal details and send it again.')}")
    if space_id in _turns(app):
        raise HTTPException(409, tr(lang, "The tutor is still answering your previous message in this space."))
    security.spend_ai(request, user["id"])

    window = max(0, s.memory_window)
    recent = db.all("SELECT role, content FROM space_messages WHERE space_id=? AND content != '' ORDER BY id DESC LIMIT ?",
                    (space_id, window))[::-1] if window else []
    now = time.time()
    db.execute("INSERT INTO space_messages (space_id, role, content, ts) VALUES (?, 'user', ?, ?)", (space_id, message, now))
    cur = db.execute("INSERT INTO space_messages (space_id, role, content, meta, ts) VALUES (?, 'assistant', '', ?, ?)",
                     (space_id, json.dumps({"pending": True, "events": []}), now))
    turn = Turn(space_id, cur.lastrowid)
    _turns(app)[space_id] = turn
    turn.task = asyncio.create_task(_run(app, turn, dict(user), dict(sp), message, recent, lang, body.web))
    app.state.bg_tasks.add(turn.task)
    turn.task.add_done_callback(app.state.bg_tasks.discard)
    return StreamingResponse(turn.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{space_id}/turn")
async def follow_turn(space_id: int, request: Request):
    """Reconnect to the turn still running in this space (after leaving the page): replays it, then follows it."""
    user = current_user(request)
    get_space(request, user, space_id)
    turn = _turns(request.app).get(space_id)
    if turn is None:
        return Response(status_code=204)
    return StreamingResponse(turn.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.delete("/{space_id}/turn")
async def stop_turn(space_id: int, request: Request):
    user = current_user(request)
    get_space(request, user, space_id)
    turn = _turns(request.app).get(space_id)
    if turn and turn.task and not turn.task.done():
        turn.task.cancel()
    return {"ok": True}


# ------------------------------------------------------------------------------ memory
def _schedule_fold(app, space_id: int, lang: str) -> None:
    tasks = app.state.bg_tasks
    task = asyncio.create_task(fold_memory(app, space_id, lang))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def fold_memory(app, space_id: int, lang: str) -> bool:
    """Fold messages older than the window into the space summary (one compact call). Errors are ignored:
    the window alone still carries the recent context, and the next turn tries again."""
    s, db = app.state.settings, app.state.db
    sp = db.one("SELECT * FROM spaces WHERE id=?", (space_id,))
    if sp is None:
        return False
    rows = db.all("SELECT id, role, content FROM space_messages WHERE space_id=? AND id>? ORDER BY id", (space_id, sp["folded_upto"]))
    window = max(0, s.memory_window)
    if len(rows) <= window + max(1, s.memory_fold_after):
        return False
    older = rows[: len(rows) - window]
    try:
        text, _ = await app.state.gateway.chat("memory", "compact", prompts.memory_messages(sp["summary"], older, lang), max_tokens=220, temperature=0.2)
    except AIError:
        return False
    text = " ".join(text.split())[:1200]
    if not text:
        return False
    db.execute("UPDATE spaces SET summary=?, folded_upto=? WHERE id=?", (text, older[-1]["id"], space_id))
    return True
