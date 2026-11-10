import os, random
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8000"; S="/home/claude/work/shots"
with sync_playwright() as p:
    br=p.chromium.launch(executable_path="/usr/bin/google-chrome",args=["--no-sandbox"])
    pg=br.new_context(viewport={"width":1280,"height":1000}).new_page()
    pg.goto(BASE+"/study")
    pg.get_by_label("Pseudo").fill(f"ui{random.randint(1000,9999)}"); pg.get_by_label("Password").fill("secret1"); pg.get_by_role("button",name="Create account").click()
    pg.wait_for_selector(".welcome")
    pg.wait_for_selector(".welcome")
    pg.screenshot(path=f"{S}/ui-welcome.png")
    pg.get_by_label("What do you want to understand?").fill("Thales theorem"); pg.get_by_role("button",name="Explain it").click()
    pg.wait_for_selector(".tutor-card .explain", timeout=20000); pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{S}/ui-convo.png", full_page=True)
    br.close()
print("shots done")
