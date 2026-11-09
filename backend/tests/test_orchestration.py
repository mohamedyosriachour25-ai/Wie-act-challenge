"""Which model class does what: reasoning/synthesis/final writing (figure, explanation, quiz, resume) is
mid-size; classification, extraction, short summaries, hints and material answers are compact."""
import pytest
from fastapi.testclient import TestClient

from app.ai import mock_llm, prompts
from app.ai.visuals import describe_figure
from app.config import load_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))
    with TestClient(app) as c:
        yield c


def signup(client, pseudo="ada_l"):
    return {"Authorization": "Bearer " + client.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]}


def calls(client):
    return client.get("/api/ai/metrics").json()["calls"]


def test_explanation_costs_two_mid_calls_and_one_compact_call(client):
    h = signup(client)
    out = client.post("/api/study/explain", json={"topic": "Thales theorem"}, headers=h).json()
    c = calls(client)
    assert (c["mid"], c["compact"]) == (2, 1)
    assert [(m["task"], m["cls"]) for m in out["meta"]["calls"]] == [("route", "compact"), ("illustrate", "mid"), ("explain", "mid")]
    assert out["figure_note"] and out["visual"]["template"] == "thales" and not out["visual_fallback"]


def test_step_focus_always_points_at_something_in_the_figure(client):
    h = signup(client)
    for topic in ("Thales theorem", "Quadratic functions", "Photosynthesis", "French revolution causes"):
        out = client.post("/api/study/explain", json={"topic": topic}, headers=h).json()
        _, keys = describe_figure(out["visual"])
        assert all(s["focus"] in keys + [""] for s in out["steps"]), topic
        assert any(s["focus"] for s in out["steps"]), topic


def test_bad_figure_from_the_big_model_falls_back_to_a_map_that_lights_up_per_step(client, monkeypatch):
    real = mock_llm.complete

    def broken(messages):
        return '{"template": "hologram", "params": {}}' if "TASK: illustrate" in messages[0]["content"] else real(messages)

    monkeypatch.setattr(mock_llm, "complete", broken)
    out = client.post("/api/study/explain", json={"topic": "Thales theorem"}, headers=signup(client)).json()
    assert out["visual_fallback"] is True and out["visual"]["template"] == "concept_map"
    ids = {n["id"] for n in out["visual"]["params"]["nodes"]}
    assert all(s["focus"] in ids for s in out["steps"])


def test_answers_from_material_never_use_the_mid_size_model(client):
    h = signup(client)
    text = "Mitochondria break glucose down with oxygen to release usable energy as ATP. Respiration happens day and night in every cell of the body."
    client.post("/api/materials/file", content=text.encode(), headers={**h, "X-Filename": "bio.txt"})
    import time
    for _ in range(100):
        if all(d["status"] == "ready" for d in client.get("/api/materials", headers=h).json()["documents"]):
            break
        time.sleep(0.05)
    before = calls(client)
    out = client.post("/api/materials/ask", json={"scope": "me", "question": "What do mitochondria do with glucose?"}, headers=h).json()
    after = calls(client)
    assert out["grounded"] and after["mid"] == before["mid"] and after["compact"] > before["compact"]


def test_every_language_reaches_the_prompts():
    for lang, needle in (("en", "English"), ("fr", "French"), ("ar", "Modern Standard Arabic"), ("darija", "Tunisian Arabic")):
        figure_text, _ = describe_figure(None)
        for msgs in (
            prompts.illustrate_messages("topic", lang),
            prompts.explain_messages("topic", "balanced", "beginner", figure_text, lang),
            prompts.hint_messages("topic", "q", 1, lang),
            prompts.quiz_messages("topic", "beginner", lang),
            prompts.answer_messages("q", [{"title": "t", "text": "x"}], lang),
        ):
            assert needle in msgs[0]["content"], (lang, msgs[0]["content"][:60])
    assert prompts.norm_lang("xx") == "en" and prompts.norm_lang(None) == "en"
