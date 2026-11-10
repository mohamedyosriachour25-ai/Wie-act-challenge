"""Rooms: group study rooms (2 to 10 people) and public Live focus rooms (hundreds).

Both use one WebSocket per person. They differ in what they allow:
  group  chat (with an anonymous option), shared topic, ONE shared explanation, shared quiz, shared
         timer, participation tracking with private nudges. Capacity 2..10.
  live   presence, a focus signal, reactions, chat, camera wall, break games and a timer on a fixed schedule.
         A live room can also be viewed as a "study forest": every member who joins the forest grows a tree
         while their device reports "focused" during the focus phase; stepping away too long wilts it.

Privacy: a person's "focused" state is a boolean computed on their own device. Camera sharing in Live
rooms is peer to peer (WebRTC): the video never passes through the server, which only relays the small
signalling messages (SDP and ICE) needed to connect two browsers directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import competition, games, materials, scoring, security
from .ai import pipeline, tutor
from .ai.gateway import AIError
from .auth import current_user, user_from_token
from .i18n import lang_of, norm, tr

router = APIRouter(tags=["rooms"])
log = logging.getLogger("herwiego")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REACTIONS = ("👏", "🔥", "💡", "☕")
GAME_SLOT_S = 25          # seconds per trivia question (matches the client's break trivia slot)
TURN_S = 30               # seconds each player gets in a turn-based game when the break is short
TURN_LONG_S = 60          # ...and when the room has a long (10-minute) break, e.g. the 50/10 room
LONG_BREAK_MIN = 10       # a break this long or longer gives each player TURN_LONG_S
FIRST_PREP_S = 20         # short breaks: extra time before the first turn so the first player can prepare
PREROLL_S = 4             # long breaks: a short "get ready" before the first turn
TURN_GAP_S = 2            # a breath between turns, while the word just played is revealed
MIN_TURN_S = 8            # a last turn shorter than this is not started (the break is about to end)
MAX_SWAPS = 3             # word changes a player may ask for per round
GROUP_GAMES = ("trivia_race", "mime", "pictionary")   # games a player can choose for the break
TURN_GAMES = ("mime", "pictionary")                    # server-driven, one performer at a time
GUESS_GAMES = ("mime", "pictionary")                   # the server judges chat guesses against each player's word
CLOCK_GAMES = ("trivia_race",)                          # client derives the schedule from the shared clock; server scores
TREE_GRACE_S = 30         # time a tree survives with its owner away per focus session (short rooms: a quarter of the focus time)
TREE_MIN_S = 120          # a tree is only planted if at least this much focus time is left (short rooms: half the focus time)
TREE_MIN_GROWN_S = 20     # a tree that wilts before it grew this long (short rooms: 5% of the focus time) is dropped, not counted:
                          # someone just looking at the page must not leave a wilted tree behind
TREE_TICK_S = 4           # while someone is in a forest, trees are advanced and broadcast this often
TREE_FULL_S = 180         # focused seconds for a tree to look full-grown; it is still only PLANTED at the end of the
#                           focus phase, so a long phase means "grow it in a few minutes, then keep it alive".
FOREST_LOG = 200          # planted/wilted trees remembered per room (in memory, like all Live state)


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
    watch_only: bool = False  # in the mesh only to watch (mime audience), sending no video
    forest: bool = False    # has opened the study-forest view of this Live room


@dataclass
class Tree:
    """One member's tree for one focus phase. Growth is counted from the focus boolean the member's own device reports."""
    pseudo: str
    cycle: int
    t0: float                # seconds into the focus phase when it was planted
    last: float              # epoch time up to which growth has been counted
    grown: float = 0.0       # seconds focused
    away: float = 0.0        # seconds not focused (or disconnected)
    state: str = "growing"   # growing | grown | wilted


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
        self.live_explain: list[dict] | None = None   # events of the explanation being generated (replayed to late joiners)
        self.live_ask: dict | None = None             # {asker, question, events} of the question being answered
        self.owner_id = row["owner_id"] if "owner_id" in row.keys() else None
        self.comp: "competition.Competition | None" = None       # the competition being played
        self.comp_last: "competition.Competition | None" = None  # the last one, for late joiners' podium
        self.comp_seq = 0
        self.events: dict[int, dict] = {}        # live group games, keyed by pomodoro cycle index
        self.game_tasks: dict[str, asyncio.Task] = {}  # one turn-game driver per chosen game in the current break
        self.game_content: dict[int, asyncio.Task] = {}  # per cycle: words + trivia for that break (AI or built-in bank)
        self.summary = row["summary"] if "summary" in row.keys() else ""
        self.summary_at = row["summary_at"] if "summary_at" in row.keys() else 0.0
        self._presence_task: asyncio.Task | None = None
        self.trees: dict[int, Tree] = {}         # study forest: one tree per member for the current/last focus phase
        self.forest: list[dict] = []             # trees that finished (planted or wilted), newest last
        self._tree_events: list[dict] = []       # finished trees not yet announced to the room
        self._forest_task: asyncio.Task | None = None
        self._restore(row["state"] if "state" in row.keys() else "")

    # ------------------------------------------------------------ saved work
    def _restore(self, raw: str) -> None:
        """A study room's generated work comes back after a restart or when the room is reopened."""
        if not raw or self.kind != "group":
            return
        try:
            st = json.loads(raw)
        except ValueError:
            return
        if st.get("topic") == self.topic:   # an explanation/quiz only belongs to the topic it was made for
            self.explanation = st.get("explanation")
            self.quiz = st.get("quiz")
            self.results = st.get("results")
        self.qa_log = (st.get("qa") or [])[-20:]

    def save(self, app) -> None:
        if self.kind != "group":
            return
        st = {"topic": self.topic, "explanation": self.explanation, "quiz": self.quiz, "results": self.results, "qa": self.qa_log[-20:]}
        try:
            app.state.db.execute("UPDATE rooms SET state=? WHERE code=?", (json.dumps(st, ensure_ascii=False), self.code))
        except Exception:
            log.exception("saving room state failed room=%s", self.code)

    # ------------------------------------------------------------ study forest
    def tree_rules(self) -> tuple[float, float]:
        """(grace, min_avail) in seconds, scaled down for very short focus phases such as the demo room."""
        focus_s = self.timer["focus_s"]
        return min(TREE_GRACE_S, focus_s * 0.25), min(TREE_MIN_S, focus_s * 0.5)

    def update_trees(self, now: float) -> None:
        """Advance every tree to `now`, finish the ones that wilted or reached the end of the focus phase,
        and plant a tree for each forest member who is focused during a focus phase. Idempotent."""
        if self.kind != "live" or not self.timer:
            return
        t = self.timer
        total = t["focus_s"] + t["break_s"]
        cyc = live_cycle(self, now)
        grace, min_s = self.tree_rules()
        for uid, tree in list(self.trees.items()):
            if tree.state == "growing":
                end = t["started_at"] + tree.cycle * total + t["focus_s"]
                upto = min(now, end)
                if upto > tree.last:
                    m = self.members.get(uid)
                    if m is not None and m.focused is True:
                        tree.grown += upto - tree.last
                    else:
                        tree.away += upto - tree.last  # not focused, or the tab/connection is gone
                    tree.last = upto
                if tree.away > grace:
                    if tree.grown < min(TREE_MIN_GROWN_S, t["focus_s"] * 0.05):
                        del self.trees[uid]  # barely started: forget it quietly
                        continue
                    self._finish_tree(tree, "wilted", now)
                elif now >= end:
                    self._finish_tree(tree, "grown", now)
            elif cyc and tree.cycle < cyc["cycles"] - 2:
                del self.trees[uid]  # long finished: it already lives on in the forest log
        if cyc and cyc["phase"] == "focus" and t["focus_s"] - cyc["into"] >= min_s:
            for m in self.members.values():
                cur = self.trees.get(m.user_id)
                if m.forest and m.focused is True and (cur is None or cur.cycle != cyc["cycles"]):
                    self.trees[m.user_id] = Tree(m.pseudo, cyc["cycles"], cyc["into"], now)

    def _finish_tree(self, tree: Tree, state: str, now: float) -> None:
        tree.state = state
        entry = {"pseudo": tree.pseudo, "cycle": tree.cycle, "state": state, "at": now}
        self.forest.append(entry)
        del self.forest[:-FOREST_LOG]
        self._tree_events.append(entry)

    def tree_view(self, uid: int, cyc: dict | None) -> dict | None:
        tree = self.trees.get(uid)
        if tree is None or cyc is None or tree.cycle != cyc["cycles"]:
            return None  # a tree belongs to its own focus phase and the break that follows it
        avail = max(1.0, min(self.timer["focus_s"] - tree.t0, TREE_FULL_S))  # full-grown after TREE_FULL_S of focus
        p = 1.0 if tree.state == "grown" else min(1.0, tree.grown / avail)
        return {"state": tree.state, "p": round(p, 3), "avail": round(avail)}

    def pop_tree_events(self) -> list[dict]:
        events, self._tree_events = self._tree_events, []
        return events

    # ------------------------------------------------------------ outputs
    def info(self) -> dict:
        return {"code": self.code, "kind": self.kind, "name": self.name, "topic": self.topic, "capacity": self.capacity, "focus_min": self.focus_min, "break_min": self.break_min}

    def presence(self) -> dict:
        members = list(self.members.values())
        if self.kind == "live":
            now = time.time()
            self.update_trees(now)
            cyc = live_cycle(self, now)
            tracked = [m for m in members if m.focused is not None]
            rows = []
            for m in members[:40]:
                row = {"pseudo": m.pseudo, "focused": m.focused, "video": m.in_video and not m.watch_only}
                view = self.tree_view(m.user_id, cyc)
                if view:
                    row["tree"] = view
                rows.append(row)
            return {
                "online": len(members),
                "tracking": len(tracked),
                "focused": sum(1 for m in tracked if m.focused),
                "members": rows,
                "at": now,
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
            "live_explain": self.live_explain,
            "live_ask": self.live_ask,
            "competition": (self.comp or self.comp_last).public() if (self.comp or self.comp_last) else None,
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


def bank_game_points(member: "Member", user_id: int, game_points: int, game: str) -> None:
    """Break-game points also count toward the account score: 1 score point per 20 game points (daily cap 30)."""
    try:
        scoring.award(member.ws.app.state.db, user_id, "game", units=game_points // 20, ref=game)
    except Exception:    # scoring must never break a game
        pass


def log_message(app, code: str, kind: str, author: str, text: str) -> None:
    """Persist one line of a study room's conversation, so the room is saved and can be summarised."""
    text = (text or "").strip()[:1000]
    if not text:
        return
    app.state.db.execute(
        "INSERT INTO room_messages (code, kind, author, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (code, kind, author or "", text, time.time()),
    )


def load_history(db, code: str, limit: int = 80) -> list[dict]:
    rows = db.all("SELECT id, kind, author, text, created_at FROM room_messages WHERE code=? ORDER BY id DESC LIMIT ?", (code, limit))
    return [{"id": r["id"], "kind": r["kind"], "author": r["author"], "text": r["text"], "ts": r["created_at"]} for r in reversed(rows)]


def transcript_text(db, code: str, limit: int = 200) -> str:
    lines = []
    for m in load_history(db, code, limit):
        who = m["author"] or {"explanation": "AI explanation", "answer": "AI answer", "topic": "Topic"}.get(m["kind"], "System")
        lines.append(f"{who}: {m['text']}")
    return "\n".join(lines)


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
        await announce_trees(room)

    room._presence_task = asyncio.create_task(later())


async def announce_trees(room: Room) -> None:
    for entry in room.pop_tree_events():
        await broadcast(room, {"type": "tree", "entry": entry})


def ensure_forest_task(room: Room) -> None:
    """While anyone is in the forest view, advance and broadcast the trees every few seconds. Stops when the last one leaves."""
    if room._forest_task and not room._forest_task.done():
        return

    async def loop() -> None:
        while any(m.forest for m in list(room.members.values())):
            await asyncio.sleep(TREE_TICK_S)
            room.update_trees(time.time())
            if room._tree_events or any(t.state == "growing" for t in room.trees.values()):
                await broadcast(room, {"type": "presence", "presence": room.presence()})
                await announce_trees(room)

    room._forest_task = asyncio.create_task(loop())


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


@router.get("/api/rooms/mine")
def my_rooms(request: Request):
    """Study rooms the signed-in user created or spoke in, newest activity first — the live-room notepad's
    "My study rooms" tab. Declared before /api/rooms/{code} so "mine" is never read as a room code."""
    user = current_user(request)
    rows = request.app.state.db.all(
        """SELECT r.code, r.name, r.topic, r.summary,
                  COALESCE((SELECT MAX(created_at) FROM room_messages m WHERE m.code = r.code), r.created_at) AS last_at
           FROM rooms r
           WHERE r.kind = 'group' AND (r.owner_id = ? OR EXISTS
                 (SELECT 1 FROM room_messages m WHERE m.code = r.code AND m.author = ?))
           ORDER BY last_at DESC LIMIT 20""",
        (user["id"], user["pseudo"]),
    )
    return {"rooms": [{**r, "summary": (r["summary"] or "")[:600]} for r in rows]}


@router.get("/api/rooms/{code}")
def get_room(code: str, request: Request):
    row = request.app.state.db.one(
        "SELECT code, kind, name, topic, capacity, focus_min, break_min FROM rooms WHERE code=?", (code.upper(),)
    )
    if not row:
        raise HTTPException(404, tr(lang_of(request), "No room has that code."))
    return _row_out(request, row)


@router.get("/api/rooms/{code}/conversation")
def room_conversation(code: str, request: Request):
    """The saved conversation of a study room, plus its latest summary. Any signed-in user with the code can read it."""
    current_user(request)
    code = code.upper()
    row = request.app.state.db.one("SELECT kind, topic, summary, summary_at FROM rooms WHERE code=?", (code,))
    if not row:
        raise HTTPException(404, tr(lang_of(request), "No room has that code."))
    if row["kind"] == "live":
        raise HTTPException(400, tr(lang_of(request), "Live focus rooms are not saved as conversations."))
    return {
        "code": code, "topic": row["topic"],
        "history": load_history(request.app.state.db, code, limit=500),
        "summary": {"text": row["summary"], "at": row["summary_at"]},
    }


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
    if previous is None:
        scoring.track(app.state.db, user["id"], "live_join" if room.kind == "live" else "room_join", {"room": room.code})
    if previous is not None:
        member.msgs, member.anon_msgs, member.joined_at = previous.msgs, previous.anon_msgs, previous.joined_at
    room.members[user["id"]] = member
    snap = room.snapshot(member)
    if room.kind != "live":  # study rooms are saved as a conversation
        snap["history"] = load_history(app.state.db, room.code)
        snap["summary"] = {"text": room.summary, "at": room.summary_at}
    else:  # public rooms host opt-in group games on the break
        s = app.state.settings
        snap["event"] = {
            "games": list(GROUP_GAMES),   # each player picks their own; same-game players play together
            "lead_s": max(20, min(s.event_lead_s, room.timer["focus_s"] - 20)),
            "slot_s": GAME_SLOT_S,
            "min_players": s.event_min_players,
        }
        snap["forest"] = room.forest[-60:]   # trees already planted or wilted in this room
        from .phonetree import room_pocket_trees   # local import: phonetree imports this module
        snap["pocket_trees"] = room_pocket_trees(room.code)   # everyone's live pocket tree in this room
        cyc = live_cycle(room, time.time())
        if cyc is not None:  # if games for this cycle already have players, send how many picked each
            ev = room.events.get(cyc["cycles"])
            if ev:
                snap["event"]["cycle"] = cyc["cycles"]
                snap["event"]["counts"] = _counts(ev)
    await send(member, snap)
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
            room.update_trees(time.time())  # growth up to the moment they left; away time counts from here
            del room.members[user["id"]]
            if not room.members:
                for tsk in [*room.game_tasks.values(), *room.game_content.values()]:
                    if not tsk.done():
                        tsk.cancel()
                room.game_content.clear()
            if room._forest_task and not any(m.forest for m in room.members.values()):
                room._forest_task.cancel()
            schedule_presence(room, 0.2)


def video_members(room: Room) -> list[Member]:
    return [m for m in room.members.values() if m.in_video]


def live_cycle(room: Room, now: float) -> dict | None:
    """Current phase of a live room's fixed, epoch-aligned schedule (mirrors the client's useCycle)."""
    t = room.timer
    if not t:
        return None
    total = t["focus_s"] + t["break_s"]
    into_total = (now - t["started_at"]) % total
    in_focus = into_total < t["focus_s"]
    return {"phase": "focus" if in_focus else "break", "cycles": int((now - t["started_at"]) // total),
            "into": into_total if in_focus else into_total - t["focus_s"]}


def _leaderboard(ev: dict) -> list[dict]:
    rows = sorted(ev["scores"].values(), key=lambda r: (-r["score"], r["pseudo"].lower()))
    return [{"pseudo": r["pseudo"], "score": r["score"], "correct": r["correct"]} for r in rows]


def _prune_events(room: Room, keep_from: int) -> None:
    for c in [c for c in room.events if c < keep_from - 1]:
        room.events.pop(c, None)


def _counts(ev: dict) -> dict:
    """How many players have chosen each game this break (shown to everyone during the invite)."""
    return {g: len(sv["optins"]) for g, sv in ev["games"].items() if sv["optins"]}


def _subev(ev: dict, game: str) -> dict:
    """The per-game sub-event (its own players, scores and turn state)."""
    return ev["games"].setdefault(game, {"game": game, "optins": {}, "scores": {}, "started": False})


async def broadcast_game(room: Room, sv: dict, payload: dict) -> None:
    """Send only to the players who chose this game, so parallel games don't see each other's messages."""
    for uid in list(sv["optins"].keys()):
        m = room.members.get(uid)
        if m is not None:
            await send(m, payload)


def turn_timing(room: Room) -> tuple[float, float]:
    """(seconds per player, seconds before the first turn) for this room's break length."""
    if room.break_min >= LONG_BREAK_MIN:
        return TURN_LONG_S, PREROLL_S
    return TURN_S, FIRST_PREP_S


def ensure_content(app, room: Room, cycle: int) -> asyncio.Task:
    """Start generating this break's words and trivia once (compact model, or the built-in bank), in the
    languages of the people in the room. When ready, every member gets the trivia in their own language."""
    task = room.game_content.get(cycle)
    if task is None:
        break_s = room.timer["break_s"] if room.timer else 300
        per_turn, _ = turn_timing(room)
        n_trivia = max(3, min(10, int(break_s // GAME_SLOT_S)))
        n_words = max(4, min(12, int(break_s // per_turn) + 2))
        langs = {m.lang for m in room.members.values()}

        async def build():
            data = await games.make_content(app, langs, n_trivia, n_words)
            for m in list(room.members.values()):
                await send(m, content_message(data, cycle, m.lang))
            return data

        task = room.game_content[cycle] = asyncio.create_task(build())
        for old in [c for c in room.game_content if c < cycle - 1]:
            room.game_content.pop(old, None)
    return task


def ready_content(room: Room, cycle: int) -> dict | None:
    task = room.game_content.get(cycle)
    if task is None or not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def content_message(data: dict, cycle: int, lang: str) -> dict:
    return {"type": "game_content", "cycle": cycle, "trivia": games.localize_trivia(data["trivia"], lang),
            "source": data["source"]["trivia"]}


async def handle_event(app, room: Room, member: Member, kind: str, data: dict) -> None:
    """Group games in a public room. Each player picks their own game during focus; on the break every game that
    has players runs in parallel — same-game players play together. Clock games (trivia) are scored from the shared
    clock; turn games (mime, pictionary) are driven by a short server task, one performer at a time."""
    now = time.time()
    cyc = live_cycle(room, now)
    if cyc is None:
        return
    try:
        cycle = int(data.get("cycle"))
    except (TypeError, ValueError):
        return
    if cycle < cyc["cycles"] - 1 or cycle > cyc["cycles"] + 1:  # only the current or an adjacent cycle
        return

    if kind == "event_optin":
        game = data.get("game")
        game = game if game in GROUP_GAMES else "trivia_race"
        ev = room.events.setdefault(cycle, {"games": {}})
        for g, other in ev["games"].items():      # a player belongs to exactly one game: drop any earlier pick
            if g != game:
                other["optins"].pop(member.user_id, None)
                other["scores"].pop(member.user_id, None)
        sv = _subev(ev, game)
        ensure_content(app, room, cycle)              # words + trivia for this break, prepared during the invite
        sv["optins"][member.user_id] = member.pseudo
        sv["scores"].setdefault(member.user_id, {"pseudo": member.pseudo, "score": 0, "correct": 0})
        _prune_events(room, cyc["cycles"])
        await broadcast(room, {"type": "event_participants", "cycle": cycle, "game": game,
                               "counts": _counts(ev),
                               "participants": sorted(sv["optins"].values(), key=str.lower)})

    elif kind == "event_optout":      # the player would rather just rest: drop them from every game this break
        ev = room.events.get(cycle)
        if ev:
            for sv in ev["games"].values():
                sv["optins"].pop(member.user_id, None)
                sv["scores"].pop(member.user_id, None)
            await broadcast(room, {"type": "event_participants", "cycle": cycle, "game": None,
                                   "counts": _counts(ev), "participants": []})

    elif kind == "event_start":       # a client reports the break has begun; start each chosen turn game once
        ev = room.events.get(cycle)
        if not ev or cyc["phase"] != "break":
            return
        for game in TURN_GAMES:
            sv = ev["games"].get(game)
            if sv and sv["optins"] and not sv["started"]:
                sv["started"] = True
                old = room.game_tasks.get(game)
                if old and not old.done():
                    old.cancel()
                room.game_tasks[game] = asyncio.create_task(run_turn_game(app, room, cycle, game, sv))

    elif kind == "game_content":      # a client wants this break's trivia (invite shown or break started)
        ensure_content(app, room, cycle)
        ready = ready_content(room, cycle)
        if ready is not None:
            await send(member, content_message(ready, cycle, member.lang))

    elif kind == "game_result":       # clock-driven games (trivia race)
        ev = room.events.get(cycle)
        game = data.get("game") if data.get("game") in CLOCK_GAMES else "trivia_race"
        sv = ev["games"].get(game) if ev else None
        if not sv or member.user_id not in sv["optins"]:
            return
        rnd = data.get("round")
        correct = bool(data.get("correct"))
        content = ready_content(room, cycle)
        pick = data.get("pick")
        if content and content["trivia"] and isinstance(rnd, int) and isinstance(pick, int):
            correct = pick == content["trivia"][rnd % len(content["trivia"])]["a"]   # the server holds the answers
        try:
            ms = max(0.0, float(data.get("ms", GAME_SLOT_S * 1000)))
        except (TypeError, ValueError):
            ms = GAME_SLOT_S * 1000
        rec = sv["scores"].setdefault(member.user_id, {"pseudo": member.pseudo, "score": 0, "correct": 0})
        seen = rec.setdefault("_rounds", set())
        if not isinstance(rnd, int) or rnd in seen:  # one answer per round
            return
        seen.add(rnd)
        if correct:
            bonus = round(50 * max(0.0, 1 - ms / (GAME_SLOT_S * 1000)))
            rec["score"] += 100 + bonus
            bank_game_points(member, member.user_id, 100 + bonus, game)
            rec["correct"] += 1
        await broadcast_game(room, sv, {"type": "event_scores", "cycle": cycle, "game": game, "leaderboard": _leaderboard(sv)})


class _BreakOver(Exception):
    """The break has no room for another turn."""


def break_left(room: Room, now: float) -> float:
    """Seconds left in this room's break (patched in tests)."""
    cyc = live_cycle(room, now)
    if not cyc or cyc["phase"] != "break" or not room.timer:
        return 0.0
    return max(0.0, room.timer["break_s"] - cyc["into"])


def _word_key(word: dict) -> str:
    return games.normalize(word.get("en") or next(iter(word.values()), ""))


def take_word(sv: dict, uid: int) -> dict:
    """A word for this player that nobody else holds right now and that has not been played this game
    (once the pool runs dry, any word nobody holds right now)."""
    held = {_word_key(w) for u, w in sv["words"].items() if u != uid}
    mine = _word_key(sv["words"][uid]) if uid in sv["words"] else None
    fresh = [w for w in sv["pool"] if _word_key(w) not in sv["used"] and _word_key(w) not in held and _word_key(w) != mine]
    if not fresh:
        fresh = [w for w in sv["pool"] if _word_key(w) not in held and _word_key(w) != mine] or sv["pool"]
    word = fresh[0]
    sv["used"].add(_word_key(word))
    sv["words"][uid] = word
    return word


async def _send_word(room: Room, sv: dict, uid: int, *, now: bool = False) -> None:
    m = room.members.get(uid)
    if m is not None:
        left = MAX_SWAPS - sv["swaps"].get((uid, sv["round_no"]), 0)
        await send(m, {"type": "game_word", "game": sv["game"], "word": games.localize(sv["words"][uid], m.lang),
                       "round": sv["round_no"], "swaps_left": left, "now": now})


def _players(room: Room, sv: dict) -> list[int]:
    return [u for u in sv["optins"] if u in room.members]


async def run_turn_game(app, room: Room, cycle: int, game: str, sv: dict) -> None:
    """Turn-based party game (mime / pictionary), looped until the break ends: every round all players play once in
    a fresh random order (never starting with whoever played last), then a new round starts with new words.
    Everyone always holds their own secret word — distinct from everyone else's, never repeated in a game — so they
    can prepare (pictionary: a private practice board) and may swap it a few times per round. A turn lasts TURN_S
    (TURN_LONG_S in long-break rooms), shortened to what is left of the break; it ends early when the performer
    passes or every other active player has guessed. Guesses are judged on the server (`judge_chat`)."""
    per_turn, prep = turn_timing(room)
    deadline = time.time() + break_left(room, time.time())
    pseudo = lambda uid: sv["optins"].get(uid, "?")
    try:
        try:
            content = await asyncio.wait_for(asyncio.shield(ensure_content(app, room, cycle)), games.AI_TIMEOUT_S + 3)
        except Exception:
            content = None
        pool, seen = [], set()
        for w in ((content or {}).get("words", {}).get(game) or []) + random.sample(games.WORDS_FALLBACK[game], len(games.WORDS_FALLBACK[game])):
            if _word_key(w) and _word_key(w) not in seen:
                seen.add(_word_key(w)); pool.append(w)
        sv.update(pool=pool, used=set(), words={}, swaps={}, round_no=0, order=[])
        order = _players(room, sv)
        random.shuffle(order)
        for uid in order:
            take_word(sv, uid)
        sv["order"] = order
        await broadcast_game(room, sv, {"type": "game_start", "game": game, "total": len(order), "order": [pseudo(u) for u in order],
                                        "per_turn": per_turn, "prep": prep})
        for uid in order:
            await _send_word(room, sv, uid)
        await asyncio.sleep(prep)
        last = None
        while True:
            if sv["round_no"] > 0:                    # a new round: fresh order, never opening with the last performer
                order = _players(room, sv)
                random.shuffle(order)
                if len(order) > 1 and order[0] == last:
                    order.append(order.pop(0))
                sv["order"] = order
                await broadcast_game(room, sv, {"type": "game_round", "game": game, "round": sv["round_no"], "order": [pseudo(u) for u in order]})
            if not order:
                break
            for i, uid in enumerate(order):
                left = deadline - time.time()
                if left < MIN_TURN_S:
                    raise _BreakOver
                seconds = min(per_turn, left)
                if uid not in room.members:           # left the room since the round started
                    continue
                if uid not in sv["words"]:
                    take_word(sv, uid)
                sv["current"] = uid
                sv["current_round"] = f"{sv['round_no']}.{i}"
                sv["solved"] = set()
                sv["turn_started"] = time.time()
                sv["turn_seconds"] = seconds
                sv["end_reason"] = None
                done = sv["turn_done"] = asyncio.Event()
                if game == "pictionary":
                    sv["strokes"] = []
                    await broadcast_game(room, sv, {"type": "draw", "op": "clear"})
                m = room.members.get(uid)
                await send(m, {"type": "game_your_turn", "game": game, "round": sv["round_no"], "index": i, "seconds": seconds,
                               "word": games.localize(sv["words"][uid], m.lang)})
                await broadcast_game(room, sv, {"type": "game_turn", "game": game, "pseudo": pseudo(uid), "round": sv["round_no"],
                                                "index": i, "total": len(order), "seconds": seconds,
                                                "next": pseudo(order[i + 1]) if i + 1 < len(order) else None})
                try:
                    await asyncio.wait_for(done.wait(), seconds)
                except asyncio.TimeoutError:
                    sv["end_reason"] = sv.get("end_reason") or "time"
                sv["current"] = None
                played = sv["words"].pop(uid)
                for pid in list(sv["optins"].keys()):     # the word is revealed to each player in their language
                    pm = room.members.get(pid)
                    if pm is not None:
                        await send(pm, {"type": "game_turn_end", "game": game, "pseudo": pseudo(uid), "round": sv["round_no"],
                                        "reason": sv["end_reason"] or "time", "word": games.localize(played, pm.lang),
                                        "leaderboard": _leaderboard(sv)})
                take_word(sv, uid)                        # their next word, so they can prepare for the next round
                await _send_word(room, sv, uid)
                last = uid
                await asyncio.sleep(TURN_GAP_S)
            sv["round_no"] += 1
    except (asyncio.CancelledError, _BreakOver):
        pass
    finally:
        sv["started"] = False
        sv["current"] = None
        try:
            await broadcast_game(room, sv, {"type": "game_over", "game": game, "leaderboard": _leaderboard(sv)})
        except Exception:
            pass


def _started_turn_subevs(room: Room) -> list[dict]:
    return [sv for ev in room.events.values() for g in TURN_GAMES
            if (sv := ev["games"].get(g)) and sv.get("started") and sv.get("words") is not None]


async def handle_word_swap(room: Room, member: Member) -> None:
    """A player finds their word too hard: give them another one (unique, unplayed), a few times per round.
    During their own turn this only works before anyone has guessed."""
    for sv in _started_turn_subevs(room):
        uid = member.user_id
        if uid not in sv["optins"] or uid not in sv["words"]:
            continue
        if sv.get("current") == uid and sv.get("solved"):
            await send(member, {"type": "game_swap_denied", "game": sv["game"], "reason": "guessed"})
            return
        key = (uid, sv["round_no"])
        if sv["swaps"].get(key, 0) >= MAX_SWAPS:
            await send(member, {"type": "game_swap_denied", "game": sv["game"], "reason": "limit"})
            return
        sv["swaps"][key] = sv["swaps"].get(key, 0) + 1
        take_word(sv, uid)
        now = sv.get("current") == uid
        if now and sv["game"] == "pictionary":      # a new word mid-turn: start from a clean board
            sv["strokes"] = []
            await broadcast_game(room, sv, {"type": "draw", "op": "clear"})
        await _send_word(room, sv, uid, now=now)
        return


def _live_turn_subevs(room: Room) -> list[dict]:
    """Turn games with someone performing right now."""
    return [sv for ev in room.events.values() for g in TURN_GAMES
            if (sv := ev["games"].get(g)) and sv.get("started") and sv.get("current") is not None]


async def judge_chat(room: Room, member: Member, text: str) -> str:
    """Check a live-room chat line against the word being performed. Returns 'pass' (send it as is),
    'hide' (a correct guess: scored, announced, never shown) or 'blur' (a near miss, or the word itself
    from someone who cannot score: everyone else sees it blurred)."""
    for sv in _live_turn_subevs(room):
        word = sv.get("words", {}).get(sv["current"])
        if not word:
            continue
        verdict = games.judge(text, word)
        if verdict is None:
            continue
        playing = member.user_id in sv["optins"] and member.user_id != sv["current"]
        if verdict == "exact" and playing and member.user_id not in sv["solved"]:
            sv["solved"].add(member.user_id)
            per_turn, _ = turn_timing(room)
            speed = max(0.0, 1 - (time.time() - sv.get("turn_started", time.time())) / max(1e-3, sv.get("turn_seconds", per_turn)))
            rec = sv["scores"].setdefault(member.user_id, {"pseudo": member.pseudo, "score": 0, "correct": 0})
            rec["score"] += 100 + round(50 * speed)
            rec["correct"] += 1
            perf = sv["scores"].setdefault(sv["current"], {"pseudo": sv["optins"].get(sv["current"], "?"), "score": 0, "correct": 0})
            perf["score"] += 40                     # the performer scores for every player who gets it
            bank_game_points(member, member.user_id, 100 + round(50 * speed), sv["game"])
            bank_game_points(member, sv["current"], 40, sv["game"])
            await broadcast_game(room, sv, {"type": "game_guessed", "game": sv["game"], "pseudo": member.pseudo,
                                            "round": sv.get("current_round"), "leaderboard": _leaderboard(sv)})
            guessers = {u for u in sv["optins"] if u != sv["current"] and u in room.members}
            if guessers and guessers <= sv["solved"]:
                sv["end_reason"] = "all"
                sv["turn_done"].set()
            return "hide"
        if verdict == "close" and playing:
            await send(member, {"type": "game_close", "game": sv["game"]})
        return "blur"
    return "pass"


async def handle_turn_pass(room: Room, member: Member) -> None:
    """The performer gives up their turn: it ends now and the next player is up."""
    for sv in _live_turn_subevs(room):
        if sv["current"] == member.user_id:
            sv["end_reason"] = "pass"
            sv["turn_done"].set()


DRAW_MAX_OPS = 4000       # cap the per-turn stroke buffer so a long drawing cannot grow memory without bound


def _pictionary_subev(room: Room, uid: int, *, performer: bool) -> dict | None:
    """The live pictionary sub-event. performer=True → the member must be the one drawing now; else a watcher in it."""
    for ev in room.events.values():
        sv = ev["games"].get("pictionary")
        if not sv or not sv.get("started") or sv.get("current") is None:
            continue
        if performer:
            if sv["current"] == uid:
                return sv
        elif uid in sv["optins"]:
            return sv
    return None


def _clamp_stroke(data: dict) -> dict | None:
    """Shrink an incoming stroke to a small, safe shape: normalised points (0..1), bounded colour index and width."""
    raw = data.get("pts")
    if not isinstance(raw, list) or not raw:
        return None
    pts = [[round(min(1.0, max(0.0, float(q[0]))), 4), round(min(1.0, max(0.0, float(q[1]))), 4)]
           for q in raw[:400]
           if isinstance(q, (list, tuple)) and len(q) == 2 and all(isinstance(v, (int, float)) for v in q)]
    if not pts:
        return None
    try:
        c = int(data.get("c", 0))
    except (TypeError, ValueError):
        c = 0
    try:
        w = float(data.get("w", 0.006))
    except (TypeError, ValueError):
        w = 0.006
    return {"pts": pts, "c": max(0, min(c, 11)), "w": round(min(0.08, max(0.001, w)), 4)}


async def handle_draw(room: Room, member: Member, data: dict) -> None:
    """Pictionary drawing relay. Only the current performer may draw; strokes go live to that game's players and are
    kept for the turn so a late watcher can catch up (op 'sync'). The server never renders — it only relays points."""
    op = data.get("op")
    if op == "sync":  # a watcher just opened the board: replay this turn's strokes to them alone
        sv = _pictionary_subev(room, member.user_id, performer=False)
        if sv is not None:
            await send(member, {"type": "draw", "op": "replay", "strokes": sv.get("strokes", [])})
        return
    sv = _pictionary_subev(room, member.user_id, performer=True)
    if sv is None:
        return
    strokes = sv.setdefault("strokes", [])
    if op == "clear":
        strokes.clear()
        await broadcast_game(room, sv, {"type": "draw", "op": "clear"})
    elif op == "stroke":
        s = _clamp_stroke(data)
        if s is not None:
            strokes.append(s)
            del strokes[:-DRAW_MAX_OPS]
            await broadcast_game(room, sv, {"type": "draw", "op": "stroke", **s})


async def handle_rtc(app, room: Room, member: Member, kind: str, data: dict) -> None:
    """Relay WebRTC signalling for the camera wall. The media is peer to peer; only these messages pass here."""
    if kind == "rtc_join":
        if member.in_video:                      # already in the mesh: a watcher who now turns their camera on
            if member.watch_only and not data.get("watch"):
                member.watch_only = False
                schedule_presence(room, 0.3)
            return
        peers = video_members(room)
        if len(peers) >= app.state.settings.video_capacity:
            await send(member, {"type": "rtc_full", "capacity": app.state.settings.video_capacity})
            return
        member.in_video = True
        member.watch_only = bool(data.get("watch"))   # receives the others' video without sending any (mime audience)
        await send(member, {"type": "rtc_peers", "peers": [{"id": m.user_id, "pseudo": m.pseudo} for m in peers]})
        for m in peers:  # tell the people already sharing that a new camera joined
            await send(m, {"type": "rtc_peer_join", "id": member.user_id, "pseudo": member.pseudo})
        schedule_presence(room, 0.3)   # the camera wall shows everyone whose camera is on

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
    member.watch_only = False
    for m in video_members(room):
        await send(m, {"type": "rtc_peer_leave", "id": member.user_id})
    schedule_presence(room, 0.3)


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
        room.update_trees(now)  # count the time since the last report under the OLD state first
        member.focused = value if isinstance(value, bool) else None
        room.update_trees(now)  # a newly focused forest member may plant a tree
        schedule_presence(room)
        return

    if room.kind == "live":
        if kind == "reaction" and data.get("emoji") in REACTIONS and now - member.last_reaction > 1.0:
            member.last_reaction = now
            await broadcast(room, {"type": "reaction", "emoji": data["emoji"], "from": member.pseudo})
        elif kind == "chat":
            text = _clean(data.get("text"), 300)
            if text and now - member.last_chat >= 0.4:
                member.last_chat = now
                msg = {"id": f"c{int(now * 1000)}-{member.user_id}", "author": member.pseudo, "text": text, "ts": now}
                verdict = await judge_chat(room, member, text)
                if verdict == "pass":
                    await broadcast(room, {"type": "chat", "msg": msg})
                elif verdict == "blur":     # the author sees their own line; everyone else a blurred bar of its length
                    await send(member, {"type": "chat", "msg": {**msg, "close": True}})
                    hidden = {**msg, "text": "", "blur": True, "len": len(text)}
                    for o in list(room.members.values()):
                        if o is not member:
                            await send(o, {"type": "chat", "msg": hidden})
        elif kind == "forest_join":
            member.forest = True
            ensure_forest_task(room)
            room.update_trees(now)
            schedule_presence(room, 0.2)
        elif kind == "dm":
            text = _clean(data.get("text"), 300)
            target = _clean(data.get("to"), 60)
            if text and target and now - member.last_chat >= 0.3:
                member.last_chat = now
                dest = next((o for o in room.members.values() if o.pseudo == target), None)
                payload = {"type": "dm", "from": member.pseudo, "to": target, "text": text, "ts": now}
                await send(member, payload)                      # echo to the sender's own thread
                if dest is not None and dest is not member:
                    await send(dest, payload)
        elif kind == "turn_pass":
            await handle_turn_pass(room, member)
        elif kind == "word_swap":
            await handle_word_swap(room, member)
        elif kind == "draw":
            await handle_draw(room, member, data)
        elif kind in ("rtc_join", "rtc_leave", "rtc_signal"):
            await handle_rtc(app, room, member, kind, data)
        elif kind in ("event_optin", "event_optout", "event_start", "game_result", "game_content"):
            await handle_event(app, room, member, kind, data)
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
        log_message(app, room.code, "chat", msg["author"], text)
        await broadcast(room, {"type": "chat", "msg": msg})
        schedule_presence(room, 0.5)
        await nudge_quiet_members(room, now)

    elif kind == "topic":
        topic = _clean(data.get("topic"), 160)
        if len(topic) >= 2 and topic != room.topic:
            room.topic, room.explanation, room.quiz, room.results, room.answers = topic, None, None, None, {}
            log_message(app, room.code, "topic", "", topic)
            app.state.db.execute("UPDATE rooms SET topic=? WHERE code=?", (topic, room.code))  # saved rooms keep their topic
            room.live_explain = None
            room.save(app)
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

    elif kind in ("explain", "ask", "quiz_start", "summarize", "comp_start") and room.busy is None and (wait := security.ai_budget(app, member.user_id)):
        await send(member, {"type": "ai_error", "kind": "rate", "message": security.rate_limit_message(member.lang, wait), "retry_after": int(wait) + 1})

    elif kind == "explain":
        if room.topic and room.busy is None:
            room.busy = "explain"
            asyncio.create_task(run_explain(app, room, member.lang))
            await broadcast(room, {"type": "busy", "what": "explain"})

    elif kind == "ask":
        question = _clean(data.get("question"), 500)
        if len(question) < 3:
            return
        if not materials.collection_has_material(app.state.db, f"r:{room.code}"):
            await send(member, {"type": "ai_error", "kind": "input", "message": tr(member.lang, "This room has no material yet. Add a document first and wait until it is ready."), "retry_after": None})
        elif (reason := materials.personal_data_reason(question)):
            await send(member, {"type": "ai_error", "kind": "input", "message": f"{tr(member.lang, reason)} {tr(member.lang, 'Messages are sent to the AI service, so remove personal details and send it again.')}", "retry_after": None})
        elif room.busy is None:
            room.busy = "ask"
            asyncio.create_task(run_ask(app, room, question, member.pseudo, member.lang))
            await broadcast(room, {"type": "busy", "what": "ask"})

    elif kind == "quiz_start":
        if room.topic and room.busy is None and room.comp is None:
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

    elif kind == "comp_start":
        if room.topic and room.busy is None and room.comp is None and room.quiz is None:
            room.comp_seq += 1
            comp = competition.Competition(room.comp_seq, room.topic, member.pseudo)
            room.comp, room.busy = comp, "competition"
            level = ((room.explanation or {}).get("intent") or {}).get("level", "beginner")
            await broadcast(room, {"type": "comp_preparing", "competition": comp.public()})
            await broadcast(room, {"type": "busy", "what": "competition"})
            asyncio.create_task(competition.run(app, room, comp, level, member.lang))

    elif kind == "comp_answer" and room.comp is not None:
        comp, i = room.comp, data.get("i")
        if isinstance(i, int) and comp.submit(member.user_id, member.pseudo, data.get("value"), i):
            answered = len(comp.answers.get(i, {}))
            await send(member, {"type": "comp_ack", "i": i})
            await broadcast(room, {"type": "comp_progress", "i": i, "answered": answered, "players": len(room.members)})
            if answered >= len(room.members):
                comp.answered.set()        # everyone answered: reveal now, no waiting for the clock

    elif kind == "comp_stop" and room.comp is not None:
        if member.pseudo == room.comp.by or member.user_id == room.owner_id:
            room.comp_last, room.comp = None, None
            if room.busy == "competition":
                room.busy = None
                await broadcast(room, {"type": "busy", "what": None})
            await broadcast(room, {"type": "comp_cancelled", "by": member.pseudo})

    elif kind == "summarize" and room.busy is None:
        room.busy = "summary"
        asyncio.create_task(run_summary(app, room, member.lang))
        await broadcast(room, {"type": "busy", "what": "summary"})


async def nudge_quiet_members(room: Room, now: float) -> None:
    """The mediator: private, zero-cost (no AI call), never public. Quiet members get a gentle invitation."""
    if len(room.members) < 3 or room.msg_total < 4:
        return
    for m in room.members.values():
        if m.msgs == 0 and m.anon_msgs == 0 and now - m.joined_at > 120 and now - m.last_nudge > 300:
            m.last_nudge = now
            await send(m, {"type": "nudge", "text": tr(m.lang, "The conversation is moving. Want to add a thought or ask a question? You can also post anonymously.")})


async def run_ask(app, room: Room, question: str, asker: str, lang: str = "en") -> None:
    """A member's question to the room's material, answered with the staged tutor (no figure) and shared:
    everyone sees the stages, the quick draft and the final answer, with citations. The last two room
    Q&As are the conversation, so a follow-up ("and the second one?") is understood."""
    s = app.state.settings
    collection = f"r:{room.code}"
    recent: list[dict] = []
    for q in room.qa_log[-2:]:
        recent += [{"role": "user", "content": q.get("question", "")}, {"role": "assistant", "content": q.get("text") or q.get("answer", "")}]

    async def retrieve(query: str) -> list[dict]:
        return await materials.retrieve(app, collection, query, k=6)

    room.live_ask = {"asker": asker, "question": question, "events": []}
    try:
        async for ev in tutor.run_turn(
            gw=app.state.gateway, message=question, summary=room.summary or "", recent=recent, style="balanced", lang=lang,
            retrieve=retrieve, min_similarity=s.min_similarity, web=None, use_web=False, quick_draft=s.quick_draft,
            library=materials.library_overview(app.state.db, collection), figures=False,
        ):
            if ev["type"] == "done":
                r = ev["result"]
                entry = {"question": question, "asker": asker, "text": r["text"], "sources": r["sources"], "used": r["used"],
                         "grounded": any(x["kind"] == "material" for x in r["sources"]), "meta": r["meta"]}
                room.qa_log.append(entry)
                del room.qa_log[:-20]
                room.live_ask = None
                room.save(app)
                log_message(app, room.code, "answer", asker, f"{question} → {r['text'][:400]}")
                await broadcast(room, {"type": "answer", "data": entry})
            else:
                if room.live_ask is not None:
                    room.live_ask["events"].append(ev)
                await broadcast(room, {"type": "tutor_ask", "event": ev, "asker": asker, "question": question})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.live_ask = None
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def run_explain(app, room: Room, lang: str = "en") -> None:
    """The room's shared explanation, as staged partial results every member sees at the same time
    (`tutor` events): sources -> compact quick draft -> mid-size final answer -> figure (in parallel).
    The topic is explicit, so no intent call; neutral style so it is the same for everyone and cached."""
    s = app.state.settings
    collection = f"r:{room.code}"

    async def retrieve(query: str) -> list[dict]:
        if not materials.collection_has_material(app.state.db, collection):
            return []
        return await materials.retrieve(app, collection, query, k=6)

    room.live_explain = []
    try:
        topic = room.topic
        async for ev in tutor.run_turn(
            gw=app.state.gateway, message=topic, summary=room.summary or "", recent=[], style="balanced", lang=lang,
            retrieve=retrieve, min_similarity=s.min_similarity, web=app.state.web if s.web_search else None,
            use_web=True, quick_draft=s.quick_draft, personal_data=materials.personal_data_reason,
            known_intent={"intent": "explain", "standalone": f"Explain {topic}", "topic": topic, "figure": True},
            library=materials.library_overview(app.state.db, collection),
        ):
            if room.topic != topic:          # the topic changed meanwhile: drop this explanation
                return
            if ev["type"] == "done":
                r = ev["result"]
                room.explanation = {k: r[k] for k in ("text", "sources", "used", "visual", "visual_fallback", "figure_note", "intent", "meta", "step_focus")}
                room.live_explain = None
                room.save(app)
                title = next((ln.lstrip("#").strip() for ln in r["text"].splitlines() if ln.strip()), topic)
                log_message(app, room.code, "explanation", "", title[:200])
                await broadcast(room, {"type": "explanation", "data": room.explanation})
            else:
                if room.live_explain is not None:
                    room.live_explain.append(ev)
                await broadcast(room, {"type": "tutor", "event": ev})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.live_explain = None
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def run_quiz(app, room: Room, lang: str = "en") -> None:
    try:
        level = ((room.explanation or {}).get("intent") or {}).get("level", "beginner")
        questions, _ = await pipeline.get_quiz(app.state.gateway, room.topic, level, lang)
        room.quiz, room.answers, room.results = questions, {}, None
        room.save(app)
        await broadcast(room, {"type": "quiz", "quiz": room.quiz_public()})
    except (AIError, ValueError) as exc:
        await broadcast_ai_error(room, exc)
    finally:
        room.busy = None
        await broadcast(room, {"type": "busy", "what": None})


async def run_summary(app, room: Room, lang: str = "en") -> None:
    try:
        transcript = transcript_text(app.state.db, room.code)
        if len(transcript) < 20:
            await broadcast(room, {"type": "summary", "summary": {"text": "", "at": 0}, "empty": True})
            return
        out = await pipeline.summarise_session(app.state.gateway, room.topic, transcript, lang)
        now = time.time()
        room.summary, room.summary_at = out["summary"], now
        app.state.db.execute("UPDATE rooms SET summary=?, summary_at=? WHERE code=?", (room.summary, now, room.code))
        await broadcast(room, {"type": "summary", "summary": {"text": room.summary, "at": now}})
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
        scoring.track(app.state.db, user_id, "group_quiz", {"room": room.code, "correct": correct, "total": len(room.quiz)})
        scoring.award(app.state.db, user_id, "group_quiz", units=correct, ref=room.code)
    room.results = {"questions": per_q, "scores": sorted(scores, key=lambda s: -s["correct"])}
    room.save(app)
    await broadcast(room, {"type": "quiz_results", "results": room.results})
