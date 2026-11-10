"""What real models actually sent back (Mohamed's TCP/IP session): hints in JSON, a lesson padded with the
prompt's own labels and a pasted source list, a figure spec in a shape the validator did not know, and the
document's title page treated as content. Each is handled here."""
import asyncio

from app.ai import prompts
from app.ai.pipeline import _validate_figure, plain_text
from app.ai.tutor import clean_answer, figure_from_text
from app.ai.visuals import sanitize_visual
from app.materials import looks_front_matter

REAL_HINTS = [
    '{"status": "hint", "template": "tcpip-architecture", "focus_key": "couch_rene", "level": 1, "question": "What are the primary functions of the couche de réseau?"}',
    '{ "question": "What is the primary function of the couche de réseau in the TCP/IP architecture?", "answer_key": "Routing", "hint_level": "2" }',
    '{"answer": "Couche de réseaux", "template": "tcpip-architecture", "focus": "layers", "hint": "What is the role of the Internet Protocol (IP) in the couche de réseaux?"}',
]

REAL_ANSWER = """Here is the final answer:
In the TCP/IP Architecture, the fundamentals are the four layers of the protocol, which include:
* La couche réseau (Network Layer) [1]
* La couche de transport (Transport Layer)
* L'architecture logicielle TCP/IP (Logical Architecture) [1]
Note: The exact details of each layer are discussed in the TCP_IP_demo document.
Example: The Network Layer (La couche réseau) is responsible for routing packets of data between devices on the internet. [1]
Pointer to the figure: See Figure [not available in the provided sources], which illustrates the four layers of the TCP/IP Architecture.
Sources: 1 (material: TCP_IP_demo) Réseau TCP/IP Département GTR – IUT de Villetaneuse Rushed Kanawati [e-mail removed]
2 (web) Wikipedia: TCP/IP model"""


def test_hints_are_plain_sentences_even_when_the_model_answers_in_json():
    out = [plain_text(h) for h in REAL_HINTS]
    assert out[0] == "What are the primary functions of the couche de réseau?"
    assert out[2] == "What is the role of the Internet Protocol (IP) in the couche de réseaux?"
    assert all("{" not in h and "template" not in h for h in out)
    assert plain_text("Think about what each layer adds to the packet.") == "Think about what each layer adds to the packet."


def test_the_hint_prompt_asks_for_plain_text_per_level():
    for level in (1, 2, 3):
        system = prompts.hint_messages("TCP/IP", "which layer routes packets?", level)[0]["content"]
        assert f"Level {level}:" in system and "Never reply with JSON" in system


def test_a_lesson_keeps_its_content_and_loses_the_padding():
    out = clean_answer(REAL_ANSWER)
    assert out.startswith("In the TCP/IP Architecture")
    assert "La couche réseau" in out and "routing packets" in out
    for junk in ("Here is the final answer", "Note:", "Pointer to the figure", "Sources:", "Wikipedia: TCP/IP model", "not available"):
        assert junk not in out, junk


def test_the_educator_prompt_asks_for_a_structured_lesson_without_padding():
    system = prompts.tutor_messages("explain", "Explain the TCP/IP architecture", "balanced", "beginner", "fr", "", [])[0]["content"]
    for part in ("### Key ideas", "### How they connect", "### Example", "### Check yourself"):
        assert part in system
    assert "COMPLETE set" in system and "title page" in system and "Never write a 'Sources' list" in system
    assert "pointer to the figure" not in system.lower()


def test_figure_specs_in_loose_shapes_are_recovered():
    layers = {"template": "Process Flow", "params": {"layers": [
        {"name": "Application", "description": "HTTP, DNS"}, {"name": "Transport", "description": "TCP, UDP"},
        {"name": "Internet", "description": "IP routing"}, {"name": "Network access", "description": "Ethernet, Wi-Fi"}]},
        "rationale": "Shows the stack"}
    fig = _validate_figure(layers)
    assert fig["visual"]["template"] == "process_flow" and [s["label"] for s in fig["visual"]["params"]["steps"]][:2] == ["Application", "Transport"]
    top_level = sanitize_visual({"template": "tcpip-architecture", "steps": ["Application: apps", "Transport: TCP", "Internet: IP"]})
    assert top_level["template"] == "process_flow" and top_level["params"]["steps"][0] == {"label": "Application", "detail": "apps"}
    cmap = sanitize_visual({"template": "mind map", "params": {"nodes": ["TCP/IP", "TCP", "IP"], "edges": [{"source": "TCP/IP", "target": "IP"}]}})
    assert cmap["template"] == "concept_map" and cmap["params"]["edges"] == [{"from": "n0", "to": "n2", "label": ""}]


def test_an_unusable_figure_is_repaired_once_then_built_from_the_answer():
    try:
        _validate_figure({"template": "process_flow", "params": {"steps": "the four layers"}})
        raise AssertionError("an unusable spec must trigger the repair pass")
    except ValueError:
        pass
    fig = figure_from_text(REAL_ANSWER, "TCP/IP architecture")      # bullets, no title line, no numbered steps
    assert fig and fig["template"] == "concept_map" and len(fig["params"]["nodes"]) >= 3
    fig = figure_from_text("Plain text without a list.", "TCP/IP", [{"name": "IP", "simple": "addresses packets"}, {"name": "TCP", "simple": "reliable delivery"}])
    assert [n["label"] for n in fig["params"]["nodes"]] == ["TCP/IP", "IP", "TCP"]


def test_title_pages_are_recognised_as_front_matter():
    assert looks_front_matter("Réseau TCP/IP Département GTR – IUT de Villetaneuse Rushed Kanawati [e-mail removed]")
    assert looks_front_matter("Introduction ........ 3 Les couches ........ 5 Le protocole IP ........ 9 TCP ........ 14")
    assert not looks_front_matter(
        "La couche réseau achemine les paquets entre les machines. Le protocole IP attribue une adresse à chaque interface "
        "et choisit la route. La couche transport découpe les données en segments et contrôle leur bonne réception.")


def test_a_failed_figure_still_gives_the_answer_a_diagram(tmp_path):
    """End to end: the figure model returns a spec nothing can use, twice; the explanation still ends with a figure."""
    import json
    from fastapi.testclient import TestClient
    from app.config import load_settings
    from app.main import create_app
    from ._fake_service import build_fake, fake_ai_settings
    from . import _fake_service as fake

    original = fake.complete

    def broken_figures(messages):
        system = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        if system.startswith("TASK: illustrate") or system.startswith("TASK: repair"):
            return '{"template": "tcpip-architecture", "params": {"layers": "four"}}'
        return original(messages)

    fake.complete = broken_figures
    try:
        app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=build_fake()[0])
        with TestClient(app) as c:
            t = c.post("/api/auth/register", json={"pseudo": "net_learner", "password": "secret1"}).json()["token"]
            h = {"Authorization": f"Bearer {t}"}
            sid = c.get("/api/spaces", headers=h).json()["spaces"][0]["id"]
            r = c.post(f"/api/spaces/{sid}/ask", json={"message": "Explain the TCP/IP architecture", "web": False}, headers=h)
            events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
            visual = next((e for e in events if e["type"] == "visual"), None)
            assert visual and visual["visual"]["template"] == "concept_map" and visual["fallback"] is True
            assert events[-1]["type"] == "done"
    finally:
        fake.complete = original


def test_the_figure_is_drawn_from_the_final_answer(tmp_path):
    """The figure call comes after the final answer and receives it, so text and figure list the same items."""
    import json
    from fastapi.testclient import TestClient
    from app.config import load_settings
    from app.main import create_app
    from ._fake_service import build_fake, fake_ai_settings

    transport, state = build_fake()
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings()), ai_transport=transport)
    with TestClient(app) as c:
        t = c.post("/api/auth/register", json={"pseudo": "net_learner", "password": "secret1"}).json()["token"]
        h = {"Authorization": f"Bearer {t}"}
        sid = c.get("/api/spaces", headers=h).json()["spaces"][0]["id"]
        r = c.post(f"/api/spaces/{sid}/ask", json={"message": "Explain photosynthesis", "web": False}, headers=h)
        events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
        final = next(e["text"] for e in events if e["type"] == "final")
        tasks = [e["messages"][0]["content"].split("\n", 1)[0] for e in state.log]
        assert tasks.index("TASK: tutor") < tasks.index("TASK: illustrate")
        fig_call = next(e for e in state.log if e["messages"][0]["content"].startswith("TASK: illustrate"))
        assert "ANSWER:\n" + final[:200] in fig_call["messages"][1]["content"]
        assert "same order and the same number" in fig_call["messages"][0]["content"]
    system = prompts.tutor_messages("explain", "x", "balanced", "beginner", "en", "", [], draft="d")[0]["content"]
    assert "Do not trust it" in system and "Never mix two different models" in system
