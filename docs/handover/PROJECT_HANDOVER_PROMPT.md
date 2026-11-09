# MASTER HANDOVER PROMPT — paste this into a new AI coding session

You are taking over an existing, working software project. **You are continuing it, not starting over.** Inspect the real code before changing anything, preserve working functionality, do not rewrite subsystems, and validate every change against the existing test gates.

## The project
**EdNova — GenAI Peer Learning Companion**, a web learning platform for the IEEE WIE ACT 5.0 challenge. An AI tutor gives explanations with an **interactive SVG illustration beside the text**, plus quizzes, hints, spaced-repetition flashcards, a revision sheet, RAG over uploaded course material, study rooms (2–10 people), and public "Live" focus rooms with a shared pomodoro timer, a WebRTC camera wall, live chat, private messages, and per-player parallel break games (Quiz / Mime / Pictionary). UI is in English, French, Arabic and Darija (RTL). Accounts are pseudonymous.

## Stack
- **Frontend:** React 18 + Vite 5 + react-router 6, hand-written CSS with design tokens, custom i18n (`t`/`tx`/`tr`), in-house SVG illustration renderers. Entry `frontend/src/App.jsx`; hard logic in `frontend/src/lib/hooks.js`.
- **Backend:** FastAPI + uvicorn (Python 3.11+), REST under `/api/*` + a WebSocket `/ws/rooms/{code}`. Entry `backend/app/main.py` (`app = create_app()`). Routers: `auth, routes_study, materials, rooms, review`. Auth = scrypt + HMAC-signed bearer token.
- **DB/storage:** SQLite (WAL), thin wrapper `backend/app/db.py` (no ORM); images/PDFs and chunk embeddings stored as BLOBs; similarity in Python.
- **AI:** a single **gateway** (`backend/app/ai/gateway.py`) is the only caller of an OpenAI-compatible inference service. A built-in **fake service** (`ai/mock_api.py`/`mock_llm.py`) runs in-process so everything works with no key. Model routing (revised 9 Nov to match the project's orchestration guidance): **mid-size = reasoning/synthesis/planning + final user-facing writing (figure choice, the explanation text, the quiz draft, the resume); compact = classification/triage, field extraction/formatting, short summaries, hints, material answers, and the fallback when mid-size is saturated.**

## Architecture (one process)
React SPA ⇄ FastAPI (REST + WS) ⇄ AI gateway (routing, exact+semantic cache, retries, quota wait, 503→compact fallback, metrics) ⇄ inference service (fake by default). The backend also serves the built SPA (`frontend/dist`). Room state and rate limiters are in memory; study-room transcripts persist to SQLite; Live-room/game state is ephemeral.

## Current status
Feature-complete prototype on the fake AI service. **132 backend pytest tests pass**, `npm run i18n:check` is green (479 sentences × FR/AR/Darija), `npm run build` works, and Playwright suites (`run.py`, `game.py`, `resume.py`, …) pass. Done recently: revision-sheet button, live chat, private messages, mime-guessing via chat, and a rework of break games into **per-player choice + parallel play + reset each break**, with **Pictionary replacing Karaoke** (copyright on lyrics) on the same turn-game engine.

## What is NOT done (your work, in owner-priority order)
1. ~~Camera focus/attention detector~~ — **DONE** (opt-in, on-device; `frontend/src/lib/attention.js`, `useAttention` in `hooks.js`, `AttentionCheck` in `Live.jsx`; thresholds still to be tuned on a real webcam).
2. ~~Study forest room + QR code~~ — **DONE** (a view over Live room `LIVE-FOREST`; server-side `Tree` logic in `rooms.py`, `pages/Forest.jsx`, inline-SVG QR; QR needs a phone-reachable URL).
3. (Optional) Karaoke as a 4th game, traditional/public-domain or user-supplied lyrics only.
4. Web-search toggle (needs an external search API), real-time subject-accurate illustrations, smart-tutor "explain/simplify/teach" + transcription, login active-recall (1/3/7-day).
5. Report ≤6 pages + business-model note — **deferred by the owner; do not start unless asked.**

## Reference docs (read them first)
In `docs/handover/`: `PROJECT_TECHNICAL_DOCUMENTATION.md` (full details, API table, ER diagram, decisions), `PROJECT_STRUCTURE.txt` (annotated tree), `DEVELOPMENT_ROADMAP.md` (phased plan), `env.example.txt` (config). The live status note is `claude/ednova-app-status.md` (project knowledge base).

## Run & validate
```bash
# backend
cd backend && python -m venv venv && source venv/bin/activate   # PS: .\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000             # fake AI by default
# frontend
cd ../frontend && npm install && npm run dev                    # http://localhost:5173 → proxies to :8000
# gates (must stay green)
cd ../backend && python -m pytest -q
cd ../frontend && npm run i18n:check && npm run build
# E2E (start server first): cd e2e && python run.py   (needs playwright + chromium)
```
Health check: `curl http://127.0.0.1:8000/api/health` → `{"ok":true,"mock":true}`.

## Coding conventions & hard constraints (do not violate)
- **Inspect before editing.** Read `app/rooms.py`, `frontend/src/pages/Live.jsx`, `frontend/src/lib/hooks.js`, `app/config.py`, `app/db.py` and the technical doc; confirm the code matches the doc before changing it.
- **The gateway is the only code that talks to the inference service.** Keep it so.
- **Keep the current model routing split** (mid = reasoning/synthesis/final writing; compact = classification/extraction/short summaries/fallback — see §14 of the technical doc). Do not revert to the old "mid=figures only" rule without the owner's say-so.
- **i18n is enforced** — wrap every new UI string with `t`/`tx` and add it to `src/locales/{fr,ar,darija}.js`; the build fails on missing OR unused keys.
- **Live-room game messages are scoped to a game's players** via `broadcast_game` — preserve the scoping or parallel games cross-talk.
- **No new heavy infra** (Redis/Postgres/ORM/SFU) unless there is a scaling reason; this is deliberately single-process + SQLite.
- **Preserve data:** patches to the owner are a single zip of changed files excluding `venv/.env/ednova.db/node_modules/dist`.
- **Test every change:** add a backend pytest test and/or an E2E check; keep all gates green; update `docs/handover/*` and `claude/ednova-app-status.md` after significant changes.
- **Deadline:** Phase 1 (report, repo, demo) is 10 November 2026; the report is deferred by the owner.

## Your first actions
1. Read the four key files above + `PROJECT_TECHNICAL_DOCUMENTATION.md`; note any discrepancy between code and docs.
2. Run the test/i18n/build/E2E gates to confirm a green baseline.
3. Try the camera attention check by hand on a real webcam; tune `ATTENTION` in `attention.js` if needed.
4. Open the forest (`/forest`) on two devices/tabs, scan the QR from a phone, then pick the next owner-priority item (optional karaoke, web-search toggle, …), update i18n and the handover docs.

Behave as an engineer continuing this project: minimal, surgical changes; preserve working modules; validate and document.
