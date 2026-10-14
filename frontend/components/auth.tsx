"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api, appPath, go, type Me } from "@/lib/api";

import { TermsAcceptance } from "@/components/legal";

const Ctx = createContext<{ me: Me | null; loading: boolean; logout: () => Promise<void>; setMe: (m: Me) => void }>(
  { me: null, loading: true, logout: async () => {}, setMe: () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null)).finally(() => setLoading(false));
  }, []);
  async function logout() {
    try { await api.logout(); } finally { go("/login"); }
  }
  return <Ctx.Provider value={{ me, loading, logout, setMe }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

/** Client-side guard (middleware already redirects; this covers expired/revoked sessions). */
export function RequireAuth({ roles, children }: { roles?: Me["role"][]; children: React.ReactNode }) {
  const { me, loading, setMe, logout } = useAuth();
  useEffect(() => {
    if (loading) return;
    if (!me) go(`/login?next=${encodeURIComponent(appPath())}`);
    else if (roles && !roles.includes(me.role)) go(me.role === "technician" ? "/technician" : "/manager");
  }, [me, loading, roles]);
  if (loading || !me || (roles && !roles.includes(me.role))) {
    return <div className="grid min-h-[60vh] place-items-center text-sm text-slate-400">Loading…</div>;
  }
  if (me.needs_terms) return <TermsAcceptance me={me} onAccepted={setMe} onSignOut={logout} />;   // click-through gate (API enforces too)
  return <>{me.company_deletion_scheduled_for && <DeletionBanner when={me.company_deletion_scheduled_for} />}{children}</>;
}

export function DeletionBanner({ when }: { when: string }) {
  return (
    <div data-testid="deletion-banner" className="bg-red-600 px-4 py-2 text-center text-sm font-medium text-white">
      This workspace is locked and will be permanently deleted on {new Date(when).toLocaleString()}.{" "}
      <a href={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/account`} className="underline">Export your data or cancel under Account</a>.
    </div>
  );
}
