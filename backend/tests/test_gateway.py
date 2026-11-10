"""The gateway against the fake service: each test is one row of the guide's error contract."""
import asyncio

import pytest

from app.ai.gateway import AIError, Gateway
from app.config import Settings

from ._fake_service import build_fake

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make(**over):
    s = Settings(base_url="http://fake-ai.test", api_key="test-key", max_quota_wait_s=10, **over)
    transport, state = build_fake({s.compact_model: "compact", s.mid_model: "mid"})
    return Gateway(s, transport), state


MSG = [{"role": "system", "content": "TASK: route\n"}, {"role": "user", "content": "TOPIC: Thales theorem"}]


async def test_normal_call_updates_metrics_and_reads_quota_header():
    gw, _ = make()
    text, meta = await gw.chat("route", "compact", MSG, max_tokens=100)
    assert "level" in text
    assert meta.cls == "compact" and not meta.cached
    assert gw.metrics.calls["compact"] == 1
    assert gw.quota_remaining is not None


async def test_identical_request_is_served_from_cache():
    gw, state = make()
    await gw.chat("route", "compact", MSG, max_tokens=100)
    _, meta = await gw.chat("route", "compact", MSG, max_tokens=100)
    assert meta.cached and state.calls == 1
    assert gw.metrics.cache_hits == 1 and gw.metrics.repeated_identical == 0


async def test_concurrent_identical_requests_share_one_call():
    gw, state = make()
    results = await asyncio.gather(*(gw.chat("route", "compact", MSG, max_tokens=100) for _ in range(5)))
    assert state.calls == 1
    assert gw.metrics.coalesced == 4
    assert len({r[0] for r in results}) == 1


async def test_quota_exhausted_waits_the_stated_delay_then_succeeds():
    gw, state = make()
    state.simulate("quota", seconds=1.0)
    _, meta = await gw.chat("route", "compact", MSG, max_tokens=100)
    assert gw.metrics.quota_refusals >= 1 and gw.metrics.quota_handled >= 1
    assert meta.waited_ms >= 500


async def test_quota_beyond_max_wait_raises_a_clear_error():
    gw, state = make()
    state.simulate("quota", seconds=60)
    with pytest.raises(AIError) as exc:
        await gw.chat("route", "compact", MSG, max_tokens=100)
    assert exc.value.kind == "quota" and exc.value.retry_after and exc.value.retry_after > 10


async def test_mid_saturated_falls_back_to_compact_and_says_so():
    gw, state = make()
    state.simulate("saturate", seconds=30)
    _, meta = await gw.chat("explain", "mid", [{"role": "system", "content": "TASK: explain\n"}, {"role": "user", "content": "TOPIC: x"}], max_tokens=300)
    assert meta.degraded and meta.cls == "compact"
    assert gw.metrics.fallbacks == 1 and gw.metrics.saturation_503 == 2
    assert gw.status()["notice"]["kind"] == "fallback"


async def test_request_beyond_ceiling_is_fixed_not_dropped():
    gw, _ = make()
    _, meta = await gw.chat("route", "compact", MSG, max_tokens=4000)
    assert gw.metrics.bad_requests == 2 and meta.attempts == 3


async def test_chat_json_returns_none_instead_of_crashing_on_garbage():
    gw, _ = make()

    def never(obj):
        raise ValueError("nope")

    obj, _ = await gw.chat_json("route", "compact", MSG, never, max_tokens=100)
    assert obj is None and gw.metrics.repairs == 1


async def test_unknown_model_is_an_error_not_a_hang():
    gw, _ = make(compact_model="compact-1b")
    gw.s = Settings(base_url="http://fake-ai.test", api_key="test-key", compact_model="nope")
    with pytest.raises(AIError):
        await gw.chat("route", "compact", MSG, max_tokens=100)


async def test_embeddings_are_cached_per_text():
    gw, _ = make()
    a = await gw.embed(["alpha", "beta"])
    b = await gw.embed(["beta", "alpha"])
    assert gw.metrics.embeddings == 2 and a[0] == b[1]


async def test_degraded_answers_are_not_cached():
    gw, state = make()
    explain = [{"role": "system", "content": "TASK: explain\n"}, {"role": "user", "content": "TOPIC: x"}]
    state.simulate("saturate", seconds=30)
    _, first = await gw.chat("explain", "mid", explain, max_tokens=300)
    assert first.degraded
    state.simulate("clear")
    _, second = await gw.chat("explain", "mid", explain, max_tokens=300)
    assert not second.cached and not second.degraded and second.cls == "mid"


async def test_embeddings_are_batched_under_the_ceiling_and_cached():
    gw, state = make()
    texts = [f"passage number {i} about topic {i}" for i in range(100)]  # more than the 48-item ceiling
    vectors = await gw.embed(texts)
    assert len(vectors) == 100 and len(vectors[0]) == 256
    calls = state.embed_calls
    assert calls >= 3 and gw.metrics.embeddings == 100
    again = await gw.embed(texts[:10])  # served from the cache: no new call
    assert again == vectors[:10] and state.embed_calls == calls


async def test_embedding_ceiling_error_halves_the_batch():
    gw, state = make()
    vectors = await gw.embed([f"another distinct passage {i}" for i in range(60)], batch=60)  # 60 > 48 -> 400 -> halves
    assert len(vectors) == 60
