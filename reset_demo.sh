#!/usr/bin/env bash
# Reset the prototype to a clean demo state: wipe the SQLite DB, restart the API (re-seeding demo
# companies/users), then upload + publish the sample SOPs as manager@demo.co.
# Usage: ./reset_demo.sh            (expects the backend venv in backend/venv)
# Live LLM mode: export OPENAI_API_KEY (and optionally LLM_MODEL) before running; it is inherited, never logged.
set -euo pipefail
cd "$(dirname "$0")/backend"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
rm -rf data
nohup ./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 &
for i in $(seq 1 90); do curl -sf localhost:8000/api/health >/dev/null && break; sleep 1; done
./venv/bin/python - <<'PY'
import httpx
B = "http://localhost:8000"
lr = httpx.post(f"{B}/api/auth/login", json={"email": "manager@demo.co", "password": "Manager!2026"}).json()
c = httpx.Client(base_url=B, headers={"Authorization": f"Bearer {lr['token']}"}, timeout=300)
# Demo manager accepts the current Terms (click-through) so the script can run; other demo users accept on first login.
c.post("/api/auth/accept-terms", json={"version": lr["user"]["terms_version_required"], "accept": True}).raise_for_status()
for name, publish in [("construction_loto.txt", True), ("hospital_central_line.pdf", True),
                      ("retail_store_opening.txt", True), ("warehouse_forklift_preop_SCANNED.pdf", False),
                      ("hvac_rooftop_unit_pm.txt", True)]:
    d = c.post("/api/documents/upload", files={"file": (name, open(f"../samples/{name}", "rb"))}).json()
    state = " (draft)"
    if publish:
        r = c.post(f"/api/documents/{d['document']['id']}/publish", params={"attest": "true"})   # demo: scripted attestation
        state = " -> published" if r.status_code == 200 else f" -> NOT published, review safety flags first ({str(r.json().get('detail'))[:120]})"
    print(f"{name}: {len(d['checklist']['tasks'])} tasks, {d['run']['parser']}, {d['document']['extraction_method']}, "
          f"safety {d['safety']['summary']['coverage_pct']}%{state}")
PY
