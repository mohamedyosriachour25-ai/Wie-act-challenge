"""A fake OpenAI-compatible inference service, for the test suite only.

This is NOT part of the app: nothing under backend/app imports this module, and the running
app has no fallback and no "mock mode" at all -- HERWIEGO_BASE_URL and HERWIEGO_API_KEY must point at
a real service or the app refuses to start (see app/config.py). This file exists purely so the
test suite can verify the gateway's error-handling contract (quota/saturation/ceiling, retries,
fallback, caching) and the study pipeline's wiring, without spending a real, billed API key on
every test run. It speaks the same contract described in the integration guide: OpenAI-compatible,
Bearer auth, no streaming, token-bucket quota, 429 + Retry-After on quota exhaustion, 503 + Retry-After
on saturation, 400 past a ceiling, 200 + quota headers normally, separately metered embeddings.

The text content below is synthetic test fixture data: generic, clearly non-topic-specific, and
never served to a real user.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time

import httpx

MAX_TOKENS_CEILING = 1024
MAX_EMBED_INPUTS = 48
EMBED_DIM = 256


class FakeState:
    """Simulated quota/saturation state, equivalent to the old built-in demo service's MockState."""

    def __init__(self) -> None:
        self.capacity = 40.0
        self.refill = 2.0
        self.tokens = self.capacity
        self.stamp = time.monotonic()
        self.blocked_until = 0.0
        self.embed_tokens = 40.0
        self.embed_stamp = time.monotonic()
        self.embed_calls = 0
        self.mid_saturated_until = 0.0
        self.calls = 0

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.refill)
        self.stamp = now

    def take(self) -> float:
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


# ------------------------------------------------------------------- fixture "model" content
def _topic(text: str) -> str:
    m = re.search(r"TOPIC:\s*(.+)", text)
    return (m.group(1).strip() if m else "this topic")[:120]


def kind_for(topic: str) -> str:
    """Which illustration template a topic should route to -- enough variety for the tests to
    exercise every template, including the two added for freer-form, topic-appropriate figures."""
    t = topic.lower()
    if re.search(r"thal[eè]s|triangle|similar|proportion", t):
        return "thales"
    if re.search(r"quadratic|parabola|function|linear|sine|sinus|exponential|equation|graph", t):
        return "function_plot"
    if re.search(r"photosynth|mitosis|algorithm", t):
        return "process_flow"
    if re.search(r"revolution|war|history|century|decade|era|timeline|founded|empire", t):
        return "timeline"
    if re.search(r"\bvs\b|versus|compare|comparison|difference between", t):
        return "comparison"
    return "concept_map"


def _visual_for(kind: str, topic: str) -> dict:
    if kind == "thales":
        return {"template": "thales", "params": {"ratio": 0.5, "labels": ["A", "B", "C"], "right_angle": False}}
    if kind == "function_plot":
        return {"template": "function_plot", "params": {"kind": "quadratic", "a": 1, "b": 0, "c": -4}}
    if kind == "process_flow":
        return {
            "template": "process_flow",
            "params": {"cycle": False, "steps": [{"label": f"Stage {i + 1}", "detail": "Fixture detail."} for i in range(4)]},
        }
    if kind == "timeline":
        return {
            "template": "timeline",
            "params": {"events": [{"date": str(2000 + i), "label": f"Event {i + 1}", "detail": "Fixture detail."} for i in range(4)]},
        }
    if kind == "comparison":
        return {
            "template": "comparison",
            "params": {
                "columns": ["Option A", "Option B"],
                "rows": [{"label": f"Aspect {i + 1}", "cells": ["Fixture A", "Fixture B"]} for i in range(3)],
            },
        }
    nodes = [{"id": "core", "label": topic[:28] or "Topic", "note": f"The central idea: {topic}."}] + [
        {"id": f"n{i}", "label": f"Point {i + 1}", "note": "Fixture note."} for i in range(4)
    ]
    return {"template": "concept_map", "params": {"nodes": nodes, "edges": [{"from": "core", "to": n["id"], "label": ""} for n in nodes[1:]]}}


def _lesson(topic: str) -> dict:
    kind = kind_for(topic)
    visual = _visual_for(kind, topic)
    _, focus_keys = (None, None)
    if kind == "thales":
        focuses = ["DE", "ADE", "ratio", "solve"]
    elif kind == "function_plot":
        focuses = ["a", "a", "c", "roots"]
    elif kind == "process_flow":
        focuses = ["0", "1", "2", "3"]
    elif kind == "timeline":
        focuses = ["0", "1", "2", "3"]
    elif kind == "comparison":
        focuses = ["0", "1", "2", ""]
    else:
        focuses = ["n0", "n1", "n2", "n3"]
    return {
        "title": (topic[:80] or "Topic"),
        "summary": f"A short fixture explanation of {topic}.",
        "steps": [{"text": f"Fixture point {i + 1} about {topic}.", "focus": f} for i, f in enumerate(focuses)],
        "everyday_example": "A fixture everyday example.",
        "common_mistake": "A fixture common mistake.",
        "visual": visual,
    }


def _quiz(topic: str) -> dict:
    return {
        "questions": [
            {
                "q": f"Fixture question {i + 1} about {topic}?",
                "options": ["Option A", "Option B", "Option C", "Option D"],
                "answer": i % 4,
                "why": "Fixture rationale.",
            }
            for i in range(4)
        ]
    }


def _passages(user: str) -> dict[int, tuple[str, str]]:
    out = {}
    for m in re.finditer(r"^\[(\d+)\] \(([^)]*)\) (.+)$", user, re.M):
        out[int(m.group(1))] = (m.group(2), m.group(3))
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ0-9]{4,}", text.lower()))


def _answer(user: str) -> str:
    q = re.search(r"QUESTION:\s*(.+)", user)
    qw = _words(q.group(1) if q else "")
    best, best_score, best_n = "", 0, 1
    for n, (_, text) in _passages(user).items():
        for sentence in _sentences(text):
            score = len(qw & _words(sentence))
            if score > best_score:
                best, best_score, best_n = sentence, score, n
    if not best:
        return "The passages do not answer this question."
    return f"According to your material: {best} [{best_n}]"


def _grounded_lesson(topic: str, user: str) -> dict:
    lesson = json.loads(json.dumps(_lesson(topic)))
    sentences = [s for _, text in _passages(user).values() for s in _sentences(text)][:4]
    if len(sentences) >= 2:
        lesson["steps"] = [{"text": s[:220], "focus": ""} for s in sentences]
        lesson["summary"] = "Built from your course material."
    return lesson


def complete(messages: list[dict]) -> str:
    """Stand-in for the real model's text completion. Tests may monkeypatch this to inject a
    specific (e.g. malformed) answer, the same way they could with the old built-in demo service."""
    system = messages[0]["content"]
    user = messages[-1]["content"]
    task = (re.search(r"TASK:\s*(\w+)", system) or re.search(r"TASK:\s*(\w+)", user) or [None, "explain"])[1]
    topic = _topic(user)

    if task == "route":
        return json.dumps({"subject_area": "general", "level": "beginner"})
    if task == "illustrate":
        lesson = _lesson(topic)
        return json.dumps({**lesson["visual"], "rationale": "Fixture rationale for the figure."})
    if task == "explain":
        lesson = _grounded_lesson(topic, user) if "COURSE MATERIAL:" in user else _lesson(topic)
        return json.dumps({k: v for k, v in lesson.items() if k != "visual"})
    if task == "filter":
        n = len(_passages(user))
        return json.dumps({"keep": list(range(1, min(n, 3) + 1))})
    if task == "answer":
        return _answer(user)
    if task == "quiz":
        return json.dumps(_quiz(topic))
    if task == "quiz_check":
        m = re.search(r"DRAFT QUIZ:\s*(\{.*\})\s*$", user, re.S)
        if m:
            try:
                draft = json.loads(m.group(1))
                if isinstance(draft.get("questions"), list) and len(draft["questions"]) >= 3:
                    return json.dumps(draft)
            except json.JSONDecodeError:
                pass
        return json.dumps(_quiz(topic))
    if task == "hint":
        level = int((re.search(r"HINT_LEVEL:\s*(\d)", user) or [0, 1])[1])
        return {
            1: "What is the question really asking you to find? Say it in your own words first.",
            2: "Name the key idea or rule from the explanation that connects what you know to what you need.",
            3: "Write the first step only: put the known values into the rule, and leave the last step for you.",
        }.get(level, "Try the first step.")
    if task == "summary":
        return (
            f"- The room worked on {topic}.\n"
            "- Key points from the explanation were reviewed together.\n"
            "- Members discussed the topic and tried the shared quiz.\n"
            "- Open question: revisit the trickiest step next session."
        )
    if task == "resume":
        topics = [ln[2:].strip() for ln in user.splitlines() if ln.startswith("- ")]
        parts = []
        for t in topics or [topic]:
            lesson = _lesson(t)
            parts.append(f"## {t}\n- {lesson['summary']}\n- Review the key definition and one worked example.\n- Try a quick self-test to check recall.")
        return "\n\n".join(parts)
    if task in ("game_words", "trivia"):
        langs = re.findall(r'"(en|fr|ar|darija)": "\.\.\."', system) or ["en"]
        langs = list(dict.fromkeys(langs))
        n = int((re.search(r"(?:Give|Write) (\d+)", system) or [0, 6])[1])
        tag = lambda l, x: x if l == "en" else f"{x} ({l})"
        if task == "game_words":
            return json.dumps({"words": [{l: tag(l, f"fixture word {i}") for l in langs} for i in range(n)]})
        return json.dumps({"questions": [
            {"q": {l: tag(l, f"Fixture question {i}?") for l in langs},
             "options": [{l: tag(l, f"option {i}-{k}") for l in langs} for k in range(4)], "answer": i % 4}
            for i in range(n)]})
    if task == "repair":
        return user
    return "OK"


STOPWORDS = frozenset(
    "the and for are was were with that this from into what where when which who whom how does did why can its "
    "has have had not but all any one you your their they them then than also about over under between each "
    "les des une que qui dans pour est sont avec sur par pas plus aux ces ses".split()
)


def lexical_embedding(text: str) -> list[float]:
    """Signed feature hashing of content words: a cheap stand-in for a real embedding model."""
    vec = [0.0] * EMBED_DIM
    for word in re.findall(r"[a-zà-ÿ0-9]{3,}", text.lower()):
        if word in STOPWORDS:
            continue
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


# ------------------------------------------------------------------- the httpx fake transport
def build_fake(models: dict[str, str] | None = None) -> tuple[httpx.MockTransport, FakeState]:
    """`models` maps model name -> "compact"|"mid" (defaults match app.config.Settings' defaults)."""
    models = models or {"compact-1b": "compact", "mid-8b": "mid"}
    state = FakeState()

    async def handler(request: httpx.Request) -> httpx.Response:
        # A real await point: without it, one request can run start-to-finish before a concurrent,
        # identical one even checks the gateway's in-flight map, so coalescing would never trigger.
        await asyncio.sleep(0)
        path = request.url.path
        body = json.loads(request.content or b"{}")

        if path.endswith("/embeddings"):
            inputs = body.get("input") or []
            inputs = [inputs] if isinstance(inputs, str) else inputs
            if len(inputs) > MAX_EMBED_INPUTS:
                return httpx.Response(400, json={"error": {"message": f"input exceeds the ceiling of {MAX_EMBED_INPUTS} items."}})
            wait = state.take_embed()
            if wait > 0:
                secs = max(1, math.ceil(wait))
                return httpx.Response(
                    429,
                    json={"error": {"type": "quota_exhausted", "message": "Embeddings quota exhausted.", "retry_after": secs}},
                    headers={"Retry-After": str(secs)},
                )
            state.embed_calls += 1
            data = [{"index": i, "object": "embedding", "embedding": lexical_embedding(t)} for i, t in enumerate(inputs)]
            return httpx.Response(200, json={"object": "list", "data": data, "model": body.get("model", "")})

        # /chat/completions
        cls = models.get(body.get("model", ""))
        if cls is None:
            return httpx.Response(400, json={"error": {"message": "Unknown model."}})
        if body.get("stream"):
            return httpx.Response(400, json={"error": {"message": "Streaming is not supported."}})
        max_tokens = int(body.get("max_tokens") or 256)
        if max_tokens > MAX_TOKENS_CEILING:
            return httpx.Response(400, json={"error": {"message": f"max_tokens exceeds the ceiling of {MAX_TOKENS_CEILING}."}})
        now = time.monotonic()
        if cls == "mid" and now < state.mid_saturated_until:
            return httpx.Response(503, json={"error": {"message": "Mid-size class saturated."}}, headers={"Retry-After": "2"})
        wait = state.take()
        if wait > 0:
            secs = max(1, math.ceil(wait))
            return httpx.Response(
                429,
                json={"error": {"type": "quota_exhausted", "message": "Team quota exhausted.", "retry_after": secs}},
                headers={"Retry-After": str(secs), "X-Quota-Remaining": "0"},
            )
        state.calls += 1
        text = complete(body["messages"])
        prompt_tokens = sum(len(m["content"]) for m in body["messages"]) // 4
        return httpx.Response(
            200,
            json={
                "id": f"fake-{state.calls}",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(text) // 4},
            },
            headers={"X-Quota-Remaining": str(state.remaining())},
        )

    return httpx.MockTransport(handler), state


def fake_ai_settings() -> dict:
    """Keyword overrides for load_settings()/Settings() that satisfy the "a real service must be
    configured" check, while the transport (not the network) actually answers the calls."""
    return {"base_url": "http://fake-ai.test", "api_key": "test-key"}
