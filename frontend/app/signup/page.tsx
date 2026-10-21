"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_BASE, go } from "@/lib/api";
import { SiteFooter } from "@/components/legal";
import { useHidePurchase } from "@/lib/native";

export default function SignupPage() {
  const [f, setF] = useState({ company_name: "", full_name: "", email: "", password: "" });
  const hidePurchase = useHidePurchase();
  const [agree, setAgree] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {   // the Terms version the user is agreeing to comes from the server
    fetch(API_BASE + "/api/legal/terms", { cache: "no-store" }).then((r) => setVersion(r.headers.get("X-Terms-Version"))).catch(() => {});
  }, []);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Chicago";
      const res = await fetch(API_BASE + "/api/auth/signup", { method: "POST", credentials: API_BASE ? "include" : "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...f, timezone: tz, accept_terms: agree, terms_version: version }) });
      const j = await res.json();
      if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : j.detail?.message ?? "Sign-up failed");
      if (API_BASE) window.sessionStorage.setItem("sopcheck.session", j.token);
      go("/manager");
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)); } finally { setBusy(false); }
  }
  const input = "mt-1 w-full rounded-xl bg-slate-50 px-3 py-2.5 text-sm ring-1 ring-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500";
  return (
    <>
      <main className="mx-auto max-w-md px-4 py-10">
        <div className="mb-5 flex items-center justify-center gap-2">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal-500 text-xl font-bold text-white">✓</span>
          <span className="text-xl font-semibold tracking-tight text-slate-800">SOPCheck</span>
        </div>
        <form onSubmit={submit} className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200" data-testid="signup">
          <h1 className="text-lg font-semibold text-slate-900">{hidePurchase ? "Create your organization account" : "Start your free trial"}</h1>
          <p className="mt-1 text-sm text-slate-500">{hidePurchase ? "Set up a SOPCheck workspace for your team." : "You get 14 days to try SOPCheck. No card is required to start."}</p>
          <label className="mt-4 block text-xs font-semibold uppercase tracking-wide text-slate-400">Company<input required value={f.company_name} onChange={set("company_name")} className={input} /></label>
          <label className="mt-3 block text-xs font-semibold uppercase tracking-wide text-slate-400">Your name<input required value={f.full_name} onChange={set("full_name")} className={input} /></label>
          <label className="mt-3 block text-xs font-semibold uppercase tracking-wide text-slate-400">Work login<input required type="email" autoComplete="username" value={f.email} onChange={set("email")} className={input} /></label>
          <label className="mt-3 block text-xs font-semibold uppercase tracking-wide text-slate-400">Password (10+ characters)<input required type="password" minLength={10} autoComplete="new-password" value={f.password} onChange={set("password")} className={input} /></label>
          <label className="mt-4 flex items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5 h-4 w-4 accent-teal-600" data-testid="signup-terms" />
            <span>I agree to the <Link href="/terms" target="_blank" className="text-teal-700 underline">Terms of Service</Link> and{" "}
              <Link href="/privacy" target="_blank" className="text-teal-700 underline">Privacy Policy</Link>. I understand SOPCheck is an informational tool only: AI checklists may be wrong and must be verified against our official SOP, and my organization is responsible for its workers, sites and decisions.</span>
          </label>
          {err && <div className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700" role="alert">{err}</div>}
          <button disabled={!agree || busy || !version} className="mt-5 w-full rounded-xl bg-slate-800 py-2.5 text-sm font-semibold text-white disabled:opacity-40">
            {busy ? "Creating…" : "Create account"}
          </button>
          <p className="mt-3 text-center text-sm text-slate-500">Already have an account? <Link href="/login" className="text-teal-700 underline">Sign in</Link></p>
        </form>
      </main>
      <SiteFooter />
    </>
  );
}
