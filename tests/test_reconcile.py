import random
from datetime import datetime, timedelta

from abiso.reconcile import reconcile, times_compatible, window
from abiso.schema import Area, DisruptionType, Event, Status

T = lambda s: datetime.fromisoformat(s + "+08:00")  # noqa: E731


def E(pid, type="power", status="scheduled", areas=("Basak",), start="2026-10-04T08:00",
      end="2026-10-04T17:00", all_day=False, eid=None, detail_only=None):
    if detail_only:
        a = [Area(barangay=None, detail=detail_only)]
    else:
        a = [Area(barangay=b) for b in areas]
    return Event(event_id=eid or f"ev-{pid}", type=DisruptionType(type), status=Status(status), areas=a,
                 start=T(start) if start else None, end=T(end) if end else None, all_day=all_day,
                 summary=f"from {pid}", source_post_ids=[pid], confidence=0.9, model="m")


def times(*pairs):
    return {pid: T(t) for pid, t in pairs}


PT = times(("p1", "2026-10-03T10:00"), ("p2", "2026-10-04T15:00"), ("p3", "2026-10-04T18:00"),
           ("p4", "2026-10-14T18:00"))


def test_extension_with_only_an_end_time():
    out = reconcile([E("p1"), E("p2", status="extended", start=None, end="2026-10-04T20:00")], PT)
    assert len(out) == 1
    e = out[0]
    assert e.status == Status.extended and e.end == T("2026-10-04T20:00") and e.start == T("2026-10-04T08:00")
    assert e.source_post_ids == ["p1", "p2"] and e.event_id == "ev-p1" and e.summary == "from p2"


def test_cancellation_keeps_original_times():
    out = reconcile([E("p1"), E("p2", status="cancelled", start=None, end=None)], PT)
    assert len(out) == 1 and out[0].status == Status.cancelled
    assert (out[0].start, out[0].end) == (T("2026-10-04T08:00"), T("2026-10-04T17:00"))


def test_resolved_early_takes_restoration_time():
    out = reconcile([E("p1"), E("p2", status="resolved", start=None, end="2026-10-04T14:30")], PT)
    assert out[0].status == Status.resolved and out[0].end == T("2026-10-04T14:30")


def test_later_window_in_new_post_is_an_extension():
    out = reconcile([E("p1"), E("p2", start="2026-10-04T16:00", end="2026-10-04T21:00")], PT)
    assert len(out) == 1 and out[0].status == Status.extended and out[0].end == T("2026-10-04T21:00")


def test_adjacent_within_an_hour_merges():
    out = reconcile([E("p1", end="2026-10-04T12:00"), E("p2", start="2026-10-04T12:45", end="2026-10-04T17:00")], PT)
    assert len(out) == 1 and out[0].end == T("2026-10-04T17:00")


def test_gap_over_an_hour_does_not_merge():
    out = reconcile([E("p1", end="2026-10-04T12:00"), E("p2", start="2026-10-04T13:30", end="2026-10-04T17:00")], PT)
    assert len(out) == 2


def test_next_day_same_place_is_a_new_event():
    out = reconcile([E("p1"), E("p2", start="2026-10-05T08:00", end="2026-10-05T17:00")], PT)
    assert len(out) == 2


def test_different_barangay_or_type_does_not_merge():
    assert len(reconcile([E("p1"), E("p2", areas=("Pusok",))], PT)) == 2
    assert len(reconcile([E("p1"), E("p2", type="water")], PT)) == 2


def test_city_wide_overlaps_any_barangay_and_stays_city_wide():
    out = reconcile([E("p1", areas=()), E("p2", status="cancelled", areas=("Basak",), start=None, end=None)], PT)
    assert len(out) == 1 and out[0].areas == [] and out[0].status == Status.cancelled


def test_unresolved_places_match_on_text():
    out = reconcile([E("p1", detail_only="Feeder 12"),
                     E("p2", detail_only="feeder 12", status="resolved", start=None, end=None)], PT)
    assert len(out) == 1 and out[0].status == Status.resolved
    assert len(reconcile([E("p1", detail_only="Feeder 12"), E("p2", detail_only="Feeder 7")], PT)) == 2


def test_area_union_on_new_announcement():
    out = reconcile([E("p1", areas=("Basak",)), E("p2", areas=("Basak", "Pusok"))], PT)
    assert [a.barangay for a in out[0].areas] == ["Basak", "Pusok"]


def test_class_suspension_extended_to_next_day():
    mon = E("p1", type="class_suspension", areas=(), start="2026-10-05T00:00", end=None, all_day=True)
    tue = E("p2", type="class_suspension", areas=(), start="2026-10-06T00:00", end=None, all_day=True)
    out = reconcile([mon, tue], PT)
    assert len(out) == 1 and out[0].status == Status.extended
    assert out[0].start == T("2026-10-05T00:00") and out[0].end == T("2026-10-07T00:00") and out[0].all_day


def test_start_only_notices_never_get_an_invented_end():
    # vet finding 10-03: two start-only notices merged into "extended, end 09:00".
    out = reconcile([E("p1", start="2026-10-04T08:00", end=None),
                     E("p2", start="2026-10-04T09:00", end=None)], PT)
    assert len(out) == 1 and out[0].end is None and out[0].status == Status.scheduled
    assert out[0].start == T("2026-10-04T08:00")


def test_end_filled_in_later_is_not_an_extension():
    out = reconcile([E("p1", start="2026-10-04T08:00", end=None),
                     E("p2", start="2026-10-04T08:00", end="2026-10-04T17:00")], PT)
    assert out[0].end == T("2026-10-04T17:00") and out[0].status == Status.scheduled


def test_rescheduled_announcement_does_not_downgrade_ongoing():
    out = reconcile([E("p1", status="ongoing"), E("p2", status="scheduled")], PT)
    assert out[0].status == Status.ongoing


def test_not_disruption_passes_through():
    nd = E("p2", type="not_disruption", areas=(), start=None, end=None)
    out = reconcile([E("p1"), nd, E("p3", type="not_disruption", areas=(), start=None, end=None)], PT)
    assert len(out) == 3


def test_events_from_the_same_post_never_merge():
    a = E("p1", eid="a", end="2026-10-04T12:00")
    b = E("p1", eid="b", start="2026-10-04T12:00", end="2026-10-04T17:00")
    assert len(reconcile([a, b], PT)) == 2


def test_untimed_update_does_not_reach_back_past_max_lag():
    out = reconcile([E("p1"), E("p4", status="resolved", start=None, end=None)], PT)
    assert len(out) == 2


def test_untimed_new_announcement_does_not_merge_into_timed():
    assert len(reconcile([E("p1"), E("p2", start=None, end=None)], PT)) == 2


def test_order_comes_from_post_times_not_input_order():
    cancel = E("p2", status="cancelled", start=None, end=None)
    out = reconcile([cancel, E("p1")], PT)
    assert len(out) == 1 and out[0].status == Status.cancelled and out[0].event_id == "ev-p1"


def test_chain_extend_then_resolve():
    out = reconcile([E("p1"), E("p2", status="extended", start=None, end="2026-10-04T20:00"),
                     E("p3", status="resolved", start=None, end="2026-10-04T19:10")], PT)
    assert len(out) == 1 and out[0].status == Status.resolved
    assert out[0].end == T("2026-10-04T19:10") and out[0].source_post_ids == ["p1", "p2", "p3"]


def test_deterministic_under_shuffle_and_pure():
    evs = [E("p1"), E("p2", status="extended", start=None, end="2026-10-04T20:00"),
           E("p3", areas=("Pusok",)), E("p4", type="water")]
    snapshot = [e.model_copy(deep=True) for e in evs]
    want = reconcile(evs, PT)
    for seed in range(5):
        shuffled = evs[:]
        random.Random(seed).shuffle(shuffled)
        assert reconcile(shuffled, PT) == want
    assert evs == snapshot          # inputs untouched


def test_idempotent():
    evs = [E("p1"), E("p2", status="extended", start=None, end="2026-10-04T20:00"), E("p3", areas=("Pusok",))]
    once = reconcile(evs, PT)
    assert reconcile(once, PT) == once


def test_without_post_times_input_order_is_used():
    out = reconcile([E("p1"), E("p2", status="cancelled", start=None, end=None)])
    assert len(out) == 1 and out[0].status == Status.cancelled


def test_window_rules():
    assert window(E("x", start=None, end=None)) is None
    w = window(E("x", start="2026-10-05T00:00", end=None, all_day=True))
    assert w.end - w.start == timedelta(days=1)
    assert times_compatible(E("a"), E("b", status="extended", start=None, end="2026-10-04T23:00"), timedelta(hours=1))
    assert not times_compatible(E("a"), E("b", status="extended", start=None, end="2026-10-04T06:00"), timedelta(hours=1))
