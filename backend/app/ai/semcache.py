"""Semantic cache: reuse a whole pipeline result when a new request means the same thing as a recent one.

The gateway already serves byte-identical calls from its own cache. This layer catches the *same question
asked in different words* — very common when many learners study the same topic ("explain Thales" vs
"Thales theorem") — by comparing short intent texts with the embeddings endpoint (its own, cheaper quota).
A hit skips all generation calls for that request; a miss costs one embedding and is then remembered.

In-process, like the rest of the single-process state. With several instances it would move to a shared
vector store; the call sites would not change.
"""
from __future__ import annotations

import math
import re
import time

from .gateway import AIError


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()[:200]


class SemanticCache:
    def __init__(self, gateway, settings) -> None:
        self.gw = gateway
        self.on = settings.semantic_cache
        self.ttl = settings.cache_ttl_s
        self.explain_threshold = settings.sem_threshold
        self.ask_threshold = settings.sem_threshold_ask
        self.max_per_ns = 128
        self.store: dict[str, list[tuple[float, list[float], dict]]] = {}
        self.seen: dict[str, dict[str, float]] = {}
        self.hits = 0
        self.misses = 0

    def _threshold(self, ns: str) -> float:
        return self.ask_threshold if ns.startswith("ask:") else self.explain_threshold

    async def fetch(self, ns: str, text: str):
        """Returns (hit_result, store_callback).
        - hit_result is a cached result to serve as-is, or None.
        - store_callback(result) remembers a freshly generated result, or None when there is nothing to do
          (feature off, exact repeat already handled by the gateway cache, or embeddings unavailable).
        """
        if not self.on:
            return None, None
        text = _norm(text)
        now = time.time()
        seen = self.seen.setdefault(ns, {})
        for k, ts in list(seen.items()):
            if now - ts > self.ttl:
                del seen[k]
        if text in seen:
            return None, None  # exact repeat: the gateway's own cache serves it; don't spend an embedding

        try:
            vec = (await self.gw.embed([text]))[0]
        except AIError:
            return None, None  # embeddings quota/outage: just generate, don't let the cache break the request

        entries = [e for e in self.store.get(ns, []) if now - e[0] < self.ttl]
        self.store[ns] = entries
        best, best_sim = None, -1.0
        for _ts, vector, result in entries:
            sim = cosine(vec, vector)
            if sim > best_sim:
                best_sim, best = sim, result
        if best is not None and best_sim >= self._threshold(ns):
            self.hits += 1
            self.gw.metrics.cache_hits += 1
            self.gw.metrics.semantic_hits += 1
            return best, None

        self.misses += 1

        def store(result: dict) -> None:
            seen[text] = time.time()
            bucket = self.store.setdefault(ns, [])
            bucket.append((time.time(), vec, result))
            if len(bucket) > self.max_per_ns:
                del bucket[: len(bucket) - self.max_per_ns]

        return None, store

    def clear(self) -> None:
        self.store.clear()
        self.seen.clear()
        self.hits = self.misses = 0
