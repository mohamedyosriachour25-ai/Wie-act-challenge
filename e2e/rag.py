import random, sys, time
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("EDNOVA_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("EDNOVA_SHOTS", "shots")
problems = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)
def watch(page, label):
    page.on("console", lambda m: problems.append(f"[{label}] console: {m.text}") if m.type == "error" else None)
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

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None, args=["--no-sandbox"])
    ctx1 = br.new_context(viewport={"width": 1280, "height": 1000}); page = ctx1.new_page(); watch(page, "A")
    page.goto(BASE + "/study"); page.wait_for_selector(".auth-card"); register(page, a)
    page.wait_for_selector(".diag"); page.get_by_role("button", name="Skip for now").click()
    page.wait_for_selector(".materials")
    check("empty library invites to add material", "Nothing here yet" in page.locator(".materials").inner_text())

    # private data is refused with a clear reason
    page.get_by_role("button", name="Paste notes").click()
    page.get_by_label("Notes").fill("Contact me at someone@example.com " + NOTES)
    page.get_by_role("button", name="Add notes").click()
    page.wait_for_selector(".materials .error-box")
    check("personal data is refused", "e-mail" in page.locator(".materials .error-box").inner_text())
    page.get_by_role("button", name="Cancel").click()

    # real file upload
    page.locator('.materials input[type=file]').set_input_files("/tmp/biology.md")
    page.wait_for_selector(".docs li .status.ready", timeout=15000)
    check("uploaded file becomes Ready", "biology" in page.locator(".docs").inner_text())
    page.screenshot(path=f"{SHOTS}/10-materials-ready.png")

    page.get_by_label("Question", exact=True).fill("Where does photosynthesis take place?")
    page.get_by_role("button", name="Ask", exact=True).click()
    page.wait_for_selector(".answer", timeout=15000)
    txt = page.locator(".answer").first.inner_text()
    check("answer cites the chloroplasts passage", "chloroplasts" in txt.lower() and page.locator(".answer .cite").count() >= 1, txt[:120].replace("\n"," "))
    page.locator(".answer summary").first.click()
    check("sources list is expandable", page.locator(".sources li").count() >= 1)
    page.screenshot(path=f"{SHOTS}/11-answer.png", full_page=True)

    page.get_by_label("Question", exact=True).fill("quantum chromodynamics lattice gauge")
    page.get_by_role("button", name="Ask", exact=True).click()
    page.wait_for_selector(".answer.nf", timeout=10000)
    check("unrelated question says so, without sources", "do not" in page.locator(".answer.nf").inner_text().lower() or "not" in page.locator(".answer.nf").inner_text().lower())

    # grounded explanation
    page.get_by_label("What do you want to understand?").fill("photosynthesis")
    page.get_by_role("button", name="Explain it").click()
    page.wait_for_selector(".explain", timeout=20000)
    check("explanation says it is built from the material", "Built from your material" in page.locator(".grounded").inner_text())
    check("explanation still has an illustration", page.locator(".explain .sheet svg").count() == 1)
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
    page.wait_for_selector(".room .answer", timeout=15000); page2.wait_for_selector(".room .answer", timeout=15000)
    check("both see the same answer", page.locator(".room .answer .a").first.inner_text() == page2.locator(".room .answer .a").first.inner_text())
    check("answer names who asked", b in page.locator(".room .answer .q").first.inner_text())
    page.screenshot(path=f"{SHOTS}/13-room-answer.png", full_page=True)

    page2.set_viewport_size({"width": 390, "height": 800}); page2.wait_for_timeout(300)
    ov = page2.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("no horizontal scroll in room on mobile", not ov)
    br.close()

print("\nPROBLEMS:", problems if problems else "none")
sys.exit(1 if problems else 0)
