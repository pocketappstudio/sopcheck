"""Runtime configuration (env vars)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_DIR = BASE_DIR.parent                              # sopcheck/
PROMPT_FILE = Path(os.getenv("PROMPT_FILE", PROJECT_DIR / "prompts" / "system_prompt.md"))

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")
if DATABASE_URL.startswith(("postgres://", "postgresql://")):       # Render/Fly/Heroku style URLs -> psycopg 3 driver
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL.split("://", 1)[1]
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "data" / "uploads"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None      # any OpenAI-compatible endpoint
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1")          # any chat model the key can access; see README


def is_reasoning_model(m: str) -> bool:
    return m.startswith(("o1", "o3", "o4", "gpt-5"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "120"))
LLM_MAX_REPAIRS = int(os.getenv("LLM_MAX_REPAIRS", "1"))
LLM_FALLBACK_TO_MOCK = os.getenv("LLM_FALLBACK_TO_MOCK", "true").lower() == "true"
CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "24000"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
DEMO_SEED = os.getenv("DEMO_SEED", "true").lower() == "true"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

PROMPT_VERSION = "sop-extract/1.2.0"

def parser_mode() -> str:
    return "llm" if OPENAI_API_KEY else "mock"

# ---- legal ----
TERMS_VERSION = os.getenv("TERMS_VERSION", "2026-10-17-r5")   # bump when legal/terms.md or privacy.md change -> users re-accept
LEGAL_DIR = Path(os.getenv("LEGAL_DIR", PROJECT_DIR / "legal"))
# Operator name substituted into legal/*.md ({{OWNER_LEGAL_NAME}}).
OWNER_LEGAL_NAME = os.getenv("OWNER_LEGAL_NAME", "SOPCheck")

# ---- billing (Gumroad memberships; all config-driven) ----
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "14"))
TRIAL_MAX_TECHNICIANS = int(os.getenv("TRIAL_MAX_TECHNICIANS", "10"))
TRIAL_MAX_PARSES = int(os.getenv("TRIAL_MAX_PARSES", "50"))
GUMROAD_VERIFY_URL = os.getenv("GUMROAD_VERIFY_URL", "https://api.gumroad.com/v2/licenses/verify")
GUMROAD_TIMEOUT_S = float(os.getenv("GUMROAD_TIMEOUT_S", "10"))
LICENSE_RECHECK_DAYS = int(os.getenv("LICENSE_RECHECK_DAYS", "7"))   # re-verify paid licenses lazily (cancellations)
BILLING_ENFORCED = os.getenv("BILLING_ENFORCED", "true").lower() == "true"
PLANS = {   # override any field with PLAN_<KEY>_<FIELD> env vars, e.g. PLAN_STARTER_PRODUCT_ID
    key: {
        "key": key, "name": name,
        "price_usd_month": float(os.getenv(f"PLAN_{key.upper()}_PRICE", price)),
        "max_technicians": int(os.getenv(f"PLAN_{key.upper()}_MAX_TECHNICIANS", techs)),
        "max_parses_month": int(os.getenv(f"PLAN_{key.upper()}_MAX_PARSES", parses)),
        "gumroad_product_id": os.getenv(f"PLAN_{key.upper()}_PRODUCT_ID", ""),       # Gumroad product_id (not permalink)
        "checkout_url": os.getenv(f"PLAN_{key.upper()}_CHECKOUT_URL", ""),           # e.g. https://<you>.gumroad.com/l/<permalink>
    }
    for key, name, price, techs, parses in [("starter", "Starter", "49", "10", "50"), ("team", "Team", "99", "50", "200")]
}

# ---- deployment ----
ENV = os.getenv("APP_ENV", "development")                       # set APP_ENV=production on the server
SIGNUPS_PER_IP_HOUR = int(os.getenv("SIGNUPS_PER_IP_HOUR", "5"))
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")           # "none" (+ COOKIE_SECURE=true) only if a cookie must cross sites


def check_production() -> None:
    """Refuse to boot a production server with demo data or a default signing key."""
    if ENV != "production":
        return
    problems = []
    if not os.getenv("JWT_SECRET") or len(os.getenv("JWT_SECRET", "")) < 32:
        problems.append("JWT_SECRET must be set (>= 32 random chars)")
    if DEMO_SEED:
        problems.append("DEMO_SEED must be false (demo users have published passwords)")
    if os.getenv("COOKIE_SECURE", "false").lower() != "true":
        problems.append("COOKIE_SECURE must be true (HTTPS)")
    if any(o.startswith("http://localhost") for o in CORS_ORIGINS):
        problems.append("CORS_ORIGINS must list your real frontend origin(s), e.g. https://<user>.github.io")
    if problems:
        raise RuntimeError("Unsafe production config: " + "; ".join(problems))

# ---- account / data deletion ----
DELETION_GRACE_DAYS = float(os.getenv("DELETION_GRACE_DAYS", "7"))   # workspace deletion grace period before hard delete
PURGE_INTERVAL_S = float(os.getenv("PURGE_INTERVAL_S", "3600"))     # how often the background purge runs
