import sys, time, json
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []
def poll(page, expr, ms=20000):
    import time as _t; end=_t.time()+ms/1000
    while _t.time()<end:
        if page.evaluate(expr): return True
        page.wait_for_timeout(200)
    return False

def note(msg):
    print(msg, flush=True)

def check(name, cond, extra=""):
    note(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        problems.append(name)

def watch(page, label):
    page.on("console", lambda m: problems.append(f"[{label}] console {m.type}: {m.text}") if m.type in ("error",) else None)
    page.on("pageerror", lambda e: problems.append(f"[{label}] pageerror: {e}"))

def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo)
    page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    ctx1 = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx1.new_page(); watch(page, "A")

    # ---- Home
    page.goto(BASE); page.wait_for_selector(".hero")
    page.wait_for_timeout(600)
    page.screenshot(path=f"{SHOTS}/01-home.png")
    check("home hero shows interactive figure", page.locator(".hero .sheet svg").count() == 1)
    register(page, "tester_755712421")
    page.wait_for_selector(".progress")
    check("registered, progress panel on home", page.locator(".progress").count() == 1)

    # ---- Solo study opens straight on the tutor (no learning-style quiz any more)
    page.get_by_role("link", name="Solo study").first.click()
    page.wait_for_selector(".welcome")
    check("solo study opens without a quiz", page.locator(".diag").count() == 0)

    # ---- Explain Thales
    page.locator(".composer input").fill("Thales theorem"); page.locator(".composer .send").click()
    page.wait_for_selector(".thinking")
    page.screenshot(path=f"{SHOTS}/03-loading.png")
    page.wait_for_selector(".explain", timeout=20000)
    page.wait_for_timeout(400)
    check("thales illustration rendered beside the text", page.locator(".explain .sheet svg").count() == 1 and page.locator(".explain-text .steps li").count() >= 3)
    before = page.locator(".fig-callout-title mark").inner_text()
    page.locator(".fig-handle").scroll_into_view_if_needed(); box = page.locator(".fig-handle").bounding_box()
    page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
    page.mouse.down(); page.mouse.move(box["x"] + 5, box["y"] + 70, steps=8); page.mouse.up()
    after = page.locator(".fig-callout-title mark").inner_text()
    check("dragging D changes the ratio", before != after, f"{before} -> {after}")
    page.locator(".steps button").nth(1).click()
    check("selecting a step highlights the small triangle", page.locator(".fig-hot-fill.strong").count() == 1)
    page.locator(".explain").screenshot(path=f"{SHOTS}/04-explain-thales.png")
    page.get_by_role("button", name="Previous", exact=False).count()

    # ---- Hints
    page.get_by_label("What are you trying to work out?").fill("find DE when AD = 3 and AB = 9")
    for label in ["Give me a nudge", "Name the key idea", "Show the first step"]:
        page.get_by_role("button", name=label).click(); page.wait_for_timeout(1500)
    check("three hint levels", page.locator(".hints li").count() == 3)

    # ---- Quiz
    page.wait_for_selector("text=Question 1 of 4", timeout=20000)
    page.locator(".panel:has-text('Check yourself') .option").first.click()
    page.wait_for_selector(".feedback")
    check("quiz gives feedback with a reason", "Thales" in page.locator(".feedback").inner_text() or len(page.locator(".feedback").inner_text()) > 15)
    page.screenshot(path=f"{SHOTS}/05-hints-quiz.png", full_page=True)

    # ---- Other templates
    for topic, tmpl in [("Quadratic functions", "interactive graph"), ("Photosynthesis", "step-through"), ("Causes of the French Revolution", "concept map")]:
        n_before = page.locator(".explain").count()
        page.get_by_label("What do you want to understand?").fill(topic)
        page.locator(".composer .send").click()
        poll(page, f"document.querySelectorAll('.explain').length > {n_before}")
        page.wait_for_timeout(500)
        cap = page.locator(".sheet-cap").last.inner_text().lower()
        check(f"{topic} -> {tmpl}", tmpl in cap, cap.replace("\n", " "))
        page.locator(".explain .sheet").last.screenshot(path=f"{SHOTS}/06-{topic.split()[0].lower()}.png")

    # ---- Resilience demo: large model busy -> fallback
    page.get_by_role("button", name="AI").first.click()
    page.get_by_role("button", name="Make the large model busy").click()
    page.keyboard.press("Escape")
    page.get_by_label("What do you want to understand?").fill("How does a transistor switch work")
    n_before = page.locator(".explain").count()
    page.locator(".composer .send").click()
    page.wait_for_selector(".thinking")
    poll(page, f"document.querySelectorAll('.explain').length > {n_before}", 30000)
    page.wait_for_timeout(400)
    txt = page.locator(".meta").last.inner_text()
    check("fallback is announced to the user", "larger model was busy" in txt, txt)
    page.get_by_role("button", name="AI").first.click()
    page.screenshot(path=f"{SHOTS}/07-ai-panel.png")
    page.get_by_role("button", name="Back to normal").click()

    # ---- Group room, two users
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
    page2 = ctx2.new_page(); watch(page2, "B")
    page2.goto(BASE + "/rooms"); register(page2, "tester_12866231")
    page.goto(BASE + "/rooms"); page.wait_for_selector(".lobby")
    page.get_by_label("Room name").fill("Geometry night")
    page.get_by_role("button", name="Create room").click()
    page.wait_for_selector(".room")
    code = page.locator(".code").inner_text()
    page2.wait_for_selector(".lobby"); page2.get_by_label("Room code").fill(code); page2.get_by_role("button", name="Join room").click()
    page2.wait_for_selector(".room")
    page.wait_for_timeout(800)
    check("both people are listed", page.locator(".members li").count() == 2, str(page.locator(".members li").count()))
    page.get_by_label("Topic for the room").fill("Thales theorem"); page.get_by_role("button", name="Set topic").click()
    page2.wait_for_selector("text=Thales theorem")
    page.get_by_role("button", name="Explain for the room").click()
    page2.wait_for_selector(".explain", timeout=20000)
    check("explanation is shared with the other person", page2.locator(".explain .sheet svg").count() == 1)
    page2.get_by_role("textbox", name="Message").fill("I do not get step 3"); page2.get_by_label("Send anonymously").check()
    page2.get_by_role("button", name="Send", exact=True).click()
    page.wait_for_selector(".log li.anon")
    check("anonymous message hides the author", "tester_12866231" not in page.locator(".log li.anon").inner_text())
    page.get_by_role("button", name="Start a shared quiz").click()
    page2.wait_for_selector(".rq", timeout=20000)
    page.locator(".rq .option").first.click(); page2.locator(".rq .option").nth(1).click()
    page.get_by_role("button", name="Start for everyone").click()
    page2.wait_for_selector(".ring")
    check("shared timer reaches the other person", page2.locator(".ring").count() == 1)
    page.get_by_role("button", name="Reveal answers for everyone").click()
    page2.wait_for_selector(".scores")
    page.screenshot(path=f"{SHOTS}/08-group-room.png", full_page=True)

    # third person is refused when full? capacity 6 here, so test via lobby listing instead
    page.goto(BASE + "/rooms"); page.wait_for_selector(".room-list li")
    check("lobby lists the room", "Geometry night" in page.locator(".room-list").inner_text())
    page.screenshot(path=f"{SHOTS}/09-lobby.png")

    # ---- Live
    page.goto(BASE + "/live"); page.wait_for_selector(".live-list li")
    page.screenshot(path=f"{SHOTS}/10-live-list.png")
    page.get_by_role("button", name="Enter room").first.click()
    page.wait_for_selector(".ring")
    page2.goto(BASE + "/live/LIVE-DEEP"); page2.wait_for_selector(".ring")
    page.wait_for_timeout(2500)
    check("live room counts both people", page.locator(".big-num").inner_text() == "2", page.locator(".big-num").inner_text())
    page.get_by_role("button", name="Send 🔥").click()
    page.wait_for_timeout(300)
    page.screenshot(path=f"{SHOTS}/11-live-room.png")

    # ---- mobile
    ctx3 = browser.new_context(viewport={"width": 390, "height": 800}, is_mobile=True)
    pm = ctx3.new_page(); watch(pm, "M")
    pm.goto(BASE); pm.wait_for_selector(".hero"); pm.wait_for_timeout(500)
    pm.screenshot(path=f"{SHOTS}/12-mobile-home.png", full_page=True)
    overflow = pm.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("no horizontal scroll on mobile home", not overflow)
    browser.close()

print("\nPROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
