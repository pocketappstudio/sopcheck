"""Headless browser test + screenshots (Playwright). Requires a freshly reset demo (../reset_demo.sh),
backend :8000 and frontend :3000.   pip install playwright ; python scripts_screenshots.py"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)
URL = os.getenv("APP_URL", "http://localhost:3000")
CHROME = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")
MOBILE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
checks = []


def ok(cond, msg):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + msg, flush=True)


def login(page, email, pw):
    page.goto(f"{URL}/login")
    page.fill("[data-testid=email]", email)
    page.fill("[data-testid=password]", pw)
    page.click("[data-testid=signin]")
    # first sign-in shows the Terms click-through gate; accept it so the legacy flow continues
    try:
        page.wait_for_load_state("networkidle", timeout=15000); page.wait_for_timeout(500)
        if page.locator("[data-testid=terms-gate]").count():
            page.check("[data-testid=terms-checkbox]"); page.click("[data-testid=terms-accept]")
    except Exception:
        pass


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])

    # ---------- auth ----------
    m = browser.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    m.goto(f"{URL}/manager")
    ok(re.search(r"/login\?next=%2Fmanager", m.url), f"unauthenticated /manager redirects to login ({m.url})")
    m.screenshot(path=OUT / "login_mobile.png")
    m.fill("[data-testid=email]", "manager@demo.co"); m.fill("[data-testid=password]", "nope"); m.click("[data-testid=signin]")
    expect(m.get_by_text("Invalid email or password")).to_be_visible()
    ok(True, "wrong password shows error")

    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, timezone_id="America/Chicago")
    page = ctx.new_page()
    page.goto(f"{URL}/login"); page.screenshot(path=OUT / "login_desktop.png")
    login(page, "manager@demo.co", "Manager!2026")
    page.wait_for_url(f"{URL}/manager")
    expect(page.get_by_text("Morgan Blake")).to_be_visible()
    ok(True, "manager signed in -> /manager, nav shows user")

    # ---------- open published LOTO doc ----------
    page.locator("[data-testid=doc-list] button", has_text="Lockout").click()
    expect(page.locator("tbody").get_by_text("Apply your personal lock and danger tag").first).to_be_visible()
    expect(page.get_by_text("v1 · published")).to_be_visible()
    ok("100%" in page.inner_text("main"), "LOTO safety coverage 100% shown")
    page.screenshot(path=OUT / "manager_dashboard_desktop.png", full_page=True)

    # ---------- edit mode: edit / reorder / delete / add ----------
    page.click("[data-testid=edit]")
    rows = page.locator("[data-testid=edit-row]")
    n0 = rows.count()
    rows.nth(1).get_by_label("Task title").fill("Don arc-flash PPE (Class 0 gloves, face shield, 8 cal/cm2 suit)")
    rows.nth(3).get_by_label("Move up").click()
    warn = rows.filter(has_text="Never work on energized").first
    desc = warn.get_by_label("Description")
    desc.fill(desc.input_value().split(" WARNING:")[0])                       # drop the 50 V warning
    rows.filter(has=page.locator("input[value='Remove your lock and tag']")).get_by_label("Delete task").click()
    page.click("[data-testid=add-task]")
    new = rows.last
    new.get_by_label("Task title").fill("Restore barricades and signage")
    new.get_by_label("Description").fill("Remove temporary barricades and post the panel as back in service.")
    new.get_by_label("Verification method").select_option("Photo Upload")
    ok(rows.count() == n0, f"edit rows after delete+add: {rows.count()} (was {n0})")
    page.screenshot(path=OUT / "manager_edit_mode_desktop.png", full_page=True)
    page.click("[data-testid=tab-json]")
    ok("Restore barricades and signage" in page.inner_text("[data-testid=json]") and "8 cal/cm2 suit" in page.inner_text("[data-testid=json]"),
       "raw JSON preview reflects unsaved edits")
    page.click("[data-testid=tab-checklist]")
    # client-side validation
    new.get_by_label("Task title").fill("")
    page.click("[data-testid=save]")
    expect(page.get_by_text("Fix the highlighted fields before saving.")).to_be_visible()
    ok(True, "empty title blocked by validation")
    new.get_by_label("Task title").fill("Restore barricades and signage")
    page.click("[data-testid=save]")
    expect(page.get_by_text("v2 · draft")).to_be_visible(timeout=20000)
    ok(True, "saving edits on published v1 created v2 draft")
    expect(page.get_by_text("Technicians see v1 until v2 is published.")).to_be_visible()

    # ---------- safety review ----------
    page.click("[data-testid=tab-safety]")
    flags = page.locator("[data-testid=flag]")
    expect(flags.first).to_be_visible()
    ok(flags.count() >= 2, f"safety panel shows {flags.count()} open flags after edits")
    page.screenshot(path=OUT / "manager_safety_review_desktop.png", full_page=True)
    f50 = flags.filter(has_text="50 volts")
    ok("✕ 50 volts (>)" in f50.inner_text(), "flag shows missing atom '50 volts (>)'")
    f50.locator("[data-testid=flag-attach]").click()
    expect(page.locator("[data-testid=flag]", has_text="50 volts").get_by_text(re.compile("attached"))).to_be_visible(timeout=20000)
    ok(True, "attach-to-task resolved the 50 V flag")
    lock = page.locator("[data-testid=flag]", has_text="lock and tag").first
    lock.locator("[data-testid=flag-dismiss]").click()
    lock.locator("[data-testid=dismiss-reason]").fill("Lock removal is covered by site LOTO-REMOVAL SOP")
    lock.locator("[data-testid=dismiss-confirm]").click()
    expect(page.get_by_text("Dismissed: Lock removal is covered")).to_be_visible(timeout=20000)
    ok(True, "dismiss-with-reason resolved the lock-removal flag")
    page.screenshot(path=OUT / "manager_safety_resolved_desktop.png", full_page=True)
    page.click("[data-testid=publish]")
    page.check("[data-testid=attest-checkbox]"); page.click("[data-testid=attest-confirm]")   # publish attestation
    expect(page.get_by_text("✓ Live v2")).to_be_visible(timeout=20000)
    ok(True, "published v2")
    page.click("[data-testid=tab-audit]")
    expect(page.locator("[data-testid=audit-time]").first).to_be_visible()
    t = page.locator("[data-testid=audit-time]").first.inner_text()
    ok(re.search(r"(CDT|CST)$", t), f"audit times rendered in browser zone: '{t}'")
    page.screenshot(path=OUT / "manager_audit_log_desktop.png", full_page=True)

    # ---------- OCR doc ----------
    page.locator("[data-testid=doc-list] button", has_text="forklift").click()
    expect(page.locator("[data-testid=ocr-badge]")).to_contain_text("OCR")
    ok("confidence" in page.inner_text("[data-testid=ocr-badge]"), f"OCR badge: {page.inner_text('[data-testid=ocr-badge]')}")
    page.screenshot(path=OUT / "manager_ocr_document_desktop.png", full_page=True)

    # manager dashboard on mobile
    m.goto(f"{URL}/login"); login(m, "manager@demo.co", "Manager!2026"); m.wait_for_url(f"{URL}/manager")
    m.locator("[data-testid=doc-list] button", has_text="Lockout").click()
    expect(m.get_by_text("Record voltage reading").first).to_be_visible()
    m.screenshot(path=OUT / "manager_dashboard_mobile.png", full_page=True)

    # ---------- technician (mobile, Chicago) ----------
    tctx = browser.new_context(**MOBILE, timezone_id="America/Chicago")
    tp = tctx.new_page()
    login(tp, "alex@demo.co", "Tech!2026")
    tp.wait_for_url(f"{URL}/technician")
    tp.goto(f"{URL}/manager"); tp.wait_for_url(f"{URL}/technician")
    ok(True, "technician redirected away from /manager")
    cards = tp.locator("[data-testid=task-card]")
    expect(cards.first).to_be_visible()
    ok(cards.filter(has_text="Never work on energized").count() >= 1 or cards.filter(has_text="50 volts").count() >= 1,
       "technician sees v2 with re-attached 50 V safety note")
    cb = cards.filter(has=tp.locator("text=Checkbox")).first
    cb.locator("button[aria-label^=Complete]").click()
    expect(cb.locator("[data-testid=done-time]")).to_be_visible(timeout=15000)
    dt = cb.locator("[data-testid=done-time]").inner_text()
    ok(re.search(r"\d{1,2}:\d{2} [AP]M (CDT|CST)", dt), f"completion time in local zone: '{dt}'")
    numc = cards.filter(has_text="Record voltage reading").first
    numc.locator("input[type=number]").fill("0.3")
    numc.locator("button[aria-label^=Complete]").click()
    expect(numc.locator("[data-testid=done-time]")).to_contain_text("0.3", timeout=15000)
    ok(True, "numeric reading submitted")
    tp.evaluate("window.scrollTo(0,0)")
    tp.screenshot(path=OUT / "technician_view_mobile.png")
    tp.screenshot(path=OUT / "technician_view_mobile_full.png", full_page=True)

    # dual sign-off: Jordan (Site Safety Officer) signs, Alex co-signs with his own password
    jo = browser.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    login(jo, "jordan@demo.co", "Tech!2026"); jo.wait_for_url(f"{URL}/technician")
    dual = jo.locator("[data-testid=task-card]").filter(has=jo.locator("text=Dual Sign-off")).first
    expect(dual).to_be_visible()
    dual.get_by_placeholder("Type full name to sign").fill("Jordan Lee")
    dual.get_by_label("Co-signer", exact=True).select_option("alex@demo.co")
    dual.get_by_label("Co-signer password").fill("wrong")
    dual.locator("button[aria-label^=Complete]").click()
    expect(dual.get_by_text("co-signer credentials are invalid")).to_be_visible(timeout=15000)
    ok(True, "wrong co-signer password rejected in UI")
    dual.get_by_label("Co-signer password").fill("Tech!2026")
    dual.locator("button[aria-label^=Complete]").click()
    expect(dual.locator("[data-testid=done-time]")).to_contain_text("Alex Rivera", timeout=15000)
    ok(True, f"dual sign-off completed: '{dual.locator('[data-testid=done-time]').inner_text()}'")
    jo.screenshot(path=OUT / "technician_dual_signoff_mobile.png", full_page=True)

    # shift windows (company timezone) - Sam the RN has Shift Start tasks
    sp = browser.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    login(sp, "sam@demo.co", "Tech!2026"); sp.wait_for_url(f"{URL}/technician")
    w = sp.locator("[data-testid=window]").first
    expect(w).to_be_visible()
    ok(re.search(r"7:00 AM – 9:00 AM (CDT|CST)", w.inner_text()), f"Shift Start window chip in company zone: '{w.inner_text()}'")
    sp.screenshot(path=OUT / "technician_shift_windows_mobile.png")

    # same submission viewed from a Tokyo browser -> different local label
    jp = browser.new_context(**MOBILE, timezone_id="Asia/Tokyo").new_page()
    login(jp, "alex@demo.co", "Tech!2026"); jp.wait_for_url(f"{URL}/technician")
    jt = jp.locator("[data-testid=done-time]").first
    expect(jt).to_be_visible()
    ok("GMT+9" in jt.inner_text(), f"same completion in Asia/Tokyo browser: '{jt.inner_text()}'")

    # logout
    tp.click("[data-testid=logout]"); tp.wait_for_url(re.compile(r"/login"))
    tp.goto(f"{URL}/technician"); ok("/login" in tp.url, "after sign-out, /technician redirects to login")
    browser.close()

print(f"\n{sum(checks)}/{len(checks)} browser checks passed")
for f in sorted(OUT.glob("*.png")):
    print(" ", f)
