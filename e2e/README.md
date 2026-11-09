# Browser end-to-end tests

These drive a real Chromium against a running EdNova server (fake AI service is fine).

```bash
pip install playwright && playwright install chromium
cd backend && python -m uvicorn app.main:app --port 8000   # in another terminal
cd e2e
python run.py       # pages, solo study, illustrations, rooms, resilience, mobile
python rag.py       # course material upload, retrieval, grounded answers
python lang.py      # French / Arabic / Darija, right-to-left, no horizontal scroll
python camera.py    # two-peer WebRTC camera wall in a Live room (uses a fake camera)
python attention.py # opt-in camera attention check: off by default, on-device, camera released (fake camera; ~1-2 min)
python forest.py   # study forest + QR: QR decodes, two users, grow/wilt/plant, replant (LIVE-DEMO; ~3-4 min, EDNOVA_QUICK=1 skips the replant)
python draw.py     # pictionary drawing board: performer draws, watcher sees it live, Clear empties both (LIVE-DEMO; ~2 min)
python images.py    # image resources: upload, gallery, lightbox
python conversation.py  # study room saved as a conversation + AI summary
python game.py      # public-room group game: invite, opt-in, shared leaderboard (uses LIVE-DEMO, ~2 min)
```

`camera.py` needs a Chromium with a fake camera; it passes the flags itself.
Set `EDNOVA_BASE` to test another URL, `EDNOVA_CHROME` to point at a specific Chrome binary,
and `EDNOVA_SHOTS` to change where screenshots are written (default `e2e/shots`).
