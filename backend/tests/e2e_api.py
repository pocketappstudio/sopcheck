"""End-to-end API test (v2). Requires a FRESH backend (empty DB, demo seed) at BASE.
Covers: auth & roles, cross-company denial, upload (txt / text-PDF / scanned PDF via OCR), safety audit &
resolution, edit + versioning + audit log, publish, filtered distribution, all evidence types incl.
authenticated dual sign-off, timezone-aware timestamps and company shift windows.
Run:  ./venv/bin/python tests/e2e_api.py [http://localhost:8000]"""
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    return cond


def client(token=None):
    return httpx.Client(base_url=BASE, timeout=300, headers={"Authorization": f"Bearer {token}"} if token else {})


def login(email, pw, accept=True):
    """Log in; by default also performs the click-through Terms acceptance (required before any other API use)."""
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": email, "password": pw}, timeout=60)
    if accept and r.status_code == 200 and r.json()["user"]["needs_terms"]:
        v = r.json()["user"]["terms_version_required"]
        httpx.post(f"{BASE}/api/auth/accept-terms", json={"version": v, "accept": True},
                   headers={"Authorization": f"Bearer {r.json()['token']}"}, timeout=60).raise_for_status()
    return r


anon = client()
print("health:", anon.get("/api/health").json())

# ---------------- 1. auth ----------------
print("\n== auth & roles")
ok(anon.get("/api/documents").status_code == 401, "no token -> 401 on /api/documents")
ok(anon.get("/api/tasks").status_code == 401, "no token -> 401 on /api/tasks")
ok(login("manager@demo.co", "wrong").status_code == 401, "wrong password -> 401")
# ---- terms click-through gate ----
r0 = login("manager@demo.co", "Manager!2026", accept=False)
T0 = client(r0.json()["token"])
u0 = r0.json()["user"]
ok(u0["needs_terms"] and u0["terms_version_accepted"] is None, f"fresh user must accept Terms v{u0['terms_version_required']}")
rr = T0.get("/api/documents")
ok(rr.status_code == 403 and rr.json()["detail"]["code"] == "terms_required", "API blocked with 403 terms_required until Terms accepted")
ok(T0.get("/api/auth/me").status_code == 200, "/api/auth/me still allowed before acceptance")
ok(T0.post("/api/auth/accept-terms", json={"version": "old-version", "accept": True}).status_code == 422, "accepting a stale Terms version -> 422")
ok(T0.post("/api/auth/accept-terms", json={"version": u0["terms_version_required"], "accept": False}).status_code == 422, "accept=false -> 422")
ra = T0.post("/api/auth/accept-terms", json={"version": u0["terms_version_required"], "accept": True})
ok(ra.status_code == 200 and not ra.json()["needs_terms"] and ra.json()["terms_accepted_at"], "accept current Terms -> stored version + timestamp")
ok(T0.get("/api/documents").status_code == 200, "API usable after acceptance")
ok(anon.get("/api/legal/terms").status_code == 200 and "as is" in anon.get("/api/legal/terms").text.lower(), "public /api/legal/terms served")
pl = anon.get("/api/plans").json()
ok([(p["key"], p["price_usd_month"], p["max_technicians"], p["max_parses_month"]) for p in pl["plans"]] ==
   [("starter", 49.0, 10, 50), ("team", 99.0, 50, 200)] and pl["trial"]["days"] == 14, "public /api/plans: trial 14d, Starter $49 10/50, Team $99 50/200")
ok("@" not in anon.get("/api/plans").text and "@" not in anon.get("/api/legal/terms").text and "@" not in anon.get("/api/legal/privacy").text,
   "no email address in plans / legal documents")
r = login("manager@demo.co", "Manager!2026")
ok(r.status_code == 200 and "opschk_session" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers.get("set-cookie", ""),
   "manager login -> 200 + HttpOnly session cookie")
M = client(r.json()["token"])
me = M.get("/api/auth/me").json()
ok(me["role"] == "manager" and me["company_name"] == "Demo Company", f"me = {me['full_name']} / {me['role']} / {me['company_name']} / tz {me['company_timezone']}")
cookie_client = httpx.Client(base_url=BASE, cookies={"opschk_session": r.json()["token"]})
ok(cookie_client.get("/api/auth/me").status_code == 200, "cookie-only auth works")
ALEX = client(login("alex@demo.co", "Tech!2026").json()["token"])
JORDAN_T = login("jordan@demo.co", "Tech!2026").json()["token"]
G = client(login("manager@globex.co", "Manager!2026").json()["token"])
GT = client(login("tech@globex.co", "Tech!2026").json()["token"])
ok(ALEX.get("/api/documents").status_code == 403, "technician -> 403 on manager endpoint /api/documents")
with open(SAMPLES / "retail_store_opening.txt", "rb") as f:
    ok(ALEX.post("/api/documents/upload", files={"file": ("x.txt", f)}).status_code == 403, "technician -> 403 on upload")
tmp = login("casey@demo.co", "Tech!2026").json()["token"]
ok(client(tmp).post("/api/auth/logout").status_code == 200 and client(tmp).get("/api/auth/me").status_code == 401,
   "logout revokes the session (token no longer accepted)")
codes = [login("sam@demo.co", "bad").status_code for _ in range(6)]
ok(codes[-1] == 429, f"brute-force throttle: 6th bad attempt -> {codes[-1]}")

# ---------------- 2. uploads incl. OCR ----------------
print("\n== uploads (txt, text-PDF, scanned PDF -> OCR)")
docs = {}
for name in ["construction_loto.txt", "hospital_central_line.pdf", "retail_store_opening.txt", "warehouse_forklift_preop_SCANNED.pdf"]:
    with open(SAMPLES / name, "rb") as f:
        r = M.post("/api/documents/upload", files={"file": (name, f)})
    ok(r.status_code == 200, f"upload {name} -> {r.status_code}")
    b = r.json(); docs[name] = b
    d, s = b["document"], b["safety"]["summary"]
    print(f"     extraction={d['extraction_method']} ocr_conf={d['ocr_confidence']} tasks={len(b['checklist']['tasks'])} "
          f"industry={b['checklist']['industry']} safety={s['counts']} coverage={s['coverage_pct']}% v{b['run']['version_no']} {b['run']['version_status']}")
    assert set(b["checklist"]) == {"industry", "document_summary", "tasks"}
    assert all(set(t) == {"task_id", "task_title", "description", "assigned_position", "site_requirement", "timeframe", "verification_method"}
               for t in b["checklist"]["tasks"])
scan = docs["warehouse_forklift_preop_SCANNED.pdf"]
ok(scan["document"]["extraction_method"] == "ocr" and scan["document"]["ocr_confidence"] >= 85 and len(scan["checklist"]["tasks"]) >= 7,
   f"scanned PDF OCR'd: method=ocr conf={scan['document']['ocr_confidence']}% tasks={len(scan['checklist']['tasks'])}")
ok(any("DO NOT OPERATE" in t["description"] for t in scan["checklist"]["tasks"]) and any("9,000 lbs" in t["description"] for t in scan["checklist"]["tasks"]),
   "OCR text preserved safety literals ('DO NOT OPERATE', '9,000 lbs')")
ok(docs["hospital_central_line.pdf"]["document"]["extraction_method"] == "pdf_text", "text-layer PDF not OCR'd (pdf_text)")
loto = docs["construction_loto.txt"]
ok(loto["safety"]["summary"]["coverage_pct"] == 100.0, f"LOTO safety coverage now {loto['safety']['summary']['coverage_pct']}% (480V purpose hazard carried as SAFETY CONTEXT)")
ok(any("480V" in t["description"] and "SAFETY CONTEXT" in t["description"] for t in loto["checklist"]["tasks"]), "480V note attached to a task")
ok(all(b["safety"]["summary"]["coverage_pct"] == 100.0 for b in docs.values()), "all 4 samples at 100% coverage on fresh parse")

# ---------------- 3. cross-company isolation ----------------
print("\n== cross-company denial")
lid = loto["document"]["id"]
ok(G.get(f"/api/documents/{lid}").status_code == 404, "Globex manager GET Demo document -> 404")
ok(G.get("/api/documents").json() == [], "Globex manager document list is empty")
ok(G.post(f"/api/documents/{lid}/publish?attest=true").status_code == 404, "Globex manager publish Demo document -> 404")
ok(G.put(f"/api/documents/{lid}/checklist", json={"base_run_id": loto["run"]["id"], "tasks": loto["tasks"]}).status_code == 404,
   "Globex manager edit Demo document -> 404")

# ---------------- 4. edit draft, version on published ----------------
print("\n== editing & versioning")
tasks = [dict(t) for t in loto["tasks"]]
bad = [dict(tasks[0], verification_method="Selfie")] + tasks[1:]
r = M.put(f"/api/documents/{lid}/checklist", json={"base_run_id": loto["run"]["id"], "tasks": bad})
ok(r.status_code == 422, "invalid verification_method 'Selfie' rejected -> 422")
tasks[1]["task_title"] = "Don arc-flash PPE"                      # edit field
tasks[2], tasks[3] = tasks[3], tasks[2]                          # reorder
removed = tasks.pop(10)                                          # delete 'Remove your lock and tag'
tasks.insert(0, {"task_title": "Hold pre-job safety briefing", "description": "Brief all affected employees on the LOTO scope.",
                 "assigned_position": "Authorized Electrician", "site_requirement": "Main Electrical Room", "timeframe": "Pre-Task",
                 "verification_method": "Signature"})            # add
r = M.put(f"/api/documents/{lid}/checklist", json={"base_run_id": loto["run"]["id"], "tasks": tasks})
b = r.json()
ok(r.status_code == 200 and b["run"]["version_no"] == 1 and b["run"]["version_status"] == "draft",
   "edit draft v1 in place -> still v1 draft")
codes = [t["task_id"] for t in b["checklist"]["tasks"]]
ok(codes == [f"T-{i:03d}" for i in range(1, len(codes) + 1)] and b["checklist"]["tasks"][0]["task_title"] == "Hold pre-job safety briefing"
   and b["checklist"]["tasks"][2]["task_title"] == "Don arc-flash PPE", f"added/edited/reordered and renumbered ({len(codes)} tasks)")
ok(any(f["coverage"] != "covered" and "lock and tag" in f["span_text"] for f in b["safety"]["flags"]),
   "deleting 'Remove your lock and tag' raised a safety flag")
ok(M.put(f"/api/documents/{lid}/checklist", json={"base_run_id": 999999, "tasks": tasks}).status_code == 409, "stale base_run_id -> 409")
flag = next(f for f in b["safety"]["flags"] if f["coverage"] != "covered")
r = M.post(f"/api/documents/{lid}/publish")
ok(r.status_code == 428 and r.json()["detail"]["code"] == "attestation_required", "publish without reviewer attestation -> 428")
r = M.post(f"/api/documents/{lid}/publish?attest=true")
ok(r.status_code == 409, "publish blocked while safety flags unresolved -> 409")

# ---------------- 5. safety resolution ----------------
print("\n== safety flag resolution")
r = M.post(f"/api/documents/{lid}/safety/{flag['id']}/resolve", json={"action": "dismiss"})
ok(r.status_code == 422, "dismiss without reason -> 422")
r = M.post(f"/api/documents/{lid}/safety/{flag['id']}/resolve", json={"action": "create_task"})
b = r.json()
nf = next(f for f in b["safety"]["flags"] if f["span_hash"] == flag["span_hash"])
ok(r.status_code == 200 and nf["coverage"] == "covered" and nf["resolution"] == "task_created",
   f"create_task resolution -> new task {nf['resolution_task_code']} covers span ({b['safety']['summary']['counts']})")
r = M.post(f"/api/documents/{lid}/publish?attest=true")
ok(r.status_code == 200 and r.json()["run"]["version_status"] == "published", "publish v1 after resolution")
pub = [a for a in M.get(f"/api/documents/{lid}/audit").json() if a["action"] == "publish"]
ok(pub and pub[-1]["data"]["attestation"].startswith("I have reviewed every task") and pub[-1]["data"]["attested_by_user_id"] and pub[-1]["actor"],
   "publish audit entry records attestation text + user + time")
for fmt, mark in (("csv", "DISCLAIMER"), ("md", "NOT a substitute"), ("json", '"disclaimer"')):
    ex = M.get(f"/api/documents/{lid}/export?format={fmt}")
    ok(ex.status_code == 200 and mark in ex.text and "attachment" in ex.headers.get("content-disposition", ""), f"export .{fmt} carries the disclaimer")
v1_run = r.json()["run"]["id"]

# Edit the PUBLISHED doc -> new draft v2; technicians still see v1
pub = M.get(f"/api/documents/{lid}").json()
t2 = [dict(t) for t in pub["tasks"]]
warn_idx = next(i for i, t in enumerate(t2) if "WARNING: Never work on energized" in t["description"])
t2[warn_idx]["description"] = t2[warn_idx]["description"].split(" WARNING:")[0]      # simulate dropping the warning
r = M.put(f"/api/documents/{lid}/checklist", json={"base_run_id": pub["run"]["id"], "tasks": t2})
b = r.json()
ok(r.status_code == 200 and b["run"]["version_no"] == 2 and b["run"]["version_status"] == "draft" and b["document"]["published_run_id"] == v1_run,
   "editing published v1 created draft v2; v1 stays live")
alex_tasks = ALEX.get("/api/tasks", params={"position": "Authorized Electrician"}).json()
ok(any("WARNING: Never work on energized" in t["description"] for t in alex_tasks) and all(t["parse_run_id"] == v1_run for t in alex_tasks if t["document_id"] == lid),
   "technician still sees published v1 (with the warning)")
wflag = next(f for f in b["safety"]["flags"] if "50 volts" in f["span_text"])
ok(wflag["coverage"] == "missing" and "50 volts (>)" in wflag["missing_atoms"], f"dropped 50 V warning flagged: {wflag['coverage']} missing={wflag['missing_atoms']}")
r = M.post(f"/api/documents/{lid}/safety/{wflag['id']}/resolve", json={"action": "attach", "task_code": wflag["best_task_code"]})
b = r.json()
af = next(f for f in b["safety"]["flags"] if f["span_hash"] == wflag["span_hash"])
ok(r.status_code == 200 and af["coverage"] == "covered" and af["resolution"] == "attached" and b["run"]["version_no"] == 2,
   f"attach resolution -> SAFETY NOTE appended to {af['resolution_task_code']}, covered, still v2 draft")
# dismiss path: drop the Pre-Task hold-point sentence and dismiss with a reason
t3 = [dict(t) for t in b["tasks"]]
i = next(i for i, t in enumerate(t3) if "DO NOT proceed until both signatures" in t["description"])
t3[i]["description"] = t3[i]["description"].replace("DO NOT proceed until both signatures are recorded.", "").strip()
b = M.put(f"/api/documents/{lid}/checklist", json={"base_run_id": b["run"]["id"], "tasks": t3}).json()
hf = next(f for f in b["safety"]["flags"] if "DO NOT proceed" in f["span_text"])
r = M.post(f"/api/documents/{lid}/safety/{hf['id']}/resolve", json={"action": "dismiss", "reason": "Covered by the Dual Sign-off verification itself"})
b = r.json()
df = next(f for f in b["safety"]["flags"] if f["span_hash"] == hf["span_hash"])
ok(r.status_code == 200 and df["resolution"] == "dismissed" and df["resolution_reason"].startswith("Covered by"), "dismiss with reason recorded")
r = M.post(f"/api/documents/{lid}/publish?attest=true")
ok(r.status_code == 200 and r.json()["run"]["version_no"] == 2 and [v["version_status"] for v in r.json()["versions"]] == ["superseded", "published"],
   "publish v2 -> v1 superseded, v2 published")
alex_tasks = ALEX.get("/api/tasks", params={"position": "Authorized Electrician"}).json()
ok(all(t["parse_run_id"] == r.json()["run"]["id"] for t in alex_tasks if t["document_id"] == lid), "technicians now see v2")
log = M.get(f"/api/documents/{lid}/audit").json()
acts = {e["action"] for e in log}
need = {"upload_parse", "task_update", "task_add", "task_delete", "task_reorder", "version_create", "publish", "attach", "create_task", "dismiss"}
ok(need <= acts, f"audit log has {sorted(need)}; e.g. {next(e for e in log if e['action']=='task_update')['data']['changes']}")
ok(all(e["actor"] == "Morgan Blake" for e in log), "audit entries attributed to the manager")

# publish the other three for distribution tests
for n in ["hospital_central_line.pdf", "retail_store_opening.txt", "warehouse_forklift_preop_SCANNED.pdf"]:
    M.post(f"/api/documents/{docs[n]['document']['id']}/publish?attest=true")

# ---------------- 6. distribution + submissions ----------------
print("\n== distribution, evidence, dual sign-off")
f = ALEX.get("/api/filters").json()
print("     positions:", f["positions"])
pre = ALEX.get("/api/tasks", params={"position": "Authorized Electrician", "site": "Main Electrical Room", "timeframe": "Pre-Task"}).json()
ok(len(pre) > 0 and all(t["timeframe"] == "Pre-Task" for t in pre), f"filter electrician/main room/pre-task -> {len(pre)} tasks")
ok(GT.get("/api/tasks").json() == [], "Globex technician sees no Demo tasks")
all_t = M.get("/api/tasks").json()
by_vm = {}
for t in all_t:
    by_vm.setdefault(t["verification_method"], t)
ok(GT.post("/api/submissions", json={"task_id": by_vm["Checkbox"]["id"]}).status_code == 404, "Globex technician submit to Demo task -> 404")
ok(M.post("/api/submissions", json={"task_id": by_vm["Checkbox"]["id"]}).status_code == 403, "manager without technician record cannot submit -> 403")
photo = ALEX.post("/api/uploads/photo", files={"file": ("p.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}).json()["url"]
ok(ALEX.get(photo).status_code == 200 and GT.get(photo).status_code == 404 and anon.get(photo).status_code == 401,
   "photo served to own company only (Globex 404, anonymous 401)")
payloads = {"Checkbox": {}, "Numeric Input": {"numeric_value": 0.4}, "Text Input": {"text_value": "LOT-88213"},
            "Photo Upload": {"photo_url": photo}, "Signature": {"signature": "Alex Rivera"},
            "Dual Sign-off": {"signature": "Alex Rivera", "cosigner_email": "jordan@demo.co", "cosigner_password": "Tech!2026"}}
for vm, extra in payloads.items():
    t = by_vm[vm]
    if vm != "Checkbox":
        bad = ALEX.post("/api/submissions", json={"task_id": t["id"]})
        ok(bad.status_code == 422, f"{vm}: missing evidence -> 422 {bad.json()['detail']['errors']}")
    r = ALEX.post("/api/submissions", json={"task_id": t["id"], **extra})
    ok(r.status_code == 201, f"{vm}: with evidence -> 201" + (f" (cosigner={r.json().get('cosigner_name')})" if vm == "Dual Sign-off" else ""))
    if vm == "Checkbox":
        sub = r.json()
dual = by_vm["Dual Sign-off"]["id"]
ok(ALEX.post("/api/submissions", json={"task_id": dual, "signature": "A", "cosigner_email": "jordan@demo.co", "cosigner_password": "nope"}).status_code == 422,
   "dual sign-off with wrong co-signer password -> 422")
ok(ALEX.post("/api/submissions", json={"task_id": dual, "signature": "A", "cosigner_email": "alex@demo.co", "cosigner_password": "Tech!2026"}).status_code == 422,
   "dual sign-off with self as co-signer -> 422")
ok(ALEX.post("/api/submissions", json={"task_id": dual, "signature": "A", "cosigner_email": "tech@globex.co", "cosigner_password": "Tech!2026"}).status_code == 422,
   "dual sign-off with other-company co-signer -> 422")
done = [t for t in ALEX.get("/api/tasks").json() if t["completed"]]
ok(len(done) == 6, f"Alex's completed tasks today: {len(done)}")
jordan_done = [t for t in client(JORDAN_T).get("/api/tasks").json() if t["completed"]]
ok(len(jordan_done) == 0, "completion state is per technician (Jordan: 0)")

# ---------------- 7. time handling ----------------
print("\n== timestamps & company timezone")
ts = sub["submitted_at"]
dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
ok(dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0 and abs((datetime.now(timezone.utc) - dt).total_seconds()) < 600,
   f"submitted_at is timezone-aware UTC: {ts}")
win = next(t for t in ALEX.get("/api/tasks").json() if t["timeframe"] == "Before 7:00 AM" or t["timeframe"] == "Shift Start")
print("     window:", win["timeframe"], win["window"])
ok(win["window"] and win["window"]["timezone"] == "America/Chicago", "shift window computed in company timezone (America/Chicago)")
ok(M.patch("/api/company", json={"timezone": "Mars/Olympus"}).status_code == 422, "invalid timezone rejected")
ok(ALEX.patch("/api/company", json={"timezone": "UTC"}).status_code == 403, "technician cannot change company settings")
r = M.patch("/api/company", json={"timezone": "America/Denver", "shift_windows": {"Shift Start": ["06:00", "08:00"], "Opening": ["05:30", "07:00"], "Shift End": ["17:00", "19:00"]}})
ok(r.status_code == 200 and r.json()["timezone"] == "America/Denver", "manager updated company timezone -> America/Denver")
w2 = next(t for t in ALEX.get("/api/tasks").json() if t["timeframe"] == "Shift Start")["window"]
ok(w2["timezone"] == "America/Denver" and w2["local"] == "06:00–08:00", f"Shift Start window now {w2['local']} {w2['timezone']} -> UTC {w2['start']}")
M.patch("/api/company", json={"timezone": "America/Chicago"})
ok(G.get("/api/company").json()["timezone"] == "America/New_York", "Globex timezone untouched (America/New_York)")

# ---------------- 8. billing (trial) ----------------
print("\n== billing")
b = M.get("/api/billing").json()
ok(b["plan"] == "trial" and b["trial_days_left"] in (13, 14) and b["limits"] == {"max_technicians": 10, "max_parses_month": 50},
   f"new company on 14-day trial: {b['trial_days_left']} days left, limits {b['limits']}")
ok(b["usage"]["parses_this_month"] >= 4 and b["usage"]["technicians"] == 5, f"usage metered: {b['usage']['parses_this_month']} parses, {b['usage']['technicians']} seats")
ok(ALEX.get("/api/billing").status_code == 403, "technician cannot see billing")
nt = M.post("/api/technicians", json={"email": "newtech@demo.co", "full_name": "New Tech", "password": "Tech!2026x", "position": "HVAC Technician", "site": "Roof"})
ok(nt.status_code == 201 and M.get("/api/billing").json()["usage"]["technicians"] == 6, "manager adds a technician seat (6/10)")
lk = M.post("/api/billing/license", json={"license_key": "ABCD1234-EFGH5678"})
ok(lk.status_code == 503 and lk.json()["detail"]["code"] == "billing_not_configured", "license verify without Gumroad product ids -> 503 not configured")

# ---------------- 9. self-serve signup (trial + terms at signup) ----------------
print("\n== signup")
tv = login("manager@demo.co", "Manager!2026").json()["user"]["terms_version_required"]
bad = anon.post("/api/auth/signup", json={"company_name": "Acme HVAC", "full_name": "Pat Doe", "email": "pat@acme.test",
                                          "password": "Sup3rSecret!", "accept_terms": False, "terms_version": tv})
ok(bad.status_code == 422, "signup without accepting Terms -> 422")
su = anon.post("/api/auth/signup", json={"company_name": "Acme HVAC", "full_name": "Pat Doe", "email": "pat@acme.test",
                                         "password": "Sup3rSecret!", "accept_terms": True, "terms_version": tv})
ok(su.status_code == 201 and su.json()["user"]["role"] == "manager" and not su.json()["user"]["needs_terms"], "signup creates company + manager with Terms accepted")
A = client(su.json()["token"])
ab = A.get("/api/billing").json()
ok(ab["plan"] == "trial" and ab["usage"]["technicians"] == 0 and A.get("/api/documents").json() == [], "new company: own trial, no data from other tenants")
ok(anon.post("/api/auth/signup", json={"company_name": "Acme 2", "full_name": "X", "email": "pat@acme.test", "password": "Sup3rSecret!",
                                       "accept_terms": True, "terms_version": tv}).status_code == 409, "duplicate login -> 409")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
