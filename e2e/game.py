import os, time, random, json, urllib.request
from playwright.sync_api import sync_playwright
BASE=os.environ.get("HERWIEGO_BASE","http://127.0.0.1:8000"); SHOTS=os.environ.get("HERWIEGO_SHOTS","shots")
problems=[]
def check(n,c,e=""):
    print(("PASS " if c else "FAIL ")+n+(f"  {e}" if e else ""))
    if not c: problems.append(n)
def reg(pg,ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button",name="Create account").click()
code="LIVE-DEMO"; FOCUS=60; TOTAL=120; LEAD=40
def into(): return time.time()%TOTAL
# wait for an invite window with runway
budget=time.time()+200
while time.time()<budget:
    x=into()
    if x<FOCUS and (FOCUS-x)<=LEAD and (FOCUS-x)>18: break
    time.sleep(1)
with sync_playwright() as p:
    br=p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None,args=["--no-sandbox","--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"])
    ca=br.new_context(viewport={"width":1100,"height":1000},permissions=["camera","microphone"])
    cb=br.new_context(viewport={"width":1100,"height":1000},permissions=["camera","microphone"])
    a=ca.new_page(); b=cb.new_page()
    for pg,w in ((a,"A"),(b,"B")): pg.on("pageerror",lambda e,w=w:problems.append(f"[{w}] {e}"))
    sfx=random.randint(1000,9999)
    a.goto(f"{BASE}/live/{code}"); reg(a,f"ma{sfx}"); a.wait_for_selector(".live")
    b.goto(f"{BASE}/live/{code}"); reg(b,f"mb{sfx}"); b.wait_for_selector(".live")
    a.wait_for_selector(".panel.invite", timeout=20000)
    check("invite offers game choices", a.get_by_role("button",name="Mime").count()==1 and a.get_by_role("button",name="Quiz").count()==1)
    a.get_by_role("button",name="Mime").click()
    b.get_by_role("button",name="Mime").click()
    a.wait_for_timeout(500)
    check("shows chosen game after joining", "Mime" in a.locator(".panel.invite").inner_text())
    a.screenshot(path=f"{SHOTS}/game-pick.png")
    # wait for break + the server pre-roll
    while into()<FOCUS+1: time.sleep(0.5)
    a.wait_for_selector(".game-stage", timeout=15000)
    b.wait_for_selector(".game-stage", timeout=15000)
    check("the game takes the middle of the room", a.locator(".game-stage").count()==1 and a.locator(".stage").count()==0)
    # after the prep time (20 s in short-break rooms), exactly one of them is the performer (has the stage button)
    for pg in (a, b): pg.wait_for_function("() => /Your turn|is playing/.test(document.querySelector('.game-stage .turn-clock')?.textContent || '')", timeout=40000)
    perf_a = a.get_by_role("button",name="Go on stage (camera + mic)").count()==1
    perf_b = b.get_by_role("button",name="Go on stage (camera + mic)").count()==1
    check("one player is the performer (stage + mic button)", perf_a != perf_b)
    performer, watcher = (a,b) if perf_a else (b,a)
    check("performer sees their turn + a prompt", "Your turn" in performer.locator(".turn-clock").inner_text())
    check("watcher sees who is playing now", "is playing" in watcher.locator(".turn-clock").inner_text())
    performer.get_by_role("button",name="Go on stage (camera + mic)").click()
    performer.wait_for_selector(".game-focus .vtile", timeout=8000)
    check("performer goes on stage, centre", performer.locator(".game-focus .vtile").count()==1)
    watcher.wait_for_selector(".game-focus .vtile video", timeout=15000)
    watcher.wait_for_function("() => { const v = document.querySelector('.game-focus .vtile video'); return !!v && v.videoWidth > 0; }", timeout=15000)
    check("the watcher sees the performer's camera centre stage, own camera off", watcher.locator(".game-focus .vtile").count()==1)
    performer.screenshot(path=f"{SHOTS}/game-turn.png", full_page=True)
    watcher.screenshot(path=f"{SHOTS}/game-watch.png", full_page=True)

    # ---- mime guessing: the watcher types the word in the chat, the performer's client judges it
    prompt = performer.locator(".prompt-big").inner_text()       # e.g. "Act out: a cat"
    word = prompt.split(":",1)[1].strip() if ":" in prompt else prompt.strip()
    check("performer has a secret word to act", len(word) > 1, word)
    watcher.locator(".chat-compose input").fill(word)
    watcher.locator(".chat-compose button").click()
    end = time.time() + 12
    while time.time() < end and watcher.locator(".chat-sys").count() == 0:
        watcher.wait_for_timeout(300)
    check("a correct chat guess is announced to the room", watcher.locator(".chat-sys").count() >= 1,
          watcher.locator(".chat-sys").first.inner_text() if watcher.locator(".chat-sys").count() else "no announce")
    watcher.screenshot(path=f"{SHOTS}/game-guessed.png", full_page=True)
    br.close()
print("PROBLEMS:", json.dumps(problems) if problems else "none")
