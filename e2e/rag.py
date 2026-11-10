import time
import random, sys, time
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)
def watch(page, label):
    # the personal-data check answers 400 on purpose; the browser logs that as a failed resource, which is expected here
    page.on("console", lambda m: problems.append(f"[{label}] console: {m.text}") if m.type == "error" and "status of 400" not in m.text else None)
    page.on("pageerror", lambda e: problems.append(f"[{label}] pageerror: {e}"))
def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo); page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()

NOTES = ("Photosynthesis takes place in the chloroplasts of plant cells. Chlorophyll absorbs light energy, mostly red and blue. "
         "The light reactions split water and release oxygen. The Calvin cycle then uses the stored energy to build glucose from carbon dioxide.\n\n"
         "Cellular respiration is the opposite exchange. Mitochondria break glucose down with oxygen to release usable energy as ATP. "
         "Respiration happens day and night, while photosynthesis needs light.")
open("/tmp/biology.md", "w").write(NOTES)
a, b = f"rag_a{random.randint(1000,9999)}", f"rag_b{random.randint(1000,9999)}"

LABEL = "What do you want to understand?"
def tutor_turn(page, text, ms=30000):
    """Ask the solo tutor and wait until the finished answer sits in the thread (cached answers skip the stages)."""
    n = page.locator(".msg-row.tutor .answer").count()
    page.get_by_label(LABEL).fill(text); page.locator(".composer .send").click()
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        if page.evaluate(f"document.querySelectorAll('.msg-row.tutor .answer').length > {n} && !document.querySelector('.stage-trail')"):
            break
        page.wait_for_timeout(200)
    page.wait_for_timeout(300)
    return page.locator(".msg-row.tutor .answer").last


with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    ctx1 = br.new_context(viewport={"width": 1280, "height": 1000}); page = ctx1.new_page(); watch(page, "A")
    page.goto(BASE + "/study"); page.wait_for_selector(".auth-card"); register(page, a)
    page.wait_for_selector(".welcome")
    page.wait_for_selector(".materials")
    check("empty library invites to add material", "Nothing here yet" in page.locator(".materials").inner_text())

    # personal details are removed, not refused: the notes are added and the panel says what was taken out
    page.get_by_role("button", name="Paste notes").click()
    page.get_by_label("Notes").fill("Contact me at someone@example.com " + NOTES)
    page.get_by_role("button", name="Add notes").click()
    page.wait_for_selector(".docs li .status.ready", timeout=15000)
    check("notes with an e-mail are accepted, the e-mail removed", "personal details removed" in page.locator(".docs").inner_text(), page.locator(".docs").inner_text().replace("\n", " ")[:120])
    page.locator(".docs li").first.get_by_role("button", name="Remove").click()
    page.wait_for_timeout(600)

    # a scanned PDF (no text layer) is added at once and its pages are read by the OCR model
    from PIL import Image, ImageDraw
    pages = []
    for k in range(2):
        im = Image.new("RGB", (620, 880), "white"); ImageDraw.Draw(im).text((40, 40), f"Scanned page {k + 1}", fill="black"); pages.append(im)
    pages[0].save("/tmp/scanned-course.pdf", "PDF", save_all=True, append_images=pages[1:])
    page.locator('.materials input[type=file]').set_input_files("/tmp/scanned-course.pdf")
    page.wait_for_selector(".docs li .status.reading, .docs li .status.ready", timeout=10000)
    page.wait_for_selector(".docs li .status.ready", timeout=30000)
    txt = page.locator(".docs").inner_text()
    check("scanned PDF becomes Ready after OCR", "scanned pages read" in txt, txt.replace("\n", " ")[:160])
    check("the original PDF can be opened", page.locator(".docs li a", has_text="Open").count() == 1)
    page.screenshot(path=f"{SHOTS}/10-scanned-pdf.png")
    page.locator(".docs li").first.get_by_role("button", name="Remove").click()
    page.wait_for_timeout(600)

    # real file upload
    page.locator('.materials input[type=file]').set_input_files("/tmp/biology.md")
    page.wait_for_selector(".docs li .status.ready", timeout=15000)
    check("uploaded file becomes Ready", "biology" in page.locator(".docs").inner_text())
    page.screenshot(path=f"{SHOTS}/10-materials-ready.png")

    # the solo tutor answers from the space's material and cites it
    ans = tutor_turn(page, "Where does photosynthesis take place?")
    txt = ans.inner_text()
    check("answer cites the chloroplasts passage", "chloroplasts" in txt.lower() and ans.locator(".sources li.material").count() >= 1, txt[:120].replace("\n"," "))
    ans.locator(".sources summary").first.click()
    check("sources list is expandable", ans.locator(".sources details[open]").count() >= 1)
    page.screenshot(path=f"{SHOTS}/11-answer.png", full_page=True)

    ans = tutor_turn(page, "quantum chromodynamics lattice gauge")
    check("unrelated question is answered without material sources", ans.locator(".sources li.material").count() == 0)

    # grounded explanation with a figure
    ans = tutor_turn(page, "Explain photosynthesis")
    check("explanation is built from the material", ans.locator(".sources li.material").count() >= 1)
    check("explanation still has an illustration", ans.locator(".sheet svg").count() == 1)
    page.screenshot(path=f"{SHOTS}/12-grounded-explain.png", full_page=True)

    # ---- room: two people share material and answers
    page.goto(BASE + "/rooms"); page.wait_for_selector("text=Create a room", timeout=8000)
    page.get_by_label("Room name").fill("Biology night"); page.get_by_role("button", name="Create room").click()
    page.wait_for_selector(".room"); url = page.url
    page.locator('.materials input[type=file]').set_input_files("/tmp/biology.md")
    page.wait_for_selector(".docs li .status.ready", timeout=15000)
    ctx2 = br.new_context(viewport={"width": 1280, "height": 1000}); page2 = ctx2.new_page(); watch(page2, "B")
    page2.goto(url); page2.wait_for_selector(".auth-card"); register(page2, b)
    page2.wait_for_selector(".room"); page2.wait_for_selector(".docs li .status.ready", timeout=8000)
    check("second person sees the shared document", "biology" in page2.locator(".docs").inner_text())
    page2.get_by_label("Question for everyone").fill("What do mitochondria do?")
    page2.get_by_role("button", name="Ask", exact=True).click()
    page.wait_for_selector(".room-qa:not(.live) .answer", timeout=30000); page2.wait_for_selector(".room-qa:not(.live) .answer", timeout=30000)
    a1 = page.locator(".room-qa:not(.live) .answer .md").first.inner_text(); a2 = page2.locator(".room-qa:not(.live) .answer .md").first.inner_text()
    check("both see the same answer", a1 == a2 and len(a1) > 10, a1[:80].replace("\n", " "))
    check("answer cites the room material", page.locator(".room-qa:not(.live) .sources li.material").count() >= 1)
    check("answer names who asked", b in page.locator(".room-qa:not(.live) .q").first.inner_text())
    page.screenshot(path=f"{SHOTS}/13-room-answer.png", full_page=True)

    page2.set_viewport_size({"width": 390, "height": 800}); page2.wait_for_timeout(300)
    ov = page2.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("no horizontal scroll in room on mobile", not ov)
    br.close()

print("\nPROBLEMS:", problems if problems else "none")
sys.exit(1 if problems else 0)
