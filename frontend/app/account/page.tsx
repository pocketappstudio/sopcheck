"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, go, type AccountInfo } from "@/lib/api";
import { Nav } from "@/components/ui";
import { RequireAuth, useAuth } from "@/components/auth";
import { SiteFooter } from "@/components/legal";

export default function AccountPage() {
  return (
    <>
      <Nav active="account" />
      <RequireAuth><Account /></RequireAuth>
      <SiteFooter />
    </>
  );
}

function Section({ title, children, danger }: { title: string; children: React.ReactNode; danger?: boolean }) {
  return (
    <section className={`rounded-2xl bg-white p-5 shadow-sm ring-1 ${danger ? "ring-red-200" : "ring-slate-200"}`}>
      <h2 className={`text-lg font-semibold ${danger ? "text-red-700" : "text-slate-900"}`}>{title}</h2>
      <div className="mt-2 space-y-3 text-sm text-slate-600">{children}</div>
    </section>
  );
}

function Account() {
  const { me, setMe } = useAuth();
  const [acc, setAcc] = useState<AccountInfo | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selfConfirm, setSelfConfirm] = useState("");
  const [companyConfirm, setCompanyConfirm] = useState("");
  const load = () => api.account().then((a) => { setAcc(a); setMe(a.user); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!me || !acc) return <main className="mx-auto max-w-3xl px-4 py-10 text-sm text-slate-400">{err ?? "Loading…"}</main>;
  const isManager = me.role !== "technician";
  const run = async (fn: () => Promise<unknown>) => { setBusy(true); setErr(null); setMsg(null); try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const scheduled = acc.deletion.scheduled_for;

  return (
    <main className="mx-auto max-w-3xl space-y-5 px-4 py-8" data-testid="account">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Account &amp; data</h1>
        <p className="mt-1 text-sm text-slate-500">{me.full_name} · {me.role} · {me.company_name}. See the <Link href="/privacy" className="underline">Privacy Policy</Link> for how long data is kept.</p>
      </div>
      {msg && <div className="rounded-xl bg-teal-50 p-3 text-sm text-teal-800" data-testid="account-msg">{msg}</div>}
      {err && <div className="rounded-xl bg-red-50 p-3 text-sm text-red-700" data-testid="account-err">{err}</div>}

      <Section title="Export your data">
        <p>Download a JSON copy of everything stored about you{isManager ? ", plus the whole company workspace (users, documents, checklist versions, completions, safety flags, audit log and a list of uploaded files)" : " (profile, sessions, your completed checklist items and activity)"}. Passwords and license keys are never included.</p>
        <button disabled={busy} data-testid="export-data" onClick={() => run(async () => { await api.exportMyData(); setMsg("Export downloaded."); })}
                className="rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Download my data (JSON)</button>
      </Section>

      <Section title="Delete my account" danger>
        {acc.can_self_delete ? (<>
          <p>Your login is disabled immediately and your name, sign-in history and signatures are removed. Checklist items you completed stay in the company&apos;s records, shown as &quot;Deleted user&quot;, because the company may need them for compliance. This cannot be undone.</p>
          <label className="block">Type <b>DELETE</b> to confirm
            <input value={selfConfirm} onChange={(e) => setSelfConfirm(e.target.value)} data-testid="self-delete-confirm"
                   className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
          <button disabled={busy || selfConfirm !== "DELETE"} data-testid="self-delete"
                  onClick={() => run(async () => { await api.deleteMyAccount(); go("/login?deleted=1"); })}
                  className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Delete my account</button>
        </>) : <p>You are the only manager of this workspace, so your account can&apos;t be deleted on its own. Make another user a manager first, or delete the whole workspace below.</p>}
      </Section>

      {isManager && (
        <Section title="Delete company workspace" danger>
          {scheduled ? (<>
            <p data-testid="deletion-scheduled">Deletion is scheduled. The workspace is locked, and on <b>{new Date(scheduled).toLocaleString()}</b> all of its data (users, documents, checklists, completions, photos and files) will be permanently deleted. Only a minimal record that a workspace was deleted is kept; it holds no names or content. Export anything you need before then.</p>
            <button disabled={busy} data-testid="cancel-deletion" onClick={() => run(async () => { await api.cancelCompanyDeletion(); setMsg("Deletion cancelled. The workspace is unlocked."); await load(); })}
                    className="rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Cancel deletion</button>
          </>) : (<>
            <p>Permanently deletes <b>{me.company_name}</b> for everyone: all users, documents, checklists, completions, photos and files. The workspace locks right away. You have {acc.deletion.grace_days} days to cancel, then it is deleted for good. Only a minimal record that a workspace was deleted is kept; it holds no names or content. Paid plans are managed in Gumroad; cancel your subscription there too.</p>
            <label className="block">Type the company name <b>{me.company_name}</b> to confirm
              <input value={companyConfirm} onChange={(e) => setCompanyConfirm(e.target.value)} data-testid="company-delete-confirm"
                     className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
            <button disabled={busy || companyConfirm.trim() !== me.company_name} data-testid="company-delete"
                    onClick={() => run(async () => { const r = await api.deleteCompany(companyConfirm); setMsg(r.message); setCompanyConfirm(""); await load(); })}
                    className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Delete workspace</button>
          </>)}
        </Section>
      )}
    </main>
  );
}
