"""Account-area test: JSON export, technician self-delete, last-manager guard, workspace deletion
(name confirm, 423 lock, cancel, grace expiry -> hard delete incl. photos, tombstone), tenant isolation.
Run against a dedicated server started with a tiny DELETION_GRACE_DAYS and PURGE_INTERVAL_S (see run line in README)."""
import json, os, sqlite3, sys, time, uuid
from pathlib import Path
import httpx

BASE = os.getenv("BASE", "http://127.0.0.1:8097")
UPLOADS = Path(os.environ["UPLOAD_DIR"]); DB = os.getenv("DB_FILE")
ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    ok += bool(cond); fail += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

def C(tok=None):
    return httpx.Client(base_url=BASE, timeout=60, headers={"Authorization": f"Bearer {tok}"} if tok else {})

terms = httpx.get(f"{BASE}/api/legal/terms").headers["X-Terms-Version"]
def signup(company):
    login = f"mgr-{uuid.uuid4().hex[:6]}@test.example"
    r = httpx.post(f"{BASE}/api/auth/signup", json={"company_name": company, "full_name": "Pat Manager", "email": login,
                   "password": "manager-pass-123", "accept_terms": True, "terms_version": terms}, timeout=60)
    r.raise_for_status(); return r.json()["token"], login
def login(l, pw):
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": l, "password": pw}, timeout=60)
    if r.status_code == 200 and r.json()["user"]["needs_terms"]:
        C(r.json()["token"]).post("/api/auth/accept-terms", json={"version": terms, "accept": True}).raise_for_status()
    return r

name_a, name_b = f"Acme Pumps {uuid.uuid4().hex[:4]}", f"Bravo Labs {uuid.uuid4().hex[:4]}"
ta, _ = signup(name_a); tb, _ = signup(name_b)
A, B = C(ta), C(tb)
cid_a = A.get("/api/auth/me").json()["company_id"]

# workspace content: a parsed doc, a technician, a photo
r = A.post("/api/documents/parse-text", json={"title": "Pump check", "text": "1. Inspect pump seals daily for leaks.\n2. Record discharge pressure in psi each shift."})
check("parse doc", r.status_code in (200, 201), r.text[:200])
tech_login = f"tech-{uuid.uuid4().hex[:6]}@test.example"
r = A.post("/api/technicians", json={"email": tech_login, "full_name": "Terry Tech", "password": "tech-pass-123", "position": "Operator", "site": "Plant 1"})
check("create technician", r.status_code == 201, r.text[:200])
T = C(login(tech_login, "tech-pass-123").json()["token"])
r = T.post("/api/uploads/photo", files={"file": ("p.jpg", b"\xff\xd8fakejpeg", "image/jpeg")})
photo = r.json()["url"].split("/")[-1]
check("photo stored", (UPLOADS / photo).exists())
B.post("/api/uploads/photo", files={"file": ("b.jpg", b"\xff\xd8other", "image/jpeg")})

# account info + export
acc = T.get("/api/account").json()
check("technician can self-delete", acc["can_self_delete"] is True)
check("sole manager cannot self-delete", A.get("/api/account").json()["can_self_delete"] is False)
ex = T.get("/api/account/export")
check("tech export is JSON attachment", ex.status_code == 200 and "attachment" in ex.headers.get("content-disposition", ""))
ej = ex.json()
check("tech export has own profile, no workspace", ej["you"]["profile"]["full_name"] == "Terry Tech" and "workspace" not in ej)
check("tech export has no password hash", "password_hash" not in ex.text)
mx = A.get("/api/account/export"); mj = mx.json()
check("manager export has workspace", {"users", "documents", "tasks", "audit_log", "files"} <= set(mj["workspace"]))
check("manager export lists photo", photo in mj["workspace"]["files"])
check("manager export has no secrets", "password_hash" not in mx.text and '"license_key":' not in mx.text)
check("manager export excludes other tenant", name_b not in mx.text)

# last manager guard + technician self delete
check("last manager self-delete blocked (409)", A.post("/api/account/delete", json={"confirm": "DELETE"}).status_code == 409)
check("self-delete needs confirm", T.post("/api/account/delete", json={"confirm": "yes"}).status_code == 422)
r = T.post("/api/account/delete", json={"confirm": "DELETE"})
check("technician self-delete ok", r.status_code == 200, r.text[:200])
check("deleted session revoked", T.get("/api/auth/me").status_code == 401)
check("deleted login no longer works", login(tech_login, "tech-pass-123").status_code in (401, 403))
users = A.get("/api/account/export").json()["workspace"]["users"]
du = [u for u in users if u["role"] == "technician"][0]
check("user anonymized", du["full_name"].startswith("Deleted user") and "Terry" not in json.dumps(users) and du["deleted_at"])

# workspace deletion
check("wrong name rejected", A.post("/api/company/delete", json={"confirm_name": "nope"}).status_code == 422)
r = A.post("/api/company/delete", json={"confirm_name": name_a})
check("deletion scheduled", r.status_code == 200 and r.json()["scheduled_for"], r.text[:200])
check("workspace locked (423)", A.get("/api/documents").status_code == 423)
check("export still allowed during grace", A.get("/api/account/export").status_code == 200)
check("me shows pending deletion", A.get("/api/auth/me").json()["company_deletion_scheduled_for"])
check("cancel works", A.post("/api/company/delete/cancel").status_code == 200)
check("unlocked after cancel", A.get("/api/documents").status_code == 200)
check("other tenant unaffected", B.get("/api/documents").status_code == 200)
r = A.post("/api/company/delete", json={"confirm_name": name_a}); check("re-scheduled", r.status_code == 200)

deadline = time.time() + 90
while time.time() < deadline and A.get("/api/auth/me").status_code == 200:
    time.sleep(2)
check("purged: session gone", A.get("/api/auth/me").status_code == 401)
check("purged: photos deleted", not list(UPLOADS.glob(f"c{cid_a}_*")))
check("other tenant photo kept", any(UPLOADS.glob("c*_*")))
check("other tenant still works", B.get("/api/documents").status_code == 200)
if DB:
    con = sqlite3.connect(DB)
    left = {t: con.execute(f"select count(*) from {t} where company_id=?", (cid_a,)).fetchone()[0]
            for t in ("users", "technicians", "documents", "parse_runs", "tasks", "submissions", "safety_flags", "audit_log", "auth_sessions")}
    check("purged: no rows left", not any(left.values()), str(left))
    check("purged: company row gone", con.execute("select count(*) from companies where id=?", (cid_a,)).fetchone()[0] == 0)
    tomb = con.execute("select * from deletion_tombstones where company_id=?", (cid_a,)).fetchall()
    check("tombstone written", len(tomb) == 1)
    check("tombstone has no names", name_a not in str(tomb) and "Pat" not in str(tomb), str(tomb))
print(f"\n{ok}/{ok+fail} checks passed"); sys.exit(1 if fail else 0)
