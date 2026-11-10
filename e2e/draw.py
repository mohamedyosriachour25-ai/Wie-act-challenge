"""Pictionary, end to end, on LIVE-DEMO (1 min focus / 1 min break): two players pick Pictionary. While the game
runs it takes the middle of the room (live board beside the chat, camera wall hidden). Each player holds a private
word (never the same as the other's) and can swap it; the first sketches on a private practice board that goes live
when their turn starts. A near miss is blurred and flagged "Close answer!"; the correct guess is hidden, announced,
ends the turn and reveals the word; the turn then passes to the next player. ~2 minutes."""
import os, time, random, re
from playwright.sync_api import sync_playwright

BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000"); SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + (f"  {e}" if e else ""))
    if not c: problems.append(n)
def reg(pg, ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button", name="Create account").click()

code = "LIVE-DEMO"; FOCUS = 60; TOTAL = 120; LEAD = 40
def into(): return time.time() % TOTAL
INK = "(sel) => { const c = document.querySelector(sel); if (!c) return -1; const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }"
# real pointer events (synthetic mouse drags are unreliable in headless Chrome)
SKETCH = """(sel) => { const c = document.querySelector(sel); const r = c.getBoundingClientRect();
  const ev = (type, x, y) => c.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 7, clientX: r.left + x, clientY: r.top + y, buttons: 1 }));
  ev('pointerdown', 40, 40); for (let i = 1; i <= 20; i++) ev('pointermove', 40 + i * 12, 40 + i * 12); ev('pointerup', 280, 280); }"""

budget = time.time() + 200
while time.time() < budget:
    x = into()
    if x < FOCUS and 18 < (FOCUS - x) <= LEAD: break
    time.sleep(1)

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    a = br.new_context(viewport={"width": 1400, "height": 1000}).new_page(); b = br.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    for pg, w in ((a, "A"), (b, "B")): pg.on("pageerror", lambda e, w=w: problems.append(f"[{w}] {e}"))
    sfx = random.randint(1000, 9999)
    a.goto(f"{BASE}/live/{code}"); reg(a, f"da{sfx}"); a.wait_for_selector(".live")
    b.goto(f"{BASE}/live/{code}"); reg(b, f"db{sfx}"); b.wait_for_selector(".live")

    a.wait_for_selector(".panel.invite", timeout=20000)
    pic = lambda pg: pg.get_by_role("button", name=re.compile(r"^Pictionary"))
    check("invite offers Pictionary", pic(a).count() == 1)
    pic(a).click(); pic(b).click()

    a.wait_for_selector(".game-stage .turn-order li", timeout=60000); b.wait_for_selector(".game-stage .turn-order li", timeout=20000)
    check("the game takes the middle of the room (camera wall hidden, chat beside the board)",
          a.locator(".stage").count() == 0 and a.locator(".game-chat .chat-compose").count() == 1)
    for pg in (a, b): pg.wait_for_selector(".my-word strong", timeout=30000)
    wa, wb = a.locator(".my-word strong").inner_text().strip(), b.locator(".my-word strong").inner_text().strip()
    check("both players hold a private word, and not the same one", wa and wb and wa != wb, f"{wa} / {wb}")
    check("both see the same playing order", a.locator(".turn-order li").count() == b.locator(".turn-order li").count() == 2)
    first, other = (a, b) if "play first" in a.locator(".my-word").inner_text() else (b, a)
    check("the word is not shown to the other player", (wa if first is a else wb) not in other.inner_text(".game-stage"))

    old = first.locator(".my-word strong").inner_text().strip()
    first.get_by_role("button", name=re.compile(r"^Too hard\? Change my word")).click(); first.wait_for_timeout(800)
    new = first.locator(".my-word strong").inner_text().strip()
    check("a hard word can be swapped for another one", new and new != old and new != (wb if first is a else wa), f"{old} -> {new}")

    first.evaluate(SKETCH, ".prep-board .draw-canvas"); first.wait_for_timeout(300)
    check("the practice board takes a sketch", first.evaluate(INK, ".prep-board .draw-canvas") > 0)
    first.screenshot(path=f"{SHOTS}/draw-prep.png")

    first.wait_for_selector(".game-focus .draw-canvas.editable", timeout=40000); other.wait_for_selector(".game-focus .draw-canvas", timeout=10000)
    word = first.locator(".prompt-big").inner_text().split(":", 1)[1].strip()
    check("the performer draws the swapped word", word == new, word)
    first.wait_for_timeout(1500)
    check("the sketch goes live to the watcher, centre stage", other.evaluate(INK, ".game-focus .draw-canvas") > 0)
    other.screenshot(path=f"{SHOTS}/draw-stage.png")

    core = word.split()[-1]
    near = core[:-1] + ("x" if core[-1] != "x" else "y")
    other.locator(".chat-compose input").fill(near); other.locator(".chat-compose button").click(); other.wait_for_timeout(800)
    check("a near miss tells the guesser 'Close answer!'", other.locator(".chat-close").count() == 1)
    check("…and is blurred for the performer", first.locator(".chat-line.blurred").count() == 1)

    other.locator(".chat-compose input").fill(core); other.locator(".chat-compose button").click()
    other.wait_for_selector(".game-focus .turn-reveal", timeout=6000)
    check("the correct guess is announced", any("guessed it" in x for x in other.locator(".chat-sys").all_inner_texts()))
    check("…but never shown as text", core not in " ".join(first.locator(".chat-line").all_inner_texts()))
    check("the word is revealed when the turn ends", word in other.inner_text(".turn-reveal"))
    other.wait_for_selector(".game-focus .draw-canvas.editable", timeout=10000)
    check("the turn passes to the next player", other.locator(".prompt-big").count() == 1)
    other.screenshot(path=f"{SHOTS}/draw-after.png")
    br.close()
print("PROBLEMS:", problems or "none")
