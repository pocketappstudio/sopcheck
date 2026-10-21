#!/usr/bin/env bash
# Build the static GitHub Pages preview with the in-browser demo backend (Pyodide + offline mock parser).
# Output: frontend/out/ (deploy as-is; includes .nojekyll). Requires a seed DB built from generic samples (see DEPLOY.md).
set -euo pipefail
cd "$(dirname "$0")"
SEED_DB="${SEED_DB:-demo/seed.db}"
BASE="${NEXT_PUBLIC_BASE_PATH-/sopcheck}"
python3 - "$SEED_DB" <<'PY'
import base64, json, pathlib, sys
root = pathlib.Path("..").resolve(); out = {}
def add(dest, p): out[dest] = base64.b64encode(p.read_bytes()).decode()
for p in (root / "backend" / "app").glob("*.py"): add(f"/sop/backend/app/{p.name}", p)
for p in (root / "prompts").rglob("*"):
    if p.is_file(): add(f"/sop/prompts/{p.relative_to(root / 'prompts')}", p)
for n in ("terms.md", "privacy.md"):   # only the published legal docs; never drafts or internal notes
    add(f"/sop/legal/{n}", root / "legal" / n)
add("/sop/demo_boot.py", pathlib.Path("demo/demo_boot.py"))
add("/seed/app.db", pathlib.Path(sys.argv[1]))
pathlib.Path("public/demo").mkdir(parents=True, exist_ok=True)
pathlib.Path("public/demo/bundle.json").write_text(json.dumps(out))
print("bundle files:", len(out))
PY
STATIC_EXPORT=1 NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH="$BASE" NEXT_PUBLIC_SHOW_DEMO_ACCOUNTS=true NEXT_PUBLIC_API_BASE= npx next build
touch out/.nojekyll
rm -rf public/demo
echo "static demo built in frontend/out (base path $BASE)"
