"""Illustration specs.

The model never draws. It picks a template from a fixed catalogue and fills in parameters; this module
validates and clamps them, and the React renderer draws the figure. A bad or missing spec degrades to a
concept map built from the explanation's own steps, so every explanation still gets an illustration.

To add a template: add a validator here, a renderer in frontend/src/visuals, and a line in the catalogue.
"""
from __future__ import annotations

import math
import re

TEMPLATES = ("thales", "function_plot", "process_flow", "concept_map", "timeline", "comparison")

CATALOGUE = """Visual templates (pick exactly one, whichever actually fits this topic best):
- thales: similar triangles, proportionality, parallel lines. params {"ratio": 0.2-0.8, "labels": ["A","B","C"], "right_angle": true|false}
- function_plot: curves and equations. params {"kind": "linear"|"quadratic"|"sine"|"exponential", "a": -5..5, "b": -5..5, "c": -5..5}
- process_flow: an ordered process or a cycle. params {"cycle": true|false, "steps": [{"label": "short", "detail": "one sentence"}]} with 3 to 7 steps
- concept_map: how ideas relate (use this as a last resort, when nothing else fits). params {"nodes": [{"id": "n1", "label": "short", "note": "one sentence"}], "edges": [{"from": "n1", "to": "n2", "label": "short"}]} with 4 to 9 nodes, the first node is the central idea
- timeline: events in chronological order (history, biographies, the development of something over time). params {"events": [{"date": "short, e.g. '1789' or 'Day 3'", "label": "short title", "detail": "one sentence"}]} with 3 to 8 events, given in chronological order
- comparison: two or three things compared side by side (causes vs effects, pros vs cons, A vs B). params {"columns": ["short column title", ...] (2 or 3 columns), "rows": [{"label": "short aspect being compared", "cells": ["one short phrase per column", ...]}]} with 3 to 7 rows; every row's "cells" must have exactly as many items as "columns"
"""


def _num(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, v))


def _str(value, limit: int, default: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""
    return text[:limit] if text else default


TEMPLATE_ALIASES = {
    "process": "process_flow", "flow": "process_flow", "flowchart": "process_flow", "steps": "process_flow",
    "cycle": "process_flow", "pipeline": "process_flow", "layers": "process_flow", "stack": "process_flow",
    "conceptmap": "concept_map", "mindmap": "concept_map", "map": "concept_map", "graph": "concept_map",
    "network": "concept_map", "tree": "concept_map", "hierarchy": "concept_map",
    "chronology": "timeline", "history": "timeline", "compare": "comparison", "table": "comparison",
    "plot": "function_plot", "function": "function_plot", "curve": "function_plot", "triangle": "thales",
}
LIST_KEYS = {
    "process_flow": ("steps", "stages", "layers", "phases", "items", "levels", "nodes"),
    "concept_map": ("nodes", "concepts", "items", "ideas"),
    "timeline": ("events", "items", "dates", "steps"),
}
LABEL_KEYS = ("label", "name", "title", "concept", "step", "stage", "layer", "event", "term", "text")
DETAIL_KEYS = ("detail", "details", "description", "desc", "note", "explanation", "summary", "role", "function", "definition")


def _pick(item: dict, keys: tuple, limit: int) -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, (str, int, float)) and _str(v, limit):
            return _str(v, limit)
    return ""


def _items(p: dict, template: str) -> list | None:
    for k in LIST_KEYS.get(template, ()):
        if isinstance(p.get(k), list):
            return p[k]
    return None


def _template_name(spec: dict, p: dict) -> str | None:
    """Small models often write the template name loosely ("Process Flow", "flowchart", "tcpip-layers") or
    describe the parts without naming a template: map those to the catalogue instead of losing the figure."""
    raw = re.sub(r"[^a-z_]", "", str(spec.get("template") or spec.get("type") or "").lower().replace(" ", "_").replace("-", "_"))
    if raw in TEMPLATES:
        return raw
    flat = raw.replace("_", "")
    for alias, name in TEMPLATE_ALIASES.items():
        if flat == alias or alias in flat:
            return name
    if isinstance(p.get("events"), list):
        return "timeline"
    if isinstance(p.get("columns"), list) and isinstance(p.get("rows"), list):
        return "comparison"
    if isinstance(p.get("edges") or p.get("links"), list) or isinstance(p.get("nodes") or p.get("concepts"), list):
        return "concept_map"
    if any(isinstance(p.get(k), list) for k in LIST_KEYS["process_flow"]):
        return "process_flow"
    return None


def sanitize_visual(spec) -> dict | None:
    """Return a clean spec, or None when nothing usable can be recovered from it."""
    if not isinstance(spec, dict):
        return None
    p = spec.get("params")
    # parameters are sometimes written at the top level instead of under "params"
    p = p if isinstance(p, dict) else {k: v for k, v in spec.items() if k not in ("template", "type", "rationale")}
    template = _template_name(spec, p)

    if template == "thales":
        labels = p.get("labels")
        labels = [_str(x, 2, d) for x, d in zip(labels, "ABC")] if isinstance(labels, list) and len(labels) == 3 else list("ABC")
        return {
            "template": "thales",
            "params": {"ratio": _num(p.get("ratio"), 0.2, 0.8, 0.5), "labels": labels, "right_angle": bool(p.get("right_angle", False))},
        }

    if template == "function_plot":
        kind = p.get("kind") if p.get("kind") in ("linear", "quadratic", "sine", "exponential") else "quadratic"
        defaults = {"linear": (1, 0, 0), "quadratic": (1, 0, -4), "sine": (2, 1, 0), "exponential": (1, 0.5, 0)}[kind]
        return {
            "template": "function_plot",
            "params": {
                "kind": kind,
                "a": _num(p.get("a"), -5, 5, defaults[0]),
                "b": _num(p.get("b"), -5, 5, defaults[1]),
                "c": _num(p.get("c"), -5, 5, defaults[2]),
            },
        }

    if template == "process_flow":
        raw = _items(p, "process_flow")
        if not isinstance(raw, list):
            return None
        steps = []
        for item in raw[:7]:
            if isinstance(item, str):
                label, _, detail = item.partition(":")
                item = {"label": label, "detail": detail}
            if isinstance(item, dict) and _pick(item, LABEL_KEYS, 40):
                steps.append({"label": _pick(item, LABEL_KEYS, 40), "detail": _pick(item, DETAIL_KEYS, 140)})
        return {"template": "process_flow", "params": {"cycle": bool(p.get("cycle", False)), "steps": steps}} if len(steps) >= 3 else None

    if template == "concept_map":
        raw = _items(p, "concept_map")
        if not isinstance(raw, list):
            return None
        nodes, ids, by_label = [], set(), {}
        for i, item in enumerate(raw[:9]):
            if isinstance(item, str):
                item = {"label": item}
            if not isinstance(item, dict) or not _pick(item, LABEL_KEYS, 30):
                continue
            nid = _str(item.get("id"), 20, f"n{i}")
            if nid in ids:
                nid = f"{nid}_{i}"
            ids.add(nid)
            label = _pick(item, LABEL_KEYS, 30)
            by_label[label.lower()] = nid
            nodes.append({"id": nid, "label": label, "note": _pick(item, DETAIL_KEYS, 160)})
        if len(nodes) < 3:
            return None
        edges = []
        raw_edges = p.get("edges") if isinstance(p.get("edges"), list) else p.get("links") if isinstance(p.get("links"), list) else []
        for e in raw_edges[:14]:
            if not isinstance(e, dict):
                continue
            a, b = e.get("from", e.get("source")), e.get("to", e.get("target"))
            a = a if a in ids else by_label.get(str(a).lower())
            b = b if b in ids else by_label.get(str(b).lower())
            if a and b and a != b:
                edges.append({"from": a, "to": b, "label": _str(e.get("label") or e.get("relation"), 20)})
        if not edges:
            edges = [{"from": nodes[0]["id"], "to": n["id"], "label": ""} for n in nodes[1:]]
        return {"template": "concept_map", "params": {"nodes": nodes, "edges": edges}}

    if template == "timeline":
        raw = _items(p, "timeline")
        if not isinstance(raw, list):
            return None
        events = []
        for item in raw[:8]:
            if isinstance(item, dict) and _pick(item, LABEL_KEYS, 40):
                events.append({"date": _pick(item, ("date", "year", "time", "when", "period"), 24) or "?", "label": _pick(item, LABEL_KEYS, 40),
                               "detail": _pick(item, DETAIL_KEYS, 160)})
        return {"template": "timeline", "params": {"events": events}} if len(events) >= 3 else None

    if template == "comparison":
        cols = p.get("columns")
        cols = [_str(c, 24) for c in cols[:3]] if isinstance(cols, list) else []
        cols = [c for c in cols if c]
        if len(cols) < 2:
            return None
        raw = p.get("rows")
        if not isinstance(raw, list):
            return None
        rows = []
        for item in raw[:7]:
            if not isinstance(item, dict) or not _str(item.get("label"), 40):
                continue
            cells = item.get("cells")
            cells = [_str(c, 60) for c in cells[: len(cols)]] if isinstance(cells, list) else []
            cells += [""] * (len(cols) - len(cells))
            rows.append({"label": _str(item.get("label"), 40), "cells": cells})
        return {"template": "comparison", "params": {"columns": cols, "rows": rows}} if len(rows) >= 2 else None

    return None


def describe_figure(visual: dict | None) -> tuple[str, list[str]]:
    """Plain-text description of a validated figure, and the focus keys a step may point at.

    The explanation writer (compact model) only sees this text, so every step can name the part of the
    figure it is about without the writer having to design or understand the drawing itself.
    """
    if not visual:
        return "No figure is available. Leave every focus empty.", []
    p, t = visual["params"], visual["template"]
    if t == "thales":
        a, b, c = p["labels"]
        keys = ["AB", "AC", "BC", "AD", "AE", "DE", "ADE"]
        return (
            f"A triangle {a}{b}{c} with a line DE parallel to {b}{c} (D on {a}{b}, E on {a}{c}); the learner can drag D. "
            "Focus keys: AB, AC, BC (sides of the big triangle), AD, AE, DE (segments), ADE (the small triangle).",
            keys,
        )
    if t == "function_plot":
        return (
            f"The graph of a {p['kind']} function with sliders for a, b and c. Focus keys: a, b, c (a slider) and roots (where the curve meets the x-axis).",
            ["a", "b", "c", "roots"],
        )
    if t == "process_flow":
        steps = p["steps"]
        lines = "; ".join(f"{i} = {s['label']}" for i, s in enumerate(steps))
        return f"A {'cycle' if p['cycle'] else 'process'} of {len(steps)} stages. Focus keys are the stage numbers: {lines}.", [str(i) for i in range(len(steps))]
    if t == "timeline":
        events = p["events"]
        lines = "; ".join(f"{i} = {e['date']}: {e['label']}" for i, e in enumerate(events))
        return f"A timeline of {len(events)} events in order. Focus keys are the event numbers: {lines}.", [str(i) for i in range(len(events))]
    if t == "comparison":
        rows = p["rows"]
        lines = "; ".join(f"{i} = {r['label']}" for i, r in enumerate(rows))
        return f"A comparison of {', '.join(p['columns'])} across {len(rows)} aspects. Focus keys are the row numbers: {lines}.", [str(i) for i in range(len(rows))]
    nodes = p["nodes"]
    lines = "; ".join(f"{n['id']} = {n['label']}" for n in nodes)
    return f"A concept map. Focus keys are node ids: {lines}.", [n["id"] for n in nodes]


def concept_map_from_steps(title: str, steps: list[dict]) -> dict:
    """Deterministic fallback: a hub-and-spoke map built from the explanation itself (no AI call)."""
    nodes = [{"id": "core", "label": _str(title, 30, "Topic"), "note": ""}]
    for i, step in enumerate(steps[:7]):
        text = step["text"]
        label = _str(re.split(r"[.:;,(]| - | — ", text)[0], 28, f"Point {i + 1}")
        nodes.append({"id": f"s{i}", "label": label, "note": _str(text, 160)})
    edges = [{"from": "core", "to": n["id"], "label": ""} for n in nodes[1:]]
    return {"template": "concept_map", "params": {"nodes": nodes, "edges": edges}}


def focus_labels(visual: dict | None) -> list[tuple[str, str]]:
    """(focus key, words that name it) for every part of a figure a step can point at."""
    if not visual:
        return []
    p, t = visual["params"], visual["template"]
    if t == "thales":
        return [(k, k) for k in ("ADE", "AB", "AC", "BC", "AD", "AE", "DE")]
    if t == "function_plot":
        return [("roots", "root roots zero zeros racine racines solution solutions")]
    if t == "process_flow":
        return [(str(i), s["label"]) for i, s in enumerate(p["steps"])]
    if t == "timeline":
        return [(str(i), f"{e['date']} {e['label']}") for i, e in enumerate(p["events"])]
    if t == "comparison":
        return [(str(i), r["label"]) for i, r in enumerate(p["rows"])]
    return [(n["id"], n["label"]) for n in p.get("nodes", [])]


def step_focus(steps: list[str], visual: dict | None) -> list[str]:
    """For each numbered step of a written answer, the figure part it is about ("" when none): segment names
    for Thales (exact, longest first), otherwise the label sharing the most words with the step. No AI call."""
    import re

    labels = focus_labels(visual)
    if not labels:
        return ["" for _ in steps]
    words = lambda s: {w for w in re.findall(r"\w+", s.lower()) if len(w) >= 3}
    out = []
    for step in steps:
        if visual["template"] == "thales":
            found = [(m.start(), -len(k), k) for k, _ in labels for m in [re.search(rf"\b{k}\b", step)] if m]
            hit = min(found)[2] if found else ""   # the part named first in the step
        else:
            sw = words(step)
            best = max(labels, key=lambda kl: len(sw & words(kl[1])))
            hit = best[0] if sw & words(best[1]) else ""
        out.append(hit)
    return out
