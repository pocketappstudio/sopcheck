"""Billing enforcement test against a FRESH backend started with small limits and a fake Gumroad:
  TRIAL_MAX_PARSES=2 TRIAL_MAX_TECHNICIANS=6 PLAN_STARTER_PRODUCT_ID=prodS PLAN_TEAM_PRODUCT_ID=prodT
  PLAN_STARTER_MAX_PARSES=3 GUMROAD_VERIFY_URL=http://127.0.0.1:8098/v2/licenses/verify
and tests/fake_gumroad.py running on :8098.   Run: ./venv/bin/python tests/billing_test.py http://localhost:8005"""
import sys
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8005"
res = []


def ok(c, m):
    res.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)


def session(email, pw):
    r = httpx.post(f"{B}/api/auth/login", json={"email": email, "password": pw}).json()
    c = httpx.Client(base_url=B, headers={"Authorization": f"Bearer {r['token']}"}, timeout=120)
    c.post("/api/auth/accept-terms", json={"version": r["user"]["terms_version_required"], "accept": True}).raise_for_status()
    return c


M = session("manager@demo.co", "Manager!2026")
TXT = "Store opening\n1. Unlock the front door.\n2. Count the cash drawer and record the total.\n3. Turn on the lights.\n"
for i in range(2):
    ok(M.post("/api/documents/parse-text", json={"title": f"Doc {i}", "text": TXT}).status_code == 200, f"trial parse {i + 1}/2 ok")
r = M.post("/api/documents/parse-text", json={"title": "Doc 3", "text": TXT})
d = r.json()["detail"]
ok(r.status_code == 402 and d["code"] == "parse_limit" and "Upgrade" in d["message"] and "@" not in d["message"],
   f"3rd parse on trial -> 402 parse_limit: {d['message'][:110]}...")
t = M.post("/api/technicians", json={"email": "t6@demo.co", "full_name": "Tech Six", "password": "Tech!2026x"})
ok(t.status_code == 201, "6th seat ok (limit 6)")
t = M.post("/api/technicians", json={"email": "t7@demo.co", "full_name": "Tech Seven", "password": "Tech!2026x"})
ok(t.status_code == 402 and t.json()["detail"]["code"] == "seat_limit", f"7th seat -> 402 seat_limit: {t.json()['detail']['message'][:90]}...")

for key, code, why in [("BAD-KEY-0000", 402, "unknown key"), ("REFUNDED-KEY-1", 402, "refunded"), ("CANCELLED-KEY-1", 402, "cancelled membership")]:
    r = M.post("/api/billing/license", json={"license_key": key})
    ok(r.status_code == code and r.json()["detail"]["code"] == "license_invalid", f"license '{why}' rejected: {r.json()['detail']['message'][:70]}")
r = M.post("/api/billing/license", json={"license_key": "STARTER-KEY-1234"})
ok(r.status_code == 200 and r.json()["plan"] == "starter" and r.json()["license"]["last4"] == "1234" and "STARTER-KEY" not in r.text,
   "valid Starter key -> plan starter (only last 4 of key returned)")
ok(M.post("/api/documents/parse-text", json={"title": "Doc 3", "text": TXT}).status_code == 200, "after upgrade parse 3/3 ok (Starter limit 3 in test)")
r = M.post("/api/documents/parse-text", json={"title": "Doc 4", "text": TXT})
ok(r.status_code == 402 and "Team plan" in r.json()["detail"]["message"], "Starter cap -> 402 suggesting Team")
r = M.post("/api/billing/license", json={"license_key": "TEAM-KEY-9999"})
ok(r.status_code == 200 and r.json()["plan"] == "team", "Team key -> plan team")
ok(M.post("/api/documents/parse-text", json={"title": "Doc 4", "text": TXT}).status_code == 200, "Team plan parse ok")
print(f"\n{sum(res)}/{len(res)} checks passed"); sys.exit(0 if all(res) else 1)
