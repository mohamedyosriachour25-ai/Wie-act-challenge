"""Leaving a page never loses a generation.

Solo study: ask, leave for another tab while the answer is being written, come back: the answer is picked up
where it is (or already finished) with its figure; after a reload it is still there.
Study room: start an explanation, leave, come back: it is still being shown (in progress or finished).
"""
import json, os, random, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond:
        problems.append(name)


def poll(page, expr, ms=30000):
    end = time.time() + ms / 1000
    while time.time() < end:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(200)
    return False


with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    page = br.new_context(viewport={"width": 1280, "height": 1000}).new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.goto(BASE + "/study")
    page.get_by_label("Pseudo").fill(f"keep_{random.randint(10000, 99999)}")
    page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()
    page.wait_for_selector(".welcome")

    # ---- Solo study: leave while the answer is being written
    topic = f"Explain Thales theorem for case {random.randint(100, 999)}"     # unique, so no cached answer
    page.get_by_label("What do you want to understand?").fill(topic)
    page.locator(".composer .send").click()
    page.wait_for_selector(".stage-trail")
    page.get_by_role("link", name="Study rooms").first.click()       # switch tab mid-generation
    page.wait_for_selector(".lobby")
    page.wait_for_timeout(400)
    page.get_by_role("link", name="Solo study").first.click()
    page.wait_for_selector(".thread")
    resumed = poll(page, "!!document.querySelector('.stage-trail') || !!document.querySelector('.msg-row.tutor .answer')", 8000)
    check("coming back shows the answer (still in progress or finished)", resumed)
    finished = poll(page, "!document.querySelector('.stage-trail') && !!document.querySelector('.msg-row.tutor .answer .sheet svg')", 30000)
    check("the answer finishes with its figure", finished)
    check("the question appears once", page.locator(".msg-row.you").count() == 1, str(page.locator(".msg-row.you").count()))
    page.screenshot(path=f"{SHOTS}/persist-solo.png")
    page.reload(); page.wait_for_selector(".thread")
    poll(page, "!!document.querySelector('.msg-row.tutor .answer .sheet svg')", 8000)
    check("after a reload the answer and figure are still there", page.locator(".msg-row.tutor .answer .sheet svg").count() == 1)

    # ---- Study room: leave while the explanation is generated, come back
    page.goto(BASE + "/rooms"); page.wait_for_selector(".lobby")
    page.get_by_label("Room name").fill("Keep it"); page.get_by_role("button", name="Create room").click()
    page.wait_for_selector(".room")
    url = page.url
    page.get_by_label("Topic for the room").fill(f"Photosynthesis {random.randint(100, 999)}")
    page.get_by_role("button", name="Set topic").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Explain for the room").click()
    page.wait_for_selector(".room-answer .stage-trail", timeout=10000)
    page.goto(BASE + "/live"); page.wait_for_timeout(500)              # leave mid-way
    page.goto(url); page.wait_for_selector(".room")
    shown = poll(page, "!!document.querySelector('.room-answer')", 8000)
    check("back in the room, the explanation is shown (in progress or finished)", shown)
    done = poll(page, "!!document.querySelector('.room-answer .answer .sheet svg') && !document.querySelector('.room-answer .stage-trail')", 30000)
    check("the room explanation finishes with its figure", done)
    page.screenshot(path=f"{SHOTS}/persist-room.png")
    page.goto(BASE + "/live"); page.wait_for_timeout(300); page.goto(url); page.wait_for_selector(".room")
    check("a finished room explanation is still there later", poll(page, "!!document.querySelector('.room-answer .answer .sheet svg')", 8000))
    br.close()

print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
