# HerwieGO

A learning companion for IEEE WIE ACT 5.0 (GenAI Peer Learning Companion challenge). Every explanation comes with an interactive illustration beside the text. Learners can study alone, in a study room of 2 to 10 people, or in a public Live focus room.

**Status:** working prototype. Every lesson, quiz and illustration comes from the real OpenAI-compatible inference service, configured via `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY` (see below) -- there is no built-in fake service and no fallback; the app refuses to start without real credentials.

## Run it

Backend (Python 3.11+):

```bash
cd backend
python -m venv venv && source venv/bin/activate      # Windows PowerShell: .\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cp .env.example .env          # required: fill in HERWIEGO_BASE_URL and HERWIEGO_API_KEY, there is no fallback
python -m uvicorn app.main:app --reload --port 8000
```

Frontend (Node 18+):

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173, proxies /api and /ws to :8000
npm run build                 # then the backend serves frontend/dist on :8000 by itself
```

Tests: `cd backend && python -m pytest -q` (151 backend tests, including orchestration and translation coverage, run against a test-only fake HTTP transport -- see "Testing without a live key" below) and `cd frontend && npm run i18n:check` (every interface sentence must exist in French, Arabic and Darija).

### Testing without a live key

The app itself has no fallback: it refuses to start without a real `HERWIEGO_BASE_URL`/`HERWIEGO_API_KEY`. The test suite still runs without one -- `backend/tests/_fake_service.py` is a test-only `httpx` transport that speaks the same contract (quota/saturation/ceiling errors, usage headers) so the gateway's resilience logic and the app's end-to-end flows can be verified without spending the real, billed key on every run. Nothing under `backend/app` imports it.

### Test database (fake users, tracking, scores)

```bash
cd backend
python scripts/test_db.py create        # herwiego-test.db + test-users.csv (30 users, 3 weeks of activity)
python scripts/test_db.py report        # who is tracked: last seen, logins, actions, score, level
HERWIEGO_DB=herwiego-test.db uvicorn app.main:app --reload   # then sign in with any pseudo/password from the CSV
```
`add --pseudo name` adds one more user (password generated, appended to the CSV); `report --user name` shows one user's points and activity. Every action in the app is tracked (`activity`) and scored (`points`, rules in `app/scoring.py`); the score, level, rank and leaderboard show on the Home page.

## Connect the real service

Edit `backend/.env` (never commit it):

```
HERWIEGO_BASE_URL=<base url from the organisers>
HERWIEGO_API_KEY=<your team key>
HERWIEGO_COMPACT_MODEL=<compact model id from GET /v1/models>
HERWIEGO_MID_MODEL=<mid-size model id from GET /v1/models>
HERWIEGO_EMBED_MODEL=<embeddings model id>
HERWIEGO_MIN_SIMILARITY=<0.1 to start; tune on the real embeddings>
HERWIEGO_MAX_CONCURRENT=<per-key concurrency ceiling>
```

Assumptions to confirm with the organisers, because the guide does not specify them: the HTTP status and header names for "team quota exhausted" (the gateway assumes 429 with Retry-After or a `retry_after` field in the body), the names of the quota headers (any header containing "remaining" is read), and how the vector collection is queried.

## Architecture

```
React (Vite)  <--REST + WebSocket-->  FastAPI  -->  AI gateway  -->  real inference service
 Home, Solo, Study rooms, Live         auth, study,      routing, cache, retries,    compact + mid-size
 illustration renderers                rooms, progress   fallback, metrics           (your HERWIEGO_BASE_URL)
```

- `backend/app/ai/gateway.py`: the only code that talks to the inference service.
- `backend/app/ai/pipeline.py`: the study steps and which model class handles each.
- `backend/app/ai/visuals.py`: validation of the illustration specs the model produces.
- `backend/tests/_fake_service.py`: test-only fake HTTP transport (not used by the running app).
- `backend/app/rooms.py`: group rooms and Live rooms over WebSocket.
- `backend/app/materials.py`: course material upload, chunking, background indexing and similarity search.
- `frontend/src/visuals/`: one renderer per illustration template.

### Which model class does what

| Step | Class | Why |
|---|---|---|
| Route the request (subject, level, best illustration template) | compact | classification, language-independent so it is cached across languages |
| **Design the illustration** (template, parameters, per-step focus) | **mid-size** | the reasoning step: it decides what the figure shows |
| **Write the explanation** around the finished figure | **compact** | plan once, write cheaply: it receives a text description of the figure and the valid focus keys |
| Hints (3 levels, never the final answer) | compact | short targeted text |
| Pick the relevant passages when more than 3 match a question | compact | cheap filtering before the answer |
| **Answer a question from course material**, with [n] citations | **compact** | the answer is text written from a few retrieved passages |
| Quiz generation + review | compact | a compact draft reviewed by a second compact pass; the mid model is kept for figures |
| Fix malformed JSON | compact | formatting, only when needed |
| Any mid-size step when that class is saturated | compact | fallback, announced to the user |

An explanation costs three calls: `route` (compact) and `illustrate` (mid-size) run in parallel, then `explain` (compact) is written around the validated figure. If the illustration is unusable, a concept map is built from the key points and the explanation still gets a figure. The note "Why this figure" shows the learner the reasoning behind the picture.

### AI optimisation

Two optimisations beyond the base orchestration:

- **Semantic cache.** The gateway already serves byte-identical calls from its cache. On top of that, an explanation or a material answer is reused when a *new request means the same thing* as a recent one — "explain Thales" vs "Thales theorem" — matched with the embeddings endpoint (its own, cheaper quota). A hit skips every generation call; a miss costs one embedding and is remembered. Reuse is scoped by task, language and (for explanations) learning style; grounded material answers are scoped by collection **and a version token**, so adding or removing a document invalidates reuse. Grounded explanations are never reused. Counted in metrics as `semantic_hits`. Tune `HERWIEGO_SEM_THRESHOLD` (0.86) and `HERWIEGO_SEM_THRESHOLD_ASK` (0.93); turn it off with `HERWIEGO_SEMANTIC_CACHE=0`.
- **Quiz draft-then-revise.** The quiz is drafted by the mid-size model (synthesis) and then reviewed by the compact model (checking): the reviewer corrects a wrong answer key, weak or duplicate distractors, and drops any question without a single defensible answer. It can only keep or improve the draft — if the review fails or is empty, the draft is used unchanged. Costs one extra compact call per quiz; turn it off with `HERWIEGO_QUIZ_VERIFY=0`.
- **Output reliability.** Route, illustrate and quiz prompts carry a worked example (few-shot) and an explicit JSON schema, and when a model's JSON still fails to parse or validate, the repair pass is **schema-aware** (it is told the exact shape to produce), which recovers far more answers than a generic "fix this JSON" and keeps malformed-output failures and fallbacks down.

### Languages

The interface and the prompts work in English, French, Arabic (Modern Standard) and Darija (Tunisian, Arabic script). The switch is in the header and the choice is remembered. Arabic and Darija switch the page to right-to-left; figures stay left-to-right so the maths reads correctly.

- Prompts carry a language rule (`lang_rule` in `ai/prompts.py`); JSON keys, template names and focus keys always stay English so validation is language-independent.
- The cache key includes the language, quiz answer checks reuse the quiz's language.
- Server messages are translated in `app/i18n.py`. In a room each member receives AI errors and statuses in their own language. Explanations and quizzes use the language of the person who triggers them.
- Interface texts: `frontend/src/locales/{fr,ar,darija}.js`, keyed by the English sentence. A missing key shows English, and `npm run i18n:check` fails on any gap.
- Another Darija variant (Moroccan, Algerian) means editing `LANGUAGES` in `ai/prompts.py` and `locales/darija.js`.

### Efficiency and resilience

- Answers are cached (6 h) and shared by every user and every room member. Identical requests in flight at the same time are merged into one call. A degraded answer is shown once and never cached.
- Group rooms generate one explanation and one quiz for everyone, so a 10-person room costs the same as one person.
- Error contract: quota exhausted, wait the stated delay and retry (or tell the user when the wait is too long); 503, retry once then fall back to the compact class; 400, halve `max_tokens` when the error names a ceiling; 200, read the quota headers.
- The interface never freezes: staged loading messages, a live AI status chip, and an AI usage panel with the metrics the organisers also measure (calls per model, compact share, tokens, cache hits, quota waits, fallbacks).
- With the fake service, the AI usage panel has buttons to exhaust the quota or make the large model busy, for a live resilience demo.

### Course material (retrieval)

Learners add a PDF, `.txt`/`.md` file or pasted notes to their own library, and a group room has a shared library. Text is split into passages of about 800 characters and embedded in the background (batches under the endpoint's ceiling, progress shown, waits for the separate embeddings quota instead of failing). Search is cosine similarity over a local SQLite store.

"Narrow before generating": passages below `HERWIEGO_MIN_SIMILARITY` are dropped. If none remain there is **no model call** and the user is told the material does not cover the question. If more than 3 remain, a compact call keeps the best ones. One compact call then writes the answer with `[n]` citations that open the source passage. Explanations use the same passages and say which documents they were built from. In a room one question produces one answer shown to everyone.

You can also add **images** (PNG, JPG, WebP, GIF) as visual resources — a photo of the board, a scanned page, a diagram. They are shown in a gallery and opened full size, but they are kept out of retrieval: not indexed, not sent to the AI (no OCR), so they never spend quota. Up to 12 per library.

Safeguards: uploads that look like they contain e-mail addresses or phone numbers are refused (text is sent to the inference service); the material is passed to the model as data, never instructions; limits of 6 documents, 240 passages per document, 600 per library; room material needs room membership. If the platform's per-team vector collection turns out to be required, only `retrieve` and `index_document` in `materials.py` change.

### Illustrations

The model never draws. It picks whichever template actually fits the topic (`thales`, `function_plot`, `process_flow`, `timeline`, `comparison`, or `concept_map` as a last resort) and fills in parameters -- fresh, topic-specific content every time, never a reused figure. The backend validates and clamps them, and React draws the figure. If the spec is unusable, a concept map is built from the explanation's own steps, so every explanation has an illustration. To add a template: a validator in `visuals.py`, a renderer in `frontend/src/visuals/`, a line in the catalogue and in `VisualSheet.jsx`.

### Revision (spaced repetition)

Every quiz a learner takes becomes a deck of flashcards (front: the question, back: the correct answer and why), with no extra AI call — the cards are built from the quiz already generated and cached. A **Revision** tab shows the cards that are due; the learner flips each one and grades recall (Again / Hard / Good / Easy), and an SM-2-style schedule brings each card back just before it would be forgotten. Cards are de-duplicated per learner, and the home progress shows how many are due. Endpoints under `/api/review` (`add_quiz`, `due`, `grade`, `stats`).

### Saved study rooms

A study room is saved as a conversation. Chat, topic changes, AI explanations and material answers are written to a transcript (`room_messages`) that survives a restart; a member who rejoins sees the whole conversation. A **Summarise** button asks the compact model for a short recap of the session (main topics, key points, open questions); the summary is stored on the room and shown to everyone. The transcript and summary are also available over REST at `GET /api/rooms/{code}/conversation`. Live focus rooms stay ephemeral and are not saved.

### Group games on the break

A public Live room runs an opt-in group game at each break. Because the Pomodoro timer is deterministic (epoch-aligned), every client derives the same schedule, so no server loop is needed: near the end of a focus block (the last `HERWIEGO_EVENT_LEAD_S` seconds, default 300, clamped to fit the focus length) an invite appears; whoever opts in plays a shared trivia race on the break. The server owns the opt-in list and the live leaderboard (points for a correct answer plus a speed bonus); only people who opted in for that cycle score. Three games ship, and each player picks one (same-game players play together, in parallel): a **quiz** (AI-generated trivia race, scored on the server) and two **turn games**, **mime** and **pictionary**. Turn games loop in rounds until the break ends — a new random order and new words each round — and every player holds a private word no one else has (swappable up to 3 times a round if it is too hard). A turn ends on time (30 s, or 60 s in rooms with a break of 10 min or more), when the player passes, or when everyone else has guessed in the chat. While a turn game runs it takes the middle of the room: the performer's camera (mime) or the live board (pictionary) beside the chat. Turn games are driven by a short server task (started when a client reports the break began); the quiz stays clock-driven. A seeded fast-cycle room `LIVE-DEMO` (1-minute focus/break) lets you see focus → break → game in under two minutes — handy for the demo.

### Camera in Live rooms

A Live room can turn into a shared camera wall. Turning your camera on is opt-in and off by default; you then see everyone else who is also sharing (it is reciprocal). The video is **peer to peer** over WebRTC: it never passes through the server, which only relays the small SDP/ICE signalling messages over the room's existing WebSocket, using the "perfect negotiation" pattern so two browsers can connect without clashing. The public space (presence + chat) holds up to `HERWIEGO_LIVE_CAPACITY` people (default 500), while `HERWIEGO_VIDEO_CAPACITY` (default 9) caps how many cameras share the mesh at once. This is a mesh, so it is meant for a handful of people; many publishers, or peers on different networks behind strict NATs, would need an SFU and a TURN server. The on-device focus signal is unchanged and still uses no camera.

## Deploy

```bash
cp .env.example .env     # set HERWIEGO_SECRET (openssl rand -hex 32), and for the real service the URL, key and model ids
docker compose up --build
```

One image holds the built React app and the API on port 8000; the database lives in the `herwiego-data` volume. The container runs as a non-root user, has a health check on `/api/health`, and starts only when `HERWIEGO_SECRET` is set. Put it behind HTTPS (any reverse proxy) for a public deployment and set `HERWIEGO_TRUST_PROXY=1` there so per-connection limits see the real client address. CI (`.github/workflows/ci.yml`) runs the backend tests on Python 3.12 and 3.13, the translation check, the frontend build and a container start-up check.

Without Docker: `npm run build` in `frontend`, then `HERWIEGO_ENV=production HERWIEGO_SECRET=... python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log` in `backend`.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HERWIEGO_BASE_URL` / `HERWIEGO_API_KEY` | none -- required | your real AI service; the app refuses to start without both |
| `HERWIEGO_COMPACT_MODEL`, `HERWIEGO_MID_MODEL`, `HERWIEGO_EMBED_MODEL` | | model ids from `GET /v1/models` |
| `HERWIEGO_ENV` | `development` | `production` refuses a weak secret or missing key and hides `/docs` |
| `HERWIEGO_SECRET` | dev value | signs session tokens; at least 24 random characters in production |
| `HERWIEGO_USER_AI_PER_MIN` | 20 | AI actions one learner may start per minute |
| `HERWIEGO_LOGIN_ATTEMPTS` | 8 | failed sign-ins per 5 min per pseudo (5x per connection) |
| `HERWIEGO_REGISTER_PER_HOUR` | 15 | accounts created per connection per hour |
| `HERWIEGO_TRUST_PROXY` | 0 | read the client address from `X-Forwarded-For` |
| `HERWIEGO_LIVE_CAPACITY` | 500 | people allowed in one public Live room (presence + chat) |
| `HERWIEGO_VIDEO_CAPACITY` | 9 | cameras sharing one Live-room mesh at once |
| `HERWIEGO_EVENT_LEAD_S` | 300 | how long before a break the group-game invite opens |
| `HERWIEGO_EVENT_MIN_PLAYERS` | 2 | players needed for a group game |
| `HERWIEGO_SEMANTIC_CACHE` | 1 | reuse near-duplicate requests via embeddings (0 to disable) |
| `HERWIEGO_SEM_THRESHOLD` / `_ASK` | 0.86 / 0.93 | cosine thresholds for explanation / material-answer reuse |
| `HERWIEGO_QUIZ_VERIFY` | 1 | compact reviewer checks the quiz draft (0 to disable) |
| `HERWIEGO_MAX_CONCURRENT`, `HERWIEGO_MIN_SIMILARITY`, `HERWIEGO_MAX_QUOTA_WAIT_S`, `HERWIEGO_CACHE_TTL_S` | 2, 0.1, 45, 6 h | gateway tuning |
| `HERWIEGO_DB`, `HERWIEGO_LOG_LEVEL` | `herwiego.db`, `INFO` | |

### Security and operations

- **The team quota is shared, so one learner cannot drain it:** every AI action (explain, hint, quiz, ask, and the room versions) counts against a per-learner budget, and a refused action never reaches the service.
- Sign-in and account creation are throttled; unknown pseudos cost the same time as wrong passwords. Passwords are hashed with scrypt, sessions are signed and expire after 7 days.
- Request bodies are capped (uploads stop being read at 3 MB, also when chunked). Security headers and a strict Content-Security-Policy are set; the API is never cached.
- Logs carry a request id, path, status and duration. The team key, the signing secret, bearer tokens and `?token=` are redacted everywhere, including uvicorn's WebSocket lines. Run uvicorn with `--no-access-log` (the Docker image does).
- SQLite runs in WAL mode. Documents whose indexing was interrupted by a restart resume where they stopped.
- Known limits: rooms and rate limits live in one process (several instances need a shared store such as Redis), and the WebSocket session token travels in the URL, so a reverse proxy must not log query strings.

## Privacy and ethics

- Accounts are a pseudo and a password. No e-mail, name, photo or voice is stored.
- The "focused" signal is a boolean computed on the user's device (tab visibility, window focus, recent activity) and can be turned off.
- Camera sharing in Live rooms is opt-in and off by default. The video is peer to peer and never reaches the server (only connection signalling does); nothing is recorded. The challenge brief discourages collecting faces, so camera sharing is a deliberate, user-initiated choice, not a default.
- Only the learner's topic and question text is sent to the inference service.
- Anonymous chat messages never carry the author's pseudo, and do not count toward the participation bars.

## Not built yet

Camera-based attention detection (planned fully on-device), internet retrieval from public sources, shared study stream, karaoke games, resume generator, voice read-aloud, teacher dashboard.
