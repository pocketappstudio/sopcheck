"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/ui";
import { API_BASE } from "@/lib/api";
import { Markdown, SiteFooter } from "@/components/legal";

export default function LegalPage() {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    document.title = "Privacy Policy — SOPCheck";
    fetch(API_BASE + "/api/legal/privacy", { cache: "no-store" }).then((r) => r.text()).then(setText).catch(() => setText("Could not load the document."));
  }, []);
  return (
    <>
      <Nav active="home" />
      <main className="mx-auto max-w-3xl px-4 py-10">
        <article className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 sm:p-10" data-testid="legal-doc">
          {text ? <Markdown text={text} /> : <div className="text-sm text-slate-400">Loading…</div>}
        </article>
      </main>
      <SiteFooter />
    </>
  );
}
