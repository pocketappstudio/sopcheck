"""Screenshots + browser checks for pricing, Terms click-through, AI banner, publish attestation, technician footer.
Requires a freshly reset demo (../reset_demo.sh), backend :8000 and frontend :3000."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "screenshots"
URL = os.getenv("APP_URL", "http://localhost:3000")
CHROME = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")
MOBILE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
checks = []


def ok(c, m):
    checks.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)


def login(page, email, pw):
    page.goto(f"{URL}/login"); page.fill("[data-testid=email]", email); page.fill("[data-testid=password]", pw)
    page.click("[data-testid=signin]")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None, args=["--no-sandbox"])
    pg = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id="America/Chicago").new_page()
    pg.goto(f"{URL}/pricing"); expect(pg.get_by_text("Starter")).to_be_visible(timeout=15000)
    txt = pg.inner_text("body")
    ok("$49" in txt and "$99" in txt and "14 days" in txt and "@" not in txt, "pricing page: $49 / $99 / 14-day trial, no email address")
    pg.screenshot(path=OUT / "sopcheck_pricing_desktop.png", full_page=True)
    pg.goto(f"{URL}/terms"); expect(pg.locator("[data-testid=legal-doc] h1")).to_be_visible(timeout=15000)
    t = pg.inner_text("[data-testid=legal-doc]")
    ok("as is" in t.lower() and "SOPCheck" in t and "Gumroad" in t and "October 12, 2026" in t and "{{" not in t and "@" not in t and "LLC" not in t,
       "terms rendered: operator SOPCheck, as-is, Gumroad, dated, no placeholders/email")
    must = ["informational tool only", "does not direct", "as is", "may be wrong", "official SOP", "no professional or safety advice", "workers", "sites",
            "Acceptable use", "Gumroad", "Termination", "Changes"]
    banned = ["arbitrat", "jury", "class action", "indemnif", "release", "covenant not to sue", "governing law", "arkansas", "one (1) year", "$50"]
    missing = [m for m in must if m.lower() not in t.lower()]
    present = [b for b in banned if b in t.lower()]
    ok(not missing and not present, f"simple terms: required sections present (missing: {missing}), removed clauses absent (found: {present})")
    pg.goto(f"{URL}/privacy"); expect(pg.locator("[data-testid=legal-doc] h1")).to_be_visible(timeout=15000)
    pv = pg.inner_text("[data-testid=legal-doc]")
    ok("Gumroad" in pv and "October 12, 2026" in pv and not re.search(r"\[(?!ATTORNEY)[A-Z ]{3,}", pv) and "@" not in pv and "mail to" not in pv,
       "privacy rendered: Gumroad messaging only, dated, no placeholders/address")
    pg.screenshot(path=OUT / "sopcheck_terms_page_desktop.png")

    # first login of a technician -> terms gate (reset taylor's acceptance so the gate is exercised on every run)
    import sqlite3
    _db = Path(__file__).resolve().parents[1] / "backend" / "data" / "app.db"
    if _db.exists():
        _c = sqlite3.connect(_db); _c.execute("update users set terms_version=NULL where email='taylor@demo.co'"); _c.commit(); _c.close()
    tp = b.new_context(**MOBILE, timezone_id="America/Chicago").new_page()
    login(tp, "taylor@demo.co", "Tech!2026")
    expect(tp.locator("[data-testid=terms-gate]")).to_be_visible(timeout=15000)
    gate = tp.inner_text("[data-testid=terms-gate]")
    ok("informational tool only" in gate and "may be wrong" in gate and "as is" in gate and "@" not in gate
       and not any(b in gate.lower() for b in ("arbitrat", "indemnif", "release", "jury")),
       "acceptance screen: simple summary (informational, AI may be wrong, as is); no legal extras, no email")
    ok(tp.locator("[data-testid=terms-accept]").is_disabled(), "terms gate shown on first login; Accept disabled until checkbox ticked")
    tp.check("[data-testid=terms-checkbox]")
    tp.screenshot(path=OUT / "sopcheck_terms_acceptance_mobile.png", full_page=True)
    tp.click("[data-testid=terms-accept]")
    expect(tp.locator("[data-testid=task-card]").first).to_be_visible(timeout=15000)
    expect(tp.locator("[data-testid=tech-footer]")).to_contain_text("this checklist is an aid")
    ok(True, "after acceptance technician sees checklist + footer disclaimer")
    tp.locator("[data-testid=tech-footer]").scroll_into_view_if_needed()
    tp.screenshot(path=OUT / "sopcheck_technician_footer_mobile.png")

    # manager: banner + publish attestation on the draft forklift SOP
    # make sure the forklift SOP has an unpublished draft (saving the current checklist creates a new draft version; no LLM call)
    import httpx
    API = os.getenv("API_URL", "http://localhost:8000")
    lr = httpx.post(f"{API}/api/auth/login", json={"email": "manager@demo.co", "password": "Manager!2026"}).json()
    H = {"Authorization": f"Bearer {lr['token']}"}
    if lr["user"]["needs_terms"]:
        pass   # accepted in the browser below (re-acceptance after a TERMS_VERSION bump)
    login(pg, "manager@demo.co", "Manager!2026"); pg.wait_for_url(f"{URL}/manager")
    pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(500)
    if pg.locator("[data-testid=terms-gate]").count():
        pg.check("[data-testid=terms-checkbox]"); pg.click("[data-testid=terms-accept]")
        expect(pg.locator("[data-testid=terms-gate]")).to_have_count(0, timeout=15000)
        ok(True, "manager re-accepts after TERMS_VERSION bump")
    docs = httpx.get(f"{API}/api/documents", headers=H).json()
    fk = next(d for d in docs if "forklift" in d["title"].lower())
    det = httpx.get(f"{API}/api/documents/{fk['id']}", headers=H).json()
    if det["document"].get("status") == "published" or det["run"].get("status") == "published":
        tasks = [{k: v for k, v in t.items() if k not in ("id", "_key", "document_id", "parse_run_id", "company_id", "safety_notes")} for t in det["checklist"]["tasks"]]
        r = httpx.put(f"{API}/api/documents/{fk['id']}/checklist", headers=H, json={"base_run_id": det["run"]["id"], "tasks": tasks})
        print("draft created:", r.status_code, r.text[:200] if r.status_code >= 300 else "")
    pg.reload(); pg.wait_for_load_state("networkidle")
    expect(pg.locator("[data-testid=ai-banner]")).to_contain_text("AI-generated draft")
    ok(True, "manager dashboard shows persistent AI-draft banner")
    expect(pg.locator("[data-testid=billing]")).to_contain_text("Parses this month", timeout=15000)
    ok(True, "billing panel visible (plan + usage)")
    pg.locator("[data-testid=doc-list] button", has_text="warehouse").click()
    pg.click("[data-testid=publish]")
    expect(pg.locator("[data-testid=attest-panel]")).to_be_visible()
    ok(pg.locator("[data-testid=attest-confirm]").is_disabled(), "publish requires attestation checkbox")
    pg.check("[data-testid=attest-checkbox]")
    pg.locator("[data-testid=attest-panel]").scroll_into_view_if_needed()
    pg.screenshot(path=OUT / "sopcheck_publish_attestation_desktop.png")
    pg.click("[data-testid=attest-confirm]")
    expect(pg.get_by_text(re.compile(r"✓ Live v\d+")).first).to_be_visible(timeout=20000)
    pg.click("[data-testid=tab-audit]"); pg.wait_for_timeout(800)
    ok("attested" in pg.inner_text("body"), "audit log shows attestation")

    # self-serve signup with Terms at signup
    sp = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id="America/Chicago").new_page()
    sp.goto(f"{URL}/signup")
    sp.fill("[data-testid=signup] input >> nth=0", "Acme Mechanical"); sp.fill("[data-testid=signup] input >> nth=1", "Pat Doe")
    sp.fill("[data-testid=signup] input >> nth=2", f"pat{int(__import__('time').time())}@acme-mech.test"); sp.fill("[data-testid=signup] input >> nth=3", "Sup3rSecret!x")
    ok(sp.locator("[data-testid=signup] button").is_disabled(), "signup disabled until Terms accepted")
    sp.check("[data-testid=signup-terms]")
    sp.screenshot(path=OUT / "sopcheck_signup_desktop.png")
    sp.locator("[data-testid=signup] button").click()
    sp.wait_for_url(f"{URL}/manager", timeout=15000)
    expect(sp.locator("[data-testid=billing]")).to_contain_text("Free trial")
    ok(True, "signup -> manager dashboard on Free trial (no Terms gate: accepted at signup)")
    b.close()
print(f"\n{sum(checks)}/{len(checks)} browser checks passed")
