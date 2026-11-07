"""A fake EdNova inference service that follows the contract in the Integration & API Guide:
OpenAI-compatible, Bearer auth, no streaming, token-bucket quota, 429 with a delay when the team quota
is exhausted, 503 + Retry-After when a model class is saturated, 400 when a ceiling is exceeded,
200 + quota headers in normal operation, separately metered embeddings.

Use it to develop and test resilience before the real key arrives. The status code and header names for
"quota exhausted" are not specified in the guide: 429 + Retry-After is an assumption to confirm.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from . import mock_llm

MAX_TOKENS_CEILING = 1024
MAX_EMBED_INPUTS = 48
EMBED_DIM = 256


class MockState:
    def __init__(self, scale: float = 1.0):
        self.scale = scale
        self.capacity = 40.0          # burst reserve
        self.refill = 2.0             # sustained rate, tokens per second
        self.tokens = self.capacity
        self.stamp = time.monotonic()
        self.blocked_until = 0.0      # simulated quota exhaustion (monotonic clock)
        self.embed_tokens = 40.0      # embeddings are metered separately from generation
        self.embed_stamp = time.monotonic()
        self.embed_calls = 0
        self.mid_saturated_until = 0.0
        self.calls = 0

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.refill)
        self.stamp = now

    def take(self) -> float:
        """Return 0 if a unit was consumed, else the seconds to wait."""
        self._refill()
        now = time.monotonic()
        if now < self.blocked_until:
            return self.blocked_until - now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.refill

    def take_embed(self) -> float:
        now = time.monotonic()
        self.embed_tokens = min(40.0, self.embed_tokens + (now - self.embed_stamp) * 2.0)
        self.embed_stamp = now
        if now < self.blocked_until:
            return self.blocked_until - now
        if self.embed_tokens >= 1:
            self.embed_tokens -= 1
            return 0.0
        return (1 - self.embed_tokens) / 2.0

    def remaining(self) -> int:
        self._refill()
        return int(self.tokens)

    def simulate(self, mode: str, seconds: float = 8.0) -> None:
        now = time.monotonic()
        if mode == "quota":
            self.blocked_until = now + seconds
        elif mode == "saturate":
            self.mid_saturated_until = now + seconds + 4
        elif mode == "clear":
            self.blocked_until = 0.0
            self.mid_saturated_until = 0.0
            self.tokens = self.capacity
            self.embed_tokens = 40.0


STOPWORDS = frozenset(
    "the and for are was were with that this from into what where when which who whom how does did why can its "
    "has have had not but all any one you your their they them then than also about over under between each "
    "les des une que qui dans pour est sont avec sur par pas plus aux ces ses".split()
)


def lexical_embedding(text: str) -> list[float]:
    """Signed feature hashing of content words: texts sharing words are close. Stands in for a real embedding model."""
    vec = [0.0] * EMBED_DIM
    for word in re.findall(r"[a-zà-ÿ0-9]{3,}", text.lower()):
        if word in STOPWORDS:
            continue
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def build_mock(settings: Settings) -> tuple[APIRouter, MockState]:
    router = APIRouter(prefix="/mock/v1")
    state = MockState(settings.mock_latency_scale)

    def models() -> dict[str, str]:
        return {settings.compact_model: "compact", settings.mid_model: "mid"}

    def unauthorized(request: Request) -> JSONResponse | None:
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            return JSONResponse({"error": {"message": "Missing bearer token."}}, status_code=401)
        return None

    @router.get("/models")
    async def list_models(request: Request):
        if (bad := unauthorized(request)) is not None:
            return bad
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": c} for m, c in models().items()]}

    @router.post("/chat/completions")
    async def chat(request: Request):
        if (bad := unauthorized(request)) is not None:
            return bad
        body = await request.json()
        cls = models().get(body.get("model", ""))
        if cls is None:
            return JSONResponse({"error": {"message": "Unknown model."}}, status_code=400)
        if body.get("stream"):
            return JSONResponse({"error": {"message": "Streaming is not supported: responses are returned whole."}}, status_code=400)
        max_tokens = int(body.get("max_tokens") or 256)
        if max_tokens > MAX_TOKENS_CEILING:
            return JSONResponse(
                {"error": {"message": f"max_tokens exceeds the ceiling of {MAX_TOKENS_CEILING}."}}, status_code=400
            )
        now = time.monotonic()
        if cls == "mid" and now < state.mid_saturated_until:
            return JSONResponse(
                {"error": {"message": "Mid-size class saturated."}}, status_code=503, headers={"Retry-After": "2"}
            )
        wait = state.take()
        if wait > 0:
            secs = max(1, math.ceil(wait))
            return JSONResponse(
                {"error": {"type": "quota_exhausted", "message": "Team quota exhausted.", "retry_after": secs}},
                status_code=429,
                headers={"Retry-After": str(secs), "X-Quota-Remaining": "0"},
            )
        state.calls += 1
        await asyncio.sleep((1.8 if cls == "mid" else 0.45) * state.scale)
        text = mock_llm.complete(body["messages"])
        prompt_tokens = sum(len(m["content"]) for m in body["messages"]) // 4
        return JSONResponse(
            {
                "id": f"mock-{state.calls}",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(text) // 4},
            },
            headers={"X-Quota-Remaining": str(state.remaining())},
        )

    @router.post("/embeddings")
    async def embeddings(request: Request):
        if (bad := unauthorized(request)) is not None:
            return bad
        body = await request.json()
        inputs = body.get("input") or []
        inputs = [inputs] if isinstance(inputs, str) else inputs
        if len(inputs) > MAX_EMBED_INPUTS:
            return JSONResponse({"error": {"message": f"input exceeds the ceiling of {MAX_EMBED_INPUTS} items."}}, status_code=400)
        wait = state.take_embed()
        if wait > 0:
            secs = max(1, math.ceil(wait))
            return JSONResponse(
                {"error": {"type": "quota_exhausted", "message": "Embeddings quota exhausted.", "retry_after": secs}},
                status_code=429,
                headers={"Retry-After": str(secs)},
            )
        state.embed_calls += 1
        await asyncio.sleep(0.15 * state.scale)
        data = [{"index": i, "object": "embedding", "embedding": lexical_embedding(text)} for i, text in enumerate(inputs)]
        return {"object": "list", "data": data, "model": body.get("model", "")}

    return router, state
