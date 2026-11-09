"""Pictionary drawing board: two players pick Pictionary; when the turn starts the performer gets an editable
canvas, draws a stroke, and the watcher's read-only canvas receives it live. Clear empties both. Uses LIVE-DEMO
(1 min focus / 1 min break), so one player performs one turn. ~2 minutes."""
import os, time, random
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EDNOVA_BASE", "http://127.0.0.1:8000"); SHOTS = os.environ.get("EDNOVA_SHOTS", "shots")
problems = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + (f"  {e}" if e else ""));
    if not c: problems.append(n)
def reg(pg, ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button", name="Create account").click()

code = "LIVE-DEMO"; FOCUS = 60; TOTAL = 120; LEAD = 40
def into(): return time.time() % TOTAL

# count pixels that have been drawn on (alpha > 0); the board clears to fully transparent
INK = "(() => { const c = document.querySelector('.draw-canvas'); if (!c) return -1; const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; })()"

budget = time.time() + 200
while time.time() < budget:
    x = into()
    if x < FOCUS and 18 < (FOCUS - x) <= LEAD: break
    time.sleep(1)

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None, args=["--no-sandbox"])
    ca = br.new_context(viewport={"width": 1100, "height": 1000}); cb = br.new_context(viewport={"width": 1100, "height": 1000})
    a = ca.new_page(); b = cb.new_page()
    for pg, w in ((a, "A"), (b, "B")): pg.on("pageerror", lambda e, w=w: problems.append(f"[{w}] {e}")); pg.on("console", lambda m, w=w: problems.append(f"[{w}] console {m.type}: {m.text}") if m.type == "error" else None)
    sfx = random.randint(1000, 9999)
    a.goto(f"{BASE}/live/{code}"); reg(a, f"da{sfx}"); a.wait_for_selector(".live")
    b.goto(f"{BASE}/live/{code}"); reg(b, f"db{sfx}"); b.wait_for_selector(".live")

    a.wait_for_selector(".panel.invite", timeout=20000)
    check("invite offers Pictionary", a.get_by_role("button", name="Pictionary").count() == 1)
    a.get_by_role("button", name="Pictionary").click(); b.get_by_role("button", name="Pictionary").click()

    while into() < FOCUS + 1: time.sleep(0.5)
    a.wait_for_selector(".turn-game", timeout=15000); b.wait_for_selector(".turn-game", timeout=15000)

    # one player gets the editable canvas (their turn), the other a read-only one
    end = time.time() + 15; performer = watcher = None
    while time.time() < end and performer is None:
        if a.locator(".draw-canvas.editable").count() == 1: performer, watcher = a, b
        elif b.locator(".draw-canvas.editable").count() == 1: performer, watcher = b, a
        else: time.sleep(0.4)
    check("exactly one performer has an editable board", performer is not None)
    if performer is None:
        print("NO performer; aborting"); raise SystemExit(1)
    check("the watcher's board is read-only", watcher.locator(".draw-canvas.editable").count() == 0 and watcher.locator(".draw-canvas").count() == 1)

    # performer draws a stroke across the canvas
    box = performer.locator(".draw-canvas").bounding_box()
    cx, cy = box["x"], box["y"]
    performer.mouse.move(cx + box["width"] * 0.2, cy + box["height"] * 0.3)
    performer.mouse.down()
    for k in range(1, 9):
        performer.mouse.move(cx + box["width"] * (0.2 + 0.07 * k), cy + box["height"] * (0.3 + 0.045 * k)); performer.wait_for_timeout(40)
    performer.mouse.up(); performer.wait_for_timeout(300)
    check("the performer's own canvas has ink", performer.evaluate(INK) > 0, str(performer.evaluate(INK)))

    end = time.time() + 6; got = 0
    while time.time() < end:
        got = watcher.evaluate(INK)
        if got > 0: break
        watcher.wait_for_timeout(300)
    check("the watcher received the drawing live", got > 0, str(got))
    performer.screenshot(path=f"{SHOTS}/draw-performer.png"); watcher.screenshot(path=f"{SHOTS}/draw-watcher.png")

    # clear empties both boards
    performer.get_by_role("button", name="Clear").click(); performer.wait_for_timeout(500)
    check("performer board cleared", performer.evaluate(INK) == 0, str(performer.evaluate(INK)))
    end = time.time() + 5; w2 = 1
    while time.time() < end:
        w2 = watcher.evaluate(INK)
        if w2 == 0: break
        watcher.wait_for_timeout(300)
    check("watcher board cleared too", w2 == 0, str(w2))

    br.close()

print("\n" + ("ALL PASS" if not problems else f"PROBLEMS: {problems}"))
raise SystemExit(1 if problems else 0)
