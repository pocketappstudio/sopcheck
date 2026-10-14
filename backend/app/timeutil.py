"""Company-timezone helpers for shift windows and 'today' boundaries. All storage is UTC."""
from __future__ import annotations
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

VALID_ZONES = available_timezones()


def zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz if tz in VALID_ZONES else "UTC")


def local_day_start_utc(tz: str, now: datetime | None = None) -> datetime:
    """UTC instant of local midnight 'today' in the company zone (for daily completion state)."""
    z = zone(tz)
    now_local = (now or datetime.now(timezone.utc)).astimezone(z)
    return datetime.combine(now_local.date(), time(0), z).astimezone(timezone.utc)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


TIME_IN_TEXT = re.compile(r"\b(before|by|until)\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\b", re.I)


def window_for(timeframe: str, tz: str, windows: dict, now: datetime | None = None) -> dict | None:
    """Return today's window for a timeframe label in the company zone, as UTC ISO + status."""
    spec = windows.get(timeframe)
    if not spec:
        m = TIME_IN_TEXT.search(timeframe or "")
        if m:
            h = int(m.group(2)) % 12 if m.group(4) else int(m.group(2))
            if (m.group(4) or "").upper() == "PM":
                h += 12
            spec = ["00:00", f"{h:02d}:{int(m.group(3) or 0):02d}"]
    if not spec:
        return None
    z = zone(tz)
    now_utc = now or datetime.now(timezone.utc)
    day = now_utc.astimezone(z).date()
    start = datetime.combine(day, _hm(spec[0]), z)
    end = datetime.combine(day, _hm(spec[1]), z)
    if end <= start:
        end += timedelta(days=1)
    status = "upcoming" if now_utc < start else ("active" if now_utc <= end else "closed")
    return {"start": start.astimezone(timezone.utc).isoformat(), "end": end.astimezone(timezone.utc).isoformat(),
            "local": f"{spec[0]}–{spec[1]}", "timezone": tz, "status": status}
