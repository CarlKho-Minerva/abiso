"""Calendar upsert against an in-memory fake of the Calendar API (no network)."""
import base64
import itertools
from datetime import datetime

from abiso.schema import Event, Status
from serve import calendar, store


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeCalendar:
    """Just enough of googleapiclient's calendar v3 surface, with call logging."""

    def __init__(self):
        self.events_by_id, self.cals, self.calls = {}, [], []
        self._ids = itertools.count(1)

    # events()
    def events(self):
        return self

    def list(self, calendarId, privateExtendedProperty=None, pageToken=None, **kw):
        self.calls.append("list")
        if privateExtendedProperty is None:            # calendarList().list()
            return _Call(lambda: {"items": self.cals})
        key, val = privateExtendedProperty.split("=", 1)
        hits = [e for e in self.events_by_id.values()
                if e["extendedProperties"]["private"].get(key) == val]
        return _Call(lambda: {"items": hits})

    def insert(self, calendarId=None, body=None):
        self.calls.append("insert")
        if calendarId is None:                         # calendars().insert()
            cal = {"id": f"cal{next(self._ids)}@group.calendar.google.com", **body}
            self.cals.append(cal)
            return _Call(lambda: cal)
        ev = {"id": f"ev{next(self._ids)}", **body}
        self.events_by_id[ev["id"]] = ev
        return _Call(lambda: ev)

    def update(self, calendarId, eventId, body):
        self.calls.append("update")
        self.events_by_id[eventId] = {"id": eventId, **body}
        return _Call(lambda: self.events_by_id[eventId])

    def delete(self, calendarId, eventId):
        self.calls.append("delete")
        return _Call(lambda: self.events_by_id.pop(eventId))

    def calendarList(self):
        return _CalList(self)

    def calendars(self):
        return self

    def acl(self):
        raise AssertionError("sharing must never be touched")


class _CalList:
    def __init__(self, fake):
        self.fake = fake

    def list(self, pageToken=None):
        return _Call(lambda: {"items": self.fake.cals})


def load():
    return store.load_events(), store.load_posts()


def by_id(events, eid):
    return next(e for e in events if e.event_id == eid)


def test_upsert_is_idempotent():
    events, posts = load()
    fake = FakeCalendar()
    first = calendar.upsert(fake, "cal", events, posts)
    second = calendar.upsert(fake, "cal", events, posts)
    assert len(first["created"]) == 5 and first["updated"] == []
    assert second["created"] == [] and sorted(second["updated"]) == sorted(first["created"])
    assert len(fake.events_by_id) == 5


def test_skips_not_disruption_and_undated_and_reports_them():
    events, posts = load()
    r = calendar.upsert(FakeCalendar(), "cal", events, posts)
    assert r["skipped_not_disruption"] == ["synthetic-ev-notdisruption-1007"]
    assert r["skipped_no_date"] == ["synthetic-ev-power-agus-tbd"]


def test_title_is_summary_and_description_lists_source_urls():
    events, posts = load()
    body = calendar.event_body(by_id(events, "synthetic-ev-power-basak-1004"), posts)
    assert body["summary"] == "No power, Basak and parts of Pusok, Sun 4 Oct, 8 AM to 5 PM"
    assert "https://example.invalid/SYNTHETIC/llcpio/posts/1001" in body["description"]
    assert body["start"] == {"dateTime": "2026-10-04T08:00:00+08:00", "timeZone": "Asia/Manila"}
    assert body["extendedProperties"]["private"]["abiso_event_id"] == "synthetic-ev-power-basak-1004"


def test_cancelled_event_is_updated_to_say_cancelled_not_deleted():
    events, posts = load()
    fake = FakeCalendar()
    ev = by_id(events, "synthetic-ev-power-maribago-1007")
    scheduled = ev.model_copy(update={"status": Status.scheduled})
    calendar.upsert(fake, "cal", [scheduled], posts)
    calendar.upsert(fake, "cal", [ev], posts)
    assert "delete" not in fake.calls
    (stored,) = fake.events_by_id.values()
    assert stored["summary"].startswith("CANCELLED: ")
    assert stored["description"].startswith("CANCELLED.")
    assert "https://example.invalid/SYNTHETIC/llcpio/posts/1005" in stored["description"]


def test_all_day_uses_exclusive_end_date():
    events, posts = load()
    body = calendar.event_body(by_id(events, "synthetic-ev-classes-city-1006"), posts)
    assert body["start"] == {"date": "2026-10-06"} and body["end"] == {"date": "2026-10-07"}


def test_missing_end_gets_one_hour_and_says_so():
    ev = Event(event_id="x", type="power", start="2026-10-04T08:00:00+08:00",
               summary="No power, Basak", source_post_ids=["nope"], confidence=0.5, model="t")
    body = calendar.event_body(ev, {})
    assert body["end"]["dateTime"] == "2026-10-04T09:00:00+08:00"
    assert "End time: not stated" in body["description"]
    assert "(post id nope; link not captured)" in body["description"]   # no invented URL


def test_create_calendar_is_idempotent_and_private():
    fake = FakeCalendar()
    cid, created = calendar.create_calendar(fake)
    cid2, created2 = calendar.create_calendar(fake)
    assert created and not created2 and cid == cid2
    assert fake.cals[0]["summary"] == "Abiso: Lapu-Lapu City"   # acl() would have raised


def test_delete_removes_only_named_events():
    events, posts = load()
    fake = FakeCalendar()
    calendar.upsert(fake, "cal", events, posts)
    gone = calendar.delete(fake, "cal", ["synthetic-ev-water-pajo-1005", "never-written"])
    assert gone == ["synthetic-ev-water-pajo-1005"] and len(fake.events_by_id) == 4


def test_subscribe_link_encodes_calendar_id():
    link = calendar.subscribe_link("abc@group.calendar.google.com")
    cid = link.split("cid=")[1]
    assert base64.urlsafe_b64decode(cid + "=" * (-len(cid) % 4)).decode() == "abc@group.calendar.google.com"


# ---------- prep actions, evening-before reminder, per-barangay view (spec 2a) ----------

def test_reminder_fires_at_8pm_the_evening_before():
    events, posts = load()
    minutes = {eid: calendar.event_body(by_id(events, eid), posts)["reminders"]["overrides"][0]["minutes"]
               for eid in ("synthetic-ev-power-basak-1004",      # Sun 08:00 -> Sat 20:00
                           "synthetic-ev-water-pajo-1005",       # Mon 22:00 -> Sun 20:00
                           "synthetic-ev-classes-city-1006")}    # all day Tue -> Mon 20:00
    assert minutes == {"synthetic-ev-power-basak-1004": 12 * 60,
                       "synthetic-ev-water-pajo-1005": 26 * 60,
                       "synthetic-ev-classes-city-1006": 4 * 60}


def test_cancelled_event_has_no_reminder():
    events, posts = load()
    body = calendar.event_body(by_id(events, "synthetic-ev-power-maribago-1007"), posts)
    assert body["reminders"] == {"useDefault": False, "overrides": []}


def test_description_leads_with_type_specific_prep():
    events, posts = load()
    water = calendar.event_body(by_id(events, "synthetic-ev-water-pajo-1005"), posts)["description"]
    power = calendar.event_body(by_id(events, "synthetic-ev-power-basak-1004"), posts)["description"]
    assert water.startswith("Prepare the evening before. Water cut tomorrow 10 PM: Fill containers")
    assert power.startswith("Prepare the evening before. Brownout tomorrow 8 AM: Freeze ice packs")
    assert "charge phones and power banks" in power and "medicine that needs cold" in power


def test_clock_writes_times_the_way_the_city_does():
    ph = store.PH
    assert [calendar.clock(datetime(2026, 10, 4, h, m, tzinfo=ph)) for h, m in
            ((8, 0), (22, 0), (12, 0), (0, 0), (8, 30))] == ["8 AM", "10 PM", "12 NN", "12 AM", "8:30 AM"]


def test_basak_view_carries_only_basak_and_city_wide_plus_prep_events():
    events, posts = load()
    fake = FakeCalendar()
    r = calendar.upsert(fake, "basak", events, posts, barangay="Basak")
    assert sorted(r["created"]) == ["synthetic-ev-classes-city-1006", "synthetic-ev-power-basak-1004"]
    assert "synthetic-ev-water-pajo-1005" in r["skipped_other_area"]
    assert "synthetic-ev-power-maribago-1007" in r["skipped_other_area"]
    titles = sorted(e["summary"] for e in fake.events_by_id.values())
    assert "Prep tonight: Brownout tomorrow 8 AM" in titles
    assert "Prep tonight: No classes tomorrow" in titles
    assert len(fake.events_by_id) == 4
    calendar.upsert(fake, "basak", events, posts, barangay="Basak")       # idempotent, prep included
    assert len(fake.events_by_id) == 4


def test_prep_event_is_at_8pm_and_says_cancelled_when_cancelled():
    events, posts = load()
    ev = by_id(events, "synthetic-ev-power-maribago-1007")
    body = calendar.prep_body(ev, posts)
    assert body["start"]["dateTime"] == "2026-10-06T20:00:00+08:00"
    assert body["summary"].startswith("CANCELLED, no need to prepare")
    live = calendar.prep_body(ev.model_copy(update={"status": Status.scheduled}), posts)
    assert live["summary"] == "Prep tonight: Brownout tomorrow 9 AM"
    assert "https://example.invalid/SYNTHETIC/llcpio/posts/1004" in live["description"]


def test_delete_removes_prep_companions_too():
    events, posts = load()
    fake = FakeCalendar()
    calendar.upsert(fake, "basak", events, posts, barangay="Basak")
    calendar.delete(fake, "basak", [e.event_id for e in events])
    assert fake.events_by_id == {}


def test_barangay_calendar_name():
    assert calendar.calendar_name("Basak") == "Abiso: Basak"
    assert calendar.calendar_name(None) == "Abiso: Lapu-Lapu City"
