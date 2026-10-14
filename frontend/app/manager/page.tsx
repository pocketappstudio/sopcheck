"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Billing,
  api, ApiError, VERIFICATION_METHODS, type AuditEntry, type Company, type DocDetail, type DocumentInfo,
  type EditableTask, type SafetyFlag, type VerificationMethod,
} from "@/lib/api";
import { RequireAuth } from "@/components/auth";
import { AiDraftBanner, PublishAttestation, SiteFooter } from "@/components/legal";
import { Badge, Nav, Spinner, VMBadge } from "@/components/ui";
import { formatDateTime, viewerZone } from "@/lib/time";

type Tab = "checklist" | "safety" | "json" | "audit";
const FIELDS: (keyof Omit<EditableTask, "_key" | "id" | "task_id" | "is_safety_critical">)[] =
  ["task_title", "description", "assigned_position", "site_requirement", "timeframe", "verification_method"];

let keySeq = 0;
const newKey = () => `k${++keySeq}`;

export default function ManagerPage() {
  return <RequireAuth roles={["manager", "admin"]}><ManagerDashboard /></RequireAuth>;
}

function ManagerDashboard() {
  const [mode, setMode] = useState("…");
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [cur, setCur] = useState<DocDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("checklist");
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<EditableTask[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, string[]>>({});
  const [publishGate, setPublishGate] = useState<SafetyFlag[] | null>(null);
  const [attest, setAttest] = useState<{ force: boolean; reason?: string } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => api.documents().then(setDocs).catch((e) => setError(e.message)), []);
  useEffect(() => {
    api.health().then((h) => setMode(h.parser_mode === "llm" ? `LLM · ${h.model}` : "Mock parser (offline)")).catch(() => setMode("API offline"));
    refresh();
  }, [refresh]);

  const load = useCallback((d: DocDetail) => {
    setCur(d); setEditing(false); setRowErrors({}); setPublishGate(null); setAttest(null);
    setRows(d.tasks.map((t) => ({ ...t, _key: newKey() })));
  }, []);

  async function run<T>(label: string, p: Promise<T>, after?: (v: T) => void) {
    setBusy(label); setError(null);
    try { const v = await p; after?.(v); await refresh(); return v; }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  const onFile = (f?: File | null) => f && run("upload", api.upload(f), (d) => { load(d); setTab("checklist"); });

  // ---- editing ----
  function validate(rs: EditableTask[]) {
    const errs: Record<string, string[]> = {};
    rs.forEach((r) => {
      const e: string[] = [];
      for (const f of FIELDS) if (!String(r[f] ?? "").trim()) e.push(`${f.replace("_", " ")} is required`);
      if (!VERIFICATION_METHODS.includes(r.verification_method)) e.push("verification method must be from the controlled list");
      if (r.task_title.length > 120) e.push("title must be ≤ 120 characters");
      if (e.length) errs[r._key] = e;
    });
    if (!rs.length) errs["_all"] = ["a checklist needs at least one task"];
    return errs;
  }
  const update = (key: string, field: string, value: string) => setRows((rs) => rs.map((r) => (r._key === key ? { ...r, [field]: value } : r)));
  const move = (i: number, d: number) => setRows((rs) => { const n = [...rs]; const j = i + d; if (j < 0 || j >= n.length) return rs; [n[i], n[j]] = [n[j], n[i]]; return n; });
  const remove = (key: string) => setRows((rs) => rs.filter((r) => r._key !== key));
  const add = () => setRows((rs) => [...rs, { _key: newKey(), task_title: "", description: "", assigned_position: rs.at(-1)?.assigned_position ?? "Unspecified",
    site_requirement: rs.at(-1)?.site_requirement ?? "Unspecified", timeframe: rs.at(-1)?.timeframe ?? "Unspecified", verification_method: "Checkbox" }]);

  async function save() {
    if (!cur) return;
    const errs = validate(rows);
    setRowErrors(errs);
    if (Object.keys(errs).length) { setError("Fix the highlighted fields before saving."); return; }
    await run("save", api.saveChecklist(cur.document.id, cur.run.id, rows.map((r) => ({ id: r.id, ...Object.fromEntries(FIELDS.map((f) => [f, r[f]])) }) as Omit<EditableTask, "_key">)), load);
  }
  function cancel() { if (cur) load(cur); }

  // live JSON preview reflects unsaved edits (renumbered exactly like the server will)
  const previewJson = useMemo(() => {
    if (!cur) return null;
    if (!editing) return cur.checklist;
    return { industry: cur.checklist.industry, document_summary: cur.checklist.document_summary,
      tasks: rows.map((r, i) => ({ task_id: `T-${String(i + 1).padStart(3, "0")}`, ...Object.fromEntries(FIELDS.map((f) => [f, r[f]])) })) };
  }, [cur, editing, rows]);

  async function publish(force = false, reason?: string) {
    if (!cur) return;
    setBusy("publish"); setError(null); setAttest(null);
    try { load(await api.publish(cur.document.id, force, reason, true)); await refresh(); }
    catch (e) {
      if (e instanceof ApiError && e.status === 409 && typeof e.detail === "object" && e.detail && "unresolved" in e.detail) {
        setPublishGate((e.detail as { unresolved: SafetyFlag[] }).unresolved);
      } else setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  }

  const s = cur?.safety.summary;
  const open = cur?.safety.flags.filter((f) => f.coverage !== "covered" && !f.resolution) ?? [];
  const draft = cur?.versions.find((v) => v.version_status === "draft");
  const live = cur?.versions.find((v) => v.version_status === "published");
  const options = useMemo(() => {
    const t = cur?.tasks ?? [];
    return { positions: Array.from(new Set(t.map((x) => x.assigned_position))), sites: Array.from(new Set(t.map((x) => x.site_requirement))),
      timeframes: Array.from(new Set(t.map((x) => x.timeframe))) };
  }, [cur]);

  return (
    <>
      <Nav active="manager" />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Manager Dashboard</h1>
            <p className="text-sm text-slate-500">Upload an SOP / Scope of Work, review & edit the checklist, resolve safety flags, publish.</p>
          </div>
          <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600 ring-1 ring-slate-200">Parser: <b className="text-slate-800">{mode}</b></span>
        </div>
        <AiDraftBanner />

        <div className="mt-5 grid gap-5 lg:grid-cols-[300px_1fr]">
          <section className="space-y-4">
            <div onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
                 onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files?.[0]); }} onClick={() => fileRef.current?.click()}
                 className={`cursor-pointer rounded-2xl border-2 border-dashed p-6 text-center transition ${dragOver ? "border-teal-500 bg-teal-50" : "border-slate-300 bg-white hover:border-teal-400"}`}>
              <div className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-slate-800 text-2xl text-white">{busy === "upload" ? <Spinner /> : "⇪"}</div>
              <div className="mt-3 font-semibold text-slate-800">{busy === "upload" ? "Parsing (OCR if scanned)…" : "Drop SOP / SOW here"}</div>
              <div className="text-xs text-slate-500">.txt, .md or .pdf (scanned PDFs are OCR&apos;d)</div>
              <input ref={fileRef} type="file" accept=".txt,.md,.pdf" className="hidden" data-testid="file-input" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
            </div>
            {error && <div className="rounded-xl bg-red-50 p-3 text-sm text-red-700 ring-1 ring-red-200" role="alert">{error}</div>}
            <div className="rounded-2xl bg-white p-3 ring-1 ring-slate-200">
              <div className="px-1 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Documents</div>
              {docs.length === 0 && <div className="px-1 py-3 text-sm text-slate-400">No documents yet.</div>}
              <ul className="space-y-1" data-testid="doc-list">
                {docs.map((d) => (
                  <li key={d.id}>
                    <button onClick={() => run("open", api.document(d.id), load)} className={`w-full rounded-lg px-2 py-2 text-left transition hover:bg-slate-50 ${cur?.document.id === d.id ? "bg-slate-100" : ""}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-800">{d.title}</span>
                        <StatusPill d={d} />
                      </div>
                      <div className="flex items-center gap-1 text-xs text-slate-400">
                        <span className="truncate">{d.industry} · {d.task_count} tasks</span>
                        {d.extraction_method === "ocr" || d.extraction_method === "mixed" ? <span className="rounded bg-violet-100 px-1 text-[10px] font-bold text-violet-700">OCR</span> : null}
                        {d.safety && d.safety.unresolved > 0 && <span className="rounded bg-orange-100 px-1 text-[10px] font-bold text-orange-700">⚠ {d.safety.unresolved}</span>}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <BillingPanel />
            <CompanySettings />
          </section>

          <section className="min-w-0">
            {!cur ? (
              <div className="grid h-full min-h-[320px] place-items-center rounded-2xl bg-white p-10 text-center text-slate-400 ring-1 ring-slate-200">
                Upload a document or select one to review its checklist.
              </div>
            ) : (
              <div className="space-y-4">
                <div className="rounded-2xl bg-white p-5 ring-1 ring-slate-200">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-semibold text-slate-900">{cur.document.title}</h2>
                        <Badge tone="indigo">{cur.checklist.industry}</Badge>
                        <OcrBadge d={cur.document} />
                      </div>
                      <p className="mt-1 text-sm text-slate-600">{cur.checklist.document_summary}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" data-testid="versions">
                        {cur.versions.map((v) => (
                          <button key={v.id} onClick={() => run("open", api.document(cur.document.id, v.id), load)}
                                  className={`rounded-full px-2 py-0.5 font-semibold ring-1 ${v.id === cur.run.id ? "ring-slate-800" : "ring-transparent"} ${
                                    v.version_status === "published" ? "bg-emerald-100 text-emerald-700" : v.version_status === "draft" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"}`}>
                            v{v.version_no} · {v.version_status}
                          </button>
                        ))}
                        {live && draft && <span className="text-slate-400">Technicians see v{live.version_no} until v{draft.version_no} is published.</span>}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {cur.editable && !editing && (
                        <button onClick={() => { setEditing(true); setTab("checklist"); }} data-testid="edit"
                                className="rounded-xl bg-white px-4 py-2 text-sm font-semibold text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50">
                          ✎ {cur.run.version_status === "published" ? "Edit (creates new draft)" : "Edit"}
                        </button>
                      )}
                      {editing && <>
                        <button onClick={cancel} className="rounded-xl bg-white px-4 py-2 text-sm font-semibold text-slate-600 ring-1 ring-slate-300">Cancel</button>
                        <button onClick={save} disabled={busy === "save"} data-testid="save" className="rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
                          {busy === "save" ? <Spinner /> : "Save changes"}
                        </button>
                      </>}
                      {!editing && cur.run.version_status === "draft" && (
                        <button onClick={() => { setPublishGate(null); setAttest({ force: false }); }} disabled={!!busy} data-testid="publish"
                                className="rounded-xl bg-teal-500 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-teal-600 disabled:opacity-50">
                          Publish v{cur.run.version_no}
                        </button>
                      )}
                      {!editing && cur.run.version_status === "published" && <span className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-white">✓ Live v{cur.run.version_no}</span>}
                      {!editing && (
                        <div className="flex items-center gap-1 rounded-xl px-2 text-xs text-slate-500 ring-1 ring-slate-200" title="Exports include the SOPCheck disclaimer">
                          Export {(["csv", "md", "json"] as const).map((f) => (
                            <button key={f} onClick={() => api.download(cur.document.id, f, cur.run.id).catch((e) => setError(e.message))} data-testid={`export-${f}`} className="rounded px-1.5 py-1 font-semibold text-slate-700 hover:bg-slate-100">.{f}</button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  {publishGate && <PublishGate flags={publishGate} onCancel={() => setPublishGate(null)} onReview={() => { setPublishGate(null); setTab("safety"); }}
                                               onForce={(reason) => { setPublishGate(null); setAttest({ force: true, reason }); }} />}
                  {attest && <PublishAttestation version={cur.run.version_no} busy={busy === "publish"} onCancel={() => setAttest(null)}
                                                 onConfirm={() => publish(attest.force, attest.reason)} />}
                  <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Stat label="Tasks" value={editing ? rows.length : cur.checklist.tasks.length} />
                    <Stat label="Parser" value={cur.run.parser.toUpperCase()} sub={cur.run.model ?? cur.run.prompt_version} />
                    <Stat label="Safety coverage" value={`${s?.coverage_pct ?? 100}%`} tone={open.length ? "text-orange-600" : "text-emerald-600"}
                          sub={s ? `${s.counts.covered} covered · ${s.counts.partial} partial · ${s.counts.missing} missing` : ""} />
                    <Stat label="Open safety flags" value={open.length} tone={open.length ? "text-orange-600" : "text-emerald-600"} sub={s?.method} />
                  </div>
                  {cur.run.notes && <p className="mt-3 text-xs text-slate-400">{cur.run.notes}</p>}
                </div>

                <div className="rounded-2xl bg-white ring-1 ring-slate-200">
                  <div className="flex flex-wrap gap-1 border-b border-slate-100 p-2">
                    {([["checklist", editing ? "Edit checklist" : "Checklist"], ["safety", `Safety review${open.length ? ` (${open.length})` : ""}`], ["json", "Raw JSON"], ["audit", "Audit log"]] as [Tab, string][]).map(([t, label]) => (
                      <button key={t} onClick={() => setTab(t)} data-testid={`tab-${t}`}
                              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${tab === t ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100"}`}>{label}</button>
                    ))}
                  </div>
                  {tab === "checklist" && (editing
                    ? <EditTable rows={rows} errors={rowErrors} options={options} update={update} move={move} remove={remove} add={add} />
                    : <ViewTable d={cur} />)}
                  {tab === "safety" && <SafetyPanel d={cur} busy={busy} onResolve={(f, action, code, reason) =>
                    run("resolve", api.resolveFlag(cur.document.id, f.id, action, code, reason), load)} />}
                  {tab === "json" && (
                    <div>
                      {editing && <div className="bg-amber-50 px-4 py-2 text-xs text-amber-800">Live preview of unsaved edits — task_ids are renumbered on save.</div>}
                      <pre data-testid="json" className="max-h-[560px] overflow-auto bg-slate-900 p-4 text-xs leading-relaxed text-emerald-200">{JSON.stringify(previewJson, null, 2)}</pre>
                    </div>
                  )}
                  {tab === "audit" && <AuditPanel docId={cur.document.id} stamp={cur.run.updated_at} />}
                </div>
              </div>
            )}
          </section>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}

function StatusPill({ d }: { d: DocumentInfo }) {
  if (d.published_run_id && d.draft_run_id) return <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-700">v{d.draft_version} draft</span>;
  if (d.published_run_id) return <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">live v{d.published_version}</span>;
  return <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-700">draft</span>;
}

function OcrBadge({ d }: { d: DocumentInfo }) {
  if (d.extraction_method !== "ocr" && d.extraction_method !== "mixed") return null;
  const low = (d.ocr_confidence ?? 0) < 70;
  return (
    <span data-testid="ocr-badge" title={(d.ocr_pages ?? []).map((p) => `page ${p.page}: ${p.confidence}% (${p.words} words)`).join("\n")}
          className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${low ? "bg-red-50 text-red-700" : "bg-violet-50 text-violet-700"}`}>
      📄 OCR{d.extraction_method === "mixed" ? " (some pages)" : ""} · {d.ocr_confidence}% confidence{low ? " — verify text" : ""}
    </span>
  );
}

function ViewTable({ d }: { d: DocDetail }) {
  return (
    <>
      <ul className="divide-y divide-slate-100 md:hidden">
        {d.tasks.map((t) => (
          <li key={t.id} className="p-3">
            <div className="flex items-start justify-between gap-2"><div className="font-medium text-slate-800">{t.task_title}</div><span className="font-mono text-[10px] text-slate-400">{t.task_id}</span></div>
            <p className="mt-0.5 text-xs text-slate-500">{t.description}</p>
            <div className="mt-2 flex flex-wrap gap-1"><Badge icon="👤">{t.assigned_position}</Badge><Badge icon="📍" tone="teal">{t.site_requirement}</Badge><Badge icon="⏱" tone="orange">{t.timeframe}</Badge><VMBadge vm={t.verification_method} /></div>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-400">
            <tr><th className="px-3 py-2">ID</th><th className="px-3 py-2">Task</th><th className="px-3 py-2">Position</th><th className="px-3 py-2">Site</th><th className="px-3 py-2">Timeframe</th><th className="px-3 py-2">Verify</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {d.tasks.map((t) => (
              <tr key={t.id} className="align-top">
                <td className="whitespace-nowrap px-3 py-3 font-mono text-xs text-slate-400">{t.task_id}{t.is_safety_critical && <div className="text-orange-500" title="safety-critical">⚠</div>}</td>
                <td className="px-3 py-3"><div className="font-medium text-slate-800">{t.task_title}</div><div className="mt-0.5 text-xs text-slate-500">{t.description}</div></td>
                <td className="w-32 px-3 py-3 text-xs"><Cell v={t.assigned_position} /></td>
                <td className="w-32 px-3 py-3 text-xs"><Cell v={t.site_requirement} /></td>
                <td className="w-28 px-3 py-3 text-xs"><Cell v={t.timeframe} /></td>
                <td className="whitespace-nowrap px-3 py-3"><VMBadge vm={t.verification_method} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function EditTable({ rows, errors, options, update, move, remove, add }: {
  rows: EditableTask[]; errors: Record<string, string[]>; options: { positions: string[]; sites: string[]; timeframes: string[] };
  update: (k: string, f: string, v: string) => void; move: (i: number, d: number) => void; remove: (k: string) => void; add: () => void;
}) {
  const inp = "w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100";
  return (
    <div className="p-3" data-testid="edit-table">
      <datalist id="dl-pos">{options.positions.map((o) => <option key={o} value={o} />)}<option value="Unspecified" /></datalist>
      <datalist id="dl-site">{options.sites.map((o) => <option key={o} value={o} />)}<option value="Unspecified" /></datalist>
      <datalist id="dl-time">{options.timeframes.map((o) => <option key={o} value={o} />)}<option value="Unspecified" /></datalist>
      {errors["_all"] && <div className="mb-2 text-sm text-red-600">{errors["_all"].join("; ")}</div>}
      <ol className="space-y-2">
        {rows.map((r, i) => (
          <li key={r._key} data-testid="edit-row" className={`rounded-xl p-3 ring-1 ${errors[r._key] ? "bg-red-50/40 ring-red-300" : r.id ? "bg-slate-50 ring-slate-200" : "bg-teal-50/40 ring-teal-300"}`}>
            <div className="flex items-center gap-2">
              <span className="w-12 shrink-0 font-mono text-xs text-slate-400">T-{String(i + 1).padStart(3, "0")}</span>
              <input aria-label="Task title" className={`${inp} font-medium`} value={r.task_title} placeholder="Action-oriented title" onChange={(e) => update(r._key, "task_title", e.target.value)} />
              <div className="flex shrink-0 gap-1">
                <IconBtn label="Move up" onClick={() => move(i, -1)} disabled={i === 0}>↑</IconBtn>
                <IconBtn label="Move down" onClick={() => move(i, 1)} disabled={i === rows.length - 1}>↓</IconBtn>
                <IconBtn label="Delete task" onClick={() => remove(r._key)} danger>✕</IconBtn>
              </div>
            </div>
            <textarea aria-label="Description" rows={2} className={`${inp} mt-2`} value={r.description} placeholder="Technician instruction (keep safety details verbatim)" onChange={(e) => update(r._key, "description", e.target.value)} />
            <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
              <input aria-label="Position" list="dl-pos" className={inp} value={r.assigned_position} onChange={(e) => update(r._key, "assigned_position", e.target.value)} />
              <input aria-label="Site" list="dl-site" className={inp} value={r.site_requirement} onChange={(e) => update(r._key, "site_requirement", e.target.value)} />
              <input aria-label="Timeframe" list="dl-time" className={inp} value={r.timeframe} onChange={(e) => update(r._key, "timeframe", e.target.value)} />
              <select aria-label="Verification method" className={inp} value={r.verification_method} onChange={(e) => update(r._key, "verification_method", e.target.value as VerificationMethod)}>
                {VERIFICATION_METHODS.map((v) => <option key={v}>{v}</option>)}
              </select>
            </div>
            {errors[r._key] && <div className="mt-1 text-xs text-red-600">{errors[r._key].join("; ")}</div>}
          </li>
        ))}
      </ol>
      <button onClick={add} data-testid="add-task" className="mt-3 w-full rounded-xl border-2 border-dashed border-slate-300 py-2 text-sm font-semibold text-slate-500 hover:border-teal-400 hover:text-teal-600">+ Add task</button>
    </div>
  );
}

function IconBtn({ children, label, onClick, disabled, danger }: { children: React.ReactNode; label: string; onClick: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} disabled={disabled}
            className={`grid h-8 w-8 place-items-center rounded-lg text-sm font-bold ring-1 disabled:opacity-30 ${danger ? "text-red-600 ring-red-200 hover:bg-red-50" : "text-slate-600 ring-slate-200 hover:bg-white"}`}>
      {children}
    </button>
  );
}

const COV_STYLE: Record<string, string> = {
  covered: "bg-emerald-100 text-emerald-700", partial: "bg-amber-100 text-amber-800", missing: "bg-red-100 text-red-700",
};

function SafetyPanel({ d, busy, onResolve }: { d: DocDetail; busy: string | null;
  onResolve: (f: SafetyFlag, a: "attach" | "create_task" | "dismiss", code?: string, reason?: string) => void }) {
  const [showAll, setShowAll] = useState(false);
  const flags = d.safety.flags.filter((f) => showAll || f.coverage !== "covered" || f.resolution);
  return (
    <div className="p-3" data-testid="safety-panel">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-xs text-slate-500">
          Every safety-critical sentence in the source (warnings, PPE, isolation, thresholds, hold points, hazards) is matched against the tasks
          by normalized numbers/units/comparators, regulation ids and PPE, plus fuzzy text similarity.
        </p>
        <label className="flex items-center gap-1 text-xs text-slate-500"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show covered ({d.safety.flags.length} spans)</label>
      </div>
      {flags.length === 0 && <div className="rounded-xl bg-emerald-50 p-4 text-sm text-emerald-700">✓ All {d.safety.flags.length} safety-critical spans are covered by the checklist.</div>}
      <ul className="space-y-2">
        {flags.map((f) => <FlagCard key={f.id} f={f} d={d} busy={busy} onResolve={onResolve} />)}
      </ul>
    </div>
  );
}

function FlagCard({ f, d, busy, onResolve }: { f: SafetyFlag; d: DocDetail; busy: string | null;
  onResolve: (f: SafetyFlag, a: "attach" | "create_task" | "dismiss", code?: string, reason?: string) => void }) {
  const [code, setCode] = useState(f.best_task_code ?? d.tasks[0]?.task_id ?? "");
  const [reason, setReason] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const openFlag = f.coverage !== "covered" && !f.resolution;
  return (
    <li data-testid="flag" className={`rounded-xl p-3 ring-1 ${openFlag ? (f.coverage === "missing" ? "ring-red-200" : "ring-amber-200") : "ring-slate-200"}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${COV_STYLE[f.coverage]}`}>{f.coverage}</span>
        {f.resolution && <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-bold uppercase text-white">{f.resolution.replace("_", " ")}{f.resolution_task_code ? ` → ${f.resolution_task_code}` : ""}</span>}
        {f.categories.map((c) => <span key={c} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">{c.replace("_", " ")}</span>)}
        <span className="ml-auto text-[10px] text-slate-400">best match {f.best_task_code ?? "—"} · score {f.score}</span>
      </div>
      <p className="mt-1.5 text-sm text-slate-800">“{f.span_text}”</p>
      {f.section && <p className="text-[11px] text-slate-400">Section: {f.section}</p>}
      {(f.missing_atoms.length > 0 || f.matched_atoms.length > 0) && (
        <div className="mt-1 flex flex-wrap gap-1 text-[11px]">
          {f.matched_atoms.map((a) => <span key={"m" + a} className="rounded bg-emerald-50 px-1.5 text-emerald-700">✓ {a}</span>)}
          {f.missing_atoms.map((a) => <span key={"x" + a} className="rounded bg-red-50 px-1.5 text-red-700">✕ {a}</span>)}
        </div>
      )}
      {f.resolution === "dismissed" && <p className="mt-1 text-xs italic text-slate-500">Dismissed: {f.resolution_reason}</p>}
      {openFlag && d.editable && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select value={code} onChange={(e) => setCode(e.target.value)} aria-label="Attach to task" className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs">
            {d.tasks.map((t) => <option key={t.id} value={t.task_id}>{t.task_id} · {t.task_title.slice(0, 40)}</option>)}
          </select>
          <button disabled={!!busy} onClick={() => onResolve(f, "attach", code)} data-testid="flag-attach" className="rounded-lg bg-teal-500 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">Attach to task</button>
          <button disabled={!!busy} onClick={() => onResolve(f, "create_task", code)} data-testid="flag-create" className="rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-300 disabled:opacity-50">Create task after</button>
          {!dismissing ? (
            <button onClick={() => setDismissing(true)} data-testid="flag-dismiss" className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100">Dismiss…</button>
          ) : (
            <span className="flex w-full items-center gap-2">
              <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required, logged)" data-testid="dismiss-reason"
                     className="flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs" />
              <button disabled={reason.trim().length < 5 || !!busy} onClick={() => onResolve(f, "dismiss", undefined, reason)} data-testid="dismiss-confirm"
                      className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">Dismiss</button>
            </span>
          )}
        </div>
      )}
    </li>
  );
}

function PublishGate({ flags, onCancel, onReview, onForce }: { flags: SafetyFlag[]; onCancel: () => void; onReview: () => void; onForce: (r: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <div className="mt-3 rounded-xl bg-orange-50 p-3 text-sm text-orange-900 ring-1 ring-orange-200" role="alertdialog">
      <b>{flags.length} unresolved safety flag(s).</b> Resolve them in Safety review, or publish anyway with a logged reason.
      <div className="mt-2 flex flex-wrap gap-2">
        <button onClick={onReview} className="rounded-lg bg-orange-500 px-3 py-1.5 text-xs font-semibold text-white">Review flags</button>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Override reason" className="min-w-[180px] flex-1 rounded-lg border border-orange-200 px-2 py-1.5 text-xs" />
        <button disabled={reason.trim().length < 5} onClick={() => onForce(reason)} className="rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-orange-800 ring-1 ring-orange-300 disabled:opacity-40">Publish anyway</button>
        <button onClick={onCancel} className="px-2 text-xs text-orange-700">Cancel</button>
      </div>
    </div>
  );
}

function AuditPanel({ docId, stamp }: { docId: number; stamp: string }) {
  const [rows, setRows] = useState<AuditEntry[] | null>(null);
  useEffect(() => { api.audit(docId).then(setRows).catch(() => setRows([])); }, [docId, stamp]);
  if (!rows) return <div className="p-4 text-sm text-slate-400"><Spinner /> Loading…</div>;
  return (
    <div className="p-3" data-testid="audit-panel">
      <p className="px-1 pb-2 text-[11px] text-slate-400">Times shown in your browser timezone ({viewerZone()}).</p>
      <ul className="divide-y divide-slate-100 text-sm">
        {rows.map((r) => (
          <li key={r.id} className="flex gap-3 px-1 py-2">
            <span className="w-40 shrink-0 text-xs text-slate-400" data-testid="audit-time">{formatDateTime(r.created_at)}</span>
            <span className="w-28 shrink-0 font-mono text-xs font-semibold text-slate-700">{r.action}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-slate-500" title={JSON.stringify(r.data)}>{summarize(r)}</span>
            <span className="shrink-0 text-xs text-slate-400">{r.actor}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function summarize(r: AuditEntry): string {
  const d = r.data ?? {};
  if (r.action === "task_update") return `${d.task_code}: ${Object.keys((d.changes as object) ?? {}).join(", ")} changed`;
  if (r.action === "task_add") return `added "${d.task_title}" as ${d.new_task_code}`;
  if (r.action === "task_delete") return `deleted ${d.task_code} "${d.task_title}"`;
  if (r.action === "publish") return `published v${d.version_no}${d.forced_with_unresolved ? ` (override: ${d.reason})` : ""}${d.attestation ? ` · attested: "${d.attestation}"` : ""}`;
  if (r.action === "export") return `exported .${d.format}`;
  if (r.action === "version_create") return `v${d.version_no} draft created from v${d.from_version}`;
  if (["attach", "create_task", "dismiss"].includes(r.action)) return `${String(d.span).slice(0, 70)}${d.task_code ? ` → ${d.task_code}` : ""}${d.reason ? ` — ${d.reason}` : ""}`;
  return JSON.stringify(d).slice(0, 120);
}

const ZONES = ["America/Chicago", "America/New_York", "America/Denver", "America/Phoenix", "America/Los_Angeles", "America/Anchorage",
  "Pacific/Honolulu", "UTC", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore", "Australia/Sydney"];

function CompanySettings() {
  const [c, setC] = useState<Company | null>(null);
  const [tz, setTz] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => { api.company().then((x) => { setC(x); setTz(x.timezone); }).catch(() => {}); }, []);
  if (!c) return null;
  async function save() { const x = await api.updateCompany({ timezone: tz }); setC(x); setSaved(true); setTimeout(() => setSaved(false), 1500); }
  return (
    <div className="rounded-2xl bg-white p-3 ring-1 ring-slate-200" data-testid="company-settings">
      <div className="px-1 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Company · {c.name}</div>
      <label className="block px-1 text-[11px] text-slate-500">Timezone for shift windows</label>
      <div className="mt-1 flex gap-2">
        <select value={tz} onChange={(e) => setTz(e.target.value)} className="flex-1 rounded-lg border border-slate-200 px-2 py-1.5 text-sm">
          {Array.from(new Set([c.timezone, ...ZONES])).map((z) => <option key={z}>{z}</option>)}
        </select>
        <button disabled={tz === c.timezone} onClick={save} className="rounded-lg bg-slate-800 px-3 text-xs font-semibold text-white disabled:opacity-30">{saved ? "✓" : "Save"}</button>
      </div>
      <ul className="mt-2 space-y-0.5 px-1 text-[11px] text-slate-500">
        {Object.entries(c.shift_windows).map(([k, [a, b]]) => <li key={k} className="flex justify-between"><span>{k}</span><span className="font-mono">{a}–{b}</span></li>)}
      </ul>
    </div>
  );
}

function Stat({ label, value, sub, tone = "text-slate-900" }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`text-xl font-bold ${tone}`}>{value}</div>
      {sub && <div className="truncate text-[11px] text-slate-400" title={sub}>{sub}</div>}
    </div>
  );
}

function Cell({ v }: { v: string }) {
  return v === "Unspecified" ? <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">Unspecified</span> : <span className="text-slate-700">{v}</span>;
}


function BillingPanel() {
  const [b, setB] = useState<Billing | null>(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.billing().then(setB).catch(() => {}); }, []);
  async function activate() {
    setBusy(true); setMsg(null);
    try { const r = await api.activateLicense(key.trim()); setB(r); setKey(""); setMsg({ ok: true, text: `Activated: ${r.plan_name}` }); }
    catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); } finally { setBusy(false); }
  }
  if (!b) return null;
  const bar = (used: number, max: number) => (
    <div className="mt-1 h-1.5 rounded-full bg-slate-100"><div className={`h-1.5 rounded-full ${used >= max ? "bg-red-500" : "bg-teal-500"}`} style={{ width: `${Math.min(100, (100 * used) / Math.max(1, max))}%` }} /></div>
  );
  return (
    <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200" data-testid="billing">
      <div className="flex items-center justify-between">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Billing</div>
        <Link href="/pricing" className="text-xs font-semibold text-teal-700 underline">Plans</Link>
      </div>
      <div className="mt-1 text-sm font-semibold text-slate-800">{b.plan_name}{b.trial_days_left != null && <span className="font-normal text-slate-500"> · {b.trial_days_left} days left</span>}</div>
      {!b.active && <div className="mt-1 rounded bg-red-50 p-2 text-xs text-red-700">Your trial or membership has ended. Existing checklists stay available; parsing new documents needs a plan.</div>}
      <div className="mt-2 text-xs text-slate-600">Technicians {b.usage.technicians}/{b.limits.max_technicians}{bar(b.usage.technicians, b.limits.max_technicians)}</div>
      <div className="mt-2 text-xs text-slate-600">Parses this month {b.usage.parses_this_month}/{b.limits.max_parses_month}{bar(b.usage.parses_this_month, b.limits.max_parses_month)}</div>
      {b.license.last4 && <div className="mt-2 text-[11px] text-slate-400">License …{b.license.last4} · {b.license.status}</div>}
      <div className="mt-3 flex gap-1">
        <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="Gumroad license key" aria-label="Gumroad license key"
               className="min-w-0 flex-1 rounded-lg bg-slate-50 px-2 py-1.5 text-xs ring-1 ring-slate-200" />
        <button onClick={activate} disabled={busy || key.trim().length < 8} className="rounded-lg bg-slate-800 px-2 py-1.5 text-xs font-semibold text-white disabled:opacity-40">Activate</button>
      </div>
      {msg && <div className={`mt-2 text-xs ${msg.ok ? "text-emerald-700" : "text-red-700"}`}>{msg.text}</div>}
      <div className="mt-2 text-[11px] text-slate-400">Billing, cancellations &amp; refunds are handled through Gumroad.</div>
    </div>
  );
}
