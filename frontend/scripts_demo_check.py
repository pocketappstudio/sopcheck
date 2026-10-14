"""Browser check of the static in-browser demo (GitHub Pages build). Usage: python scripts_demo_check.py <base url ending in /sopcheck>"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8099/sopcheck").rstrip("/")
OUT = Path(__file__).resolve().parents[1] / "screenshots"
CHROME = "/usr/bin/google-chrome"
res = []
def ok(c, m): res.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id="America/Chicago")
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    t = time.time()
    pg.goto(f"{BASE}/login/?reset-demo=1")
    pg.fill("[data-testid=email]", "manager@demo.co"); pg.fill("[data-testid=password]", "Manager!2026"); pg.click("[data-testid=signin]")
    pg.wait_for_url("**/manager/**", timeout=120000)
    expect(pg.locator("[data-testid=doc-list] button").first).to_be_visible(timeout=120000)
    ok(True, f"manager login + dashboard ({time.time()-t:.1f}s incl. demo boot)")
    n = pg.locator("[data-testid=doc-list] button").count(); ok(n >= 5, f"seeded documents listed: {n}")
    expect(pg.locator("[data-testid=ai-banner]")).to_be_visible(timeout=20000); ok(True, "AI-draft banner shown")
    pg.locator("[data-testid=doc-list] button", has_text="Lockout").click()
    expect(pg.locator("text=Review the one-line diagram >> visible=true").first).to_be_visible(timeout=20000); ok(True, "checklist detail renders")
    pg.screenshot(path=OUT / "pages_demo_manager_desktop.png", full_page=False)
    # upload with offline parser
    tf = Path("/tmp/demo_upload.txt"); tf.write_text("Pump Room Weekly Check\n1. Wear safety glasses and gloves.\n2. Lock out the pump motor before inspecting the coupling.\n3. Record discharge pressure in psi.\n4. Photograph the seal area.\n5. Supervisor signs off.\n")
    up = pg.locator("input[type=file]").first
    up.set_input_files(str(tf))
    expect(pg.locator("[data-testid=doc-list] button", has_text="Pump").first).to_be_visible(timeout=60000)
    ok(True, "upload parsed in-browser with offline parser")
    # technician
    tp = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, timezone_id="America/Chicago").new_page()
    tp.goto(f"{BASE}/login/")
    tp.fill("[data-testid=email]", "alex@demo.co"); tp.fill("[data-testid=password]", "Tech!2026"); tp.click("[data-testid=signin]")
    tp.wait_for_selector("[data-testid=terms-gate], [data-testid=task-card]", timeout=120000)
    if tp.locator("[data-testid=terms-gate]").count():
        tp.check("[data-testid=terms-checkbox]"); tp.click("[data-testid=terms-accept]")
    expect(tp.locator("[data-testid=task-card]").first).to_be_visible(timeout=60000)
    ok(tp.locator("[data-testid=task-card]").count() > 0, f"technician view renders ({tp.locator('[data-testid=task-card]').count()} task cards)")
    tp.screenshot(path=OUT / "pages_demo_technician_mobile.png")
    for path, txt in [("/pricing/", "Starter"), ("/terms/", "informational tool"), ("/privacy/", "Privacy")]:
        q = ctx.new_page(); q.goto(BASE + path); expect(q.get_by_text(txt).first).to_be_visible(timeout=120000); ok(True, f"{path} renders")
    ok(not errs, f"no page errors {errs[:2]}")
    b.close()
print(f"\n{sum(res)}/{len(res)} demo checks passed"); sys.exit(0 if all(res) else 1)
