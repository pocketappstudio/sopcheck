"""LLM extraction pipeline: prompt loading -> chunking -> per-chunk extraction ->
validate (Pydantic) -> repair loop -> deterministic post-fixes -> merge & dedupe -> renumber.
Falls back to the deterministic mock parser when no OPENAI_API_KEY is configured."""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pydantic import ValidationError

from . import config, usage
from .ingest import chunk_text
from .mock_parser import mock_parse
from .schemas import Checklist, ChunkChecklist, VERIFICATION_METHODS


# ---------------- prompt loading ----------------
@lru_cache
def load_prompts() -> dict[str, str]:
    md = config.PROMPT_FILE.read_text(encoding="utf-8")

    def section(name: str) -> str:
        m = re.search(rf"<!-- {name}_START -->\n(.*?)\n<!-- {name}_END -->", md, re.S)
        if not m:
            raise RuntimeError(f"{name} markers not found in {config.PROMPT_FILE}")
        return m.group(1).strip()

    return {"system": section("SYSTEM_PROMPT"), "user": section("USER_PROMPT"), "repair": section("REPAIR_PROMPT")}


def render(template: str, **kw) -> str:
    # str.format would choke on JSON braces in the template; replace known placeholders only.
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ---------------- deterministic post-fixes ----------------
VM_SYNONYMS = {
    "checkbox": "Checkbox", "check box": "Checkbox", "check": "Checkbox", "visual": "Checkbox", "yes/no": "Checkbox",
    "photo": "Photo Upload", "photo upload": "Photo Upload", "photograph": "Photo Upload", "image": "Photo Upload", "picture": "Photo Upload",
    "numeric": "Numeric Input", "numeric input": "Numeric Input", "number": "Numeric Input", "reading": "Numeric Input", "measurement": "Numeric Input",
    "text": "Text Input", "text input": "Text Input", "free text": "Text Input", "notes": "Text Input",
    "signature": "Signature", "sign": "Signature", "sign-off": "Signature", "initials": "Signature",
    "dual sign-off": "Dual Sign-off", "dual signoff": "Dual Sign-off", "dual signature": "Dual Sign-off",
    "two-person verification": "Dual Sign-off", "double check": "Dual Sign-off", "independent double-check": "Dual Sign-off", "co-sign": "Dual Sign-off",
}
CORE_TASK_KEYS = ["task_id", "task_title", "description", "assigned_position", "site_requirement", "timeframe", "verification_method"]


def strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    return text


def post_fix(data: dict) -> dict:
    """Safe, non-semantic repairs applied before validation."""
    if not isinstance(data, dict):
        return data
    data = {"industry": data.get("industry"), "document_summary": data.get("document_summary"),
            "tasks": data.get("tasks", data.get("checklist"))}           # extra top-level keys dropped
    tasks = data.get("tasks") or []
    fixed = []
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict):
            continue
        t = {k: t.get(k) for k in CORE_TASK_KEYS}                     # drop extra keys
        for k in CORE_TASK_KEYS:
            v = t.get(k)
            v = "" if v is None else (", ".join(map(str, v)) if isinstance(v, list) else str(v)).strip()
            if k in ("assigned_position", "site_requirement", "timeframe") and v.lower() in ("", "n/a", "na", "none", "unknown", "not specified"):
                v = "Unspecified"
            t[k] = v
        vm = t["verification_method"]
        if vm not in VERIFICATION_METHODS:
            t["verification_method"] = VM_SYNONYMS.get(vm.lower(), "Checkbox" if not vm else vm)
        if len(t["task_title"]) > 120:
            t["task_title"] = t["task_title"][:117].rstrip() + "..."
        fixed.append(t)
    data["tasks"] = fixed
    for k in ("industry", "document_summary"):
        if isinstance(data.get(k), str):
            data[k] = data[k].strip()
    return data


def renumber(tasks: list[dict]) -> list[dict]:
    for i, t in enumerate(tasks, 1):
        t["task_id"] = f"T-{i:03d}"
    return tasks


def validate(raw_text: str, model: type[Checklist] = Checklist) -> tuple[Checklist | None, list[str], dict | None]:
    try:
        data = json.loads(strip_fences(raw_text))
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"], None
    data = post_fix(data)
    try:
        return model.model_validate(data), [], data
    except ValidationError as e:
        errs = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        return None, errs, data


# ---------------- merge & dedupe ----------------
STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "on", "in", "with", "your", "all", "any"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP}


def merge_chunks(results: list[Checklist], boundaries: list[int] | None = None) -> dict:
    merged: list[dict] = []
    for ci, res in enumerate(results):
        incoming = [t.model_dump() for t in res.tasks]
        if merged and incoming:
            # only compare the first 2 incoming tasks with the last 2 merged ones (overlap zone)
            for t in incoming[:2]:
                for m in merged[-2:]:
                    a, b = _tokens(t["description"]), _tokens(m["description"])
                    jacc = len(a & b) / max(1, len(a | b))
                    contain = len(a & b) / max(1, min(len(a), len(b)))   # overlap re-extractions are often a subset
                    if _tokens(t["task_title"]) == _tokens(m["task_title"]) or jacc >= 0.85 or (contain >= 0.9 and min(len(a), len(b)) >= 8):
                        if len(t["description"]) > len(m["description"]):
                            m.update(description=t["description"])
                        t["_dup"] = True
                        break
        merged.extend(t for t in incoming if not t.pop("_dup", False))
    industries = [r.industry for r in results]
    industry = max(set(industries), key=industries.count)
    return {"industry": industry, "document_summary": results[0].document_summary, "tasks": renumber(merged)}


# ---------------- main entry ----------------
class NoTasksFound(ValueError):
    """The document has text but no actionable step could be extracted (-> HTTP 422, not 502)."""


@dataclass
class ParseResult:
    checklist: dict
    parser: str
    model: str | None
    status: str = "succeeded"
    chunk_count: int = 1
    repair_attempts: int = 0
    raw_output: str | None = None
    validation_errors: list = field(default_factory=list)
    notes: str | None = None
    latency_ms: int = 0
    usage: dict | None = None


def _client():
    from openai import OpenAI
    return OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL, timeout=config.LLM_TIMEOUT_S)


def _complete(client, messages: list[dict]) -> str:
    kwargs = dict(model=config.LLM_MODEL, messages=messages)
    if not config.is_reasoning_model(config.LLM_MODEL):        # o-series / gpt-5 reject temperature != default
        kwargs["temperature"] = 0
    try:
        resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception as e:  # some OpenAI-compatible servers reject response_format
        if "response_format" not in str(e):
            raise
        resp = client.chat.completions.create(**kwargs)
    usage.record(config.LLM_MODEL, getattr(resp, "usage", None))
    return resp.choices[0].message.content or ""


def llm_parse(text: str, title: str, company: str) -> ParseResult:
    prompts = load_prompts()
    client = _client()
    chunks = chunk_text(text, config.CHUNK_MAX_CHARS)
    results: list[Checklist] = []
    raw_all, errors_all, repairs, context = [], [], 0, "None"
    next_id = 1
    for ci, chunk in enumerate(chunks, 1):
        messages = [
            {"role": "system", "content": prompts["system"]},
            {"role": "user", "content": render(prompts["user"], company_name=company, document_title=title,
                                               chunk_index=ci, chunk_total=len(chunks), section_context=context,
                                               task_id_start=f"{next_id:03d}", document_text=chunk)},
        ]
        raw = _complete(client, messages)
        raw_all.append(raw)
        parsed, errs, _ = validate(raw, ChunkChecklist)
        attempt = 0
        while parsed is None and attempt < config.LLM_MAX_REPAIRS:
            attempt += 1; repairs += 1
            errors_all.append({"chunk": ci, "attempt": attempt, "errors": errs})
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": render(prompts["repair"], validation_errors="\n".join(f"- {e}" for e in errs[:30]))}]
            raw = _complete(client, messages)
            raw_all.append(raw)
            parsed, errs, _ = validate(raw, ChunkChecklist)
        if parsed is None:
            errors_all.append({"chunk": ci, "attempt": attempt + 1, "errors": errs})
            raise RuntimeError(f"LLM output for chunk {ci} failed validation after {attempt} repair(s): {errs[:5]}")
        results.append(parsed)
        if not parsed.tasks:
            continue
        next_id += len(parsed.tasks)
        last = parsed.tasks[-1]
        context = (f"last heading context -> position: {last.assigned_position}; site: {last.site_requirement}; "
                   f"timeframe: {last.timeframe}")
    with_tasks = [r for r in results if r.tasks]
    if not with_tasks:
        raise NoTasksFound("LLM found no actionable tasks in the document")
    if len(with_tasks) != len(results):  # keep the best summary/industry even if chunk 1 was preamble-only
        with_tasks[0] = with_tasks[0].model_copy(update={"document_summary": results[0].document_summary or with_tasks[0].document_summary})
    results = with_tasks
    checklist = merge_chunks(results) if len(results) > 1 else {**results[0].model_dump(), "tasks": renumber([t.model_dump() for t in results[0].tasks])}
    Checklist.model_validate(checklist)  # final guarantee
    return ParseResult(checklist=checklist, parser="llm", model=config.LLM_MODEL, chunk_count=len(chunks),
                       status="repaired" if repairs else "succeeded", repair_attempts=repairs,
                       raw_output="\n\n----- next response -----\n\n".join(raw_all), validation_errors=errors_all)


def parse_document(text: str, title: str, company: str = "Demo Company") -> ParseResult:
    t0 = time.perf_counter()
    meter = usage.start()
    if config.parser_mode() == "llm":
        try:
            res = llm_parse(text, title, company)
        except Exception as e:
            if not config.LLM_FALLBACK_TO_MOCK:
                raise
            res = ParseResult(checklist=mock_parse(text, title), parser="mock", model=None,
                              notes=f"LLM extraction failed, fell back to mock parser: {type(e).__name__}: {str(e)[:300]}")
    else:
        res = ParseResult(checklist=mock_parse(text, title), parser="mock", model=None,
                          notes="OPENAI_API_KEY not set - deterministic mock parser used.")
    if not res.checklist.get("tasks"):
        raise NoTasksFound("no actionable steps or instructions were found in the document text")
    Checklist.model_validate(res.checklist)
    res.latency_ms = int((time.perf_counter() - t0) * 1000)
    res.usage = usage.summary(meter)
    return res
