"""Rooms: group study rooms (2 to 10 people) and public Live focus rooms (hundreds).

Both use one WebSocket per person. They differ in what they allow:
  group  chat (with an anonymous option), shared topic, ONE shared explanation, shared quiz, shared
         timer, participation tracking with private nudges. Capacity 2..10.
  live   no chat: presence, a focus signal, reactions, and a timer that follows a fixed schedule.

Privacy: a person's "focused" state is a boolean computed on their own device. Camera sharing in Live
rooms is peer to peer (WebRTC): the video never passes through the server, which only relays the small
signalling messages (SDP and ICE) needed to connect two browsers directly.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import materials, security
from .ai import pipeline
from .ai.gateway import AIError
from .auth import current_user, user_from_token
from .i18n import lang_of, norm, tr

router = APIRouter(tags=["rooms"])
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REACTIONS = ("👏", "🔥", "💡", "☕")


@dataclass
class Member:
    user_id: int
    pseudo: str
    ws: WebSocket
    joined_at: float
    focused: bool | None = None
    msgs: int = 0
    anon_msgs: int = 0
    last_chat: float = 0.0
    last_nudge: float = 0.0
    last_reaction: float = 0.0
    lang: str = "en"
    in_video: bool = False  # has the camera turned on in a Live room (mesh WebRTC)


class Room:
    def __init__(self, row: dict):
        self.code = row["code"]
        self.kind = row["kind"]
        self.name = row["name"]
        self.topic = row["topic"]
        self.capacity = row["capacity"]
        self.focus_min = row["focus_min"]
        self.break_min = row["break_min"]
        self.members: dict[int, Member] = {}
        self.chat: list[dict] = []
        self.msg_total = 0
        self.timer: dict | None = None
        if self.kind == "live":  # fixed schedule aligned on the epoch: everyone sees the same phase
            self.timer = {"started_at": 0, "focus_s": self.focus_min * 60, "break_s": self.break_min * 60}
        self.explanation: dict | None = None
        self.quiz: list[dict] | None = None
        self.answers: dict[int, dict] = {}
        self.results: dict | None = None
        self.busy: str | None = None
        self.qa_log: list[dict] = []   # shared answers from the room's material
        self._presence_task: asyncio.Task | None = None

    # ------------------------------------------------------------ outputs
    def info(self) -> dict:
        return {"code": self.code, "kind": self.kind, "name": self.name, "topic": self.topic, "capacity": self.capacity, "focus_min": self.focus_min, "break_min": self.break_min}

    def presence(self) -> dict:
        members = list(self.members.values())
        if self.kind == "live":
            tracked = [m for m in members if m.focused is not None]
            return {
                "online": len(members),
                "tracking": len(tracked),
                "focused": sum(1 for m in tracked if m.focused),
                "members": [{"pseudo": m.pseudo, "focused": m.focused} for m in members[:40]],
            }
        return {
            "online": len(members),
            "capacity": self.capacity,
            "members": [{"pseudo": m.pseudo, "focused": m.focused, "msgs": m.msgs} for m in members],
        }

    def quiz_public(self) -> dict | None:
        if self.quiz is None:
            return None
        return {"questions": pipeline.public_quiz(self.quiz), "answered": self.answered_counts(), "results": self.results}

    def answered_counts(self) -> dict:
        return {a["pseudo"]: len(a["picks"]) for a in self.answers.values()}

    def snapshot(self, member: Member) -> dict:
        return {
            "type": "snapshot",
            "room": self.info(),
            "you": member.pseudo,
            "you_id": member.user_id,
            "presence": self.presence(),
            "chat": self.chat[-50:],
            "timer": self.timer,
            "topic": self.topic,
            "explanation": self.explanation,
            "quiz": self.quiz_public(),
            "busy": self.busy,
            "qa": self.qa_log[-5:],
            "server_now": time.time(),
        }


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def get(self, row: dict) -> Room:
        room = self.rooms.get(row["code"])
        if room is None:
            room = self.rooms[row["code"]] = Room(row)
        return room

    def online(self, code: str) -> int:
        room = self.rooms.get(code)
        return len(room.members) if room else 0


async def send(member: Member, payload: dict) -> None:
    try:
        await member.ws.send_json(payload)
    except Exception:
        pass


async def broadcast(room: Room, payload: dict) -> None:
    await asyncio.gather(*(send(m, payload) for m in list(room.members.values())))


async def broadcast_ai_error(room: Room, exc: Exception) -> None:
    """Each person gets the error in their own language."""
    from .routes_study import ai_error_message

    kind = getattr(exc, "kind", "input")
    retry = getattr(exc, "retry_after", None)

    def text(m: Member) -> str:
        return ai_error_message(exc, m.lang) if isinstance(exc, AIError) else tr(m.lang, str(exc))

    await asyncio.gather(*(send(m, {"type": "ai_error", "kind": kind, "message": text(m), "retry_after": retry}) for m in list(room.members.values())))


def schedule_presence(room: Room, delay: float = 1.5) -> None:
    """Coalesce presence updates: a 200-person room must not fan out on every focus ping."""
    if room._presence_task and not room._presence_task.done():
        return

    async def later() -> None:
        await asyncio.sleep(delay)
        await broadcast(room, {"type": "presence", "presence": room.presence()})

    room._presence_task = asyncio.create_task(later())


# ----------------------------------------------------------------------- REST
class CreateRoom(BaseModel):
    name: str
    topic: str = ""
    capacity: int = 6
    is_public: bool = True


def _row_out(request: Request, row: dict) -> dict:
    return {**row, "online": request.app.state.hub.online(row["code"])}


@router.get("/api/rooms")
def list_rooms(request: Request, kind: str = "group"):
    if kind not in ("group", "live"):
        raise HTTPException(400, "Kind must be group or live.")
    order = "ASC" if kind == "live" else "DESC"  # live rooms keep their designed order, new study rooms come first
    rows = request.app.state.db.all(
        f"SELECT code, kind, name, topic, capacity, focus_min, break_min FROM rooms WHERE kind=? AND is_public=1 ORDER BY created_at {order}, code LIMIT 50",
        (kind,),
    )
    return {"rooms": [_row_out(request, r) for r in rows]}


@router.post("/api/rooms")
def create_room(body: CreateRoom, request: Request):
    user = current_user(request)
    name = " ".join(body.name.split())
    if not 2 <= len(name) <= 40:
        raise HTTPException(400, tr(lang_of(request), "Give the room a name of 2 to 40 characters."))
    if not 2 <= body.capacity <= 10:
        raise HTTPException(400, tr(lang_of(request), "A study room holds 2 to 10 people."))
    db = request.app.state.db
    code = "".join(random.choices(CODE_ALPHABET, k=6))
    while db.one("SELECT code FROM rooms WHERE code=?", (code,)):
        code = "".join(random.choices(CODE_ALPHABET, k=6))
    db.execute(
        "INSERT INTO rooms (code, kind, name, topic, capacity, owner_id, is_public, created_at) VALUES (?, 'group', ?, ?, ?, ?, ?, ?)",
        (code, name, " ".join(body.topic.split())[:160], body.capacity, user["id"], int(body.is_public), time.time()),
    )
    return _row_out(request, db.one("SELECT code, kind, name, topic, capacity, focus_min, break_min FROM rooms WHERE code=?", (code,)))


@router.get("/api/rooms/{code}")
def get_room(code: str, request: Request):
    row = request.app.state.db.one(
        "SELECT code, kind, name, topic, capacity, focus_min, break_min FROM rooms WHERE code=?", (code.upper(),)
    )
    if not row:
        raise HTTPException(404, tr(lang_of(request), "No room has that code."))
    return _row_out(request, row)


# ------------------------------------------------------------------ WebSocket
@router.websocket("/ws/rooms/{code}")
async def room_socket(ws: WebSocket, code: str, token: str = ""):
    await ws.accept()
    app = ws.app
    lang = lang_of(ws)
    user = user_from_token(app, token)
    if not user:
        await ws.send_json({"type": "error", "code": "auth", "message": tr(lang, "Sign in to join a room.")})
        return await ws.close(4401)
    row = app.state.db.one("SELECT * FROM rooms WHERE code=?", (code.upper(),))
    if not row:
        await ws.send_json({"type": "error", "code": "not_found", "message": tr(lang, "No room has that code.")})
        return await ws.close(4404)

    room: Room = app.state.hub.get(row)
    previous = room.members.pop(user["id"], None)
    if previous is None and len(room.members) >= room.capacity:
        await ws.send_json({"type": "error", "code": "room_full", "message": tr(lang, "This room is full ({n} people).", n=room.capacity)})
        return await ws.close(4403)
    if previous is not None:  # same person opened a second tab: the new tab takes over
        if previous.in_video:  # its camera tile must disappear for everyone else
            for m in video_members(room):
                await send(m, {"type": "rtc_peer_leave", "id": previous.user_id})
        await send(previous, {"type": "error", "code": "replaced", "message": tr(previous.lang, "You joined this room from another tab.")})
        try:
            await previous.ws.close(4001)
        except Exception:
            pass

    member = Member(user["id"], user["pseudo"], ws, time.time(), lang=lang)
    if previous is not None:
        member.msgs, member.anon_msgs, member.joined_at = previous.msgs, previous.anon_msgs, previous.joined_at
    room.members[user["id"]] = member
    await send(member, room.snapshot(member))
    schedule_presence(room, 0.2)

    try:
        while True:
            try:
                data = await ws.receive_json()
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, dict):
                await handle(app, room, member, data)
    except WebSocketDisconnect:
        pass
    finally:
        if room.members.get(user["id"]) is member:
            await _rtc_drop(room, member)
            del room.members[user["id"]]
            schedule_presence(room, 0.2)


def video_members(room: Room) -> list[Member]:
    return [m for m in room.members.values() if m.in_video]


async def handle_rtc(app, room: Room, member: Member, kind: str, data: dict) -> None:
    """Relay WebRTC signalling for the camera wall. The media is peer to peer; only these messages pass here."""
    if kind == "rtc_join":
        if member.in_video:
            return
        peers = video_members(room)
        if len(peers) >= app.state.settings.video_capacity:
            await send(member, {"type": "rtc_full", "capacity": app.state.settings.video_capacity})
            return
        member.in_video = True
        await send(member, {"type": "rtc_peers", "peers": [{"id": m.user_id, "pseudo": m.pseudo} for m in peers]})
        for m in peers:  # tell the people already sharing that a new camera joined
            await send(m, {"type": "rtc_peer_join", "id": member.user_id, "pseudo": member.pseudo})

    elif kind == "rtc_leave":
        await _rtc_drop(room, member)

    elif kind == "rtc_signal":
        target = room.members.get(data.get("to"))
        if target is not None and target.in_video and member.in_video and isinstance(data.get("data"), dict):
            await send(target, {"type": "rtc_signal", "from": member.user_id, "data": data["data"]})


async def _rtc_drop(room: Room, member: Member) -> None:
    if not member.in_video:
        return
    member.in_video = False
    for m in video_members(room):
        await send(m, {"type": "rtc_peer_leave", "id": member.user_id})


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


async def handle(app, room: Room, member: Member, data: dict) -> None:
    kind = data.get("type")
    now = time.time()
    if kind == "lang":
        member.lang = norm(data.get("lang"))
        return

    if kind == "focus":
        value = data.get("focused")
        member.focused = value if isinstance(value, bool) else None
        schedule_presence(room)
        return

    if room.kind == "live":
        if kind == "reaction" and data.get("emoji") in REACTIONS and now - member.last_reaction > 1.0:
            member.last_reaction = now
            await broadcast(room, {"type": "reaction", "emoji": data["emoji"], "from": member.pseudo})
        elif kind in ("rtc_join", "rtc_leave", "rtc_signal"):
            await handle_rtc(app, room, member, kind, data)
        return

    # ---- group-only messages
    if kind == "chat":
        text = _clean(data.get("text"), 500)
        if not text or now - member.last_chat < 0.4:
            return
        member.last_chat = now
        anonymous = bool(data.get("anonymous"))
        if anonymous:
            member.anon_msgs += 1  # counts toward not being nudged, but never shown as participation
        else:
            member.msgs += 1
        room.msg_total += 1
        msg = {"id": f"{int(now * 1000)}-{room.msg_total}", "author": "Anonymous" if anonymous else member.pseudo, "text": text, "ts": now, "anon": anonymous}
        room.chat.append(msg)
        del room.chat[:-100]
        await broadcast(room, {"type": "chat", "msg": msg})
        schedule_presence(room, 0.5)
        await nudge_quiet_members(room, now)

    elif kind == "topic":
        topic = _clean(data.get("topic"), 160)
        if len(topic) >= 2 and topic != room.topic:
            room.topic, room.explanation, room.quiz, room.results, room.answers = topic, None, None, None, {}
            await broadcast(room, {"type": "topic", "topic": topic})

    elif kind == "timer_start":
        try:
            focus, brk = int(data.get("focus_min", 25)), int(data.get("break_min", 5))
        except (TypeError, ValueError):
            return
        room.timer = {"started_at": now, "focus_s": max(5, min(focus, 90)) * 60, "break_s": max(1, min(brk, 30)) * 60}
        await broadcast(room, {"type": "timer", "timer": room.timer, "server_now": now})

    elif kind == "timer_stop":
        room.timer = None
        await broadcast(room, {"type": "timer", "timer": None, "server_now": now})

    elif kind in ("explain", "ask", "quiz_start") and room.busy is None and (wait := security.ai_budget(app, member.user_id)):
        await send(member, {"type": "ai_error", "kind": "rate", "message": security.rate_limit_message(member.lang, wait), "retry_after": int(wait) + 1})

    elif kind == "explain":
        if room.topic and room.busy is None:
            room.busy = "explain"
            asyncio.create_task(run_explain(app, room, member.lang))
            await broadcast(room, {"type": "busy", "what": "explain"})

    elif kind == "ask":
        question = _clean(data.get("question"), 300)
        if len(question) < 3:
            return
        if not materials.collection_has_material(app.state.db, f"r:{room.code}"):
            await send(member, {"type": "ai_error", "kind": "input", "message": tr(member.lang, "This room has no material yet. Add a document first and wait until it is ready."), "retry_after": None})
        elif room.busy is None:
            room.busy = "ask"
            asyncio.create_task(run_ask(app, room, question, member.pseudo, member.lang))
            await broadcast(room, {"type": "busy", "what": "ask"})

    elif kind == "quiz_start":
        if room.topic and room.busy is None:
            room.busy = "quiz"
            asyncio.create_task(run_quiz(app, room, member.lang))
            await broadcast(room, {"type": "busy", "what": "quiz"})

    elif kind == "answer" and room.quiz is not None and room.results is None:
        q, choice = data.get("q"), data.get("choice")
        if isinstance(q, int) and isinstance(choice, int) and 0 <= q < len(room.quiz) and 0 <= choice <= 3:
            rec = room.answers.setdefault(member.user_id, {"pseudo": member.pseudo, "picks": {}})
            rec["picks"].setdefault(q, choice)  # first answer counts
            await broadcast(room, {"type": "quiz_progress", "answered": room.answered_counts()})

    elif kind == "quiz_reveal" and room.quiz is not None and room.results is None:
        await reveal(app, room)


async def nudge_quiet_members(room: Room, now: float) -> None:
    """The mediator: private, zero-cost (no AI call), never public. Quiet members get a gentle invitation."""
    if len(room.members) < 3 or room.msg_total < 4:
        return
    for m in room.members.values():
        if m.msgs == 0 and m.anon_msgs == 0 and now - m.joined_at > 120 and now - m.last_nudge > 300:
            m.last_nudge = now
            await send(m, {"type": "nudge", "text": tr(m.lang, "The conversation is moving. Want to add a thought or ask a question? You can also post anonymously.")})


async def run_ask(app, room: Room, question: str, asker: str, lang: str = "en") -> None:
    try:
        cands = await materials.retrieve(app, f"r:{room.code}", question)
        result = await pipeline.ask_material(app.state.gateway, question, cands, app.state.settings.min_similarity, lang)
        entry = {**result, "asker": asker}
        room.qa_log.append(entry)
        del room.qa_log[:-20]
        await broadcast(room, {"type": "answer", "data": entry})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def run_explain(app, room: Room, lang: str = "en") -> None:
    try:
        # Neutral style so the explanation is the same for everyone and shared through the cache.
        # When the room has course material, the explanation is grounded in the most relevant passages.
        context = None
        collection = f"r:{room.code}"
        if materials.collection_has_material(app.state.db, collection):
            found = await materials.retrieve(app, collection, room.topic, k=4)
            context = [c for c in found if c["score"] >= app.state.settings.min_similarity] or None
        result = await pipeline.explain(app.state.gateway, room.topic, "balanced", context=context, lang=lang)
        room.explanation = result
        await broadcast(room, {"type": "explanation", "data": result})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def run_quiz(app, room: Room, lang: str = "en") -> None:
    try:
        level = room.explanation["level"] if room.explanation else "beginner"
        questions, _ = await pipeline.get_quiz(app.state.gateway, room.topic, level, lang)
        room.quiz, room.answers, room.results = questions, {}, None
        await broadcast(room, {"type": "quiz", "quiz": room.quiz_public()})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def reveal(app, room: Room) -> None:
    assert room.quiz is not None
    per_q = []
    for i, q in enumerate(room.quiz):
        picks = [0, 0, 0, 0]
        for rec in room.answers.values():
            if i in rec["picks"]:
                picks[rec["picks"][i]] += 1
        per_q.append({"answer": q["answer"], "why": q["why"], "picks": picks})
    scores = []
    for user_id, rec in room.answers.items():
        correct = sum(1 for i, c in rec["picks"].items() if room.quiz[i]["answer"] == c)
        scores.append({"pseudo": rec["pseudo"], "correct": correct, "total": len(room.quiz)})
        app.state.db.execute(
            "INSERT INTO events (user_id, ts, kind, topic, correct, total) VALUES (?, ?, 'group_quiz', ?, ?, ?)",
            (user_id, time.time(), room.topic[:160], correct, len(room.quiz)),
        )
    room.results = {"questions": per_q, "scores": sorted(scores, key=lambda s: -s["correct"])}
    await broadcast(room, {"type": "quiz_results", "results": room.results})
