# MASTER HANDOVER PROMPT — paste this into a new AI coding session

You are taking over an existing, working software project. **You are continuing it, not starting over.** Inspect the real code before changing anything, preserve working functionality, do not rewrite subsystems, and validate every change against the existing test gates.

## The project
**HerwieGO — GenAI Peer Learning Companion**, a web learning platform for the IEEE WIE ACT 5.0 challenge. An AI tutor gives explanations with an **interactive SVG illustration beside the text**, plus quizzes, hints, spaced-repetition flashcards, a revision sheet, RAG over uploaded course material, study rooms (2–10 people), and public "Live" focus rooms with a shared pomodoro timer, a WebRTC camera wall, live chat, private messages, and per-player parallel break games (Quiz / Mime / Pictionary). UI is in English, French, Arabic and Darija (RTL). Accounts are pseudonymous.

## Stack
- **Frontend:** React 18 + Vite 5 + react-router 6, hand-written CSS with design tokens, custom i18n (`t`/`tx`/`tr`), in-house SVG illustration renderers. Entry `frontend/src/App.jsx`; hard logic in `frontend/src/lib/hooks.js`.
- **Backend:** FastAPI + uvicorn (Python 3.11+), REST under `/api/*` + WebSockets `/ws/rooms/{code}` and `/ws/phone-tree/{token}`. Entry `backend/app/main.py` (`app = create_app()`). Routers: `auth, routes_study, materials, rooms, phonetree, review`. Auth = scrypt + HMAC-signed bearer token (the same HMAC scheme also signs the anonymous pocket-tree link token).
- **DB/storage:** SQLite (WAL), thin wrapper `backend/app/db.py` (no ORM); images/PDFs and chunk embeddings stored as BLOBs; similarity in Python.
- **AI:** a single **gateway** (`backend/app/ai/gateway.py`) is the only caller of an OpenAI-compatible inference service — there is no built-in fake/mock service in the shipped app; `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` must point at a real, working endpoint or the app refuses to start. Model routing (revised 9 Nov to match the project's orchestration guidance): **mid-size = reasoning/synthesis/planning + final user-facing writing (figure choice, the explanation text, the quiz draft, the resume); compact = classification/triage, field extraction/formatting, short summaries, hints, material answers, and the fallback when mid-size is saturated.**

## Architecture (one process)
React SPA ⇄ FastAPI (REST + WS) ⇄ AI gateway (routing, exact+semantic cache, retries, quota wait, 503→compact fallback, metrics) ⇄ real inference service (mandatory, no fallback). The backend also serves the built SPA (`frontend/dist`). Room state and rate limiters are in memory; study-room transcripts persist to SQLite; Live-room/game state is ephemeral.

## Current status
Feature-complete prototype wired to a **real AI service only** (no mock, no fallback). As of 10 Nov (Lot 15, with Lots 13–14 merged in the same tree): **233 backend pytest tests pass** against the test-only fake transport (`backend/tests/_fake_service.py`, not part of the shipped app), `npm run i18n:check` is green (590 sentences × FR/AR/Darija), `npm run build` works, and the 14 Playwright suites pass against a local stand-in service (not yet against the real key). Recent lots: study spaces with the staged tutor (SSE, compact draft → mid-size final, memory, declared Wikipedia), RAG (embedding cache, hybrid BM25, adaptive cut, one-time document summaries; local vector store), room Q&A on the tutor, step→figure links, **room competition**, **scoring v2** (study minutes, working days, capped social play), **voice**, PWA icons, **PDF OCR**, and **generations that survive leaving the page or a restart** (the teacher dashboard was removed at the owner's request). The spec book has priority over other wishes (e.g. no upstream token streaming). See `PROJECT_TECHNICAL_DOCUMENTATION.md` §4 "Lot 15 additions" and §15.

## What is NOT done (your work, in owner-priority order)
1. ~~Camera focus/attention detector~~ — **DONE** (opt-in, on-device; `frontend/src/lib/attention.js`, `useAttention` in `hooks.js`, `AttentionCheck` in `Live.jsx`; thresholds still to be tuned on a real webcam).
2. ~~Study forest room + QR code~~ — **DONE, then replaced on 10 Nov by a forest inside every live room (see 2d)** (a view over Live room `LIVE-FOREST`; server-side `Tree` logic in `rooms.py`, `pages/Forest.jsx`, inline-SVG QR; QR needs a phone-reachable URL).
2b. ~~Pocket tree (anti-phone-distraction QR/link)~~ — **DONE, 9 Nov.** Independent of the study-forest tree above: `GET /api/rooms/{code}/phone-tree` mints a signed, anonymous link; the standalone `/tree/:token` page (no app header/nav, desktop-compatible for local testing in a private window) tracks its own tab visibility over `/ws/phone-tree/{token}`; hidden/closed > 60 s wilts it, sends a private `phone_tree_alert` to the owner's *desktop* room connection ("stay focused"), and logs the finished tree into the room's shared forest. Server logic + token signing: `backend/app/phonetree.py` (tests: `backend/tests/test_phonetree.py`). Frontend: `pages/PhoneTree.jsx`, `components/PocketTreePanel.jsx` (QR/link panel + alert toast, wired into both `pages/Live.jsx` and `pages/Forest.jsx`). No Playwright E2E yet — test manually via a private/incognito window per the user's own plan (no second physical device needed).
2d. ~~No pre-study quiz; a forest in every live room; one QR + copy-invite; live room built around the camera wall + study notepad~~ — **DONE, 10 Nov** (see `claude/herwiego-app-status.md`, Lot 7).
2c. ~~Remove mock/placeholder AI, widen illustrations~~ — **DONE, 10 Nov.** In-app mock AI service deleted with no fallback (real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` now mandatory everywhere); UI placeholder/example content removed (`Study.jsx` example chips, mitochondria/Thales placeholder text, `AIStatus.jsx`'s mock-only resilience-demo buttons); two new illustration templates added (`timeline`, `comparison`) so history/comparison topics get a fitting diagram instead of being forced into the generic concept-map template. Scoped to these two templates rather than a fully unconstrained AI-drawing system, given the same-day deadline.
3. (Optional) Karaoke as a 4th game, traditional/public-domain or user-supplied lyrics only.
4. Web-search toggle (needs an external search API), further illustration templates/free-form AI drawing, smart-tutor "explain/simplify/teach" + transcription, login active-recall (1/3/7-day).
5. Report ≤6 pages + business-model note — **deferred by the owner; do not start unless asked.**

## Reference docs (read them first)
In `docs/handover/`: `PROJECT_TECHNICAL_DOCUMENTATION.md` (full details, API table, ER diagram, decisions), `PROJECT_STRUCTURE.txt` (annotated tree), `DEVELOPMENT_ROADMAP.md` (phased plan), `env.example.txt` (config). The live status note is `claude/herwiego-app-status.md` (project knowledge base).

## Run & validate
```bash
# backend
cd backend && python -m venv venv && source venv/bin/activate   # PS: .\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
# set HERWIEGO_BASE_URL and HERWIEGO_API_KEY to a real, working OpenAI-compatible endpoint first — the app refuses to start without them, no mock/fallback
python -m uvicorn app.main:app --reload --port 8000
# frontend
cd ../frontend && npm install && npm run dev                    # http://localhost:5173 → proxies to :8000
# gates (must stay green) — backend tests use their own test-only fake HTTP transport, no real key needed for pytest
cd ../backend && python -m pytest -q
cd ../frontend && npm run i18n:check && npm run build
# E2E (start server first, with real credentials set): cd e2e && python run.py   (needs playwright + chromium)
```
Health check: `curl http://127.0.0.1:8000/api/health` → `{"ok":true}`.

## Coding conventions & hard constraints (do not violate)
- **Inspect before editing.** Read `app/rooms.py`, `frontend/src/pages/Live.jsx`, `frontend/src/lib/hooks.js`, `app/config.py`, `app/db.py` and the technical doc; confirm the code matches the doc before changing it.
- **The gateway is the only code that talks to the inference service.** Keep it so.
- **No mock/fake AI in the shipped app, and no fallback.** The in-app mock service was deleted on 10 Nov at the owner's request; don't add one back. `backend/tests/_fake_service.py` is test-only (injected via `create_app(ai_transport=...)`) and must never be reachable from the running app.
- **Keep the current model routing split** (mid = reasoning/synthesis/final writing; compact = classification/extraction/short summaries/fallback — see §14 of the technical doc). Do not revert to the old "mid=figures only" rule without the owner's say-so.
- **i18n is enforced** — wrap every new UI string with `t`/`tx` and add it to `src/locales/{fr,ar,darija}.js`; the build fails on missing OR unused keys.
- **Live-room game messages are scoped to a game's players** via `broadcast_game` — preserve the scoping or parallel games cross-talk.
- **No new heavy infra** (Redis/Postgres/ORM/SFU) unless there is a scaling reason; this is deliberately single-process + SQLite.
- **Preserve data:** patches to the owner are a single zip of changed files excluding `venv/.env/herwiego.db/node_modules/dist`.
- **Test every change:** add a backend pytest test and/or an E2E check; keep all gates green; update `docs/handover/*` and `claude/herwiego-app-status.md` after significant changes.
- **Deadline:** Phase 1 (report, repo, demo) is 10 November 2026; the report is deferred by the owner.

## Your first actions
1. Read the four key files above + `PROJECT_TECHNICAL_DOCUMENTATION.md`; note any discrepancy between code and docs.
2. Run the test/i18n/build/E2E gates to confirm a green baseline.
3. Try the camera attention check by hand on a real webcam; tune `ATTENTION` in `attention.js` if needed.
4. Open a live room on two devices/tabs, scan the room-forest QR from a phone (or open "Open my tree on this device" in a private window), then pick the next owner-priority item (optional karaoke, web-search toggle, …), update i18n and the handover docs.

Behave as an engineer continuing this project: minimal, surgical changes; preserve working modules; validate and document.
