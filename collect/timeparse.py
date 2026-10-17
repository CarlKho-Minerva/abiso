"""Turn the timestamp a platform shows into an absolute Asia/Manila datetime, or refuse.

Rule: never invent precision. A date with no time of day ("August 5", "2d") returns None;
the caller keeps the raw string. Relative minutes/hours are accepted (error under an hour).
Absolute strings Facebook renders are in the *viewer's* timezone (the capture machine),
so they are read in `viewer_tz` and converted to +08:00.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PH = timezone(timedelta(hours=8))

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_REL = re.compile(
    r"^(?:(?P<n>\d+)|an?)\s*(?P<u>m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b(?:\s+ago)?$")
_CLOCK = r"(?P<hh>\d{1,2}):(?P<mm>\d{2})\s*(?P<ap>am|pm)"
_ABS = re.compile(
    r"^(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+)?"
    r"(?P<mon>[a-z]+)\.?\s+(?P<d>\d{1,2})(?:,?\s+(?P<y>\d{4}))?"
    r"(?:,?\s+(?:at\s+)?" + _CLOCK + r")?$")
_YEST = re.compile(r"^yesterday(?:\s+at\s+" + _CLOCK + r")?$")


def from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, PH)


def _clock(m: re.Match) -> tuple[int, int]:
    h = int(m["hh"]) % 12 + (12 if m["ap"] == "pm" else 0)
    return h, int(m["mm"])


def parse_fb_time(raw: str, captured_at: datetime, viewer_tz: str) -> datetime | None:
    """Resolve a Facebook timestamp string. Returns +08:00 datetime or None if unresolvable."""
    s = " ".join(raw.strip().lower().replace(" ", " ").split())
    if not s:
        return None
    if s in ("just now", "now"):
        return captured_at.astimezone(PH)
    m = _REL.match(s)
    if m:
        n = int(m["n"]) if m["n"] else 1
        unit = timedelta(minutes=1) if m["u"].startswith("m") else timedelta(hours=1)
        return (captured_at - n * unit).astimezone(PH).replace(second=0, microsecond=0)
    tz = ZoneInfo(viewer_tz)
    local_now = captured_at.astimezone(tz)
    m = _YEST.match(s)
    if m:
        if not m["hh"]:
            return None
        h, mi = _clock(m)
        d = (local_now - timedelta(days=1)).date()
        return datetime(d.year, d.month, d.day, h, mi, tzinfo=tz).astimezone(PH)
    m = _ABS.match(s)
    if m and m["mon"] in _MONTHS and m["hh"]:
        mon, day = _MONTHS[m["mon"]], int(m["d"])
        year = int(m["y"]) if m["y"] else local_now.year
        h, mi = _clock(m)
        dt = datetime(year, mon, day, h, mi, tzinfo=tz)
        if not m["y"] and dt > local_now:  # "December 30" seen in January is last year
            dt = dt.replace(year=year - 1)
        return dt.astimezone(PH)
    return None
