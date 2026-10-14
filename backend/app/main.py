"""FastAPI app: auth -> upload/OCR -> parse -> safety audit -> edit/version -> publish ->
distribute (filters, company timezone shift windows) -> technician submissions.
Every query is scoped to the caller's company_id (taken from the session, never from the client)."""
from __future__ import annotations
import hashlib
import re
import threading
import time as _time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing, config
from . import usage as usage_meter
from .auth import (COOKIE_NAME, COOKIE_SECURE, SESSION_HOURS, authenticate, current_session, current_user,
                   hash_password, issue_token, require_manager, require_technician, verify_password)
from .db import Base, SessionLocal, engine, get_db
from .ingest import Extraction, extract, is_safety_critical, normalize, pdf_title
from .llm import NoTasksFound, ParseResult, load_prompts, parse_document, validate as validate_llm_json
from .mock_parser import choose_verification, make_title
from .models import (DEFAULT_SHIFT_WINDOWS, AuditLog, AuthSession, Company, DeletionTombstone, Document, ParseRun, SafetyFlag,
                     Submission, Task, Technician, User, now)
from .safety import missing_part, audit as safety_audit
from .schemas import (AcceptTerms, CompanyDelete, SelfDelete, SignupRequest, Checklist, CompanySettings, LicenseKey, TechnicianCreate, EditChecklist, LoginRequest, ParseTextRequest, ResolveFlag,
                      SubmissionCreate, SubmissionOut, TaskOut)
from .timeutil import VALID_ZONES, local_day_start_utc, window_for

CORE_FIELDS = ["task_title", "description", "assigned_position", "site_requirement", "timeframe", "verification_method"]

DEMO_USERS = [  # (company_slug, email, password, full_name, role, technician(position, site) or None)
    ("demo", "manager@demo.co", "Manager!2026", "Morgan Blake", "manager", None),
    ("demo", "alex@demo.co", "Tech!2026", "Alex Rivera", "technician", ("Authorized Electrician", "Main Electrical Room")),
    ("demo", "jordan@demo.co", "Tech!2026", "Jordan Lee", "technician", ("Site Safety Officer", "Main Electrical Room")),
    ("demo", "sam@demo.co", "Tech!2026", "Sam Patel", "technician", ("RN", "Patient Bedside")),
    ("demo", "casey@demo.co", "Tech!2026", "Casey Morgan", "technician", ("Opening Manager", "Sales Floor")),
    ("demo", "taylor@demo.co", "Tech!2026", "Taylor Brooks", "technician", ("HVAC Technician", "Rooftop")),
    ("globex", "manager@globex.co", "Manager!2026", "Dana Whitfield", "manager", None),
    ("globex", "tech@globex.co", "Tech!2026", "Riley Chen", "technician", ("Certified Forklift Operator", "Charging Area")),
]


def seed(db: Session):
    if db.scalar(select(User).limit(1)):
        return
    companies = {
        "demo": Company(name="Demo Company", slug="demo", timezone="America/Chicago", settings={"shift_windows": DEFAULT_SHIFT_WINDOWS}),
        "globex": Company(name="Globex Facilities", slug="globex", timezone="America/New_York", settings={"shift_windows": DEFAULT_SHIFT_WINDOWS}),
    }
    for c in companies.values():
        db.add(c)
    db.flush()
    for slug, email, pw, name, role, tech in DEMO_USERS:
        c = companies[slug]
        tech_id = None
        if tech:
            t = Technician(company_id=c.id, display_name=name, position=tech[0], site=tech[1])
            db.add(t); db.flush(); tech_id = t.id
        db.add(User(company_id=c.id, email=email, password_hash=hash_password(pw), full_name=name, role=role, technician_id=tech_id))
    db.commit()


@asynccontextmanager
async def lifespan(_app):
    config.check_production()
    Base.metadata.create_all(engine)
    load_prompts()
    if config.DEMO_SEED:
        with SessionLocal() as db:
            seed(db)
    stop = threading.Event()
    threading.Thread(target=_purge_loop, args=(stop,), daemon=True, name="purge").start()
    yield
    stop.set()


app = FastAPI(title="SOPCheck API — Universal SOP → Technician Checklist", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Terms-Version", "Content-Disposition"])
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- helpers ----------------
def audit(db: Session, user: User | None, entity: str, entity_id: int, action: str, data: dict | None = None, company_id: int | None = None):
    db.add(AuditLog(company_id=company_id or user.company_id, actor_user_id=user.id if user else None,
                    entity_type=entity, entity_id=entity_id, action=action, data=data))


def get_doc(db: Session, user: User, doc_id: int) -> Document:
    d = db.get(Document, doc_id)
    if not d or d.company_id != user.company_id:      # 404 (not 403) so other tenants can't probe ids
        raise HTTPException(404, "document not found")
    return d


def run_tasks(db: Session, run: ParseRun) -> list[Task]:
    return db.scalars(select(Task).where(Task.parse_run_id == run.id).order_by(Task.sequence)).all()


def task_json(t: Task) -> dict:
    return {"task_id": t.task_code, **{f: getattr(t, f) for f in CORE_FIELDS}}


def checklist_of(db: Session, run: ParseRun) -> dict:
    return {"industry": run.industry or "General", "document_summary": run.document_summary or "",
            "tasks": [task_json(t) for t in run_tasks(db, run)]}


def draft_run(doc: Document) -> ParseRun | None:
    return next((r for r in reversed(doc.runs) if r.version_status == "draft"), None)


def published_run(doc: Document) -> ParseRun | None:
    return next((r for r in doc.runs if r.id == doc.published_run_id), None)


def editable_base(doc: Document) -> ParseRun:
    return draft_run(doc) or published_run(doc) or doc.runs[-1]


def run_dict(r: ParseRun) -> dict:
    return {"id": r.id, "version_no": r.version_no, "version_status": r.version_status, "parent_run_id": r.parent_run_id,
            "status": r.status, "parser": r.parser, "model": r.model, "prompt_version": r.prompt_version,
            "chunk_count": r.chunk_count, "repair_attempts": r.repair_attempts, "latency_ms": r.latency_ms, "usage": r.usage,
            "notes": r.notes, "validation_errors": r.validation_errors, "safety_audit": r.safety_audit,
            "created_at": r.created_at, "updated_at": r.updated_at}


def doc_dict(d: Document) -> dict:
    dr, pr = draft_run(d), published_run(d)
    return {"id": d.id, "title": d.title, "source_filename": d.source_filename, "status": d.status,
            "page_count": d.page_count, "extraction_method": d.extraction_method, "ocr_confidence": d.ocr_confidence,
            "ocr_pages": d.ocr_pages, "published_run_id": d.published_run_id, "published_at": d.published_at,
            "published_version": pr.version_no if pr else None, "draft_run_id": dr.id if dr else None,
            "draft_version": dr.version_no if dr else None, "created_at": d.created_at, "char_count": len(d.raw_text or ""),
            "industry": (dr or pr or (d.runs[-1] if d.runs else None)).industry if d.runs else None,
            "parser": d.runs[0].parser if d.runs else None,
            "task_count": len((dr or pr or d.runs[-1]).output_json.get("tasks", [])) if d.runs and (dr or pr or d.runs[-1]).output_json else 0,
            "safety": (dr or pr or d.runs[-1]).safety_audit if d.runs else None}


def flag_dict(f: SafetyFlag) -> dict:
    return {k: getattr(f, k) for k in ("id", "span_hash", "span_text", "section", "categories", "atoms", "coverage", "score",
                                       "best_task_code", "matched_atoms", "missing_atoms", "detector", "resolution",
                                       "resolution_reason", "resolution_task_code", "resolved_by", "resolved_at")}


def recompute_safety(db: Session, doc: Document, run: ParseRun):
    """Re-run the hybrid audit for a version and carry manager resolutions forward by span hash."""
    from types import SimpleNamespace
    prior = {f.span_hash: SimpleNamespace(**{k: getattr(f, k) for k in ("resolution", "resolution_reason", "resolution_task_code",
                                                                        "resolved_by", "resolved_at")})
             for f in db.scalars(select(SafetyFlag).where(SafetyFlag.document_id == doc.id).order_by(SafetyFlag.id)).all()
             if f.resolution}
    for f in db.scalars(select(SafetyFlag).where(SafetyFlag.parse_run_id == run.id)).all():
        db.delete(f)
    db.flush()
    cl = checklist_of(db, run)
    rep = safety_audit(doc.raw_text, cl["tasks"], cl["document_summary"])
    unresolved = 0
    for s in rep["spans"]:
        f = SafetyFlag(company_id=doc.company_id, document_id=doc.id, parse_run_id=run.id, span_hash=s["span_hash"],
                       span_text=s["span_text"], section=s["section"], categories=s["categories"], atoms=s["atoms"],
                       coverage=s["coverage"], score=s["score"], best_task_code=s["best_task_code"],
                       matched_atoms=s["matched_atoms"], missing_atoms=s["missing_atoms"], detector=s["detector"])
        p = prior.get(s["span_hash"])
        if p and (p.resolution == "dismissed" or s["coverage"] == "covered"):
            f.resolution, f.resolution_reason, f.resolution_task_code = p.resolution, p.resolution_reason, p.resolution_task_code
            f.resolved_by, f.resolved_at = p.resolved_by, p.resolved_at
        if s["coverage"] != "covered" and not f.resolution:
            unresolved += 1
        db.add(f)
    run.safety_audit = {"counts": rep["counts"], "total": rep["total"], "coverage_pct": rep["coverage_pct"],
                        "unresolved": unresolved, "method": rep["method"]}
    run.output_json = cl
    db.flush()


def renumber(db: Session, run: ParseRun, ordered: list[Task]):
    for t in ordered:                                   # two-phase to dodge the (run, task_code) unique constraint
        t.task_code = f"tmp-{t.id}"
    db.flush()
    for i, t in enumerate(ordered, 1):
        t.sequence, t.task_code = i, f"T-{i:03d}"
    db.flush()


def ensure_draft(db: Session, doc: Document, user: User) -> tuple[ParseRun, dict[int, int]]:
    """Return the draft version, creating v(n+1) as a copy of the published one if needed."""
    d = draft_run(doc)
    if d:
        return d, {}
    base = published_run(doc) or doc.runs[-1]
    new = ParseRun(company_id=doc.company_id, document_id=doc.id, version_no=max(r.version_no for r in doc.runs) + 1,
                   version_status="draft", parent_run_id=base.id, status="edited", parser="manual_edit",
                   model=None, prompt_version=base.prompt_version, chunk_count=base.chunk_count,
                   industry=base.industry, document_summary=base.document_summary, output_json=base.output_json,
                   created_by=user.id, notes=f"Draft created from v{base.version_no} by {user.full_name}")
    db.add(new); db.flush()
    id_map = {}
    for t in run_tasks(db, base):
        c = Task(company_id=t.company_id, document_id=t.document_id, parse_run_id=new.id, task_code=t.task_code,
                 sequence=t.sequence, is_safety_critical=t.is_safety_critical, origin_task_id=t.id,
                 **{f: getattr(t, f) for f in CORE_FIELDS})
        db.add(c); db.flush(); id_map[t.id] = c.id
    doc.runs.append(new)
    audit(db, user, "document", doc.id, "version_create", {"version_no": new.version_no, "from_version": base.version_no, "run_id": new.id})
    return new, id_map


def apply_edit(db: Session, doc: Document, payload: EditChecklist, user: User) -> ParseRun:
    base = editable_base(doc)
    if payload.base_run_id != base.id:
        raise HTTPException(409, f"Checklist changed since you started editing (current version run id {base.id}). Reload and retry.")
    run, id_map = ensure_draft(db, doc, user)
    existing = {t.id: t for t in run_tasks(db, run)}
    before_order = [t.id for t in sorted(existing.values(), key=lambda t: t.sequence)]
    before_codes = {t.id: t.task_code for t in existing.values()}
    keep, ordered, changes = set(), [], []
    for item in payload.tasks:
        tid = id_map.get(item.id, item.id) if item.id else None
        if tid and tid in existing:
            t = existing[tid]
            diff = {f: [getattr(t, f), getattr(item, f)] for f in CORE_FIELDS if getattr(t, f) != getattr(item, f)}
            if diff:
                for f, (_, new_v) in diff.items():
                    setattr(t, f, new_v)
                t.is_safety_critical = is_safety_critical(t.description)
                t.edited_by = user.id
                changes.append(("task_update", t, {"task_code": before_codes[t.id], "changes": diff}))
            keep.add(tid)
        else:
            if item.id and not tid in existing:
                raise HTTPException(422, f"task id {item.id} does not belong to this document version")
            t = Task(company_id=doc.company_id, document_id=doc.id, parse_run_id=run.id, task_code=f"new-{uuid.uuid4().hex[:8]}",
                     sequence=10_000 + len(ordered), is_safety_critical=is_safety_critical(item.description), edited_by=user.id,
                     **{f: getattr(item, f) for f in CORE_FIELDS})
            db.add(t); db.flush()
            changes.append(("task_add", t, {f: getattr(item, f) for f in CORE_FIELDS}))
            keep.add(t.id)
        ordered.append(t)
    for tid, t in existing.items():
        if tid not in keep:
            changes.append(("task_delete", t, {"task_code": t.task_code, **{f: getattr(t, f) for f in CORE_FIELDS}}))
            db.delete(t)
    db.flush()
    renumber(db, run, ordered)
    after_order = [t.id for t in ordered]
    kept_before = [i for i in before_order if i in keep]
    kept_after = [i for i in after_order if i in existing]
    if kept_before != kept_after:
        changes.append(("task_reorder", None, {"before": [before_codes[i] for i in kept_before],
                                                "after_positions": [before_codes[i] for i in kept_after]}))
    if payload.industry and payload.industry != run.industry:
        changes.append(("checklist_update", None, {"industry": [run.industry, payload.industry]})); run.industry = payload.industry
    if payload.document_summary and payload.document_summary != run.document_summary:
        changes.append(("checklist_update", None, {"document_summary": [run.document_summary, payload.document_summary]}))
        run.document_summary = payload.document_summary
    cl = checklist_of(db, run)
    Checklist.model_validate(cl)                       # final strict-schema guarantee
    for action, t, data in changes:
        if t is not None and action != "task_delete":
            data = {**data, "new_task_code": t.task_code}
        audit(db, user, "task" if t is not None else "parse_run", t.id if t is not None else run.id, action,
              {**data, "document_id": doc.id, "version_no": run.version_no})
    run.updated_at = now()
    recompute_safety(db, doc, run)
    return run


def doc_detail(db: Session, doc: Document, run: ParseRun | None = None) -> dict:
    db.refresh(doc)
    run = run or editable_base(doc)
    tasks = run_tasks(db, run)
    flags = db.scalars(select(SafetyFlag).where(SafetyFlag.parse_run_id == run.id).order_by(SafetyFlag.id)).all()
    return {
        "document": doc_dict(doc),
        "run": run_dict(run),
        "parse_run": run_dict(run),
        "versions": [{"id": r.id, "version_no": r.version_no, "version_status": r.version_status, "parser": r.parser,
                      "created_at": r.created_at, "task_count": len((r.output_json or {}).get("tasks", []))} for r in doc.runs],
        "editable": run.id == editable_base(doc).id,
        "checklist": checklist_of(db, run),
        "tasks": [{"id": t.id, **task_json(t), "is_safety_critical": t.is_safety_critical} for t in tasks],
        "safety": {"summary": run.safety_audit, "flags": [flag_dict(f) for f in flags]},
    }


# ---------------- auth ----------------
_failed: dict[str, list[float]] = defaultdict(list)


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    key = req.email.strip().lower()
    _failed[key] = [t for t in _failed[key] if t > _time.time() - 300]
    if len(_failed[key]) >= 5:
        raise HTTPException(429, "Too many failed attempts. Try again in 5 minutes.")
    user = authenticate(db, req.email, req.password)
    if not user:
        _failed[key].append(_time.time())
        raise HTTPException(401, "Invalid email or password")
    _failed.pop(key, None)
    token, exp = issue_token(db, user, request.headers.get("user-agent"))
    user.last_login_at = now()
    audit(db, user, "user", user.id, "login", {"ip": request.client.host if request.client else None})
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE, samesite=config.COOKIE_SAMESITE,
                        max_age=SESSION_HOURS * 3600, path="/")
    return {"token": token, "expires_at": exp, "user": me_dict(user)}


def me_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "company_id": u.company_id,
            "terms_version_required": config.TERMS_VERSION, "terms_version_accepted": u.terms_version,
            "terms_accepted_at": u.terms_accepted_at, "needs_terms": u.terms_version != config.TERMS_VERSION,
            "company_name": u.company.name, "company_timezone": u.company.timezone,
            "company_deletion_scheduled_for": u.company.deletion_scheduled_for,
            "technician": {"id": u.technician.id, "display_name": u.technician.display_name, "position": u.technician.position,
                           "site": u.technician.site} if u.technician else None}


_signups: dict[str, list[float]] = defaultdict(list)


@app.post("/api/auth/signup", status_code=201)
def signup(req: SignupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Self-serve trial: creates a company on the free trial plus its first manager. The Terms click-through is
    part of signup (version + time + IP stored)."""
    ip = request.client.host if request.client else "?"
    _signups[ip] = [t for t in _signups[ip] if t > _time.time() - 3600]
    if len(_signups[ip]) >= config.SIGNUPS_PER_IP_HOUR:
        raise HTTPException(429, "Too many signups from this network. Try again later.")
    if not req.accept_terms or req.terms_version != config.TERMS_VERSION:
        raise HTTPException(422, f"You must accept the current Terms of Service and Privacy Policy ({config.TERMS_VERSION}).")
    if req.timezone not in VALID_ZONES:
        raise HTTPException(422, f"Unknown IANA timezone '{req.timezone}'")
    email = req.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Enter a valid work login (an email-style address).")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with that login already exists. Sign in instead.")
    base = re.sub(r"[^a-z0-9]+", "-", req.company_name.lower()).strip("-")[:40] or "company"
    slug, n = base, 1
    while db.scalar(select(Company).where(Company.slug == slug)):
        n += 1; slug = f"{base}-{n}"
    c = Company(name=req.company_name.strip(), slug=slug, timezone=req.timezone, settings={"shift_windows": DEFAULT_SHIFT_WINDOWS})
    db.add(c); db.flush()
    u = User(company_id=c.id, email=email, password_hash=hash_password(req.password), full_name=req.full_name.strip(), role="manager",
             terms_version=config.TERMS_VERSION, terms_accepted_at=now(), terms_accept_ip=ip)
    db.add(u); db.flush()
    audit(db, u, "company", c.id, "signup", {"plan": "trial", "trial_days": config.TRIAL_DAYS})
    audit(db, u, "user", u.id, "terms_accept", {"version": config.TERMS_VERSION, "ip": ip, "at": "signup"})
    _signups[ip].append(_time.time())
    token, exp = issue_token(db, u, request.headers.get("user-agent"))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE, samesite=config.COOKIE_SAMESITE,
                        max_age=SESSION_HOURS * 3600, path="/")
    return {"token": token, "expires_at": exp, "user": me_dict(u)}


@app.post("/api/auth/logout")
def logout(response: Response, s=Depends(current_session), db: Session = Depends(get_db)):
    user, sess = s
    sess = db.merge(sess)
    sess.revoked_at = now()
    audit(db, user, "user", user.id, "logout")
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return me_dict(user)


@app.post("/api/auth/accept-terms")
def accept_terms(body: AcceptTerms, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Click-through acceptance of the Terms of Service + Privacy Policy; version, time and IP are stored per user."""
    if not body.accept or body.version != config.TERMS_VERSION:
        raise HTTPException(422, f"You must accept the current Terms version ({config.TERMS_VERSION}).")
    user = db.merge(user)
    user.terms_version, user.terms_accepted_at = config.TERMS_VERSION, now()
    user.terms_accept_ip = request.client.host if request.client else None
    audit(db, user, "user", user.id, "terms_accept", {"version": config.TERMS_VERSION, "ip": user.terms_accept_ip})
    db.commit()
    return me_dict(user)


# ---------------- legal + billing ----------------
LEGAL_DOCS = {"terms": "terms.md", "privacy": "privacy.md"}


@app.get("/api/legal/{name}", response_class=PlainTextResponse)
def legal_doc(name: str):
    if name not in LEGAL_DOCS:
        raise HTTPException(404, "not found")
    text = (config.LEGAL_DIR / LEGAL_DOCS[name]).read_text(encoding="utf-8")
    text = text.replace("{{OWNER_LEGAL_NAME}}", config.OWNER_LEGAL_NAME)
    return PlainTextResponse(text,
                             media_type="text/markdown; charset=utf-8", headers={"X-Terms-Version": config.TERMS_VERSION})


@app.get("/api/plans")
def plans():
    return billing.public_plans()


@app.get("/api/billing")
def billing_status(user: User = Depends(require_manager), db: Session = Depends(get_db)):
    st = billing.status(db, db.get(Company, user.company_id)); db.commit()
    return st


@app.post("/api/billing/license")
def activate_license(body: LicenseKey, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    try:
        res = billing.verify_license(body.license_key)
    except httpx.HTTPError as e:
        raise HTTPException(502, {"code": "gumroad_unreachable", "message": f"Could not reach Gumroad to verify the key ({type(e).__name__}). Try again shortly."})
    audit(db, user, "company", c.id, "license_verify", {"ok": res["ok"], "plan": res["plan"], "reason": res["reason"],
                                                       "key_last4": body.license_key.strip()[-4:]})
    if not res["ok"]:
        db.commit()
        raise HTTPException(402, {"code": "license_invalid", "message": res["reason"]})
    c.plan, c.license_key, c.license_key_last4 = res["plan"], body.license_key.strip(), body.license_key.strip()[-4:]
    c.license_verified_at, c.license_status = now(), "active"
    db.commit()
    return billing.status(db, c)


# ---------------- company ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "parser_mode": config.parser_mode(), "model": config.LLM_MODEL if config.parser_mode() == "llm" else None,
            "prompt_version": config.PROMPT_VERSION, "server_time_utc": datetime.now(timezone.utc)}


@app.get("/api/company")
def get_company(user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    return {"id": c.id, "name": c.name, "timezone": c.timezone, "shift_windows": (c.settings or {}).get("shift_windows", {})}


@app.patch("/api/company")
def update_company(body: CompanySettings, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    before = {"timezone": c.timezone, "shift_windows": (c.settings or {}).get("shift_windows")}
    if body.timezone is not None:
        if body.timezone not in VALID_ZONES:
            raise HTTPException(422, f"Unknown IANA timezone '{body.timezone}'")
        c.timezone = body.timezone
    if body.shift_windows is not None:
        c.settings = {**(c.settings or {}), "shift_windows": body.shift_windows}
    audit(db, user, "company", c.id, "settings_update", {"before": before, "after": body.model_dump(exclude_none=True)})
    db.commit()
    return get_company(user, db)


@app.post("/api/technicians", status_code=201)
def create_technician(body: TechnicianCreate, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """Add a technician login (uses one seat of the company's plan)."""
    billing.require_seat(db, db.get(Company, user.company_id))
    if db.scalar(select(User).where(User.email == body.email.strip().lower())):
        raise HTTPException(409, "A user with that login already exists")
    t = Technician(company_id=user.company_id, display_name=body.full_name, position=body.position, site=body.site)
    db.add(t); db.flush()
    u = User(company_id=user.company_id, email=body.email.strip().lower(), password_hash=hash_password(body.password),
             full_name=body.full_name, role="technician", technician_id=t.id)
    db.add(u); db.flush()
    audit(db, user, "user", u.id, "technician_create", {"position": body.position, "site": body.site})
    db.commit()
    return {"id": t.id, "user_id": u.id, "display_name": t.display_name, "position": t.position, "site": t.site}


@app.get("/api/technicians")
def technicians(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Technician, User).join(User, User.technician_id == Technician.id)
                      .where(Technician.company_id == user.company_id, User.active)).all()
    return [{"id": t.id, "display_name": t.display_name, "email": u.email, "position": t.position, "site": t.site} for t, u in rows]


# ---------------- documents (manager) ----------------
def merge_usage(extract: dict | None, audit_u: dict | None) -> dict | None:
    if not extract and not audit_u:
        return None
    out = {"extraction": extract, "safety_audit": audit_u}
    out["total_tokens"] = sum((u or {}).get("prompt_tokens", 0) + (u or {}).get("completion_tokens", 0) for u in (extract, audit_u))
    out["est_cost_usd"] = round(sum((u or {}).get("est_cost_usd", 0) for u in (extract, audit_u)), 5)
    return out


def ingest(db: Session, user: User, ex: Extraction, title: str, filename: str | None, mime: str | None, raw: bytes,
           result: ParseResult | None = None) -> dict:
    text = ex.text
    if len(text.strip()) < 20:
        raise HTTPException(422, "Document contains no extractable text, and OCR found none either. " + "; ".join(ex.warnings))
    if result is None:
        billing.require_parse_quota(db, db.get(Company, user.company_id))
        try:
            result = parse_document(text, title, user.company.name)
            usage_meter.start()                                 # second meter: safety-audit LLM/embedding calls
        except NoTasksFound as e:
            raise HTTPException(422, "No actionable steps were found in this document (" + str(e) + "). Upload a procedure "
                                     "that contains instructions, or check that the right file/pages were uploaded.")
        except Exception as e:
            raise HTTPException(502, f"Extraction failed: {type(e).__name__}: {str(e)[:500]}")
    cl = result.checklist
    doc = Document(company_id=user.company_id, title=title, source_filename=filename, mime_type=mime,
                   sha256=hashlib.sha256(raw).hexdigest(), raw_text=text, page_count=ex.page_count,
                   extraction_method=ex.method, ocr_confidence=ex.ocr_confidence, ocr_pages=ex.ocr_pages,
                   status="draft", created_by=user.id)
    db.add(doc); db.flush()
    notes = "; ".join(filter(None, [result.notes] + ex.warnings + ([f"OCR used on {len(ex.ocr_pages)} page(s), mean confidence {ex.ocr_confidence}%"] if ex.ocr_pages else [])))
    run = ParseRun(company_id=user.company_id, document_id=doc.id, version_no=1, version_status="draft",
                   status=result.status, parser=result.parser, model=result.model, prompt_version=config.PROMPT_VERSION,
                   chunk_count=result.chunk_count, repair_attempts=result.repair_attempts, industry=cl["industry"],
                   document_summary=cl["document_summary"], output_json=cl, raw_output=result.raw_output,
                   validation_errors=result.validation_errors, notes=notes or None, latency_ms=result.latency_ms, created_by=user.id)
    db.add(run); db.flush()
    for seq, t in enumerate(cl["tasks"], 1):
        db.add(Task(company_id=user.company_id, document_id=doc.id, parse_run_id=run.id, task_code=t["task_id"], sequence=seq,
                    is_safety_critical=is_safety_critical(t["description"]), **{f: t[f] for f in CORE_FIELDS}))
    db.flush()
    doc.runs.append(run) if run not in doc.runs else None
    recompute_safety(db, doc, run)
    run.usage = merge_usage(result.usage, usage_meter.summary(usage_meter._meter.get()))
    audit(db, user, "document", doc.id, "upload_parse", {"run_id": run.id, "parser": result.parser, "extraction": ex.method,
                                                          "ocr_confidence": ex.ocr_confidence})
    db.commit()
    return doc_detail(db, doc, run)


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), title: Optional[str] = Form(None),
                          user: User = Depends(require_manager), db: Session = Depends(get_db)):
    raw = await file.read()
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {config.MAX_UPLOAD_MB} MB")
    name = file.filename or "upload.txt"
    if not name.lower().endswith((".txt", ".pdf", ".md")):
        raise HTTPException(415, "Only .txt, .md and .pdf files are supported")
    try:
        ex = extract(raw, name, file.content_type)
    except Exception as e:
        raise HTTPException(422, f"Could not read file: {e}")
    if not title and name.lower().endswith(".pdf"):
        title = pdf_title(raw)                      # PDF metadata beats a cover-page fragment
    if not title:
        first = next((l.strip() for l in ex.text.split("\n") if l.strip()), "")
        title = first if 12 <= len(first) <= 120 and not first.lower().endswith((" for", " of", " and", " the")) \
            else name.rsplit(".", 1)[0].replace("_", " ").title()
    return ingest(db, user, ex, title, name, file.content_type, raw)


@app.post("/api/documents/import-json")
async def import_checklist(file: UploadFile = File(...), checklist: str = Form(...), parser: str = Form("manual-llm-sim"),
                           model: Optional[str] = Form(None), notes: Optional[str] = Form(None), title: Optional[str] = Form(None),
                           user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """Import an externally produced checklist JSON (e.g. an offline / simulated LLM run) together with its source
    document. The JSON goes through the same post-fix + strict validation as live LLM output; the source file goes
    through the normal extraction/OCR path so the safety audit runs against the real document text."""
    raw = await file.read()
    name = file.filename or "upload.pdf"
    parsed, errs, _ = validate_llm_json(checklist)
    if not parsed:
        raise HTTPException(422, {"errors": errs})
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,19}", parser):
        raise HTTPException(422, "parser label must be 2-20 chars of a-z 0-9 . _ -")
    try:
        ex = extract(raw, name, file.content_type)
    except Exception as e:
        raise HTTPException(422, f"Could not read file: {e}")
    if not title and name.lower().endswith(".pdf"):
        title = pdf_title(raw)
    title = title or name.rsplit(".", 1)[0].replace("_", " ").title()
    result = ParseResult(checklist=parsed.model_dump(), parser=parser, model=model, status="imported",
                         notes=notes or f"Checklist imported via /api/documents/import-json (parser={parser}); not produced by this server's LLM call.")
    return ingest(db, user, ex, title, name, file.content_type, raw, result=result)


@app.post("/api/documents/parse-text")
def parse_text(req: ParseTextRequest, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    return ingest(db, user, Extraction(text=normalize(req.text)), req.title, None, "text/plain", req.text.encode())


@app.get("/api/documents")
def list_documents(user: User = Depends(require_manager), db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).where(Document.company_id == user.company_id).order_by(Document.id.desc())).all()
    return [doc_dict(d) for d in docs]


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int, run_id: Optional[int] = None, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    d = get_doc(db, user, doc_id)
    run = None
    if run_id:
        run = next((r for r in d.runs if r.id == run_id), None)
        if not run:
            raise HTTPException(404, "version not found")
    return doc_detail(db, d, run)


@app.put("/api/documents/{doc_id}/checklist")
def edit_checklist(doc_id: int, body: EditChecklist, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """Save manager edits (field edits, add, delete, reorder). Editing a published version creates a new draft."""
    d = get_doc(db, user, doc_id)
    base = editable_base(d)
    if base and base.id == body.base_run_id:          # a no-op save must not spawn an empty new version of a published doc
        cur = [{"id": t.id, **{k: getattr(t, k) for k in CORE_FIELDS}} for t in run_tasks(db, base)]
        new = [{"id": t.id, **{k: getattr(t, k) for k in CORE_FIELDS}} for t in body.tasks]
        if cur == new and (body.industry in (None, base.industry)) and (body.document_summary in (None, base.document_summary)):
            return doc_detail(db, d, base)
    run = apply_edit(db, d, body, user)
    d.status = "published" if d.published_run_id else "draft"
    db.commit()
    return doc_detail(db, d, run)


ATTESTATION = "I have reviewed every task against the source document"


@app.post("/api/documents/{doc_id}/publish")
def publish(doc_id: int, force: bool = False, reason: Optional[str] = None, attest: bool = False,
            user: User = Depends(require_manager), db: Session = Depends(get_db)):
    d = get_doc(db, user, doc_id)
    run = draft_run(d)
    if not run:
        raise HTTPException(409, "Nothing to publish: no draft version")
    if not attest:
        raise HTTPException(428, {"code": "attestation_required", "attestation": ATTESTATION, "message":
                                  "AI-generated checklists must be reviewed before publishing. Confirm the attestation "
                                  f"'{ATTESTATION}' (attest=true)."})
    unresolved = db.scalars(select(SafetyFlag).where(SafetyFlag.parse_run_id == run.id, SafetyFlag.coverage != "covered",
                                                     SafetyFlag.resolution.is_(None))).all()
    if unresolved and not force:
        raise HTTPException(409, {"message": f"{len(unresolved)} unresolved safety flag(s). Resolve them or publish with force=true.",
                                  "unresolved": [flag_dict(f) for f in unresolved]})
    prev = published_run(d)
    if prev:
        prev.version_status = "superseded"
    run.version_status = "published"
    d.status, d.published_run_id, d.published_at = "published", run.id, now()
    audit(db, user, "document", d.id, "publish", {"run_id": run.id, "version_no": run.version_no,
                                                  "superseded_version": prev.version_no if prev else None,
                                                  "forced_with_unresolved": len(unresolved) if unresolved else 0, "reason": reason,
                                                  "attestation": ATTESTATION, "attested_by": user.full_name,
                                                  "attested_by_user_id": user.id, "attested_at": now().isoformat()})
    db.commit()
    return doc_detail(db, d, run)


EXPORT_DISCLAIMER = ("SOPCheck export - AI-assisted documentation aid. This checklist is NOT a substitute for your company's "
                     "official procedures, qualified supervision, training or regulatory compliance. AI output may omit or "
                     "misstate safety-critical information; verify every task against the source document before use.")


@app.get("/api/documents/{doc_id}/export")
def export_checklist(doc_id: int, format: str = Query("csv", pattern="^(csv|md|json)$"), run_id: Optional[int] = None,
                     user: User = Depends(require_manager), db: Session = Depends(get_db)):
    """Download the checklist; every format carries the disclaimer and the version / review status."""
    import csv, io, json as _json
    d = get_doc(db, user, doc_id)
    run = next((r for r in d.runs if r.id == run_id), None) if run_id else (published_run(d) or editable_base(d))
    if not run:
        raise HTTPException(404, "version not found")
    cl = checklist_of(db, run)
    meta = {"document": d.title, "version": run.version_no, "version_status": run.version_status, "parser": run.parser,
            "model": run.model, "exported_at": now().isoformat(), "exported_by": user.full_name}
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", d.title)[:60] or "checklist"
    audit(db, user, "document", d.id, "export", {"format": format, "run_id": run.id}); db.commit()
    if format == "json":
        body, mt = _json.dumps({"disclaimer": EXPORT_DISCLAIMER, **meta, **cl}, indent=2, default=str), "application/json"
    elif format == "md":
        lines = [f"> **{EXPORT_DISCLAIMER}**", "", f"# {d.title} (v{run.version_no}, {run.version_status})", "", cl["document_summary"], "",
                 "| # | Task | Description | Position | Site | Timeframe | Verification |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {t['task_id']} | {t['task_title']} | {t['description'].replace('|', '/')} | {t['assigned_position']} | "
                  f"{t['site_requirement']} | {t['timeframe']} | {t['verification_method']} |" for t in cl["tasks"]]
        lines += ["", f"_{EXPORT_DISCLAIMER}_"]
        body, mt = "\n".join(lines), "text/markdown"
    else:
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow([f"DISCLAIMER: {EXPORT_DISCLAIMER}"])
        w.writerow([f"{k}: {v}" for k, v in meta.items()])
        w.writerow(["task_id"] + CORE_FIELDS)
        for t in cl["tasks"]:
            w.writerow([t["task_id"]] + [t[f] for f in CORE_FIELDS])
        body, mt = buf.getvalue(), "text/csv"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{safe}_v{run.version_no}.{format}"'})


@app.post("/api/documents/{doc_id}/safety/{flag_id}/resolve")
def resolve_flag(doc_id: int, flag_id: int, body: ResolveFlag, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    d = get_doc(db, user, doc_id)
    f = db.get(SafetyFlag, flag_id)
    base = editable_base(d)
    if not f or f.document_id != d.id or f.parse_run_id != base.id:
        raise HTTPException(404, "flag not found on the current version")
    span_hash, span = f.span_hash, f.span_text
    if body.action == "dismiss":
        if not body.reason or len(body.reason.strip()) < 5:
            raise HTTPException(422, "A dismissal reason (at least 5 characters) is required")
        f.resolution, f.resolution_reason, f.resolved_by, f.resolved_at = "dismissed", body.reason.strip(), user.id, now()
        base.safety_audit = {**(base.safety_audit or {}), "unresolved": max(0, (base.safety_audit or {}).get("unresolved", 1) - (0 if f.coverage == "covered" else 1))}
        audit(db, user, "safety_flag", f.id, "dismiss", {"document_id": d.id, "span": span, "reason": f.resolution_reason})
        db.commit()
        return doc_detail(db, d, base)
    tasks = run_tasks(db, base)
    items = [{"id": t.id, **{k: getattr(t, k) for k in CORE_FIELDS}} for t in tasks]
    if body.action == "attach":
        idx = next((i for i, t in enumerate(tasks) if t.task_code == (body.task_code or f.best_task_code)), None)
        if idx is None:
            raise HTTPException(422, "task_code not found in the current version")
        note = missing_part(span, f'{items[idx]["task_title"]} {items[idx]["description"]}')   # only what the task lacks
        if note:
            items[idx]["description"] = f'{items[idx]["description"]} SAFETY NOTE: {note}'
        target_idx = idx
    else:  # create_task
        anchor = next((i for i, t in enumerate(tasks) if t.task_code == (body.task_code or f.best_task_code)), len(tasks) - 1)
        ref = tasks[anchor] if tasks else None
        signal = re.search(r"\b(WARNING|CAUTION|DANGER)\b", span)     # prompt rule: ⚠ only when the source marks it
        new = {"task_title": (("⚠ " if signal else "") + make_title(span))[:120], "description": span,
               "assigned_position": ref.assigned_position if ref else "Unspecified",
               "site_requirement": ref.site_requirement if ref else "Unspecified",
               "timeframe": ref.timeframe if ref else "Unspecified", "verification_method": choose_verification(span)}
        items.insert(anchor + 1, new)
        target_idx = anchor + 1
    run = apply_edit(db, d, EditChecklist(base_run_id=base.id, tasks=items), user)
    target = run_tasks(db, run)[target_idx]
    nf = db.scalar(select(SafetyFlag).where(SafetyFlag.parse_run_id == run.id, SafetyFlag.span_hash == span_hash))
    if nf:
        nf.resolution = "attached" if body.action == "attach" else "task_created"
        nf.resolution_task_code, nf.resolved_by, nf.resolved_at = target.task_code, user.id, now()
        nf.resolution_reason = body.reason
    audit(db, user, "safety_flag", nf.id if nf else flag_id, body.action,
          {"document_id": d.id, "span": span, "task_code": target.task_code, "version_no": run.version_no})
    db.commit()
    return doc_detail(db, d, run)


@app.get("/api/documents/{doc_id}/audit")
def document_audit(doc_id: int, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    d = get_doc(db, user, doc_id)
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.company_id == user.company_id)).all()}
    rows = db.scalars(select(AuditLog).where(AuditLog.company_id == user.company_id).order_by(AuditLog.id.desc())).all()
    out = [r for r in rows if (r.entity_type == "document" and r.entity_id == d.id) or (r.data or {}).get("document_id") == d.id]
    return [{"id": r.id, "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id, "data": r.data,
             "actor": names.get(r.actor_user_id), "created_at": r.created_at} for r in out[:300]]


# ---------------- distribution (technicians) ----------------
def published_tasks_q(company_id: int):
    return (select(Task).join(Document, Task.document_id == Document.id)
            .where(Task.company_id == company_id, Document.status == "published", Task.parse_run_id == Document.published_run_id))


def _matches(value: str, wanted: Optional[str], include_unspecified: bool) -> bool:
    if not wanted:
        return True
    if include_unspecified and value == "Unspecified":
        return True
    parts = [p.strip().lower() for p in value.split("+")]
    return wanted.strip().lower() in parts or value.strip().lower() == wanted.strip().lower()


@app.get("/api/filters")
def filters(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tasks = db.scalars(published_tasks_q(user.company_id)).all()
    return {
        "positions": sorted({p.strip() for t in tasks for p in t.assigned_position.split("+")} - {"Unspecified"}),
        "sites": sorted({t.site_requirement for t in tasks} - {"Unspecified"}),
        "timeframes": sorted({t.timeframe for t in tasks} - {"Unspecified"}),
        "technicians": [t["display_name"] for t in technicians(user, db)],
    }


@app.get("/api/tasks", response_model=list[TaskOut])
def list_tasks(position: Optional[str] = None, site: Optional[str] = None, timeframe: Optional[str] = None,
               document_id: Optional[int] = None, include_unspecified: bool = True,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Published tasks for the caller's company, filtered by position / site / timeframe.
    'completed' = completed today (company-local day) by the calling technician."""
    company = db.get(Company, user.company_id)
    q = published_tasks_q(user.company_id).order_by(Task.document_id, Task.sequence)
    if document_id:
        q = q.where(Task.document_id == document_id)
    tasks = [t for t in db.scalars(q).all()
             if _matches(t.assigned_position, position, include_unspecified) and _matches(t.site_requirement, site, include_unspecified)
             and _matches(t.timeframe, timeframe, include_unspecified)]
    start = local_day_start_utc(company.timezone)
    sq = select(Submission).where(Submission.company_id == user.company_id, Submission.task_id.in_([t.id for t in tasks] or [-1]),
                                  Submission.submitted_at >= start)
    if user.technician_id:
        sq = sq.where(Submission.technician_id == user.technician_id)
    latest = {s.task_id: s for s in db.scalars(sq.order_by(Submission.id)).all()}
    windows = (company.settings or {}).get("shift_windows", {})
    out = []
    for t in tasks:
        s = latest.get(t.id)
        item = TaskOut.model_validate(t)
        item.completed = bool(s and s.status == "completed")
        item.last_submission = SubmissionOut.model_validate(s).model_dump(mode="json") if s else None
        item.window = window_for(t.timeframe, company.timezone, windows)
        out.append(item)
    return out


@app.post("/api/submissions", response_model=SubmissionOut, status_code=201)
def submit(req: SubmissionCreate, user: User = Depends(require_technician), db: Session = Depends(get_db)):
    task = db.get(Task, req.task_id)
    doc = db.get(Document, task.document_id) if task else None
    if not task or task.company_id != user.company_id or not doc or doc.published_run_id != task.parse_run_id:
        raise HTTPException(404, "task not found")
    vm, errors, cosigner = task.verification_method, [], None
    if vm == "Numeric Input" and req.numeric_value is None:
        errors.append("numeric_value is required for Numeric Input tasks")
    if vm == "Photo Upload" and not req.photo_url:
        errors.append("photo_url is required for Photo Upload tasks")
    if vm == "Photo Upload" and req.photo_url and not req.photo_url.startswith(f"/files/c{user.company_id}_"):
        errors.append("photo_url must be a photo uploaded to this company")
    if vm == "Text Input" and not req.text_value:
        errors.append("text_value is required for Text Input tasks")
    if vm in ("Signature", "Dual Sign-off") and not req.signature:
        errors.append("signature is required for Signature / Dual Sign-off tasks")
    if vm == "Dual Sign-off":
        if not req.cosigner_email or not req.cosigner_password:
            errors.append("cosigner_email and cosigner_password are required for Dual Sign-off tasks")
        else:
            cosigner = db.scalar(select(User).where(User.email == req.cosigner_email.lower()))
            if not cosigner or cosigner.company_id != user.company_id or not cosigner.active \
                    or not verify_password(req.cosigner_password, cosigner.password_hash):
                errors.append("co-signer credentials are invalid")
                cosigner = None
            elif cosigner.id == user.id:
                errors.append("co-signer must be a different person than the technician")
                cosigner = None
    if errors:
        raise HTTPException(422, {"errors": errors, "verification_method": vm})
    tech = db.get(Technician, user.technician_id)
    sub = Submission(company_id=user.company_id, task_id=task.id, technician_id=tech.id, user_id=user.id,
                     technician_name=tech.display_name, status="completed" if req.checked else "failed_check",
                     checked=req.checked, numeric_value=req.numeric_value, text_value=req.text_value, photo_url=req.photo_url,
                     signature=req.signature, cosigner_user_id=cosigner.id if cosigner else None,
                     cosigner_name=cosigner.full_name if cosigner else None, notes=req.notes)
    db.add(sub); db.flush()
    audit(db, user, "submission", sub.id, "submit", {"task_id": task.id, "document_id": doc.id, "verification_method": vm,
                                                     "cosigner_user_id": sub.cosigner_user_id})
    db.commit()
    return SubmissionOut.model_validate(sub)


@app.get("/api/submissions", response_model=list[SubmissionOut])
def list_submissions(task_id: Optional[int] = None, limit: int = Query(100, le=500), user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    q = select(Submission).where(Submission.company_id == user.company_id).order_by(Submission.id.desc()).limit(limit)
    if user.role == "technician":
        q = q.where(Submission.technician_id == user.technician_id)
    if task_id:
        q = q.where(Submission.task_id == task_id)
    return [SubmissionOut.model_validate(s) for s in db.scalars(q).all()]


@app.post("/api/uploads/photo")
async def upload_photo(file: UploadFile = File(...), user: User = Depends(current_user)):
    raw = await file.read()
    if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "photo too large")
    ext = (file.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "heic", "gif"):
        raise HTTPException(415, "unsupported image type")
    name = f"c{user.company_id}_{uuid.uuid4().hex}.{ext}"
    (config.UPLOAD_DIR / name).write_bytes(raw)
    return {"url": f"/files/{name}", "sha256": hashlib.sha256(raw).hexdigest()}


@app.get("/files/{name}")
def get_file(name: str, user: User = Depends(current_user)):
    if "/" in name or ".." in name or not name.startswith(f"c{user.company_id}_"):
        raise HTTPException(404, "file not found")
    path = config.UPLOAD_DIR / name
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path)


# ---------------- account area: data export + deletion ----------------
def _row(o, skip=()) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name not in skip}


USER_SECRET_COLS = ("password_hash",)
COMPANY_SECRET_COLS = ("license_key",)


@app.get("/api/account")
def account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    managers = db.scalar(select(func.count(User.id)).where(User.company_id == c.id, User.role.in_(("manager", "admin")), User.active)) or 0
    return {"user": me_dict(user), "deletion": {"requested_at": c.deletion_requested_at, "scheduled_for": c.deletion_scheduled_for,
                                                "grace_days": config.DELETION_GRACE_DAYS},
            "can_self_delete": user.role == "technician" or managers > 1,
            "retention_note": "Exports include everything stored about you; managers' exports include the whole workspace."}


@app.get("/api/account/export")
def export_my_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Machine-readable copy of the caller's data (JSON). Managers also receive the full company workspace.
    Secrets are never included (password hashes, session tokens, the full Gumroad license key)."""
    c = db.get(Company, user.company_id)
    mine = {
        "profile": _row(user, USER_SECRET_COLS),
        "technician_record": _row(user.technician) if user.technician else None,
        "sessions": [_row(x, ("id",)) for x in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).all()],
        "my_submissions": [_row(x) for x in db.scalars(select(Submission).where(
            (Submission.user_id == user.id) | (Submission.cosigner_user_id == user.id))).all()],
        "my_activity": [_row(x) for x in db.scalars(select(AuditLog).where(AuditLog.company_id == c.id, AuditLog.actor_user_id == user.id)).all()],
    }
    out = {"export_format": "sopcheck-account-export/1", "exported_at": now(), "company": {"id": c.id, "name": c.name}, "you": mine}
    if user.role in ("manager", "admin"):
        q = lambda M: [_row(x) for x in db.scalars(select(M).where(M.company_id == c.id)).all()]   # noqa: E731
        out["workspace"] = {
            "company": _row(c, COMPANY_SECRET_COLS) | {"license_key_last4": c.license_key_last4},
            "users": [_row(x, USER_SECRET_COLS) for x in db.scalars(select(User).where(User.company_id == c.id)).all()],
            "technicians": q(Technician), "documents": q(Document),
            "checklist_versions": [_row(x, ("raw_output",)) for x in db.scalars(select(ParseRun).where(ParseRun.company_id == c.id)).all()],
            "tasks": q(Task), "safety_flags": q(SafetyFlag), "submissions": q(Submission), "audit_log": q(AuditLog),
            "files": sorted(p.name for p in config.UPLOAD_DIR.glob(f"c{c.id}_*")),
        }
    audit(db, user, "user", user.id, "data_export", {"scope": "workspace" if "workspace" in out else "self"}); db.commit()
    import json as _json
    return Response(_json.dumps(out, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="sopcheck-export-{c.slug}-user{user.id}.json"'})


@app.post("/api/account/delete")
def delete_my_account(body: SelfDelete, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Self-service deletion. The login is disabled at once and personal data is erased or anonymized. Work records
    the company must keep (completed checklist items) stay, attributed to 'Deleted user'."""
    if body.confirm != "DELETE":
        raise HTTPException(422, "Type DELETE to confirm.")
    user = db.merge(user)
    if user.role in ("manager", "admin"):
        others = db.scalar(select(func.count(User.id)).where(User.company_id == user.company_id, User.role.in_(("manager", "admin")),
                                                         User.active, User.id != user.id)) or 0
        if not others:
            raise HTTPException(409, "You are the last manager. Delete the whole workspace instead, or make someone else a manager first.")
    label = f"Deleted user #{user.id}"
    for sub in db.scalars(select(Submission).where((Submission.user_id == user.id) | (Submission.cosigner_user_id == user.id))).all():
        if sub.user_id == user.id:
            sub.technician_name, sub.signature = label, ("[signature removed]" if sub.signature else None)
        if sub.cosigner_user_id == user.id:
            sub.cosigner_name = label
    for a in db.scalars(select(AuditLog).where(AuditLog.actor_user_id == user.id)).all():
        if a.data and "ip" in a.data:
            a.data = {k: v for k, v in a.data.items() if k != "ip"}
    if user.technician:
        user.technician.display_name, user.technician.position, user.technician.site = f"Deleted technician #{user.technician.id}", "-", "-"
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))).all():
        sess.revoked_at = now()
    db.execute(AuthSession.__table__.update().where(AuthSession.user_id == user.id).values(user_agent=None))
    user.email = f"deleted-user-{user.id}-{uuid.uuid4().hex[:8]}"      # frees the login; not an address
    user.full_name, user.password_hash, user.active = label, hash_password(uuid.uuid4().hex), False
    user.terms_accept_ip, user.last_login_at, user.deleted_at = None, None, now()
    audit(db, None, "user", user.id, "account_self_delete", {"role": user.role}, company_id=user.company_id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "message": "Your account was deleted and you have been signed out."}


@app.post("/api/company/delete")
def request_company_deletion(body: CompanyDelete, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    if body.confirm_name.strip() != c.name:
        raise HTTPException(422, "The company name you typed does not match. Type it exactly as shown to confirm.")
    if c.deletion_scheduled_for:
        raise HTTPException(409, "Deletion is already scheduled.")
    c.deletion_requested_at, c.deletion_requested_by = now(), user.id
    c.deletion_scheduled_for = now() + timedelta(days=config.DELETION_GRACE_DAYS)
    audit(db, user, "company", c.id, "workspace_delete_requested", {"scheduled_for": c.deletion_scheduled_for.isoformat()})
    db.commit()
    return {"ok": True, "scheduled_for": c.deletion_scheduled_for, "grace_days": config.DELETION_GRACE_DAYS,
            "message": "Workspace locked and scheduled for permanent deletion. Export anything you need before then. A manager can cancel until that time."}


@app.post("/api/company/delete/cancel")
def cancel_company_deletion(user: User = Depends(require_manager), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    if not c.deletion_scheduled_for:
        raise HTTPException(409, "No deletion is scheduled.")
    c.deletion_requested_at = c.deletion_scheduled_for = c.deletion_requested_by = None
    audit(db, user, "company", c.id, "workspace_delete_cancelled")
    db.commit()
    return {"ok": True}


PURGE_ORDER = [Submission, SafetyFlag, Task, ParseRun, Document, AuthSession, AuditLog, User, Technician]


def purge_company(db: Session, c: Company) -> dict:
    """Hard delete: every row of the workspace, then its uploaded files (photos), leaving only a tombstone."""
    counts = {}
    for M in PURGE_ORDER:
        res = db.execute(M.__table__.delete().where(M.__table__.c.company_id == c.id))
        counts[M.__tablename__] = res.rowcount or 0
    files = list(config.UPLOAD_DIR.glob(f"c{c.id}_*"))
    for f in files:
        f.unlink(missing_ok=True)
    counts["files"] = len(files)
    db.add(DeletionTombstone(company_id=c.id, slug_sha256=hashlib.sha256(c.slug.encode()).hexdigest(),
                             requested_at=c.deletion_requested_at, requested_by_user_id=c.deletion_requested_by, counts=counts))
    db.delete(c)
    db.commit()
    return counts


def purge_due_companies() -> int:
    with SessionLocal() as db:
        due = db.scalars(select(Company).where(Company.deletion_scheduled_for.is_not(None),
                                               Company.deletion_scheduled_for <= now())).all()
        for c in due:
            purge_company(db, c)
        # retention: expired session records are removed 90 days after expiry (privacy policy section 4)
        db.execute(AuthSession.__table__.delete().where(AuthSession.expires_at < now() - timedelta(days=90)))
        db.execute(DeletionTombstone.__table__.delete().where(DeletionTombstone.purged_at < now() - timedelta(days=3 * 365)))
        db.commit()
        return len(due)


def _purge_loop(stop: threading.Event):
    """Runs once at startup, then every PURGE_INTERVAL_S. Errors are swallowed and retried next interval."""
    while True:
        try:
            purge_due_companies()
        except Exception as e:   # noqa: BLE001
            print("purge error:", type(e).__name__)
        if stop.wait(config.PURGE_INTERVAL_S):
            return
