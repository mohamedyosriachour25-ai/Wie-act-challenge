"""Study-space tutor: one learner turn, delivered as a sequence of partial results.

The inference service answers each request whole (guide: no streaming; "loading indication, staged
feedback or partial results"), so latency is hidden by STAGES rather than tokens. Each stage is pushed
to the browser the moment it completes, so the learner never faces an empty screen:

  1. intent      compact  what the learner wants, rewritten as a standalone question using the
                          conversation (resolves "it", "why?", "another one")          ~1 small call
  2. sources     -        retrieval over THIS space's material, and in parallel a declared Wikipedia
                          lookup (used only when the material does not cover the question, or for
                          reference facts) -- narrow before generating                 no AI call
  3. draft       compact  draft-then-revise: a short first answer, visible within seconds
  4. final       mid      final user-facing writing: corrects and completes the draft from the sources
  5. figure      mid      planning: which figure and its parameters; started right after step 2, in
                          parallel with 3-4, and shown when it lands (concept-map fallback, no AI call)

Greetings/off-topic ("chat") get one compact reply and no figure: no teaching content, so no mid call.
Every step goes through the gateway (cache, in-flight merge, quota wait, 503 -> compact fallback).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncIterator, Awaitable, Callable

from . import prompts
from .gateway import AIError, Gateway
from .pipeline import LEVELS, _validate_figure, _summarise
from .visuals import concept_map_from_steps, step_focus

MAX_MESSAGE = 800
RELATIVE_CUT = 0.5    # a passage scoring under half the best one is noise for this question
MATERIAL_K = 4
WEB_K = 2


def clean_message(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()
    if len(text) < 2:
        raise ValueError("Type a question of at least two characters.")
    return text[:MAX_MESSAGE]


def _validate_intent(obj: dict) -> dict:
    intent = obj.get("intent") if obj.get("intent") in prompts.INTENTS else "question"
    standalone = " ".join(str(obj.get("standalone", "")).split())[:300]
    if len(standalone) < 2:
        raise ValueError("bad intent: no standalone question")
    return {
        "intent": intent,
        "standalone": standalone,
        "topic": " ".join(str(obj.get("topic", "")).split())[:80] or standalone[:80],
        "level": obj.get("level") if obj.get("level") in LEVELS else "beginner",
        "needs_web": bool(obj.get("needs_web")),
        "figure": bool(obj.get("figure")),
    }


def default_intent(message: str) -> dict:
    return {"intent": "question", "standalone": message[:300], "topic": message[:80], "level": "beginner", "needs_web": False, "figure": False}


_CITE = re.compile(r"\s*\[\d+\]")
_MD = re.compile(r"[*_`#]+")


def numbered_steps(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s*\d+[.)]\s+(.+)$", text or "", re.M)]


_META_LINE = re.compile(
    r"^\s*(\*\*)?(sources?|références?|references|note|pointer to the figure|see figure|figure|why this figure|"
    r"illustration|disclaimer|remarque)(\*\*)?\s*[:：]", re.I)


def clean_answer(text: str) -> str:
    """Remove what a small model sometimes adds around the lesson: a code fence or JSON wrapper, a pasted
    'Sources:' list (the app shows sources itself), notes about figures it cannot see, placeholders."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    if t[:1] == "{":
        from .gateway import extract_json
        obj = extract_json(t)
        if isinstance(obj, dict):
            t = next((v for k in ("answer", "text", "content", "explanation", "response") if isinstance(v := obj.get(k), str)), t)
    t = re.sub(r"^(here is|here's|voici|this is)\b[^\n]{0,40}(answer|réponse|explanation|explication)[^\n]*:\s*\n", "", t, flags=re.I)
    out, skipping = [], False
    for line in t.splitlines():
        if _META_LINE.match(line):
            skipping = bool(re.match(r"^\s*(\*\*)?(sources?|références?|references)", line, re.I))   # a source list runs on
            continue
        if skipping and (re.match(r"^\s*([-*•]|\[?\d+\]?[.)]?\s)", line) or not line.strip()):
            continue
        skipping = False
        line = re.sub(r"\[(?:not available|not provided|figure|insert)[^\]]*\]", "", line, flags=re.I)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_BOLD = re.compile(r"\*\*([^*]{2,60})\*\*")


def answer_items(text: str) -> list[str]:
    """The points an answer is built from: its numbered list, else its bullets, else its bold key terms."""
    items = [m.group(1) for m in re.finditer(r"^\s*\d+[.)]\s+(.+)$", text or "", re.M)]
    if len(items) < 2:
        items = [m.group(1) for m in re.finditer(r"^\s*[-*•]\s+(.+)$", text or "", re.M)]
    if len(items) < 2:
        items = list(dict.fromkeys(m.group(1).strip() for m in _BOLD.finditer(text or "")))
    return items


def figure_from_text(text: str, topic: str = "", concepts: list | None = None) -> dict | None:
    """Deterministic concept map (no AI call) when the figure model gave nothing usable: from the answer's own
    key points, else from the key concepts found when the course document was analysed."""
    title = next((ln.lstrip("#").strip() for ln in (text or "").splitlines() if ln.startswith("#")), "") or topic
    clean = lambda s: _MD.sub("", _CITE.sub("", s)).strip()
    steps = []
    for item in answer_items(text):
        bold = _BOLD.search(item)
        label = bold.group(1) if bold else re.split(r"\s[-–—:]\s|:\s", item, maxsplit=1)[0]
        steps.append({"text": clean(label) + (": " + clean(item[len(bold.group(0)):].lstrip(" :–—-")) if bold else "")} if bold else {"text": clean(item)})
    if len(steps) < 2 and concepts:
        steps = [{"text": (c.get("name", "") + (": " + c["simple"] if c.get("simple") else "")) if isinstance(c, dict) else str(c)} for c in concepts[:7]]
    steps = [st for st in steps if st["text"]]
    if not title or len(steps) < 2:
        return None
    return concept_map_from_steps(clean(title), steps)


MID_SOURCES = 4   # enough room for the passage that lists a whole set (all the layers, all the stages)


def narrow_sources(sources: list[dict], draft: str) -> list[dict]:
    """Narrow before the mid-size call (guide pattern): the compact draft has already read every passage and
    cited the ones that answer the question; the mid-size model gets those (plus the best material passage),
    at most MID_SOURCES. A shorter prompt is also a faster answer on CPU inference. Numbers stay unchanged,
    so citations in the final answer still point at the sources the learner sees."""
    if not sources:
        return []
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", draft or "")}
    best = next((s for s in sources if s["kind"] == "material"), sources[0])
    keep = [s for s in sources if s["n"] in cited or s is best] if cited else sources[: MID_SOURCES - 1 if draft else MID_SOURCES]
    return keep[:MID_SOURCES]


def wants_figure(intent: dict) -> bool:
    return intent["intent"] == "explain" or (intent["figure"] and intent["intent"] in ("question", "follow_up"))


async def run_turn(
    *,
    gw: Gateway,
    message: str,
    summary: str,
    recent: list[dict],
    style: str,
    lang: str,
    retrieve: Callable[[str], Awaitable[list[dict]]],
    min_similarity: float,
    web=None,
    use_web: bool = True,
    quick_draft: bool = True,
    personal_data: Callable[[str], str | None] | None = None,
    known_intent: dict | None = None,
    library: list[dict] | None = None,
    figures: bool = True,
) -> AsyncIterator[dict]:
    """Yield events: stage, intent, sources, draft, final, visual, done. AIError on a fatal failure."""
    lang = prompts.norm_lang(lang)
    t0 = time.perf_counter()
    metas = []
    elapsed = lambda: int((time.perf_counter() - t0) * 1000)

    # 1. intent (skipped when the caller already knows it, e.g. a study room's "explain the topic") ----
    if known_intent is not None:
        intent = {**default_intent(message), **known_intent}
    else:
        yield {"type": "stage", "stage": "understanding"}
        try:
            intent, m_int = await gw.chat_json(
                "intent", "compact", prompts.intent_messages(message, summary, recent, library), _validate_intent,
                max_tokens=160, schema=prompts.INTENT_SCHEMA, temperature=0.0,
            )
            metas.append(m_int)
        except AIError as exc:
            if exc.kind == "quota":
                raise
            intent = None
        intent = intent or default_intent(message)
    yield {"type": "intent", **intent}
    query = intent["standalone"]

    # 2. sources: this space's material, and a speculative web lookup in parallel ---------------
    yield {"type": "stage", "stage": "reading"}
    web_ok = bool(use_web and web is not None and intent["intent"] != "chat" and not (personal_data and personal_data(query)))
    web_task = asyncio.ensure_future(web.search(query, lang, WEB_K)) if web_ok else None
    try:
        found = await retrieve(query)
    except AIError:
        found = []
    # adaptive relevance: above the absolute floor AND not far below the best passage of this search
    floor = max(min_similarity, (found[0]["score"] if found else 0.0) * RELATIVE_CUT)
    material = [c for c in found if c["score"] >= floor][:MATERIAL_K]
    web_hits: list[dict] = []
    if web_task is not None:
        if intent["needs_web"] or not material:
            web_hits = await web_task
        else:
            web_task.cancel()     # the material covers it: the learner's own course wins
    sources = [{"n": i, "kind": "material", "title": c["title"], "text": c["text"], "score": round(c["score"], 2)} for i, c in enumerate(material, 1)]
    sources += [{"n": len(material) + i, "kind": "web", "title": w["title"], "text": w["text"], "url": w["url"]} for i, w in enumerate(web_hits, 1)]
    yield {"type": "sources", "sources": [_public_source(s) for s in sources]}

    level, convo = intent["level"], prompts._conversation(summary, recent)
    # document-level context for the writers: the analysed documents the passages come from (else the 3 newest)
    titles = {s["title"] for s in sources if s["kind"] == "material"}
    lib = [d for d in (library or []) if d["title"] in titles] or (library or [])[:3]
    chat_only = intent["intent"] == "chat"

    # The figure is drawn FROM the finished answer (step 5), not in parallel with it: drawn separately, the
    # figure and the text disagreed (a 4-layer model in the text, 5 boxes in another order in the figure).
    fig_task = None
    want_fig = figures and not chat_only and wants_figure(intent)

    try:
        # 3. quick draft (compact) ----------------------------------------------------------------
        draft = ""
        if chat_only or quick_draft:
            yield {"type": "stage", "stage": "drafting"}
            try:
                draft, m_draft = await gw.chat(
                    "draft", "compact", prompts.draft_messages(intent["intent"], query, style, level, lang, convo, sources, lib),
                    max_tokens=220, temperature=0.3,
                )
                metas.append(m_draft)
                draft = clean_answer(draft)
                if draft:
                    gw.metrics.record(gw.metrics.first_text_ms, elapsed())
                    yield {"type": "draft", "text": draft}
            except AIError as exc:
                if chat_only:
                    raise
                draft = ""   # no draft: the mid-size answer below is still written

        # 4. final answer (mid), unless this is small talk -----------------------------------------
        final, degraded = draft, False
        if not chat_only:
            yield {"type": "stage", "stage": "writing"}
            try:
                final, m_final = await gw.chat(
                    "tutor", "mid", prompts.tutor_messages(intent["intent"], query, style, level, lang, convo, narrow_sources(sources, draft), draft, lib),
                    max_tokens=700, temperature=0.3,
                )
                metas.append(m_final)
                final = clean_answer(final) or draft
            except AIError:
                if not draft:
                    raise
                final, degraded = draft, True     # keep the draft rather than show nothing
            if not final:
                raise AIError("format", "The model returned an empty answer. Try rephrasing.")
            gw.metrics.record(gw.metrics.final_text_ms, elapsed())
            yield {"type": "final", "text": final, "draft_kept": degraded}

        if want_fig:
            ctx = [{"title": s["title"], "text": s["text"]} for s in narrow_sources(sources, final) if s["kind"] == "material"] or None
            fig_task = asyncio.ensure_future(gw.chat_json(
                "illustrate", "mid", prompts.illustrate_messages(intent["topic"], lang, ctx, answer=final, library=lib),
                _validate_figure, max_tokens=600, schema=prompts.FIGURE_SCHEMA))

        # 5. figure ---------------------------------------------------------------------------------
        visual, fallback, note = None, False, ""
        if fig_task is not None:
            if not fig_task.done():
                yield {"type": "stage", "stage": "illustrating"}
            try:
                figure, m_fig = await fig_task
                metas.append(m_fig)
                visual, note = (figure or {}).get("visual"), (figure or {}).get("rationale", "")
            except AIError:
                visual = None
            if visual is None:   # the figure was wanted: build one from the answer or the document's concepts
                concepts = [c for d in (library or []) for c in d.get("concepts", [])]
                visual, fallback, note = figure_from_text(final, intent["topic"], concepts), True, ""
            if visual is not None:
                links = step_focus(numbered_steps(final), visual)   # clicking a step lights up its part of the figure
                yield {"type": "visual", "visual": visual, "fallback": fallback, "note": note, "step_focus": links}
    finally:
        if fig_task is not None and not fig_task.done():
            fig_task.cancel()

    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", final) if 1 <= int(n) <= len(sources)})
    meta = _summarise(metas)
    meta.update({"ms": elapsed(), "degraded": meta["degraded"] or degraded})
    yield {
        "type": "done",
        "result": {
            "intent": intent, "text": final, "sources": [_public_source(s) for s in sources], "used": used,
            "visual": visual, "visual_fallback": fallback, "figure_note": note, "meta": meta,
            "step_focus": step_focus(numbered_steps(final), visual) if visual else [],
        },
    }


def _public_source(s: dict) -> dict:
    out = {"n": s["n"], "kind": s["kind"], "title": s["title"], "snippet": " ".join(s["text"].split())[:320]}
    if s["kind"] == "web":
        out["url"] = s["url"]
    else:
        out["score"] = s["score"]
    return out
