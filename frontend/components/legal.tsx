"use client";

import Link from "next/link";
import { useState } from "react";
import { api, type Me } from "@/lib/api";

export const AI_DRAFT_BANNER = "AI-generated draft — verify against your official SOP before publishing.";
export const ATTESTATION = "I have reviewed this checklist and am responsible for its use";

/** Mandatory click-through acceptance of the current Terms + Privacy version (stored per user server-side). */
export function TermsAcceptance({ me, onAccepted, onSignOut }: { me: Me; onAccepted: (m: Me) => void; onSignOut: () => void }) {
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function accept() {
    setBusy(true); setErr(null);
    try { onAccepted(await api.acceptTerms(me.terms_version_required)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return (
    <main className="mx-auto max-w-xl px-4 py-10" data-testid="terms-gate">
      <div className="mb-5 flex items-center gap-2">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal-500 text-xl font-bold text-white">✓</span>
        <span className="text-xl font-semibold tracking-tight text-slate-800">SOPCheck</span>
      </div>
      <div className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <h1 className="text-lg font-semibold text-slate-900">Before you continue</h1>
        <p className="mt-1 text-sm text-slate-500">Hi {me.full_name}. Please review and accept the Terms of Service and Privacy Policy (version {me.terms_version_required}).</p>
        <ul className="mt-4 space-y-2 rounded-xl bg-amber-50 p-4 text-sm text-amber-900 ring-1 ring-amber-200">
          <li>• SOPCheck is an <b>informational tool only</b>. It does not direct anyone to do anything.</li>
          <li>• Checklists are <b>AI-generated and may be wrong</b>. Verify every task against your official SOP before use. This is not professional or safety advice.</li>
          <li>• Your organization is responsible for its workers, sites and decisions. The Service is provided <b>as is</b>, without warranties.</li>
          <li>• Document text is processed by OpenAI. Upload only documents you have the right to share. SOPCheck is for business use by adults 18+.</li>
        </ul>
        <p className="mt-3 text-sm">
          Read the full <Link href="/terms" target="_blank" className="font-medium text-teal-700 underline">Terms of Service</Link> and{" "}
          <Link href="/privacy" target="_blank" className="font-medium text-teal-700 underline">Privacy Policy</Link>.
        </p>
        <label className="mt-4 flex items-start gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} data-testid="terms-checkbox" className="mt-0.5 h-4 w-4 accent-teal-600" />
          <span>I have read and agree to the Terms of Service and Privacy Policy on behalf of myself and my organization.</span>
        </label>
        {err && <div className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700" role="alert">{err}</div>}
        <div className="mt-5 flex justify-between gap-2">
          <button onClick={onSignOut} className="rounded-xl px-4 py-2 text-sm font-medium text-slate-600 ring-1 ring-slate-300">Decline &amp; sign out</button>
          <button onClick={accept} disabled={!agree || busy} data-testid="terms-accept"
                  className="rounded-xl bg-slate-800 px-5 py-2 text-sm font-semibold text-white disabled:opacity-40">{busy ? "Saving…" : "Accept and continue"}</button>
        </div>
      </div>
    </main>
  );
}

export function AiDraftBanner() {
  return (
    <div className="mt-4 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-300" role="note" data-testid="ai-banner">
      <span aria-hidden>⚠</span>
      <span><b>{AI_DRAFT_BANNER}</b> SOPCheck is an informational tool. You decide what to publish and remain responsible for it.</span>
    </div>
  );
}

export function TechnicianFooter() {
  return (
    <footer className="mx-auto max-w-6xl px-4 pb-8 pt-2 text-center text-xs text-slate-500" data-testid="tech-footer">
      Follow your company&apos;s official procedures; this checklist is an aid. ·{" "}
      <Link href="/terms" className="underline">Terms</Link> · <Link href="/privacy" className="underline">Privacy</Link>
    </footer>
  );
}

export function SiteFooter() {
  return (
    <footer className="mx-auto max-w-6xl px-4 pb-8 pt-6 text-center text-xs text-slate-400">
      SOPCheck is an informational tool, not a substitute for official procedures. ·{" "}
      <Link href="/pricing" className="underline">Pricing</Link> · <Link href="/terms" className="underline">Terms</Link> ·{" "}
      <Link href="/privacy" className="underline">Privacy</Link> · Billing &amp; refunds via Gumroad
    </footer>
  );
}

/** Publish confirmation: the manager must tick the attestation (logged server-side with user + time). */
export function PublishAttestation({ version, onCancel, onConfirm, busy }: { version: number; onCancel: () => void; onConfirm: () => void; busy: boolean }) {
  const [ok, setOk] = useState(false);
  return (
    <div className="mt-4 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-300" data-testid="attest-panel">
      <div className="text-sm font-semibold text-slate-900">Publish v{version} to technicians</div>
      <p className="mt-1 text-xs text-slate-600">{AI_DRAFT_BANNER} Your confirmation is recorded in the audit log with your name and the time.</p>
      <label className="mt-3 flex items-start gap-2 text-sm text-slate-800">
        <input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} data-testid="attest-checkbox" className="mt-0.5 h-4 w-4 accent-teal-600" />
        <span>{ATTESTATION}.</span>
      </label>
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-lg px-3 py-1.5 text-sm text-slate-600 ring-1 ring-slate-300">Cancel</button>
        <button onClick={onConfirm} disabled={!ok || busy} data-testid="attest-confirm"
                className="rounded-lg bg-teal-500 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-40">Confirm &amp; publish</button>
      </div>
    </div>
  );
}

/** Tiny markdown renderer for our own legal docs (headings, paragraphs, lists, tables, bold, links, quotes). */
export function Markdown({ text }: { text: string }) {
  const inline = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/_(.+?)_/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((\/[^)\s]*)\)/g, '<a class="text-teal-700 underline" href="$2">$1</a>');
  const out: string[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (!l.trim()) continue;
    const h = l.match(/^(#{1,3})\s+(.*)$/);
    if (h) { const n = h[1].length; out.push(`<h${n} class="${["", "text-2xl font-bold mt-2", "text-lg font-semibold mt-6", "font-semibold mt-4"][n]}">${inline(h[2])}</h${n}>`); continue; }
    if (l.startsWith(">")) { out.push(`<blockquote class="my-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">${inline(l.replace(/^>\s?/, ""))}</blockquote>`); continue; }
    if (l.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) { const c = lines[i].split("|").slice(1, -1).map((x) => x.trim()); if (!c.every((x) => /^-+$/.test(x))) rows.push(c); i++; }
      i--;
      out.push(`<table class="my-3 w-full text-left text-sm">${rows.map((r, k) => `<tr class="border-b">${r.map((c) => `<${k ? "td" : "th"} class="p-2 align-top">${inline(c)}</${k ? "td" : "th"}>`).join("")}</tr>`).join("")}</table>`);
      continue;
    }
    if (l.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) { items.push(`<li>${inline(lines[i].slice(2))}</li>`); i++; }
      i--; out.push(`<ul class="my-2 list-disc space-y-1 pl-6">${items.join("")}</ul>`); continue;
    }
    out.push(`<p class="my-2 leading-relaxed">${inline(l)}</p>`);
  }
  return <div className="text-[15px] text-slate-700" dangerouslySetInnerHTML={{ __html: out.join("") }} />;
}
