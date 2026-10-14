// Two deployment modes:
//  * server mode (next start): calls are same-origin (/api is proxied to FastAPI by next.config.mjs) and carry the httpOnly cookie.
//  * static mode (GitHub Pages export): NEXT_PUBLIC_API_BASE points at the API origin; the session token returned by
//    /api/auth/login is kept in sessionStorage (tab-scoped) and sent as a Bearer header (cross-site cookies are unreliable).
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
import { DEMO, installDemo } from "./demo";
installDemo();
const TOKENS = !!API_BASE || DEMO;     // Bearer-token sessions (static hosting / in-browser demo)
const TOKEN_KEY = "sopcheck.session";
const tokenStore = {
  get: () => (TOKENS && typeof window !== "undefined" ? window.sessionStorage.getItem(TOKEN_KEY) : null),
  set: (t: string | null) => { if (TOKENS && typeof window !== "undefined") { if (t) window.sessionStorage.setItem(TOKEN_KEY, t); else window.sessionStorage.removeItem(TOKEN_KEY); } },
};
/** Navigate within the app (respects the GitHub Pages base path). */
export function go(path: string) { window.location.href = BASE_PATH + path; }
/** Current in-app path without the base path. */
export function appPath() { const p = window.location.pathname; return BASE_PATH && p.startsWith(BASE_PATH) ? p.slice(BASE_PATH.length) || "/" : p; }
function authInit(init?: RequestInit): RequestInit {
  const t = tokenStore.get();
  return { credentials: API_BASE ? "include" : "same-origin", cache: "no-store", ...init,
    headers: { ...(init?.headers ?? {}), ...(t ? { Authorization: `Bearer ${t}` } : {}) } };
}

export type VerificationMethod = "Checkbox" | "Photo Upload" | "Numeric Input" | "Text Input" | "Signature" | "Dual Sign-off";
export const VERIFICATION_METHODS: VerificationMethod[] = ["Checkbox", "Photo Upload", "Numeric Input", "Text Input", "Signature", "Dual Sign-off"];

export interface ChecklistTask {
  task_id: string; task_title: string; description: string; assigned_position: string;
  site_requirement: string; timeframe: string; verification_method: VerificationMethod;
}
export interface Checklist { industry: string; document_summary: string; tasks: ChecklistTask[] }
export interface EditableTask extends Omit<ChecklistTask, "task_id"> { id?: number; task_id?: string; is_safety_critical?: boolean; _key: string }

export interface Me {
  id: number; email: string; full_name: string; role: "manager" | "technician" | "admin";
  company_id: number; company_name: string; company_timezone: string;
  company_deletion_scheduled_for?: string | null;
  technician: { id: number; display_name: string; position: string | null; site: string | null } | null;
  needs_terms: boolean; terms_version_required: string; terms_version_accepted: string | null; terms_accepted_at: string | null;
}
export interface Plan { key: string; name: string; price_usd_month: number; max_technicians: number; max_parses_month: number; checkout_url: string; purchasable: boolean }
export interface Plans { trial: { days: number; max_technicians: number; max_parses_month: number }; plans: Plan[]; support: string }
export interface Billing {
  plan: string; plan_name: string; active: boolean; trial_ends_at: string | null; trial_days_left: number | null;
  limits: { max_technicians: number; max_parses_month: number };
  usage: { technicians: number; parses_this_month: number };
  license: { last4: string | null; verified_at: string | null; status: string | null }; enforced: boolean;
}
export interface Run {
  id: number; version_no: number; version_status: "draft" | "published" | "superseded"; parent_run_id: number | null;
  status: string; parser: string; model: string | null; prompt_version: string; chunk_count: number; repair_attempts: number;
  latency_ms: number | null; notes: string | null; created_at: string; updated_at: string;
  safety_audit: SafetySummary | null;
}
export interface SafetySummary { counts: { covered: number; partial: number; missing: number }; total: number; coverage_pct: number; unresolved: number; method: string }
export interface SafetyFlag {
  id: number; span_hash: string; span_text: string; section: string | null; categories: string[];
  coverage: "covered" | "partial" | "missing"; score: number; best_task_code: string | null;
  matched_atoms: string[]; missing_atoms: string[]; detector: string;
  resolution: "attached" | "task_created" | "dismissed" | null; resolution_reason: string | null;
  resolution_task_code: string | null; resolved_at: string | null;
}
export interface DocumentInfo {
  id: number; title: string; status: string; source_filename: string | null; created_at: string;
  published_run_id: number | null; published_version: number | null; draft_run_id: number | null; draft_version: number | null;
  industry?: string; task_count?: number; parser?: string; char_count: number;
  extraction_method: "text" | "pdf_text" | "ocr" | "mixed"; ocr_confidence: number | null;
  ocr_pages: { page: number; confidence: number; words: number }[] | null; safety: SafetySummary | null;
}
export interface DocDetail {
  document: DocumentInfo; run: Run; editable: boolean; checklist: Checklist;
  versions: { id: number; version_no: number; version_status: string; parser: string; created_at: string; task_count: number }[];
  tasks: (ChecklistTask & { id: number; is_safety_critical: boolean })[];
  safety: { summary: SafetySummary | null; flags: SafetyFlag[] };
}
export interface AuditEntry { id: number; action: string; entity_type: string; entity_id: number; data: Record<string, unknown>; actor: string | null; created_at: string }
export interface Submission {
  id: number; task_id: number; technician_name: string; status: string; numeric_value: number | null; text_value: string | null;
  photo_url: string | null; signature: string | null; cosigner_name: string | null; submitted_at: string;
}
export interface TaskWindow { start: string; end: string; local: string; timezone: string; status: "upcoming" | "active" | "closed" }
export interface TaskRow {
  id: number; document_id: number; task_code: string; sequence: number; task_title: string; description: string;
  assigned_position: string; site_requirement: string; timeframe: string; verification_method: VerificationMethod;
  is_safety_critical: boolean; completed: boolean; last_submission: Submission | null; window: TaskWindow | null;
}
export interface Filters { positions: string[]; sites: string[]; timeframes: string[]; technicians: string[] }
export interface Company { id: number; name: string; timezone: string; shift_windows: Record<string, [string, string]> }
export interface TechnicianInfo { id: number; display_name: string; email: string; position: string | null; site: string | null }

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, authInit(init));
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    tokenStore.set(null);
    go(`/login?expired=1&next=${encodeURIComponent(appPath())}`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    let detail: unknown;
    try {
      const j = await res.json();
      detail = j.detail;
      const d = j.detail;
      msg = typeof d === "string" ? d : d?.errors ? d.errors.join("; ") : d?.message ? d.message
        : Array.isArray(d) ? d.map((e: { loc?: string[]; msg: string }) => `${(e.loc ?? []).slice(-2).join(".")}: ${e.msg}`).join("; ") : JSON.stringify(d ?? j);
    } catch { /* ignore */ }
    throw new ApiError(res.status, msg, detail);
  }
  return res.json() as Promise<T>;
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  login: async (email: string, password: string) => {
    const r = await req<{ user: Me; token: string }>("/api/auth/login", json("POST", { email, password }));
    tokenStore.set(r.token);
    return r;
  },
  logout: async () => { try { return await req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }); } finally { tokenStore.set(null); } },
  me: () => req<Me>("/api/auth/me"),
  health: () => req<{ parser_mode: string; model: string | null; prompt_version: string }>("/api/health"),
  company: () => req<Company>("/api/company"),
  updateCompany: (body: Partial<Pick<Company, "timezone" | "shift_windows">>) => req<Company>("/api/company", json("PATCH", body)),
  technicians: () => req<TechnicianInfo[]>("/api/technicians"),
  upload: (file: File) => { const fd = new FormData(); fd.append("file", file); return req<DocDetail>("/api/documents/upload", { method: "POST", body: fd }); },
  parseText: (text: string, title: string) => req<DocDetail>("/api/documents/parse-text", json("POST", { text, title })),
  documents: () => req<DocumentInfo[]>("/api/documents"),
  document: (id: number, runId?: number) => req<DocDetail>(`/api/documents/${id}${runId ? `?run_id=${runId}` : ""}`),
  saveChecklist: (id: number, base_run_id: number, tasks: Omit<EditableTask, "_key">[]) =>
    req<DocDetail>(`/api/documents/${id}/checklist`, json("PUT", { base_run_id, tasks })),
  publish: (id: number, force = false, reason?: string, attest = false) =>
    req<DocDetail>(`/api/documents/${id}/publish?force=${force}&attest=${attest}${reason ? `&reason=${encodeURIComponent(reason)}` : ""}`, { method: "POST" }),
  /** Download an export (fetch + blob so it works with Bearer auth in static mode too). */
  download: async (id: number, format: "csv" | "md" | "json", runId?: number) => {
    const res = await fetch(`${API_BASE}/api/documents/${id}/export?format=${format}${runId ? `&run_id=${runId}` : ""}`, authInit());
    if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? `checklist.${format}`;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  account: () => req<AccountInfo>("/api/account"),
  /** Download the caller's data as JSON (managers get the whole workspace). */
  exportMyData: async () => {
    const res = await fetch(`${API_BASE}/api/account/export`, authInit());
    if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "sopcheck-export.json";
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  deleteMyAccount: async () => { const r = await req<{ ok: boolean; message: string }>("/api/account/delete", json("POST", { confirm: "DELETE" })); tokenStore.set(null); return r; },
  deleteCompany: (confirm_name: string) => req<{ ok: boolean; scheduled_for: string; message: string }>("/api/company/delete", json("POST", { confirm_name })),
  cancelCompanyDeletion: () => req<{ ok: boolean }>("/api/company/delete/cancel", { method: "POST" }),
  acceptTerms: (version: string) => req<Me>("/api/auth/accept-terms", json("POST", { version, accept: true })),
  plans: () => req<Plans>("/api/plans"),
  billing: () => req<Billing>("/api/billing"),
  activateLicense: (license_key: string) => req<Billing>("/api/billing/license", json("POST", { license_key })),
  resolveFlag: (docId: number, flagId: number, action: "attach" | "create_task" | "dismiss", task_code?: string, reason?: string) =>
    req<DocDetail>(`/api/documents/${docId}/safety/${flagId}/resolve`, json("POST", { action, task_code, reason })),
  audit: (id: number) => req<AuditEntry[]>(`/api/documents/${id}/audit`),
  filters: () => req<Filters>("/api/filters"),
  tasks: (p: { position?: string; site?: string; timeframe?: string }) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v) as [string, string][]);
    return req<TaskRow[]>(`/api/tasks?${qs}`);
  },
  submit: (body: Record<string, unknown>) => req<Submission>("/api/submissions", json("POST", body)),
  uploadPhoto: (file: File) => { const fd = new FormData(); fd.append("file", file); return req<{ url: string }>("/api/uploads/photo", { method: "POST", body: fd }); },
};

export interface AccountInfo {
  user: Me; can_self_delete: boolean; retention_note: string;
  deletion: { requested_at: string | null; scheduled_for: string | null; grace_days: number };
}
