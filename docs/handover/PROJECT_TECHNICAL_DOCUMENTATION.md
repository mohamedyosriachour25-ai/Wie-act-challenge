# EdNova — Project Technical Documentation & Development Handover

> Generated 2026-11-08 by inspecting the actual repository (`ednova/`). Grounded in source, not in conversation.
> Companion files in `docs/handover/`: `PROJECT_HANDOVER_PROMPT.md`, `DEVELOPMENT_ROADMAP.md`, `env.example.txt`, `PROJECT_STRUCTURE.txt`.
> Verified: `python -m pytest -q` → **132 passed**; `npm run i18n:check` → **all translations present (479 sentences)**; `npm run attention:test` → **9 passed**; `npm run qr:test` → **4 passed**; `npm run build` → OK; Playwright suites `run.py`, `game.py`, `camera.py`, `resume.py`, `lang.py`, `attention.py`, `forest.py`, `draw.py` → PASS.

---

## 1. Project overview

**EdNova — GenAI Peer Learning Companion.** A web learning platform built for the **IEEE WIE ACT 5.0** challenge (Phase 1 deliverables: report ≤6 pages, business-model note, GitHub repo, demo — deadline **10 November 2026**). The report and business-model note are intentionally deferred by the project owner.

**Purpose.** Give a learner an AI tutor whose every explanation comes with an **interactive illustration beside the text**, plus tools to practice (quizzes, hints, spaced-repetition flashcards, a revision sheet) and to study socially (study rooms of 2–10, and public "Live" focus rooms with a shared pomodoro timer, cameras, chat and break games).

**Target users.** Students (secondary/university). Accounts are **pseudonymous** (a pseudo + password, no email).

**Problems it addresses.** Passive text-only tutoring; studying alone/unmotivated; forgetting (spaced repetition); the cost/latency/quota of large models (handled by a routing + caching gateway).

**Core functionalities.**
- Solo study: a conversational tutor — explanation + live SVG figure + 3-level hint ladder + self-quiz.
- Learning-style diagnostic that tunes the explanation style.
- RAG: upload PDFs/images/text as course material; answers can be grounded in it.
- Revision: quizzes become SM-2 flashcards; a student-triggered **revision sheet** recaps studied topics.
- Study rooms (2–10): shared explanation/quiz, chat, saved as a conversation with an AI summary.
- Live public rooms: shared pomodoro clock, presence + focus signal, reactions, **camera wall (WebRTC mesh)**, **live chat**, **private messages**, and **break games** (Quiz / Mime / Pictionary) with **per-player choice and parallel play**.
- Multilingual UI: English, French, Arabic, Darija (with RTL).

**Architecture approach.** React/Vite SPA ⇄ FastAPI (REST + WebSocket) ⇄ an **AI gateway** that is the only component talking to the inference service. A built-in **fake inference service** (OpenAI-compatible) runs in-process so the whole app works end-to-end with no external key; switching to the real service is a `.env` change. Single-process by design (SQLite, in-memory room state).

---

## 2. Complete technology stack

### Frontend
- **Framework:** React 18.3 (`react`, `react-dom`). Language: modern JS/JSX (ES modules).
- **Build tool / dev server:** Vite 5.4 (`@vitejs/plugin-react`). Dev server proxies `/api` and `/ws` to `:8000` (`vite.config.js`).
- **Routing:** `react-router-dom` 6.26 (`src/App.jsx`).
- **State management:** React hooks + a small Auth context (`src/lib/auth.jsx`); no Redux. Custom hooks in `src/lib/hooks.js`.
- **API communication:** `fetch` wrapper with bearer token (`src/lib/api.js`); WebSocket for rooms.
- **Styling:** a single hand-written `src/styles.css` with design tokens (no Tailwind/MUI). Fonts via `@fontsource/*` (Familjen Grotesk, Atkinson Hyperlegible, Noto Kufi/Naskh Arabic).
- **i18n:** custom `t()/tx()/tr()` (`src/lib/i18n.jsx`) + English-keyed dicts in `src/locales/{fr,ar,darija}.js`. `scripts/i18n-check.mjs` enforces coverage (build fails on missing OR unused keys).
- **Illustrations:** in-house SVG renderers in `src/visuals/` (no chart lib).
- **On-device face model (opt-in attention check):** `@vladmandic/human` 3.3.6 (MIT), loaded lazily by dynamic `import()` only when the learner opts in (separate ~1.6 MB chunk). Only BlazeFace + FaceMesh are used; their weights (~2 MB) are self-hosted in `frontend/public/models/` so no third-party download happens.
- **QR code (study forest invite):** `qrcode-generator` ^1 (MIT, no dependencies). Only its boolean module matrix is used (`frontend/src/lib/qr.js`); `components/QrCode.jsx` draws it as inline SVG (no canvas, no image, CSP-safe), always dark on white with a 4-module quiet zone. `jsqr` (dev only) decodes it in `npm run qr:test`.

### Backend
- **Framework:** FastAPI ~0.162 on **uvicorn[standard]** ~0.54. Language: **Python 3.11+** (CI runs 3.12 and 3.13; Docker uses 3.13).
- **API architecture:** REST under `/api/*` + a WebSocket at `/ws/rooms/{code}`. Routers split by domain (`auth`, `routes_study`, `materials`, `rooms`, `review`).
- **Auth/authorization:** scrypt password hashing; **HMAC-SHA256-signed bearer token** (stateless, `app/auth.py`). No external IdP.
- **Business logic:** `app/ai/pipeline.py` (study steps), `app/rooms.py` (rooms/games), `app/materials.py` (RAG), `app/review.py` (SM-2).
- **Middleware:** custom `security.Guard` (body-size cap, rate limiting, access log with secret redaction).
- **Background services:** asyncio tasks — document indexing (resumed on startup via lifespan), turn-game drivers, presence scheduling.
- **HTTP client:** `httpx` (gateway → inference service; in mock mode via in-process ASGI transport).
- **PDF:** `pypdf` (text extraction for RAG).

### Database & storage
- **Engine:** **SQLite** (WAL mode), file at `EDNOVA_DB` (default `ednova.db`; Docker `/data/ednova.db`).
- **Client/ORM:** none — a thin hand-written wrapper `app/db.py` (parameterised SQL, a global lock, `row_factory=Row`). Schema created on boot via `executescript`; two lightweight in-code migrations (`ALTER TABLE` for `docs.kind`, `rooms.summary/summary_at`).
- **File storage:** uploaded images/PDFs stored **as BLOBs in SQLite** (`blobs` table), served via `/api/materials/{id}/raw`. No external object store.
- **Vectors:** chunk embeddings stored as BLOBs in `chunks.emb`; similarity computed in Python (cosine). No vector DB.

### External services & integrations
- **AI/LLM:** an OpenAI-compatible inference service (the challenge service). **Built-in fake** (`app/ai/mock_api.py` + `mock_llm.py`) used by default and by all tests. Three model classes: **compact** (classification/triage, field extraction/formatting, short targeted summaries, fallback — `route`, `filter`, `answer`, `hint`, `quiz_check`, `summary`), **mid-size** (reasoning/synthesis/planning + final user-facing writing — `illustrate`, `explain`, `quiz`, `resume`), **embeddings** (RAG + semantic cache).
- **Auth providers / email / payments:** none.
- **WebRTC:** peer-to-peer camera/mic mesh; signalling relayed through the app's WebSocket. No TURN/STUN configured (works on LAN/same-NAT; see limits).

---

## 3. Project file structure

See `PROJECT_STRUCTURE.txt` for the full annotated tree. Key points:

- **Entry point:** `backend/app/main.py` → `create_app()` builds everything; module-level `app = create_app()` is what `uvicorn app.main:app` imports. It also **serves the built SPA** (`frontend/dist`) when present, so one process can host the whole app.
- **Config:** `backend/app/config.py` — a frozen `Settings` dataclass; `load_settings()` reads env (auto-loads `backend/.env`). Nothing environment-specific is hard-coded.
- **Routers (backend/app/):** `auth.py`, `routes_study.py`, `materials.py`, `rooms.py`, `review.py`. There is **no separate controller/service/model layer** — each router file holds its routes + logic; cross-cutting helpers live in `security.py`, `i18n.py`, `db.py`.
- **AI subsystem (backend/app/ai/):** `gateway.py` (the only caller of the service), `pipeline.py` (study steps + JSON validation), `prompts.py` (prompt builders + schemas + few-shot), `visuals.py` (figure validation), `semcache.py` (semantic cache), `mock_api.py`/`mock_llm.py` (fake service).
- **Frontend pages (frontend/src/pages/):** `Home`, `Study` (solo tutor), `Rooms` (lobby), `Room` (study room), `Live` (lobby) + `LiveRoom`, `Forest` (study forest + QR invite), `Review`.
- **Frontend libs:** `lib/hooks.js` holds the hard parts — `useRoomSocket`, `useCycle` (epoch-aligned pomodoro), `useVideoMesh` (WebRTC perfect-negotiation), `useFocusSignal`, `useAIStatus`.
- **Illustration renderers:** `src/visuals/*` one per template, dispatched by `VisualSheet.jsx`.
- **Tests:** `backend/tests/` (pytest, 132 tests) and `e2e/` (Playwright, run against a live server).

---

## 4. System architecture & data flow

```mermaid
flowchart LR
  subgraph Browser["React SPA (Vite build)"]
    UI[Pages: Home / Study / Rooms / Live / Review]
    VIS[Illustration renderers]
    WS1[useRoomSocket / useVideoMesh]
  end
  subgraph Server["FastAPI (single process)"]
    MW[Guard middleware: body cap, rate limit, access log]
    AUTH[auth.py]
    STUDY[routes_study.py]
    MAT[materials.py]
    ROOMS[rooms.py REST + WS + Hub]
    REV[review.py]
    GW[ai/gateway.py]
    SC[ai/semcache.py]
    DB[(SQLite WAL)]
  end
  subgraph AI["Inference service"]
    MOCK[Built-in fake service\nmock_api + mock_llm]
    REAL[Real challenge service]
  end
  UI -- REST /api --> MW --> AUTH & STUDY & MAT & ROOMS & REV
  WS1 -- WebSocket /ws/rooms/{code} --> ROOMS
  STUDY & MAT & REV & ROOMS --> GW
  GW --> SC
  GW -- httpx (in-proc ASGI when mock) --> MOCK
  GW -- httpx https --> REAL
  AUTH & STUDY & MAT & ROOMS & REV --> DB
  VIS --- UI
```

**Frontend/backend communication.** REST for everything stateless (bearer token in `Authorization`); a single WebSocket per room for real-time (presence, chat, DM, reactions, timer, games, WebRTC signalling). Media (camera/mic) is peer-to-peer; only signalling passes through the server.

**Auth flow.** `POST /api/auth/register|login` → scrypt-verify → return an HMAC-signed token (`<body>.<sig>`). Client stores it in `localStorage` and sends `Authorization: Bearer <token>`. `current_user()` verifies the signature and loads the user. The WebSocket passes the token in the query string (so the server's own access log is disabled; the app writes a redacted log instead).

**AI request flow (example: explain).** `POST /api/study/explain` → `security.spend_ai()` (per-user rate limit) → optional RAG retrieval from the learner's material → `pipeline.explain()` → `gateway.chat()`:
1. exact cache → semantic cache (`semcache`) → in-flight merge (dedupe concurrent identical calls);
2. route to **mid** (figure) and **compact** (text) as the step requires;
3. concurrency cap, retries, **quota wait** (429/Retry-After up to `max_quota_wait_s`, else a friendly error), **503→compact fallback**;
4. schema-aware JSON validation/repair (`pipeline` + `visuals`).
Then `log_event()` records it in `events` for progress/revision.

**Live-room game flow (per-player, parallel).** During focus, each client sends `event_optin{game}`; a player belongs to exactly one game. On the break, each chosen game runs in parallel: **trivia** is clock-driven (clients derive the question schedule; server scores `game_result`), **mime/pictionary** are driven by a short server task (`run_turn_game`) per game. Game messages are **scoped to that game's players** (`broadcast_game`). The choice resets each break.

---

## 5. Complete feature inventory

| Feature | Description | Location | Status | Dependencies |
|---|---|---|---|---|
| Pseudonymous auth | register/login/me; scrypt + HMAC token | `app/auth.py`, FE `lib/auth.jsx` | FULLY IMPLEMENTED | `users` table, `EDNOVA_SECRET` |
| Learning-style diagnostic | 4-question quiz sets `users.learning_style` | `routes_study.py` (`/study/diagnostic`), FE `components/Study.jsx` | FULLY IMPLEMENTED | users |
| Solo explain + figure | explanation (compact) + illustration spec (mid) rendered as SVG | `pipeline.explain`, `ai/visuals.py`, FE `pages/Study.jsx`, `visuals/*` | FULLY IMPLEMENTED | gateway, events |
| Hint ladder (3 levels) | progressive hints | `/study/hint`, FE `HintLadder` | FULLY IMPLEMENTED | gateway |
| Self-quiz (+answer/finish) | compact draft + compact review; answer check; finish logs score | `/study/quiz*`, FE `SoloQuiz` | FULLY IMPLEMENTED | gateway, events |
| Progress panel | topics, mastery, streak | `/progress`, FE `ProgressPanel` | FULLY IMPLEMENTED | events |
| Revision sheet (student-triggered) | per-topic recap from recent `events` (compact) | `/study/resume`, `pipeline.revision_sheet`, FE `pages/Study.jsx` RevisionSheet | FULLY IMPLEMENTED (tests + E2E `resume.py`) | events, gateway |
| Spaced repetition (SM-2) | quizzes → flashcards; grade/due/stats | `app/review.py`, FE `pages/Review.jsx` | FULLY IMPLEMENTED | `cards` table |
| RAG materials | upload PDF/image/text, background index, similarity search | `app/materials.py`, FE `components/Materials.jsx` | FULLY IMPLEMENTED | `docs`/`chunks`/`blobs`, embeddings |
| Grounded answers | answer strictly from material; personal-data guard | `/materials/ask`, `pipeline.ask_material` | FULLY IMPLEMENTED | embeddings |
| Study rooms (2–10) | shared explain/quiz, chat (anon option), saved conversation + AI summary | `app/rooms.py` (kind=group), FE `pages/Room.jsx` | FULLY IMPLEMENTED | rooms, room_messages |
| Live rooms: pomodoro + presence + focus signal | epoch-aligned clock; focus computed on device (no camera) | `rooms.py`, FE `useCycle`, `useFocusSignal` | FULLY IMPLEMENTED | — |
| Live camera wall | opt-in WebRTC mesh (video + performer audio), cap `video_capacity` | `rooms.py` (rtc_*), FE `useVideoMesh` | FULLY IMPLEMENTED (E2E `camera.py`) | WebRTC; no TURN |
| Live chat | room-wide text chat | `rooms.py` live branch `chat`, FE `RoomChat` | FULLY IMPLEMENTED (test + run.py) | — |
| Private messages | DM to one named member (sender+recipient only) | `rooms.py` `dm`, FE `RoomChat` selector | FULLY IMPLEMENTED (test) | — |
| Break games: per-player choice + parallel | each player picks Quiz/Mime/Pictionary; same-game players play together; reset each break | `rooms.py` events model, FE `pages/Live.jsx` | FULLY IMPLEMENTED (tests + E2E `game.py`) | — |
| Game: Quiz (trivia race) | clock-driven MCQ, scored leaderboard | `rooms.py` `game_result`, FE `GroupGame` | FULLY IMPLEMENTED | — |
| Game: Mime | turn-based; perform on camera; others guess in chat; performer's client judges | `rooms.py` `run_turn_game`/`handle_turn_guess`, FE `TurnGame` | FULLY IMPLEMENTED (E2E) | camera, chat |
| Game: Pictionary | like mime but "draw it and show the camera" | same engine as mime | FULLY IMPLEMENTED (backend test) | camera, chat |
| AI status / metrics | model-usage banner, metrics endpoint, simulate | `/ai/status`,`/ai/metrics`,`/ai/simulate`, FE `AIStatus` | FULLY IMPLEMENTED | gateway |
| i18n EN/FR/AR/Darija + RTL | enforced coverage | `i18n.py`, FE `lib/i18n.jsx`, `locales/*` | FULLY IMPLEMENTED | — |
| Semantic cache / draft-then-revise | reuse near-duplicate requests; review quiz before display | `ai/semcache.py`, `pipeline.get_quiz` | FULLY IMPLEMENTED | embeddings |
| Real inference service | swap mock → real via `.env` | `ai/gateway.py` | IMPLEMENTED BUT NOT TESTED against the real endpoint | org key/URL |
| Karaoke | — | — | REMOVED (replaced by Pictionary; copyright on lyrics) | — |
| Camera focus/attention detector | opt-in, on-device: face found + head pose → "facing the screen" yes/no, feeds the existing focus boolean; camera released outside focus phase; off by default | `frontend/src/lib/attention.js`, `useAttention` + `useFocusSignal(enabled, camera)` in `lib/hooks.js`, `AttentionCheck` in `pages/Live.jsx`, models in `frontend/public/models/` | FULLY IMPLEMENTED (unit tests `scripts/attention-test.mjs`, E2E `attention.py`) | getUserMedia, `@vladmandic/human` |
| Study forest room + QR code | a view over a Live room (`LIVE-FOREST`, 25/5): while the member's own focus boolean is true during a focus phase a tree grows; unfocused/disconnected time counts as "away" and the tree wilts after a grace (min(30 s, 25 % of focus)); a tree still alive at the end of the focus phase is planted and stays in the forest. Opt-in per member (`forest_join`). An invite panel shows a QR (inline SVG) + copyable URL of the room | BE `app/rooms.py` (`Tree`, `Room.update_trees/tree_view/_finish_tree`, `announce_trees`, `ensure_forest_task`); FE `pages/Forest.jsx`, `components/Tree.jsx`, `components/QrCode.jsx`, `lib/qr.js`; route `/forest/:code`; entry card in `pages/Live.jsx` | FULLY IMPLEMENTED (pytest `test_forest.py` 19 tests, `scripts/qr-test.mjs`, E2E `forest.py`) | `qrcode-generator` |
| Web-search toggle | online references alongside material | — | PLANNED (needs external search API) | — |
| Real-time subject-accurate illustrations | model-emitted diagram spec for any topic (beyond 4 templates) | — | PLANNED | — |
| Login active-recall (1/3/7-day) | animated recall cards on login | — | PLANNED (builds on `cards`) | — |
| Report ≤6 pages + business-model note | challenge deliverables | — | PLANNED (deferred by owner) | — |

**User roles.** One role: an authenticated learner. Authorization is **ownership-based** (a user sees/edits only their own material, their own `cards`, rooms they belong to). Study rooms have an `owner_id`; there is no admin/teacher role yet.

---

## 6. Database documentation

SQLite, created on boot from `SCHEMA` in `app/db.py`. All timestamps are REAL (epoch seconds). No real ORM; access via the `DB` wrapper.

```mermaid
erDiagram
  users ||--o{ events : logs
  users ||--o{ cards : owns
  users ||--o{ docs : owns
  docs  ||--o{ chunks : "split into"
  docs  ||--|| blobs : "image/pdf bytes"
  rooms ||--o{ room_messages : "saved transcript"

  users { int id PK; text pseudo UK; text pw_hash; text salt; text learning_style; real created_at }
  events { int id PK; int user_id FK; real ts; text kind; text topic; int correct; int total }
  cards { int id PK; int user_id FK; text topic; text front; text back; real due_at; real interval_days; real ease; int reps; int lapses; real created_at }
  docs { int id PK; text collection; text title; int owner_id FK; text status; int chunks_total; int chunks_done; text message; real created_at; text kind }
  chunks { int id PK; int doc_id FK; int idx; text text; blob emb }
  room_messages { int id PK; text code; text kind; text author; text text; real created_at }
  blobs { int doc_id PK FK; text mime; blob data }
  rooms { text code PK; text kind; text name; text topic; int capacity; int owner_id; int is_public; int focus_min; int break_min; real created_at; text summary; real summary_at }
```

**Notes & constraints.**
- `users.pseudo` is `UNIQUE COLLATE NOCASE`.
- `cards` is de-duplicated per learner by `UNIQUE(user_id, front)`.
- Indexes: `events(user_id, ts)`, `cards(user_id, due_at)`, `docs(collection)`, `chunks(doc_id)`, `room_messages(code, id)`.
- **Relationships are not enforced by SQLite FKs** (no `FOREIGN KEY` clauses / `PRAGMA foreign_keys` is off); integrity is enforced in application code. (Technical debt — see §10.)
- **Migrations** are ad-hoc `ALTER TABLE` guards in `DB.__init__` (added `docs.kind`, `rooms.summary`, `rooms.summary_at`). There is no migration framework; new columns must follow the same pattern.
- **Seed data:** `seed_live_rooms()` inserts 5 public Live rooms: `LIVE-DEEP` (25/5), `LIVE-LONG` (50/10), `LIVE-SPRINT` (15/3), `LIVE-FOREST` (25/5, the study forest), `LIVE-DEMO` (1/1, the fast demo/E2E room). The forest room is hidden from the plain Live list and offered through its own card.
- `events.kind` values seen: `explain`, `quiz`, `group_quiz`, `resume`. `room_messages.kind`: `chat | explanation | answer | system`. `docs.status`: `waiting | indexing | ready | error` (see materials.py). `docs.kind`: `text | image`.
- **Game state is NOT in the DB** — it lives in memory on the `Hub`/`Room` objects and is lost on restart (acceptable: games are ephemeral per break).

---

## 7. API documentation

All JSON. Auth = HMAC bearer token unless noted. Errors use FastAPI `HTTPException` with translated messages; AI errors return a structured body (`detail`, `kind`, `retry_after`) with status 429/502/503. Language is taken from an `Accept-Language`-style header/param via `lang_of()`.

### Auth (`/api/auth`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| POST | `/register` | no | `{pseudo, password}` | `{token, ...user}` |
| POST | `/login` | no | `{pseudo, password}` | `{token, ...user}` |
| GET | `/me` | yes | — | current user |

### Study (`/api`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| GET | `/study/diagnostic` | yes | — | diagnostic questions |
| POST | `/study/diagnostic` | yes | answers → sets `learning_style` | updated user |
| POST | `/study/explain` | yes | `{topic, level?}` | `{topic, level, explanation, figure, ...}` (grounded in material if any) |
| POST | `/study/hint` | yes | `{topic, question, level}` | hint text |
| POST | `/study/quiz` | yes | `{topic, level?, lang?}` | quiz (answers stripped for the public form) |
| POST | `/study/quiz/answer` | yes | `{topic, level, lang?, question, choice}` | correctness + why |
| POST | `/study/quiz/finish` | yes | `{topic, correct, total}` | logs score; may add flashcards |
| POST | `/study/resume` | yes | — | `{sheet, topics, meta}` revision sheet from recent events |
| GET | `/progress` | yes | — | topics, mastery, streak |
| GET | `/ai/status` | yes | — | model availability banner |
| GET | `/ai/metrics` | yes | — | gateway metrics |
| POST | `/ai/simulate` | yes | `{mode}` | test hook to simulate AI conditions |

### Materials / RAG (`/api/materials`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| GET | `` (list) | yes | `?scope=me\|room:<code>` | documents + status |
| POST | `/file` | yes | raw body; `X-Filename` header; `?scope&title` | doc (indexing continues in background) |
| DELETE | `/{doc_id}` | yes | `?scope` | ok |
| GET | `/{doc_id}/raw` | yes* | `?scope&token` | original bytes (image/PDF); token in query so `<img>`/`<iframe>` can load it |
| POST | `/ask` | yes | `{scope, question, ...}` | grounded answer (refuses personal-data extraction) |

### Rooms (`/api`, `/ws`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| GET | `/api/rooms` | yes | — | group rooms the user can see |
| POST | `/api/rooms` | yes | `{name(2–40), topic, capacity(2–10), is_public}` | new room `{code,...}` |
| GET | `/api/rooms/{code}` | varies | — | room info (used by FE + E2E) |
| GET | `/api/rooms/{code}/conversation` | yes | — | saved transcript + summary (study rooms) |
| WS | `/ws/rooms/{code}?token=&lang=` | token in query | see protocol below | realtime stream |

### Review (`/api/review`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| POST | `/add_quiz` | yes | quiz → flashcards (deduped) | ok |
| GET | `/due` | yes | — | cards due now |
| POST | `/grade` | yes | `{card_id, grade(0–3)}` | next schedule |
| GET | `/stats` | yes | — | counts / upcoming |

### Health
| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/health` | no | `{ok, mock}` |

### WebSocket protocol (`/ws/rooms/{code}`)
Client→server `kind`s handled (see `app/rooms.py`): `topic, explain, ask, quiz_start, quiz_reveal, answer, summarize, chat, dm, reaction, focus, forest_join, draw, timer_start, timer_stop, lang, rtc_join, rtc_leave, rtc_signal, event_optin, event_optout, event_start, game_result, mime_guess|turn_guess`. `draw` is pictionary strokes (op `sync`|`stroke`|`clear`, normalised 0..1 points); `event_optout` drops the player from every game this break.
Server→client message `type`s include: `snapshot, presence, chat, dm, reaction, explanation, quiz, answer, summary, rtc_*, event_participants, event_scores, game_start, game_next, game_your_turn, game_over, game_guessed`, `draw` (op `stroke`|`clear`|`replay` — pictionary strokes to that game's players), `tree` (a tree finished: `{pseudo, cycle, state: grown|wilted}`). `presence` carries `at` (server clock, used by the client to interpolate tree growth) and, for forest members only, a per-member `tree` = `{state, p, avail}`; the `snapshot` of a Live room carries `forest` (last 60 finished trees).
Study rooms (`kind=group`) use the chat/explain/quiz/summarize branch and persist to `room_messages`; Live rooms (`kind=live`) use the reactions/chat/dm/rtc/games branch and are **not** persisted.

**Error handling.** AI failures → `ai_error_response()` maps `AIError.kind` to 429/502/503 with `Retry-After`. Validation → 400 with a translated message. Rate limit → 429 from `security.Guard`/`SlidingWindow`. Not-found/ownership → 404/403.

**Not yet present / to confirm.** Real-service quota status/header names are assumed (429 + `Retry-After`, any header containing "remaining"); confirm with organisers. No endpoints yet for the planned features (web-search toggle, login active-recall).

---

## 8. Environment configuration & dependencies

- **Runtime:** Python 3.11+ (CI 3.12/3.13; Docker 3.13). Node 18+ (CI/Docker use Node 22). Package managers: `pip`, `npm`.
- **Mandatory for a real deployment:** strong `EDNOVA_SECRET`; and if not mock, `EDNOVA_BASE_URL`, `EDNOVA_API_KEY`, `EDNOVA_COMPACT_MODEL`, `EDNOVA_MID_MODEL`, `EDNOVA_EMBED_MODEL`. Everything else is optional with defaults.
- **Database:** SQLite — no server to run; the file is created automatically.
- **Ports:** backend `8000`; Vite dev `5173` (proxies to 8000). Expected URL in production/single-process: `http://127.0.0.1:8000`.
- **Env files:** repo ships `./.env.example` (docker) and `backend/.env.example` (local). A consolidated template is in `docs/handover/env.example.txt`. Config is read by `app/config.py` (auto-loads `backend/.env`).
- **Optional:** the real AI service (optional — defaults to mock), a reverse proxy (`EDNOVA_TRUST_PROXY=1`), Docker.

---

## 9. How to install and run

**Linux/macOS**
```bash
# 1. Backend
cd backend
python -m venv venv && source venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env            # optional; without it the fake AI is used
# 2. Frontend (dev with hot reload)
cd ../frontend
npm install
npm run dev                     # http://localhost:5173  (proxies /api,/ws → :8000)
# in another shell, start the backend:
cd ../backend && source venv/bin/activate
python -m uvicorn app.main:app --reload --port 8000
```

**Windows PowerShell**
```powershell
cd backend
python -m venv venv; .\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
cd ..\frontend
npm install
npm run dev
# another terminal:
cd ..\backend; .\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

**Single process (prod-like): build the SPA, let the backend serve it**
```bash
cd frontend && npm run build          # outputs frontend/dist
cd ../backend && python -m uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

**Docker**
```bash
cp .env.example .env                  # set EDNOVA_SECRET (openssl rand -hex 32)
docker compose up --build             # http://127.0.0.1:8000  (EDNOVA_ENV=production)
```

- **DB creation / migrations / seeding:** automatic on boot (schema + in-code `ALTER` + `seed_live_rooms`). Nothing to run manually.
- **Tests:** `cd backend && python -m pytest -q` (132 tests). i18n: `cd frontend && npm run i18n:check`. E2E: start the server, then `cd e2e && python run.py` (needs Playwright + Chromium; `pip install playwright && playwright install chromium`). The E2E `game.py` waits for a game window in the `LIVE-DEMO` cycle (up to ~1 min) before playing.
- **Verify a service is up:** `curl http://127.0.0.1:8000/api/health` → `{"ok":true,"mock":true|false}`.
- **Common startup errors:** `ModuleNotFoundError: app` → run uvicorn **from `backend/`**. `No module named uvicorn` → wrong venv activated. Production refuses to start with a weak `EDNOVA_SECRET` or missing key (by design). On Windows, a `UnicodeDecodeError` reading UTF-8 files is avoided in-code, but set `PYTHONUTF8=1` if any tooling defaults to cp1252.

---

## 10. Testing, debugging & current health

- **Automated tests:** 132 backend pytest tests across 11 files (auth, orchestration, gateway, ai-optim, materials, features/rooms/games, forest, pictionary-draw, review, hardening, i18n) — all passing. Frontend `i18n:check` green (468 sentences × 3 languages). `npm run attention:test` (9 pure-logic tests for the attention tracker) and `npm run qr:test` (4 tests; decodes the generated QR with jsQR); both run in CI. Playwright E2E suites (`run.py`, `game.py`, `conversation.py`, `camera.py`, `images.py`, `rag.py`, `lang.py`, `review.py`, `resume.py`, `attention.py`, `forest.py`, `draw.py`) — all re-run and passing (`forest.py` takes ~3–4 min on `LIVE-DEMO`; `EDNOVA_QUICK=1` skips its replant step). No coverage % is collected.
- **Known/expected behaviours that look like errors:** the `rag.py` E2E logs a console `400` which is the **intentional** personal-data-extraction refusal.
- **Known limits / technical debt:**
  1. **Single process, in-memory state** (rooms, rate limiters, semantic cache, game state) → no horizontal scaling without Redis/shared state.
  2. **No SFU/TURN** for WebRTC → camera mesh works on LAN/same-NAT; cross-NAT needs a TURN server; wall capped by `video_capacity`.
  3. **No DB foreign keys** — integrity is app-enforced only.
  4. **Mock embeddings are lexical**, so similarity thresholds (`min_similarity`, `sem_threshold*`) must be re-tuned on the real embeddings.
  5. **Real service never exercised** — the single biggest unknown; quota status/header assumptions unconfirmed.
  6. **WS token in URL** (mitigated by disabling uvicorn's access log; the app logs redacted).
  7. **Camera attention check** — thresholds (`ATTENTION` in `attention.js`: yaw 40°, pitch 55°, 8 s turned / 10 s no face before "away") are reasoned, not tuned on real users. Verified on Chromium's fake camera (no face → away) and on a synthetic cartoon face (→ facing the screen); **not yet tried on a real webcam, other devices, glasses/headscarves, or poor light** — do this before the demo. Face detectors can be less reliable for some people, so it is strictly opt-in and a missed detection only changes the shared yes/no dot, never a score. Needs a secure context (HTTPS or localhost) for `getUserMedia`; falls back to the CPU backend when WebGL is unavailable (slower). The strict CSP (no `unsafe-eval`) was confirmed to work with the model.
  8. **Game correctness is client-reported** (mime/pictionary guesses are judged by the performer's own client; cross-language guesses assume one room language).
- **Security posture:** scrypt hashing, signed tokens, body-size cap, rate limits (login/register/AI), production refuses weak secrets and hides `/docs`, access log redacts secrets. No HTTPS termination in-app (expected behind a proxy).

---

## 11. Completed vs remaining

### A. Successfully completed (implemented + verified)
**Camera attention detector (opt-in, on-device)**; **Study forest + QR invite**; Auth; diagnostic; solo explain + figure; hints; quiz (+answer/finish); progress; **revision sheet**; **SM-2 review**; RAG (upload/index/search/ask, images+PDF, personal-data guard); study rooms (chat, saved conversation + summary); Live rooms (pomodoro, presence, focus signal, reactions, **camera wall**, **chat**, **DM**); **break games with per-player choice + parallel play** (Quiz/Mime/Pictionary, mime/pictionary chat-guessing); AI gateway (routing, caches, retries, quota, fallback, metrics); i18n EN/FR/AR/Darija + RTL; Docker + CI.

### B. Partially completed
- **Real inference service**: code path exists and is config-driven, but never run against the real endpoint; quota header/status assumptions unconfirmed. *Files:* `app/ai/gateway.py`, `backend/.env`. *Priority: HIGH.*

### C. Remaining work (not implemented)
| Task | Why | Relevant files (new/affected) | Priority |
|---|---|---|---|
| Karaoke as optional 4th game | owner may still want it, with public-domain/traditional songs | `app/rooms.py` (GROUP_GAMES/TURN_GAMES banks), FE `pages/Live.jsx` | LOW/OPTIONAL |
| Web-search toggle | online references beside material | new provider module + `pipeline`/routes; needs external search API key | MEDIUM |
| Real-time subject-accurate illustrations | generalise beyond 4 templates | `ai/prompts.py` (diagram-spec schema), `ai/visuals.py`, FE `visuals/*` | MEDIUM |
| Smart tutor on "explain/simplify/teach" + transcription | richer tutoring with captions | `pipeline.explain`, FE `pages/Study.jsx` | MEDIUM |
| Login active-recall (1/3/7-day) | distributed practice on login, animated cards | `app/review.py` (schedule intervals), FE new login-review flow | MEDIUM |
| Report ≤6 pages + business-model note | challenge deliverables | docs | HIGH (deadline) but **deferred by owner** |
| Scale-out (Redis/SFU/TURN) | production scale | infra | LOW (post-challenge) |

---

## 12. Development roadmap
See `DEVELOPMENT_ROADMAP.md` for the full phased plan. Summary: **Phase 1** connect + validate the real AI service and re-tune similarity thresholds; **Phase 2** finish partially-done items; **Phase 3** build the next features in owner priority (camera focus detector ✔ → study-forest/QR ✔ → optional karaoke → web-search/illustrations/tutor/active-recall); **Phase 4** testing/perf; **Phase 5** security/prod; **Phase 6** deploy. **Do not rewrite** working subsystems; extend them.

---

## 13. Deployment & production readiness

- **Already configured:** multi-stage `Dockerfile` (node builds SPA → python serves it), `docker-compose.yml` (production env, named volume for the DB, healthcheck), CI that builds the image and asserts `/api/health`. Production mode hides `/docs`, refuses weak secrets, runs as a non-root user, DB on a volume.
- **Needs doing for real prod:** put it behind HTTPS (reverse proxy; set `EDNOVA_TRUST_PROXY=1`); set the real `EDNOVA_*` AI vars; provide a **TURN server** if cross-NAT video is required; back up the SQLite volume; consider moving room/rate-limit/cache state to Redis and video to an SFU **only if** scaling beyond a single process.
- **Frontend hosting:** none separate — the backend serves `frontend/dist`. (A static CDN is optional.)
- **DB hosting:** the SQLite file on a persistent volume; no DB server.

---

## 14. Important development decisions & context (do not reverse)

- **Single process + SQLite + in-memory room state** is a deliberate choice for the challenge (simplicity, zero external infra). Don't introduce Redis/Postgres/an ORM without a scaling reason.
- **The gateway is the only caller of the inference service.** All model calls go through `ai/gateway.py`; keep it that way.
- **Model routing follows the project's orchestration guidance (9 Nov revision):** **mid-size** = reasoning/synthesis/planning and final user-facing writing — figure choice (`illustrate`), the explanation text itself (`explain`), the quiz draft (`quiz`), and the revision sheet (`resume`). **compact** = classification/triage (`route`), field extraction/formatting (`filter`, `quiz_check`), short targeted summaries (`summary`), hints, material answers, and the automatic fallback when mid-size is saturated. This supersedes the earlier "mid = figures only" rule; preserve *this* split going forward unless the owner changes it again.
- **Built-in fake service** mirrors the real API so everything runs/tests without a key. Tests run with `mock_latency_scale=0`.
- **i18n is enforced** (build fails on missing OR unused keys). New UI strings must be wrapped (`t`/`tx`) and added to all three locales. Keys are English sentences.
- **Illustrations are live SVG from a small set of validated templates** (Thales, FunctionPlot, ProcessFlow, ConceptMap), not images; `ai/visuals.py` validates specs. Generalising this is a planned feature, not a rewrite.
- **Camera attention detector is opt-in, on-device, and only a yes/no leaves the device.** No frame is stored, drawn or sent; the camera is opened only during the focus phase and released on the break or when switched off; the face model is self-hosted (no CDN, no new external service to declare). Looking down at notes is deliberately not "away". When the camera says "facing the screen", the 90 s input-idle rule is waived (reading a book is studying).
- **Pictionary is played on a shared canvas, not the camera.** The performer draws on an HTML canvas (`components/DrawBoard.jsx`); strokes are relayed to that game's players as normalised points via `draw` and kept per turn so a late watcher catches up (`draw` op `sync`). Only the current performer may draw; the server never renders, it only relays. Plain canvas 2D, so it runs under the strict CSP.
- **Every break game is opt-in and opt-out.** A player picks one game or **Skip — just rest** during the invite, and can **Leave the game** during the break (`event_optout`); nobody is ever forced into a timed game.
- **A forest tree looks full-grown after `TREE_FULL_S` (180 s) of focus**, but is still only PLANTED at the end of the focus phase, so a 25-min phase reads as "grow it in a few minutes, then keep it alive". Wilt grace and the plant/drop thresholds are unchanged and time-based.
- **Study forest is a view over a Live room, not a new room kind.** No schema change, no new infra: the server keeps one `Tree` per opted-in member per focus phase, fed only by the member's own `focus` boolean (the same signal as the pomodoro focus indicator, including the camera attention check). Forest state is in memory and ephemeral (finished trees are kept in `room.forest`, capped at 200). A tree that wilts having grown less than min(20 s, 5 % of focus) is dropped silently so a brief peek leaves no "wilted" record. Keep `TREE_*` constants in `rooms.py` and `GRACE_S/MIN_S` in `Forest.jsx` in step.
- **Karaoke was removed** because copyrighted lyrics cannot be embedded and public-domain songs make a weak game; **Pictionary** replaced it on the same turn-game engine. Re-adding karaoke must use traditional/public-domain or user-supplied lyrics only.
- **Break games: per-player choice + parallel play + reset each break** was a deliberate rework of the old "first chooser decides" model. Game messages are **scoped** to a game's players (`broadcast_game`) — keep that scoping or parallel games will cross-talk.
- **Patches are delivered as a single zip of changed files** excluding `venv/.env/ednova.db/node_modules/dist`, so the owner's local data survives an overwrite.
- **Report/business-model note are deferred** by the owner — do not spend effort there unless asked.
- **Forest limits to remember:** focus is client-reported (like game results), so a determined user can fake it; leaving the page (tab hidden / idle) wilts the tree after the grace; a QR only helps if the encoded URL is reachable from the scanning device (the page shows a localhost hint); a ticker (every 4 s) runs only while at least one member is in a forest.
- **Open questions:** real-service quota header/status names; embeddings model id and similarity tuning; whether karaoke returns.

---

## 15. Next development session: exact resumption point

1. **Current state:** feature-complete prototype on the fake AI service; 132 backend tests + i18n + key E2E green. Lots 1 (revision sheet, chat, DM, mime-guessing) and 2 (per-player parallel games, Pictionary replacing karaoke) are done and shipped as a cumulative patch.
2. **Last completed implementation:** break-games rework — per-player game choice, parallel play, scoped broadcasts, Pictionary; plus the revision-sheet button and live chat/DM.
3. **Last files modified:** `backend/app/rooms.py`, `backend/tests/test_features.py`, `frontend/src/pages/Live.jsx`, `frontend/src/locales/{fr,ar,darija}.js` (and lot 1: `routes_study.py`, `ai/prompts.py`, `ai/pipeline.py`, `ai/mock_llm.py`, `i18n.py`, `pages/Study.jsx`, `styles.css`, `e2e/game.py`, `e2e/resume.py`).
4. **Last task worked on:** finishing lot 2 and generating this handover documentation.
5. **Immediate next task (owner priority):** the camera attention detector and the **study forest + QR** are DONE; next is the optional **karaoke** 4th game (public-domain / user-supplied lyrics only), then the web-search toggle, illustrations, smart tutor and login active-recall. Before the demo, try the attention check by hand on a real webcam (adjust `ATTENTION`) and open the forest on two real devices (QR scan from a phone needs a URL the phone can reach, not `localhost`).
6. **Inspect before changing anything:** `app/rooms.py` (events/games model, `broadcast_game`, `run_turn_game`, `handle_turn_guess`), `frontend/src/pages/Live.jsx` (`LiveRoomView`, `useVideoMesh`), `frontend/src/lib/hooks.js` (`useCycle`, `useVideoMesh`, `useFocusSignal`), `app/config.py`, `app/db.py` (SCHEMA + seed).
7. **Must be running:** backend `uvicorn app.main:app` on :8000 (fake AI by default); for E2E, Playwright + Chromium and a built `frontend/dist` (or `npm run dev`).
8. **Do NOT modify:** the gateway's role as sole service caller; the model-routing split; the i18n enforcement; the `broadcast_game` scoping; the patch-exclusion convention. Don't rewrite working subsystems.
9. **Known issues to keep in mind:** real service untested; mock embeddings lexical; no TURN for cross-NAT video; game correctness client-reported; `LIVE-DEEP` room state persists across tests in a file run (use a fresh cycle/room and scope assertions to participants, as the current tests do).
10. **How to validate changes:** `cd backend && python -m pytest -q` (keep 132 green), `cd frontend && npm run i18n:check` + `npm run build`, then the relevant Playwright suite against a running server. Add a backend test + an E2E check for each new feature.

**First 5 actions for the next session (prioritised):**
1. Read `app/rooms.py`, `frontend/src/pages/Live.jsx`, `frontend/src/lib/hooks.js`, `app/config.py` and this doc; confirm reality matches it.
2. Run the test + i18n + build + E2E gates to confirm a green baseline.
3. (Done) Camera attention detector: `useAttention` → `useFocusSignal(share, camera)`; see `lib/attention.js`.
4. Try it on a real webcam and tune `ATTENTION` (thresholds + grace periods) if needed.
5. (Done) Study forest + QR: see `pages/Forest.jsx`, `Room.update_trees` in `rooms.py`. Next, scaffold whichever owner-priority item is chosen (karaoke, web-search toggle, …) and update i18n + docs.

---

*End of technical documentation. See `PROJECT_HANDOVER_PROMPT.md` for the copy-paste prompt to start the next AI session.*
