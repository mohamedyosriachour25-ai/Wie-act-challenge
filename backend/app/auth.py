"""Pseudonymous accounts: the user picks a pseudo and a password. No e-mail, no real name."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time

from . import security
from .i18n import lang_of, tr
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])
PSEUDO_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")
TOKEN_TTL = 7 * 24 * 3600


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def make_token(secret: str, user_id: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"uid": user_id, "exp": time.time() + TOKEN_TTL}).encode()).decode()
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def user_from_token(app, token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(app.state.settings.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        if data["exp"] < time.time():
            return None
        return app.state.db.one("SELECT id, pseudo, learning_style FROM users WHERE id=?", (data["uid"],))
    except Exception:
        return None


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    user = user_from_token(request.app, token) if token else None
    if not user:
        raise HTTPException(401, tr(lang_of(request), "Sign in to continue."))
    return user


class Credentials(BaseModel):
    pseudo: str
    password: str


def _session(request: Request, user: dict) -> dict:
    return {
        "token": make_token(request.app.state.settings.secret, user["id"]),
        "user": {"pseudo": user["pseudo"], "learning_style": user.get("learning_style")},
    }


@router.post("/register")
def register(body: Credentials, request: Request):
    pseudo = body.pseudo.strip()
    security.check_register_allowed(request)
    if not PSEUDO_RE.match(pseudo):
        raise HTTPException(400, tr(lang_of(request), "Pick a pseudo of 3 to 20 characters: letters, digits, _ or -. Do not use your real name."))
    if len(body.password) < 6:
        raise HTTPException(400, tr(lang_of(request), "Use a password of at least 6 characters."))
    db = request.app.state.db
    if db.one("SELECT id FROM users WHERE pseudo=?", (pseudo,)):
        raise HTTPException(409, tr(lang_of(request), "That pseudo is taken. Try another one."))
    salt = os.urandom(16)
    cur = db.execute(
        "INSERT INTO users (pseudo, pw_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (pseudo, _hash(body.password, salt), salt.hex(), time.time()),
    )
    return _session(request, {"id": cur.lastrowid, "pseudo": pseudo, "learning_style": None})


@router.post("/login")
def login(body: Credentials, request: Request):
    db = request.app.state.db
    pseudo = body.pseudo.strip()
    security.check_login_allowed(request, pseudo)
    row = db.one("SELECT * FROM users WHERE pseudo=?", (pseudo,))
    # Hash even for unknown pseudos so the response time does not reveal which pseudos exist.
    salt = bytes.fromhex(row["salt"]) if row else b"\0" * 16
    candidate = _hash(body.password, salt)
    if not row or not hmac.compare_digest(row["pw_hash"], candidate):
        security.record_login_failure(request, pseudo)
        raise HTTPException(401, tr(lang_of(request), "Pseudo or password is wrong."))
    security.clear_login_failures(request, pseudo)
    return _session(request, row)


@router.get("/me")
def me(request: Request):
    user = current_user(request)
    return {"pseudo": user["pseudo"], "learning_style": user["learning_style"]}
