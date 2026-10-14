# Deploying SOPCheck

| Piece | Where | Config in repo |
|---|---|---|
| Frontend (static) | GitHub Pages | `.github/workflows/pages.yml`, `frontend/next.config.mjs` (`STATIC_EXPORT=1`) |
| API + OCR | Render **or** Fly.io (Docker) | `Dockerfile`, `render.yaml`, `fly.toml` |
| Database | Render Postgres / Fly Postgres (or SQLite on the `/data` volume for a small single instance) | `DATABASE_URL` |
| Billing | Gumroad memberships + License API | `PLAN_*` env vars |
| AI | OpenAI API | `OPENAI_API_KEY` (server secret only) |

Placeholders that live in exactly one place:

- **Operator name:** the `OWNER_LEGAL_NAME` env var. The default is `SOPCheck`. It is substituted into `legal/terms.md` and `legal/privacy.md` when they are served. No personal name, address or county appears anywhere.
- **Gumroad:** `PLAN_STARTER_PRODUCT_ID`, `PLAN_TEAM_PRODUCT_ID`, `PLAN_STARTER_CHECKOUT_URL` and `PLAN_TEAM_CHECKOUT_URL`. Until these are set, the pricing page shows "Available soon on Gumroad" and license activation answers "not configured".

## 1. API on Render (Blueprint)

1. Push this folder to a GitHub repository.
2. In Render, choose New → Blueprint and select the repository. `render.yaml` creates:
   - the `sopcheck-api` Docker service, with a 5 GB disk at `/data` for uploads;
   - the `sopcheck-db` Postgres database;
   - a generated `JWT_SECRET`.
3. Fill in the prompted secrets:
   - `OPENAI_API_KEY`;
   - `CORS_ORIGINS`, for example `https://<user>.github.io` or your custom domain;
   - `PUBLIC_BASE_URL`;
   - the four `PLAN_*` Gumroad values.
4. The service refuses to start if any of these production settings is unsafe (enforced by `APP_ENV=production`, which the Dockerfile sets):
   - `JWT_SECRET` is shorter than 32 characters;
   - demo seeding is on;
   - `COOKIE_SECURE` is not true;
   - `CORS_ORIGINS` still points at localhost.

## 1b. Alternatively: API on Fly.io

```bash
fly launch --no-deploy --copy-config        # keep fly.toml; rename the app if "sopcheck-api" is taken
fly volumes create sopcheck_data --size 3
fly secrets set OPENAI_API_KEY=... JWT_SECRET=$(openssl rand -hex 32) CORS_ORIGINS=https://<user>.github.io \
  PLAN_STARTER_PRODUCT_ID=... PLAN_TEAM_PRODUCT_ID=... PLAN_STARTER_CHECKOUT_URL=... PLAN_TEAM_CHECKOUT_URL=...
fly deploy
```

Without Postgres, SQLite on the volume works for a single machine. For more scale, run `fly postgres create`, then `fly postgres attach`. Plain `postgres://` URLs are converted to the psycopg 3 driver automatically.

## 2. Frontend on GitHub Pages

1. In the repo, go to Settings → Pages → Source and choose **GitHub Actions**.
2. Under Settings → Secrets and variables → Actions → **Variables**, set:
   - `SOPCHECK_API_BASE`: the API URL, for example `https://sopcheck-api.onrender.com`, with no trailing slash;
   - `SOPCHECK_BASE_PATH`: `/<repo-name>` for `https://<user>.github.io/<repo-name>/`. Leave it empty if you use a custom domain.
3. Push to `main`, or run the workflow manually. It builds `frontend/out` with the demo-account tiles hidden.

How the static build works:

- There is no Next.js server, so the browser calls the API directly over CORS.
- The session token is kept in `sessionStorage`, scoped to the tab, and sent as a Bearer header.
- Pages protect themselves on the client side, and the API enforces auth, roles, tenancy, Terms acceptance, attestation and billing on every request.
- With a custom domain on the same site as the API (for example `app.example.com` and `api.example.com`), httpOnly cookies also work.

You can also run the regular server build (`next build && next start`) on any Node host. That build proxies `/api` and uses the httpOnly cookie.

## 3. Gumroad

1. Create two **membership** products, Starter at $49/month and Team at $99/month, and turn on **"Generate a unique license key per sale"** for each.
2. Copy each product's **product_id**, shown in the license-key section of the product's settings. Then copy its public link into the matching `PLAN_*` env vars.
3. Customers buy on Gumroad, receive a license key, and paste it into **Manager → Billing**. The API then:
   - calls `POST https://api.gumroad.com/v2/licenses/verify` with `increment_uses_count=false`;
   - rejects refunded, charged-back, disputed, ended, cancelled or failed-payment memberships;
   - re-checks lazily every `LICENSE_RECHECK_DAYS` days, which is 7 by default.
4. Support and refunds go through Gumroad only. The app shows no contact address.

## 4. Operator CLI

```bash
cd backend && python -m app.cli set-plan --company <slug> --plan team          # comp / fix a plan manually
python -m app.cli create-user --company <slug> --email <login> --password '...' --name '...' --role manager
```

## Launch checklist

Before launch:

- [ ] Optional: have a lawyer review `legal/terms.md` and `legal/privacy.md`.
- [ ] After any legal edit, bump `TERMS_VERSION` so every user re-accepts.
- Optional, not required: forming an entity or buying liability insurance later is a personal choice. Nothing in the app depends on either.
- [ ] Create the Gumroad membership products with license keys enabled, then set the four `PLAN_*` vars.
- [ ] Deploy the API (Render or Fly) and set the secrets, using a new OpenAI key with a usage cap in OpenAI's billing settings.
- [ ] Enable GitHub Pages and set the `SOPCHECK_API_BASE` and `SOPCHECK_BASE_PATH` variables.
- [ ] Smoke test: sign up, accept the Terms, upload an SOP, see the AI-draft banner, publish with the attestation, check the technician view and footer, and download an export (which carries the disclaimer).
- [ ] Buy your own Starter plan on Gumroad (then refund it) to test license activation end to end.
- [ ] Configure database backups (Render or Fly Postgres snapshots).
- [ ] Optional: a custom domain for Pages and for the API.

## Data deletion (Account area)
- The API purges workspaces whose deletion grace period has ended. This runs at startup and hourly (`PURGE_INTERVAL_S`). On hosts that sleep idle instances (Render free tier, Fly auto-stop), also schedule `python -m app.cli purge-deleted` daily so deletion happens on time.
- `UPLOAD_DIR` must be the persistent volume (`/data/uploads`) so the purge deletes the real photo files.
- Backups: keep backup retention at 30 days or less to match privacy.md section 4, or update the policy.
- [ ] Optional: attorney review of the privacy.md section 4 retention periods (marked [ATTORNEY REVIEW]).

## Preview: in-browser demo on GitHub Pages
`frontend/scripts_build_demo.sh` builds a static site in which the real FastAPI backend runs inside the browser with Pyodide, using the offline mock parser. There is no server and there are no AI calls. Data is saved per browser in IndexedDB, and `?reset-demo=1` resets it.
- **Seed data:** `frontend/demo/seed.db` holds the generic samples and demo logins. To regenerate it, start the API with `LLM_MODEL=mock` on a fresh DB and run the upload part of `reset_demo.sh`.
- **Publishing:** the contents of `frontend/out/` go on the `gh-pages` branch.
- **Limit:** OCR of scanned PDFs is not available in the browser demo. Text PDFs, .txt and .md files work.
