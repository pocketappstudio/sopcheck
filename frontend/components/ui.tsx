"use client";

import Link from "next/link";
import { useAuth } from "@/components/auth";
import type { VerificationMethod } from "@/lib/api";

const VM_STYLE: Record<string, string> = {
  "Checkbox": "bg-slate-100 text-slate-700 ring-slate-300",
  "Photo Upload": "bg-violet-50 text-violet-700 ring-violet-300",
  "Numeric Input": "bg-sky-50 text-sky-700 ring-sky-300",
  "Text Input": "bg-amber-50 text-amber-700 ring-amber-300",
  "Signature": "bg-emerald-50 text-emerald-700 ring-emerald-300",
  "Dual Sign-off": "bg-rose-50 text-rose-700 ring-rose-300",
};
const VM_ICON: Record<string, string> = {
  "Checkbox": "☑", "Photo Upload": "📷", "Numeric Input": "#", "Text Input": "✎", "Signature": "✍", "Dual Sign-off": "👥",
};

export function VMBadge({ vm }: { vm: VerificationMethod | string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ${VM_STYLE[vm] ?? VM_STYLE.Checkbox}`}>
      <span aria-hidden>{VM_ICON[vm] ?? "☑"}</span>{vm}
    </span>
  );
}

export function Badge({ icon, children, tone = "slate" }: { icon?: string; children: React.ReactNode; tone?: "slate" | "teal" | "orange" | "indigo" | "red" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-600",
    teal: "bg-teal-50 text-teal-700",
    orange: "bg-orange-50 text-orange-700",
    indigo: "bg-indigo-50 text-indigo-700",
    red: "bg-red-50 text-red-700",
  };
  return (
    <span className={`inline-flex max-w-full items-center gap-1 truncate rounded-md px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {icon && <span aria-hidden>{icon}</span>}<span className="truncate">{children}</span>
    </span>
  );
}

export function Nav({ active }: { active: "home" | "manager" | "technician" | "login" | "account" }) {
  const { me, logout } = useAuth();
  const item = (href: string, label: string, key: string) => (
    <Link href={href} className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${active === key ? "bg-white text-slate-900 shadow-sm" : "text-slate-300 hover:text-white"}`}>
      {label}
    </Link>
  );
  return (
    <header className="sticky top-0 z-20 bg-slate-800/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 text-white">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-teal-500 text-lg font-bold">✓</span>
          <span className="font-semibold tracking-tight">SOPCheck</span>
        </Link>
        {me && (
          <div className="flex items-center gap-2">
            <nav className="flex gap-1 rounded-xl bg-slate-700/60 p-1">
              {me.role !== "technician" && item("/manager", "Manager", "manager")}
              {item("/technician", "Technician", "technician")}
              {item("/account", "Account", "account")}
            </nav>
            <div className="hidden text-right leading-tight md:block">
              <div className="text-xs font-semibold text-white">{me.full_name}</div>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">{me.role} · {me.company_name}</div>
            </div>
            <button onClick={logout} data-testid="logout" title="Sign out"
                    className="rounded-lg px-2 py-1.5 text-xs font-medium text-slate-300 ring-1 ring-slate-600 hover:bg-slate-700 hover:text-white">
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent align-[-2px]" />;
}
