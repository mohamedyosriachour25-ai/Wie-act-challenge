# HerwieGO — Project Technical Documentation & Development Handover

> Generated 2026-11-08 by inspecting the actual repository (`herwiego/`). Grounded in source, not in conversation.
> Companion files in `docs/handover/`: `PROJECT_HANDOVER_PROMPT.md`, `DEVELOPMENT_ROADMAP.md`, `env.example.txt`, `PROJECT_STRUCTURE.txt`.
> Verified: `python -m pytest -q` → **143 passed**; `npm run i18n:check` → **all translations present (495 sentences)**; `npm run attention:test` → **9 passed**; `npm run qr:test` → **4 passed**; `npm run build` → OK; Playwright suites `run.py`, `game.py`, `camera.py`, `resume.py`, `lang.py`, `attention.py`, `forest.py`, `draw.py` → PASS (not re-run for the Lot 3e pocket-tree addition below — see its own backend tests instead).

---

## 1. Project overview

**HerwieGO — GenAI Peer Learning Companion.** A web learning platform built for the **IEEE WIE ACT 5.0** challenge (Phase 1 deliverables: report ≤6 pages, business-model note, GitHub repo, demo — deadline **10 November 2026**). The report and business-model note are intentionally deferred by the project owner.

**Purpose.** Give a learner an AI tutor whose every explanation comes with an **interactive illustration beside the text**, plus tools to practice (quizzes, hints, spaced-repetition flashcards, a revision sheet) and to study socially (study rooms of 2–10, and public "Live" focus rooms with a shared pomodoro timer, cameras, chat and break games).

**Target users.** Students (secondary/university). Accounts are **pseudonymous** (a pseudo + password, no email).

**Problems it addresses.** Passive text-only tutoring; studying alone/unmotivated; forgetting (spaced repetition); the cost/latency/quota of large models (handled by a routing + caching gateway).

**Core functionalities.**
- Solo study: a conversational tutor — explanation + live SVG figure + 3-level hint ladder + self-quiz.
- Learning-style diagnostic that tunes the explanation style (backend only since 10 Nov: the quiz was removed from the UI, so solo study opens straight on the tutor; a style set earlier still applies).
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
- **Styling:** a single hand-written `src/styles.css` with design tokens (no Tailwind/MUI). **Purple theme since 10 Nov:** Primary `#6839F5`, Secondary `#29145E`, Tertiary `#8057FF`, Neutral `#1E1738`; dark gradient header with pill nav, gradient `.page-head` banners, soft white cards. The theme is a token remap plus a "Purple theme layer" block at the end of `styles.css` (no class names changed). Fonts via `@fontsource/*`: Manrope (headings/body) and JetBrains Mono (labels), Latin subsets only; Noto Kufi/Naskh Arabic for RTL. `components/Backdrop.jsx` draws the interactive background (canvas: drifting orbs, a node constellation that links to the pointer, click ripples; pointer-events none, paused when the tab is hidden, a still frame under reduced motion; not shown on the standalone `/tree/:token` page).
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
- **HTTP client:** `httpx` (gateway → the real inference service over real HTTP; no mock transport in the shipped app — tests inject a test-only `httpx.MockTransport` via `create_app(ai_transport=...)`).
- **PDF:** `pypdf` (text extraction for RAG).

### Database & storage
- **Engine:** **SQLite** (WAL mode), file at `HERWIEGO_DB` (default `herwiego.db`; Docker `/data/herwiego.db`).
- **Client/ORM:** none — a thin hand-written wrapper `app/db.py` (parameterised SQL, a global lock, `row_factory=Row`). Schema created on boot via `executescript`; two lightweight in-code migrations (`ALTER TABLE` for `docs.kind`, `rooms.summary/summary_at`).
- **File storage:** uploaded images/PDFs stored **as BLOBs in SQLite** (`blobs` table), served via `/api/materials/{id}/raw`. No external object store.
- **Vectors:** chunk embeddings stored as BLOBs in `chunks.emb`; similarity computed in Python (cosine). No vector DB.

### External services & integrations
- **AI/LLM:** an OpenAI-compatible inference service (the challenge service), real credentials mandatory — **no built-in fake/mock service and no fallback** (removed 10 Nov; `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` must be set to a real, working endpoint or the app refuses to start). The backend test suite uses its own test-only fake HTTP transport (`backend/tests/_fake_service.py`), which is not reachable from the running app. Three model classes: **compact** (classification/triage, field extraction/formatting, short targeted summaries, fallback — `route`, `filter`, `answer`, `hint`, `quiz_check`, `summary`), **mid-size** (reasoning/synthesis/planning + final user-facing writing — `illustrate`, `explain`, `quiz`, `resume`), **embeddings** (RAG + semantic cache).
- **Auth providers / email / payments:** none.
- **WebRTC:** peer-to-peer camera/mic mesh; signalling relayed through the app's WebSocket. No TURN/STUN configured (works on LAN/same-NAT; see limits).

---

## 3. Project file structure

See `PROJECT_STRUCTURE.txt` for the full annotated tree. Key points:

- **Entry point:** `backend/app/main.py` → `create_app()` builds everything; module-level `app = create_app()` is what `uvicorn app.main:app` imports. It also **serves the built SPA** (`frontend/dist`) when present, so one process can host the whole app.
- **Config:** `backend/app/config.py` — a frozen `Settings` dataclass; `load_settings()` reads env (auto-loads `backend/.env`). Nothing environment-specific is hard-coded.
- **Routers (backend/app/):** `auth.py`, `routes_study.py`, `materials.py`, `rooms.py`, `review.py`. There is **no separate controller/service/model layer** — each router file holds its routes + logic; cross-cutting helpers live in `security.py`, `i18n.py`, `db.py`.
- **AI subsystem (backend/app/ai/):** `gateway.py` (the only caller of the service — always talks to the real endpoint, no mock mode), `pipeline.py` (study steps + JSON validation), `prompts.py` (prompt builders + schemas + few-shot), `visuals.py` (figure validation; six templates including the 10 Nov additions `timeline`/`comparison`), `semcache.py` (semantic cache). `mock_api.py`/`mock_llm.py` were deleted 10 Nov; the test-only replacement lives at `backend/tests/_fake_service.py`, outside the shipped `app/` package.
- **Frontend pages (frontend/src/pages/):** `Home`, `Study` (solo tutor), `Rooms` (lobby), `Room` (study room), `Live` (lobby) + `LiveRoom` (camera wall + room forest + study notepad), `Review`, `PhoneTree` (standalone `/tree/:token` pocket-tree page, no app header/nav — see §7's Pocket tree row).
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
    REAL[Real challenge service\nmandatory, no fallback]
  end
  UI -- REST /api --> MW --> AUTH & STUDY & MAT & ROOMS & REV
  WS1 -- WebSocket /ws/rooms/{code} --> ROOMS
  STUDY & MAT & REV & ROOMS --> GW
  GW --> SC
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
| Pseudonymous auth | register/login/me; scrypt + HMAC token | `app/auth.py`, FE `lib/auth.jsx` | FULLY IMPLEMENTED | `users` table, `HERWIEGO_SECRET` |
| Learning-style diagnostic | 4-question quiz sets `users.learning_style` | `routes_study.py` (`/study/diagnostic`) | BACKEND ONLY — UI quiz removed 10 Nov at the owner's request; endpoints kept, an existing style still applies | users |
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
| Account score + tracking | every user's actions tracked (`activity`); points ledger with rules + daily caps, levels, ranks, weekly/all-time leaderboard; shown on Home (`ScorePanel`); test DB generator | `app/scoring.py`, `scripts/test_db.py`, FE `components/Study.jsx` `ScorePanel` | FULLY IMPLEMENTED (pytest `test_scoring.py` 7) | auth, study, games |
| Break games: per-player choice + parallel | each player picks Quiz/Mime/Pictionary; same-game players play together; reset each break | `rooms.py` events model, FE `pages/Live.jsx` | FULLY IMPLEMENTED (tests + E2E `game.py`) | — |
| Game: Quiz (trivia race) + break trivia | clock-driven MCQ (25 s per question); **questions are fresh every break**: written by the compact model (task `trivia`) in the languages of the people in the room, else sampled from a 22-question built-in bank in EN/FR/AR/Darija (options shuffled); the server holds the answers and scores the pick | `app/games.py` (`make_trivia`, `TRIVIA_FALLBACK`), `rooms.py` (`ensure_content`, `game_content`, `game_result`), FE `GroupGame`, `BreakGame` | FULLY IMPLEMENTED (pytest `test_games.py`) | compact model (optional) |
| Game: Mime | turn-based on the camera wall. Everyone gets a **private word up front** and sees the **full order** ("you play right after X"); turn = **60 s in long-break rooms (break ≥ 10 min, e.g. 50/10), else 30 s with 20 s of prep before the first turn**; a turn ends on time, when the performer **passes**, or when **every other player has guessed**. **The server judges chat guesses**: exact (any language, articles ignored) → hidden, scored (+100…150 guesser, +40 performer), announced; **close (≤ 2 characters off, ≤ 1 for short words) → blurred for everyone else + "Close answer!" to the guesser**. Words: compact model (task `game_words`) or a 22-word built-in bank. **Played in rounds until the break ends**: each round every player plays once in a fresh random order (`game_round`; never opening with whoever just played), with a new word each time; a turn is cut to what is left of the break and none starts with under `MIN_TURN_S` (8 s) left. **No two players ever hold the same word**, and no word comes back in a game (`take_word`). **Word swap**: a player can change a hard word up to `MAX_SWAPS` (3) times per round (`word_swap` → `game_word` / `game_swap_denied`), on their own turn only until someone guesses. **While the game runs it takes the middle of the room** (`GameStage`): the performer's camera (mime) or the live board (pictionary) beside the chat; the camera wall comes back at game over. Mime players join the WebRTC mesh **watch-only** (`rtc_join {watch:true}`) so they see the performer without turning their own camera on | `app/games.py` (`judge`, `make_words`, `WORDS_FALLBACK`), `rooms.py` (`turn_timing`, `break_left`, `run_turn_game`, `take_word`, `handle_word_swap`, `judge_chat`, `handle_turn_pass`), FE `GameStage`, `useVideoMesh.watch` | FULLY IMPLEMENTED (pytest `test_games.py` 26; E2E `game.py` 10/10) | camera, chat |
| Game: Pictionary | same engine as mime, drawn on a shared canvas. While waiting, each player sketches their word on a **private practice board** (never sent); when their turn starts the sketch is replayed to the room and they keep drawing live. Swapping the word mid-turn clears the board | same as mime + `components/DrawBoard.jsx` | FULLY IMPLEMENTED (pytest `test_games.py`, `test_draw.py`; E2E `draw.py` 15/15) | chat |
| AI status / metrics | model-usage banner, metrics endpoint | `/ai/status`,`/ai/metrics`, FE `AIStatus` | FULLY IMPLEMENTED | gateway |
| i18n EN/FR/AR/Darija + RTL | enforced coverage | `i18n.py`, FE `lib/i18n.jsx`, `locales/*` | FULLY IMPLEMENTED | — |
| Semantic cache / draft-then-revise | reuse near-duplicate requests; review quiz before display | `ai/semcache.py`, `pipeline.get_quiz` | FULLY IMPLEMENTED | embeddings |
| Real inference service, no fallback | mock/fake service removed 10 Nov; `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` mandatory via `.env` or app refuses to start | `ai/gateway.py`, `ai/config.py` | IMPLEMENTED; NOT YET VERIFIED against a working real endpoint (owner had not checked key status as of 10 Nov) | org key/URL |
| Illustration templates: timeline, comparison | two new figure templates beyond the original four, for history/sequence and side-by-side topics | `ai/visuals.py`, FE `visuals/Timeline.jsx`, `visuals/Comparison.jsx` | FULLY IMPLEMENTED (pytest `test_visuals.py`, 8 tests) | gateway |
| Karaoke | — | — | REMOVED (replaced by Pictionary; copyright on lyrics) | — |
| Camera focus/attention detector | opt-in, on-device: face found + head pose → "facing the screen" yes/no, feeds the existing focus boolean; camera released outside focus phase; off by default | `frontend/src/lib/attention.js`, `useAttention` + `useFocusSignal(enabled, camera)` in `lib/hooks.js`, `AttentionCheck` in `pages/Live.jsx`, models in `frontend/public/models/` | FULLY IMPLEMENTED (unit tests `scripts/attention-test.mjs`, E2E `attention.py`) | getUserMedia, `@vladmandic/human` |
| ~~Study forest room + QR code~~ | **REMOVED 10 Nov** — replaced by a forest in *every* live room (next row). The `LIVE-FOREST` room, `/forest` page and E2E `forest.py` are gone (`/forest/*` redirects to `/live`); the desktop `Tree` logic in `rooms.py` (`forest_join`) is kept but no longer used by the UI | — | REMOVED | — |
| Pocket tree = each live room's forest (anti-phone-distraction) | Every live room has its own forest. One QR per member (minted by `GET /api/rooms/{code}/phone-tree`, signed, anonymous) opens `/tree/:token` on the phone; the tree grows while that page is in front and withers after 60 s away (`PHONE_TREE_GRACE_S`). It follows the room's shared clock: **during breaks it is paused** (phone is free; grace restarts at focus). The phone page shows the room and a live "break in …" countdown; on leaving it during focus it speaks + puts in the tab title + vibrates one of several nudges, e.g. "Don't let your focus wither like a tree — the break is in {time} in {room}." The room sees everyone's tree live (`phone_trees` broadcasts + `pocket_trees` in the live snapshot), tree icons on the camera-wall seats, and grown/withered counts; the owner's desktop gets `phone_tree_alert` when theirs withers. The room panel has only that QR + a "Copy invite link" button (room URL) | BE `app/phonetree.py` (`_phase`, `_in_break`, `_view`, `room_pocket_trees`, `_announce`, `_advance(paused=)`, `_watch`); FE `components/RoomForest.jsx`, `pages/PhoneTree.jsx`, seats in `pages/Live.jsx` | FULLY IMPLEMENTED (pytest `test_phonetree.py`, 16 tests incl. break pause, room-aware view, per-room forest list, end-to-end wither→forest→alert) | none |
| Live room: camera wall + study notepad | the room is built around a camera wall showing every member (video if shared, otherwise an avatar seat with focus dot + pocket-tree icon; "camera on — turn yours on to see it" when only they share); timer/focus/attention + room forest on the left; a study notepad on the right with tabs Assistant (ask my material, RAG with citations + my material), My rooms (study rooms I created/spoke in: summary + latest AI explanations, `GET /api/rooms/mine`), Revision (revision sheet), Notes (private, autosaved in this browser) | FE `pages/Live.jsx` (`Stage`, `Seat`), `components/Notepad.jsx`; BE `rooms.py` (`my_rooms`, presence `video` flag) | FULLY IMPLEMENTED (pytest `test_my_rooms…`; E2E `camera.py` 12/12, `attention.py`, `game.py` pass) | camera, materials |
| Web-search toggle | online references alongside material | — | PLANNED (needs external search API) | — |
| Fully free-form illustrations | model-emitted diagram spec for any topic, beyond the fixed template catalogue (now 6 templates: `thales`, `function_plot`, `process_flow`, `concept_map`, `timeline`, `comparison`) | — | PLANNED | — |
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
  users ||--o{ activity : "tracked actions"
  users ||--o{ points : "score ledger"

  users { int id PK; text pseudo UK; text pw_hash; text salt; text learning_style; real created_at; real last_seen; int login_count }
  activity { int id PK; int user_id FK; real ts; text kind; text detail }
  points { int id PK; int user_id FK; real ts; text rule; int points; text ref }
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
- **Seed data:** `seed_live_rooms()` inserts 4 public Live rooms: `LIVE-DEEP` (25/5), `LIVE-LONG` (50/10), `LIVE-SPRINT` (15/3), `LIVE-DEMO` (1/1, the fast demo/E2E room), and deletes the retired `LIVE-FOREST` room from existing databases (every live room has its own forest now).
- `events.kind` values seen: `explain`, `quiz`, `group_quiz`, `resume`. `room_messages.kind`: `chat | explanation | answer | system`. `docs.status`: `waiting | indexing | ready | error` (see materials.py). `docs.kind`: `text | image`.
- **Tracking + scoring (`app/scoring.py`).** `activity` logs what each user does (`signup, login, visit, explain, quiz, resume, review, group_quiz, room_join, live_join, pocket_tree`; `detail` is small JSON). `points` is an append-only score ledger: one row per award, tagged with the `rule` that gave it; a score is `SUM(points)`. Rules (points per unit / daily cap): `signup` 10, `daily_login` 5/5, `streak` 2 per streak day from day 2 /20, `explain` 5/50, `quiz_correct` 10 per right answer /200, `quiz_perfect` 20/60 (≥ 3 questions all right), `group_quiz` 10 per right answer /150, `resume` 5/15, `review_card` 2/60, `game` 1 per 10 break-game points /150, `pocket_tree` 15/60. Levels start at 0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000… points. To grow the system: add a `Rule` to `RULES`, call `scoring.award()` where it happens (old rows keep their meaning). `users.last_seen` / `login_count` are updated by `scoring.seen()` (any authenticated request, at most once a minute; the first one of a UTC day tracks a `visit` and gives the daily + streak bonus). Columns are added to old databases by `scoring.init()`. `/api/progress` `xp` now returns this score.
- **Test database:** `backend/scripts/test_db.py create` builds `herwiego-test.db` with fake users (unique `adjective_animalNN` pseudos, random 12-character passwords hashed like real sign-ups) and a replayed history through the same scoring calls; plain passwords go to `test-users.csv` (git-ignored). `add` adds one user, `report [--user X]` prints the tracking/score table. Run the app on it with `HERWIEGO_DB=herwiego-test.db`.
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
| GET | `/api/rooms/mine` | yes | — | study rooms I created or spoke in (name, topic, summary, last_at), newest first — the live-room notepad |
| GET | `/api/rooms/{code}/conversation` | yes | — | saved transcript + summary (study rooms) |
| WS | `/ws/rooms/{code}?token=&lang=` | token in query | see protocol below | realtime stream |

### Review (`/api/review`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| POST | `/add_quiz` | yes | quiz → flashcards (deduped) | ok |
| GET | `/due` | yes | — | cards due now |
| POST | `/grade` | yes | `{card_id, grade(0–3)}` | next schedule |
| GET | `/stats` | yes | — | counts / upcoming |

### Score (`/api/score`)
| Method | Path | Auth | Body / params | Returns |
|---|---|---|---|---|
| GET | `/me` | yes | — | `total, today, week, level, floor, next, progress, rank, week_rank, streak_days, by_rule[], recent[]` |
| GET | `/leaderboard` | yes | `period=week|all` (default week) | `top[]` (10: `pseudo, score, rank`) + `you` |
| GET | `/rules` | no | — | rules (points, daily cap, label) + level floors |

### Health
| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/health` | no | `{ok}` |

### WebSocket protocol (`/ws/rooms/{code}`)
Client→server `kind`s handled (see `app/rooms.py`): `topic, explain, ask, quiz_start, quiz_reveal, answer, summarize, chat, dm, reaction, focus, forest_join, draw, timer_start, timer_stop, lang, rtc_join, rtc_leave, rtc_signal, event_optin, event_optout, event_start, game_result, game_content, turn_pass, word_swap, mime_guess|turn_guess`. `rtc_join` takes an optional `watch: true` (receive video only; presence `video` stays false). `draw` is pictionary strokes (op `sync`|`stroke`|`clear`, normalised 0..1 points); `event_optout` drops the player from every game this break.
Server→client message `type`s include: `phone_trees` (`{trees: [{pseudo, state, p, visible}]}` — the room's forest; the live `snapshot` also carries `pocket_trees`), live `presence` members carry `video` (camera on), `snapshot, presence, chat, dm, reaction, explanation, quiz, answer, summary, rtc_*, event_participants, event_scores, game_start, game_word {word, round, swaps_left, now}, game_round {round, order}, game_turn, game_your_turn {round, index, seconds, word}, game_turn_end, game_swap_denied {reason: limit|guessed}, game_close, game_content, game_over, game_guessed`, `draw` (op `stroke`|`clear`|`replay` — pictionary strokes to that game's players), `tree` (a tree finished: `{pseudo, cycle, state: grown|wilted}` — also fed by a finished *pocket* tree, with `cycle: "phone"` and extra `source: "phone", grown: <seconds>` keys), `phone_tree_alert` (`{pseudo, grown}` — sent privately, not broadcast, to the owner's desktop connection the instant their pocket tree wilts; see `app/phonetree.py`). `presence` carries `at` (server clock, used by the client to interpolate tree growth) and, for forest members only, a per-member `tree` = `{state, p, avail}`; the `snapshot` of a Live room carries `forest` (last 60 finished trees). The separate `/ws/phone-tree/{token}` socket (its own connection, not the room's) sends only `phone_tree` (`{state, p, grown, visible, away_for, grace_s}`) and `error`, and accepts only `{type: "visible", visible}` from the client.
Study rooms (`kind=group`) use the chat/explain/quiz/summarize branch and persist to `room_messages`; Live rooms (`kind=live`) use the reactions/chat/dm/rtc/games branch and are **not** persisted.

**Error handling.** AI failures → `ai_error_response()` maps `AIError.kind` to 429/502/503 with `Retry-After`. Validation → 400 with a translated message. Rate limit → 429 from `security.Guard`/`SlidingWindow`. Not-found/ownership → 404/403.

**Not yet present / to confirm.** Real-service quota status/header names are assumed (429 + `Retry-After`, any header containing "remaining"); confirm with organisers. No endpoints yet for the planned features (web-search toggle, login active-recall).

---

## 8. Environment configuration & dependencies

- **Runtime:** Python 3.11+ (CI 3.12/3.13; Docker 3.13). Node 18+ (CI/Docker use Node 22). Package managers: `pip`, `npm`.
- **Mandatory in every environment, not just production (since 10 Nov):** `HERWIEGO_BASE_URL`, `HERWIEGO_API_KEY` (a real, non-placeholder key), `HERWIEGO_COMPACT_MODEL`, `HERWIEGO_MID_MODEL`, `HERWIEGO_EMBED_MODEL` — the app raises `RuntimeError` on startup and refuses to run without them; there is no mock fallback. A strong `HERWIEGO_SECRET` is additionally mandatory for a real (production) deployment. Everything else is optional with defaults.
- **Database:** SQLite — no server to run; the file is created automatically.
- **Ports:** backend `8000`; Vite dev `5173` (proxies to 8000). Expected URL in production/single-process: `http://127.0.0.1:8000`.
- **Env files:** repo ships `./.env.example` (docker) and `backend/.env.example` (local). A consolidated template is in `docs/handover/env.example.txt`. Config is read by `app/config.py` (auto-loads `backend/.env`).
- **Optional:** a reverse proxy (`HERWIEGO_TRUST_PROXY=1`), Docker. The real AI service is **not** optional — see above.

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
cp .env.example .env                  # set HERWIEGO_SECRET (openssl rand -hex 32)
docker compose up --build             # http://127.0.0.1:8000  (HERWIEGO_ENV=production)
```

- **DB creation / migrations / seeding:** automatic on boot (schema + in-code `ALTER` + `seed_live_rooms`). Nothing to run manually.
- **Tests:** `cd backend && python -m pytest -q` (151 tests — run against a test-only fake HTTP transport, `backend/tests/_fake_service.py`, so no real API key is spent; it is not part of the shipped app). i18n: `cd frontend && npm run i18n:check`. E2E: start the server **with real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` set** (the app will not start otherwise), then `cd e2e && python run.py` (needs Playwright + Chromium; `pip install playwright && playwright install chromium`). The E2E `game.py` waits for a game window in the `LIVE-DEMO` cycle (up to ~1 min) before playing.
- **Verify a service is up:** `curl http://127.0.0.1:8000/api/health` → `{"ok":true}`.
- **Common startup errors:** `ModuleNotFoundError: app` → run uvicorn **from `backend/`**. `No module named uvicorn` → wrong venv activated. The app refuses to start with a weak `HERWIEGO_SECRET` (production only) or with a missing/placeholder `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` (every environment, by design — no mock fallback). On Windows, a `UnicodeDecodeError` reading UTF-8 files is avoided in-code, but set `PYTHONUTF8=1` if any tooling defaults to cp1252.

---

## 10. Testing, debugging & current health

- **Automated tests:** 187 backend pytest tests across 15 files (incl. `test_scoring.py`: caps, daily visit + streak, levels/ranks, API, the test-database script and logins with its passwords) (auth, orchestration, gateway, ai-optim, materials, features/rooms/games, forest, phonetree, pictionary-draw, review, hardening, i18n, visuals) — all passing, run against the test-only fake transport in `backend/tests/_fake_service.py`. Frontend `i18n:check` green (495 sentences × 3 languages). `npm run attention:test` (9 pure-logic tests for the attention tracker) and `npm run qr:test` (4 tests; decodes the generated QR with jsQR); both run in CI. Playwright E2E suites (`run.py`, `game.py`, `conversation.py`, `camera.py`, `images.py`, `rag.py`, `lang.py`, `review.py`, `resume.py`, `attention.py`, `draw.py`) start the server with real credentials (no mock fallback). On 10 Nov after the central game stage + looping rounds, `camera.py` (12/12), `draw.py` (15/15) and `game.py` (10/10) were re-run and pass; earlier the same day `attention.py` too (they need no AI); the AI-dependent ones were updated for the removed quiz/example chips but not re-run without a working key. `forest.py` was removed with the Forest page. No coverage % is collected.
- **Known/expected behaviours that look like errors:** the `rag.py` E2E logs a console `400` which is the **intentional** personal-data-extraction refusal.
- **Known limits / technical debt:**
  1. **Single process, in-memory state** (rooms, rate limiters, semantic cache, game state) → no horizontal scaling without Redis/shared state.
  2. **No SFU/TURN** for WebRTC → camera mesh works on LAN/same-NAT; cross-NAT needs a TURN server; wall capped by `video_capacity`.
  3. **No DB foreign keys** — integrity is app-enforced only.
  4. **The test suite's fake embeddings are lexical** (not semantic), so similarity thresholds (`min_similarity`, `sem_threshold*`) tuned against them may need re-tuning once run against the real embeddings in practice.
  5. **Real service never exercised against live traffic** — the single biggest unknown; quota status/header assumptions unconfirmed; API-key/endpoint status was last reported as "not checked recently" as of 10 Nov.
  6. **WS token in URL** (mitigated by disabling uvicorn's access log; the app logs redacted).
  7. **Camera attention check** — thresholds (`ATTENTION` in `attention.js`: yaw 40°, pitch 55°, 8 s turned / 10 s no face before "away") are reasoned, not tuned on real users. Verified on Chromium's fake camera (no face → away) and on a synthetic cartoon face (→ facing the screen); **not yet tried on a real webcam, other devices, glasses/headscarves, or poor light** — do this before the demo. Face detectors can be less reliable for some people, so it is strictly opt-in and a missed detection only changes the shared yes/no dot, never a score. Needs a secure context (HTTPS or localhost) for `getUserMedia`; falls back to the CPU backend when WebGL is unavailable (slower). The strict CSP (no `unsafe-eval`) was confirmed to work with the model.
  8. **Game guesses are judged on the server** against the word in every UI language; trivia answers too. Mime watching relies on the peer-to-peer mesh (≤ the mesh cap), so very large mime groups may not all see the performer.
- **Security posture:** scrypt hashing, signed tokens, body-size cap, rate limits (login/register/AI), production refuses weak secrets and hides `/docs`, access log redacts secrets. No HTTPS termination in-app (expected behind a proxy).

---

## 11. Completed vs remaining

### A. Successfully completed (implemented + verified)
**Camera attention detector (opt-in, on-device)**; **Study forest + QR invite**; **Pocket tree (anti-phone-distraction, QR/link)**; Auth; diagnostic; solo explain + figure; hints; quiz (+answer/finish); progress; **revision sheet**; **SM-2 review**; RAG (upload/index/search/ask, images+PDF, personal-data guard); study rooms (chat, saved conversation + summary); Live rooms (pomodoro, presence, focus signal, reactions, **camera wall**, **chat**, **DM**); **break games with per-player choice + parallel play** (Quiz/Mime/Pictionary, mime/pictionary chat-guessing); AI gateway (routing, caches, retries, quota, fallback, metrics); i18n EN/FR/AR/Darija + RTL; Docker + CI.

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
- **Needs doing for real prod:** put it behind HTTPS (reverse proxy; set `HERWIEGO_TRUST_PROXY=1`); set the real `HERWIEGO_*` AI vars; provide a **TURN server** if cross-NAT video is required; back up the SQLite volume; consider moving room/rate-limit/cache state to Redis and video to an SFU **only if** scaling beyond a single process.
- **Frontend hosting:** none separate — the backend serves `frontend/dist`. (A static CDN is optional.)
- **DB hosting:** the SQLite file on a persistent volume; no DB server.

---

## 14. Important development decisions & context (do not reverse)

- **Single process + SQLite + in-memory room state** is a deliberate choice for the challenge (simplicity, zero external infra). Don't introduce Redis/Postgres/an ORM without a scaling reason.
- **The gateway is the only caller of the inference service.** All model calls go through `ai/gateway.py`; keep it that way.
- **Model routing follows the project's orchestration guidance (9 Nov revision):** **mid-size** = reasoning/synthesis/planning and final user-facing writing — figure choice (`illustrate`), the explanation text itself (`explain`), the quiz draft (`quiz`), and the revision sheet (`resume`). **compact** = classification/triage (`route`), field extraction/formatting (`filter`, `quiz_check`), short targeted summaries (`summary`), hints, material answers, and the automatic fallback when mid-size is saturated. This supersedes the earlier "mid = figures only" rule; preserve *this* split going forward unless the owner changes it again.
- **No built-in fake/mock AI service in the shipped app, and no fallback (10 Nov decision, at the owner's request).** `mock_api.py`/`mock_llm.py` were deleted entirely; `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` are mandatory in every environment and the app raises `RuntimeError` on startup without them. Don't reintroduce an in-app mock path. The backend test suite instead injects a test-only `httpx.MockTransport` (`backend/tests/_fake_service.py`) via `create_app(ai_transport=...)`; that module is outside `app/` and never reachable from the running app.
- **i18n is enforced** (build fails on missing OR unused keys). New UI strings must be wrapped (`t`/`tx`) and added to all three locales. Keys are English sentences.
- **Illustrations are live SVG from a small set of validated templates** (Thales, FunctionPlot, ProcessFlow, ConceptMap, plus **Timeline** and **Comparison** added 10 Nov for sequence/history and side-by-side topics), not images; `ai/visuals.py` validates specs. Fully generalising this (free-form AI-emitted diagram specs for any topic) is still a planned feature, not a rewrite.
- **Camera attention detector is opt-in, on-device, and only a yes/no leaves the device.** No frame is stored, drawn or sent; the camera is opened only during the focus phase and released on the break or when switched off; the face model is self-hosted (no CDN, no new external service to declare). Looking down at notes is deliberately not "away". When the camera says "facing the screen", the 90 s input-idle rule is waived (reading a book is studying).
- **Pictionary is played on a shared canvas, not the camera.** The performer draws on an HTML canvas (`components/DrawBoard.jsx`); strokes are relayed to that game's players as normalised points via `draw` and kept per turn so a late watcher catches up (`draw` op `sync`). Only the current performer may draw; the server never renders, it only relays. Plain canvas 2D, so it runs under the strict CSP.
- **Every break game is opt-in and opt-out.** A player picks one game or **Skip — just rest** during the invite, and can **Leave the game** during the break (`event_optout`); nobody is ever forced into a timed game.
- **A forest tree looks full-grown after `TREE_FULL_S` (180 s) of focus**, but is still only PLANTED at the end of the focus phase, so a 25-min phase reads as "grow it in a few minutes, then keep it alive". Wilt grace and the plant/drop thresholds are unchanged and time-based.
- **(Superseded 10 Nov: the UI no longer uses the desktop forest — each live room's forest is its members' pocket trees, see `phonetree.py`. The notes below describe the kept-but-unused `rooms.py` logic.)** **Study forest is a view over a Live room, not a new room kind.** No schema change, no new infra: the server keeps one `Tree` per opted-in member per focus phase, fed only by the member's own `focus` boolean (the same signal as the pomodoro focus indicator, including the camera attention check). Forest state is in memory and ephemeral (finished trees are kept in `room.forest`, capped at 200). A tree that wilts having grown less than min(20 s, 5 % of focus) is dropped silently so a brief peek leaves no "wilted" record. Keep `TREE_*` constants in `rooms.py` and `GRACE_S/MIN_S` in `Forest.jsx` in step.
- **Karaoke was removed** because copyrighted lyrics cannot be embedded and public-domain songs make a weak game; **Pictionary** replaced it on the same turn-game engine. Re-adding karaoke must use traditional/public-domain or user-supplied lyrics only.
- **Break games: per-player choice + parallel play + reset each break** was a deliberate rework of the old "first chooser decides" model. Game messages are **scoped** to a game's players (`broadcast_game`) — keep that scoping or parallel games will cross-talk.
- **Patches are delivered as a single zip of changed files** excluding `venv/.env/herwiego.db/node_modules/dist`, so the owner's local data survives an overwrite.
- **Report/business-model note are deferred** by the owner — do not spend effort there unless asked.
- **Forest limits to remember:** focus is client-reported (like game results), so a determined user can fake it; leaving the page (tab hidden / idle) wilts the tree after the grace; a QR only helps if the encoded URL is reachable from the scanning device (the page shows a localhost hint); a ticker (every 4 s) runs only while at least one member is in a forest.
- **Open questions:** real-service quota header/status names; embeddings model id and similarity tuning; whether karaoke returns.

---

## 15. Next development session: exact resumption point

1. **Current state:** feature-complete prototype, now wired to a **real AI service only** — the in-app mock was deleted 10 Nov, along with UI placeholder/example content; real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` are mandatory to run. 151 backend tests (against the test-only `_fake_service.py` transport) + i18n green. E2E suites need a real endpoint now and have not been re-run since the cutover. Lots 1 (revision sheet, chat, DM, mime-guessing), 2 (per-player parallel games, Pictionary replacing karaoke), and 3 (real-AI-only cutover + timeline/comparison illustrations) are done.
2. **Last completed implementation:** real-AI-only cutover — deleted the in-app mock service and UI placeholders, added `timeline`/`comparison` illustration templates, re-architected the backend test suite onto a test-only fake HTTP transport.
3. **Last files modified:** `backend/app/config.py`, `backend/app/ai/gateway.py`, `backend/app/main.py`, `backend/app/routes_study.py` (mock removed); `backend/app/ai/visuals.py`, `frontend/src/visuals/Timeline.jsx`, `frontend/src/visuals/Comparison.jsx`, `frontend/src/visuals/VisualSheet.jsx`, `frontend/src/styles.css` (new templates); `frontend/src/pages/Study.jsx`, `frontend/src/pages/Room.jsx`, `frontend/src/components/Materials.jsx`, `frontend/src/components/AIStatus.jsx` (placeholders removed); `backend/tests/_fake_service.py`, `backend/tests/test_visuals.py` + most other `backend/tests/*.py` (test re-architecture); `frontend/src/locales/{fr,ar,darija}.js` (deleted earlier mocks/mock_llm.py reference — mock_api.py/mock_llm.py are deleted files, kept here only as history of lot 1).
4. **Last task worked on:** updating `docs/handover/*` and this file to match the real-AI-only cutover, then full verification + delivery.
5. **Immediate next task (owner priority):** verify a real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` actually works end-to-end (status was "not checked recently" as of 10 Nov) and re-run the Playwright E2E suites against it; then the optional **karaoke** 4th game (public-domain / user-supplied lyrics only), the web-search toggle, further illustration freedom, smart tutor and login active-recall. Before the demo, also try the attention check by hand on a real webcam (adjust `ATTENTION`) and open the forest on two real devices (QR scan from a phone needs a URL the phone can reach, not `localhost`).
6. **Inspect before changing anything:** `app/rooms.py` (events/games model, `broadcast_game`, `run_turn_game`, `handle_turn_guess`), `frontend/src/pages/Live.jsx` (`LiveRoomView`, `useVideoMesh`), `frontend/src/lib/hooks.js` (`useCycle`, `useVideoMesh`, `useFocusSignal`), `app/config.py`, `app/db.py` (SCHEMA + seed).
7. **Must be running:** backend `uvicorn app.main:app` on :8000, with real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` set — it will not start otherwise, no mock fallback; for E2E, Playwright + Chromium and a built `frontend/dist` (or `npm run dev`).
8. **Do NOT modify:** the gateway's role as sole service caller; the "no mock, no fallback" rule; the model-routing split; the i18n enforcement; the `broadcast_game` scoping; the patch-exclusion convention. Don't rewrite working subsystems.
9. **Known issues to keep in mind:** real service unverified end-to-end; the test suite's fake embeddings are lexical, not semantic; no TURN for cross-NAT video; game correctness client-reported; `LIVE-DEEP` room state persists across tests in a file run (use a fresh cycle/room and scope assertions to participants, as the current tests do).
10. **How to validate changes:** `cd backend && python -m pytest -q` (keep 151 green), `cd frontend && npm run i18n:check` + `npm run build`, then the relevant Playwright suite against a running server with real credentials. Add a backend test + an E2E check for each new feature.

**First 5 actions for the next session (prioritised):**
1. Read `app/rooms.py`, `frontend/src/pages/Live.jsx`, `frontend/src/lib/hooks.js`, `app/config.py` and this doc; confirm reality matches it.
2. Run the test + i18n + build + E2E gates to confirm a green baseline.
3. (Done) Camera attention detector: `useAttention` → `useFocusSignal(share, camera)`; see `lib/attention.js`.
4. Try it on a real webcam and tune `ATTENTION` (thresholds + grace periods) if needed.
5. (Done) Per-room forest + camera-wall live room + study notepad: see `components/RoomForest.jsx`, `components/Notepad.jsx`, `pages/Live.jsx`, `app/phonetree.py`. Next, scaffold whichever owner-priority item is chosen (karaoke, web-search toggle, …) and update i18n + docs.

---

*End of technical documentation. See `PROJECT_HANDOVER_PROMPT.md` for the copy-paste prompt to start the next AI session.*
