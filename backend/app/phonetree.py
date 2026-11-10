"""Pocket tree: a lightweight, independent anti-distraction tree reachable by scanning a QR code or
opening a link on a phone — separate from the study-forest tab tracking in rooms.py (Room.update_trees).

Growth is tracked purely by whether the phone's tree-space page is open and visible: time visible grows
the tree, and the tab being hidden or closed for more than PHONE_TREE_GRACE_S seconds withers it. That
state lives entirely on this module's own registry, independent of the owner's desktop focus/forest
state — but two things link the two back together:
  - the moment a pocket tree withers, a private "stay focused" reminder is sent to the owner's desktop
    session in the room (if they are connected), over the room's own WebSocket;
  - the finished tree (grown or wilted) is logged into the room's shared forest (Room.forest /
    Room._tree_events), the same log the desktop-tracked trees use, so the reward is visible to the
    room and reuses the existing forest UI with no changes to it.

Access is anonymous: the link/QR carries a signed token (HMAC, the same mechanism as login session
tokens in auth.py) naming the owning room and user, so the tree-space page needs no login of its own —
just the link. The token is minted only for a signed-in member of that live room (see the REST route).

Every live room carries its own forest: the room is told about each member's pocket tree (state, growth,
whether the phone tab is in front) via `phone_trees` broadcasts and the live snapshot, so the room page
can show the forest growing. The tree follows its room's shared clock: during a break the phone is free,
so the tree neither grows nor withers, and the grace period restarts when focus resumes. The phone page
gets the room name and the seconds left in the current phase so it can say when the next break comes.

This is a best-effort, single-process, in-memory feature like the rest of the room state in rooms.py.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import rooms, scoring
from .i18n import lang_of, tr

router = APIRouter(tags=["phonetree"])

PHONE_TREE_GRACE_S = 60      # seconds the tree survives with the tab hidden/closed before it wilts
PHONE_TREE_FULL_S = 180      # seconds visible for the tree to look fully grown (matches rooms.TREE_FULL_S)
PHONE_TREE_TICK_S = 4        # how often the tree's growth/wither is advanced and pushed to the phone
PHONE_TREE_TTL = 12 * 3600   # a tree-space link is good for 12h after being generated


def make_tree_token(secret: str, room_code: str, user_id: int, pseudo: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({
        "room": room_code, "uid": user_id, "pseudo": pseudo, "exp": time.time() + PHONE_TREE_TTL,
    }).encode()).decode()
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_tree_token(secret: str, token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        if data["exp"] < time.time():
            return None
        return data
    except Exception:
        return None


@dataclass
class PhoneTree:
    """One independent, ongoing pocket-tree session for one (room, owner) pair."""
    room_code: str
    owner_id: int
    pseudo: str
    started_at: float
    last: float                       # epoch time up to which growth/away has been counted
    grown: float = 0.0                # seconds spent visible (the reward metric)
    visible: bool = True
    hidden_since: float | None = None  # None while visible; set the moment it goes hidden/closes
    state: str = "growing"            # growing | grown | wilted (same vocabulary as rooms.Tree)
    ws: WebSocket | None = None
    task: "asyncio.Task | None" = None


_trees: dict[tuple[str, int], PhoneTree] = {}  # (room_code, owner_id) -> its current pocket tree


def _phase(room: "rooms.Room | None", now: float) -> dict | None:
    """The room's current phase and the whole seconds left in it, from the live room's shared clock."""
    if room is None:
        return None
    cyc = rooms.live_cycle(room, now)
    if cyc is None:
        return None
    length = room.timer["focus_s"] if cyc["phase"] == "focus" else room.timer["break_s"]
    return {"phase": cyc["phase"], "remaining": max(0, int(-(-(length - cyc["into"]) // 1)))}


def _in_break(room: "rooms.Room | None", now: float) -> bool:
    """True while the room is on its break: the phone is free then, so the tree is paused (patched in tests)."""
    ph = _phase(room, now)
    return bool(ph and ph["phase"] == "break")


def _view(tree: PhoneTree, room: "rooms.Room | None" = None) -> dict:
    now = time.time()
    away_for = max(0.0, now - tree.hidden_since) if tree.hidden_since is not None else 0.0
    view = {
        "type": "phone_tree",
        "state": tree.state,
        "p": round(min(1.0, tree.grown / PHONE_TREE_FULL_S), 3),
        "grown": round(tree.grown),
        "visible": tree.visible,
        "away_for": round(away_for),
        "grace_s": PHONE_TREE_GRACE_S,
    }
    if room is not None:
        view["room"] = room.name
        ph = _phase(room, now)
        if ph:
            view.update(ph)
    return view


def room_pocket_trees(room_code: str) -> list[dict]:
    """Every member's current pocket tree in one room — the room's forest as shown on the room page."""
    out = [{"pseudo": t.pseudo, "state": t.state, "p": round(min(1.0, t.grown / PHONE_TREE_FULL_S), 3), "visible": t.visible}
           for (code, _), t in _trees.items() if code == room_code]
    return sorted(out, key=lambda x: x["pseudo"].lower())


async def _announce(room: "rooms.Room") -> None:
    await rooms.broadcast(room, {"type": "phone_trees", "trees": room_pocket_trees(room.code)})


def _set_visible(tree: PhoneTree, visible: bool, now: float) -> None:
    tree.visible = visible
    tree.hidden_since = None if visible else (tree.hidden_since or now)


def _advance(tree: PhoneTree, now: float, paused: bool = False) -> str | None:
    """Move the tree's clock to `now`. Returns "grown" or "wilted" the instant that transition happens,
    else None. Safe to call repeatedly, including with a `now` no later than the last call.
    `paused` (the room is on its break): nothing grows or withers, and a hidden tab's grace restarts
    from now, so leaving the phone during a break never counts against the tree."""
    dt = max(0.0, now - tree.last)
    tree.last = now
    if tree.state == "wilted":
        return None
    if paused:
        if tree.hidden_since is not None:
            tree.hidden_since = now
        return None
    if tree.visible:
        if tree.state == "growing":
            tree.grown += dt
            if tree.grown >= PHONE_TREE_FULL_S:
                tree.state = "grown"
                return "grown"
        return None
    if tree.hidden_since is not None and now - tree.hidden_since >= PHONE_TREE_GRACE_S:
        tree.state = "wilted"
        return "wilted"
    return None


def _log_forest(room: rooms.Room, tree: PhoneTree, state: str, now: float) -> None:
    """Feed a finished (or just-grown) pocket tree into the room's shared forest log, the same list and
    broadcast the desktop-tracked trees use, so it shows up in the forest for everyone without any
    changes to rooms.py or the Forest page."""
    entry = {"pseudo": tree.pseudo, "cycle": "phone", "state": state, "at": now, "source": "phone", "grown": round(tree.grown)}
    room.forest.append(entry)
    del room.forest[:-rooms.FOREST_LOG]
    room._tree_events.append(entry)


async def _push(tree: PhoneTree, room: "rooms.Room | None" = None) -> None:
    if tree.ws is None:
        return
    try:
        await tree.ws.send_json(_view(tree, room))
    except Exception:
        pass


async def _watch(room: rooms.Room, tree: PhoneTree, app=None) -> None:
    """Advance one pocket tree every PHONE_TREE_TICK_S for as long as it is alive. Ends once it wilts;
    a new WebSocket connection for the same (room, owner) then starts a fresh tree and a fresh task."""
    ticks = 0
    try:
        while True:
            await asyncio.sleep(PHONE_TREE_TICK_S)
            now = time.time()
            event = _advance(tree, now, paused=_in_break(room, now))
            ticks += 1
            if event:
                _log_forest(room, tree, event, now)
                if app is not None:
                    scoring.track(app.state.db, tree.owner_id, "pocket_tree", {"room": room.code, "state": event})
                    if event == "grown":
                        scoring.award(app.state.db, tree.owner_id, "pocket_tree", ref=room.code)
                await rooms.announce_trees(room)
            if event == "wilted":
                owner = room.members.get(tree.owner_id)
                if owner is not None:
                    await rooms.send(owner, {"type": "phone_tree_alert", "pseudo": tree.pseudo, "grown": round(tree.grown)})
                await _push(tree, room)
                await _announce(room)
                return
            await _push(tree, room)
            if event or ticks % 4 == 0:   # the room's forest view: on every change, else every few ticks
                await _announce(room)
    except asyncio.CancelledError:
        pass


@router.get("/api/rooms/{code}/phone-tree")
def phone_tree_link(code: str, request: Request):
    """Mint a signed, anonymous link to a fresh pocket-tree space for the signed-in caller, for a live
    room they can reach by its code. The page behind the link needs no login of its own."""
    from .auth import current_user

    user = current_user(request)
    code = code.upper()
    row = request.app.state.db.one("SELECT kind FROM rooms WHERE code=?", (code,))
    if not row or row["kind"] != "live":
        raise HTTPException(404, tr(lang_of(request), "No room has that code."))
    token = make_tree_token(request.app.state.settings.secret, code, user["id"], user["pseudo"])
    return {"token": token}


@router.websocket("/ws/phone-tree/{token}")
async def phone_tree_socket(ws: WebSocket, token: str):
    await ws.accept()
    app = ws.app
    lang = lang_of(ws)
    data = read_tree_token(app.state.settings.secret, token)
    if not data:
        await ws.send_json({"type": "error", "message": tr(lang, "This pocket-tree link is invalid or has expired.")})
        return await ws.close(4401)
    room = app.state.hub.rooms.get(data["room"])
    if room is None:
        await ws.send_json({"type": "error", "message": tr(lang, "No room has that code.")})
        return await ws.close(4404)

    key = (data["room"], int(data["uid"]))
    now = time.time()
    tree = _trees.get(key)
    if tree is None or tree.state == "wilted":
        tree = _trees[key] = PhoneTree(room_code=data["room"], owner_id=int(data["uid"]), pseudo=data["pseudo"],
                                        started_at=now, last=now)
        tree.task = asyncio.create_task(_watch(room, tree, app))
    tree.ws = ws
    _set_visible(tree, True, now)
    await ws.send_json(_view(tree, room))
    await _announce(room)

    try:
        while True:
            try:
                msg = await ws.receive_json()
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(msg, dict) and msg.get("type") == "visible":
                visible = bool(msg.get("visible"))
                changed = visible != tree.visible
                _set_visible(tree, visible, time.time())
                await _push(tree, room)          # the phone gets the time left until the break right away
                if changed:
                    await _announce(room)
    except WebSocketDisconnect:
        pass
    finally:
        if tree.ws is ws:
            tree.ws = None
            _set_visible(tree, False, time.time())
            try:
                await _announce(room)
            except Exception:
                pass
