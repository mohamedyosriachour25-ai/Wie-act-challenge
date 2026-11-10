"""Study forest: QR join link, a tree that grows while the tab is focused, wilts when its owner is away, is
planted at the end of the focus phase, and starts again next session. Uses LIVE-DEMO (1 min focus, 1 min break),
so the whole run takes about 3 to 4 minutes. Set HERWIEGO_QUICK=1 to skip the final 'next session' part."""
import json, os, random, re, tempfile, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
QUICK = bool(os.environ.get("HERWIEGO_QUICK"))
ROOM = "LIVE-DEMO"
ARGS = ["--no-sandbox"]
problems = []

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)

def poll(page, expr, timeout=12000):
    # page.wait_for_function needs eval, which our CSP forbids; page.evaluate(string) is allowed, so poll it.
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(250)
    return False

def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo); page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()

def ring(page):
    """(phase, seconds left) read from the timer ring."""
    phase = "focus" if page.locator(".ring.focus").count() else "break"
    m = re.search(r"(\d+):(\d+)", page.locator(".ring-text strong").inner_text())
    return phase, int(m.group(1)) * 60 + int(m.group(2))

def wait_for_phase(page, phase, min_left, timeout=150):
    end = time.time() + timeout
    while time.time() < end:
        ph, left = ring(page)
        if ph == phase and left >= min_left:
            return left
        page.wait_for_timeout(500)
    return None

def number_after(page, label):
    panel = page.locator(".panel").filter(has=page.get_by_role("heading", name="The forest", exact=True))
    m = re.search(label + r"\s*(\d+)", panel.inner_text())
    return int(m.group(1)) if m else -1

AWAY = "Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'}); document.dispatchEvent(new Event('visibilitychange')); true"
BACK = "delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange')); true"

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=ARGS)
    ca = br.new_context(viewport={"width": 1100, "height": 1100}); cb = br.new_context(viewport={"width": 1100, "height": 1100})
    a, b = ca.new_page(), cb.new_page()
    reqs, errors = [], []
    for pg, who in ((a, "A"), (b, "B")):
        pg.on("pageerror", lambda e, w=who: problems.append(f"[{w}] pageerror: {e}"))
        pg.on("console", lambda m, w=who: m.type == "error" and errors.append(f"[{w}] {m.text}"))
    a.on("request", lambda r: reqs.append((r.method, r.url)))
    sfx = random.randint(1000, 9999)
    ann, bob = f"forest_a{sfx}", f"forest_b{sfx}"

    # ---- the Live list offers the forest, and /forest opens the default forest room
    a.goto(BASE + "/live"); register(a, ann); a.wait_for_selector(".forest-card")
    check("Live list has a study-forest entry", a.locator(".forest-card").count() == 1)
    check("the forest room is not duplicated in the plain list", a.locator(".live-list li").filter(has_text="Study forest").count() == 0)
    a.get_by_role("link", name="Enter the forest").click(); a.wait_for_selector(".forest")
    check("'Enter the forest' goes to /forest/LIVE-FOREST", a.url.endswith("/forest/LIVE-FOREST"))
    a.goto(BASE + f"/forest/{ROOM}"); a.wait_for_selector(".invite-qr .qr")

    # ---- QR: encodes this room's URL and decodes from a real screenshot
    url = a.locator(".qr").get_attribute("data-value")
    check("QR value is this room's join URL", url == f"{BASE}/forest/{ROOM}", url)
    check("the join link is also shown as text", url in a.locator(".join-url").inner_text())
    try:
        import cv2
        png = os.path.join(tempfile.mkdtemp(), "qr.png"); a.locator(".qr").screenshot(path=png)
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(png))
        check("the rendered QR decodes (as a phone camera would) to the join URL", decoded == url, decoded)
    except ImportError:
        print("NOTE: OpenCV not installed; screenshot-decode of the QR skipped (qr:test already decodes the matrix).")
    check("localhost hint is shown", "only works on this computer" in a.locator(".invite-qr").inner_text())

    # ---- "scan": B opens the URL read from A's QR and joins
    left = wait_for_phase(a, "focus", 48, timeout=150)
    check("reached the start of a focus phase", left is not None, f"{left}s left")
    b.goto(url); register(b, bob); b.wait_for_selector(".forest")
    planted0, wilted0 = number_after(a, r"Trees planted here:"), number_after(a, r"Trees that wilted:")

    # ---- a tree grows for A
    check("A's tree is planted and growing", poll(a, "document.querySelector('.tree-status')?.dataset.treeState === 'growing'", 15000))
    p1 = float(a.locator(".tree-big").get_attribute("data-progress")); a.wait_for_timeout(8000)
    p2 = float(a.locator(".tree-big").get_attribute("data-progress"))
    check("A's tree grows over time", p2 > p1, f"{p1} -> {p2}")
    check("A sees both trees in the forest", poll(a, "document.querySelectorAll('.tree-grid li').length >= 2", 15000))
    a.screenshot(path=f"{SHOTS}/forest-growing.png")

    # ---- B leaves the tab for longer than the grace period (15 s in the 1-minute demo room)
    check("B's tree is growing before leaving", poll(b, "document.querySelector('.tree-status')?.dataset.treeState === 'growing'", 15000))
    b.evaluate(AWAY)
    check("B is told to come back (while away)", poll(b, "document.querySelector('.tree-status')?.textContent.includes('Come back')", 8000))
    check("B's tree wilts after too long away", poll(b, "document.querySelector('.tree-status')?.dataset.treeState === 'wilted'", 40000))
    check("A sees B's tree wilted", poll(a, "!!document.querySelector('.tree-grid .tree.wilted')", 15000))
    check("A's own tree is still growing", a.locator(".tree-status").get_attribute("data-tree-state") == "growing")
    b.screenshot(path=f"{SHOTS}/forest-wilted.png")

    # ---- the focus phase ends: A's tree is planted, B's stays wilted, the counters move
    wait_for_phase(a, "break", 40, timeout=90)
    check("A's tree is planted at the end of the focus phase", poll(a, "document.querySelector('.tree-status')?.dataset.treeState === 'grown'", 15000))
    check("A's tree stays visible during the break", a.locator(".tree-status").get_attribute("data-tree-state") == "grown")
    check("planted counter went up", poll(a, "[...document.querySelectorAll('.panel')].find(p => p.querySelector('h3')?.textContent === 'The forest')?.textContent.includes('Trees planted here: %d')" % (planted0 + 1), 15000), f"was {planted0}")
    check("wilted counter went up", number_after(a, r"Trees that wilted:") >= wilted0 + 1, f"was {wilted0}")
    check("B's page shows its tree as wilted", b.locator(".tree-status").get_attribute("data-tree-state") == "wilted")
    a.screenshot(path=f"{SHOTS}/forest-planted.png")

    if not QUICK:
        # ---- B comes back and gets a fresh tree in the next focus session
        b.evaluate(BACK)
        check("next focus session starts", wait_for_phase(a, "focus", 45, timeout=90) is not None)
        check("B plants a new tree once back and focused", poll(b, "document.querySelector('.tree-status')?.dataset.treeState === 'growing'", 20000))
        check("A and B are both growing again", poll(a, "document.querySelectorAll('.tree-grid .tree.growing').length >= 2", 15000))

    # ---- leaving removes the tree from the forest
    b.close()
    check("B's tree disappears from A's forest when B leaves", poll(a, "![...document.querySelectorAll('.tree-grid .who')].some(e => e.textContent.includes('%s'))" % bob, 15000))

    # ---- privacy and hygiene
    origin = urlparse(BASE).netloc
    foreign = sorted({urlparse(u).netloc for _, u in reqs if urlparse(u).scheme in ("http", "https") and urlparse(u).netloc != origin})
    check("no request to any other host", not foreign, str(foreign))
    check("no console errors", not [e for e in errors if "favicon" not in e], str(errors[:3]))
    br.close()
print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
raise SystemExit(1 if problems else 0)
