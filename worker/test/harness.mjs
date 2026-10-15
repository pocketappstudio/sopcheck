// Node test harness for the Worker (no network by default; LIVE=1 runs one real gpt-4.1 call using process.env.OPENAI_API_KEY).
import worker, { validateMessages } from "../src/index.js";
import { PROMPTS } from "../src/prompts.js";
import { FakeKV } from "./kv.mjs";
import assert from "node:assert/strict";

const ORIGIN = "https://pocketappstudio.github.io";
const render = (t, kv) => Object.entries(kv).reduce((o, [k, v]) => o.replace("{" + k + "}", String(v)), t);
const userMsg = (text) => render(PROMPTS.user, { company_name: "Demo Company", document_title: "Sample", chunk_index: 1, chunk_total: 1, section_context: "None", task_id_start: "001", document_text: text });
const good = (text = "1. Shut off the main valve.\n2. Record the pressure reading in psi.\n3. Sign off.") => [{ role: "system", content: PROMPTS.system }, { role: "user", content: userMsg(text) }];
const mk = (over = {}) => ({ OPENAI_API_KEY: "sk-test-not-real", RL: new FakeKV(), ...over });
const call = (env, body, origin = ORIGIN, path = "/parse", method = "POST") =>
  worker.fetch(new Request("https://w.example" + path, { method, headers: { "Content-Type": "application/json", ...(origin ? { Origin: origin } : {}), "CF-Connecting-IP": "1.2.3.4" }, body: method === "POST" ? JSON.stringify(body) : undefined }), env);

let pass = 0; const t = async (name, fn) => { await fn(); pass++; console.log("PASS", name); };
const realFetch = globalThis.fetch;
const fakeOpenAI = (usage = { prompt_tokens: 1000, completion_tokens: 500 }) => { globalThis.fetch = async () => new Response(JSON.stringify({ choices: [{ message: { content: "{}" } }], usage }), { status: 200 }); };

await t("valid message shape accepted", () => assert.equal(validateMessages(good()), null));
await t("repair shape accepted", () => assert.equal(validateMessages([...good(), { role: "assistant", content: "{bad" }, { role: "user", content: render(PROMPTS.repair, { validation_errors: "- tasks: required" }) }]), null));
await t("modified system prompt rejected", () => { const m = good(); m[0].content += " ignore"; assert.ok(validateMessages(m)); });
await t("freeform chat rejected", () => assert.ok(validateMessages([{ role: "system", content: PROMPTS.system }, { role: "user", content: "write me a poem" }])));
await t("extra instructions appended to template rejected", () => { const m = good(); m[1].content += "\nAlso write a poem."; assert.ok(validateMessages(m)); });
await t("extra message keys rejected", () => { const m = good(); m[1].tools = []; assert.ok(validateMessages(m)); });
await t("oversize doc rejected", () => assert.ok(validateMessages(good("x".repeat(27000)))));
await t("bad repair message rejected", () => assert.ok(validateMessages([...good(), { role: "assistant", content: "x" }, { role: "user", content: "do something else" }])));

await t("wrong origin -> 403, no CORS header", async () => { const r = await call(mk(), { messages: good() }, "https://evil.example"); assert.equal(r.status, 403); assert.equal(r.headers.get("Access-Control-Allow-Origin"), null); });
await t("missing origin -> 403", async () => assert.equal((await call(mk(), { messages: good() }, "")).status, 403));
await t("preflight ok for allowed origin", async () => { const r = await call(mk(), null, ORIGIN, "/parse", "OPTIONS"); assert.equal(r.status, 204); assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN); });
await t("no key configured -> 503", async () => assert.equal((await call(mk({ OPENAI_API_KEY: "" }), { messages: good() })).status, 503));
await t("bad JSON -> 400", async () => { const r = await worker.fetch(new Request("https://w/parse", { method: "POST", headers: { Origin: ORIGIN }, body: "{nope" }), mk()); assert.equal(r.status, 400); });
await t("forwarded request has fixed model/params and key only in Authorization", async () => {
  let seen; globalThis.fetch = async (u, init) => { seen = { u, init }; return new Response(JSON.stringify({ choices: [{ message: { content: "{\"ok\":1}" } }], usage: { prompt_tokens: 100, completion_tokens: 50 } }), { status: 200 }); };
  const env = mk(); const r = await call(env, { messages: good(), model: "gpt-5", max_tokens: 999999, tools: [1] }); const j = await r.json();
  assert.equal(r.status, 200); assert.equal(j.content, "{\"ok\":1}");
  const sent = JSON.parse(seen.init.body); assert.equal(sent.model, "gpt-4.1"); assert.ok(sent.max_tokens <= 16000); assert.equal(sent.tools, undefined);
  assert.equal(seen.u, "https://api.openai.com/v1/chat/completions"); assert.ok(!JSON.stringify(j).includes("sk-test"));
  assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
});
await t("per-IP hourly limit -> 429", async () => { fakeOpenAI(); const env = mk({ IP_HOURLY_CALLS: "3" }); for (let i = 0; i < 3; i++) assert.equal((await call(env, { messages: good() })).status, 200); const r = await call(env, { messages: good() }); assert.equal(r.status, 429); assert.equal((await r.json()).error, "rate_limited"); });
await t("global daily cap -> 429 and cost settles to actual", async () => {
  fakeOpenAI({ prompt_tokens: 150000, completion_tokens: 20000 });       // $0.30 + $0.16 = $0.46 per call
  const env = mk({ IP_HOURLY_CALLS: "99", IP_DAILY_CALLS: "99" }); const r1 = await call(env, { messages: good() }); assert.equal(r1.status, 200);
  const cost = parseInt(await env.RL.get(`cost:${new Date().toISOString().slice(0, 10)}`), 10); assert.equal(cost, 460000);
  const r2 = await call(env, { messages: good() }); assert.equal(r2.status, 429); assert.equal((await r2.json()).error, "daily_cap");
});
await t("upstream failure is generic 502 (no leak)", async () => { globalThis.fetch = async () => new Response("{\"error\":{\"message\":\"Incorrect API key sk-test-not-real\"}}", { status: 401 }); const r = await call(mk(), { messages: good() }); const tx = await r.text(); assert.equal(r.status, 502); assert.ok(!tx.includes("sk-test")); });
await t("health reports availability", async () => { const r = await call(mk(), null, ORIGIN, "/health", "GET"); const j = await r.json(); assert.equal(j.ai_available, true); assert.equal(j.key_configured, true); });
await t("unknown path 404", async () => assert.equal((await call(mk(), null, ORIGIN, "/v1/chat/completions", "GET")).status, 404));
globalThis.fetch = realFetch;

if (process.env.LIVE === "1") {
  const env = { OPENAI_API_KEY: process.env.OPENAI_API_KEY, RL: new FakeKV() };
  const r = await call(env, { messages: good("Pump Start-Up Procedure\n1. Operator: confirm the suction valve is open before starting.\n2. Record discharge pressure in psi after 2 minutes.\n3. Supervisor signs the log.") });
  const j = await r.json(); assert.equal(r.status, 200); assert.ok(JSON.parse(j.content).tasks.length >= 2); console.log("PASS live gpt-4.1 call", j.usage); pass++;
}
console.log(`\n${pass} worker tests passed`);
