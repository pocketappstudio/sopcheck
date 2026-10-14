"use client";

import Link from "next/link";
import { Nav } from "@/components/ui";
import { SiteFooter } from "@/components/legal";

export default function Home() {
  return (
    <>
      <Nav active="home" />
      <main className="mx-auto max-w-4xl px-4 py-12">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">Any SOP → a technician checklist.</h1>
        <p className="mt-3 max-w-2xl text-slate-600">
          Upload a standard operating procedure or scope of work from any industry. An LLM extracts every task with its
          position, site, timeframe and verification method, preserving safety details verbatim. Technicians get a
          filtered, mobile checklist for their role, location and shift.
        </p>
        <div className="mt-6 flex gap-2">
          <Link href="/login" className="inline-block rounded-xl bg-slate-800 px-5 py-2.5 text-sm font-semibold text-white">Sign in</Link>
          <Link href="/pricing" className="inline-block rounded-xl bg-white px-5 py-2.5 text-sm font-semibold text-slate-800 ring-1 ring-slate-300">Pricing · 14-day free trial</Link>
        </div>
        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          <Link href="/manager" className="group rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 transition hover:shadow-md">
            <div className="text-sm font-semibold text-teal-600">Manager Dashboard</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">Upload, review & publish</div>
            <p className="mt-2 text-sm text-slate-500">Parse TXT/PDF documents, preview the structured checklist and raw JSON, check safety coverage, publish.</p>
          </Link>
          <Link href="/technician" className="group rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 transition hover:shadow-md">
            <div className="text-sm font-semibold text-orange-600">Technician View</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">Do the work, prove it</div>
            <p className="mt-2 text-sm text-slate-500">Pick role, site and shift. Large checkboxes, readings, photos and sign-offs per task.</p>
          </Link>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
