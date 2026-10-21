"use client";

import { Nav } from "@/components/ui";
import { SiteFooter } from "@/components/legal";
import { useHidePurchase } from "@/lib/native";

const FAQ: [string, string][] = [
  ["How do I turn an SOP into a checklist?", "Sign in as a manager, upload the SOP or scope of work (.txt, .md or .pdf), or scan a paper copy with the camera in the app. SOPCheck drafts a checklist that you review, edit and publish."],
  ["Is the AI draft always correct?", "No. Drafts are produced automatically, in part with AI, and can contain mistakes or miss steps. Review every draft against your official procedure before publishing. SOPCheck is an informational tool and does not replace your procedures, training or applicable regulations."],
  ["Is my document sent to an AI service?", "Only after you agree to the prompt shown before the first AI parse. Otherwise the on-device parser is used. See the Privacy Policy for details."],
  ["Technicians can't see a checklist.", "Technicians only see published checklists that match their role, site and shift. Check that the checklist is published and that the technician's filters match."],
  ["How do I export or delete my data?", "Open Account in the app to download a copy of your data or to request deletion of your account or your organization's workspace."],
];

export default function SupportPage() {
  const hidePurchase = useHidePurchase();
  return (
    <>
      <Nav active="home" />
      <main className="mx-auto max-w-3xl px-4 py-10" data-testid="support">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900">SOPCheck Support</h1>
        <p className="mt-2 text-slate-600">Answers to common questions, and how to reach us.</p>
        <div className="mt-6 space-y-3">
          {FAQ.map(([q, a]) => (
            <details key={q} className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
              <summary className="cursor-pointer font-semibold text-slate-800">{q}</summary>
              <p className="mt-2 text-sm text-slate-600">{a}</p>
            </details>
          ))}
        </div>
        <div className="mt-8 rounded-2xl bg-white p-5 ring-1 ring-slate-200" data-testid="support-contact">
          <h2 className="text-lg font-semibold text-slate-900">Contact us</h2>
          {hidePurchase ? (
            <p className="mt-2 text-sm text-slate-600">For account help, your organization&apos;s account owner can reach us through the SOPCheck website.</p>
          ) : (
            <p className="mt-2 text-sm text-slate-600">
              Support, billing questions and refunds are handled through Gumroad. Message us from your purchase in your{" "}
              <a href="https://gumroad.com/library" className="text-teal-700 underline" rel="noopener">Gumroad library</a>, or visit{" "}
              <a href="https://pocketappstudio.gumroad.com" className="text-teal-700 underline" rel="noopener">pocketappstudio.gumroad.com</a>.
            </p>
          )}
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
