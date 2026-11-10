import random, json, base64
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)
def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo); page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")
open("/tmp/diagram.png", "wb").write(PNG)

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": 1100, "height": 1000}); page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.goto(BASE + "/study"); page.wait_for_selector(".auth-card"); register(page, f"img_{random.randint(1000,9999)}")
    page.wait_for_selector(".welcome")
    page.wait_for_selector(".materials")
    check("empty state mentions images", "image" in page.locator(".materials").inner_text().lower())

    # upload a PNG through the hidden file input
    page.set_input_files(".materials input[type=file]", "/tmp/diagram.png")
    page.wait_for_selector(".gallery .thumb img", timeout=8000)
    check("image appears in the gallery", page.locator(".gallery li").count() == 1)
    check("gallery says images are not searched", "not searched" in page.locator(".gallery-wrap").inner_text())
    # the thumbnail actually loads its bytes (naturalWidth > 0)
    import time
    ok = False
    for _ in range(40):
        if page.evaluate("(() => { const i = document.querySelector('.gallery .thumb img'); return !!i && i.complete && i.naturalWidth > 0; })()"):
            ok = True; break
        page.wait_for_timeout(200)
    check("thumbnail image loads its bytes", ok)

    # lightbox opens
    page.locator(".gallery .thumb").evaluate("e => e.scrollIntoView({block: 'center'})")   # keep it clear of the sticky header
    page.locator(".gallery .thumb").click()
    page.wait_for_selector(".lightbox img")
    check("clicking a thumbnail opens the lightbox", page.locator(".lightbox").count() == 1)
    page.screenshot(path=f"{SHOTS}/img-gallery.png")
    page.get_by_role("button", name="Close").click()
    check("lightbox closes", page.locator(".lightbox").count() == 0)

    # a text note still works alongside the image and stays askable
    page.get_by_role("button", name="Paste notes").click()
    page.get_by_label("Notes").fill("The mitochondrion is the powerhouse of the cell. It produces ATP through respiration, "
                                    "using oxygen and glucose. Chloroplasts, by contrast, capture light for photosynthesis.")
    page.get_by_role("button", name="Add notes").click()
    page.wait_for_selector(".docs li")
    check("text note and image coexist", page.locator(".docs li").count() == 1 and page.locator(".gallery li").count() == 1)
    # remove the image
    page.locator(".gallery li").evaluate("e => e.scrollIntoView({block: 'center'})")
    page.locator(".gallery li").get_by_role("button", name="Remove").click()
    page.wait_for_function if False else None
    ok2 = False
    for _ in range(25):
        if page.locator(".gallery li").count() == 0:
            ok2 = True; break
        page.wait_for_timeout(200)
    check("image can be removed", ok2)
    br.close()
print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
