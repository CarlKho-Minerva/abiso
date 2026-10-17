"""Several issuers posting the same disruption (spec section 2a)."""
import random
from datetime import timedelta

from abiso.reconcile import reconcile, reconcile_posts, reconcile_with_conflicts
from abiso.schema import DisruptionType, Post, Status
from tests.test_reconcile import PT, E, T

ISS = {"p1": "MECO", "p2": "LLCPIO", "p3": "MECO", "p4": "LLCPIO"}


def test_same_brownout_from_two_issuers_merges_without_conflict():
    r = reconcile_with_conflicts([E("p1"), E("p2", areas=("Basak", "Pusok"))], PT, ISS)
    assert len(r.events) == 1 and r.conflicts == []
    assert r.events[0].source_post_ids == ["p1", "p2"] and r.events[0].status == Status.scheduled


def test_different_times_from_two_issuers_is_a_conflict_not_an_extension():
    r = reconcile_with_conflicts([E("p1"), E("p2", start="2026-10-04T09:00", end="2026-10-04T18:00")], PT, ISS)
    assert len(r.events) == 1
    e = r.events[0]
    assert e.status == Status.scheduled                                        # not "extended"
    assert (e.start, e.end) == (T("2026-10-04T08:00"), T("2026-10-04T18:00"))  # union: never hides
    fields = {c.field: c for c in r.conflicts}
    assert set(fields) == {"start", "end"}
    c = fields["start"]
    assert c.event_id == "ev-p1" and c.type == DisruptionType.power
    assert [(x.issuer, x.post_id, x.value) for x in c.claims] == [
        ("MECO", "p1", "2026-10-04T08:00:00+08:00"), ("LLCPIO", "p2", "2026-10-04T09:00:00+08:00")]


def test_conflict_tolerance():
    evs = [E("p1"), E("p2", start="2026-10-04T08:15", end="2026-10-04T17:00")]
    assert len(reconcile_with_conflicts(evs, PT, ISS).conflicts) == 1
    assert reconcile_with_conflicts(evs, PT, ISS, conflict_tolerance=timedelta(minutes=30)).conflicts == []


def test_same_issuer_change_is_an_extension_not_a_conflict():
    r = reconcile_with_conflicts([E("p1"), E("p3", start="2026-10-04T08:00", end="2026-10-04T20:00")], PT, ISS)
    assert r.conflicts == [] and r.events[0].status == Status.extended


def test_other_issuer_still_announcing_after_a_cancellation():
    cancel = E("p2", status="cancelled", start=None, end=None)
    stale = E("p3", status="scheduled")              # MECO again, posted after the city cancelled
    r = reconcile_with_conflicts([E("p1"), cancel, stale], PT, ISS)
    assert len(r.events) == 1 and r.events[0].status == Status.cancelled
    statuses = [c for c in r.conflicts if c.field == "status"]
    assert len(statuses) == 1
    assert [(x.issuer, x.value) for x in statuses[0].claims] == [("LLCPIO", "cancelled"), ("MECO", "scheduled")]


def test_relay_of_original_notice_does_not_undo_a_cancellation():
    times_ = dict(PT, p0=T("2026-10-03T08:00"))
    iss = dict(ISS, p0="MECO")
    evs = [E("p0"), E("p1", status="cancelled", start=None, end=None), E("p2")]
    r = reconcile_with_conflicts(evs, times_, iss)
    assert len(r.events) == 1 and r.events[0].status == Status.cancelled
    assert [c.field for c in r.conflicts] == ["status"]


def test_relaying_a_cancellation_is_not_a_conflict():
    r = reconcile_with_conflicts([E("p1"), E("p2", status="cancelled", start=None, end=None)], PT, ISS)
    assert r.conflicts == [] and r.events[0].status == Status.cancelled


def test_conflict_is_against_each_issuers_latest_claim():
    # MECO corrects itself to 09:00; the city then posts 09:00: no conflict.
    times_ = dict(PT, p5=T("2026-10-04T19:00"))
    iss = dict(ISS, p5="LLCPIO")
    evs = [E("p1"), E("p3", start="2026-10-04T09:00"), E("p5", start="2026-10-04T09:00")]
    r = reconcile_with_conflicts(evs, times_, iss)
    assert len(r.events) == 1 and r.conflicts == []


def test_no_issuers_no_conflicts_and_old_api_unchanged():
    evs = [E("p1"), E("p2", start="2026-10-04T09:00", end="2026-10-04T18:00")]
    assert reconcile_with_conflicts(evs, PT).conflicts == []
    assert reconcile(evs, PT)[0].status == Status.extended


def test_water_and_power_from_different_issuers_stay_apart():
    iss = {"p1": "MECO", "p2": "MCWD", "p3": "LLCPIO"}
    evs = [E("p1"), E("p2", type="water"), E("p3", start="2026-10-04T10:00")]
    r = reconcile_with_conflicts(evs, PT, iss)
    assert sorted(e.type.value for e in r.events) == ["power", "water"]
    assert [(c.field, c.claims[0].issuer, c.claims[1].issuer) for c in r.conflicts] == [("start", "MECO", "LLCPIO")]


def test_reconcile_posts_reads_issuer_from_page():
    def P(pid, page, t):
        return Post(post_id=pid, page=page, url="u", posted_at=T(t), captured_at=T(t), capture_method="dom")
    posts = [P("p1", "MECO", "2026-10-03T10:00"), P("p2", "LLCPIO", "2026-10-04T15:00")]
    r = reconcile_posts([E("p2", start="2026-10-04T09:00"), E("p1")], posts)
    assert len(r.events) == 1 and r.events[0].event_id == "ev-p1"
    assert [c.field for c in r.conflicts] == ["start"]


def test_conflicts_deterministic_under_shuffle():
    evs = [E("p1"), E("p2", start="2026-10-04T09:00", end="2026-10-04T18:00"), E("p3", areas=("Pusok",)),
           E("p4", status="cancelled", start=None, end=None)]
    want = reconcile_with_conflicts(evs, PT, ISS)
    for seed in range(5):
        s = evs[:]
        random.Random(seed).shuffle(s)
        got = reconcile_with_conflicts(s, PT, ISS)
        assert got.events == want.events and got.conflicts == want.conflicts
