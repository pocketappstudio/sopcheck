"""Upload the generic samples to a running backend and report parse metrics (tasks, safety, latency,
tokens, estimated $). Never reads or prints API keys.  Usage: python measure_parse.py BASE_URL OUT.json"""
import json, sys, time
from pathlib import Path
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else None
S = Path(__file__).resolve().parents[2] / "samples"
DOCS = ["construction_loto.txt", "hospital_central_line.pdf", "retail_store_opening.txt",
        "warehouse_forklift_preop_SCANNED.pdf", "hvac_rooftop_unit_pm.txt"]
sys.path.insert(0, str(S.parent / "backend"))
from app.schemas import Checklist  # noqa: E402

tok = httpx.post(f"{B}/api/auth/login", json={"email": "manager@demo.co", "password": "Manager!2026"}).json()["token"]
c = httpx.Client(base_url=B, headers={"Authorization": f"Bearer {tok}"}, timeout=600)
rows, full = [], {}
for name in DOCS:
    t0 = time.time()
    r = c.post("/api/documents/upload", files={"file": (name, open(S / name, "rb"))})
    wall = round(time.time() - t0, 1)
    d = r.json()
    if r.status_code != 200:
        rows.append({"doc": name, "error": r.status_code, "detail": str(d)[:300]}); continue
    run, sm = d["run"], d["safety"]["summary"] or {}
    try:
        Checklist.model_validate(d["checklist"]); valid = True
    except Exception as e:  # noqa: BLE001
        valid = f"INVALID: {str(e)[:120]}"
    u = run.get("usage") or {}
    rows.append({"doc": name, "doc_id": d["document"]["id"], "parser": run["parser"], "model": run["model"],
                 "status": run["status"], "schema_valid": valid, "repairs": run["repair_attempts"],
                 "chunks": run["chunk_count"], "tasks": len(d["checklist"]["tasks"]),
                 "safety_critical": sum(t["is_safety_critical"] for t in d["tasks"]),
                 "coverage_pct": sm.get("coverage_pct"), "flags": len(d["safety"]["flags"]),
                 "latency_ms": run["latency_ms"], "wall_s": wall,
                 "tokens": u.get("total_tokens"), "est_cost_usd": u.get("est_cost_usd"), "notes": run["notes"]})
    full[name] = {"checklist": d["checklist"], "flags": d["safety"]["flags"], "summary": sm, "usage": u}
for row in rows:
    print(json.dumps(row))
if OUT:
    Path(OUT).write_text(json.dumps({"rows": rows, "full": full}, indent=1, default=str))
