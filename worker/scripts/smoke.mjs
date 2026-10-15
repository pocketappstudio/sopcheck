// Smoke test against a deployed Worker: node scripts/smoke.mjs <worker-url> [origin]
// Does: /health, a forbidden-origin request (must be 403), a malformed request (must be 400), and ONE real parse of a generic sample.
import { PROMPTS } from "../src/prompts.js";
const url = (process.argv[2] || "").replace(/\/$/, ""); const origin = process.argv[3] || "https://pocketappstudio.github.io";
if (!url) { console.error("usage: smoke.mjs <worker-url> [origin]"); process.exit(2); }
const render = (t, kv) => Object.entries(kv).reduce((o, [k, v]) => o.replace("{" + k + "}", String(v)), t);
const sample = "Compressor Room Weekly Check\n1. Operator: inspect the air compressor oil level and record it in the log.\n2. Operator: drain the receiver tank condensate valve and confirm the drain is clear.\n3. Supervisor: sign the weekly inspection sheet.";
const messages = [{ role: "system", content: PROMPTS.system }, { role: "user", content: render(PROMPTS.user, { company_name: "Demo Company", document_title: "Smoke test", chunk_index: 1, chunk_total: 1, section_context: "None", task_id_start: "001", document_text: sample }) }];
let bad = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) bad++; };
const h = await (await fetch(url + "/health", { headers: { Origin: origin } })).json();
check(h.ok && h.key_configured && h.ai_available, `health ${JSON.stringify(h)}`);
const evil = await fetch(url + "/parse", { method: "POST", headers: { Origin: "https://evil.example", "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });
check(evil.status === 403, `foreign origin rejected (${evil.status})`);
const junk = await fetch(url + "/parse", { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify({ messages: [{ role: "user", content: "hi" }] }) });
check(junk.status === 400, `non-SOP request rejected (${junk.status})`);
const pre = await fetch(url + "/parse", { method: "OPTIONS", headers: { Origin: origin, "Access-Control-Request-Method": "POST" } });
check(pre.status === 204 && pre.headers.get("access-control-allow-origin") === origin, "CORS preflight ok");
const r = await fetch(url + "/parse", { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });
const j = await r.json(); let n = 0; try { n = JSON.parse(j.content).tasks.length; } catch { /* */ }
check(r.status === 200 && n >= 2, `real gpt-4.1 parse returned ${n} tasks, usage ${JSON.stringify(j.usage)}`);
process.exit(bad ? 1 : 0);
