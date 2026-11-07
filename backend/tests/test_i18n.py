"""Server texts exist in every language, and the language really travels through REST, WebSocket and prompts."""
import ast
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.ai import mock_llm
from app.config import load_settings
from app.i18n import CATALOG, LANGS, norm, tr
from app.main import create_app
from app.routes_study import DIAGNOSTIC

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
INTERNAL = ("bad ", "too few", "empty selection")


def literals() -> set[str]:
    """Every English sentence the code can show a person: tr("..."), AIError("kind", "..."), user-facing ValueError("...")."""
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        if path.name in ("i18n.py",) or "mock" in path.name:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            args = node.args
            if name == "tr" and len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                found.add(args[1].value)
            elif name == "AIError" and len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                found.add(args[1].value)
            elif name == "ValueError" and args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                if not args[0].value.startswith(INTERNAL):
                    found.add(args[0].value)
    # sentences passed as variables (diagnostic data, private-data reasons)
    for d in DIAGNOSTIC:
        found.add(d["q"])
        found.update(o[0] for o in d["options"])
    src = (APP / "materials.py").read_text()
    found.update(re.findall(r'return "(This looks like[^"]+)"', src))
    found.add("Material is sent to the AI service, so remove personal details and add it again.")
    # dynamic messages are translated as a family, not literally
    return {s for s in found if not s.startswith(("The AI service refused", "Embeddings failed", "The AI service answered"))}


def test_every_server_text_has_french_arabic_and_derja():
    missing = [(lang, s) for s in sorted(literals()) for lang in ("fr", "ar", "darija") if s not in CATALOG[lang]]
    assert not missing, missing


def test_translations_keep_their_placeholders_and_use_the_right_script():
    for lang in ("fr", "ar", "darija"):
        for en, text in CATALOG[lang].items():
            assert sorted(re.findall(r"\{\w+\}", en)) == sorted(re.findall(r"\{\w+\}", text)), (lang, en)
            if lang in ("ar", "darija"):
                assert re.search(r"[؀-ۿ]", text), (lang, en)
    assert tr("fr", "unknown sentence") == "unknown sentence" and norm("zz") == "en" and set(LANGS) == {"en", "fr", "ar", "darija"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(load_settings(db_path=str(tmp_path / "t.db"), mock_latency_scale=0.0))) as c:
        yield c


def signup(client, pseudo="ada_l"):
    return {"Authorization": "Bearer " + client.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]}


def test_rest_errors_follow_the_x_lang_header(client):
    r = client.post("/api/auth/register", json={"pseudo": "a b", "password": "secret1"}, headers={"X-Lang": "fr"})
    assert r.status_code == 400 and "Choisissez un pseudo" in r.json()["detail"]
    r = client.post("/api/auth/register", json={"pseudo": "a b", "password": "secret1"}, headers={"X-Lang": "darija"})
    assert "اختار" in r.json()["detail"]
    assert "Pick a pseudo" in client.post("/api/auth/register", json={"pseudo": "a b", "password": "secret1"}).json()["detail"]


def test_diagnostic_is_served_in_the_chosen_language(client):
    h = signup(client)
    en = client.get("/api/study/diagnostic", headers=h).json()["questions"]
    ar = client.get("/api/study/diagnostic", headers={**h, "X-Lang": "ar"}).json()["questions"]
    assert len(en) == len(ar) == 4 and en[0]["q"] != ar[0]["q"] and re.search(r"[؀-ۿ]", ar[0]["q"])
    assert all(len(a["options"]) == 4 for a in ar)


def test_chosen_language_reaches_the_model_and_the_cached_copy_is_per_language(client, monkeypatch):
    seen = []
    real = mock_llm.complete

    def spy(messages):
        seen.append(messages[0]["content"])
        return real(messages)

    monkeypatch.setattr(mock_llm, "complete", spy)
    h = signup(client)
    for lang in ("fr", "darija", "fr"):
        client.post("/api/study/explain", json={"topic": "Photosynthesis"}, headers={**h, "X-Lang": lang})
    assert any("French" in s for s in seen) and any("Tunisian Arabic" in s for s in seen)
    # route does not depend on the language, so it is shared: 1 route + (figure + explanation) per language; the repeat is a cache hit
    assert len(seen) == 5


def test_room_members_get_errors_and_nudges_in_their_own_language(client):
    ta = signup(client, "alice")["Authorization"].split()[1]
    tb = signup(client, "bob")["Authorization"].split()[1]
    room = client.post("/api/rooms", json={"name": "Maths", "capacity": 2}, headers={"Authorization": f"Bearer {ta}"}).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={ta}&lang=fr") as a, client.websocket_connect(f"/ws/rooms/{room['code']}?token={tb}&lang=ar") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"type": "ask", "question": "anything at all"})   # no material in the room
        ea = next(m for m in iter(a.receive_json, None) if m["type"] == "ai_error")
        assert "Cette salle" in ea["message"]
        b.send_json({"type": "lang", "lang": "darija"})
        b.send_json({"type": "ask", "question": "anything at all"})
        eb = next(m for m in iter(b.receive_json, None) if m["type"] == "ai_error")
        assert "الغرفة" in eb["message"] and "مازال" in eb["message"]


def test_status_carries_a_code_the_interface_can_translate(client):
    s = client.get("/api/ai/status").json()
    assert "code" in s and "cls" in s
