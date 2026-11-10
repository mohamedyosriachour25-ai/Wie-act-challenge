# HerwieGO — Development Roadmap

Ordered by the actual project state and the owner's stated priorities. **Preserve existing code; extend, don't rewrite.** Every phase ends with the green gate: `pytest -q` (233+), `npm run i18n:check`, `npm run build`, and the relevant Playwright suite.

## Phase 1 — Stabilise against the real AI service (CRITICAL)
Nothing here changes architecture; it validates the biggest unknown. Note (9 Nov 2026): the built-in
fake service has since been removed entirely (see `claude/herwiego-app-status.md`) -- the app now has
no fallback and refuses to start without real `HERWIEGO_*` values, so this phase is no longer optional.
1. Put the real `HERWIEGO_*` values in `backend/.env`; the app will not start without them.
2. Confirm with organisers (or observe): quota **HTTP status + header names** (gateway assumes 429 + `Retry-After` / any header containing "remaining"), the **embeddings model id**, and how the vector collection is queried. Adjust `app/ai/gateway.py` only if the assumptions are wrong.
3. **Re-tune similarity** on real embeddings: `HERWIEGO_MIN_SIMILARITY`, `HERWIEGO_SEM_THRESHOLD`, `HERWIEGO_SEM_THRESHOLD_ASK`.
4. Smoke-test each AI path (explain, figure, hint, quiz, ask_material, resume, summary) and watch `/ai/metrics`.
*Files:* `app/ai/gateway.py`, `app/config.py`, `backend/.env`. *Risk:* HIGH, *Effort:* S–M.

## Phase 2 — Finish partially-done work
- Harden the real-service error mapping discovered in Phase 1 (retries/backoff, friendly messages).
- Any thresholds/prompts that under-perform on the real models (`app/ai/prompts.py`).
*Priority:* HIGH.

## Phase 3 — New features (owner priority order)
1. **Camera focus/attention detector** (HIGH) — **DONE (8 Nov 2026)**: `lib/attention.js` + `useAttention` + `AttentionCheck` in `Live.jsx`; remaining: tune on real webcams. Original brief: On-device attention estimate from the webcam, feeding the existing focus signal; strictly opt-in, no upload, clear toggle. *Files:* `frontend/src/lib/hooks.js` (new `useAttention` or extend `useVideoMesh`/`useFocusSignal`), `frontend/src/pages/Live.jsx`. Mostly client-side; add an E2E smoke test. Keep it light (performance + privacy).
2. **"Study forest" room + QR code** (HIGH) — **DONE (8 Nov 2026), then replaced on 10 Nov by a forest inside every live room (pocket trees)**: `Room.update_trees` in `rooms.py`, `pages/Forest.jsx`, `components/{Tree,QrCode}.jsx`, `lib/qr.js`, seeded room `LIVE-FOREST`; tests `test_forest.py`, `qr-test.mjs`, `e2e/forest.py`. Original brief: A focus room where keeping the tab open/visible grows a tree; leaving wilts it; join via a QR that encodes the room URL. *Files:* new `frontend/src/pages/Forest.jsx` + route in `App.jsx`; a QR library (npm); reuse `useCycle` + the Page Visibility API; optionally a new `rooms` kind if server coordination is wanted (otherwise purely client-side). i18n + docs.
3. **Karaoke as optional 4th game** (LOW/OPTIONAL — only if the owner confirms). Traditional/public-domain or user-supplied lyrics only. *Files:* `app/rooms.py` game banks, `frontend/src/pages/Live.jsx`.
4. **Web-search toggle** (MEDIUM). On/off switch to fetch online references beside material; needs an external search API key. *Files:* new provider module, `app/ai/pipeline.py`, study routes, `frontend/src/pages/Study.jsx`. Ship the mechanism disabled by default.
5. **Real-time subject-accurate illustrations** (MEDIUM). Let the model emit a validated diagram spec for any subject instead of choosing from 4 templates. *Files:* `app/ai/prompts.py` (schema), `app/ai/visuals.py` (validate/normalise), `frontend/src/visuals/*` (a generic renderer).
6. **Smart tutor on "explain/simplify/teach" + transcription** (MEDIUM). *Files:* `app/ai/pipeline.py`, `frontend/src/pages/Study.jsx`.
7. **Login active-recall (1/3/7-day)** (MEDIUM). On login, surface due recall cards with animation + explanation (skippable), using distributed intervals. *Files:* `app/review.py` (schedule), a new login-review flow in the FE, reuse `cards`.

## Phase 4 — Testing & optimisation
- Add a backend test + an E2E check for each Phase-3 feature.
- Watch `/ai/metrics` under the real service; tune caches/concurrency.
- Measure the camera-detector cost on low-end devices.

## Phase 5 — Security & production readiness
- HTTPS via reverse proxy; `HERWIEGO_TRUST_PROXY=1`.
- Strong `HERWIEGO_SECRET`; confirm production refusals.
- Back up the SQLite volume; review rate limits for real traffic.
- TURN server if cross-NAT video is in scope.

## Phase 6 — Deployment
- `docker compose up --build` with real `.env`; the CI image/healthcheck already pass.
- Decide hosting (single container is enough for the challenge demo).
- Only if scaling beyond one process: move room/rate-limit/cache state to Redis and video to an SFU — **not** required for the challenge.

## Separate track (deferred by owner)
- Report ≤6 pages + business-model note (Phase-1 challenge deliverables). Do not start unless the owner asks.
