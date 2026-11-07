"""Canned 'model' answers for the fake service, so the whole app can be exercised without the real key.

Prompts start with 'TASK: <name>'; this module answers per task. The content is deliberately modest:
a few topics have hand-written lessons, everything else gets a generic structure. The real models
replace all of this by changing EDNOVA_BASE_URL.
"""
from __future__ import annotations

import json
import re


def _topic(text: str) -> str:
    m = re.search(r"TOPIC:\s*(.+)", text)
    return (m.group(1).strip() if m else "this topic")[:120]


def kind_for(topic: str) -> str:
    t = topic.lower()
    if re.search(r"thal[eè]s|triangle|similar|proportion", t):
        return "thales"
    if re.search(r"quadratic|parabola|function|linear|sine|sinus|exponential|equation|graph", t):
        return "function_plot"
    if re.search(r"photosynth|cycle|process|algorithm|how does|steps|mitosis|water", t):
        return "process_flow"
    return "concept_map"


THALES = {
    "title": "Thales' theorem (parallel lines in a triangle)",
    "summary": "A line drawn parallel to one side of a triangle cuts the other two sides in the same proportion.",
    "steps": [
        {"text": "Take triangle ABC and draw a line DE parallel to BC, with D on AB and E on AC.", "focus": "DE"},
        {"text": "The small triangle ADE has the same shape as ABC, only smaller.", "focus": "ADE"},
        {"text": "So the sides match up in the same ratio: AD/AB = AE/AC = DE/BC.", "focus": "ratio"},
        {"text": "Use it to find a missing length: write the ratio, plug in the known sides, solve.", "focus": "solve"},
    ],
    "everyday_example": "A shadow and the object casting it: tall flagpole, short stick, same sun angle, same proportion.",
    "common_mistake": "Comparing AD/DB with AE/AC. The ratio must use the whole side (AB or AC) or the same kind of part on both sides.",
    "visual": {"template": "thales", "params": {"ratio": 0.5, "labels": ["A", "B", "C"], "right_angle": False}},
}

QUADRATIC = {
    "title": "Quadratic functions and their parabola",
    "summary": "A quadratic y = ax² + bx + c draws a parabola. The numbers a, b and c control its shape and position.",
    "steps": [
        {"text": "The sign of a decides the direction: a > 0 opens upward, a < 0 opens downward.", "focus": "a"},
        {"text": "The bigger |a| is, the narrower the parabola.", "focus": "a"},
        {"text": "c is where the curve crosses the vertical axis.", "focus": "c"},
        {"text": "The roots (where y = 0) are the solutions of ax² + bx + c = 0.", "focus": "roots"},
    ],
    "everyday_example": "A ball thrown upward follows a parabola: it rises, peaks at the vertex, then falls.",
    "common_mistake": "Forgetting that a quadratic can have zero, one or two real roots.",
    "visual": {"template": "function_plot", "params": {"kind": "quadratic", "a": 1, "b": 0, "c": -4}},
}

PHOTOSYNTHESIS = {
    "title": "Photosynthesis",
    "summary": "Plants turn light, water and carbon dioxide into sugar and oxygen.",
    "steps": [
        {"text": "Leaves absorb sunlight with chlorophyll.", "focus": "0"},
        {"text": "Roots bring water up to the leaves.", "focus": "1"},
        {"text": "Carbon dioxide enters through tiny pores.", "focus": "2"},
        {"text": "Light energy turns water and CO2 into glucose, releasing oxygen.", "focus": "3"},
    ],
    "everyday_example": "A plant on a dark shelf grows weak and pale: no light, no sugar.",
    "common_mistake": "Thinking plants get their mass from soil. Most of it comes from CO2 in the air.",
    "visual": {
        "template": "process_flow",
        "params": {
            "cycle": False,
            "steps": [
                {"label": "Light absorbed", "detail": "Chlorophyll captures sunlight."},
                {"label": "Water arrives", "detail": "Roots pull water to the leaves."},
                {"label": "CO2 enters", "detail": "Gas moves in through leaf pores."},
                {"label": "Sugar and oxygen", "detail": "Glucose is stored, oxygen is released."},
            ],
        },
    },
}


def _generic(topic: str) -> dict:
    nodes = [
        {"id": "core", "label": topic[:28], "note": f"The central idea: {topic}."},
        {"id": "def", "label": "Definition", "note": "What it is, in one plain sentence."},
        {"id": "ex", "label": "Example", "note": "A concrete case from daily life."},
        {"id": "why", "label": "Why it matters", "note": "Where you will use it."},
        {"id": "err", "label": "Common mistake", "note": "What people often get wrong."},
    ]
    return {
        "title": topic[:80],
        "summary": f"A short guided tour of {topic}. (Demo content: connect the real models for full lessons.)",
        "steps": [
            {"text": f"Start with what {topic} means in your own words.", "focus": "def"},
            {"text": "Look at one concrete example.", "focus": "ex"},
            {"text": "Ask why it matters and where it shows up.", "focus": "why"},
            {"text": "Check the mistake people make most often.", "focus": "err"},
        ],
        "everyday_example": "Pick something you did today that relates to it.",
        "common_mistake": "Memorising the definition without testing it on an example.",
        "visual": {
            "template": "concept_map",
            "params": {
                "nodes": nodes,
                "edges": [{"from": "core", "to": n["id"], "label": ""} for n in nodes[1:]],
            },
        },
    }


def _lesson(topic: str) -> dict:
    kind = kind_for(topic)
    if kind == "thales":
        return THALES
    if kind == "function_plot":
        return QUADRATIC
    if kind == "process_flow":
        return PHOTOSYNTHESIS
    return _generic(topic)


def _quiz(topic: str) -> dict:
    kind = kind_for(topic)
    if kind == "thales":
        qs = [
            ("In triangle ABC, DE is parallel to BC. Which ratios are equal?", ["AD/AB = AE/AC", "AD/DB = AC/AE", "DE/AD = BC/AC", "AB/AD = DE/BC"], 0, "Thales compares the small triangle to the whole one: AD/AB = AE/AC = DE/BC."),
            ("AD = 3, AB = 9, BC = 12. What is DE?", ["2", "4", "6", "36"], 1, "DE/BC = AD/AB = 1/3, so DE = 12/3 = 4."),
            ("What must be true before using Thales' theorem?", ["DE is parallel to BC", "The triangle is right-angled", "AB = AC", "D is the midpoint"], 0, "Parallel lines are the only requirement."),
            ("Which statement is the common mistake?", ["Using AD/AB = AE/AC", "Using AD/DB = AE/AC", "Writing DE/BC", "Checking parallelism"], 1, "AD/DB mixes a part with a whole."),
        ]
    elif kind == "function_plot":
        qs = [
            ("For y = x² − 4, where does the curve cross the y-axis?", ["y = −4", "y = 4", "y = 0", "y = 2"], 0, "At x = 0, y = c = −4."),
            ("If a is negative, the parabola…", ["opens upward", "opens downward", "is a straight line", "disappears"], 1, "The sign of a sets the direction."),
            ("What are the roots of x² − 4 = 0?", ["2 and −2", "4 and −4", "0 only", "none"], 0, "x² = 4, so x = 2 or x = −2."),
            ("Which change makes the parabola narrower?", ["Smaller |a|", "Bigger |a|", "Bigger c", "Smaller c"], 1, "A larger |a| squeezes the curve."),
        ]
    else:
        qs = [
            (f"Which sentence best describes {topic}?", ["A clear definition tested on an example", "A list of unrelated facts", "Something to memorise without understanding", "None of these"], 0, "Understanding means being able to apply it."),
            ("What is the best first step when you are stuck?", ["Give up", "Ask for a small hint", "Read the answer immediately", "Skip the topic"], 1, "A hint keeps the thinking on your side."),
            ("Why test yourself with questions?", ["It shows what you really know", "It is more fun than studying", "It replaces understanding", "It is required"], 0, "Retrieval practice reveals gaps."),
            ("What helps memory most over time?", ["One long session", "Short sessions spread out", "Reading once", "Highlighting only"], 1, "Spacing beats cramming."),
        ]
    return {"questions": [{"q": q, "options": o, "answer": a, "why": w} for q, o, a, w in qs]}


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
    system = messages[0]["content"]
    user = messages[-1]["content"]
    task = (re.search(r"TASK:\s*(\w+)", system) or re.search(r"TASK:\s*(\w+)", user) or [None, "explain"])[1]
    topic = _topic(user)

    if task == "route":
        return json.dumps({"subject_area": "general", "level": "beginner"})
    if task == "illustrate":
        lesson = _lesson(topic)
        return json.dumps({**lesson["visual"], "rationale": f"The figure shows the key relation in {topic[:60]} so the steps can point at it."})
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
    if task == "hint":
        level = int((re.search(r"HINT_LEVEL:\s*(\d)", user) or [0, 1])[1])
        return {
            1: "What is the question really asking you to find? Say it in your own words first.",
            2: "Name the key idea or rule from the explanation that connects what you know to what you need.",
            3: "Write the first step only: put the known values into the rule, and leave the last step for you.",
        }.get(level, "Try the first step.")
    if task == "repair":
        return user
    return "OK"
