"""Hybrid safety-coverage audit.

1. Span extraction: split the source into sentences (keeping section context) and keep the
   safety-critical ones via rules (signal words, PPE, energy isolation, thresholds, regulatory refs,
   hold points / escalation, hazards). When an LLM key is set, an LLM classifier can add spans the
   rules missed (union).
2. Atoms: each span is reduced to normalized, must-not-lose facts: number+unit (+comparator),
   regulatory references, PPE items, quoted literals, frequencies.
3. Matching against task text (title + description + timeframe):
   - exact normalized atom matching (e.g. '8 cal/cm2' == '8 cal/cm²', '41 F' == '41°F'),
   - fuzzy text similarity (rapidfuzz token-set) or, when a key is set, embedding cosine similarity.
4. Classification per span: covered / partial / missing.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from dataclasses import dataclass, field, asdict

from rapidfuzz import fuzz

from . import config, usage

# ---------------- rule vocabulary ----------------
# "do not" only counts as a signal when it is an instruction (sentence/clause start, or upper-case
# DO NOT), not inside a relative clause like "efficiencies that do not diminish ...".
SIGNAL = re.compile(r"\b(WARNING|CAUTION|DANGER|NEVER|must not|shall not|STOP)\b|(?-i:\bDO NOT\b)|(?:^|[.;:!]\s+|•\s*)do not\b", re.I)
PPE = re.compile(r"\b((?:personal )?gas (?:monitors?|detectors?|meters?)|gloves?|goggles|safety glasses|face shields?|respirators?|N95|hard hats?|harness(?:es)?|arc[- ]flash suits?|"
                 r"arc[- ]rated|ear ?plugs|hearing protection|steel[- ]toe(?: boots)?|gowns?|masks?|aprons?|"
                 r"high[- ]visibility vests?|hi[- ]vis(?: vests?)?|vests?|PPE)\b", re.I)
ENERGY = re.compile(r"\b(lockout|tagout|LOTO|zero energy|energi[sz]ed|re-energi[sz]e|isolat\w+|hazardous energy|lock and (?:danger )?tag|"
                    r"de-?energi[sz]e|breaker|voltage|arc flash)\b", re.I)
HOLD = re.compile(r"\b(do not proceed|only after|until both|stop work|stop and|hold point|before moving|before touching|"
                  r"notify [\w\s']{0,40}immediately|immediately|independent(?:ly)? (?:double-check|verify))\b", re.I)
HAZARD = re.compile(r"\b(hydrogen sulfide|carbon monoxide|combustible gas|hazard\w*|injur\w*|tip-?overs?|pedestrian strikes?|crush\w*|pinch[- ]points?|electrocution|shock|burns?|fire (?:exits?|code)|"
                    r"explosion|fall protection|confined space|sterile|infection|high-alert|spills?|trip hazards?|exceed)\b", re.I)
COMPARATOR = re.compile(r"\b(at or below|at or above|no more than|no less than|not exceed|less than|greater than|more than|"
                        r"at least|minimum|maximum|below|above|under|over|exactly|within|up to|rated)\b", re.I)

UNIT_MAP = [
    (r"cal/cm(?:2|²)", "cal/cm2"), (r"mL/hr", "mL/h"), (r"mph", "mph"), (r"(?-i:kV)", "kV"), (r"(?-i:mA)", "mA"),
    (r"volts?|(?-i:V)(?![a-z])", "V"), (r"amps?|(?-i:A)(?![a-z])", "A"), (r"psi[ga]?", "psi"), (r"kPa", "kPa"), (r"bar\b", "bar"),
    (r"°\s?C|degrees? C(?:elsius)?|(?-i:C)(?![a-z])", "°C"), (r"°\s?F|degrees? F(?:ahrenheit)?|(?-i:F)(?![a-z])", "°F"),
    (r"seconds?|secs?", "s"), (r"minutes?|mins?", "min"), (r"hours?|hrs?", "h"),
    (r"days?", "d"), (r"weeks?", "wk"), (r"months?", "mo"), (r"years?", "yr"), (r"ft-?lbs?", "ft-lb"), (r"lbs?|pounds?", "lb"), (r"kg", "kg"),
    (r"N·?m", "N·m"), (r"ft\b|feet", "ft"), (r"mm\b", "mm"), (r"inch(?:es)?", "in"), (r"ppm", "ppm"),
    (r"(?-i:dB)", "dB"), (r"mL\b", "mL"), (r"mg\b", "mg"), (r"mcg", "mcg"), (r"units?\b", "units"), (r"%", "%"),
]
UNIT_RE = "|".join(f"(?:{p})" for p, _ in UNIT_MAP)
NUM_UNIT = re.compile(rf"(\$)?(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s?({UNIT_RE})?(?!\w)", re.I)
REG_PATTERNS = [
    r"\b\d+\s*CFR\s*[\d.]+\d", r"\bNFPA\s*\d+\w*", r"\bANSI\s*[A-Z]?\d[\w.]*", r"\bISO\s*\d+(?::\d+)?",
    r"\bNPSG\.?[\d.]+\d", r"\bIEC\s*\d+", r"\bASTM\s*[A-Z]\d+", r"\b(?:OSHA|HACCP|CDC|FDA|FAA|EPA|Joint Commission)\b",
]
REG = re.compile("|".join(REG_PATTERNS), re.I)
FREQ = re.compile(r"\bevery\s+(\d+)\s*(hours?|hrs?|days?|minutes?|weeks?|months?|years?|shifts?)\b", re.I)
QUOTED = re.compile(r"[\"“]([^\"”]{3,60})[\"”]")
STOP = set("the a an and or of to for on in at by with your all any this that is are be it its as from into if then "
           "than per each when must will shall should can".split())


def _canon_unit(u: str | None) -> str:
    if not u:
        return ""
    for pat, canon in UNIT_MAP:
        if re.fullmatch(pat, u.strip(), re.I):
            return canon
    return u.strip().lower()


def _canon_num(n: str) -> str:
    v = float(n.replace(",", ""))
    return str(int(v)) if v.is_integer() else f"{v:g}"


def _cmp_symbol(text_before: str) -> str:
    m = None
    for m in COMPARATOR.finditer(text_before[-30:]):
        pass
    if not m:
        return ""
    w = m.group(1).lower()
    return {"at or below": "<=", "no more than": "<=", "not exceed": "<=", "maximum": "<=", "up to": "<=", "within": "<=", "rated": "",
            "under": "<", "below": "<", "less than": "<",
            "at or above": ">=", "no less than": ">=", "at least": ">=", "minimum": ">=",
            "greater than": ">", "more than": ">", "above": ">", "over": ">", "exactly": "="}.get(w, "")


def extract_atoms(text: str) -> list[dict]:
    atoms: list[dict] = []
    seen = set()

    def add(kind, key, label, **kw):
        if (kind, key, kw.get("cmp", "")) in seen:
            return
        seen.add((kind, key, kw.get("cmp", "")))
        atoms.append({"kind": kind, "key": key, "label": label, **kw})

    for m in NUM_UNIT.finditer(text):
        money, num, unit = m.group(1), m.group(2), m.group(3)
        if not unit and not money:
            continue                                   # bare numbers (step refs, model numbers) are not thresholds
        canon_unit = "USD" if money else _canon_unit(unit)
        if not money and re.search(r"[A-Za-z]-$", text[:m.start()]):
            continue                                   # designations like R-410A, M-16A are names, not 410 amps
        if re.search(r"\bevery\s*$", text[:m.start()], re.I):
            continue                                   # 'every 4 hours' is captured as a frequency atom
        add("quantity", f"{_canon_num(num)} {canon_unit}", m.group(0).strip(), cmp=_cmp_symbol(text[:m.start()]))
    for m in REG.finditer(text):
        add("regulation", re.sub(r"[\s.]", "", m.group(0)).lower(), m.group(0).strip())
    for m in PPE.finditer(text):
        k = re.sub(r"[- ]", "", m.group(0).lower()).rstrip("s")
        if k in ("ppe",):
            continue
        add("ppe", k, m.group(0))
    for m in FREQ.finditer(text):
        add("frequency", f"every {m.group(1)} {_canon_unit(m.group(2))}", m.group(0))
    for m in QUOTED.finditer(text):
        add("literal", m.group(1).strip().lower(), m.group(0))
    return atoms


def categories(text: str) -> list[str]:
    cats = []
    if SIGNAL.search(text): cats.append("warning")
    if PPE.search(text): cats.append("ppe")
    if ENERGY.search(text): cats.append("energy_isolation")
    if HOLD.search(text): cats.append("hold_point")
    if REG.search(text): cats.append("regulatory")
    if HAZARD.search(text): cats.append("hazard")
    if any(a["kind"] == "quantity" for a in extract_atoms(text)) and (COMPARATOR.search(text) or re.search(r"\bmust\b", text, re.I)):
        cats.append("threshold")
    if FREQ.search(text): cats.append("frequency")
    return cats


# ---------------- span extraction ----------------
@dataclass
class Span:
    text: str
    section: str
    categories: list[str]
    atoms: list[dict]
    detector: str = "rules"
    hash: str = ""

    def __post_init__(self):
        self.hash = hashlib.sha256(normalize(self.text).encode()).hexdigest()[:32]


STEP_PREFIX = re.compile(r"^(?:step\s+)?\d+(?:\.\d+)*[.)]?\s+", re.I)
ROLE_PREFIX = re.compile(r"^((?:[A-Z][\w&/\-]*\s){0,4}[A-Z][\w&/\-]*(?:\s(?:on Duty|and [A-Z][\w ]{1,30}))?):\s+(?=[A-Z])")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")


def is_heading(line: str) -> bool:
    if line.startswith(("•", "-", "*", "–")):
        return False                                   # bullet items are content, not headings
    m = STEP_PREFIX.match(line)
    if m:                                              # numbered Title-Case sub-heading: "1.8.2 Personal Protective Equipment (PPE)"
        rest = line[m.end():]
        if re.match(r"^\d+\.\d", line) and len(rest.split()) <= 8 and not rest.endswith((".", ";", ":", "!", "?")) \
                and sum(w[:1].isupper() for w in rest.split() if w.isalpha() and len(w) > 3) >= max(1, len([w for w in rest.split() if w.isalpha() and len(w) > 3]) - 1):
            return True
    if re.match(r"^\d+\.\s+[A-Z][A-Z0-9&/\-–—,]+(?: [A-Z0-9&/\-–—,]+)*(?:\s*\([^)]*\))?(?:\s+[-–—]\s+[^.]+)?$", line):
        return True
    return len(line) <= 90 and not line.endswith((".", ";", "!")) and not STEP_PREFIX.match(line) and not SIGNAL.match(line)


def normalize(s: str) -> str:
    s = s.lower().replace("²", "2").replace("°", " ")
    s = re.sub(r"[^\w%$/.\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_spans(source: str, use_llm: bool | None = None) -> list[Span]:
    lines = [l.strip() for l in source.split("\n") if l.strip()]
    spans: list[Span] = []
    section = ""
    seen = set()
    for i, line in enumerate(lines):
        if i == 0:
            continue                                           # document title
        if is_heading(line):
            section = line
            for m in FREQ.finditer(line):                     # frequencies stated in headings are compliance facts
                sp = Span(m.group(0), section, ["frequency"], extract_atoms(m.group(0)))
                if sp.hash not in seen:
                    seen.add(sp.hash); spans.append(sp)
            continue
        body = STEP_PREFIX.sub("", line)
        body = ROLE_PREFIX.sub("", body)
        for sent in SENT_SPLIT.split(body):
            sent = sent.strip()
            if len(sent) < 6:
                continue
            cats = span_categories(sent, section)
            if not cats:
                continue
            # a sentence whose only signal is a bare regulatory mention like "Company: X" is still kept
            sp = Span(sent, section, cats, extract_atoms(sent))
            if sp.hash not in seen:
                seen.add(sp.hash); spans.append(sp)
    if use_llm if use_llm is not None else bool(config.OPENAI_API_KEY):
        try:
            spans = _llm_augment(lines, spans)
        except Exception:
            pass                                               # rules-only result is still valid
    return spans


def _llm_augment(lines: list[str], spans: list[Span]) -> list[Span]:
    """Ask the LLM which sentences are safety-critical; add any the rules missed."""
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL, timeout=config.LLM_TIMEOUT_S)
    sents, sections = [], []
    section = ""
    for l in lines[1:]:
        if is_heading(l):
            section = l
            continue
        for s in SENT_SPLIT.split(ROLE_PREFIX.sub("", STEP_PREFIX.sub("", l))):
            if len(s.strip()) >= 6 and not statement_only(s.strip(), section):   # same exclusions as the rules
                sents.append(s.strip()); sections.append(section)
    prompt = ("Classify which numbered sentences from a work procedure are SAFETY-CRITICAL (warnings, PPE, energy isolation, "
              "thresholds/limits, regulatory references, hold points, escalation rules, hazards). Return JSON "
              '{"critical": [{"i": <number>, "category": "<short>"}]}. Sentences:\n' +
              "\n".join(f"{i}. {s}" for i, s in enumerate(sents)))
    kw = {} if config.is_reasoning_model(config.LLM_MODEL) else {"temperature": 0}
    resp = client.chat.completions.create(model=config.LLM_MODEL, response_format={"type": "json_object"},
                                          messages=[{"role": "user", "content": prompt}], **kw)
    usage.record(config.LLM_MODEL, getattr(resp, "usage", None))
    data = json.loads(resp.choices[0].message.content or "{}")
    have = {s.hash for s in spans}
    for item in data.get("critical", []):
        i = item.get("i")
        if isinstance(i, int) and 0 <= i < len(sents):
            sp = Span(sents[i], sections[i], [str(item.get("category", "llm"))], extract_atoms(sents[i]), detector="llm")
            if sp.hash in have:
                for s in spans:
                    if s.hash == sp.hash and "llm" not in s.detector:
                        s.detector = "rules+llm"
            else:
                have.add(sp.hash); spans.append(sp)
    return spans


# ---------------- matching ----------------
def _content(s: str) -> str:
    return " ".join(w for w in normalize(s).split() if w not in STOP)


def _atom_in(atom: dict, task_atoms: list[dict], task_norm: str) -> bool:
    if atom["kind"] == "quantity":
        hits = [a for a in task_atoms if a["kind"] == "quantity" and a["key"] == atom["key"]]
        if not hits:
            return False
        return not atom.get("cmp") or any((h.get("cmp") or "") == atom["cmp"] for h in hits)
    if atom["kind"] in ("regulation", "ppe", "frequency"):
        return any(a["kind"] == atom["kind"] and a["key"] == atom["key"] for a in task_atoms)
    if atom["kind"] == "literal":
        return atom["key"] in task_norm or normalize(atom["key"]) in task_norm
    return False


class _Embedder:
    """Optional semantic similarity via an OpenAI-compatible embeddings endpoint."""
    def __init__(self):
        self.ok = bool(config.OPENAI_API_KEY)
        self.cache: dict[str, list[float]] = {}
        if self.ok:
            from openai import OpenAI
            self.client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL, timeout=config.LLM_TIMEOUT_S)

    def embed(self, texts: list[str]):
        need = [t for t in texts if t not in self.cache]
        if need:
            resp = self.client.embeddings.create(model=config.EMBEDDING_MODEL, input=need)
            usage.record(config.EMBEDDING_MODEL, getattr(resp, "usage", None))
            for t, d in zip(need, resp.data):
                self.cache[t] = d.embedding

    def sim(self, a: str, b: str) -> float:
        va, vb = self.cache[a], self.cache[b]
        dot = sum(x * y for x, y in zip(va, vb))
        na, nb = math.sqrt(sum(x * x for x in va)), math.sqrt(sum(y * y for y in vb))
        return 100.0 * dot / (na * nb) if na and nb else 0.0


COVERED_TEXT = 80.0     # text similarity needed when atoms all match / when span has no atoms
PARTIAL_TEXT = 60.0


def audit(source: str, tasks: list[dict], summary: str = "", spans: list[Span] | None = None,
          use_embeddings: bool | None = None) -> dict:
    """Classify every safety span as covered / partial / missing against the given checklist tasks."""
    spans = spans if spans is not None else extract_spans(source)
    singles = [(i,) for i in range(len(tasks))]
    pairs = [(i, i + 1) for i in range(len(tasks) - 1)]          # a source sentence split into 2 tasks
    groups = singles + pairs
    def gtext(g): return " ".join(f'{tasks[i]["task_title"]}. {tasks[i]["description"]}' for i in g)
    def gatom(g): return " ".join(f'{tasks[i]["task_title"]}. {tasks[i]["description"]} Timeframe: {tasks[i].get("timeframe", "")}' for i in g)
    texts = [gtext(g) for g in groups]
    norms = [normalize(gatom(g)) for g in groups]
    contents = [_content(x) for x in texts]
    t_atoms = [extract_atoms(gatom(g)) for g in groups]
    s_atoms = extract_atoms(summary)
    emb = None
    if use_embeddings if use_embeddings is not None else bool(config.OPENAI_API_KEY):
        try:
            emb = _Embedder()
            emb.embed([s.text for s in spans] + texts)
        except Exception:
            emb = None
    results = []
    for sp in spans:
        sc = _content(sp.text)
        best_i, best_score, best_matched = -1, -1.0, []
        for i, tc in enumerate(contents):
            text_sim = fuzz.token_set_ratio(sc, tc) if sc and tc else 0.0
            if emb:
                text_sim = max(text_sim, emb.sim(sp.text, texts[i]))
            matched = [a for a in sp.atoms if _atom_in(a, t_atoms[i], norms[i])]
            # rank: atoms first (they are the non-negotiable facts), then text similarity; prefer single tasks
            score = (len(matched) / len(sp.atoms) * 100 if sp.atoms else text_sim) * 0.6 + text_sim * 0.4
            if len(groups[i]) > 1:
                score -= 0.5
            if score > best_score:
                best_i, best_score, best_matched, best_text = i, score, matched, text_sim
        if best_i < 0:
            best_text, best_matched = 0.0, []
        n = len(sp.atoms)
        m = len(best_matched)
        # regulation-only spans may legitimately live in the document summary
        union_matched = [a for a in sp.atoms if any(_atom_in(a, ta, nn) for ta, nn in zip(t_atoms[:len(singles)] + [s_atoms], norms[:len(singles)] + [normalize(summary)]))]
        reg_only = n > 0 and all(a["kind"] == "regulation" for a in sp.atoms)
        if (n and m == n and best_text >= PARTIAL_TEXT) or (not n and best_text >= COVERED_TEXT) or (reg_only and len(union_matched) == n):
            cov = "covered"
        elif (n and (m or len(union_matched))) or best_text >= PARTIAL_TEXT:
            cov = "partial"
        else:
            cov = "missing"
        # an exact (normalized) containment is always covered
        if cov != "covered" and best_i >= 0 and normalize(sp.text) in norms[best_i]:
            cov = "covered"
        matched_keys = {a["key"] + a.get("cmp", "") for a in (union_matched if reg_only else best_matched)}
        results.append({
            "span_hash": sp.hash, "span_text": sp.text, "section": sp.section, "categories": sp.categories,
            "atoms": sp.atoms, "detector": sp.detector, "coverage": cov, "score": round(max(best_score, 0), 1),
            "text_similarity": round(best_text, 1),
            "best_task_code": tasks[groups[best_i][0]].get("task_id") if best_i >= 0 else None,
            "best_task_codes": [tasks[j].get("task_id") for j in groups[best_i]] if best_i >= 0 else [],
            "matched_atoms": [a["label"] for a in sp.atoms if a["key"] + a.get("cmp", "") in matched_keys],
            "missing_atoms": [a["label"] + (f' ({a["cmp"]})' if a.get("cmp") else "") for a in sp.atoms if a["key"] + a.get("cmp", "") not in matched_keys],
        })
    counts = {k: sum(1 for r in results if r["coverage"] == k) for k in ("covered", "partial", "missing")}
    total = len(results)
    return {"spans": results, "counts": counts, "total": total,
            "coverage_pct": round(100.0 * counts["covered"] / total, 1) if total else 100.0,
            "method": "rules+atoms+" + ("embeddings" if emb else "rapidfuzz")}


ROLE_SECTION = re.compile(r"RESPONSIBILIT|ROLES|DEFINITION", re.I)
GOAL_SECTION = re.compile(r"PURPOSE|OBJECTIVE|\bGOAL", re.I)
SIGNAL_WORD = re.compile(r"\b(WARNING|DANGER|CAUTION|never|do not|must not|immediately)\b", re.I)


def statement_only(sentence: str, section: str) -> bool:
    """Role assignments in Responsibilities/Definitions and goal statements in Purpose/Objective
    ('Prevent forklift tip-overs and pedestrian strikes.') describe the procedure; they are not
    checkable hazards unless they carry a concrete atom (threshold, PPE, regulation) or a signal word."""
    return bool((ROLE_SECTION.search(section or "") or GOAL_SECTION.search(section or ""))
                and not extract_atoms(sentence) and not SIGNAL_WORD.search(sentence))


# Billing / pricing language (markups, overtime rates, "cost includes ... PPE" clauses):
# Numbers, PPE words and "shall not" there are commercial terms, not hazards.
COMMERCIAL = re.compile(r"\b(mark-?up|per diem|overtime rate|lump sum|pro-rated|bill(?:ed|ing)?|invoic\w+|charge[ds]?|price\w*|"
                        r"cost(?:s| plus)?|compensat\w+|payment|paid|bid price|line item|fee schedule|rates?)\b", re.I)
COMMERCIAL_ONLY = {"threshold", "ppe", "warning", "frequency"}


def span_categories(sentence: str, section: str = "") -> list[str]:
    """Rule categories for a sentence. Role-assignment statements in Responsibilities/Definitions
    sections ('The Authorized Electrician performs all isolation steps') inform assigned_position,
    they are not hazards, unless they carry a threshold, PPE item or warning."""
    cats = categories(sentence)
    if cats and set(cats) <= COMMERCIAL_ONLY and len(COMMERCIAL.findall(sentence)) >= 1 and not HAZARD.search(sentence) \
            and not ENERGY.search(sentence) and not re.search(r"\b(WARNING|DANGER|CAUTION|immediately)\b", sentence, re.I) \
            and (len(COMMERCIAL.findall(sentence)) >= 2 or re.search(r"\b(mark-?up|per diem|overtime|lump sum|bid|invoic\w+|compensat\w+)\b", sentence, re.I)):
        return []
    if cats and ROLE_SECTION.search(section or "") and set(cats) <= {"energy_isolation", "regulatory", "hold_point"} \
            and not extract_atoms(sentence):
        return []
    if cats and GOAL_SECTION.search(section or "") and statement_only(sentence, section) \
            and set(cats) <= {"hazard", "energy_isolation", "hold_point"}:
        return []
    return cats


def is_safety_sentence(s: str, section: str = "") -> bool:
    """True for safety-critical sentences that are not just regulatory citations."""
    cats = span_categories(s, section)
    return bool(cats) and cats != ["regulatory"]


def missing_part(span: str, task_text: str, threshold: int = 90) -> str:
    """The part of a source safety sentence that a task does not already say, for "attach to task".
    Splits the sentence into clauses (", " / "; " / " and " / " but " boundaries) and keeps the clauses
    not found (rapidfuzz partial match) in the task text. Returns "" when everything is already there,
    the full sentence when nothing is (or when trimming would drop a must-keep atom)."""
    tn = normalize(task_text)
    parts = [p for p in re.split(r"(?<=[,;:])\s+|\s+(?=(?:and|but|unless|or)\s)", span.strip()) if p.strip()]
    if len(parts) <= 1:
        return "" if fuzz.partial_ratio(normalize(span), tn) >= threshold else span.strip()
    merged: list[str] = []
    for p in parts:                       # glue fragments shorter than 3 words onto the previous clause
        if merged and len(p.split()) < 3:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    parts = merged
    if len(parts) <= 1:
        return "" if fuzz.partial_ratio(normalize(span), tn) >= threshold else span.strip()
    present = [fuzz.partial_ratio(normalize(p), tn) >= threshold for p in parts]
    if all(present):
        return ""
    if not any(present):
        return span.strip()
    out, prev = [], True
    for p, ok in zip(parts, present):
        if not ok:
            out.append(("… " if prev else "") + p.strip())
        prev = ok
    excerpt = " ".join(out).strip().rstrip(",;")
    if not excerpt.endswith((".", "!", "?")):
        excerpt += "."
    need = {a["key"] for a in extract_atoms(span)} - {a["key"] for a in extract_atoms(task_text)}
    if need - {a["key"] for a in extract_atoms(excerpt)}:
        return span.strip()
    return excerpt
