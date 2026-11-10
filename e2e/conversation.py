import os, random, json
from playwright.sync_api import sync_playwright
BASE=os.environ.get("HERWIEGO_BASE","http://127.0.0.1:8000"); SHOTS=os.environ.get("HERWIEGO_SHOTS","shots")
problems=[]
def check(n,c,e=""):
    print(("PASS " if c else "FAIL ")+n+(f"  {e}" if e else "")); 
    if not c: problems.append(n)
def reg(pg,ps): pg.get_by_label("Pseudo").fill(ps); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button",name="Create account").click()
with sync_playwright() as p:
    br=p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None,args=["--no-sandbox"])
    ca=br.new_context(viewport={"width":1280,"height":950}); a=ca.new_page()
    a.on("pageerror",lambda e:problems.append(f"pageerror: {e}"))
    sfx=random.randint(1000,9999)
    a.goto(BASE+"/rooms"); reg(a,f"al{sfx}")
    a.wait_for_selector("text=Create a room", timeout=15000)
    a.get_by_label("Room name").fill("Geometry session"); a.get_by_role("button",name="Create room").click()
    a.wait_for_selector(".room")
    check("conversation panel present", a.locator(".panel.chat h3").inner_text().strip() != "")
    # set a topic, chat, explain
    a.get_by_label("Topic for the room").fill("Thales theorem"); a.get_by_role("button",name="Set topic").click()
    a.wait_for_timeout(300)
    a.get_by_role("textbox", name="Message").fill("Why are the triangles similar?"); a.get_by_role("button",name="Send",exact=True).click()
    a.wait_for_timeout(300)
    a.get_by_role("button",name="Explain for the room").click()
    a.wait_for_selector(".log.convo .event.explanation", timeout=20000)
    convo=a.locator(".log.convo").inner_text()
    check("chat shows in conversation", "triangles similar" in convo)
    check("topic shows in conversation", "Thales" in convo)
    check("explanation shows in conversation", a.locator(".log.convo .event.explanation").count()>=1)
    # summarise
    a.get_by_role("button",name="Summarise").click()
    a.wait_for_selector(".session-summary", timeout=20000)
    check("session summary appears", len(a.locator(".session-summary").inner_text().strip())>10)
    a.screenshot(path=f"{SHOTS}/room-conversation.png", full_page=True)
    # rejoin in a new context -> history + summary persist
    code=a.url.rsplit("/",1)[-1]
    cb=br.new_context(viewport={"width":1280,"height":950}); b=cb.new_page()
    b.goto(BASE+"/rooms"); reg(b,f"bo{sfx}")
    b.wait_for_selector("text=Have a code?", timeout=15000)
    b.get_by_label("Room code").fill(code); b.get_by_role("button",name="Join room").click()
    b.wait_for_selector(".room")
    b.wait_for_selector(".log.convo .event.explanation", timeout=8000)
    check("rejoin loads saved conversation", "Thales" in b.locator(".log.convo").inner_text())
    check("rejoin shows saved summary", b.locator(".session-summary").count()==1)
    br.close()
print("PROBLEMS:", json.dumps(problems) if problems else "none")
