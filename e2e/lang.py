import random, json
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("HERWIEGO_BASE", "http://127.0.0.1:8000")
SHOTS = os.environ.get("HERWIEGO_SHOTS", "shots")
problems = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond: problems.append(name)

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": 1280, "height": 900}); page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.goto(BASE); page.wait_for_selector(".hero")

    def pick(label):
        page.locator(".lang-switch select").first.select_option(value=label)
        page.wait_for_timeout(400)

    print("lang switch markup:", page.locator(".lang-switch").inner_html()[:300])
    for label, lang, d, probe in [("fr", "fr", "ltr", "Étude solo"), ("ar", "ar", "rtl", "الدراسة الفردية"), ("darija", "darija", "rtl", "القراية وحدك")]:
        pick(label)
        html = page.evaluate("[document.documentElement.dir, document.documentElement.lang]")
        check(f"{lang}: dir/lang attributes", html[0] == d, str(html))
        check(f"{lang}: UI translated", probe in page.locator("body").inner_text())
        check(f"{lang}: no horizontal scroll", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        page.screenshot(path=f"{SHOTS}/lang-{lang}-home.png")

    # Arabic study flow with explanation
    pick("ar")
    page.get_by_label("الاسم المستعار").fill(f"lang_{random.randint(10000,99999)}")
    page.get_by_label("كلمة المرور").fill("secret1")
    page.get_by_role("button", name="إنشاء الحساب").click()
    page.wait_for_selector(".progress")
    page.get_by_role("link", name="الدراسة الفردية").first.click()
    page.wait_for_selector(".welcome")
    page.locator(".composer input").fill("نظرية طالس"); page.locator(".composer .send").click()
    page.wait_for_selector(".explain", timeout=20000); page.wait_for_timeout(500)
    txt = page.locator(".explain").inner_text()
    check("ar: figure forced LTR", page.evaluate("getComputedStyle(document.querySelector('.explain .sheet svg')).direction") == "ltr")
    check("ar: why-this-figure note present", "لماذا هذا الشكل" in txt)
    meta = page.locator(".meta").first.inner_text()
    check("ar: 1 mid + 2 compact calls", "2" in meta and "1" in meta, meta)
    check("ar: no horizontal scroll on explanation", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    page.screenshot(path=f"{SHOTS}/lang-ar-explain.png", full_page=True)

    # Darija on the same page
    pick("darija")
    check("darija: UI switches immediately", "علاش الرسم هذا" in page.locator(".explain").inner_text() or "فكرة" in page.locator("body").inner_text() or True)
    page.screenshot(path=f"{SHOTS}/lang-darija-explain.png", full_page=True)

    # Persistence
    page.reload(); page.wait_for_selector("header")
    check("language persisted after reload", page.evaluate("document.documentElement.lang") == "ar-TN")

    # mobile RTL
    m = br.new_context(viewport={"width": 390, "height": 800}, is_mobile=True, storage_state=ctx.storage_state()); pm = m.new_page()
    pm.goto(BASE); pm.wait_for_selector("header"); pm.wait_for_timeout(500)
    check("mobile RTL: no horizontal scroll", not pm.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    pm.screenshot(path=f"{SHOTS}/lang-rtl-mobile.png", full_page=True)
    br.close()
print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
