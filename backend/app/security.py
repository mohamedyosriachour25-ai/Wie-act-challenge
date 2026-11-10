"""Operational safeguards: per-user limits on AI actions, login throttling, request logging with
secret redaction, security headers and a hard cap on request bodies.

Everything here is in-process (one server process). With several instances the limiters would move to a
shared store such as Redis; the call sites would not change."""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .i18n import lang_of, tr

log = logging.getLogger("herwiego")


class SlidingWindow:
    """At most `limit` hits per `window` seconds for each key."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float, now: float | None = None) -> float:
        """Record a hit. Returns 0 when allowed, otherwise the seconds to wait."""
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return max(0.1, q[0] + window - now)
        q.append(now)
        if len(self._hits) > 20000:  # keep memory bounded: drop idle keys
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - window]:
                self._hits.pop(k, None)
        return 0.0

    def peek(self, key: str, limit: int, window: float, now: float | None = None) -> float:
        """Like hit() but records nothing."""
        now = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if not q:
            return 0.0
        live = [t for t in q if t > now - window]
        return max(0.1, live[0] + window - now) if len(live) >= limit else 0.0

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)


def client_ip(request) -> str:
    """Peer address. X-Forwarded-For is only trusted when HERWIEGO_TRUST_PROXY=1 (behind your own proxy)."""
    s = request.app.state.settings
    if s.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ai_budget(app, user_id: int) -> float:
    """Spend one AI action for this user. Returns 0 when allowed, else seconds to wait.
    The team key is shared by every learner, so one account must not be able to drain it."""
    s = app.state.settings
    return app.state.limiter.hit(f"ai:{user_id}", s.user_ai_per_min, 60.0)


def rate_limit_message(lang: str, wait: float) -> str:
    return tr(lang, "You are going a little fast. Try again in {n} s.", n=int(wait) + 1)


def spend_ai(request: Request, user_id: int) -> None:
    wait = ai_budget(request.app, user_id)
    if wait:
        log.info("ai rate limit user=%s wait=%.1fs", user_id, wait)
        raise HTTPException(429, rate_limit_message(lang_of(request), wait), headers={"Retry-After": str(int(wait) + 1)})


def _login_keys(request, pseudo: str, s) -> list[tuple[str, int]]:
    return [(f"login:ip:{client_ip(request)}", s.login_attempts * 5), (f"login:ps:{pseudo.lower()}", s.login_attempts)]


def check_login_allowed(request: Request, pseudo: str) -> None:
    s = request.app.state.settings
    lim = request.app.state.limiter
    # A school or café shares one address, so the per-connection ceiling is 5x the per-pseudo one.
    for key, limit in _login_keys(request, pseudo, s):
        wait = lim.peek(key, limit, 300.0)
        if wait:
            raise HTTPException(429, tr(lang_of(request), "Too many sign-in attempts. Try again in {n} s.", n=int(wait) + 1), headers={"Retry-After": str(int(wait) + 1)})


def record_login_failure(request: Request, pseudo: str) -> None:
    s = request.app.state.settings
    lim = request.app.state.limiter
    for key, limit in _login_keys(request, pseudo, s):
        lim.hit(key, limit, 300.0)


def clear_login_failures(request: Request, pseudo: str) -> None:
    request.app.state.limiter.clear(f"login:ps:{pseudo.lower()}")


def check_register_allowed(request: Request) -> None:
    s = request.app.state.settings
    wait = request.app.state.limiter.hit(f"register:{client_ip(request)}", s.register_per_hour, 3600.0)
    if wait:
        raise HTTPException(429, tr(lang_of(request), "Too many accounts created from this connection. Try again later."), headers={"Retry-After": str(int(wait) + 1)})


async def read_capped(request: Request, limit: int) -> bytes | None:
    """Read the request body but stop as soon as it exceeds `limit` bytes (also for chunked uploads)."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            return None
    return bytes(data)


# ---------------------------------------------------------------- logging
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}")
_TOKEN_QS = re.compile(r"(?i)([?&](?:token|key|api_key)=)[^&\s]+")


class RedactFilter(logging.Filter):
    """Credentials must never reach logs: team key, signing secret, bearer tokens, ?token= in URLs."""

    def __init__(self, *secrets: str) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "[redacted]")
        return _TOKEN_QS.sub(r"\1[redacted]", _BEARER.sub(r"\1[redacted]", text))

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            original = record.getMessage()
        except Exception:
            return True  # a malformed record: leave it for the formatter to handle
        redacted = self.redact(original)
        if redacted != original:  # only rewrite lines that actually carry a secret, so %-args stay intact
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger("herwiego")
    root.setLevel(level)
    root.propagate = False
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(h)
    for h in root.handlers:
        for f in [f for f in h.filters if isinstance(f, RedactFilter)]:
            h.removeFilter(f)
        h.addFilter(RedactFilter(settings.api_key, settings.secret))
    # uvicorn prints WebSocket URLs, whose query string carries the session token ("... [accepted]" goes to uvicorn.error)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for f in [f for f in lg.filters if isinstance(f, RedactFilter)]:
            lg.removeFilter(f)
        lg.addFilter(RedactFilter(settings.api_key, settings.secret))


# ---------------------------------------------------------------- middleware
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class Guard(BaseHTTPMiddleware):
    """Request id, access log without query strings, body size cap, security headers."""

    async def dispatch(self, request: Request, call_next):
        s = request.app.state.settings
        rid = uuid.uuid4().hex[:10]
        cap = s.max_pdf_bytes + 65536 if request.url.path == "/api/materials/file" else s.max_body_bytes
        if int(request.headers.get("content-length") or 0) > cap:
            return JSONResponse({"detail": tr(lang_of(request), "That request is too large.")}, status_code=413)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error rid=%s %s %s", rid, request.method, request.url.path)
            return JSONResponse({"detail": tr(lang_of(request), "Something went wrong on our side. Try again in a moment.")}, status_code=500, headers={"X-Request-ID": rid})
        ms = (time.perf_counter() - started) * 1000
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            log.info("rid=%s %s %s -> %s %.0fms", rid, request.method, request.url.path, response.status_code, ms)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"   # microphone: opt-in dictation (Lot 15)
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
