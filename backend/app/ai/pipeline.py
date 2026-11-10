"""Study pipeline: who handles which step, per the project's orchestration guidance:
  classification / triage / intent        -> compact
  field extraction / formatting / rewrite -> compact
  short summary of targeted text          -> compact
  reasoning, synthesis, planning          -> mid
  final user-facing writing               -> mid
  fallback when mid-size is saturated     -> compact

  route       compact   classification: subject and level (runs in parallel with `illustrate`)
  illustrate  mid       reasoning: which figure explains the topic, and its parameters
  explain     mid       final user-facing writing: the explanation the learner reads
  repair      compact   fixes malformed JSON (only when needed)
  hint        compact   short targeted text
  filter      compact   field extraction: keeps the few passages that help a material question
  answer      compact   short cited answer written from those passages
  quiz        mid       reasoning/synthesis: plans and drafts the question set
  quiz_check  compact   field extraction/correction: reviews the draft before the learner sees it
  summary     compact   short summary of one session's targeted text
  resume      mid       final user-facing writing: merges topics studied into one revision sheet

Efficiency rules unaffected by the mid/compact split: every mid call still goes through the gateway's
cache (exact + semantic) and in-flight de-duplication, the figure plan is shared per topic and language
across every user and room member, and a saturated mid model falls back to compact automatically.
"""
from __future__ import annotations

import asyncio
import re

from . import prompts
from .gateway import extract_json, AIError, Gateway
from .visuals import concept_map_from_steps, describe_figure, sanitize_visual

LEVELS = ("beginner", "intermediate", "advanced")


def clean_topic(topic: str) -> str:
    topic = re.sub(r"\s+", " ", topic or "").strip()
    if len(topic) < 2:
        raise ValueError("Type a topic of at least two characters.")
    return topic[:160]


def _validate_route(obj: dict) -> dict:
    level = obj.get("level") if obj.get("level") in LEVELS else "beginner"
    return {"subject_area": str(obj.get("subject_area", "other"))[:20], "level": level}


def _validate_figure(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("bad figure")
    visual = sanitize_visual(obj)
    if visual is None:   # unusable: let chat_json run its schema-aware repair once; after that the caller builds a figure itself
        raise ValueError("bad figure")
    return {"visual": visual, "rationale": str(obj.get("rationale", "")).strip()[:200]}


def _validate_explanation(obj: dict) -> dict:
    title = str(obj["title"]).strip()[:120]
    raw_steps = obj["steps"]
    if not title or not isinstance(raw_steps, list):
        raise ValueError("bad explanation")
    steps = []
    for item in raw_steps[:6]:
        if isinstance(item, str):
            item = {"text": item}
        text = str(item.get("text", "")).strip()[:240]
        if text:
            steps.append({"text": text, "focus": str(item.get("focus", ""))[:30]})
    if len(steps) < 2:
        raise ValueError("too few steps")
    return {
        "title": title,
        "summary": str(obj.get("summary", "")).strip()[:300],
        "steps": steps,
        "everyday_example": str(obj.get("everyday_example", "")).strip()[:300],
        "common_mistake": str(obj.get("common_mistake", "")).strip()[:300],
    }


def _validate_quiz(obj: dict) -> list[dict]:
    questions = []
    for q in obj["questions"][:5]:
        options = [str(o).strip()[:140] for o in q["options"]]
        answer = int(q["answer"])
        if len(options) != 4 or not 0 <= answer <= 3 or not str(q["q"]).strip():
            continue
        questions.append({"q": str(q["q"]).strip()[:240], "options": options, "answer": answer, "why": str(q.get("why", "")).strip()[:240]})
    if len(questions) < 3:
        raise ValueError("too few questions")
    return questions


def _summarise(metas) -> dict:
    return {
        "calls": [m.to_dict() for m in metas],
        "cached": all(m.cached for m in metas),
        "degraded": any(m.degraded for m in metas),
    }


async def _together(first, second):
    """Run two model calls at once; if one fails, cancel the other and raise."""
    t1, t2 = asyncio.ensure_future(first), asyncio.ensure_future(second)
    try:
        return await t1, await t2
    except BaseException:
        t1.cancel()
        t2.cancel()
        raise


async def explain(gw: Gateway, topic: str, style: str = "balanced", level: str | None = None, context: list[dict] | None = None, lang: str = "en") -> dict:
    topic, lang = clean_topic(topic), prompts.norm_lang(lang)
    # Reuse a near-identical explanation, but only when it is not grounded in specific course passages.
    sem = getattr(gw, "semcache", None)
    store = None
    if sem is not None and not context:
        hit, store = await sem.fetch(f"explain:{lang}:{style}", topic)
        if hit is not None:
            return {**hit, "meta": {"calls": [], "cached": True, "degraded": False, "semantic": True}}
    (route, m_route), (figure, m_fig) = await _together(
        gw.chat_json("route", "compact", prompts.route_messages(topic), _validate_route, max_tokens=100, schema=prompts.ROUTE_SCHEMA),
        gw.chat_json("illustrate", "mid", prompts.illustrate_messages(topic, lang, context), _validate_figure, max_tokens=600, schema=prompts.FIGURE_SCHEMA),
    )
    route = route or {"subject_area": "other", "level": "beginner"}
    level = level if level in LEVELS else route["level"]
    visual = (figure or {}).get("visual")
    figure_text, focus_keys = describe_figure(visual)

    exp, m_exp = await gw.chat_json(
        "explain", "mid", prompts.explain_messages(topic, style, level, figure_text, lang, context), _validate_explanation, max_tokens=800, schema=prompts.EXPLAIN_SCHEMA
    )
    if exp is None:
        raise AIError("format", "The model's answer could not be read. Rephrase the topic and try again.")

    fallback = visual is None
    if fallback:
        visual = concept_map_from_steps(exp["title"], exp["steps"])
        for i, step in enumerate(exp["steps"][:7]):
            step["focus"] = f"s{i}"           # each step lights up its own node in the fallback map
    else:
        for step in exp["steps"]:
            step["focus"] = step["focus"] if step["focus"] in focus_keys else ""
    grounded_in = sorted({c["title"] for c in context}) if context else []
    result = {
        "topic": topic, "level": level, "style": style, **exp, "visual": visual, "visual_fallback": fallback,
        "figure_note": (figure or {}).get("rationale", ""), "grounded_in": grounded_in, "meta": _summarise([m_route, m_fig, m_exp]),
    }
    if store is not None:
        store(result)
    return result


async def hint(gw: Gateway, topic: str, question: str, level: int, lang: str = "en") -> dict:
    level = max(1, min(3, level))
    text, meta = await gw.chat("hint", "compact", prompts.hint_messages(clean_topic(topic), question[:300], level, prompts.norm_lang(lang)), max_tokens=120)
    return {"level": level, "hint": plain_text(text), "meta": _summarise([meta])}


def plain_text(text: str) -> str:
    """Small models sometimes answer a plain-text request in JSON or a code block: keep only the sentence meant
    for the student (the hint/question/text field), never the raw structure."""
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (text or "").strip())
    obj = extract_json(t) if t[:1] in "{[" else None
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict):
        for key in ("hint", "text", "question", "message", "content", "answer", "explanation"):
            v = obj.get(key)
            if isinstance(v, str) and len(v.strip()) > 3:
                return v.strip()
        strings = [v for v in obj.values() if isinstance(v, str) and len(v.split()) >= 3]
        if strings:
            return max(strings, key=len).strip()
    return re.sub(r"^(hint|indice|تلميح)\s*\d*\s*[:.-]\s*", "", t, flags=re.I).strip()


async def get_quiz(gw: Gateway, topic: str, level: str = "beginner", lang: str = "en") -> tuple[list[dict], dict]:
    topic = clean_topic(topic)
    level = level if level in LEVELS else "beginner"
    lang = prompts.norm_lang(lang)
    # Draft with the mid-size model (synthesis), then revise with the compact model (checking): the
    # reviewer fixes wrong answer keys and weak distractors. It can only keep or improve the quiz.
    draft, m_draft = await gw.chat_json("quiz", "mid", prompts.quiz_messages(topic, level, lang), _validate_quiz, max_tokens=800, schema=prompts.QUIZ_SCHEMA)
    if draft is None:
        raise AIError("format", "The quiz could not be generated. Try again.")
    metas = [m_draft]
    questions = draft
    if getattr(gw, "s", None) is not None and gw.s.quiz_verify:
        revised, m_review = await gw.chat_json("quiz_check", "compact", prompts.quiz_review_messages(topic, draft, lang), _validate_quiz, max_tokens=800, schema=prompts.QUIZ_SCHEMA)
        metas.append(m_review)
        if revised is not None and len(revised) >= 3:
            questions = revised
    return questions, _summarise(metas)


async def summarise_session(gw: Gateway, topic: str, transcript: str, lang: str = "en") -> dict:
    """Compact recap of a study-room conversation (saved with the room)."""
    text, meta = await gw.chat("summary", "compact", prompts.summary_messages(topic or "this session", transcript, prompts.norm_lang(lang)), max_tokens=350)
    return {"summary": text.strip(), "meta": _summarise([meta])}


async def revision_sheet(gw: Gateway, topics: list[str], lang: str = "en") -> dict:
    """Mid-size revision sheet across the topics a learner has studied (final user-facing writing, student-triggered)."""
    topics = [t for t in topics if t][:12]
    if not topics:
        raise ValueError("Study a few topics first, then generate a revision sheet.")
    text, meta = await gw.chat("resume", "mid", prompts.resume_messages(topics, prompts.norm_lang(lang)), max_tokens=700)
    return {"sheet": text.strip(), "topics": topics, "meta": _summarise([meta])}


def public_quiz(questions: list[dict]) -> list[dict]:
    return [{"q": q["q"], "options": q["options"]} for q in questions]


def _keep_validator(n: int):
    def validate(obj: dict) -> list[int]:
        keep = [int(k) for k in obj["keep"]]
        keep = [k for k in dict.fromkeys(keep) if 1 <= k <= n][:3]
        if not keep:
            raise ValueError("empty selection")
        return keep

    return validate


NOT_FOUND = "I could not find this in the material. Try different words, or add the page that covers it."


async def ask_material(gw: Gateway, question: str, candidates: list[dict], min_sim: float, lang: str = "en", cache_key: str | None = None) -> dict:
    """Narrow before generating: similarity search (done by the caller) -> compact filter -> one mid-size answer.

    Cost rules: no relevant passage means no model call at all; three or fewer relevant passages skip the
    compact filter; the compact model only ever sees the few passages that survived.
    """
    question = clean_topic(question)
    sem = getattr(gw, "semcache", None)
    store = None
    if sem is not None and cache_key:  # cache_key carries the collection and a version, so new material invalidates reuse
        hit, store = await sem.fetch(f"ask:{lang}:{cache_key}", question)
        if hit is not None:
            return {**hit, "meta": {"calls": [], "cached": True, "degraded": False, "semantic": True}}
    relevant = [c for c in candidates if c["score"] >= min_sim]
    empty_meta = {"calls": [], "cached": False, "degraded": False}
    if not relevant:
        return {"question": question, "answer": NOT_FOUND, "sources": [], "used": [], "grounded": False, "meta": empty_meta}

    metas = []
    if len(relevant) > 3:
        keep, m_filter = await gw.chat_json("filter", "compact", prompts.filter_messages(question, relevant), _keep_validator(len(relevant)), max_tokens=60)
        metas.append(m_filter)
        relevant = [relevant[i - 1] for i in (keep or [1, 2, 3])]

    answer, m_answer = await gw.chat("answer", "compact", prompts.answer_messages(question, relevant, prompts.norm_lang(lang)), max_tokens=400)
    metas.append(m_answer)
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer) if 1 <= int(n) <= len(relevant)})
    sources = [{"n": i, "title": c["title"], "snippet": c["text"][:320], "score": round(c["score"], 2)} for i, c in enumerate(relevant, 1)]
    result = {"question": question, "answer": answer.strip(), "sources": sources, "used": used, "grounded": True, "meta": _summarise(metas)}
    if store is not None:
        store(result)
    return result
