"""Keep events true over time (subproblem 3).

    merged = reconcile(events, post_times)

Pure and deterministic: same input, same output, no clock, no I/O.

Events are processed oldest post first. Each incoming event either merges
into the most recent compatible event already seen, or starts a new one.
Compatible means all of:
  - same type (not_disruption never merges, it passes through);
  - overlapping area: either side city-wide (no areas), or a shared barangay,
    or the same unresolved place text;
  - overlapping or adjacent time (gap <= `gap`, default 1 h), where an
    update without its own times (a cancellation, "power restored",
    "extended until 8 PM") matches on type and area alone;
  - not from the same post (one post's events are distinct by construction);
  - when post times are known, posted within `max_lag` (default 7 days) of
    the event's latest source post, so an untimed "resolved" never reaches
    back to last month's outage.

How a merge updates the older event (the older event_id is kept, so a
calendar entry stays the same entry):
  new status cancelled -> cancelled; old times and areas kept
  new status resolved  -> resolved; end = the new end if stated
  otherwise            -> times become the union of both windows; if the end
                          moved later the status is `extended`; areas are
                          unioned; a plain re-announcement (`scheduled`)
                          never downgrades ongoing/extended.
  always               -> source_post_ids accumulate (order kept), summary,
                          confidence and model come from the newer post.

Several issuers (spec section 2a): MECO, the water provider, DepEd, the city,
the barangay and PAGASA can each post the same disruption. Their posts merge
like any other (same rules), with two differences when the new post comes
from an issuer with no earlier claim on the event:
  - a later end is a disagreement, not an extension (status not promoted);
  - a relay of the original notice never undoes another issuer's
    cancellation or resolution.
Disagreements are returned as Conflicts by `reconcile_with_conflicts`
(different start or end between two issuers' announcements; one issuer
announcing what another already cancelled). `reconcile_posts` takes issuers
from Post.page.

Known limit: a partial cancellation ("cancelled for Basak only") of a
multi-barangay event cancels the whole event. A correction that shortens a
window is kept as the longer window (never hides a disruption).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from abiso.places_match import normalise
from abiso.schema import DisruptionType, Event, Post, Status

UPDATE_STATUSES = {Status.extended, Status.cancelled, Status.resolved}
DEFAULT_GAP = timedelta(hours=1)
DEFAULT_MAX_LAG = timedelta(days=7)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


def window(ev: Event) -> Optional[Window]:
    """The time span an event claims; None if it states no time at all."""
    start, end = ev.start, ev.end
    if start is None and end is None:
        return None
    if start is None:
        return Window(end, end)  # type: ignore[arg-type]
    if end is None:
        end = start + timedelta(days=1) if ev.all_day else start
    return Window(start, end)


def area_keys(ev: Event) -> set[str]:
    keys = set()
    for a in ev.areas:
        if a.barangay:
            keys.add("b:" + a.barangay)
        elif a.detail:
            keys.add("d:" + normalise(a.detail))
    return keys


def areas_overlap(a: Event, b: Event) -> bool:
    ka, kb = area_keys(a), area_keys(b)
    if not a.areas or not b.areas:   # city-wide covers everything
        return True
    return bool(ka & kb)


def times_compatible(old: Event, new: Event, gap: timedelta) -> bool:
    wo, wn = window(old), window(new)
    if wn is None:
        # An untimed update refers to an existing event; an untimed new
        # announcement only matches another untimed one.
        return new.status in UPDATE_STATUSES or wo is None
    if wo is None:
        return True  # the older post had no time; the newer one fills it in
    if new.status in UPDATE_STATUSES and new.start is None:
        # "extended until 8 PM" / "restored at 3 PM": only an end time is given
        return wo.start <= wn.end + gap
    return wn.start - gap <= wo.end and wo.start - gap <= wn.end


def _merge(old: Event, new: Event, cross_issuer: bool = False, keep_terminal: bool = False) -> Event:
    """`cross_issuer`: the new event comes from an issuer with no earlier claim
    on this event (e.g. the city relaying MECO). A later end from a different
    issuer is a disagreement, not an extension, so the status is not promoted.
    `keep_terminal`: the event's cancelled/resolved status was set by a
    different issuer than the new post's; an announcement does not flip it."""
    sources = list(dict.fromkeys(old.source_post_ids + new.source_post_ids))
    common = dict(source_post_ids=sources, summary=new.summary,
                  confidence=new.confidence, model=new.model)

    if new.status == Status.cancelled:
        return old.model_copy(update=dict(
            status=Status.cancelled,
            start=old.start or new.start, end=old.end or new.end,
            areas=old.areas, **common))

    if new.status == Status.resolved:
        return old.model_copy(update=dict(
            status=Status.resolved,
            start=old.start or new.start, end=new.end or old.end,
            areas=old.areas, **common))

    wo, wn = window(old), window(new)
    start, end, all_day = old.start, old.end, old.all_day and new.all_day
    status = new.status
    if wo and wn:
        start = min(wo.start, wn.start)
        # Only an end someone stated (or an all-day day boundary) counts. A
        # start-only notice has no end, and the merge must not invent one.
        old_end = wo.end if (old.end or old.all_day) else None
        new_end = wn.end if (new.end or new.all_day) else None
        stated = [e for e in (old_end, new_end) if e is not None]
        end = max(stated) if stated else None
        if old_end is not None and new_end is not None and new_end > old_end and not cross_issuer:
            status = Status.extended
    elif wn:
        start, end, all_day = new.start, new.end, new.all_day

    if status == Status.scheduled and old.status in (Status.ongoing, Status.extended):
        status = old.status   # a re-announcement never downgrades
    elif keep_terminal and old.status in (Status.cancelled, Status.resolved):
        # Another issuer still announcing what one issuer already cancelled or
        # resolved: keep the update, the disagreement goes to the conflicts list.
        status = old.status

    areas = list(old.areas)
    if old.areas and new.areas:
        for a in new.areas:
            if a not in areas:
                areas.append(a)
    else:
        areas = []  # either side city-wide -> city-wide

    return old.model_copy(update=dict(status=status, start=start, end=end,
                                      all_day=all_day, areas=areas, **common))


# --------------------------------------------------------------------------- #
# Issuers and conflicts
# --------------------------------------------------------------------------- #
ANNOUNCE_STATUSES = {Status.scheduled, Status.ongoing}


class Claim(BaseModel):
    issuer: str
    post_id: str
    value: Optional[str] = None      # ISO time or status, as that issuer stated it


class Conflict(BaseModel):
    """Two issuers said different things about one merged event. Feeds the
    'inconsistencies between issuers' view (spec section 2a, scale step 3)."""
    event_id: str
    type: DisruptionType
    field: str                       # "start" | "end" | "status"
    claims: list[Claim]


@dataclass
class ReconcileResult:
    events: list[Event]
    conflicts: list[Conflict] = field(default_factory=list)


@dataclass(frozen=True)
class _Claim:
    issuer: Optional[str]
    post_id: str
    event: Event


def _issuer(ev: Event, post_issuers: dict[str, str]) -> Optional[str]:
    for p in ev.source_post_ids:
        if p in post_issuers:
            return post_issuers[p]
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _detect(event_id: str, prior: list[_Claim], new: _Claim, tol: timedelta) -> list[Conflict]:
    """Compare the new claim with each OTHER issuer's latest claim on this event."""
    if new.issuer is None:
        return []
    latest: dict[str, _Claim] = {}
    for c in prior:
        if c.issuer is not None and c.issuer != new.issuer:
            latest[c.issuer] = c          # prior is in processing order: last one wins
    out: list[Conflict] = []
    n = new.event
    for c in latest.values():
        o = c.event
        if o.status in ANNOUNCE_STATUSES and n.status in ANNOUNCE_STATUSES:
            for fname in ("start", "end"):
                a, b = getattr(o, fname), getattr(n, fname)
                if a is not None and b is not None and abs(a - b) > tol:
                    out.append(Conflict(event_id=event_id, type=n.type, field=fname, claims=[
                        Claim(issuer=c.issuer, post_id=c.post_id, value=_iso(a)),
                        Claim(issuer=new.issuer, post_id=new.post_id, value=_iso(b))]))
        if o.status == Status.cancelled and n.status in ANNOUNCE_STATUSES | {Status.extended}:
            out.append(Conflict(event_id=event_id, type=n.type, field="status", claims=[
                Claim(issuer=c.issuer, post_id=c.post_id, value=o.status.value),
                Claim(issuer=new.issuer, post_id=new.post_id, value=n.status.value)]))
    return out


def _post_time(ev: Event, post_times: dict[str, datetime]) -> Optional[datetime]:
    ts = [post_times[p] for p in ev.source_post_ids if p in post_times]
    return max(ts) if ts else None


def reconcile_with_conflicts(events: list[Event], post_times: Optional[dict[str, datetime]] = None,
                             post_issuers: Optional[dict[str, str]] = None,
                             gap: timedelta = DEFAULT_GAP, max_lag: timedelta = DEFAULT_MAX_LAG,
                             conflict_tolerance: timedelta = timedelta(0)) -> ReconcileResult:
    """Merge Events across posts and issuers.

    post_times:   post_id -> posted_at; sets processing order (else input order).
    post_issuers: post_id -> issuer (the page, e.g. "MECO", "LLCPIO"). Posts
                  from different issuers about the same disruption merge into
                  one event; where they disagree on start, end, or one issuer
                  cancelled and another still announces it, a Conflict is
                  recorded. Without issuers no conflicts are reported.
    conflict_tolerance: time differences up to this are not conflicts (default
                  0: "8:00" vs "8:30" matters to a household).
    """
    post_times = post_times or {}
    post_issuers = post_issuers or {}
    indexed = list(enumerate(events))

    def order_key(item: tuple[int, Event]):
        i, ev = item
        t = _post_time(ev, post_times)
        # Unknown post times sort after known ones, then by input position.
        return (t is None, t.timestamp() if t else 0.0, i)

    merged: list[Event] = []
    claims: list[list[_Claim]] = []
    conflicts: list[Conflict] = []
    for _, ev in sorted(indexed, key=order_key):
        claim = _Claim(_issuer(ev, post_issuers), ev.source_post_ids[0], ev)
        if ev.type == DisruptionType.not_disruption:
            merged.append(ev)
            claims.append([claim])
            continue
        new_t = _post_time(ev, post_times)
        eligible: list[tuple[float, int]] = []
        for j, cand in enumerate(merged):
            if cand.type != ev.type:
                continue
            if set(ev.source_post_ids) & set(cand.source_post_ids):
                continue
            if not areas_overlap(cand, ev) or not times_compatible(cand, ev, gap):
                continue
            cand_t = _post_time(cand, post_times)
            if new_t and cand_t and new_t - cand_t > max_lag:
                continue
            eligible.append((cand_t.timestamp() if cand_t else float("-inf"), j))
        # The most recently updated candidate wins; ties go to the later one.
        best = max(eligible)[1] if eligible else None
        if best is None:
            merged.append(ev)
            claims.append([claim])
            continue
        prior = claims[best]
        known = {c.issuer for c in prior if c.issuer is not None}
        cross = claim.issuer is not None and bool(known) and claim.issuer not in known
        terminal = [c for c in prior if c.event.status in (Status.cancelled, Status.resolved)]
        keep = (bool(terminal) and claim.issuer is not None and terminal[-1].issuer is not None
                and terminal[-1].issuer != claim.issuer)
        conflicts.extend(_detect(merged[best].event_id, prior, claim, conflict_tolerance))
        merged[best] = _merge(merged[best], ev, cross_issuer=cross, keep_terminal=keep)
        prior.append(claim)
    return ReconcileResult(merged, conflicts)


def reconcile(events: list[Event], post_times: Optional[dict[str, datetime]] = None,
              gap: timedelta = DEFAULT_GAP, max_lag: timedelta = DEFAULT_MAX_LAG,
              post_issuers: Optional[dict[str, str]] = None) -> list[Event]:
    """Merged events only; see reconcile_with_conflicts for the conflicts list."""
    return reconcile_with_conflicts(events, post_times, post_issuers, gap, max_lag).events


def reconcile_posts(events: list[Event], posts: list[Post], **kw) -> ReconcileResult:
    """Convenience: times and issuers (Post.page) taken from the captured posts."""
    times = {p.post_id: p.posted_at for p in posts if p.posted_at}
    issuers = {p.post_id: p.page for p in posts}
    return reconcile_with_conflicts(events, times, issuers, **kw)
