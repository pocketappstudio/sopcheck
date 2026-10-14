"""Plans, limits and Gumroad license verification (config-driven; see config.PLANS / .env.example).

Tiers: 14-day free trial -> Starter / Team monthly memberships sold on Gumroad. Limits are enforced
server-side on technician seats (account creation) and monthly document parses (upload / parse-text).
Support and refunds are handled through Gumroad; this module never references a contact address."""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .models import Company, ParseRun, Technician, User, now

UPGRADE_HINT = "Upgrade on the Pricing page (/pricing) and enter your Gumroad license key under Billing."


def trial_ends_at(c: Company) -> datetime:
    return (c.trial_started_at or c.created_at) + timedelta(days=config.TRIAL_DAYS)


def limits(c: Company) -> dict:
    if c.plan in config.PLANS:
        p = config.PLANS[c.plan]
        return {"max_technicians": p["max_technicians"], "max_parses_month": p["max_parses_month"]}
    if c.plan == "trial":
        return {"max_technicians": config.TRIAL_MAX_TECHNICIANS, "max_parses_month": config.TRIAL_MAX_PARSES}
    return {"max_technicians": 0, "max_parses_month": 0}


def month_start_utc() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def parses_this_month(db: Session, company_id: int) -> int:
    """Original document parses (version 1 of each document, LLM or mock). Manager edits and JSON imports don't count."""
    return db.scalar(select(func.count(ParseRun.id)).where(
        ParseRun.company_id == company_id, ParseRun.version_no == 1, ParseRun.status != "imported",
        ParseRun.created_at >= month_start_utc())) or 0


def technician_seats(db: Session, company_id: int) -> int:
    return db.scalar(select(func.count(User.id)).join(Technician, User.technician_id == Technician.id)
                     .where(User.company_id == company_id, User.active)) or 0


def _effective_plan(db: Session, c: Company) -> str:
    if c.plan == "trial" and datetime.now(timezone.utc) >= trial_ends_at(c):
        c.plan = "expired"; db.flush()
    elif c.plan in config.PLANS and c.license_key and c.license_verified_at and \
            datetime.now(timezone.utc) - c.license_verified_at > timedelta(days=config.LICENSE_RECHECK_DAYS):
        try:                                            # lazy re-check catches cancelled / refunded memberships
            res = verify_license(c.license_key)
            if res["ok"]:
                c.plan, c.license_verified_at, c.license_status = res["plan"], now(), "active"
            else:
                c.plan, c.license_status = "expired", res["reason"]
            db.flush()
        except httpx.HTTPError:
            pass                                        # Gumroad unreachable: keep the current plan, retry next time
    return c.plan


def status(db: Session, c: Company) -> dict:
    plan = _effective_plan(db, c)
    lim = limits(c)
    p = config.PLANS.get(plan)
    te = trial_ends_at(c)
    return {
        "plan": plan, "plan_name": p["name"] if p else {"trial": "Free trial", "expired": "Expired"}.get(plan, plan),
        "active": plan != "expired",
        "trial_ends_at": te if plan in ("trial", "expired") else None,
        "trial_days_left": max(0, math.ceil((te - datetime.now(timezone.utc)).total_seconds() / 86400)) if plan == "trial" else None,
        "limits": lim,
        "usage": {"technicians": technician_seats(db, c.id), "parses_this_month": parses_this_month(db, c.id),
                  "month_start_utc": month_start_utc()},
        "license": {"last4": c.license_key_last4, "verified_at": c.license_verified_at, "status": c.license_status},
        "enforced": config.BILLING_ENFORCED,
    }


def require_parse_quota(db: Session, c: Company) -> None:
    if not config.BILLING_ENFORCED:
        return
    plan = _effective_plan(db, c)
    if plan == "expired":
        raise HTTPException(402, {"code": "plan_expired", "message":
                                  f"Your free trial or membership has ended, so new documents can't be parsed. Existing checklists "
                                  f"stay available. {UPGRADE_HINT}"})
    lim, used = limits(c)["max_parses_month"], parses_this_month(db, c.id)
    if used >= lim:
        nxt = "the Team plan" if plan in ("trial", "starter") else "next month's allowance"
        raise HTTPException(402, {"code": "parse_limit", "limit": lim, "used": used, "message":
                                  f"Monthly parse limit reached ({used}/{lim} documents on the {status(db, c)['plan_name']} plan). "
                                  f"The counter resets on the 1st (UTC). For more parses, upgrade to {nxt}. {UPGRADE_HINT}"})


def require_seat(db: Session, c: Company) -> None:
    if not config.BILLING_ENFORCED:
        return
    plan = _effective_plan(db, c)
    if plan == "expired":
        raise HTTPException(402, {"code": "plan_expired", "message": f"Your free trial or membership has ended. {UPGRADE_HINT}"})
    lim, used = limits(c)["max_technicians"], technician_seats(db, c.id)
    if used >= lim:
        raise HTTPException(402, {"code": "seat_limit", "limit": lim, "used": used, "message":
                                  f"Technician seat limit reached ({used}/{lim} on the {status(db, c)['plan_name']} plan). "
                                  f"Deactivate a technician or upgrade for more seats. {UPGRADE_HINT}"})


def verify_license(license_key: str) -> dict:
    """Check a key against Gumroad's License API for each configured plan product.
    POST https://api.gumroad.com/v2/licenses/verify  product_id=<id>&license_key=<key>&increment_uses_count=false
    A membership is valid when success=true and the purchase is not refunded / charged back / disputed and the
    subscription has not ended, been cancelled or failed payment."""
    configured = [p for p in config.PLANS.values() if p["gumroad_product_id"]]
    if not configured:
        raise HTTPException(503, {"code": "billing_not_configured", "message":
                                  "License verification is not configured yet (no Gumroad product ids set on the server)."})
    last_reason = "License key not recognised for any SOPCheck plan."
    for p in sorted(configured, key=lambda x: -x["price_usd_month"]):      # try the higher tier first
        r = httpx.post(config.GUMROAD_VERIFY_URL, timeout=config.GUMROAD_TIMEOUT_S,
                       data={"product_id": p["gumroad_product_id"], "license_key": license_key.strip(),
                             "increment_uses_count": "false"})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 404 or not body.get("success"):
            continue
        pur = body.get("purchase") or {}
        for flag, why in [("refunded", "refunded"), ("chargebacked", "charged back"), ("disputed", "disputed")]:
            if pur.get(flag):
                return {"ok": False, "plan": None, "reason": f"This purchase was {why}."}
        for f, why in [("subscription_ended_at", "has ended"), ("subscription_cancelled_at", "was cancelled"),
                       ("subscription_failed_at", "has a failed payment")]:
            if pur.get(f):
                return {"ok": False, "plan": None, "reason": f"This membership {why} ({pur.get(f)}). Renew it on Gumroad."}
        return {"ok": True, "plan": p["key"], "reason": "active", "uses": body.get("uses")}
    return {"ok": False, "plan": None, "reason": last_reason}


def public_plans() -> dict:
    return {"trial": {"days": config.TRIAL_DAYS, "max_technicians": config.TRIAL_MAX_TECHNICIANS,
                      "max_parses_month": config.TRIAL_MAX_PARSES},
            "plans": [{k: v for k, v in p.items() if k != "gumroad_product_id"} | {"purchasable": bool(p["checkout_url"])}
                      for p in config.PLANS.values()],
            "support": "Billing, cancellations and refunds are handled through Gumroad (your purchase receipt / Gumroad library)."}
