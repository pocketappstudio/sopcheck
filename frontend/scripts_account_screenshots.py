"""Browser checks for the Account area: JSON export download, workspace deletion (type name -> locked banner -> cancel),
technician self-delete. Uses a throwaway signup company (never the demo company). Backend :8000, frontend :3000."""
import json, os, time, uuid
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "screenshots"
URL = os.getenv("APP_URL", "http://localhost:3000"); API = os.getenv("API_URL", "http://localhost:8000")
CHROME = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")
MOBILE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
checks = []
def ok(c, m): checks.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)

terms = httpx.get(f"{API}/api/legal/terms").headers["X-Terms-Version"]
company = f"Northwind Facilities {uuid.uuid4().hex[:4]}"
mgr = f"pat.{int(time.time())}@northwind.example"; tech = f"terry.{int(time.time())}@northwind.example"
r = httpx.post(f"{API}/api/auth/signup", json={"company_name": company, "full_name": "Pat Manager", "email": mgr, "password": "manager-pass-123",
               "accept_terms": True, "terms_version": terms}); r.raise_for_status()
httpx.post(f"{API}/api/technicians", headers={"Authorization": f"Bearer {r.json()['token']}"},
           json={"email": tech, "full_name": "Terry Tech", "password": "tech-pass-123", "position": "Operator", "site": "Plant 1"}).raise_for_status()

def login(page, e, pw):
    page.goto(f"{URL}/login"); page.fill("[data-testid=email]", e); page.fill("[data-testid=password]", pw); page.click("[data-testid=signin]")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])
    pg = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id="America/Chicago", accept_downloads=True).new_page()
    login(pg, mgr, "manager-pass-123"); pg.wait_for_url("**/manager**", timeout=20000)
    pg.click("text=Account"); expect(pg.locator("[data-testid=account]")).to_be_visible(timeout=15000)
    body = pg.inner_text("body")
    ok("@" not in body.replace(mgr, "") and "email" not in body.lower(), "account page: no email wording")
    with pg.expect_download() as dl:
        pg.click("[data-testid=export-data]")
    data = json.loads(Path(dl.value.path()).read_text())
    ok(data["workspace"]["company"]["name"] == company and "password_hash" not in json.dumps(data), "manager JSON export downloaded (workspace, no secrets)")
    ok(pg.locator("[data-testid=company-delete]").is_disabled(), "workspace delete disabled until name typed")
    pg.fill("[data-testid=company-delete-confirm]", company)
    pg.screenshot(path=OUT / "sopcheck_account_desktop.png", full_page=True)
    pg.click("[data-testid=company-delete]")
    expect(pg.locator("[data-testid=deletion-scheduled]")).to_be_visible(timeout=15000)
    pg.goto(f"{URL}/manager"); expect(pg.locator("[data-testid=deletion-banner]")).to_be_visible(timeout=15000)
    ok(True, "deletion scheduled -> locked banner on manager page")
    pg.screenshot(path=OUT / "sopcheck_workspace_deletion_pending.png")
    pg.goto(f"{URL}/account"); pg.click("[data-testid=cancel-deletion]")
    expect(pg.locator("[data-testid=account-msg]")).to_contain_text("cancelled", timeout=15000)
    pg.goto(f"{URL}/manager"); pg.wait_for_timeout(1500)
    ok(pg.locator("[data-testid=deletion-banner]").count() == 0, "cancel unlocks workspace")

    tp = b.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    login(tp, tech, "tech-pass-123")
    expect(tp.locator("[data-testid=terms-gate]")).to_be_visible(timeout=15000)
    tp.check("[data-testid=terms-checkbox]"); tp.click("[data-testid=terms-accept]")
    expect(tp.locator("[data-testid=terms-gate]")).to_have_count(0, timeout=15000)
    tp.goto(f"{URL}/account"); expect(tp.locator("[data-testid=self-delete]")).to_be_visible(timeout=15000)
    ok(tp.locator("[data-testid=company-delete]").count() == 0, "technician sees no workspace deletion")
    tp.fill("[data-testid=self-delete-confirm]", "DELETE")
    tp.screenshot(path=OUT / "sopcheck_account_technician_mobile.png", full_page=True)
    tp.click("[data-testid=self-delete]")
    expect(tp.locator("[data-testid=deleted-notice]")).to_be_visible(timeout=15000)
    ok(httpx.post(f"{API}/api/auth/login", json={"email": tech, "password": "tech-pass-123"}).status_code in (401, 403), "technician self-deleted; login disabled")

    lp = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    lp.goto(f"{URL}/login"); expect(lp.get_by_text("Start a free trial").or_(lp.locator("a[href*='signup']")).first).to_be_visible(timeout=15000)
    ok(lp.locator("a[href*='signup']").count() > 0, "login page has signup link")
    lp.screenshot(path=OUT / "sopcheck_login_desktop.png")
    b.close()
print(f"\n{sum(checks)}/{len(checks)} browser checks passed")
