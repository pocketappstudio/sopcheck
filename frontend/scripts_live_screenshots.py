"""Screenshots of the live-LLM-parsed HVAC rooftop-unit SOP (manager dashboard + safety tab, technician view).
Requires the demo reset in live mode (OPENAI_API_KEY exported, ../reset_demo.sh), backend :8000, frontend :3000."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "screenshots"
URL = os.getenv("APP_URL", "http://localhost:3000")
CHROME = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")
MOBILE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}


def login(page, email, pw):
    page.goto(f"{URL}/login")
    page.fill("[data-testid=email]", email); page.fill("[data-testid=password]", pw)
    page.click("[data-testid=signin]")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])
    page = b.new_context(viewport={"width": 1440, "height": 900}, timezone_id="America/Chicago").new_page()
    login(page, "manager@demo.co", "Manager!2026"); page.wait_for_url(f"{URL}/manager")
    page.locator("[data-testid=doc-list] button", has_text="Northwind").click()
    page.wait_for_selector("tr:has-text('compressor amps')", timeout=15000)
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / "live_hvac_manager_dashboard_desktop.png", full_page=True)
    page.click("[data-testid=tab-safety]"); page.wait_for_timeout(800)
    page.screenshot(path=OUT / "live_hvac_manager_safety_desktop.png", full_page=True)

    tp = b.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    login(tp, "taylor@demo.co", "Tech!2026"); tp.wait_for_url(f"{URL}/technician")
    tp.wait_for_timeout(2500)
    print("cards with profile site:", tp.locator("[data-testid=task-card]").count())
    assert tp.locator("[data-testid=task-card]").count() > 0, "profile site not in published sites must not hide all tasks"
    tp.screenshot(path=OUT / "live_hvac_technician_view_mobile.png")
    tp.screenshot(path=OUT / "live_hvac_technician_view_mobile_full.png", full_page=True)
    b.close()
print("ok")
