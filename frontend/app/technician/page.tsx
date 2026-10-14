"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Filters, type TaskRow, type TechnicianInfo } from "@/lib/api";
import { RequireAuth, useAuth } from "@/components/auth";
import { Badge, Nav, VMBadge } from "@/components/ui";
import { TechnicianFooter } from "@/components/legal";
import { formatTime, formatWindow, viewerZone } from "@/lib/time";

type Sel = { position: string; site: string; timeframe: string };
const EMPTY: Sel = { position: "", site: "", timeframe: "" };

export default function TechnicianPage() {
  return <RequireAuth><TechnicianView /></RequireAuth>;
}

function TechnicianView() {
  const { me } = useAuth();
  const storageKey = `sopcheck.tech.sel.${me?.id}`;
  const [filters, setFilters] = useState<Filters | null>(null);
  const [crew, setCrew] = useState<TechnicianInfo[]>([]);
  const [sel, setSel] = useState<Sel | null>(null);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(true);
  const canSubmit = !!me?.technician;

  useEffect(() => {
    let init: Sel = { ...EMPTY, position: me?.technician?.position ?? "", site: me?.technician?.site ?? "" };   // default to my profile
    try { const s = localStorage.getItem(storageKey); if (s) init = { ...init, ...JSON.parse(s) }; } catch { /* ignore */ }
    setSel(init);
    api.filters().then(setFilters).catch((e) => setError(e.message));
    api.technicians().then(setCrew).catch(() => {});
  }, [me, storageKey]);

  // A profile/saved value that no published task uses (e.g. site "Rooftop" when the SOP says "Roof Access
  // Area") would silently filter everything out while the dropdown shows "All": drop it so the UI and filter agree.
  useEffect(() => {
    if (!filters || !sel) return;
    const fix: Partial<Sel> = {};
    if (sel.position && !filters.positions.includes(sel.position)) fix.position = "";
    if (sel.site && !filters.sites.includes(sel.site)) fix.site = "";
    if (sel.timeframe && !filters.timeframes.includes(sel.timeframe)) fix.timeframe = "";
    if (Object.keys(fix).length) setSel({ ...sel, ...fix });
  }, [filters, sel]);

  const load = useCallback(async () => {
    if (!sel) return;
    setLoading(true); setError(null);
    try { setTasks(await api.tasks(sel)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setLoading(false); }
  }, [sel]);
  useEffect(() => { load(); if (sel) localStorage.setItem(storageKey, JSON.stringify(sel)); }, [load, sel, storageKey]);

  const done = tasks.filter((t) => t.completed).length;
  const pct = tasks.length ? Math.round((100 * done) / tasks.length) : 0;
  const grouped = useMemo(() => {
    const g = new Map<string, TaskRow[]>();
    tasks.forEach((t) => g.set(t.timeframe, [...(g.get(t.timeframe) ?? []), t]));
    // field-day order: start-of-shift / immediate items first, recurring schedules next, "Unspecified" last
    const RANK: [RegExp, number][] = [[/^(shift start|pre-shift|opening)/i, 0], [/^pre-task|before/i, 1], [/^immediately/i, 2],
      [/^during task/i, 3], [/^post-task|upon completion/i, 4], [/^shift end|closing/i, 5], [/^(hourly|every \d+ hours?)/i, 6],
      [/^daily/i, 7], [/^weekly/i, 8], [/^monthly/i, 9], [/^quarterly/i, 10], [/^(semi-annual|every six months)/i, 11],
      [/^annual/i, 12], [/^unspecified$/i, 99]];
    const rank = (tf: string) => RANK.find(([rx]) => rx.test(tf))?.[1] ?? 50;
    return Array.from(g.entries()).map((e, i) => ({ e, i })).sort((a, b) => rank(a.e[0]) - rank(b.e[0]) || a.i - b.i).map(({ e }) => e);
  }, [tasks]);
  const set = (k: keyof Sel) => (e: React.ChangeEvent<HTMLSelectElement>) => setSel((s) => ({ ...(s ?? EMPTY), [k]: e.target.value }));

  return (
    <>
      <Nav active="technician" />
      <main className="mx-auto max-w-md px-3 pb-24 pt-4">
        <div className="rounded-2xl bg-slate-800 p-4 text-white shadow-lg">
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <div className="text-xs uppercase tracking-wider text-slate-400">{me?.technician?.display_name ?? me?.full_name} · my checklist</div>
              <div className="truncate text-lg font-semibold">{sel?.position || "All positions"}</div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-bold tabular-nums">{pct}%</div>
              <div className="text-xs text-slate-400">{done}/{tasks.length} done today</div>
            </div>
          </div>
          <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-600" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className={`h-full rounded-full transition-all duration-500 ${pct === 100 ? "bg-emerald-400" : "bg-teal-400"}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
            {sel?.site && <span className="rounded-md bg-slate-700 px-2 py-0.5">📍 {sel.site}</span>}
            {sel?.timeframe && <span className="rounded-md bg-slate-700 px-2 py-0.5">⏱ {sel.timeframe}</span>}
            <span className="rounded-md bg-slate-700 px-2 py-0.5" title="Times are shown in your device timezone">🕑 {viewerZone()}</span>
            <button onClick={() => setShowFilters((v) => !v)} className="ml-auto rounded-md bg-teal-500 px-2 py-0.5 font-semibold">{showFilters ? "Hide filters" : "Change filters"}</button>
          </div>
        </div>
        {!canSubmit && <div className="mt-3 rounded-xl bg-amber-50 p-3 text-xs text-amber-800 ring-1 ring-amber-200">Preview mode: your account isn&apos;t linked to a technician record, so you can view but not complete tasks.</div>}

        {showFilters && sel && (
          <div className="mt-3 grid grid-cols-2 gap-2 rounded-2xl bg-white p-3 ring-1 ring-slate-200">
            <Select label="Position / Role" value={sel.position} onChange={set("position")} options={filters?.positions ?? []} placeholder="All" />
            <Select label="Site" value={sel.site} onChange={set("site")} options={filters?.sites ?? []} placeholder="All" />
            <Select label="Time frame / Shift" value={sel.timeframe} onChange={set("timeframe")} options={filters?.timeframes ?? []} placeholder="All" full />
          </div>
        )}
        {error && <div className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700 ring-1 ring-red-200">{error}</div>}
        {!loading && tasks.length === 0 && <div className="mt-6 rounded-2xl bg-white p-8 text-center text-sm text-slate-400 ring-1 ring-slate-200">No published tasks match. Widen your filters or ask your manager to publish a checklist.</div>}

        {grouped.map(([tf, items]) => (
          <section key={tf} className="mt-5">
            <h2 className="mb-2 flex items-center gap-2 px-1 text-xs font-bold uppercase tracking-wider text-slate-500">
              <span className="h-2 w-2 rounded-full bg-orange-400" />{tf}
              <span className="font-medium normal-case text-slate-400">· {items.filter((i) => i.completed).length}/{items.length}</span>
              {items[0].window && <WindowChip w={items[0].window} />}
            </h2>
            <div className="space-y-3">
              {items.map((t) => <TaskCard key={t.id} task={t} canSubmit={canSubmit} meId={me?.technician?.id} crew={crew} onDone={load} />)}
            </div>
          </section>
        ))}
      </main>
      <TechnicianFooter />
    </>
  );
}

function WindowChip({ w }: { w: NonNullable<TaskRow["window"]> }) {
  const tone = w.status === "active" ? "bg-emerald-100 text-emerald-700" : w.status === "upcoming" ? "bg-sky-100 text-sky-700" : "bg-slate-200 text-slate-500";
  return <span data-testid="window" className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold normal-case tracking-normal ${tone}`}>🕖 {formatWindow(w)} · {w.status}</span>;
}

function Select({ label, value, onChange, options, placeholder, full }: { label: string; value: string; onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void; options: string[]; placeholder: string; full?: boolean }) {
  return (
    <label className={`block ${full ? "col-span-2" : ""}`}>
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</span>
      <select value={value} onChange={onChange} className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-800 outline-none focus:ring-2 focus:ring-teal-400">
        <option value="">{placeholder}</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

function TaskCard({ task, canSubmit, meId, crew, onDone }: { task: TaskRow; canSubmit: boolean; meId?: number; crew: TechnicianInfo[]; onDone: () => void }) {
  const vm = task.verification_method;
  const [num, setNum] = useState("");
  const [text, setText] = useState("");
  const [sig, setSig] = useState("");
  const [coEmail, setCoEmail] = useState("");
  const [coPw, setCoPw] = useState("");
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const done = task.completed;
  const sub = task.last_submission;
  const ready = canSubmit && (vm === "Checkbox" || (vm === "Numeric Input" && num.trim() !== "" && !Number.isNaN(Number(num))) ||
    (vm === "Text Input" && text.trim() !== "") || (vm === "Photo Upload" && !!photoUrl) || (vm === "Signature" && sig.trim() !== "") ||
    (vm === "Dual Sign-off" && sig.trim() !== "" && coEmail !== "" && coPw !== ""));

  async function complete() {
    if (done || busy) return;
    if (!canSubmit) { setErr("Preview mode — sign in as a technician to complete tasks."); return; }
    if (!ready) { setErr(hint(vm)); setOpen(true); return; }
    setBusy(true); setErr(null);
    try {
      await api.submit({ task_id: task.id, checked: true,
        numeric_value: vm === "Numeric Input" ? Number(num) : undefined, text_value: vm === "Text Input" ? text : undefined,
        photo_url: vm === "Photo Upload" ? photoUrl : undefined, signature: vm === "Signature" || vm === "Dual Sign-off" ? sig : undefined,
        cosigner_email: vm === "Dual Sign-off" ? coEmail : undefined, cosigner_password: vm === "Dual Sign-off" ? coPw : undefined });
      onDone();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); setCoPw(""); }
  }
  async function onPhoto(f?: File | null) {
    if (!f) return;
    setBusy(true); setErr(null);
    try { setPhotoUrl((await api.uploadPhoto(f)).url); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  const input = "w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-base outline-none focus:ring-2 focus:ring-teal-400";

  return (
    <article data-testid="task-card" className={`rounded-2xl bg-white p-4 shadow-sm ring-1 transition ${done ? "ring-emerald-200" : task.is_safety_critical ? "ring-orange-200" : "ring-slate-200"}`}>
      <div className="flex items-start gap-3">
        <button onClick={complete} disabled={busy} aria-label={done ? "Completed" : `Complete ${task.task_title}`} data-testid={`check-${task.id}`}
                className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl border-2 text-2xl font-bold transition active:scale-95 ${
                  done ? "border-emerald-500 bg-emerald-500 text-white" : ready ? "border-teal-500 bg-white text-transparent hover:text-teal-300" : "border-slate-300 bg-white text-transparent"}`}>
          {busy ? <span className="h-5 w-5 animate-spin rounded-full border-2 border-teal-500 border-t-transparent" /> : "✓"}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h3 className={`font-semibold leading-snug ${done ? "text-slate-400 line-through" : "text-slate-900"}`}>{task.task_title}</h3>
            <span className="shrink-0 font-mono text-[10px] text-slate-400">{task.task_code}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Badge icon="⏱" tone="orange">{task.timeframe}</Badge>
            <Badge icon="📍" tone="teal">{task.site_requirement}</Badge>
            <Badge icon="👤">{task.assigned_position}</Badge>
            {task.is_safety_critical && <Badge icon="⚠" tone="red">Safety</Badge>}
          </div>
        </div>
      </div>
      <button onClick={() => setOpen((v) => !v)} className="mt-2 w-full text-left"><p className={`text-sm text-slate-600 ${open ? "" : "line-clamp-2"}`}>{task.description}</p></button>
      <div className="mt-3 flex items-center justify-between gap-2">
        <VMBadge vm={vm} />
        {done && sub && (
          <span className="text-right text-[11px] text-emerald-600" data-testid="done-time">
            ✓ {sub.technician_name}{sub.cosigner_name ? ` + ${sub.cosigner_name}` : ""} · {formatTime(sub.submitted_at)}{sub.numeric_value != null ? ` · ${sub.numeric_value}` : ""}
          </span>
        )}
      </div>
      {!done && canSubmit && vm !== "Checkbox" && (
        <div className="mt-3 space-y-2">
          {vm === "Numeric Input" && <input inputMode="decimal" type="number" step="any" placeholder="Enter reading" value={num} onChange={(e) => setNum(e.target.value)} className={input} />}
          {vm === "Text Input" && <input placeholder="Enter value (lot #, time, notes)" value={text} onChange={(e) => setText(e.target.value)} className={input} />}
          {vm === "Photo Upload" && (
            <label className={`flex cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed px-3 py-3 text-sm font-medium ${photoUrl ? "border-emerald-400 bg-emerald-50 text-emerald-700" : "border-violet-300 bg-violet-50 text-violet-700"}`}>
              {photoUrl ? "✓ Photo attached — tap to replace" : "📷 Take / attach photo"}
              <input type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => onPhoto(e.target.files?.[0])} />
            </label>
          )}
          {(vm === "Signature" || vm === "Dual Sign-off") && <input placeholder="Type full name to sign" value={sig} onChange={(e) => setSig(e.target.value)} className={`${input} italic`} />}
          {vm === "Dual Sign-off" && (
            <div className="rounded-xl bg-rose-50 p-2 ring-1 ring-rose-200">
              <div className="mb-1 px-1 text-[11px] font-semibold text-rose-700">Second verifier signs in to co-sign</div>
              <select value={coEmail} onChange={(e) => setCoEmail(e.target.value)} className={input} aria-label="Co-signer">
                <option value="">Co-signer…</option>
                {crew.filter((t) => t.id !== meId).map((t) => <option key={t.id} value={t.email}>{t.display_name} ({t.position})</option>)}
              </select>
              <input type="password" autoComplete="off" placeholder="Co-signer password" value={coPw} onChange={(e) => setCoPw(e.target.value)} className={`${input} mt-2`} aria-label="Co-signer password" />
            </div>
          )}
        </div>
      )}
      {err && <p className="mt-2 text-xs font-medium text-red-600">{err}</p>}
    </article>
  );
}

function hint(vm: string) {
  return ({ "Numeric Input": "Enter the reading before checking off.", "Text Input": "Enter the required value before checking off.",
    "Photo Upload": "Attach a photo before checking off.", "Signature": "Type your name to sign before checking off.",
    "Dual Sign-off": "Sign, pick a co-signer and have them enter their password." } as Record<string, string>)[vm] ?? "";
}
