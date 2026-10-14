"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError, go } from "@/lib/api";
import { Spinner } from "@/components/ui";

const SHOW_DEMO = process.env.NEXT_PUBLIC_SHOW_DEMO_ACCOUNTS !== "false";
const DEMO = [
  { label: "Manager", email: "manager@demo.co", pw: "Manager!2026", note: "Demo Company" },
  { label: "Electrician", email: "alex@demo.co", pw: "Tech!2026", note: "Alex Rivera" },
  { label: "Safety Officer", email: "jordan@demo.co", pw: "Tech!2026", note: "Jordan Lee" },
  { label: "Other tenant", email: "manager@globex.co", pw: "Manager!2026", note: "Globex Facilities" },
];

function LoginForm() {
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const { user } = await api.login(email, password);
      const next = params.get("next");
      const home = user.role === "technician" ? "/technician" : "/manager";
      go(next && next.startsWith("/") && !(user.role === "technician" && next.startsWith("/manager")) ? next : home);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed");
      setBusy(false);
    }
  }

  const input = "w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-base outline-none transition focus:border-teal-400 focus:bg-white focus:ring-2 focus:ring-teal-100";
  return (
    <main className="grid min-h-screen place-items-center bg-slate-100 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal-500 text-xl font-bold text-white shadow-sm">✓</span>
          <span className="text-xl font-semibold tracking-tight text-slate-800">SOPCheck</span>
        </div>
        {params.get("deleted") && <div className="mb-3 rounded-xl bg-teal-50 p-3 text-sm text-teal-800" data-testid="deleted-notice">Your account was deleted and you have been signed out.</div>}
        <form onSubmit={submit} className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <h1 className="text-lg font-semibold text-slate-900">Sign in</h1>
          <p className="mb-5 text-sm text-slate-500">Managers publish checklists; technicians complete them.</p>
          <label className="mb-3 block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">Email</span>
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className={input} data-testid="email" />
          </label>
          <label className="mb-4 block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">Password</span>
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={input} data-testid="password" />
          </label>
          {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200" role="alert">{error}</div>}
          <button disabled={busy} className="w-full rounded-xl bg-slate-800 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-900 disabled:opacity-60" data-testid="signin">
            {busy ? <Spinner /> : "Sign in"}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-500">New to SOPCheck? <Link href="/signup" className="font-medium text-teal-700 underline">Start a 14-day free trial</Link> · <Link href="/pricing" className="underline">Pricing</Link></p>
        {SHOW_DEMO && (
          <div className="mt-4 rounded-2xl bg-white/70 p-4 ring-1 ring-slate-200">
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Demo accounts (tap to fill)</div>
            <div className="grid grid-cols-2 gap-2">
              {DEMO.map((d) => (
                <button key={d.email} type="button" onClick={() => { setEmail(d.email); setPassword(d.pw); }}
                        className="rounded-xl bg-white px-3 py-2 text-left ring-1 ring-slate-200 transition hover:ring-teal-400">
                  <div className="text-xs font-semibold text-slate-800">{d.label}</div>
                  <div className="truncate text-[11px] text-slate-400">{d.email}</div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
