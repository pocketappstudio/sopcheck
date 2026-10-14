"""Admin CLI (no user-admin UI yet).
Usage: ./venv/bin/python -m app.cli create-user --company demo --email x@demo.co --password '...' --name 'Full Name'
       --role technician [--position 'Field Technician' --site 'Main Floor']
       ./venv/bin/python -m app.cli set-plan --company demo --plan team|starter|trial|expired [--restart-trial]
       ./venv/bin/python -m app.cli purge-deleted   # cron-friendly; the server also runs it hourly
Technician accounts count against the plan's seat limit (pass --ignore-seat-limit to override as an operator)."""
import argparse
from sqlalchemy import select
from .db import Base, SessionLocal, engine
from .auth import hash_password
from . import billing
from .models import Company, Technician, User, now


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    for a in ("company", "email", "password", "name"):
        cu.add_argument(f"--{a}", required=True)
    cu.add_argument("--role", choices=["manager", "technician", "admin"], default="technician")
    cu.add_argument("--position"); cu.add_argument("--site")
    cu.add_argument("--ignore-seat-limit", action="store_true")
    sp = sub.add_parser("set-plan")
    sp.add_argument("--company", required=True)
    sp.add_argument("--plan", required=True, choices=["trial", "starter", "team", "expired"])
    sp.add_argument("--restart-trial", action="store_true")
    sub.add_parser("purge-deleted", help="hard-delete workspaces whose deletion grace period has ended")
    args = ap.parse_args()
    Base.metadata.create_all(engine)
    if args.cmd == "purge-deleted":
        from .main import purge_due_companies
        print(f"purged {purge_due_companies()} workspace(s)"); return
    with SessionLocal() as db:
        c = db.scalar(select(Company).where(Company.slug == args.company))
        if not c:
            raise SystemExit(f"unknown company slug '{args.company}'")
        if args.cmd == "set-plan":
            c.plan = args.plan
            if args.restart_trial:
                c.trial_started_at = now()
            db.commit(); print(f"{c.name}: plan={c.plan}"); return
        if (args.role == "technician" or args.position) and not args.ignore_seat_limit:
            st = billing.status(db, c)
            if st["usage"]["technicians"] >= st["limits"]["max_technicians"]:
                raise SystemExit(f"seat limit reached ({st['usage']['technicians']}/{st['limits']['max_technicians']} on {st['plan_name']})")
        if db.scalar(select(User).where(User.email == args.email.lower())):
            raise SystemExit(f"user {args.email} already exists")
        tech_id = None
        if args.role == "technician" or args.position:
            t = Technician(company_id=c.id, display_name=args.name, position=args.position, site=args.site)
            db.add(t); db.flush(); tech_id = t.id
        db.add(User(company_id=c.id, email=args.email.lower(), password_hash=hash_password(args.password),
                    full_name=args.name, role=args.role, technician_id=tech_id))
        db.commit()
        print(f"created {args.role} {args.email} in {c.name}" + (f" (technician #{tech_id})" if tech_id else ""))


if __name__ == "__main__":
    main()
