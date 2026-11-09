"""Semantic cache (near-duplicate reuse) and output-reliability (few-shot + schema-aware repair)."""
import httpx
import pytest

from app.ai import prompts
from app.ai.gateway import Gateway
from app.ai.mock_api import build_mock
from app.ai.pipeline import explain
from app.ai.semcache import SemanticCache, cosine
from app.config import Settings, load_settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .test_app import auth, signup

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make(**over):
    s = Settings(mock_latency_scale=0.0, **over)
    app = FastAPI()
    router, _ = build_mock(s)
    app.include_router(router)
    gw = Gateway(s, httpx.ASGITransport(app=app))
    gw.semcache = SemanticCache(gw, s)
    return gw


# ---------------------------------------------------------------- semantic cache
async def test_near_duplicate_topic_is_reused_without_new_calls():
    gw = make(sem_threshold=0.6)  # lexical mock scores rewords ~0.7; real embeddings score them higher
    first = await explain(gw, "Thales theorem")
    calls_after_first = dict(gw.metrics.calls)
    assert gw.metrics.semantic_hits == 0 and sum(calls_after_first.values()) >= 2

    # same intent, different words -> served from the semantic cache, no generation call
    second = await explain(gw, "Please explain the Thales theorem to me")
    assert second["meta"]["cached"] is True and second["meta"]["semantic"] is True
    assert second["meta"]["calls"] == [] and second["title"] == first["title"]
    assert gw.metrics.semantic_hits == 1
    assert gw.metrics.calls == calls_after_first  # the service was not called again


async def test_a_different_topic_is_not_reused():
    gw = make()
    await explain(gw, "Thales theorem")
    before = sum(gw.metrics.calls.values())
    out = await explain(gw, "Photosynthesis in plants")
    assert out["meta"]["calls"] and sum(gw.metrics.calls.values()) > before
    assert gw.metrics.semantic_hits == 0


async def test_reuse_is_separated_by_language_and_style():
    gw = make()
    await explain(gw, "Thales theorem", style="visual", lang="en")
    # different style namespace -> not reused
    out = await explain(gw, "Thales theorem explained", style="practice", lang="en")
    assert out["meta"]["calls"] and gw.metrics.semantic_hits == 0
    # different language namespace -> not reused
    out2 = await explain(gw, "Thales theorem explained", style="visual", lang="fr")
    assert out2["meta"]["calls"] and gw.metrics.semantic_hits == 0


async def test_grounded_explanations_are_never_semantically_reused():
    gw = make()
    ctx = [{"title": "Geometry notes", "text": "A line parallel to one side of a triangle divides the other two sides proportionally."}]
    await explain(gw, "Thales theorem", context=ctx)
    before = sum(gw.metrics.calls.values())
    await explain(gw, "Explain Thales theorem", context=ctx)
    assert sum(gw.metrics.calls.values()) > before  # grounded answers depend on the passages, so always regenerate
    assert gw.metrics.semantic_hits == 0


async def test_semantic_cache_can_be_turned_off():
    gw = make(semantic_cache=False)
    await explain(gw, "Thales theorem")
    before = sum(gw.metrics.calls.values())
    await explain(gw, "Explain the Thales theorem")
    assert sum(gw.metrics.calls.values()) > before and gw.metrics.semantic_hits == 0


def test_cosine_is_sane():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------- output reliability
async def test_malformed_json_is_repaired_with_the_schema():
    """A model that answers with prose around the JSON, or trailing junk, is still recovered."""
    bad = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "repair" in body:  # the schema-aware repair returns clean JSON
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"subject_area":"math","level":"beginner"}'}}], "usage": {}})
        bad["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "Sure! here you go: {oops not json"}}], "usage": {}})

    gw = Gateway(Settings(mock_latency_scale=0.0, base_url="https://ai.test/v1", api_key="k"), httpx.MockTransport(handler))
    from app.ai.pipeline import _validate_route
    obj, meta = await gw.chat_json("route", "compact", prompts.route_messages("x"), _validate_route, schema=prompts.ROUTE_SCHEMA)
    assert obj == {"subject_area": "math", "level": "beginner"} and gw.metrics.repairs == 1 and bad["n"] == 1


def test_prompts_carry_worked_examples_and_schemas():
    # few-shot examples steer the real model toward valid, well-formed output
    assert "Example" in prompts.ROUTE_SYSTEM and "math" in prompts.ROUTE_SYSTEM
    quiz = prompts.quiz_messages("Thales theorem", "beginner", "en")[0]["content"]
    assert "Example question" in quiz and prompts.QUIZ_SCHEMA in quiz
    assert prompts.FIGURE_SCHEMA and prompts.EXPLAIN_SCHEMA


def test_metrics_expose_semantic_hits():
    with TestClient(create_app(load_settings(db_path=":memory:", mock_latency_scale=0.0, sem_threshold=0.6))) as c:
        token = signup(c, "ada_l")
        c.post("/api/study/explain", json={"topic": "Thales theorem"}, headers=auth(token))
        c.post("/api/study/explain", json={"topic": "explain the Thales theorem"}, headers=auth(token))
        m = c.get("/api/ai/metrics").json()
        assert m["semantic_hits"] == 1 and m["cache_hits"] >= 1


# ---------------------------------------------------------------- quiz draft-then-revise
def _quiz_json(answer: int) -> str:
    import json
    q = {"q": "Why does a line parallel to a triangle's side split the other two proportionally?",
         "options": ["The triangles are similar", "It is equilateral", "The sides are equal", "It is right-angled"],
         "answer": answer, "why": "Similar triangles keep matching ratios."}
    return json.dumps({"questions": [dict(q, q=f"Q{i}: {q['q']}") for i in range(3)]})


def _quiz_gateway(verify: bool):
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.read().decode())
        system = data["messages"][0]["content"]
        reply = _quiz_json(2) if "TASK: quiz_check" in system else _quiz_json(0)  # reviewer fixes the key 0 -> 2
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}], "usage": {}})

    s = Settings(mock_latency_scale=0.0, base_url="https://ai.test/v1", api_key="k", quiz_verify=verify)
    return Gateway(s, httpx.MockTransport(handler))


async def test_compact_reviewer_corrects_the_mid_size_quiz_draft():
    from app.ai.pipeline import get_quiz
    gw = _quiz_gateway(verify=True)
    questions, meta = await get_quiz(gw, "Thales theorem")
    assert all(q["answer"] == 2 for q in questions)            # the reviewer's corrected key is used
    assert gw.metrics.calls == {"compact": 1, "mid": 1}         # mid-size draft (synthesis), compact review (checking)
    assert [c["task"] for c in meta["calls"]] == ["quiz", "quiz_check"]


async def test_quiz_verification_can_be_turned_off():
    from app.ai.pipeline import get_quiz
    gw = _quiz_gateway(verify=False)
    questions, meta = await get_quiz(gw, "Thales theorem")
    assert all(q["answer"] == 0 for q in questions)            # only the draft, unreviewed
    assert gw.metrics.calls == {"compact": 0, "mid": 1} and len(meta["calls"]) == 1


async def test_live_mock_runs_both_quiz_passes():
    gw = make()  # fake service: the quiz_check task returns a clean quiz, so the revise step is exercised
    from app.ai.pipeline import get_quiz
    questions, meta = await get_quiz(gw, "Photosynthesis")
    assert len(questions) >= 3 and [c["task"] for c in meta["calls"]] == ["quiz", "quiz_check"]
