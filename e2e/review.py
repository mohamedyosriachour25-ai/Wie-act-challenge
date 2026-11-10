import os, random, json
from playwright.sync_api import sync_playwright
BASE=os.environ.get("HERWIEGO_BASE","http://127.0.0.1:8000"); SHOTS=os.environ.get("HERWIEGO_SHOTS","shots")
problems=[]
def check(n,c,e=""):
    print(("PASS " if c else "FAIL ")+n+(f"  {e}" if e else ""))
    if not c: problems.append(n)
def reg(pg,ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button",name="Create account").click()
def poll(pg,expr,ms=8000):
    import time; end=time.time()+ms/1000
    while time.time()<end:
        if pg.evaluate(expr): return True
        pg.wait_for_timeout(200)
    return False
with sync_playwright() as p:
    br=p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None,args=["--no-sandbox"])
    ctx=br.new_context(viewport={"width":1100,"height":1000}); pg=ctx.new_page()
    pg.on("pageerror",lambda e:problems.append(f"pageerror: {e}"))
    pg.goto(BASE+"/study"); reg(pg,f"rv{random.randint(1000,9999)}")
    pg.wait_for_selector(".welcome")
    # explain a topic, then take the quiz
    pg.get_by_label("What do you want to understand?").fill("Thales theorem")
    pg.get_by_role("button",name="Explain it").click()
    pg.wait_for_selector(".explain", timeout=20000)
    pg.wait_for_selector(".panel:has-text('Check yourself') .option", timeout=20000)
    for _ in range(8):
        opts=pg.locator(".panel:has-text('Check yourself') .option")
        if opts.count()==0: break
        opts.first.click(); pg.wait_for_timeout(250)
        see=pg.get_by_role("button",name="See my score")
        nxt=pg.get_by_role("button",name="Next question")
        if see.count(): see.click(); break
        if nxt.count(): nxt.click(); pg.wait_for_timeout(250)
    check("quiz added to revision (note shown)", poll(pg,"document.body.innerText.includes('revision') || document.body.innerText.includes('révision')",8000))
    # go to the Revision tab
    pg.get_by_role("link",name="Revision").click()
    pg.wait_for_selector(".review", timeout=8000)
    check("revision has due cards", poll(pg,"document.querySelector('.flashcard')!=null",8000))
    before=pg.locator(".flashcard .front").inner_text()
    pg.get_by_role("button",name="Show answer").click()
    check("flipping reveals the answer", pg.locator(".flashcard .back").count()==1)
    pg.screenshot(path=f"{SHOTS}/review-card.png")
    # grade Good -> card leaves the due pile, next card (or empty)
    pg.get_by_role("button",name="Good").click()
    moved=poll(pg,f"!document.body.innerText.includes({json.dumps(before)}) || document.querySelector('.review .panel .muted')!=null",8000)
    check("grading advances the review", moved)
    br.close()
print("PROBLEMS:", json.dumps(problems) if problems else "none")
