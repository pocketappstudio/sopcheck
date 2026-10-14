"""Pydantic models. `Checklist`/`TaskItem` are the strict LLM output contract
(mirrors prompts/checklist.schema.json): extra keys forbidden, enum on verification_method."""
from __future__ import annotations
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VerificationMethod = Literal["Checkbox", "Photo Upload", "Numeric Input", "Text Input", "Signature", "Dual Sign-off"]
VERIFICATION_METHODS = ["Checkbox", "Photo Upload", "Numeric Input", "Text Input", "Signature", "Dual Sign-off"]


class TaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str = Field(pattern=r"^T-\d{3,}$")
    task_title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    assigned_position: str = Field(min_length=1)
    site_requirement: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    verification_method: VerificationMethod


class Checklist(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    industry: str = Field(min_length=1)
    document_summary: str = Field(min_length=1)
    tasks: list[TaskItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [t.task_id for t in self.tasks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate task_id values: {dupes}")
        return self


class ChunkChecklist(Checklist):
    """Per-chunk result: a chunk may legitimately contain no actionable steps (e.g. only PURPOSE/SCOPE)."""
    tasks: list[TaskItem] = Field(default_factory=list)


# ---------- API payloads ----------
class ParseTextRequest(BaseModel):
    text: str = Field(min_length=20, max_length=500_000)
    title: str = "Pasted document"


class SubmissionCreate(BaseModel):
    """Technician identity comes from the session; Dual Sign-off requires the co-signer's own credentials."""
    task_id: int
    checked: bool = True
    numeric_value: Optional[float] = None
    text_value: Optional[str] = Field(None, max_length=2000)
    photo_url: Optional[str] = Field(None, max_length=500)
    signature: Optional[str] = Field(None, max_length=200)
    cosigner_email: Optional[str] = None
    cosigner_password: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("text_value", "signature", "notes", "cosigner_email")
    @classmethod
    def strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class EditTask(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: Optional[int] = None                      # DB id of an existing task (omit for new tasks)
    task_title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    assigned_position: str = Field(min_length=1, max_length=200)
    site_requirement: str = Field(min_length=1, max_length=200)
    timeframe: str = Field(min_length=1, max_length=200)
    verification_method: VerificationMethod


class EditChecklist(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    base_run_id: int                              # version the editor started from (optimistic locking)
    industry: Optional[str] = Field(None, min_length=1, max_length=100)
    document_summary: Optional[str] = Field(None, min_length=1, max_length=4000)
    tasks: list[EditTask] = Field(min_length=1, max_length=500)


class ResolveFlag(BaseModel):
    action: Literal["attach", "create_task", "dismiss"]
    task_code: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=1000)


class CompanySettings(BaseModel):
    timezone: Optional[str] = None
    shift_windows: Optional[dict[str, list[str]]] = None

    @field_validator("shift_windows")
    @classmethod
    def check_windows(cls, v):
        import re as _re
        if v is None:
            return v
        for k, w in v.items():
            if len(w) != 2 or not all(_re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", x) for x in w):
                raise ValueError(f"window for '{k}' must be ['HH:MM','HH:MM']")
        return v


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    document_id: int
    parse_run_id: int
    task_code: str
    sequence: int
    task_title: str
    description: str
    assigned_position: str
    site_requirement: str
    timeframe: str
    verification_method: str
    is_safety_critical: bool
    completed: bool = False
    last_submission: Optional[dict] = None
    window: Optional[dict] = None          # today's shift window in the company timezone (UTC ISO bounds)


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    technician_id: int
    technician_name: str
    status: str
    checked: Optional[bool]
    numeric_value: Optional[float]
    text_value: Optional[str]
    photo_url: Optional[str]
    signature: Optional[str]
    cosigner_name: Optional[str]
    notes: Optional[str]
    submitted_at: datetime          # always timezone-aware UTC (ISO 8601 with +00:00)


class AcceptTerms(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    accept: bool


class LicenseKey(BaseModel):
    license_key: str = Field(min_length=8, max_length=100)


class TechnicianCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)        # login identifier
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    position: Optional[str] = Field(None, max_length=120)
    site: Optional[str] = Field(None, max_length=120)


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)          # login identifier
    password: str = Field(min_length=10, max_length=200)
    timezone: str = "America/Chicago"
    accept_terms: bool
    terms_version: str


class SelfDelete(BaseModel):
    confirm: str


class CompanyDelete(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=200)
