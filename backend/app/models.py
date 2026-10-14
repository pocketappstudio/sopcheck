"""Prototype ORM models (SQLite/Postgres portable subset of db/schema.sql).
All timestamps are stored in UTC and always returned timezone-aware (see UTCDateTime)."""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (String, Text, Integer, ForeignKey, Boolean, Float, DateTime, JSON, Index,
                        UniqueConstraint, TypeDecorator)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store UTC; return aware UTC datetimes. SQLite drops tzinfo, which previously made the API emit
    naive timestamps that browsers parsed as *local* time (the '05:43 PM' bug)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


DEFAULT_SHIFT_WINDOWS = {
    "Opening": ["06:00", "07:00"],
    "Shift Start": ["07:00", "09:00"],
    "Pre-Shift": ["06:30", "07:00"],
    "Shift End": ["17:00", "19:00"],
    "Closing": ["21:00", "22:00"],
}


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")   # IANA zone for shift windows
    settings: Mapped[dict] = mapped_column(JSON, default=lambda: {"shift_windows": DEFAULT_SHIFT_WINDOWS})
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    # billing: plan = trial | starter | team | expired. The Gumroad license key is kept server-side (needed for
    # periodic re-verification of the membership) and only its last 4 characters are ever returned by the API.
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    trial_started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    license_key: Mapped[Optional[str]] = mapped_column(String(100))
    license_key_last4: Mapped[Optional[str]] = mapped_column(String(4))
    license_verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    license_status: Mapped[Optional[str]] = mapped_column(String(200))
    # workspace deletion: requested -> read-only/locked grace period -> hard delete (see main.purge_due_companies)
    deletion_requested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    deletion_scheduled_for: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    deletion_requested_by: Mapped[Optional[int]] = mapped_column(Integer)


class Technician(Base):
    __tablename__ = "technicians"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    position: Mapped[Optional[str]] = mapped_column(String(120))
    site: Mapped[Optional[str]] = mapped_column(String(120))
    __table_args__ = (UniqueConstraint("company_id", "display_name"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))          # bcrypt
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))                    # admin | manager | technician
    technician_id: Mapped[Optional[int]] = mapped_column(ForeignKey("technicians.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    terms_version: Mapped[Optional[str]] = mapped_column(String(40))        # accepted Terms/Privacy version
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    terms_accept_ip: Mapped[Optional[str]] = mapped_column(String(64))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)      # self-deleted: row anonymized, login disabled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    technician: Mapped[Optional[Technician]] = relationship()
    company: Mapped[Company] = relationship()


class AuthSession(Base):
    """Server-side record for each issued JWT (jti) so logout / revocation works."""
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)    # JWT jti
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[int] = mapped_column(Integer)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source_filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    extraction_method: Mapped[str] = mapped_column(String(20), default="text")   # text | pdf_text | ocr | mixed
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Float)                # mean word confidence 0-100
    ocr_pages: Mapped[Optional[list]] = mapped_column(JSON)                       # [{page, confidence, words}]
    status: Mapped[str] = mapped_column(String(20), default="draft")             # draft | published | archived
    published_run_id: Mapped[Optional[int]] = mapped_column(Integer)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)
    runs: Mapped[list["ParseRun"]] = relationship(back_populates="document", order_by="ParseRun.id")


class ParseRun(Base):
    """One checklist *version* of a document. version_status: draft | published | superseded.
    source: llm | mock (extraction) or manual_edit (manager-created version)."""
    __tablename__ = "parse_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    version_status: Mapped[str] = mapped_column(String(20), default="draft")
    parent_run_id: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))           # extraction status: succeeded | repaired | edited
    parser: Mapped[str] = mapped_column(String(20))           # llm | mock | manual_edit
    model: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    chunk_count: Mapped[int] = mapped_column(Integer, default=1)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    document_summary: Mapped[Optional[str]] = mapped_column(Text)
    output_json: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_output: Mapped[Optional[str]] = mapped_column(Text)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSON)
    safety_audit: Mapped[Optional[dict]] = mapped_column(JSON)   # summary of safety_flags
    notes: Mapped[Optional[str]] = mapped_column(Text)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    usage: Mapped[Optional[dict]] = mapped_column(JSON)          # tokens + estimated $ for the LLM extraction + audit
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)
    document: Mapped[Document] = relationship(back_populates="runs")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    parse_run_id: Mapped[int] = mapped_column(ForeignKey("parse_runs.id"))
    task_code: Mapped[str] = mapped_column(String(20))
    sequence: Mapped[int] = mapped_column(Integer)
    task_title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    assigned_position: Mapped[str] = mapped_column(String(200))
    site_requirement: Mapped[str] = mapped_column(String(200))
    timeframe: Mapped[str] = mapped_column(String(200))
    verification_method: Mapped[str] = mapped_column(String(30))
    is_safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    origin_task_id: Mapped[Optional[int]] = mapped_column(Integer)   # task this row was copied from (versioning)
    edited_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    __table_args__ = (
        UniqueConstraint("parse_run_id", "task_code"),
        Index("ix_tasks_filter", "company_id", "assigned_position", "site_requirement", "timeframe"),
        Index("ix_tasks_run_seq", "parse_run_id", "sequence"),
    )


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey("technicians.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    technician_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="completed")
    checked: Mapped[Optional[bool]] = mapped_column(Boolean)
    numeric_value: Mapped[Optional[float]] = mapped_column(Float)
    text_value: Mapped[Optional[str]] = mapped_column(Text)
    photo_url: Mapped[Optional[str]] = mapped_column(String(500))
    signature: Mapped[Optional[str]] = mapped_column(Text)
    cosigner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    cosigner_name: Mapped[Optional[str]] = mapped_column(String(120))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class SafetyFlag(Base):
    """A safety-critical source span and how well a checklist version covers it."""
    __tablename__ = "safety_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    parse_run_id: Mapped[int] = mapped_column(ForeignKey("parse_runs.id"), index=True)
    span_hash: Mapped[str] = mapped_column(String(64))
    span_text: Mapped[str] = mapped_column(Text)
    section: Mapped[Optional[str]] = mapped_column(String(200))
    categories: Mapped[list] = mapped_column(JSON)
    atoms: Mapped[list] = mapped_column(JSON)
    coverage: Mapped[str] = mapped_column(String(10))          # covered | partial | missing
    score: Mapped[float] = mapped_column(Float)
    best_task_code: Mapped[Optional[str]] = mapped_column(String(20))
    matched_atoms: Mapped[list] = mapped_column(JSON)
    missing_atoms: Mapped[list] = mapped_column(JSON)
    detector: Mapped[str] = mapped_column(String(20), default="rules")   # rules | llm | rules+llm
    resolution: Mapped[Optional[str]] = mapped_column(String(20))       # attached | task_created | dismissed
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text)
    resolution_task_code: Mapped[Optional[str]] = mapped_column(String(20))
    resolved_by: Mapped[Optional[int]] = mapped_column(Integer)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class DeletionTombstone(Base):
    """Minimal, audit-safe record that a workspace existed and was purged on request. Holds no names, logins,
    document text or files: only ids, a one-way hash of the company slug, timestamps and row counts."""
    __tablename__ = "deletion_tombstones"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True)
    slug_sha256: Mapped[str] = mapped_column(String(64))
    requested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    requested_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    purged_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    counts: Mapped[Optional[dict]] = mapped_column(JSON)
