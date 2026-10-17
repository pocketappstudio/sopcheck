#!/usr/bin/env bash
# One-shot: deploy the SOPCheck AI proxy Worker to Cloudflare, wire the demo to it, publish gh-pages and verify live.
# Required env (never printed, never written to files): CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, OPENAI_API_KEY, GITHUB_TOKEN_ROWBIN
# Optional: WORKER_NAME (default sopcheck-ai), PAGES_REPO (default pocketappstudio/sopcheck), PAGES_DIR (default /workspace/sopcheck-pages),
#           PUBLISH_DIR (default /workspace/sopcheck-publish), SKIP_PAGES=1 (deploy Worker only)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
: "${CLOUDFLARE_API_TOKEN:?missing}" "${CLOUDFLARE_ACCOUNT_ID:?missing}" "${OPENAI_API_KEY:?missing}"
WORKER_NAME="${WORKER_NAME:-sopcheck-ai}"; PAGES_REPO="${PAGES_REPO:-pocketappstudio/sopcheck}"
PAGES_DIR="${PAGES_DIR:-/workspace/sopcheck-pages}"; PUBLISH_DIR="${PUBLISH_DIR:-/workspace/sopcheck-publish}"
API="https://api.cloudflare.com/client/v4"; AUTH=(-H "Authorization: Bearer $CLOUDFLARE_API_TOKEN")
cf() { curl -sS "${AUTH[@]}" -H "Content-Type: application/json" "$@"; }
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "1/8 verifying Cloudflare token"
v=$(cf "$API/user/tokens/verify" | jget "d.get('success') and d['result']['status']" 2>/dev/null || true)
[ "$v" = "active" ] || v=$(cf "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/tokens/verify" | jget "d.get('success') and d['result']['status']" 2>/dev/null || true)
[ "$v" = "active" ] || { echo "Cloudflare token is not active/valid (check CLOUDFLARE_API_TOKEN)"; exit 1; }

echo "2/8 workers.dev subdomain"
sub=$(cf "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" | jget "(d.get('result') or {}).get('subdomain') or ''")
if [ -z "$sub" ]; then
  sub="sopcheck-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cf -X PUT "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" --data "{\"subdomain\":\"$sub\"}" | jget "d.get('success') or d" | grep -q True || { echo "could not register workers.dev subdomain"; exit 1; }
fi
echo "   subdomain: $sub"

echo "3/8 KV namespace"
kv=$(cf "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces?per_page=100" | jget "next((n['id'] for n in d.get('result',[]) if n['title']=='sopcheck-rl'), '')")
[ -n "$kv" ] || kv=$(cf -X POST "$API/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces" --data '{"title":"sopcheck-rl"}' | jget "d['result']['id']")
echo "   kv id: $kv"

echo "4/8 deploying Worker"
cd "$ROOT/worker"
[ -d node_modules/.bin ] || npm install --no-audit --no-fund >/dev/null
python3 scripts/gen_prompts.py >/dev/null
sed -e "s/__KV_ID__/$kv/" -e "s/^name = .*/name = \"$WORKER_NAME\"/" wrangler.toml > wrangler.deploy.toml
node test/harness.mjs | tail -1
npx wrangler deploy -c wrangler.deploy.toml >/tmp/wrangler-deploy.log 2>&1 || { tail -20 /tmp/wrangler-deploy.log; exit 1; }
echo "5/8 setting OPENAI_API_KEY secret (from env, via stdin)"
printf %s "$OPENAI_API_KEY" | npx wrangler secret put OPENAI_API_KEY -c wrangler.deploy.toml >/dev/null 2>&1 || { echo "secret put failed"; exit 1; }
URL="https://$WORKER_NAME.$sub.workers.dev"; echo "   worker: $URL"
echo "$URL" > "$ROOT/worker/WORKER_URL"      # public, non-secret

echo "6/8 smoke test (one real gpt-4.1 call)"
for i in $(seq 1 30); do curl -s -o /dev/null -w "%{http_code}" "$URL/health" -H "Origin: https://pocketappstudio.github.io" | grep -q 200 && break; sleep 10; done   # new workers.dev subdomains take ~1 min for DNS/TLS
node scripts/smoke.mjs "$URL"
[ "${SKIP_PAGES:-0}" = 1 ] && { echo "SKIP_PAGES=1: done (Worker only)"; exit 0; }

echo "7/8 rebuilding static demo with the Worker URL and publishing gh-pages"
: "${GITHUB_TOKEN_ROWBIN:?missing}"
cd "$ROOT/frontend"
NEXT_PUBLIC_AI_PROXY_URL="$URL" ./scripts_build_demo.sh >/tmp/demo-build.log 2>&1 || { tail -20 /tmp/demo-build.log; exit 1; }
H='!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN_ROWBIN"; }; f'
GP() { git -c credential.helper= -c "credential.helper=$H" "$@"; }
[ -d "$PAGES_DIR/.git" ] || { echo "missing $PAGES_DIR (gh-pages working copy)"; exit 1; }
cd "$PAGES_DIR"; git ls-files -z | xargs -0 -r git rm -q --cached; find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$ROOT/frontend/out/." .; touch .nojekyll
git add -A; git -c user.name=SOPCheck -c user.email=noreply@users.noreply.github.com commit -q -m "Demo: AI parsing via proxy Worker" || echo "(no page changes)"
GP push -q "https://github.com/$PAGES_REPO.git" HEAD:gh-pages
# also publish the Worker source + demo wiring to main (no secrets, no node_modules)
if [ -d "$PUBLISH_DIR/.git" ]; then
  cd "$PUBLISH_DIR"; mkdir -p worker
  (cd "$ROOT" && tar cf - --exclude=node_modules --exclude=.wrangler --exclude=wrangler.deploy.toml worker deploy-worker.sh frontend/lib/demo.ts frontend/demo/demo_boot.py frontend/scripts_demo_ai_check.py) | tar xf -
  git add -A; git -c user.name=SOPCheck -c user.email=noreply@users.noreply.github.com commit -q -m "Add AI proxy Worker and deploy script" || true
  GP push -q "https://github.com/$PAGES_REPO.git" HEAD:main || echo "(main push skipped)"
fi

echo "8/8 verifying live site"
S=https://pocketappstudio.github.io/sopcheck
for i in $(seq 1 40); do   # wait for the Pages build to finish and the new bundle (containing the Worker URL) to be served
  st=$(curl -s -H "Authorization: Bearer $GITHUB_TOKEN_ROWBIN" "https://api.github.com/repos/$PAGES_REPO/pages/builds/latest" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))" 2>/dev/null || echo "?")
  hit=$({ curl -s "$S/login/" | grep -o '/_next/static/[^"]*\.js' | sort -u | head -40 | while read -r f; do curl -s "https://pocketappstudio.github.io/sopcheck$f" | grep -c "$URL" | grep -qv '^0$' && echo hit && break; done; } || true)
  [ "$st" = built ] && [ "$hit" = hit ] && break; sleep 10
done
[ "${hit:-}" = hit ] || echo "warning: new bundle not detected yet; continuing to verification"
PY="${PY:-/workspace/pwvenv/bin/python}"
"$PY" "$ROOT/frontend/scripts_demo_check.py" "$S"
"$PY" "$ROOT/frontend/scripts_demo_ai_check.py" "$S" llm
echo "DONE: $S uses AI parsing via $URL"
