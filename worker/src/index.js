// SOPCheck AI-parse proxy (Cloudflare Worker).
// Holds OPENAI_API_KEY as a Worker secret. The browser demo (GitHub Pages) runs the real backend
// pipeline (chunking, validation, repair, merge) in Pyodide and calls this Worker only for the
// single "chat completion" step. The Worker accepts ONLY the SOP-extraction message shape
// (exact system prompt v1.2.0, templated user prompt, templated repair prompt) so it cannot be used
// as a general-purpose OpenAI proxy.
import { PROMPTS, PROMPT_VERSION } from "./prompts.js";

const MODEL = "gpt-4.1";
const PRICE_IN = 2.0, PRICE_OUT = 8.0;           // USD per 1M tokens (gpt-4.1 list price)
const MAX_OUT_TOKENS = 16000;
const MAX_BODY_BYTES = 160_000;
const MAX_DOC_CHARS = 26_000;                    // backend chunk size is 24000 (+ overlap)
const UPSTREAM_TIMEOUT_MS = 100_000;

const num = (v, d) => { const n = Number(v); return Number.isFinite(n) && v !== undefined && v !== "" ? n : d; };
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Build a strict regex from a prompt template: literal text is matched exactly, placeholders are bounded wildcards.
function templateRegex(tpl, bounds) {
  const parts = tpl.split(/(\{[a-z_]+\})/);
  const src = parts.map((p) => {
    const m = /^\{([a-z_]+)\}$/.exec(p);
    return m ? `(${bounds[m[1]] || "[^\\n]{0,200}"})` : esc(p);
  }).join("");
  return new RegExp("^" + src + "$");
}
const USER_RE = templateRegex(PROMPTS.user, {
  company_name: "[^\\n]{0,200}", document_title: "[^\\n]{0,300}", chunk_index: "\\d{1,3}", chunk_total: "\\d{1,3}",
  section_context: "[\\s\\S]{0,1200}", task_id_start: "\\d{3,5}", document_text: `[\\s\\S]{20,${MAX_DOC_CHARS}}`,
});
const REPAIR_RE = templateRegex(PROMPTS.repair, { validation_errors: "[\\s\\S]{0,8000}" });

export function validateMessages(messages) {
  if (!Array.isArray(messages) || (messages.length !== 2 && messages.length !== 4)) return "messages must have 2 or 4 entries";
  const roles = messages.length === 2 ? ["system", "user"] : ["system", "user", "assistant", "user"];
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || typeof m !== "object" || m.role !== roles[i] || typeof m.content !== "string") return `bad message ${i}`;
    if (Object.keys(m).some((k) => k !== "role" && k !== "content")) return `unexpected keys in message ${i}`;
  }
  if (messages[0].content !== PROMPTS.system) return "system prompt mismatch (expected " + PROMPT_VERSION + ")";
  if (!USER_RE.test(messages[1].content)) return "user message does not match the SOP extraction template";
  if (messages.length === 4) {
    if (messages[2].content.length > 80_000) return "assistant message too long";
    if (!REPAIR_RE.test(messages[3].content)) return "repair message does not match the template";
  }
  return null;
}

function cors(env, origin) {
  const allowed = (env.ALLOWED_ORIGINS || "https://pocketappstudio.github.io").split(",").map((s) => s.trim()).filter(Boolean);
  const ok = origin && allowed.includes(origin);
  return { ok, headers: ok ? { "Access-Control-Allow-Origin": origin, "Vary": "Origin",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400" } : { "Vary": "Origin" } };
}
const json = (obj, status, headers) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers } });

const day = () => new Date().toISOString().slice(0, 10);
const hour = () => new Date().toISOString().slice(0, 13);
const kvInt = async (kv, key) => parseInt((await kv.get(key)) || "0", 10) || 0;
const kvPut = (kv, key, v, ttl) => kv.put(key, String(Math.max(0, Math.round(v))), { expirationTtl: ttl });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";
    const c = cors(env, origin);
    if (request.method === "OPTIONS") return new Response(null, { status: c.ok ? 204 : 403, headers: c.headers });
    if (url.pathname === "/health") {
      const capMicro = Math.round(num(env.DAILY_CAP_USD, 0.5) * 1e6);
      const spent = env.RL ? await kvInt(env.RL, `cost:${day()}`) : 0;
      return json({ ok: true, prompt: PROMPT_VERSION, model: MODEL, ai_available: spent < capMicro, key_configured: !!env.OPENAI_API_KEY }, 200, c.headers);
    }
    if (url.pathname !== "/parse" || request.method !== "POST") return json({ error: "not_found" }, 404, c.headers);
    if (!c.ok) return json({ error: "forbidden_origin", message: "This endpoint only serves the SOPCheck preview site." }, 403, c.headers);
    if (!env.OPENAI_API_KEY || !env.RL) return json({ error: "not_configured", message: "AI parsing is not configured." }, 503, c.headers);

    const len = parseInt(request.headers.get("Content-Length") || "0", 10);
    if (len > MAX_BODY_BYTES) return json({ error: "too_large", message: "Document chunk too large." }, 413, c.headers);
    let body;
    try { const raw = await request.text(); if (raw.length > MAX_BODY_BYTES) throw new Error("big"); body = JSON.parse(raw); }
    catch { return json({ error: "bad_request", message: "Invalid JSON body." }, 400, c.headers); }
    const bad = validateMessages(body && body.messages);
    if (bad) return json({ error: "bad_request", message: bad }, 400, c.headers);

    // ---- limits: per-IP (hour + day) and global daily dollar cap (reserve first, settle after) ----
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const kv = env.RL;
    const capMicro = Math.round(num(env.DAILY_CAP_USD, 0.5) * 1e6);
    const ipHourMax = num(env.IP_HOURLY_CALLS, 10), ipDayMax = num(env.IP_DAILY_CALLS, 30);
    const kH = `ip:${ip}:${hour()}`, kD = `ipd:${ip}:${day()}`, kC = `cost:${day()}`;
    const [h, d, spent] = await Promise.all([kvInt(kv, kH), kvInt(kv, kD), kvInt(kv, kC)]);
    if (h >= ipHourMax || d >= ipDayMax) return json({ error: "rate_limited", message: "Demo limit reached for your connection. Try again later." }, 429, { ...c.headers, "Retry-After": "3600" });
    const inChars = messages_chars(body.messages);
    const reserve = Math.round((inChars / 3.5) * PRICE_IN + 6000 * PRICE_OUT);      // micro-USD: input estimate + typical output
    if (spent + reserve > capMicro) return json({ error: "daily_cap", message: "The AI demo has reached today's usage limit." }, 429, { ...c.headers, "Retry-After": "21600" });
    await Promise.all([kvPut(kv, kH, h + 1, 7200), kvPut(kv, kD, d + 1, 172800), kvPut(kv, kC, spent + reserve, 172800)]);

    let actual = reserve, res;
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), UPSTREAM_TIMEOUT_MS);
      res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST", signal: ctl.signal,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.OPENAI_API_KEY}` },
        body: JSON.stringify({ model: MODEL, temperature: 0, max_tokens: MAX_OUT_TOKENS, response_format: { type: "json_object" }, messages: body.messages }),
      });
      clearTimeout(timer);
    } catch (e) {
      return json({ error: "upstream_unreachable", message: "AI service did not respond." }, 502, c.headers);
    }
    let data = null;
    try { data = await res.json(); } catch { /* handled below */ }
    if (!res.ok || !data || !data.choices || !data.choices[0]) {
      const quota = res.status === 429 || res.status === 401 || res.status === 403;   // never leak upstream detail
      return json({ error: quota ? "upstream_quota" : "upstream_error", message: "AI service unavailable." }, 502, c.headers);
    }
    const u = data.usage || {};
    actual = Math.round((u.prompt_tokens || 0) * PRICE_IN + (u.completion_tokens || 0) * PRICE_OUT);
    try {
      const now = await kvInt(kv, kC);
      await kvPut(kv, kC, now - reserve + actual, 172800);          // settle reservation with the real cost
    } catch { /* cap stays conservative */ }
    return json({ content: data.choices[0].message?.content ?? "", model: MODEL, usage: { prompt_tokens: u.prompt_tokens || 0, completion_tokens: u.completion_tokens || 0 }, prompt_version: PROMPT_VERSION }, 200, c.headers);
  },
};

function messages_chars(ms) { return ms.reduce((n, m) => n + m.content.length, 0); }
