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
python images.py    # image resources: upload, gallery, lightbox
```

`camera.py` needs a Chromium with a fake camera; it passes the flags itself.
Set `EDNOVA_BASE` to test another URL, `EDNOVA_CHROME` to point at a specific Chrome binary,
and `EDNOVA_SHOTS` to change where screenshots are written (default `e2e/shots`).
