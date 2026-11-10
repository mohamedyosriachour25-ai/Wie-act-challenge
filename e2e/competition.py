"""Room competition between two learners.

Two people join a study room, the owner sets a topic and starts a competition. Both answer every question
(choice, true/false, number, short answer, exercise) as it comes; the server keeps the time, reveals each
answer after everyone has replied, and ends on a podium that both see identically.
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


def watch(page, label):
    page.on("pageerror", lambda e: problems.append(f"[{label}] pageerror: {e}"))
    page.on("console", lambda m: problems.append(f"[{label}] console: {m.text}") if m.type == "error" else None)


def register(page, pseudo):
    page.get_by_label("Pseudo").fill(pseudo)
    page.get_by_label("Password").fill("secret1")
    page.get_by_role("button", name="Create account").click()


def answer(page, k):
    """Answer the question on screen, if it is still open. Player k picks a different option, so scores differ."""
    q = page.locator(".comp-q")
    if not q.count():
        return False
    opts = q.locator(".comp-opt:not([disabled])")
    if opts.count():
        opts.nth(min(k, opts.count() - 1)).click()
        return True
    box = q.get_by_label("Your answer")
    if box.count() and box.is_enabled():
        hint = box.get_attribute("placeholder") or ""
        box.fill(str(5 + k) if "number" in hint.lower() else "photosynthesis")
        q.get_by_role("button", name="Send").click()
        return True
    return False


sfx = random.randint(10000, 99999)
a_name, b_name = f"comp_a{sfx}", f"comp_b{sfx}"
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=os.environ.get("HERWIEGO_CHROME") or None, args=["--no-sandbox"])
    a = br.new_context(viewport={"width": 1280, "height": 1000}).new_page(); watch(a, "A")
    b = br.new_context(viewport={"width": 1280, "height": 1000}).new_page(); watch(b, "B")

    a.goto(BASE + "/rooms"); register(a, a_name); a.wait_for_selector(".lobby")
    a.get_by_label("Room name").fill("Quiz night"); a.get_by_role("button", name="Create room").click()
    a.wait_for_selector(".room"); code = a.locator(".code").inner_text()
    b.goto(BASE + "/rooms"); register(b, b_name); b.wait_for_selector(".lobby")
    b.get_by_label("Room code").fill(code); b.get_by_role("button", name="Join room").click(); b.wait_for_selector(".room")

    check("competition needs a topic first", a.get_by_role("button", name="Start a competition").is_disabled())
    a.get_by_label("Topic for the room").fill("Photosynthesis"); a.get_by_role("button", name="Set topic").click()
    b.wait_for_selector("text=Photosynthesis")
    a.get_by_role("button", name="Start a competition").click()
    b.wait_for_selector(".comp-countdown, .comp-q", timeout=30000)
    check("the other player sees the competition start", True)
    b.wait_for_selector(".comp-q", timeout=20000)
    a.locator(".competition").screenshot(path=f"{SHOTS}/comp-question.png")
    check("both get the same question", a.locator(".comp-prompt").inner_text() == b.locator(".comp-prompt").inner_text())

    seen_types, reveals, end = set(), 0, time.time() + 240
    while time.time() < end and not a.locator(".comp-over").count():
        if a.locator(".comp-q").count():
            seen_types.add(a.locator(".comp-q .chip").inner_text().strip().lower())
            sent_a, sent_b = answer(a, 0), answer(b, 1)
            if sent_a and sent_b:
                a.wait_for_selector(".comp-reveal, .comp-over", timeout=70000)
                if a.locator(".comp-reveal").count():
                    reveals += 1
                    if reveals == 1:
                        check("the reveal shows the right answer and each player's points",
                              a.locator(".comp-answer strong").count() == 1 and a.locator(".comp-results li").count() == 2)
                        a.locator(".competition").screenshot(path=f"{SHOTS}/comp-reveal.png")
                    a.wait_for_selector(".comp-q, .comp-over", timeout=20000)
                continue
        a.wait_for_timeout(250)

    check("the competition reaches the podium", a.locator(".comp-over").count() == 1, f"{reveals} reveals")
    check("several question kinds were played", len(seen_types) >= 3, ", ".join(sorted(seen_types)))
    b.wait_for_selector(".comp-over", timeout=10000)
    board = lambda pg: [x.strip() for x in pg.locator(".comp-board li").all_inner_texts()]
    check("both players see the same final ranking", board(a) == board(b) and len(board(a)) == 2, " | ".join(board(a)).replace("\n", " "))
    a.locator(".competition").screenshot(path=f"{SHOTS}/comp-podium.png")
    check("a new round can be started", a.get_by_role("button", name="Play again").count() == 1)

    br.close()

print("PROBLEMS:", json.dumps(problems, indent=1) if problems else "none")
