"""Read data/events.jsonl and answer the three questions every door asks.

WHY ONE MODULE FOR THREE DOORS: Messenger (through the ADK agent), the MCP server
and the A2A card all call these same functions. If the filtering lived in each
door, a Lapu-Lapu resident could get one answer in Messenger and a different one
from an agent asking over MCP. Plain, deterministic code: no model call happens
here, so the tools are free to call and testable without a network (#testability).

WHY "found": false IS A FIRST-CLASS ANSWER: a wrong "walang pasok" is worse than
none (spec section 3, obstacle 5). Every function returns an explicit `found`
flag and a `note` saying what was searched and as of when, so the agent can say
"no official notice found" instead of guessing.

Paths come from the environment so tests and Cloud Run can point elsewhere:
    ABISO_EVENTS  default data/events.jsonl
    ABISO_POSTS   default data/raw/posts.jsonl (used only to turn post ids into links)
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from abiso.schema import DisruptionType, Event, Post

PH = timezone(timedelta(hours=8))
REPO = Path(__file__).resolve().parent.parent
MAX_RESULTS = 10
OFFICIAL_PAGE = "the official Lapu-Lapu City Facebook page"

# The words people actually type for a date, in the three languages Abiso answers in.
RELATIVE_DAYS = {
    0: {"today", "now", "tonight", "ngayon", "ngayong araw", "karon", "karong adlawa"},
    1: {"tomorrow", "bukas", "ugma"},
    -1: {"yesterday", "kahapon", "gahapon"},
}

# Words people use for a disruption type, mapped onto the schema's enum.
TYPE_ALIASES = {
    "brownout": DisruptionType.power, "power": DisruptionType.power,
    "power interruption": DisruptionType.power, "kuryente": DisruptionType.power,
    "walay suga": DisruptionType.power, "blackout": DisruptionType.power,
    "water": DisruptionType.water, "tubig": DisruptionType.water,
    "water interruption": DisruptionType.water,
    "class_suspension": DisruptionType.class_suspension, "classes": DisruptionType.class_suspension,
    "class suspension": DisruptionType.class_suspension, "walang pasok": DisruptionType.class_suspension,
    "pasok": DisruptionType.class_suspension, "klase": DisruptionType.class_suspension,
    "work_suspension": DisruptionType.work_suspension, "work suspension": DisruptionType.work_suspension,
    "road": DisruptionType.road, "road closure": DisruptionType.road, "traffic": DisruptionType.road,
    "weather": DisruptionType.weather, "typhoon": DisruptionType.weather, "bagyo": DisruptionType.weather,
    "other_advisory": DisruptionType.other_advisory, "advisory": DisruptionType.other_advisory,
}

CITY_NAMES = {"lapu-lapu", "lapulapu", "lapu lapu", "lapu-lapu city", "lapulapu city",
              "lapu lapu city", "llc", "opon"}   # not "mactan": Mactan is also a barangay


def _now() -> datetime:
    """Current time in the Philippines. Tests monkeypatch this."""
    return datetime.now(PH)


def events_path() -> Path:
    return Path(os.environ.get("ABISO_EVENTS", REPO / "data" / "events.jsonl"))


def posts_path() -> Path:
    return Path(os.environ.get("ABISO_POSTS", REPO / "data" / "raw" / "posts.jsonl"))


def _read_jsonl(path: Path, model):
    """Parse one model per line. A bad line raises, naming the file and line.

    Silence is the bug: skipping a malformed event would make a real brownout
    disappear from every answer without anyone noticing.
    """
    out = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(model.model_validate_json(line))
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not a valid {model.__name__}: {e}") from e
    return out


# ABISO_STORE=firestore (set on Cloud Run): read the feed from Firestore (serve/fs.py), which the
# refresh job keeps current, cached for CACHE_S so a page load is not a database read per call.
CACHE_S = float(os.environ.get("ABISO_CACHE_S", "60"))
_cache: dict = {}


def _firestore() -> bool:
    return os.environ.get("ABISO_STORE", "file") == "firestore"


def _from_firestore(key: str):
    import time as _t
    hit = _cache.get(key)
    if hit and _t.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    from serve import fs
    if key == "events":
        val = [Event.model_validate(d) for d in fs.read_all("events")]
    elif key == "posts":
        val = {p.post_id: p for p in (Post.model_validate(d) for d in fs.read_all("posts"))}
    else:
        val = fs.get_meta()
    _cache[key] = (_t.monotonic(), val)
    return val


def clear_cache() -> None:
    _cache.clear()


def load_events() -> list[Event]:
    if _firestore():
        return _from_firestore("events")
    p = events_path()
    if not p.exists():
        raise FileNotFoundError(
            f"No events file at {p}. Set ABISO_EVENTS, or run the compiler to create it.")
    return _read_jsonl(p, Event)


def load_posts() -> dict[str, Post]:
    """Posts by id, for source links. Missing file means no links, not a crash."""
    if _firestore():
        return _from_firestore("posts")
    p = posts_path()
    if not p.exists():
        return {}
    return {post.post_id: post for post in _read_jsonl(p, Post)}


def source_links(event: Event, posts: dict[str, Post]) -> list[str]:
    """The official post URL for each source id. Never invents a URL."""
    return [posts[pid].url for pid in event.source_post_ids if pid in posts]


# ---------- matching ----------

def norm(s: str) -> str:
    """Case, accent and punctuation-insensitive form: 'Brgy. Punta Engaño' -> 'punta engano'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(brgy|barangay|bgy|sitio)\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _squash(s: str) -> str:
    return norm(s).replace(" ", "")   # 'caw-oy' == 'cawoy' == 'caw oy'


def area_matches(event: Event, area: str) -> bool:
    """True if the event covers this place. City-wide events cover every place."""
    if not event.areas:
        return True
    q = _squash(area)
    if not q or q in {_squash(c) for c in CITY_NAMES}:
        return True
    for a in event.areas:
        if a.barangay and _squash(a.barangay) == q:
            return True
        if a.detail and q in _squash(a.detail):
            return True
    return False


def parse_day(text: str, now: Optional[datetime] = None) -> date:
    """'2026-10-04', 'today', 'bukas', 'ugma' -> a date in Philippine time."""
    now = now or _now()
    t = norm(text)
    for offset, words in RELATIVE_DAYS.items():
        if t in words:
            return (now + timedelta(days=offset)).date()
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        raise ValueError(
            f"Could not read the date {text!r}. Use YYYY-MM-DD, 'today' or 'tomorrow'.") from None


def window(event: Event) -> Optional[tuple[datetime, datetime]]:
    """When the event is in effect, in PH time. None if the notice gave no date."""
    if event.start is None:
        return None
    start = event.start if event.start.tzinfo else event.start.replace(tzinfo=PH)
    if event.all_day:
        first = start.astimezone(PH).date()
        last = (event.end.astimezone(PH).date() if event.end else first)
        return (datetime.combine(first, time(0), PH), datetime.combine(last + timedelta(days=1), time(0), PH))
    if event.end is not None:
        end = event.end if event.end.tzinfo else event.end.replace(tzinfo=PH)
    else:
        # No end stated: treat it as touching the rest of its start day only.
        end = datetime.combine(start.astimezone(PH).date() + timedelta(days=1), time(0), PH)
    return start, end


def on_day(event: Event, day: date) -> bool:
    w = window(event)
    if w is None:
        return False
    lo = datetime.combine(day, time(0), PH)
    hi = lo + timedelta(days=1)
    return w[0] < hi and w[1] > lo


def resolve_type(type_: Optional[str]) -> Optional[DisruptionType]:
    if type_ is None or not type_.strip():
        return None
    t = type_.strip().lower()
    if t in TYPE_ALIASES:
        return TYPE_ALIASES[t]
    try:
        return DisruptionType(t)
    except ValueError:
        raise ValueError(
            f"Unknown disruption type {type_!r}. Use one of: "
            + ", ".join(d.value for d in DisruptionType if d != DisruptionType.not_disruption)) from None


# ---------- the three tools ----------

def _area_text(a) -> str:
    """'Basak' + 'Basak, Lapu-Lapu City; Modena Subd.' reads once, not 'Basak, Basak, ...'."""
    if a.barangay and a.detail and norm(a.detail).startswith(norm(a.barangay)):
        return a.detail
    return ", ".join(x for x in (a.barangay, a.detail) if x)


def _as_answer(event: Event, posts: dict[str, Post]) -> dict:
    return {
        "summary": event.summary,
        "type": event.type.value,
        "status": event.status.value,
        "cancelled": event.status.value == "cancelled",
        "areas": [_area_text(a) for a in event.areas] or ["city-wide"],
        "start": event.start.isoformat() if event.start else None,
        "end": event.end.isoformat() if event.end else None,
        "all_day": event.all_day,
        "date_announced": event.start is not None,
        "source_links": source_links(event, posts),
        "event_id": event.event_id,
    }


def updated_at() -> Optional[datetime]:
    """When the feed was last rebuilt (Firestore meta, or the events file's mtime)."""
    try:
        if _firestore():
            v = _from_firestore("meta").get("updated_at")
            return datetime.fromisoformat(v).astimezone(PH) if v else None
        return datetime.fromtimestamp(events_path().stat().st_mtime, PH)
    except (OSError, ValueError):
        return None


def _freshness(events: list[Event]) -> str:
    if _firestore():
        ts = updated_at()
        return ts.strftime("%Y-%m-%d %H:%M PH time") if ts else "unknown"
    try:
        ts = datetime.fromtimestamp(events_path().stat().st_mtime, PH)
        return ts.strftime("%Y-%m-%d %H:%M PH time")
    except OSError:
        return "unknown"


def disruptions(area: Optional[str] = None, date: Optional[str] = None,
                type: Optional[str] = None) -> dict:
    """Find official disruption notices for Lapu-Lapu City.

    Args:
      area: a barangay or place name, e.g. "Basak", "Pusok", "Punta Engaño".
        Leave empty for the whole city. City-wide notices always match.
      date: "YYYY-MM-DD", or "today" / "tomorrow" (also "bukas", "ugma").
        Leave empty for every notice regardless of date.
      type: one of power, water, class_suspension, work_suspension, road,
        weather, other_advisory (words like "brownout" also work).

    Returns:
      {"found": bool, "events": [...], "note": str}. Each event has a plain
      summary, status (a cancelled event says so), areas, start/end, and
      source_links to the official post. found=false means no official notice
      was found: say that, do not guess.
    """
    events = load_events()
    posts = load_posts()
    want_type = resolve_type(type)
    day = parse_day(date) if date and date.strip() else None

    hits = []
    for e in events:
        if e.type == DisruptionType.not_disruption:
            continue
        if want_type and e.type != want_type:
            continue
        if area and area.strip() and not area_matches(e, area):
            continue
        if day and not on_day(e, day):
            continue
        hits.append(e)
    # Current and coming notices first (soonest first), then past ones (most recent first), then undated.
    now = _now()
    def order(e: Event):
        w = window(e)
        if w is None:
            return (2, 0.0)
        if w[1] >= now:
            return (0, w[0].timestamp())
        return (1, -w[0].timestamp())
    hits.sort(key=order)

    asked = ", ".join(x for x in (
        f"type {want_type.value}" if want_type else "",
        f"area {area}" if area else "",
        f"date {day.isoformat()}" if day else "") if x) or "everything"
    if not hits:
        note = (f"No official notice found for {asked}. Abiso's records were last updated "
                f"{_freshness(events)}. This is not proof nothing is happening; "
                f"check {OFFICIAL_PAGE}.")
    else:
        note = f"{len(hits)} official notice(s) for {asked}."
        if len(hits) > MAX_RESULTS:
            note += f" Showing the first {MAX_RESULTS}."
    return {"found": bool(hits), "events": [_as_answer(e, posts) for e in hits[:MAX_RESULTS]],
            "note": note}


def brownouts(area: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Find official power interruption (brownout) notices in Lapu-Lapu City.

    Args:
      area: a barangay or place name, e.g. "Basak". Empty for the whole city.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official notice was found.
    """
    return disruptions(area=area, date=date, type="power")


def classes_suspended(city: str = "Lapu-Lapu City", date: Optional[str] = None) -> dict:
    """Check whether classes are officially suspended ("walang pasok") in a city.

    Abiso only reads Lapu-Lapu City's official notices; for any other city it
    says so instead of answering.

    Args:
      city: the city name. Only Lapu-Lapu City is covered.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official suspension notice was found.
    """
    if city and norm(city) not in {norm(c) for c in CITY_NAMES}:
        return {"found": False, "events": [],
                "note": (f"Abiso only covers Lapu-Lapu City, not {city}. "
                         f"Check {city}'s official page.")}
    return disruptions(area=None, date=date, type="class_suspension")


def water_interruptions(area: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Find official water interruption notices in Lapu-Lapu City.

    Args:
      area: a barangay or place name, e.g. "Basak". Empty for the whole city.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official notice was found.
    """
    return disruptions(area=area, date=date, type="water")


def road_closures(area: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Find official road closure and rerouting notices in Lapu-Lapu City.

    Args:
      area: a barangay, road or place name. Empty for the whole city.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official notice was found.
    """
    return disruptions(area=area, date=date, type="road")


def weather_advisories(area: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Find official weather advisories (typhoon, heavy rain) relayed for Lapu-Lapu City.

    Args:
      area: a barangay or place name. Empty for the whole city.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official notice was found.
    """
    return disruptions(area=area, date=date, type="weather")


def work_suspensions(area: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Find official work suspension notices in Lapu-Lapu City.

    Args:
      area: a barangay or place name. Empty for the whole city.
      date: "YYYY-MM-DD", or "today" / "tomorrow" ("bukas", "ugma"). Empty for any date.

    Returns:
      Same shape as disruptions(). found=false means no official notice was found.
    """
    return disruptions(area=area, date=date, type="work_suspension")


# What MCP clients get: the general query plus one tool per type.
TOOLS = [disruptions, brownouts, water_interruptions, classes_suspended, work_suspensions,
         road_closures, weather_advisories]
