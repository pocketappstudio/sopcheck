"""Deterministic, offline heuristic parser used when OPENAI_API_KEY is not set.

It mirrors the rules in prompts/system_prompt.md closely enough to demo the pipeline:
- numbered steps -> tasks; compound steps split on imperative verbs
- section headings set role / site / timeframe context (inherited, with 'Unspecified' fallback)
- WARNING/CAUTION/DO NOT lines are attached verbatim to the governing task
- verification_method chosen by the same first-match rule list as the prompt
It is NOT a substitute for the LLM on messy real-world documents.
"""
from __future__ import annotations
import re

from .safety import is_safety_sentence

ROLE_WORDS = r"(?:Electrician|Officer|Nurse|Nurses|RN|LPN|CNA|Manager|Associate|Technician|Tech|Supervisor|Lead|Operator|Mechanic|" \
             r"Engineer|Inspector|Pharmacist|Physician|Worker|Employee|Cashier|Clerk|Driver|Foreman|Attendant|Staff|Crew|" \
             r"Analyst|Chef|Cook|Server|Pilot|Controller|Specialist|Coordinator|Custodian|Janitor|Guard|Plumber|Welder)"
ROLE_PREFIX_RE = re.compile(rf"^((?:[A-Z][\w&/\-]*\s){{0,4}}{ROLE_WORDS}(?:\s(?:on Duty|in Charge))?(?:\s(?:and|&)\s(?:[A-Z][\w&/\-]*\s){{0,3}}{ROLE_WORDS})?)\s*:\s*(.+)$")
ROLE_MENTION_RE = re.compile(rf"\b((?:[A-Z][a-z]+\s){{0,3}}{ROLE_WORDS})\b")
STEP_RE = re.compile(r"^(?:step\s+)?(\d+(?:\.\d+)*)[.)]?\s+(.+)$", re.IGNORECASE)
SIGNAL_RE = re.compile(r"^(WARNING|CAUTION|DANGER|NOTE|IMPORTANT|DO NOT|NEVER)\b", re.IGNORECASE)
NON_ACTION_SECTIONS = re.compile(r"\b(PURPOSE|SCOPE|RESPONSIBILIT|DEFINITION|REFERENCE|REVISION|BACKGROUND|OVERVIEW|POLICY STATEMENT)", re.IGNORECASE)

SITE_WORDS = re.compile(r"\b(Room|Bedside|Floor|Office|Station|Section|Entrance|Bay|Area|Unit|ICU|Yard|Kitchen|Lab|Laboratory|Dock|"
                        r"Hangar|Warehouse|Site|Ward|Theater|Theatre|Suite|Counter|Register|Cooler|Freezer|Roof|Basement|Shop|Plant|Zone)\b", re.IGNORECASE)
TIME_PATTERNS = [
    (re.compile(r"\bpre[- ]?task\b", re.I), "Pre-Task"),
    (re.compile(r"\bpost[- ]?task\b", re.I), "Post-Task"),
    (re.compile(r"\bpre[- ]?shift\b", re.I), "Pre-Shift"),
    (re.compile(r"\bshift start\b|\bstart of shift\b", re.I), "Shift Start"),
    (re.compile(r"\bshift end\b|\bend of shift\b", re.I), "Shift End"),
    (re.compile(r"\bpre[- ]?operation\b", re.I), "Pre-Operation"),
    (re.compile(r"\bpost[- ]?operation\b", re.I), "Post-Operation"),
    (re.compile(r"\bduring shift\b", re.I), "During Shift"),
    (re.compile(r"\bduring task\b", re.I), "During Task"),
    (re.compile(r"\bhourly\b", re.I), "Hourly"),
    (re.compile(r"\bdaily\b", re.I), "Daily"),
    (re.compile(r"\bweekly\b", re.I), "Weekly"),
    (re.compile(r"\bmonthly\b", re.I), "Monthly"),
    (re.compile(r"\bclosing\b", re.I), "Closing"),
    (re.compile(r"\bopening\b", re.I), "Opening"),
]
PHRASE_TIME_RE = re.compile(r"\b(every \d+\s*(?:hours?|days?|minutes?|weeks?)(?:\s+or\s+when\s+\w+)?|(?:complete\s+)?(?:before|by|within)\s+\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)", re.I)

SPLIT_VERBS = r"(?:apply|record|log|sign|photograph|take|notify|confirm|verify|label|turn on|turn off|re-energize|document|lock|unlock|remove|install|attach|report|enter|clean|close|open)"
TOP_HEADING_RE = re.compile(r"^\d+\.\s+[A-Z][A-Z0-9&/\-–—,]+(?: [A-Z0-9&/\-–—,]+)*(?:\s*\([^)]*\))?(?:\s+[-–—]\s+[^.]+)?$")
VERB_START_RE = re.compile(rf"^{SPLIT_VERBS}\b", re.I)

INDUSTRY_KEYWORDS = {
    "Healthcare": ["patient", "nurse", "rn", "clinical", "catheter", "sterile", "physician", "hospital", "icu", "medication"],
    "Construction": ["lockout", "tagout", "electrician", "osha", "scaffold", "breaker", "panel", "site safety", "nfpa"],
    "Retail": ["store", "cash", "drawer", "planogram", "sales floor", "endcap", "customer", "register"],
    "Aviation Maintenance": ["aircraft", "faa", "hangar", "torque", "a&p", "airworthiness"],
    "Food Service": ["haccp", "food", "kitchen", "sanitize", "allergen"],
    "Manufacturing": ["machine", "production line", "conveyor", "iso 9001"],
    "Logistics / Warehousing": ["forklift", "warehouse", "pallet", "truck", "loading dock", "powered industrial truck"],
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def detect_industry(text: str) -> str:
    low = text.lower()
    scores = {k: sum(len(re.findall(rf"\b{re.escape(w)}\b", low)) for w in ws) for k, ws in INDUSTRY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "General"


def choose_verification(desc: str) -> str:
    d = desc.lower()
    if re.search(r"independent(ly)?\s+(double-?check|verif)|double-?check|co-?sign|witness|two[- ]person|second (person|nurse|signature)", d):
        return "Dual Sign-off"
    if re.search(r"\b(measure|read and record|record (?:the )?[\w\s']{0,30}?(reading|temperature|rate|voltage|pressure|count|level|weight|percentage|charge)|count the|torque|reading)\b", d) \
            or re.search(r"record\b.*\b(mL/hr|psi|volts?|°|degrees)", d):
        return "Numeric Input"
    if re.search(r"\bsign (?:the|off|and|here|your)\b|\bsign-off\b|\bsignature\b|\battest|\bco-?sign", d):
        return "Signature"
    if re.search(r"\bphoto(graph)?\b|\btake a picture\b", d):
        return "Photo Upload"
    if re.search(r"\blot number\b|\bserial\b|\bdocument (the )?findings\b|\blog the\b|\benter\b|\bnotes?\b|\bdescribe\b", d):
        return "Text Input"
    return "Checkbox"


TRAILING_STOP = {"for","the","a","an","to","of","and","or","with","this","that","in","on","at","by","from","into","your","any","each","all"}


def make_title(desc: str) -> str:
    s = re.split(r"[.;:,]|\s(?:using|with|for at least|so that|to ensure|per|if|until)\s", desc, maxsplit=1)[0]
    words = s.split()
    words = words[:8]
    while len(words) > 2 and words[-1].lower().strip(".,;:") in TRAILING_STOP:
        words.pop()
    title = " ".join(words).rstrip(".,;:")
    return _cap(title) or "Complete step"


def parse_heading(line: str, ctx: dict) -> None:
    """Update role/site/timeframe context from a section heading."""
    body = re.sub(r"^#+\s*|^\d+\.\s+", "", line).strip()
    parts = [p.strip() for p in re.split(r"\s[—–-]\s|\(|\)", body) if p and p.strip()]
    new_site = new_time = None
    for p in parts:
        m = PHRASE_TIME_RE.search(p)
        if m or any(rx.search(p) for rx, _ in TIME_PATTERNS):
            if m and len(parts) > 1 and (p.lower().startswith(("every", "complete", "before", "by", "within")) or "," in p or "then" in p.lower()):
                tf = re.sub(r"^complete\s+", "", p, flags=re.I)
                new_time = _cap(tf)
            elif m:
                new_time = _cap(re.sub(r"^complete\s+", "", m.group(0), flags=re.I))
            else:
                if "," in p or " then " in p.lower():
                    new_time = _cap(p)
                else:
                    new_time = next(lbl for rx, lbl in TIME_PATTERNS if rx.search(p))
            continue
        rm = ROLE_MENTION_RE.search(p)
        if rm and re.search(r"responsibilit|duties", p, re.I):
            ctx["role"] = rm.group(1).strip(); ctx["section_role"] = ctx["role"]
            continue
        if SITE_WORDS.search(p) and len(p) < 60:
            new_site = _cap(p)
    if new_site:
        ctx["site"] = new_site
    if new_time:
        ctx["time"] = new_time
    # a new section resets step-level roles back to the document default
    ctx["role"] = ctx.get("section_role") or ctx.get("default_role") or "Unspecified"


def is_heading(line: str) -> bool:
    if line.startswith(("•", "*", "–")) or line.startswith("- "):
        return False                                    # bullet items are content
    if STEP_RE.match(line) and not TOP_HEADING_RE.match(line):
        return False
    if SIGNAL_RE.match(line):
        return False
    if line.startswith("#"):
        return True
    if TOP_HEADING_RE.match(line):
        return True
    if len(line) <= 90 and not line.endswith((".", ";")) and not re.search(r"[A-Za-z)]:\s", line):
        return True
    return False


def split_compound(text: str) -> list[str]:
    """Split a step into atomic actions. Constraint/conditional sentences stay attached."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    pieces: list[str] = []
    for s in sentences:
        if pieces and not VERB_START_RE.match(s):
            pieces[-1] += " " + s           # "If ...", "It must ...", "CAUTION: ..." stay with prior action
        else:
            pieces.append(s)
    out: list[str] = []
    for p in pieces:
        # split "X and <verb> Y" where X has at least 3 words
        m = re.search(rf"^(.{{12,}}?)(?:,)?\s+and\s+({SPLIT_VERBS}\b.+)$", p, re.I)
        if m and len(m.group(1).split()) >= 3 and not re.search(r"\b(if|when|until)\b", m.group(1), re.I):
            first = m.group(1).rstrip(",") + "."
            out.append(first); out.append(_cap(m.group(2)))
        else:
            out.append(p)
    return out


def _toks(s: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", s.lower())} - {"with", "that", "this", "from", "your", "must", "shall", "before", "after"}


def attach_preamble_notes(tasks: list[dict], notes: list[str], max_extra: int = 2) -> None:
    """Attach each document-level safety note to the first task plus up to `max_extra` tasks that
    share >= 2 stemmed keywords with it (mirrors the prompt rule for Purpose/Scope hazards)."""
    if not tasks:
        return
    for note in notes:
        nt = _toks(note)
        ranked = sorted(range(len(tasks)), key=lambda i: -len(nt & _toks(tasks[i]["description"])))
        targets = [0] + [i for i in ranked if i != 0 and len(nt & _toks(tasks[i]["description"])) >= 2][:max_extra]
        for i in sorted(set(targets)):
            if note not in tasks[i]["description"]:
                tasks[i]["description"] += f" SAFETY CONTEXT: {note}"


ACTION_RE = re.compile(
    r"\b(must|shall|should|needs? to|has to|have to|is required|are required|never|do not|don't|nobody|no one|stay|wear|"
    r"check|inspect|verify|confirm|record|log|sign|photograph|replace|swap|flush|clean|close|open|lock|tag|shut|"
    r"remove|install|test|measure|notify|call|report|enter|clear|review|walk|pick up|picks up|signs? in|apply|"
    r"turn (?:on|off)|restore|write it up|gets? (?:a|swapped|checked|cleaned|flushed|logged|replaced)|"
    r"are checked|is checked|are swapped|are flushed|is flushed|are replaced|is replaced)\b", re.I)
TABLE_SEP = re.compile(r"\s*\|\s*|\t+|\s{3,}")


def prose_tasks(lines: list[str], ctx: dict) -> list[dict]:
    """Sentence-level fallback for documents without numbered steps: every sentence that states an
    action, obligation or safety rule becomes one task; table rows (a | b | c) become one task each."""
    out: list[dict] = []
    header: list[str] | None = None
    for line in lines:
        cells = [c for c in TABLE_SEP.split(line) if c]
        if len(cells) >= 3 and "|" in line or (len(cells) >= 3 and re.search(r"\s{3,}", line) and not line.endswith(".")):
            if header is None:
                header = cells                      # first row of a table is its header
                continue
            desc = "; ".join(f"{h}: {c}" for h, c in zip(header, cells)) if len(header) == len(cells) else " ".join(cells)
            out.append(_prose_task(f"Record {cells[0]}. {desc}.", ctx))
            continue
        header = None
        if is_heading(line) and not ACTION_RE.search(line):
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\"(])", line):
            sent = _clean(sent).rstrip()
            if len(sent) < 12 or not (ACTION_RE.search(sent) or is_safety_sentence(sent)):
                continue
            out.append(_prose_task(sent, ctx))
    return out


def _prose_task(desc: str, ctx: dict) -> dict:
    rm = ROLE_MENTION_RE.search(desc)
    role = re.sub(r"^(The|All|Any)\s+", "", rm.group(1)) if rm else (ctx.get("default_role") or "Unspecified")
    tf = next((lbl for rx, lbl in TIME_PATTERNS if rx.search(desc)), "Unspecified")
    pm = re.search(r"\b(?:every \d+\s*(?:hours?|days?|weeks?)|within \d+ (?:business )?(?:days|hours|minutes)|before end of shift)\b", desc, re.I)
    site = next((m.group(0) for m in [re.search(r"\b(?:on|at|in) the ((?:[a-z]+ ){0,2}(?:roof|room|kitchen|dock|floor|yard|bay|site))\b", desc, re.I)] if m), None)
    title = make_title(re.sub(r"^(so|then|once [^,]+,|first,?)\s+", "", desc, flags=re.I))
    if len(title.split()) < 3:                  # "Wrap up", "Anything", "At each unit" -> use the clause after it
        rest = re.split(r"[:,]\s+|\s--\s", desc, maxsplit=1)
        title = make_title(rest[1]) if len(rest) > 1 else title
    return {"task_title": title,
            "description": _cap(desc), "assigned_position": role,
            "site_requirement": _cap(site.split(" ", 2)[-1]) if site else (ctx.get("site") or "Unspecified"),
            "timeframe": _cap(pm.group(0)) if pm else tf, "verification_method": choose_verification(desc)}


def mock_parse(text: str, title: str = "") -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    roles_seen = []
    for l in lines[:40]:
        if re.search(r"responsib|scope|applies to|performs", l, re.I):
            for m in ROLE_MENTION_RE.finditer(l):
                abbr = re.match(r"\s*\(([A-Z&]{2,6})\)", l[m.end():])
                roles_seen.append(abbr.group(1) if abbr else m.group(1).strip())
    roles_seen = [re.sub(r"^(The|All)\s+", "", r) for r in roles_seen]
    ctx = {"role": "Unspecified", "site": "Unspecified", "time": "Unspecified",
           "default_role": roles_seen[0] if roles_seen else None, "section_role": None}
    ctx["role"] = ctx["default_role"] or "Unspecified"

    tasks: list[dict] = []
    pending_safety: list[str] = []
    preamble_notes: list[str] = []          # safety-critical sentences from Purpose/Scope/etc.
    skip_section = False
    section_line = ""
    doc_title = title or (lines[0] if lines else "Document")

    for idx, line in enumerate(lines):
        if idx == 0 and not STEP_RE.match(line):
            continue  # title line
        if SIGNAL_RE.match(line):
            if tasks and not skip_section:
                tasks[-1]["description"] += " " + line
            else:
                pending_safety.append(line)
            continue
        if is_heading(line):
            core = re.sub(r"^#+\s*|^\d+\.\s+", "", line)
            skip_section = bool(NON_ACTION_SECTIONS.search(core)) and len(core.split()) <= 3
            section_line = line
            if not skip_section:
                parse_heading(line, ctx)
            continue
        m = STEP_RE.match(line)
        if not m or skip_section:
            # hazards stated in non-procedural sections become SAFETY CONTEXT notes on relevant tasks
            body_txt = STEP_RE.sub(lambda mm: mm.group(2), line) if m else line
            for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", body_txt):
                if is_safety_sentence(sent.strip(), section_line) and sent.strip() not in preamble_notes:
                    preamble_notes.append(sent.strip())
            # informational paragraph (scope/regs) -> mine roles only
            for rm in ROLE_MENTION_RE.finditer(line):
                r = re.sub(r"^(The|All)\s+", "", rm.group(1))
                if not ctx["default_role"]:
                    ctx["default_role"] = r
                    if ctx["role"] == "Unspecified":
                        ctx["role"] = r
            continue
        body = m.group(2).strip()
        role = ctx["role"]
        pm = ROLE_PREFIX_RE.match(body)
        if pm:
            role = re.sub(r"\s+(?:and|&)\s+", " + ", pm.group(1).strip())
            body = pm.group(2).strip()
            if "+" not in role:
                ctx["role"] = role      # single named role carries forward within section
        for piece in split_compound(body):
            desc = _cap(_clean(piece))
            if pending_safety:
                desc = desc + " " + " ".join(pending_safety)
                pending_safety = []
            tasks.append({
                "task_title": make_title(desc),
                "description": desc,
                "assigned_position": role or "Unspecified",
                "site_requirement": ctx["site"] or "Unspecified",
                "timeframe": ctx["time"] or "Unspecified",
                "verification_method": choose_verification(desc),
            })
    if pending_safety and tasks:
        tasks[-1]["description"] += " " + " ".join(pending_safety)
    prose = False
    if not tasks:                           # free-prose SOP with no numbered steps -> sentence-level fallback
        tasks = prose_tasks(lines[1:], ctx)
        prose = bool(tasks)
        preamble_notes = []                 # in prose mode every safety sentence is already its own task
    attach_preamble_notes(tasks, preamble_notes)

    named = {t["assigned_position"] for t in tasks} - {"Unspecified"}
    if len(named) == 1:                     # the document names exactly one performer -> it owns every step
        for t in tasks:
            t["assigned_position"] = next(iter(named))
    for i, t in enumerate(tasks, 1):
        t["task_id"] = f"T-{i:03d}"
        t["verification_method"] = choose_verification(t["description"])
    ordered = [{k: t[k] for k in ("task_id", "task_title", "description", "assigned_position",
                                   "site_requirement", "timeframe", "verification_method")} for t in tasks]
    industry = detect_industry(text)
    regs = sorted({m.group(0).strip(" .,;") for m in re.finditer(r"\b(?:OSHA|NFPA|CDC|Joint Commission|FAA|FDA|HACCP|ANSI|ISO)\b[^;\n]{0,110}?(?=;|\.\s|\.$|\s{2}|$)", text, re.M)})
    roles = sorted({t["assigned_position"] for t in ordered if t["assigned_position"] != "Unspecified"})
    summary = (f"{doc_title.strip()} — {len(ordered)} checklist tasks extracted for "
               f"{', '.join(roles) if roles else 'unspecified roles'}.")
    if regs:
        summary += f" References: {'; '.join(regs)}."
    if prose:
        summary += (" No numbered steps were found, so each actionable or safety sentence became one task"
                    " (sentence-level fallback) - review ordering, roles and timeframes carefully.")
    summary += " (Generated by the offline mock parser; set OPENAI_API_KEY for LLM extraction.)"
    return {"industry": industry, "document_summary": summary, "tasks": ordered}
