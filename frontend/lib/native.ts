import { useEffect, useState } from "react";
// Native (Capacitor) additions for the iOS/Android app build (NEXT_PUBLIC_NATIVE=1). The web build never calls these.
// Plugins are reached through the Capacitor bridge (window.Capacitor.Plugins), so the web app needs no extra npm packages.
export const NATIVE = process.env.NEXT_PUBLIC_NATIVE === "1";
// iOS-only store build flag: NEXT_PUBLIC_HIDE_PURCHASE_ON=ios hides the license-key field and external (Gumroad) purchase
// buttons when the app runs on iOS (App Store guideline 3.1.1). Web and Android are unchanged.
const HIDE_ON = (process.env.NEXT_PUBLIC_HIDE_PURCHASE_ON ?? "").split(",").map((x) => x.trim()).filter(Boolean);
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cap = (): any => (typeof window !== "undefined" ? (window as any).Capacitor : undefined);
export const isNativeShell = () => NATIVE && !!cap()?.isNativePlatform?.();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const plugin = (name: string): any => cap()?.Plugins?.[name];

export function platform(): string {
  if (typeof window === "undefined") return "web";
  if (NATIVE) { try { const f = localStorage.getItem("sopcheck.testPlatform"); if (f) return f; } catch { /* ignore */ } }
  return cap()?.getPlatform?.() ?? "web";
}
/** True when purchase UI must be hidden (decided after mount so static HTML is identical everywhere). */
export function useHidePurchase(): boolean {
  const [h, setH] = useState(false);
  useEffect(() => { setH(HIDE_ON.length > 0 && HIDE_ON.includes(platform())); }, []);
  return h;
}

export async function haptic(kind: "success" | "tap" | "warning" = "tap") {
  const H = plugin("Haptics"); if (!H) return;
  try { if (kind === "tap") await H.impact({ style: "LIGHT" }); else await H.notification({ type: kind === "success" ? "SUCCESS" : "WARNING" }); } catch { /* ignore */ }
}

function loadScript(src: string) {
  return new Promise<void>((res, rej) => { if (document.querySelector(`script[src="${src}"]`)) return res(); const s = document.createElement("script"); s.src = src; s.onload = () => res(); s.onerror = () => rej(new Error("failed to load " + src)); document.head.appendChild(s); });
}
function fileToDataUrl(f: File) { return new Promise<string>((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = rej; r.readAsDataURL(f); }); }
function pickImage(): Promise<string | null> {   // fallback when the Camera plugin is unavailable (web testing): camera-capable file input
  return new Promise((res) => {
    const i = document.createElement("input"); i.type = "file"; i.accept = "image/*"; i.setAttribute("capture", "environment");
    i.onchange = async () => res(i.files?.[0] ? await fileToDataUrl(i.files[0]) : null); i.click();
  });
}

/** Photograph a paper SOP with the device camera and read its text on-device (Tesseract OCR, bundled, works offline). */
export async function scanDocument(onStatus?: (s: string) => void): Promise<string | null> {
  const C = plugin("Camera");
  let dataUrl: string | null = null;
  if (C) {
    try { const p = await C.getPhoto({ quality: 85, resultType: "dataUrl", source: "PROMPT", correctOrientation: true, width: 2200, promptLabelHeader: "Scan SOP page" }); dataUrl = p.dataUrl; }
    catch { return null; }                                   // user cancelled
  } else dataUrl = await pickImage();
  if (!dataUrl) return null;
  onStatus?.("Reading text on your device…");
  await loadScript(`${BASE}/vendor/tesseract/tesseract.min.js`);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const T = (window as any).Tesseract;
  const w = await T.createWorker("eng", 1, { workerPath: `${BASE}/vendor/tesseract/worker.min.js`, corePath: `${BASE}/vendor/tesseract/core`, langPath: `${BASE}/vendor/tesseract/lang`, gzip: true, workerBlobURL: false });
  try { const r = await w.recognize(dataUrl); return String(r.data.text || ""); } finally { await w.terminate(); }
}

/** Push-ready: call only from an explicit user action (never required to use the app). Returns the device token. */
export async function registerPush(): Promise<string | null> {
  const P = plugin("PushNotifications"); if (!P) return null;
  const perm = await P.requestPermissions(); if (perm.receive !== "granted") return null;
  return new Promise((res) => { P.addListener("registration", (t: { value: string }) => res(t.value)); P.addListener("registrationError", () => res(null)); P.register(); });
}

function offlineBanner(offline: boolean) {
  let el = document.getElementById("native-offline");
  if (!offline) { el?.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = "native-offline"; el.setAttribute("style", "position:fixed;top:0;left:0;right:0;z-index:9997;background:#334155;color:#fff;font:12px system-ui;text-align:center;padding:4px calc(env(safe-area-inset-top) + 4px) 4px"); document.body.appendChild(el); }
  el.textContent = "Offline · your data stays on this device · AI parsing paused, the on-device parser is used";
}

export function installNative() {
  if (!NATIVE || typeof window === "undefined" || (window as unknown as { __sopNative?: boolean }).__sopNative) return;
  (window as unknown as { __sopNative?: boolean }).__sopNative = true;
  // haptic feedback on key actions (checking off a task, publishing, parsing a document)
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const r = await orig(input, init);
    try {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      if (method === "POST" && /\/api\/(submissions|documents\/upload|documents\/parse-text|documents\/\d+\/publish)/.test(url)) void haptic(r.ok ? "success" : "warning");
    } catch { /* ignore */ }
    return r;
  };
  const N = plugin("Network");
  if (N) { N.getStatus().then((s: { connected: boolean }) => offlineBanner(!s.connected)).catch(() => {}); N.addListener("networkStatusChange", (s: { connected: boolean }) => offlineBanner(!s.connected)); }
  else { addEventListener("online", () => offlineBanner(false)); addEventListener("offline", () => offlineBanner(true)); }
  plugin("StatusBar")?.setStyle?.({ style: "LIGHT" }).catch?.(() => {});
}
