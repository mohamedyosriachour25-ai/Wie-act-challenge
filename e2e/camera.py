import random, json
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("EDNOVA_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("EDNOVA_SHOTS", "shots")
problems = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)
def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo); page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()

ARGS = ["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]

def poll(page, expr, timeout=12000):
    # wait_for_function needs eval, which our CSP forbids; page.evaluate(string) is allowed, so poll it.
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(200)
    return False

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None, args=ARGS)
    # two people join the same Live room and both turn their camera on
    ca = br.new_context(viewport={"width": 1100, "height": 900}, permissions=["camera"])
    cb = br.new_context(viewport={"width": 1100, "height": 900}, permissions=["camera"])
    a, b = ca.new_page(), cb.new_page()
    for pg, who in ((a, "A"), (b, "B")):
        pg.on("pageerror", lambda e, w=who: problems.append(f"[{w}] {e}"))
    sfx = random.randint(1000, 9999)
    a.goto(BASE + "/live/LIVE-DEEP"); register(a, f"cam_a{sfx}"); a.wait_for_selector(".camera")
    b.goto(BASE + "/live/LIVE-DEEP"); register(b, f"cam_b{sfx}"); b.wait_for_selector(".camera")

    check("camera panel offers to turn the camera on", a.get_by_role("button", name="Turn camera on").count() == 1)
    a.get_by_role("button", name="Turn camera on").click()
    a.wait_for_selector(".vtile")  # local preview appears
    check("A preview has live video", poll(a, "!!document.querySelector('.vtile video') && document.querySelector('.vtile video').videoWidth > 0", 8000))
    check("A sees its own camera preview", a.locator(".vtile").count() == 1)

    b.get_by_role("button", name="Turn camera on").click()
    # once both share, each side should show two tiles (self + peer) with live video
    for pg, who in ((a, "A"), (b, "B")):
        check(f"{who} shows two tiles", poll(pg, "document.querySelectorAll('.vtile').length >= 2", 15000))
        check(f"{who} tiles both have live video", poll(pg, "[...document.querySelectorAll('.vtile video')].length>=2 && [...document.querySelectorAll('.vtile video')].every(v => v.videoWidth > 0)", 15000))
    check("A receives B's video stream", a.locator(".vtile").count() == 2)
    check("B receives A's video stream", b.locator(".vtile").count() == 2)
    a.screenshot(path=f"{SHOTS}/cam-wall.png")

    # A turns the camera off: B drops back to a single tile
    a.get_by_role("button", name="Turn camera off").click()
    check("B back to one tile", poll(b, "document.querySelectorAll('.vtile').length === 1", 8000))
    check("B drops A's tile when A stops sharing", b.locator(".vtile").count() == 1)

    # the privacy note is explicit that video is peer to peer
    check("peer-to-peer privacy note shown", "peer to peer" in a.locator(".panel.camera").inner_text())
    br.close()
print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
