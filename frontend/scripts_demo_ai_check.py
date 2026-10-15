"""Browser check: upload a GENERIC sample in the static demo and verify how it was parsed.
Usage: python scripts_demo_ai_check.py <base url> <expect: llm|mock>"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8099/sopcheck").rstrip("/")
EXPECT = (sys.argv[2] if len(sys.argv) > 2 else "llm").upper()
CHROME = "/usr/bin/google-chrome"
SAMPLE = """Boiler Room Daily Startup Procedure
Purpose: safe daily startup of the low-pressure steam boiler.
1. Operator: confirm the fuel supply valve is open and the area is clear of combustibles before startup.
2. Operator: check the water level sight glass reads between the low and high marks; record the level in inches.
3. Operator: take a photo of the burner flame pattern after ignition and attach it to the log.
4. Shift supervisor: verify the pressure relief valve tag is current and sign the startup log.
5. Operator and a second qualified operator: both initial the low-water cutoff test result before the boiler goes on line.
"""
res = []
def ok(c, m): res.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"{BASE}/login/?reset-demo=1")
    pg.fill("[data-testid=email]", "manager@demo.co"); pg.fill("[data-testid=password]", "Manager!2026"); pg.click("[data-testid=signin]")
    pg.wait_for_url("**/manager/**", timeout=120000)
    expect(pg.locator("[data-testid=doc-list] button").first).to_be_visible(timeout=120000)
    label = pg.locator("#demo-badge-label").inner_text()
    ok(("AI parsing" in label) == (len(sys.argv) <= 3 and EXPECT == "LLM" or (len(sys.argv) > 3)), f"badge reflects mode: {label.strip()}")
    if len(sys.argv) > 3 and sys.argv[3] == "kill-proxy-after-load":      # simulate proxy outage mid-session
        import subprocess; subprocess.run("pkill -f workerd; pkill -f 'wrangler dev'", shell=True); time.sleep(2)
    tf = Path("/tmp/demo_ai_sample.txt"); tf.write_text(SAMPLE)
    t = time.time()
    pg.locator("input[type=file]").first.set_input_files(str(tf))
    expect(pg.locator("[data-testid=doc-list] button", has_text="Boiler").first).to_be_visible(timeout=180000)
    ok(True, f"upload finished in {time.time()-t:.1f}s")
    pg.locator("[data-testid=doc-list] button", has_text="Boiler").first.click()
    stat = pg.get_by_text("Parser", exact=True).locator("xpath=..")
    expect(stat).to_be_visible(timeout=20000)
    txt = stat.inner_text().replace("\n", " ")
    ok(EXPECT in txt.upper(), f"parser stat: {txt}")
    if EXPECT == "LLM": ok("GPT-4.1" in txt.upper(), "model gpt-4.1")
    body = pg.inner_text("body"); ok("fuel" in body.lower() and ("T-001" in body or "T-00" in body), "tasks rendered from the sample")
    pg.screenshot(path=str(Path(__file__).resolve().parents[1] / "screenshots" / f"pages_demo_upload_{EXPECT.lower()}.png"))
    ok(not errs, f"no page errors {errs}")
    b.close()
sys.exit(0 if all(res) else 1)
