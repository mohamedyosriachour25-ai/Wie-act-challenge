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


def sanitize_visual(spec) -> dict | None:
    """Return a clean spec, or None when the template is unknown or unusable."""
    if not isinstance(spec, dict):
        return None
    template = spec.get("template")
    p = spec.get("params")
    p = p if isinstance(p, dict) else {}

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
        raw = p.get("steps")
        if not isinstance(raw, list):
            return None
        steps = []
        for item in raw[:7]:
            if isinstance(item, str):
                item = {"label": item}
            if isinstance(item, dict) and _str(item.get("label"), 40):
                steps.append({"label": _str(item.get("label"), 40), "detail": _str(item.get("detail"), 140)})
        return {"template": "process_flow", "params": {"cycle": bool(p.get("cycle", False)), "steps": steps}} if len(steps) >= 3 else None

    if template == "concept_map":
        raw = p.get("nodes")
        if not isinstance(raw, list):
            return None
        nodes, ids = [], set()
        for i, item in enumerate(raw[:9]):
            if not isinstance(item, dict) or not _str(item.get("label"), 30):
                continue
            nid = _str(item.get("id"), 20, f"n{i}")
            if nid in ids:
                nid = f"{nid}_{i}"
            ids.add(nid)
            nodes.append({"id": nid, "label": _str(item.get("label"), 30), "note": _str(item.get("note"), 160)})
        if len(nodes) < 3:
            return None
        edges = []
        for e in (p.get("edges") if isinstance(p.get("edges"), list) else [])[:14]:
            if isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids and e.get("from") != e.get("to"):
                edges.append({"from": e["from"], "to": e["to"], "label": _str(e.get("label"), 20)})
        if not edges:
            edges = [{"from": nodes[0]["id"], "to": n["id"], "label": ""} for n in nodes[1:]]
        return {"template": "concept_map", "params": {"nodes": nodes, "edges": edges}}

    if template == "timeline":
        raw = p.get("events")
        if not isinstance(raw, list):
            return None
        events = []
        for item in raw[:8]:
            if isinstance(item, dict) and _str(item.get("label"), 40):
                events.append({"date": _str(item.get("date"), 24, "?"), "label": _str(item.get("label"), 40), "detail": _str(item.get("detail"), 160)})
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
        label = _str(re.split(r"[.:;,]", text)[0], 28, f"Point {i + 1}")
        nodes.append({"id": f"s{i}", "label": label, "note": _str(text, 160)})
    edges = [{"from": "core", "to": n["id"], "label": ""} for n in nodes[1:]]
    return {"template": "concept_map", "params": {"nodes": nodes, "edges": edges}}
