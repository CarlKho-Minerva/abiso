"""Put compiled Events on Google Calendars: one for the city, one per barangay.

WHY UPSERT BY event_id: the compiler re-runs; a notice gets extended or cancelled.
Each Google event carries the Abiso event_id in a private extended property and
is looked up by it before writing, so a re-run updates the one event instead of
stacking copies. Same pattern as ~/CODELocalProjects/lifeos/gcal.py, whose OAuth
token store this reuses (gcal.service), so there is no second login to keep alive.

WHY CANCELLED EVENTS STAY: a resident who saw "No power, Maribago" yesterday needs
to see that it was called off. Deleting the event would look the same as "Abiso
lost it". The event stays, titled "CANCELLED: ...".

WHY PREP ACTIONS AND A PREP EVENT (spec section 2a): the household's problem is
spoiled food and no water, so the output is what to do the evening before. Every
event carries the prep text and a popup at 20:00 the evening before. But a popup
set through the API only fires for the account that wrote it, not for people who
subscribe to or are shared the calendar. So a barangay calendar (the resident
view) also gets a short "Prep tonight" event at 20:00 the evening before, which
shows up in their day view and fires their own default notifications.

WHY A CALENDAR PER BARANGAY: "does this affect me" is the question. The Basak
calendar only carries events naming Basak, plus city-wide ones.

WHY create-calendar NEVER TOUCHES SHARING: making a calendar public is Carl's
decision (spec section 10). This module creates calendars private and stops.

    uv run python -m serve.calendar create-calendar [--barangay Basak]
    uv run python -m serve.calendar upsert --calendar <id> [--barangay Basak] [--events data/events.jsonl]
    uv run python -m serve.calendar delete --calendar <id> --events serve/fixtures/events.jsonl
    uv run python -m serve.calendar link --calendar <id>
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Optional

from abiso.schema import DisruptionType, Event, Post, Status
from serve import store

CITY_CALENDAR = "Abiso: Lapu-Lapu City"
CALENDAR_NAME = CITY_CALENDAR
ACCOUNT = "carl@themildlyusefulcompany.com"
TZ = "Asia/Manila"
KEY_PROP = "abiso_event_id"
PREP_SUFFIX = "#prep"
PREP_AT = time(20, 0)                  # the evening before, Philippine time
PREP_LENGTH = timedelta(minutes=15)
LIFEOS = Path(os.environ.get("ABISO_LIFEOS_DIR", Path.home() / "CODELocalProjects" / "lifeos"))
DEFAULT_LENGTH = timedelta(hours=1)
TYPE_LABEL = {
    DisruptionType.power: "Power interruption",
    DisruptionType.water: "Water interruption",
    DisruptionType.class_suspension: "Class suspension",
    DisruptionType.work_suspension: "Work suspension",
    DisruptionType.road: "Road closure",
    DisruptionType.weather: "Weather advisory",
    DisruptionType.other_advisory: "Advisory",
}
# (what to call it in a prep line, what to do tonight). Plain and short on purpose.
PREP = {
    DisruptionType.power: ("Brownout", "Freeze ice packs or bottles of water, charge phones and "
                           "power banks, move medicine that needs cold to a cooler."),
    DisruptionType.water: ("Water cut", "Fill containers and the drum, store drinking water, "
                           "do laundry and dishes early."),
    DisruptionType.class_suspension: ("No classes", "Plan who watches the children."),
    DisruptionType.work_suspension: ("No work", "Check with your employer."),
    DisruptionType.road: ("Road closed", "Plan another route or leave earlier."),
    DisruptionType.weather: ("Weather advisory", "Charge phones, keep a flashlight ready, "
                             "bring in loose items."),
    DisruptionType.other_advisory: ("Advisory", "Read the official post."),
}


# ---------- pure: Event -> Google Calendar body ----------

def calendar_title(event: Event) -> str:
    if event.status == Status.cancelled:
        return f"CANCELLED: {event.summary}"
    return event.summary


def clock(dt: datetime) -> str:
    """8 AM, 10 PM, 8:30 AM, 12 NN: how the city writes times."""
    dt = dt.astimezone(store.PH)
    if dt.hour == 12 and dt.minute == 0:
        return "12 NN"
    h = dt.hour % 12 or 12
    return f"{h}{dt.strftime(':%M') if dt.minute else ''} {'AM' if dt.hour < 12 else 'PM'}"


def prep_heading(event: Event) -> str:
    """'Water cut tomorrow 10 PM'. Read the evening before, so 'tomorrow' is right."""
    label, _ = PREP[event.type]
    when = "" if event.all_day or event.start is None else f" {clock(event.start)}"
    return f"{label} tomorrow{when}"


def prep_line(event: Event) -> str:
    if event.status == Status.cancelled:
        return "Cancelled: nothing to prepare."
    return f"{prep_heading(event)}: {PREP[event.type][1]}"


def describe(event: Event, posts: dict[str, Post]) -> str:
    lines = []
    if event.status == Status.cancelled:
        lines += ["CANCELLED. The city called this off in a later post. Nothing is interrupted.", ""]
    else:
        lines += [f"Prepare the evening before. {prep_line(event)}", ""]
    lines.append(event.summary)
    lines.append(f"Type: {TYPE_LABEL.get(event.type, event.type.value)}. Status: {event.status.value}.")
    areas = [", ".join(x for x in (a.barangay, a.detail) if x) for a in event.areas]
    lines.append("Where: " + ("; ".join(areas) if areas else "city-wide"))
    if event.start and not event.end and not event.all_day:
        lines.append("End time: not stated in the notice.")
    lines += ["", "Official post(s):"]
    links = store.source_links(event, posts)
    lines += links or [f"(post id {pid}; link not captured)" for pid in event.source_post_ids]
    lines += ["", "Compiled by Abiso from the official post. If anything here differs from the post, the post is right."]
    return "\n".join(lines)


def prep_time(event: Event) -> datetime:
    """20:00 PH on the evening before the event takes effect."""
    starts = store.window(event)[0]
    return datetime.combine(starts.astimezone(store.PH).date() - timedelta(days=1), PREP_AT, store.PH)


def reminders(event: Event) -> dict:
    """A popup at 20:00 the evening before (for the calendar's owner). None if cancelled."""
    if event.status == Status.cancelled:
        return {"useDefault": False, "overrides": []}
    minutes = int((store.window(event)[0] - prep_time(event)).total_seconds() // 60)
    return {"useDefault": False, "overrides": [{"method": "popup", "minutes": minutes}]}


def event_body(event: Event, posts: dict[str, Post]) -> dict:
    """The Calendar API body for one Event. Raises ValueError if it cannot be placed."""
    if event.start is None:
        raise ValueError(f"{event.event_id}: no start date in the notice; cannot place it on a calendar")
    body = {
        "summary": calendar_title(event),
        "description": describe(event, posts),
        "extendedProperties": {"private": {KEY_PROP: event.event_id}},
        "transparency": "transparent",        # a brownout does not make you "busy"
        "reminders": reminders(event),
    }
    links = store.source_links(event, posts)
    if links:
        body["source"] = {"title": "Official post", "url": links[-1]}
    if event.all_day:
        first = event.start.astimezone(store.PH).date()
        last = event.end.astimezone(store.PH).date() if event.end else first
        body["start"] = {"date": first.isoformat()}
        body["end"] = {"date": (last + timedelta(days=1)).isoformat()}   # Google's end date is exclusive
    else:
        end = event.end or event.start + DEFAULT_LENGTH
        body["start"] = {"dateTime": event.start.isoformat(), "timeZone": TZ}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": TZ}
    return body


def prep_body(event: Event, posts: dict[str, Post]) -> dict:
    """The 'Prep tonight' companion event at 20:00 the evening before."""
    at = prep_time(event)
    if event.status == Status.cancelled:
        title = f"CANCELLED, no need to prepare: {prep_heading(event)}"
        rem = {"useDefault": False, "overrides": []}
    else:
        title = f"Prep tonight: {prep_heading(event)}"
        rem = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]}
    links = store.source_links(event, posts)
    desc = [prep_line(event), "", event.summary, ""] + (links or []) + [
        "", "Compiled by Abiso from the official post. If anything here differs from the post, the post is right."]
    return {
        "summary": title,
        "description": "\n".join(desc),
        "extendedProperties": {"private": {KEY_PROP: event.event_id + PREP_SUFFIX}},
        "transparency": "transparent",
        "start": {"dateTime": at.isoformat(), "timeZone": TZ},
        "end": {"dateTime": (at + PREP_LENGTH).isoformat(), "timeZone": TZ},
        "reminders": rem,
    }


def calendar_name(barangay: Optional[str]) -> str:
    return f"Abiso: {barangay}" if barangay else CITY_CALENDAR


def subscribe_link(calendar_id: str) -> str:
    """The 'add this calendar' link. Works for others only once Carl makes it public."""
    cid = base64.urlsafe_b64encode(calendar_id.encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/u/0?cid={cid}"


# ---------- Google side ----------

def build_service(account: str = ACCOUNT):
    """Calendar API client for Carl's account, via lifeos/gcal.py's token store.

    Loaded by path because lifeos is a folder of scripts, not a package.
    """
    path = LIFEOS / "gcal.py"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; set ABISO_LIFEOS_DIR")
    spec = importlib.util.spec_from_file_location("lifeos_gcal", path)
    gcal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcal)
    return gcal.service(False, account)


def find(svc, calendar_id: str, key: str):
    res = svc.events().list(calendarId=calendar_id,
                            privateExtendedProperty=f"{KEY_PROP}={key}",
                            maxResults=5, showDeleted=False, singleEvents=False).execute()
    items = res.get("items", [])
    return items[0] if items else None


def _put(svc, calendar_id: str, key: str, body: dict) -> str:
    existing = find(svc, calendar_id, key)
    if existing:
        svc.events().update(calendarId=calendar_id, eventId=existing["id"], body=body).execute()
        return "updated"
    svc.events().insert(calendarId=calendar_id, body=body).execute()
    return "created"


def upsert(svc, calendar_id: str, events: Iterable[Event], posts: dict[str, Post],
           barangay: Optional[str] = None) -> dict:
    """Create or update one calendar event per disruption. Returns what happened to each.

    With a barangay, only events covering it (or the whole city) are written, each
    with a "Prep tonight" companion. not_disruption events are skipped on purpose;
    undated events are skipped and reported, because a calendar entry on a made-up
    date is worse than none.
    """
    report = {"created": [], "updated": [], "prep": [], "skipped_other_area": [],
              "skipped_not_disruption": [], "skipped_no_date": []}
    for e in events:
        if e.type == DisruptionType.not_disruption:
            report["skipped_not_disruption"].append(e.event_id)
            continue
        if barangay and not store.area_matches(e, barangay):
            report["skipped_other_area"].append(e.event_id)
            continue
        if e.start is None:
            report["skipped_no_date"].append(e.event_id)
            continue
        report[_put(svc, calendar_id, e.event_id, event_body(e, posts))].append(e.event_id)
        if barangay:
            _put(svc, calendar_id, e.event_id + PREP_SUFFIX, prep_body(e, posts))
            report["prep"].append(e.event_id)
    return report


def delete(svc, calendar_id: str, event_ids: Iterable[str]) -> list[str]:
    """Delete the calendar events (and prep companions) for these Abiso ids. Only for test fixtures."""
    gone = []
    for eid in event_ids:
        for key in (eid, eid + PREP_SUFFIX):
            ev = find(svc, calendar_id, key)
            if ev:
                svc.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()
                gone.append(key)
    return gone


def create_calendar(svc, name: str = CITY_CALENDAR) -> tuple[str, bool]:
    """Find or create a private secondary calendar by name. Returns (id, created).

    Never calls acl(): a new secondary calendar is private to its owner, and
    publishing it is Carl's call.
    """
    page = None
    while True:
        res = svc.calendarList().list(pageToken=page).execute()
        for c in res.get("items", []):
            if c.get("summary") == name:
                return c["id"], False
        page = res.get("nextPageToken")
        if not page:
            break
    where = "Lapu-Lapu City" if name == CITY_CALENDAR else f"Barangay {name.removeprefix('Abiso: ')}, Lapu-Lapu City"
    cal = svc.calendars().insert(body={
        "summary": name,
        "timeZone": TZ,
        "description": (f"Official disruption notices for {where} (power, water, class "
                        "suspensions, road closures, weather), each linked to the official post, "
                        "with what to prepare the evening before. Compiled by Abiso."),
    }).execute()
    return cal["id"], True


# ---------- CLI ----------

def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", default=ACCOUNT)
    sub = p.add_subparsers(dest="cmd", required=True)
    cc = sub.add_parser("create-calendar")
    cc.add_argument("--barangay", default=None, help="make the per-barangay view, e.g. Basak")
    for name in ("upsert", "delete"):
        sp = sub.add_parser(name)
        sp.add_argument("--calendar", required=True)
        sp.add_argument("--barangay", default=None, help="only events covering this barangay, plus prep events")
        sp.add_argument("--events", default=str(store.events_path()))
        sp.add_argument("--posts", default=str(store.posts_path()))
    lk = sub.add_parser("link")
    lk.add_argument("--calendar", required=True)
    a = p.parse_args(argv)

    if a.cmd == "link":
        print(subscribe_link(a.calendar))
        return 0
    svc = build_service(a.account)
    if a.cmd == "create-calendar":
        cid, created = create_calendar(svc, calendar_name(a.barangay))
        print(f"{'created' if created else 'exists '} {cid}  (private; sharing unchanged)")
        return 0
    os.environ["ABISO_EVENTS"], os.environ["ABISO_POSTS"] = a.events, a.posts
    events = store.load_events()
    if a.cmd == "delete":
        gone = delete(svc, a.calendar, [e.event_id for e in events])
        print(f"deleted {len(gone)}: {', '.join(gone) or '-'}")
        return 0
    r = upsert(svc, a.calendar, events, store.load_posts(), barangay=a.barangay)
    for k, v in r.items():
        print(f"{k:24} {len(v):3}  {', '.join(v)}")
    if r["skipped_no_date"]:
        print(f"WARNING: {len(r['skipped_no_date'])} disruption(s) have no date and are not on the calendar.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
