"""Camera attention check in a Live room: opt-in, on-device, releases the camera, nothing leaves the page.

Uses Chromium's fake camera, which shows a test pattern with NO face, so the "away" path is what triggers.
(A real face being recognised as 'facing the screen' has to be tried by hand with a webcam.)
"""
import json, os, random, tempfile, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import _synth_face

BASE = os.environ.get("EDNOVA_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("EDNOVA_SHOTS", "shots")
ARGS = ["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
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

# Count getUserMedia calls and remember every track, so we can prove the camera is released.
INIT = """
window.__gum = { calls: 0, tracks: [] };
const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (c) => { window.__gum.calls++; const s = await orig(c); window.__gum.tracks.push(...s.getTracks()); return s; };
"""
LIVE_TRACKS = "window.__gum.tracks.filter(t => t.readyState === 'live').length"

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None, args=ARGS)
    ctx = br.new_context(viewport={"width": 1100, "height": 1000}, permissions=["camera"])
    ctx.add_init_script(INIT)
    pg = ctx.new_page()
    reqs, console_errors = [], []
    pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
    pg.on("request", lambda r: reqs.append((r.method, r.url)))

    pseudo = f"att{random.randint(1000, 9999)}"
    pg.goto(BASE + "/live/LIVE-DEEP")
    pg.get_by_label("Pseudo").fill(pseudo); pg.get_by_label("Password").fill("secret1")
    pg.get_by_role("button", name="Create account").click()
    pg.wait_for_selector(".attention")
    box = pg.get_by_label("Also use my camera to check that I am facing the screen")

    # 1. off by default, and nothing touches the camera or the model until the learner opts in
    check("attention check is off by default", not box.is_checked())
    check("default copy says no camera is used", "uses no camera" in pg.locator(".attention").inner_text())
    pg.wait_for_timeout(1500)
    check("no camera request before opt-in", pg.evaluate("window.__gum.calls") == 0)
    check("no model download before opt-in", not any("/models/" in u for _, u in reqs))

    in_focus = "Focus." in pg.locator(".center-panel").inner_text()
    box.check()
    check("opt-in copy states the on-device guarantee", "analysed on this device only" in pg.locator(".attention").inner_text())

    if not in_focus:
        # the room is in its break right now: the camera must stay off
        check("break: paused, camera stays off", poll(pg, "document.querySelector('.attention-status')?.textContent.includes('Paused during the break')", 5000))
        check("break: no camera request", pg.evaluate("window.__gum.calls") == 0)
        print("NOTE: room was in its break; the focus-phase checks were skipped. Re-run to cover them.")
    else:
        # 2. opt-in starts the camera and loads the self-hosted model
        check("camera opened after opt-in", poll(pg, "window.__gum.calls >= 1", 10000))
        check("face models fetched from this app", poll(pg, "performance.getEntriesByType('resource').some(e => e.name.includes('/models/blazeface')) && performance.getEntriesByType('resource').some(e => e.name.includes('/models/facemesh'))", 60000))
        check("model reached 'running' (status line shown)", poll(pg, "!!document.querySelector('.attention-status')", 60000))

        # 3. fake camera has no face -> after the grace period the learner shows as away
        got_away = poll(pg, "document.querySelector('.attention-status')?.textContent.includes('No face in view')", 60000)
        check("no face for a while -> 'away' status", got_away, pg.locator(".attention-status").inner_text() if pg.locator(".attention-status").count() else "")
        check("status is styled as away", pg.locator(".attention-status.away").count() == 1)
        check("my presence pip turns off (the boolean reached the room)",
              poll(pg, f"[...document.querySelectorAll('.chips-people li')].some(li => li.textContent.includes('{pseudo}') && li.querySelector('.pip.off'))", 30000))
        pg.screenshot(path=f"{SHOTS}/attention-away.png")

        # 4. privacy: only this app's own origin was contacted, and no image/video was posted anywhere
        origin = urlparse(BASE).netloc
        foreign = sorted({urlparse(u).netloc for _, u in reqs if urlparse(u).scheme in ("http", "https") and urlparse(u).netloc != origin})
        check("no request to any other host", not foreign, str(foreign))
        posts = [u for m, u in reqs if m in ("POST", "PUT") and "/api/auth" not in u]
        check("no upload during the attention check", not posts, str(posts))

        # 5. switching it off releases the camera and restores the default copy
        box.uncheck()
        check("camera released after switching off", poll(pg, LIVE_TRACKS + " === 0", 8000))
        check("status line gone", pg.locator(".attention-status").count() == 0)
        check("default copy restored", "uses no camera" in pg.locator(".attention").inner_text())

        # 6. switching on again reuses the already-loaded model and works again
        before = len([1 for _, u in reqs if "/models/" in u])
        box.check()
        check("camera reopens on second opt-in", poll(pg, "window.__gum.calls >= 2", 10000))
        check("model not downloaded twice", poll(pg, "!!document.querySelector('.attention-status')", 20000) and len([1 for _, u in reqs if "/models/" in u]) == before)
        box.uncheck()
        check("camera released again", poll(pg, LIVE_TRACKS + " === 0", 8000))

    # the strict CSP (no unsafe-eval) must not break the model
    csp = [m for m in console_errors if "Content Security Policy" in m or "Refused to" in m]
    check("no Content-Security-Policy violations", not csp, str(csp[:2]))
    br.close()

    # 7. a face in view -> 'facing the screen' and the learner stays shown as focused (synthetic cartoon face)
    clip = os.path.join(tempfile.mkdtemp(), "face.y4m")
    if in_focus and _synth_face.make(clip):
        br2 = p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None, args=ARGS + [f"--use-file-for-fake-video-capture={clip}"])
        c2 = br2.new_context(viewport={"width": 1100, "height": 1000}, permissions=["camera"]); q = c2.new_page()
        q.on("pageerror", lambda e: problems.append(f"pageerror (face): {e}"))
        who = f"face{random.randint(1000, 9999)}"
        q.goto(BASE + "/live/LIVE-DEEP")
        q.get_by_label("Pseudo").fill(who); q.get_by_label("Password").fill("secret1")
        q.get_by_role("button", name="Create account").click(); q.wait_for_selector(".attention")
        q.get_by_label("Also use my camera to check that I am facing the screen").check()
        check("face in view -> 'You are facing the screen.'", poll(q, "document.querySelector('.attention-status')?.textContent.includes('facing the screen')", 60000))
        check("status is not styled as away", q.locator(".attention-status.away").count() == 0)
        check("my presence pip is on", poll(q, f"[...document.querySelectorAll('.chips-people li')].some(li => li.textContent.includes('{who}') && li.querySelector('.pip.on'))", 15000))
        q.screenshot(path=f"{SHOTS}/attention-facing.png")
        br2.close()
    else:
        print("NOTE: face-in-view check skipped (needs ffmpeg + Pillow + numpy, and the room in its focus phase).")
print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
raise SystemExit(1 if problems else 0)
