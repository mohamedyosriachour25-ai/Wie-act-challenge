"""The AI gateway: the single place that talks to the inference service.

What it does (each point maps to a line of the challenge guide):
  * routes every step to the compact or the mid-size class, on purpose (orchestration)
  * caches answers and merges identical in-flight requests (no regeneration, cache indicator)
  * honours the error contract: quota exhausted -> wait the stated delay, 503 -> retry then fall
    back to the compact class, 400 -> fix the request, 200 -> read the quota headers
  * limits concurrent requests per key
  * never fails silently: exposes a live status the UI shows to the user
  * keeps the per-team metrics the organisers also measure
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

import httpx

from ..config import Settings


class AIError(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after
        self.status = status


@dataclass
class CallMeta:
    cls: str
    task: str = ""
    model: str = ""
    cached: bool = False
    coalesced: bool = False
    degraded: bool = False     # a mid-size step was answered by the compact class
    waited_ms: int = 0
    attempts: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class Metrics:
    """Mirrors what the organisers measure per team (guide section 8)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls = {"compact": 0, "mid": 0}
        self.tokens_in = {"compact": 0, "mid": 0}
        self.tokens_out = {"compact": 0, "mid": 0}
        self.cache_hits = 0
        self.coalesced = 0
        self.repeated_identical = 0   # identical request that still reached the service
        self.quota_refusals = 0
        self.quota_handled = 0
        self.saturation_503 = 0
        self.fallbacks = 0
        self.bad_requests = 0
        self.repairs = 0
        self.errors = 0
        self.embeddings = 0       # passages embedded
        self.embed_calls = 0      # calls to the separately metered embeddings endpoint

    def to_dict(self) -> dict:
        total = sum(self.calls.values())
        return {
            "calls": {**self.calls, "total": total},
            "compact_share": round(self.calls["compact"] / total, 2) if total else None,
            "tokens_in": dict(self.tokens_in),
            "tokens_out": dict(self.tokens_out),
            "cache_hits": self.cache_hits,
            "coalesced": self.coalesced,
            "repeated_identical": self.repeated_identical,
            "quota_refusals": self.quota_refusals,
            "quota_handled": self.quota_handled,
            "saturation_503": self.saturation_503,
            "fallbacks": self.fallbacks,
            "bad_requests": self.bad_requests,
            "repairs": self.repairs,
            "errors": self.errors,
            "embeddings": self.embeddings,
            "embed_calls": self.embed_calls,
        }


def extract_json(text: str):
    """Pull the first JSON object out of a model answer (models like to add fences or chatter)."""
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class Gateway:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        if settings.is_mock:
            base = "http://mock/mock/v1"
        else:
            base = settings.base_url.rstrip("/")
            if not base.endswith("/v1"):
                base += "/v1"
        self.client = httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=httpx.Timeout(180.0, connect=10.0),
            transport=transport,
        )
        self.sem = asyncio.Semaphore(settings.max_concurrent)
        self.metrics = Metrics()
        self.cache: dict[str, tuple[float, tuple[str, CallMeta]]] = {}
        self.inflight: dict[str, asyncio.Future] = {}
        self.seen: set[str] = set()
        self.cooldown_until = 0.0
        self.embed_cooldown_until = 0.0
        self.quota_remaining: float | None = None
        self._active = 0
        self._state = {"state": "idle", "message": "", "until": 0.0, "code": "", "cls": ""}
        self._notice: dict | None = None

    # ---------------------------------------------------------------- status
    def _set(self, state: str, message: str = "", until: float = 0.0, code: str = "", cls: str = "") -> None:
        # `message` is English (logs, tests); the interface builds its own text from `code` so it can be translated.
        self._state = {"state": state, "message": message, "until": until, "code": code, "cls": cls}

    def _note(self, kind: str, message: str) -> None:
        self._notice = {"kind": kind, "message": message, "at": time.time()}

    def status(self) -> dict:
        now = time.time()
        notice = self._notice if self._notice and now - self._notice["at"] < 20 else None
        until = self._state["until"]
        return {
            "state": self._state["state"],
            "message": self._state["message"],
            "code": self._state["code"],
            "cls": self._state["cls"],
            "retry_in": max(0, math.ceil(until - now)) if until else 0,
            "active": self._active,
            "notice": notice,
            "quota_remaining": self.quota_remaining,
            "mock": self.s.is_mock,
        }

    def reset_runtime(self) -> None:
        self.cache.clear()
        self.seen.clear()
        self.cooldown_until = 0.0
        self.embed_cooldown_until = 0.0
        self.metrics.reset()

    # ----------------------------------------------------------------- core
    @staticmethod
    def _key(cls: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        norm = [{"role": m["role"], "content": re.sub(r"\s+", " ", m["content"]).strip()} for m in messages]
        raw = json.dumps([cls, norm, max_tokens, temperature], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def chat(
        self,
        task: str,
        cls: str,
        messages: list[dict],
        max_tokens: int = 400,
        temperature: float = 0.2,
        cache: bool = True,
        allow_fallback: bool = True,
    ) -> tuple[str, CallMeta]:
        key = self._key(cls, messages, max_tokens, temperature)
        if cache:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < self.s.cache_ttl_s:
                self.metrics.cache_hits += 1
                text, meta = hit[1]
                return text, dataclasses.replace(meta, cached=True, waited_ms=0)
            pending = self.inflight.get(key)
            if pending is not None:
                self.metrics.coalesced += 1
                text, meta = await asyncio.shield(pending)
                return text, dataclasses.replace(meta, coalesced=True)

        future: asyncio.Future | None = None
        if cache:
            future = asyncio.get_running_loop().create_future()
            self.inflight[key] = future
        self._active += 1
        try:
            result = await self._execute(key, task, cls, messages, max_tokens, temperature, allow_fallback)
        except BaseException as exc:
            if future is not None:
                future.set_exception(exc)
                future.exception()  # mark as retrieved when nobody else is waiting
            raise
        else:
            if future is not None:
                future.set_result(result)
            # A degraded answer (mid-size step written by the compact class during saturation) is shown
            # once but never cached: otherwise it would keep being served after the large model recovers.
            if cache and not result[1].degraded:
                self.cache[key] = (time.time(), result)
                if len(self.cache) > 600:
                    for old in sorted(self.cache, key=lambda k: self.cache[k][0])[:100]:
                        self.cache.pop(old, None)
            return result
        finally:
            self._active -= 1
            if cache:
                self.inflight.pop(key, None)
            if self._active == 0:
                self._set("idle")

    async def _execute(self, key, task, cls, messages, max_tokens, temperature, allow_fallback):
        eff = cls
        degraded = False
        attempts = 0
        waited = 0.0
        n503 = 0
        net_fail = 0
        halvings = 0
        mt = max_tokens
        if key in self.seen:
            self.metrics.repeated_identical += 1
        self.seen.add(key)

        while True:
            attempts += 1
            model = self.s.compact_model if eff == "compact" else self.s.mid_model

            cooldown = self.cooldown_until - time.time()
            if cooldown > 0:
                if cooldown > self.s.max_quota_wait_s:
                    raise AIError("quota", "The AI quota is used up for now.", retry_after=cooldown, status=429)
                self._set("queued", f"The AI quota is refilling. Your request resumes in {math.ceil(cooldown)} s.", self.cooldown_until, code="quota")
                await asyncio.sleep(cooldown)
                waited += cooldown

            self._set("working", "")
            try:
                async with self.sem:
                    resp = await self.client.post(
                        "/chat/completions",
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": mt,
                            "temperature": temperature,
                            "stream": False,
                        },
                    )
            except httpx.TransportError:
                net_fail += 1
                if net_fail > 3:
                    self.metrics.errors += 1
                    raise AIError("network", "The AI service cannot be reached right now. Check the connection and try again.")
                await asyncio.sleep(0.5 * net_fail)
                continue

            self._read_quota(resp.headers)
            code = resp.status_code

            if code == 200:
                data = resp.json()
                text = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage", {})
                self.metrics.calls[eff] += 1
                self.metrics.tokens_in[eff] += int(usage.get("prompt_tokens", 0))
                self.metrics.tokens_out[eff] += int(usage.get("completion_tokens", 0))
                return text, CallMeta(cls=eff, task=task, model=model, degraded=degraded, waited_ms=int(waited * 1000), attempts=attempts)

            if code == 429:  # team quota exhausted: wait the stated delay, then retry
                delay = self._retry_after(resp, 2.0)
                self.metrics.quota_refusals += 1
                self.cooldown_until = time.time() + delay
                if delay > self.s.max_quota_wait_s or attempts >= 6:
                    self.metrics.errors += 1
                    raise AIError("quota", "The AI quota is used up for now.", retry_after=delay, status=429)
                self.metrics.quota_handled += 1
                continue

            if code == 503:  # model class saturated: retry once, then fall back to compact
                self.metrics.saturation_503 += 1
                n503 += 1
                delay = min(self._retry_after(resp, 1.0), 4.0)
                if eff == "mid" and allow_fallback and n503 >= 2:
                    eff, degraded = "compact", True
                    self.metrics.fallbacks += 1
                    self._note("fallback", "The larger model is busy, so a compact model answered. The answer may be shorter.")
                    continue
                if n503 >= 5:
                    self.metrics.errors += 1
                    raise AIError("unavailable", "The AI service is saturated. Try again in a moment.", retry_after=delay, status=503)
                self._set("queued", f"The {eff} model is busy. Retrying in {math.ceil(delay)} s.", time.time() + delay, code="busy", cls=eff)
                await asyncio.sleep(delay)
                waited += delay
                continue

            if code == 400:  # request beyond a ceiling: fix the request
                self.metrics.bad_requests += 1
                detail = resp.text
                if halvings < 4 and mt > 64 and re.search(r"ceiling|max_tokens|too long|exceed|length", detail, re.I):
                    mt //= 2
                    halvings += 1
                    continue
                self.metrics.errors += 1
                raise AIError("bad_request", f"The AI service refused the request: {detail[:160]}", status=400)

            self.metrics.errors += 1
            raise AIError("upstream", f"The AI service answered with an unexpected error ({code}).", status=code)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _retry_after(resp: httpx.Response, default: float) -> float:
        header = resp.headers.get("retry-after")
        if header:
            try:
                return max(0.2, float(header))
            except ValueError:
                pass
        try:
            body = resp.json()
            value = body.get("retry_after") or body.get("error", {}).get("retry_after")
            if value:
                return max(0.2, float(value))
        except Exception:
            pass
        return default

    def _read_quota(self, headers: httpx.Headers) -> None:
        # Header names are not specified in the guide: read any "remaining" counter generically.
        for name, value in headers.items():
            if "remaining" in name.lower():
                try:
                    self.quota_remaining = float(value)
                except ValueError:
                    pass

    async def chat_json(self, task: str, cls: str, messages: list[dict], validate, max_tokens: int = 500, **kw):
        """Ask for JSON, validate it, repair once with the compact class, else return None."""
        text, meta = await self.chat(task, cls, messages, max_tokens, **kw)
        for candidate in (text, None):
            if candidate is None:
                self.metrics.repairs += 1
                repair = [
                    {"role": "system", "content": "TASK: repair\nFix this JSON so it is valid and complete. Reply with JSON only."},
                    {"role": "user", "content": text[:2500]},
                ]
                candidate, _ = await self.chat("repair", "compact", repair, max_tokens=min(max_tokens, 700))
            obj = extract_json(candidate)
            if obj is not None:
                try:
                    return validate(obj), meta
                except (ValueError, KeyError, TypeError):
                    pass
        return None, meta

    async def embed(self, texts: list[str], batch: int = 32) -> list[list[float]]:
        """Embeddings have their own quota. Cached per text, sent in batches, and a batch the service
        refuses as too large is halved and retried."""
        out: dict[str, list[float]] = {}
        todo: list[str] = []
        for t in dict.fromkeys(texts):
            hit = self.cache.get("emb:" + t)
            if hit:
                out[t] = hit[1]  # type: ignore[assignment]
            else:
                todo.append(t)
        i, size = 0, max(1, batch)
        while i < len(todo):
            part = todo[i : i + size]
            try:
                vectors = await self._embed_batch(part)
            except AIError as exc:
                if exc.kind == "bad_request" and len(part) > 1 and re.search(r"ceiling|exceed|length|too", exc.message, re.I):
                    self.metrics.bad_requests += 1
                    size = max(1, len(part) // 2)
                    continue
                raise
            for t, vec in zip(part, vectors):
                self.cache["emb:" + t] = (time.time(), vec)  # type: ignore[assignment]
                out[t] = vec
            self.metrics.embeddings += len(part)
            i += len(part)
        return [out[t] for t in texts]

    async def _embed_batch(self, part: list[str]) -> list[list[float]]:
        attempts = n503 = net_fail = 0
        while True:
            attempts += 1
            cooldown = self.embed_cooldown_until - time.time()
            if cooldown > 0:
                if cooldown > self.s.max_quota_wait_s:
                    raise AIError("quota", "The embeddings quota is used up for now.", retry_after=cooldown, status=429)
                self._set("queued", f"The embeddings quota is refilling. Resuming in {math.ceil(cooldown)} s.", self.embed_cooldown_until, code="embed_quota")
                await asyncio.sleep(cooldown)
            self._set("working", "")
            try:
                async with self.sem:
                    resp = await self.client.post("/embeddings", json={"model": self.s.embed_model, "input": part})
            except httpx.TransportError:
                net_fail += 1
                if net_fail > 3:
                    self.metrics.errors += 1
                    raise AIError("network", "The AI service cannot be reached right now. Check the connection and try again.")
                await asyncio.sleep(0.5 * net_fail)
                continue
            finally:
                if self._active == 0:
                    self._set("idle")
            code = resp.status_code
            if code == 200:
                self.metrics.embed_calls += 1
                data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
                return [d["embedding"] for d in data]
            if code == 429:
                delay = self._retry_after(resp, 2.0)
                self.metrics.quota_refusals += 1
                self.embed_cooldown_until = time.time() + delay
                if delay > self.s.max_quota_wait_s or attempts >= 6:
                    self.metrics.errors += 1
                    raise AIError("quota", "The embeddings quota is used up for now.", retry_after=delay, status=429)
                self.metrics.quota_handled += 1
                continue
            if code == 503:
                self.metrics.saturation_503 += 1
                n503 += 1
                if n503 >= 5:
                    self.metrics.errors += 1
                    raise AIError("unavailable", "The AI service is saturated. Try again in a moment.", status=503)
                await asyncio.sleep(min(self._retry_after(resp, 1.0), 4.0))
                continue
            if code == 400:
                raise AIError("bad_request", f"The AI service refused the request: {resp.text[:160]}", status=400)
            self.metrics.errors += 1
            raise AIError("upstream", f"Embeddings failed ({code}).", status=code)

    async def aclose(self) -> None:
        await self.client.aclose()
