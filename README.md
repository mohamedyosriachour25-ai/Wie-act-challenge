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

Tests: `cd backend && python -m pytest -q` (233 backend tests, including orchestration and translation coverage, run against a test-only fake HTTP transport -- see "Testing without a live key" below) and `cd frontend && npm run i18n:check` (every interface sentence must exist in French, Arabic and Darija). Browser suites live in `e2e/` (14 Playwright scripts: solo tutor, RAG, rooms, competition, leave-and-return persistence, languages, revision, games, camera, attention); see `e2e/README.md`. Their latest screenshots are in `e2e/shots/` (for example `04-explain-thales.png`, `comp-podium.png`, `persist-solo.png`).

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

### Solo study spaces: the staged tutor

Solo study is organised in **study spaces**: each learner can keep several (one per subject, say), each with **its own course material** and **its own conversation memory**. The tutor in a space always reads that space's material before answering, cites it like [1], and remembers the conversation (the last 6 messages verbatim plus a running summary of older ones, folded by one compact call in the background).

The service answers each request whole (the guide rules out streaming), so a turn is split into stages and each one appears on screen the moment it finishes, over Server-Sent Events from our own server:

| Stage | Class | What the learner sees |
|---|---|---|
| `intent` — what is wanted, rewritten as a standalone question using the conversation ("why?" → "why … in Thales theorem?") | compact | "Understanding your question", then "Understood as: …" |
| retrieval in the space's material + a declared Wikipedia lookup in parallel (used only if the material does not cover it) | no AI call | the sources found |
| `draft` — a short first answer (draft-then-revise) | compact | the text, within seconds, marked "quick draft" |
| `tutor` — the final answer: corrects and completes the draft from the sources | mid-size | the final text replaces the draft |
| `illustrate` — which figure and its parameters (started in parallel with the writing) | mid-size | the figure when it lands; a concept map from the answer's steps if no spec is usable |

Greetings cost one compact call and no figure. Against a stand-in service with CPU-like delays, the first text appeared at 3.0 s and the final answer at 7.5 s, where the previous pipeline showed nothing for about 11 s. `HERWIEGO_QUICK_DRAFT=0` drops the draft (one call fewer, slower first text).

**Wikipedia is a declared external source** (the guide allows one if clearly declared): keyless, openly licensed, linked on every answer that used it, switchable per message and globally (`HERWIEGO_WEB_SEARCH=0`). Only the rewritten question is sent; a message that looks like it contains personal details is refused before anything is sent anywhere.

### How the tutor writes and draws

The writers (compact draft, mid-size final) work from one **educator prompt**: work out what the student needs, extract the key concepts from the course material for this request, explain each more simply than the document (plain words, every term defined), show how they connect, give the complete set when the subject is a set (all the layers, all the stages), and use a practical example from the subject itself. They check every statement against the passages (the final writer does not trust the draft), never mix two models (OSI numbering inside TCP/IP), and never copy a title page, a source list or remarks about the figure. An explanation always has the same shape: title, short summary, **Key ideas** (numbered, each with its concept in bold), **How they connect**, **Example**, **Check yourself**. A cleanup pass removes anything a model still adds around it (JSON or code wrappers, "Sources:" lists, notes about figures).

The **figure is drawn from the finished answer** (same items, names, order and count), with the document's analysed concepts and connections as vocabulary. If its spec is unusable, the figure validator recovers loose shapes (other key names, parameters at the top level, a template named loosely), a repair pass gets the exact parameter shapes, and as a last resort a concept map is built from the answer's key ideas or the document's concepts, so an explanation always ends with a figure. Hints are plain sentences, one per level (a guiding question, the key idea, the first step), never JSON.

Each document is analysed once by the mid-size model: subject, summary, key concepts with a one-sentence plain explanation, and how they connect. Passages that look like a title page or table of contents are ranked down in search and skipped by the analysis.

### RAG: retrieval in each library

Vectors are stored in the app's own database (`HERWIEGO_VECTOR_BACKEND=local`, the default and the supported setup; Qdrant/Chroma adapters remain in `vectorstore.py` but are not used). Every library (each study space, each room) is kept apart. How a question finds its passages:

- **Embeddings are spent once.** A content-hash cache means a passage is never embedded twice (re-uploads and shared material are free), and query embeddings are cached in memory.
- **Hybrid ranking.** 0.8 × cosine + 0.2 × keyword relevance, where keyword relevance is half BM25 (normalised) and half term coverage, with English/French/Arabic stopwords removed. This keeps exact terms (a formula name, a date) from being lost by the embedding.
- **Adaptive cut instead of one hand-tuned threshold.** A passage is kept only if its score is at least `max(HERWIEGO_MIN_SIMILARITY, 0.5 × best score)`, so weak tail passages drop out whatever the scale of the real embeddings. Neighbouring chunks of the same document are merged into one source.
- **One-time document summary.** After a document is indexed, one compact call writes a short summary and key terms from passages sampled across the whole document (stored once, never repeated). The tutor's intent and writing steps receive this library overview, so they know what the material covers even when a question matches no single passage.
- **Only what is cited goes to the mid-size model.** The compact draft cites passages; the mid-size final answer receives just those.

Study rooms and the live-room notepad use the same staged tutor as Solo study: in a study room every member sees the same draft, answer and figure arrive together. The room's **"ask the room's material"** box uses it too (without a figure and without the web): the question is rewritten with the last two room answers as context, answered from the shared library with citations, and everyone sees the draft and the final answer arrive.

When an answer has a figure, each numbered step that names a part of it (a segment such as `DE` or the triangle `ADE`, a stage of a process, a date on a timeline) becomes selectable: clicking it highlights that part of the figure. The matching is done by the server from the text, with no extra AI call.

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

### Course material (retrieval)

Learners add a PDF, `.txt`/`.md` file or pasted notes to their own library, and a group room has a shared library. Text is split into passages of about 800 characters and embedded in the background (batches under the endpoint's ceiling, progress shown, waits for the separate embeddings quota instead of failing). Search uses the hybrid ranking and adaptive cut described in "RAG" above.

**PDFs are always accepted.** A PDF with a text layer is read directly (up to 20 MB, `HERWIEGO_MAX_PDF_MB`). Pages with no text layer (scans, photos of pages) or a text layer that decodes to garbage are rendered to images and read by the service's OCR model (`HERWIEGO_OCR_MODEL`, default `deepseek-ocr`, one call per page, up to `HERWIEGO_OCR_MAX_PAGES` = 40; blank pages are skipped). The document appears at once with "Reading scanned page i of n" and becomes searchable when done. If no page can be read (no OCR model, quota), the PDF is still kept as a file the learner can open. Check the OCR model with your key: `python scripts/check_ocr.py some-scan.pdf`.

"Narrow before generating": passages below `HERWIEGO_MIN_SIMILARITY` are dropped. If none remain there is **no model call** and the user is told the material does not cover the question. If more than 3 remain, a compact call keeps the best ones. One compact call then writes the answer with `[n]` citations that open the source passage. Explanations use the same passages and say which documents they were built from. In a room one question produces one answer shown to everyone.

You can also add **images** (PNG, JPG, WebP, GIF) as visual resources — a photo of the board, a scanned page, a diagram. They are shown in a gallery and opened full size, but they are kept out of retrieval: not indexed, not sent to the AI, so they never spend quota (put scanned pages in a PDF to have them read). Up to 12 per library.

Safeguards: e-mail addresses, phone numbers and long identification numbers are removed from material before it is stored for search or sent to the inference service (the library says how many were removed; typed questions that contain them are still refused); the material is passed to the model as data, never instructions; limits of 6 documents, 240 passages per document, 600 per library; room material needs room membership.

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

### Room competition

Everyone in a study room can enter a competition on the room's topic. One mid-size call writes 8 questions grounded in the room's material (a compact call checks the answer keys when `HERWIEGO_QUIZ_VERIFY` is on), mixing five kinds:

| Kind | Time | How it is scored |
|---|---|---|
| multiple choice | 20 s | right or wrong |
| true or false | 12 s | right or wrong |
| closest number | 30 s | full marks within the tolerance, then decreasing with the distance; the closest player gets +100 |
| exercise (worked on paper, numeric result) | 60 s | as above |
| short answer | 25 s | exact = full, close spelling = half |

Points per question = closeness × (500 + 500 × speed), so right and fast earns up to 1000. The server keeps the time, hides the answer until everyone has replied or the time is up, then reveals it with each player's points; the game ends on a podium. Only the AI writing costs a call, and it is one call for the whole room. Results are saved (`competitions` table).

### Scoring: productive work first

Points reward steady work far more than play (`app/scoring.py`, rules shown in the app under "How points work"):

- **Study time.** While a study page is open and the learner is active, the browser reports one minute at a time (at most one per 55 s, idle time is not counted). Solo study and a Live room's focus phase count fully; time in a study room counts at 0.6.
- **Working day.** A day counts when effective minutes (focused minutes + 0.6 × room minutes + 2 per productive action) reach 25: +20 points, plus a working-day streak bonus of 5 per consecutive day (up to 10 days).
- **Productive actions:** explanations, correct and perfect quizzes, revision cards, adding material, revision sheets.
- **Social play pays less, with daily caps:** party games (1 point per 20 game points, cap 30), competitions (cap 40), shared quizzes (cap 40), time in rooms (cap 120).
- **Weekly productivity index (0–100):** 50 × focused hours / 10 + 30 × working days / 5 + 20 × quiz accuracy, with a 7-day chart on Home.

### Nothing generated is lost

Generations run on the server, not in the page. In Solo study (and the live-room notepad) a tutor answer is a server task: leaving the page, switching tabs or losing the connection does not stop it. Its progress is written to the database as it goes (the draft, the final text, the figure), so coming back shows the answer where it is and the page reconnects to the rest (`GET /api/spaces/{id}/turn` replays it, then follows it). **Stop** stops it on the server and keeps what was produced. After a server restart an unfinished answer keeps its partial text, is marked as interrupted, and offers **Ask again**. One answer at a time per space.

Study rooms work the same way: the explanation, the answers to questions about the material and the quiz are generated by the server, saved with the room (`rooms.state`) and come back after a restart; someone who leaves and comes back mid-generation sees the progress so far, then the rest. A room's material lives in the room's own library (`r:<code>`), a study space's in the space's library.

### Voice

A microphone button in the tutor and the room question box dictates the question with the browser's speech recognition (asked for consent the first time; on-device recognition is requested when the browser offers it, otherwise the browser's own service transcribes it — declared in the consent note). "Read aloud" reads a final answer with the browser's speech synthesis. No audio reaches HerwieGO's server.

### On a phone

The app can be added to a phone's home screen: `manifest.webmanifest` with 192/512 px icons, a maskable icon for Android and an `apple-touch-icon` for iOS.

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
- Voice is off until the learner turns it on; dictation uses the browser's speech recognition and no audio is stored or sent to HerwieGO.
- Anonymous chat messages never carry the author's pseudo, and do not count toward the participation bars.

## Not built yet

Shared study stream, karaoke games, offline mode, and tuning the retrieval cut-off on the organisers' real embeddings (the adaptive cut is designed to need little tuning, but it has only been measured on the stand-in service).
