// Browser demo mode (NEXT_PUBLIC_DEMO=1, used for the static GitHub Pages preview).
// The real SOPCheck FastAPI backend runs inside the page with Pyodide (Python compiled to WebAssembly),
// using the offline mock parser: no server and no AI calls. Every /api/* and /files/* request is answered
// in-browser, and data is saved in this browser's IndexedDB. Reset with ?reset-demo=1.
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const AI_URL = (process.env.NEXT_PUBLIC_AI_PROXY_URL ?? "").replace(/\/$/, "");
let aiOn = false;
const PY_PACKAGES = ["micropip", "sqlalchemy", "pydantic", "tzdata", "ssl", "httpx"];
const PIP_PACKAGES = ["fastapi==0.115.6", "python-multipart", "pyjwt", "pypdf", "bcrypt"];

type PyHandle = (m: string, u: string, h: string, b: Uint8Array | null) => Promise<{ toJs: () => [number, string, Uint8Array] }>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Pyodide = any;
let boot: Promise<{ handle: PyHandle; py: Pyodide }> | null = null;

function overlay(text: string | null, foot = "Runs entirely in your browser. The first load takes a few seconds.") {
  if (typeof document === "undefined") return;
  let el = document.getElementById("demo-boot");
  if (text === null) { el?.remove(); return; }
  if (!el) {
    el = document.createElement("div"); el.id = "demo-boot";
    el.setAttribute("style", "position:fixed;inset:0;z-index:9999;display:grid;place-items:center;background:rgba(241,245,249,.92);font:14px system-ui;color:#334155");
    document.body.appendChild(el);
  }
  el.innerHTML = `<div style="text-align:center;max-width:320px"><div style="font-size:28px">✓</div><b>SOPCheck demo</b><br>${text}<br><span style="font-size:12px;color:#64748b">${foot}</span></div>`;
}

function loadScript(src: string) {
  return new Promise<void>((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = () => res(); s.onerror = () => rej(new Error("failed to load " + src)); document.head.appendChild(s); });
}

async function start() {
  overlay("Starting the demo…");
  await loadScript(PYODIDE + "pyodide.js");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const py: Pyodide = await (window as any).loadPyodide({ indexURL: PYODIDE });
  overlay("Loading components…");
  await py.loadPackage(PY_PACKAGES);
  await py.pyimport("micropip").install(PIP_PACKAGES);
  const bundle: Record<string, string> = await (await fetchOrig(`${BASE_PATH}/demo/bundle.json`)).json();
  for (const [path, b64] of Object.entries(bundle)) {
    if (path.startsWith("/seed/")) continue;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    py.FS.writeFile(path, Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
  }
  py.FS.mkdirTree("/data");
  py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
  await new Promise<void>((r) => py.FS.syncfs(true, () => r()));
  const reset = new URLSearchParams(location.search).has("reset-demo");
  const has = (() => { try { py.FS.stat("/data/app.db"); return true; } catch { return false; } })();
  if (reset || !has) {
    try { for (const f of py.FS.readdir("/data/uploads")) if (f !== "." && f !== "..") py.FS.unlink("/data/uploads/" + f); } catch { /* none */ }
    py.FS.writeFile("/data/app.db", Uint8Array.from(atob(bundle["/seed/app.db"]), (c) => c.charCodeAt(0)));
    if (reset) { sessionStorage.removeItem("sopcheck.session"); }
  }
  overlay("Starting the app server…");
  aiOn = await aiHealthy();
  const pre = aiOn ? `import os\nos.environ["SOPCHECK_AI_URL"]=${JSON.stringify(AI_URL)}\n` : "";
  py.runPython(pre + (bundle["/sop/demo_boot.py"] ? atob(bundle["/sop/demo_boot.py"]) : ""));
  setBadge();
  await persist(py);
  overlay(null);
  return { handle: py.globals.get("handle") as PyHandle, py };
}

// AI parsing is used only when the proxy is configured at build time AND reports itself healthy with budget left.
async function aiHealthy() {
  if (!AI_URL || AI_URL.includes("__")) return false;
  try {
    const c = new AbortController(); const t = setTimeout(() => c.abort(), 6000);
    const r = await fetchOrig(AI_URL + "/health", { signal: c.signal }); clearTimeout(t);
    const j = await r.json();
    return !!(j.ok && j.ai_available && j.key_configured);
  } catch { return false; }
}
function setBadge() {
  const b = document.getElementById("demo-badge-label");
  if (b) b.textContent = aiOn ? "Preview demo · AI parsing on: uploaded text goes to an AI service (Cloudflare → OpenAI); falls back to offline parser · " : "Preview demo · runs in your browser · offline parser · ";
}

function persist(py: Pyodide) { return new Promise<void>((r) => py.FS.syncfs(false, () => r())); }

let fetchOrig: typeof fetch = (...a) => fetch(...a);

async function demoFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const req = new Request(input, init);
  const url = new URL(req.url);
  const { handle, py } = await (boot ??= start());
  const headers: Record<string, string> = {};
  req.headers.forEach((v, k) => { headers[k] = v; });
  const body = req.method === "GET" || req.method === "HEAD" ? null : new Uint8Array(await req.arrayBuffer());
  const parsing = req.method === "POST" && /^\/api\/documents\/(upload|parse-text)$/.test(url.pathname);
  if (parsing && aiOn) { overlay("Parsing with AI… this can take up to a minute.", "Demo only: the document text is sent to an AI service for parsing. Don't upload confidential files."); await new Promise((r) => setTimeout(r, 60)); }
  let r: [number, string, Uint8Array];
  try { r = (await handle(req.method, url.pathname + url.search, JSON.stringify(headers), body)).toJs(); } finally { if (parsing && aiOn) overlay(null); }
  if (req.method !== "GET") await persist(py);
  const h = new Headers(JSON.parse(r[1]));
  return new Response(r[0] === 204 || r[0] === 304 ? null : new Blob([r[2] as BlobPart]), { status: r[0], headers: h });
}

export function installDemo() {
  if (!DEMO || typeof window === "undefined" || (window as unknown as { __sopDemo?: boolean }).__sopDemo) return;
  (window as unknown as { __sopDemo?: boolean }).__sopDemo = true;
  const orig = window.fetch.bind(window);
  fetchOrig = orig;
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, location.href);
    if (u.origin === location.origin && (u.pathname.startsWith("/api/") || u.pathname.startsWith("/files/"))) return demoFetch(input instanceof Request ? input : u.href, init);
    return orig(input, init);
  };
  const badge = () => {
    if (document.getElementById("demo-badge")) return;
    const b = document.createElement("div"); b.id = "demo-badge";
    b.setAttribute("style", "position:fixed;left:8px;bottom:8px;z-index:9998;background:#0f766e;color:#fff;font:11px system-ui;padding:4px 8px;border-radius:999px;opacity:.9");
    b.innerHTML = `<span id="demo-badge-label">Preview demo · runs in your browser · offline parser · </span><a href="${BASE_PATH}/login/?reset-demo=1" style="color:#fff;text-decoration:underline">reset</a>`;
    document.body.appendChild(b);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", badge); else badge();
}
