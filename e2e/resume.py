import os, time, random, json
from playwright.sync_api import sync_playwright
BASE=os.environ.get("EDNOVA_BASE","http://127.0.0.1:8000"); SHOTS=os.environ.get("EDNOVA_SHOTS","shots")
problems=[]
def check(n,c,e=""):
    print(("PASS " if c else "FAIL ")+n+(f"  {e}" if e else ""))
    if not c: problems.append(n)
def reg(pg,ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button",name="Create account").click()
with sync_playwright() as p:
    br=p.chromium.launch(executable_path=os.environ.get("EDNOVA_CHROME") or None,args=["--no-sandbox"])
    pg=br.new_context(viewport={"width":1280,"height":1000}).new_page()
    pg.on("pageerror",lambda e:problems.append(f"[A] {e}"))
    sfx=random.randint(1000,9999)
    pg.goto(BASE); reg(pg,f"rev{sfx}"); pg.wait_for_selector(".progress")
    pg.get_by_role("link",name="Solo study").first.click()
    pg.get_by_role("button",name="Skip for now").click()
    pg.wait_for_selector(".welcome")
    pg.get_by_role("button",name="Thales theorem").click()
    pg.wait_for_selector(".explain",timeout=20000)
    # revision sheet panel in the side rail
    check("revision sheet button is offered", pg.get_by_role("button",name="Generate revision sheet").count()==1)
    pg.get_by_role("button",name="Generate revision sheet").click()
    pg.wait_for_selector(".revision-body",timeout=20000)
    txt=pg.locator(".revision-body").inner_text()
    check("sheet lists a studied topic", "Thales" in txt, txt[:80].replace("\n"," "))
    check("sheet has headings", pg.locator(".revision-body h4").count()>=1)
    pg.screenshot(path=f"{SHOTS}/resume-sheet.png", full_page=True)
    br.close()
print("PROBLEMS:", json.dumps(problems) if problems else "none")
