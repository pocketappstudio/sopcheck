"""Exercise the LLM code path against tests/fake_llm_server.py (start it on :8099 first):
JSON-mode extraction with a forced invalid first answer -> repair, LLM safety-span classification
(union with rules) and embedding-based semantic matching in the safety audit."""
import os
import sys
from pathlib import Path
os.environ.update(OPENAI_API_KEY="sk-fake", OPENAI_BASE_URL="http://127.0.0.1:8099/v1", LLM_MODEL="fake-gpt", LLM_FALLBACK_TO_MOCK="false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.llm import parse_document          # noqa: E402
from app.safety import audit, extract_spans  # noqa: E402

text = (Path(__file__).resolve().parents[2] / "samples" / "construction_loto.txt").read_text()
r = parse_document(text, "LOTO", "Demo Company")
print(f"parser={r.parser} status={r.status} repairs={r.repair_attempts} chunks={r.chunk_count} tasks={len(r.checklist['tasks'])}")
spans = extract_spans(text)
print("spans:", len(spans), "detectors:", sorted({s.detector for s in spans}))
rep = audit(text, r.checklist["tasks"], r.checklist["document_summary"], spans=spans)
print("audit method:", rep["method"], rep["counts"], f"{rep['coverage_pct']}%")
ok = r.parser == "llm" and r.repair_attempts >= 1 and "embeddings" in rep["method"] and any("llm" in s.detector for s in spans)
print("PASS" if ok else "FAIL", "LLM extraction + repair + LLM span classifier + embeddings")
sys.exit(0 if ok else 1)
