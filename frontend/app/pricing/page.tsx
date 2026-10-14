"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Plans } from "@/lib/api";
import { Nav } from "@/components/ui";
import { SiteFooter } from "@/components/legal";

export default function PricingPage() {
  const [p, setP] = useState<Plans | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.plans().then(setP).catch((e) => setErr(e.message)); }, []);
  return (
    <>
      <Nav active="home" />
      <main className="mx-auto max-w-5xl px-4 py-10" data-testid="pricing">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900">Pricing</h1>
        <p className="mt-2 max-w-2xl text-slate-600">Turn any SOP into a reviewed, role-based technician checklist. Start with a free trial. No card is required to start.</p>
        {err && <div className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{err}</div>}
        {p && (
          <div className="mt-8 grid gap-5 md:grid-cols-3">
            <Card name="Free trial" price="$0" period={`${p.trial.days} days`} techs={p.trial.max_technicians} parses={p.trial.max_parses_month}
                  cta={<Link href="/signup" className="block rounded-xl bg-slate-800 py-2.5 text-center text-sm font-semibold text-white">Start free trial</Link>} />
            {p.plans.map((pl) => (
              <Card key={pl.key} name={pl.name} price={`$${pl.price_usd_month.toFixed(0)}`} period="per month" techs={pl.max_technicians}
                    parses={pl.max_parses_month} highlight={pl.key === "team"}
                    cta={pl.purchasable
                      ? <a href={pl.checkout_url} target="_blank" rel="noopener" data-testid={`buy-${pl.key}`}
                           className="block rounded-xl bg-teal-500 py-2.5 text-center text-sm font-semibold text-white hover:bg-teal-600">Buy on Gumroad</a>
                      : <span className="block rounded-xl bg-slate-200 py-2.5 text-center text-sm font-semibold text-slate-500" title="Gumroad product not configured yet">Available soon on Gumroad</span>} />
            ))}
          </div>
        )}
        <div className="mt-8 grid gap-4 text-sm text-slate-600 md:grid-cols-2">
          <div className="rounded-2xl bg-white p-5 ring-1 ring-slate-200">
            <div className="font-semibold text-slate-900">How activation works</div>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>Buy a monthly membership on Gumroad.</li>
              <li>Gumroad shows your <b>license key</b> on the receipt and in your Gumroad library.</li>
              <li>In SOPCheck, open <b>Manager → Billing</b> and paste the key. Your plan upgrades right away.</li>
            </ol>
          </div>
          <div className="rounded-2xl bg-white p-5 ring-1 ring-slate-200">
            <div className="font-semibold text-slate-900">Billing, cancellations &amp; refunds</div>
            <p className="mt-2">{p?.support ?? "Handled through Gumroad."} You can cancel your membership in Gumroad at any time. Limits are counted per company: technician seats, and documents parsed per calendar month (UTC). Editing a checklist does not count as a parse.</p>
          </div>
        </div>
        <p className="mt-6 text-xs text-slate-500">
          SOPCheck is an informational tool only. AI-generated checklists must be reviewed against your official procedures before use. See the{" "}
          <Link href="/terms" className="underline">Terms of Service</Link>.
        </p>
      </main>
      <SiteFooter />
    </>
  );
}

function Card({ name, price, period, techs, parses, cta, highlight }: { name: string; price: string; period: string; techs: number; parses: number; cta: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={`flex flex-col rounded-2xl bg-white p-6 shadow-sm ring-1 ${highlight ? "ring-2 ring-teal-500" : "ring-slate-200"}`}>
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">{name}</div>
        {highlight && <span className="rounded-full bg-teal-100 px-2 py-0.5 text-[10px] font-bold uppercase text-teal-700">Most seats</span>}
      </div>
      <div className="mt-3 flex items-baseline gap-1"><span className="text-4xl font-bold text-slate-900">{price}</span><span className="text-sm text-slate-500">{period}</span></div>
      <ul className="my-5 flex-1 space-y-2 text-sm text-slate-700">
        <li>✓ Up to <b>{techs}</b> technicians</li>
        <li>✓ <b>{parses}</b> document parses / month</li>
        <li>✓ AI extraction + safety-coverage review</li>
        <li>✓ Versioning, audit log, exports</li>
      </ul>
      {cta}
    </div>
  );
}
