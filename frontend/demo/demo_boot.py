"""Browser demo runtime: runs the real SOPCheck FastAPI app inside Pyodide (no server).
The JS side (lib/demo.ts) calls handle() for every /api/* and /files/* request."""
import os, sys, types, difflib, re, json

os.environ.setdefault("DATABASE_URL", "sqlite:////data/app.db")
os.environ.setdefault("UPLOAD_DIR", "/data/uploads")
os.environ["LLM_MODEL"] = "mock"                 # offline mock parser; no AI calls from the browser
os.environ.pop("OPENAI_API_KEY", None)
os.environ.setdefault("JWT_SECRET", "browser-demo-only-not-a-secret-0123456789abcdef")
os.environ["DEMO_SEED"] = "true"
os.environ["APP_ENV"] = "demo"
os.environ.setdefault("SIGNUPS_PER_IP_HOUR", "50")
os.makedirs("/data/uploads", exist_ok=True)

# rapidfuzz has no Pyodide wheel: small difflib-based stand-in (same 0-100 scale)
def _tok(s): return set(re.findall(r"\w+", (s or "").lower()))
def _ratio(a, b): return 100.0 * difflib.SequenceMatcher(None, a, b).ratio()
def token_set_ratio(a, b):
    ta, tb = _tok(a), _tok(b)
    if not ta or not tb: return 0.0
    inter = " ".join(sorted(ta & tb)); ra = " ".join(sorted(ta - tb)); rb = " ".join(sorted(tb - ta))
    c1, c2 = (inter + " " + ra).strip(), (inter + " " + rb).strip()
    return max(_ratio(inter, c1) if inter else 0, _ratio(inter, c2) if inter else 0, _ratio(c1, c2))
def partial_ratio(a, b):
    a, b = a or "", b or ""
    if not a or not b: return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long: return 100.0
    best, n = 0.0, len(short)
    for blk in difflib.SequenceMatcher(None, short, long).get_matching_blocks():
        start = max(0, blk.b - blk.a)
        best = max(best, _ratio(short, long[start:start + n]))
    return best
_rf = types.ModuleType("rapidfuzz"); _fz = types.ModuleType("rapidfuzz.fuzz")
_fz.token_set_ratio, _fz.partial_ratio, _fz.ratio = token_set_ratio, partial_ratio, _ratio
_rf.fuzz = _fz; sys.modules["rapidfuzz"] = _rf; sys.modules["rapidfuzz.fuzz"] = _fz

import threading
threading.Thread.start = lambda self: None       # no background threads in the browser
import anyio.to_thread                             # FastAPI runs sync endpoints in a thread pool: run them inline instead
async def _run_inline(func, *args, **_kw):
    return func(*args)
anyio.to_thread.run_sync = _run_inline

sys.path.insert(0, "/sop/backend")
from app import main as _main                    # noqa: E402
from app.db import Base, engine, SessionLocal    # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as _db:
    _main.seed(_db)
try:
    _main.purge_due_companies()
except Exception:
    pass


async def handle(method, url, headers_json, body):
    """Run one HTTP request through the ASGI app. Returns (status, headers_json, body_bytes)."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    hdrs = [(k.lower().encode(), v.encode()) for k, v in json.loads(headers_json).items()]
    body = bytes(body) if body is not None else b""
    if body and not any(k == b"content-length" for k, _ in hdrs):
        hdrs.append((b"content-length", str(len(body)).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(),
             "scheme": "https", "path": u.path, "raw_path": u.path.encode(), "query_string": u.query.encode(),
             "root_path": "", "headers": hdrs, "client": ("127.0.0.1", 0), "server": ("demo", 443)}
    sent = {"done": False}
    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}
    out = {"status": 500, "headers": [], "body": bytearray()}
    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]; out["headers"] = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            out["body"].extend(msg.get("body", b""))
    await _main.app(scope, receive, send)
    h = {}
    for k, v in out["headers"]:
        k = k.decode().lower()
        if k == "set-cookie":
            continue
        h[k] = v.decode()
    return out["status"], json.dumps(h), bytes(out["body"])
