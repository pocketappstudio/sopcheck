// All API timestamps are UTC ISO-8601 with an offset. Render them in the viewer's browser zone
// with a short zone label (e.g. "12:43 PM CDT"); shift windows render in the company zone.

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(opts: Intl.DateTimeFormatOptions, tz?: string) {
  const key = JSON.stringify([opts, tz]);
  if (!fmtCache.has(key)) fmtCache.set(key, new Intl.DateTimeFormat("en-US", { ...opts, ...(tz ? { timeZone: tz } : {}) }));
  return fmtCache.get(key)!;
}

export function parseUtc(iso: string): Date {
  // Defensive: a timestamp without offset is UTC by API contract (never local time).
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
}

/** "12:43 PM CDT" in the viewer's zone (or `tz` if given). */
export function formatTime(iso: string, tz?: string) {
  return fmt({ hour: "numeric", minute: "2-digit", timeZoneName: "short" }, tz).format(parseUtc(iso));
}

/** "Oct 10, 12:43 PM CDT" in the viewer's zone. */
export function formatDateTime(iso: string, tz?: string) {
  return fmt({ month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }, tz).format(parseUtc(iso));
}

/** "7:00 – 9:00 AM CDT" for a shift window, rendered in the company's zone. */
export function formatWindow(w: { start: string; end: string; timezone: string }) {
  const a = fmt({ hour: "numeric", minute: "2-digit" }, w.timezone).format(parseUtc(w.start));
  const b = fmt({ hour: "numeric", minute: "2-digit", timeZoneName: "short" }, w.timezone).format(parseUtc(w.end));
  return `${a} – ${b}`;
}

export function viewerZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}
