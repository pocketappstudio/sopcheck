"""Precision/recall evaluation of the hybrid safety-coverage audit.

For each sample SOP we parse it (mock parser), confirm the baseline checklist is fully covered, then
simulate typical LLM failure modes on the *checklist* and check the audit catches them:
  - omission: a safety sentence is dropped from every task description            -> expect missing/partial
  - value drift: a threshold number is changed (41 F -> 45 F)                        -> expect partial
  - comparator flip: 'less than' -> 'greater than'                                   -> expect partial
and benign rewrites that must NOT be flagged (paraphrases keeping every number/unit/PPE).
Run:  ./venv/bin/python tests/test_safety_eval.py      (exit 1 if recall < 1.0 or precision < 0.9)
"""
import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ingest import extract            # noqa: E402
from app.mock_parser import mock_parse    # noqa: E402
from app.safety import audit, extract_spans  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
FILES = ["construction_loto.txt", "hospital_central_line.pdf", "retail_store_opening.txt", "warehouse_forklift_preop_SCANNED.pdf"]

# (sample, find, replace, expected) - deliberate mutations of the parsed checklist
MUTATIONS = [
    ("construction_loto.txt", "WARNING: Never work on energized equipment above 50 volts without an approved energized work permit.", "", "flag"),
    ("construction_loto.txt", "DO NOT proceed until both signatures are recorded.", "", "flag"),
    ("construction_loto.txt", "less than 1 volt", "greater than 1 volt", "flag"),
    ("construction_loto.txt", "minimum 8 cal/cm2", "minimum 4 cal/cm2", "flag"),
    ("hospital_central_line.pdf", "CAUTION: Maintain sterile field throughout; do not touch the catheter hub with non-sterile hands.", "", "flag"),
    ("hospital_central_line.pdf", "If temperature is greater than 38.3 C, notify the attending physician immediately.", "", "flag"),
    ("hospital_central_line.pdf", "for at least 20 seconds", "", "flag"),
    ("retail_store_opening.txt", "at or below 41 F; if above 41 F", "at or below 45 F; if above 45 F", "flag"),
    ("retail_store_opening.txt", "Fire exits must remain clear per local fire code.", "", "flag"),
    ("warehouse_forklift_preop_SCANNED.pdf", "Do not operate if the charge is below 20%.", "", "flag"),
    ("warehouse_forklift_preop_SCANNED.pdf", "steel-toe boots", "boots", "flag"),
    # benign paraphrases (keep all safety atoms) - must stay covered
    ("construction_loto.txt", "WARNING: Never work on energized equipment above 50 volts without an approved energized work permit.",
     "WARNING: Never perform work on equipment energized above 50 volts unless you have an approved energized work permit.", "keep"),
    ("retail_store_opening.txt", "Walk the sales floor and check aisles for spills, trip hazards, or blocked fire exits.",
     "Walk the sales floor; check every aisle for spills, trip hazards or blocked fire exits.", "keep"),
    ("warehouse_forklift_preop_SCANNED.pdf", "Keep travel speed at or below 5 mph in pedestrian aisles.",
     "In pedestrian aisles keep your travel speed at or below 5 mph.", "keep"),
]


def mutate(tasks, find, repl):
    out, hit = copy.deepcopy(tasks), False
    for t in out:
        for k in ("task_title", "description"):
            if find in t[k]:
                t[k] = re.sub(r"\s{2,}", " ", t[k].replace(find, repl)).strip()
                hit = True
    return out, hit


def flagged(rep):
    return {s["span_hash"]: s for s in rep["spans"] if s["coverage"] != "covered"}


tp = fp = fn = tn = 0
rows = []
cache = {}
for f in FILES:
    ex = extract((SAMPLES / f).read_bytes(), f, None)
    cl = mock_parse(ex.text)
    spans = extract_spans(ex.text, use_llm=False)
    base = audit(ex.text, cl["tasks"], cl["document_summary"], spans=spans, use_embeddings=False)
    cache[f] = (ex.text, cl, spans)
    bflag = flagged(base)
    fp += len(bflag)
    tn += base["total"] - len(bflag)
    rows.append((f, "baseline (unmodified)", f"{base['counts']}", "PASS" if not bflag else "FAIL"))

for f, find, repl, expect in MUTATIONS:
    text, cl, spans = cache[f]
    tasks, hit = mutate(cl["tasks"], find, repl)
    if not hit:
        rows.append((f, f"[skip] '{find[:40]}' not in checklist", "", "SKIP")); continue
    rep = audit(text, tasks, cl["document_summary"], spans=spans, use_embeddings=False)
    fl = flagged(rep)
    # the span(s) we touched = spans whose text contains the original fragment
    touched = [s for s in rep["spans"] if find.replace("WARNING: ", "").replace("CAUTION: ", "")[:30].lower() in s["span_text"].lower()
               or find.lower() in s["span_text"].lower()]
    caught = [s for s in touched if s["span_hash"] in fl]
    others = [h for h in fl if h not in {s["span_hash"] for s in touched}]
    if expect == "flag":
        tp += 1 if caught else 0
        fn += 0 if caught else 1
        verdict = "PASS" if caught else "FAIL"
        detail = ", ".join(f'{s["coverage"]} (missing atoms: {s["missing_atoms"] or "-"})' for s in caught) or "NOT CAUGHT"
    else:
        fp += 1 if caught else 0
        tn += 0 if caught else 1
        verdict = "PASS" if not caught else "FAIL"
        detail = "still covered" if not caught else f'false flag: {caught[0]["coverage"]}'
    fp += len(others)
    rows.append((f, f"{expect}: '{find[:48]}' -> '{repl[:30]}'", detail + (f" | +{len(others)} collateral flags" if others else ""), verdict))

w = max(len(r[1]) for r in rows)
for r in rows:
    print(f"{r[3]:<5} {r[0]:<38} {r[1]:<{w}}  {r[2]}")
precision = tp / (tp + fp) if tp + fp else 1.0
recall = tp / (tp + fn) if tp + fn else 1.0
print(f"\nspans evaluated: {tn + fp} benign, {tp + fn} mutated | TP={tp} FP={fp} FN={fn} TN={tn}")
print(f"precision={precision:.3f} recall={recall:.3f}")
sys.exit(0 if recall >= 1.0 and precision >= 0.9 else 1)
