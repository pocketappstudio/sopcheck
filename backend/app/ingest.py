"""Text extraction, normalization, chunking and the safety-coverage audit."""
from __future__ import annotations
import io
import os
import re
from dataclasses import dataclass, field
from pypdf import PdfReader


OCR_MIN_CHARS = 40            # a page with fewer extractable chars is treated as scanned
OCR_DPI = 300
OCR_TIMEOUT_S = int(os.getenv("OCR_TIMEOUT_S", "180"))


@dataclass
class Extraction:
    text: str
    page_count: int | None = None
    method: str = "text"                     # text | pdf_text | ocr | mixed
    ocr_confidence: float | None = None      # mean word confidence (0-100) over OCR'd pages
    ocr_pages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def ocr_image(img) -> tuple[str, float, int]:
    """OCR a PIL image once with tesseract (image_to_data); rebuild text lines and mean word confidence."""
    import pytesseract
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")   # tesseract's OpenMP threads thrash on shared CPUs
    d = pytesseract.image_to_data(img, lang="eng", config="--psm 4", output_type=pytesseract.Output.DICT,
                                  timeout=OCR_TIMEOUT_S)
    lines: dict[tuple, list[str]] = {}
    confs = []
    for i, w in enumerate(d["text"]):
        if not w.strip():
            continue
        lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(w)
        if float(d["conf"][i]) >= 0:
            confs.append(float(d["conf"][i]))
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0), len(confs)


OCR_FIXES = [
    (re.compile(r"(\d)\s?Ibs\b"), r"\1 lbs"),            # I/l confusion
    (re.compile(r"\b0(?=[A-Za-z]{2,})"), "O"),               # 0PERATE -> OPERATE
    (re.compile(r"[‘’]"), "'"), (re.compile(r"[“”]"), '"'),
]


def fix_ocr(text: str) -> str:
    for rx, rep in OCR_FIXES:
        text = rx.sub(rep, text)
    return text


def reflow(text: str) -> str:
    """Re-join lines that a PDF/OCR wrapped mid-sentence, keeping steps, headings and warnings separate."""
    lines = [l.rstrip() for l in text.split("\n")]
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        return text
    width = sorted(len(l) for l in nonblank)[int(len(nonblank) * 0.9) - 1 if len(nonblank) > 1 else 0]
    out: list[str] = []
    last_raw = ""
    # a step number needs "." / ")" or a following capital ("3 Remove ..."); "8 hours on site" / "40 °C or
    # higher" at the start of a wrapped line are continuations, not steps
    starter = re.compile(r"^(?:step\s+)?\d+(?:\.\d+)*[.)]\s+\S|^(?:step\s+)?\d+(?:\.\d+)*\s+(?-i:[A-Z(])|^(WARNING|CAUTION|DANGER|NOTE|DO NOT|NEVER)\b|^[#•\-*]\s"
                         r"|^\(?[a-z]{1,4}[.)]\s+\S|^o\s+[A-Z]", re.I)      # a. / b) / iv. list items, Word "o" sub-bullets
    for l in lines:
        s = l.strip()
        if not s:
            continue
        if out:
            p = out[-1]
            p_heading = bool(re.match(r"^\d+\.\s+[A-Z][A-Z0-9 &/\-–—,()]{2,}$", p)) or p.isupper()
            wrapped = len(last_raw) >= 0.72 * width or p.endswith(("-", ",")) or s[:1].islower() \
                or bool(re.search(r"\b(of|to|the|and|or|a|an|in|for|with|at|by|from|than|per|on)$", p, re.I))
            if not starter.match(s) and not p_heading and not p.endswith((".", ":", ";", "!", "?")) and wrapped:
                out[-1] = (p + s) if p.endswith("-") and not p.endswith(" -") else (p + " " + s)
                last_raw = s
                continue
        out.append(s)
        last_raw = s
    return "\n".join(out)


def extract(data: bytes, filename: str, content_type: str | None) -> Extraction:
    name = (filename or "").lower()
    if name.endswith(".pdf") or (content_type or "").endswith("pdf") or data[:5] == b"%PDF-":
        return _extract_pdf(data)
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return Extraction(text=normalize(data.decode(enc)))
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported text encoding")


def _extract_pdf(data: bytes) -> Extraction:
    reader = PdfReader(io.BytesIO(data))
    pages_text: list[str] = []
    ocr_pages, warnings = [], []
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        if len(re.sub(r"\s", "", t)) >= OCR_MIN_CHARS:
            pages_text.append(t)
            continue
        try:
            from pdf2image import convert_from_bytes
            img = convert_from_bytes(data, dpi=OCR_DPI, first_page=i + 1, last_page=i + 1)[0]
            otext, conf, words = ocr_image(img)
            ocr_pages.append({"page": i + 1, "confidence": round(conf, 1), "words": words})
            if conf < 70:
                warnings.append(f"page {i + 1}: low OCR confidence ({conf:.0f}%) - verify text")
            pages_text.append(fix_ocr(otext))
        except Exception as e:  # tesseract/poppler missing or render failure
            warnings.append(f"page {i + 1}: OCR unavailable ({type(e).__name__}: {e})")
            pages_text.append(t)
    n = len(reader.pages)
    pages_text = strip_running_headers(pages_text)
    method = "pdf_text" if not ocr_pages else ("ocr" if len(ocr_pages) == n else "mixed")
    conf = None
    if ocr_pages:
        tw = sum(p["words"] for p in ocr_pages) or 1
        conf = round(sum(p["confidence"] * p["words"] for p in ocr_pages) / tw, 1)
    text = normalize(reflow("\n".join(pages_text)))
    return Extraction(text=text, page_count=n, method=method, ocr_confidence=conf, ocr_pages=ocr_pages, warnings=warnings)


def _line_key(line: str) -> str:
    k = re.sub(r"\s+", " ", line.strip().lower())
    k = re.sub(r"\bpage [\w-]+", "page #", k)               # page i / 1-2 / A-3
    k = re.sub(r"\s*-\s*", "-", k)
    return re.sub(r"\d+", "#", k)


def strip_running_headers(pages: list[str], min_share: float = 0.4) -> list[str]:
    """Drop lines repeated on many pages (running headers/footers like 'Area 01 - Northwest Page 1-2
    February 1, 2024'), comparing with digits masked so page numbers don't defeat the match."""
    if len(pages) < 3:
        return ["\n".join(l for l in p.split("\n") if not TOC_LINE.match(l.strip())) for p in pages]
    from collections import Counter
    counts = Counter(k for p in pages for k in {_line_key(l) for l in p.split("\n") if l.strip()})
    threshold = max(3, int(len(pages) * min_share))
    running = {k for k, c in counts.items() if c >= threshold and len(k) < 120}
    return ["\n".join(l for l in p.split("\n") if _line_key(l) not in running and not TOC_LINE.match(l.strip()))
            for p in pages]


def pdf_title(data: bytes) -> str | None:
    """Document title from PDF metadata, minus 'Microsoft Word - ' style producer prefixes."""
    try:
        t = (PdfReader(io.BytesIO(data)).metadata or {}).get("/Title")
    except Exception:
        return None
    if not t:
        return None
    t = re.sub(r"^(Microsoft (Word|Excel|PowerPoint)|Word)\s*-\s*", "", str(t)).strip()
    t = re.sub(r"\.(docx?|pdf)$", "", t, flags=re.I)
    return t if 8 <= len(t) <= 160 else None


def extract_text(data: bytes, filename: str, content_type: str | None) -> tuple[str, int | None]:
    """Backwards-compatible helper."""
    e = extract(data, filename, content_type)
    return e.text, e.page_count


BULLET_CHARS = "\uf0b7\uf0a7\uf076\uf0d8\uf0fc\u25aa\u25cf\u2022\u25e6\u2023\u2043"   # Word/Symbol-font bullets
TOC_LINE = re.compile(r"^.{3,}?\s*(?:\.{5,}\s*[\w-]+|\.{3,}\s*(?:[A-Z]?\d+-\d+|[ivx]+))\s*$")                                     # "1.2 Selection Criteria ....... 1-2"


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(f"[{BULLET_CHARS}]", "•", text)
    text = re.sub(r"(\d)\s?o\s?([FC])\b", r"\1 °\2", text)        # Word superscript degree: "100o F" / "100 o F" -> "100 °F"
    text = "\n".join(l for l in text.split("\n") if not TOC_LINE.match(l.strip()))
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # de-hyphenate PDF line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


# NOTE: no global IGNORECASE - with it the ALL-CAPS alternatives matched almost any short line
# ("Scope D – Non-Routine Construction Services"), so chunks were cut inside bullet lists.
HEADING_RE = re.compile(
    r"^(#{1,6}\s+.+|(?i:section|part|chapter)\s+[\w.]+.*|\d+(?:\.\d+)*\.?\s+[A-Z][A-Z0-9 &/()\-–—,.]{2,}$|[A-Z][A-Z0-9 &/()\-–—,]{4,}$)",
    re.MULTILINE,
)


def chunk_text(text: str, max_chars: int = 24000, overlap_chars: int = 300) -> list[str]:
    """Split on structural boundaries (headings > blank lines > lines), never mid-step.
    Each chunk after the first is prefixed with the previous chunk's tail as overlap."""
    if len(text) <= max_chars:
        return [text]
    blocks, cur = [], []
    for line in text.split("\n"):
        if cur and HEADING_RE.match(line) and not re.match(r"^\d+\.\d", line) \
                and not all(HEADING_RE.match(c) for c in cur if c.strip()):      # keep "SECTION 2" + its title together
            blocks.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    # further split any oversize block on blank lines, then on lines
    units: list[str] = []
    for b in blocks:
        if len(b) <= max_chars // 2:      # big sections are split into paragraphs so packing doesn't strand tiny chunks
            units.append(b); continue
        for line in b.split("\n"):        # lines are paragraphs after reflow
            while len(line) > max_chars:
                cut = line.rfind(". ", 0, max_chars)
                cut = cut + 1 if cut > 0 else max_chars
                units.append(line[:cut]); line = line[cut:].lstrip()
            units.append(line)
    # greedy packing; on overflow, back up to the last section heading in the second half of the
    # buffer so chunks end at a section boundary rather than between two items of a list
    sub_head = re.compile(r"^(\d+(?:\.\d+)+\.?\s+\S|SECTION\s+\d+|[A-Z][A-Z0-9 &/()\-–—,]{4,}$)")
    chunks, buf = [], []
    size = lambda xs: sum(len(x) + 1 for x in xs)
    for u in units:
        if buf and size(buf) + len(u) + 1 > max_chars:
            cut = next((i for i in range(len(buf) - 1, 0, -1) if sub_head.match(buf[i]) and size(buf[:i]) >= max_chars * 0.5), None)
            if cut is None:
                chunks.append("\n".join(buf)); buf = []
            else:
                chunks.append("\n".join(buf[:cut])); buf = buf[cut:]
        buf.append(u)
    if buf:
        chunks.append("\n".join(buf))
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        tail = chunks[i - 1][-overlap_chars:]
        if "\n" in tail[:-1]:
            tail = tail[tail.index("\n") + 1:]               # start the overlap on a line boundary
        elif re.search(r"[.;:]\s", tail):
            tail = tail[re.search(r"[.;:]\s", tail).end():]    # ...or a sentence boundary
        elif " " in tail:
            tail = tail[tail.index(" ") + 1:]
        out.append(f"[...continued from previous section...]\n{tail}\n[...]\n{chunks[i]}")
    return out


PPE_RE = re.compile(r"\b(?:gloves?|goggles|safety glasses|face shield|respirator|N95|hard hat|harness|arc[- ]flash suit|arc[- ]rated|hearing protection|steel[- ]toe|gown|mask|apron|hi[- ]vis|high[- ]visibility|vest)\b", re.IGNORECASE)


SAFETY_FLAG_RE = re.compile(
    r"\b(WARNING|CAUTION|DANGER|DO NOT|never|lockout|tagout|LOTO|PPE|zero energy|energized|"
    r"must|sterile|high-alert|fire exit|hazard|notify|immediately)\b|\d\s?(?:volts?|V\b|cal/cm2|°?\s?[CF]\b)",
    re.IGNORECASE,
)


def is_safety_critical(description: str) -> bool:
    return bool(SAFETY_FLAG_RE.search(description) or PPE_RE.search(description))
