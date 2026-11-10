# Browser end-to-end tests

These drive a real Chromium against a running HerwieGO server (it needs real AI credentials set).

```bash
pip install playwright && playwright install chromium
cd backend && python -m uvicorn app.main:app --port 8000   # in another terminal
cd e2e
python run.py       # pages, solo tutor (staged answer, figure, step links, hints, quiz), rooms, live, mobile
python rag.py       # material upload, tutor answers cited from it, room Q&A shared by two people
python competition.py  # two-player room competition to the podium (~2 min)
python persistence.py   # leave Solo study / a study room mid-generation, come back: the work is still there
python review.py    # quiz -> flashcards -> grading
python resume.py    # revision sheet
python lang.py      # French / Arabic / Darija, right-to-left, no horizontal scroll
python camera.py    # two-peer WebRTC camera wall in a Live room (uses a fake camera)
python attention.py # opt-in camera attention check: off by default, on-device, camera released (fake camera; ~1-2 min)
python draw.py     # pictionary centre stage: private distinct words, word swap, practice board goes live, close/correct guesses, next player (LIVE-DEMO; ~2 min)
python images.py    # image resources: upload, gallery, lightbox
python conversation.py  # study room saved as a conversation + AI summary
python game.py      # public-room group game: invite, opt-in, shared leaderboard (uses LIVE-DEMO, ~2 min)
```

`camera.py` needs a Chromium with a fake camera; it passes the flags itself. It is timing-sensitive on a loaded machine: rerun once if a tile check fails.
Each run creates fresh accounts, so the suites can be repeated on the same database. Voice dictation is not covered (headless Chromium has no speech service).
Set `HERWIEGO_BASE` to test another URL, `HERWIEGO_CHROME` to point at a specific Chrome binary,
and `HERWIEGO_SHOTS` to change where screenshots are written (default `e2e/shots`).
