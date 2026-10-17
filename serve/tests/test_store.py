"""The three tools, against the SYNTHETIC fixture. Each test is one prediction a
resident would care about: right place, right day, cancellations visible, and an
explicit "not found" instead of a guess."""
from datetime import date

import pytest

from serve import store


def ids(result):
    return [e["event_id"] for e in result["events"]]


def test_brownout_by_barangay_and_date_has_source_link():
    r = store.brownouts("Basak", "2026-10-04")
    assert r["found"] is True
    assert ids(r) == ["synthetic-ev-power-basak-1004"]
    assert r["events"][0]["source_links"] == ["https://example.invalid/SYNTHETIC/llcpio/posts/1001"]


@pytest.mark.parametrize("area", ["basak", "Brgy. Basak", "BASAK", "Barangay Basak"])
def test_area_spelling_does_not_matter(area):
    assert store.brownouts(area, "2026-10-04")["found"]


def test_accents_and_hyphens_do_not_matter():
    assert ids(store.disruptions("Punta Engano", "2026-10-04", "road")) == ["synthetic-ev-road-puntaengano-1004"]


def test_detail_text_matches_feeder():
    assert ids(store.brownouts("Feeder 12")) == ["synthetic-ev-power-basak-1004"]


def test_relative_dates_in_three_languages():
    tomorrow = date(2026, 10, 4)
    for word in ("tomorrow", "bukas", "Ugma"):
        assert store.parse_day(word) == tomorrow
    assert store.brownouts("Basak", "ugma")["found"]


def test_wrong_day_is_not_found_and_says_so():
    r = store.brownouts("Basak", "2026-10-05")
    assert r["found"] is False and r["events"] == []
    assert "No official notice found" in r["note"]


def test_unknown_barangay_is_not_found():
    assert store.brownouts("Mactan Newtown Tower 9", "2026-10-04")["found"] is False


def test_cancelled_event_is_returned_and_flagged():
    r = store.brownouts("Maribago", "2026-10-07")
    assert r["found"] and r["events"][0]["cancelled"] is True
    assert len(r["events"][0]["source_links"]) == 2       # original notice + cancellation


def test_city_wide_class_suspension_matches_any_barangay():
    assert ids(store.disruptions("Pajo", "2026-10-06", "class_suspension")) == ["synthetic-ev-classes-city-1006"]


def test_classes_suspended_lapu_lapu_and_spellings():
    for city in ("Lapu-Lapu City", "lapulapu", "Opon"):
        assert store.classes_suspended(city, "2026-10-06")["found"]
    assert store.classes_suspended("Lapu-Lapu", "2026-10-05")["found"] is False


def test_other_city_is_refused_not_answered():
    r = store.classes_suspended("Cebu City", "2026-10-06")
    assert r["found"] is False and "only covers Lapu-Lapu" in r["note"]


def test_overnight_water_interruption_counts_on_both_days():
    assert store.disruptions("Pajo", "2026-10-05", "water")["found"]
    assert store.disruptions("Pajo", "2026-10-06", "water")["found"]


def test_not_disruption_posts_never_returned():
    assert "synthetic-ev-notdisruption-1007" not in ids(store.disruptions())


def test_undated_event_is_listed_without_a_date_filter_but_not_with_one():
    assert "synthetic-ev-power-agus-tbd" in ids(store.brownouts("Agus"))
    assert store.brownouts("Agus", "2026-10-04")["found"] is False


def test_each_type_tool_returns_only_its_type():
    assert ids(store.water_interruptions("Pajo", "2026-10-05")) == ["synthetic-ev-water-pajo-1005"]
    assert ids(store.road_closures("Punta Engaño", "2026-10-04")) == ["synthetic-ev-road-puntaengano-1004"]
    assert store.weather_advisories(None, "2026-10-04")["found"] is False
    assert store.work_suspensions(None, "2026-10-06")["found"] is False   # classes, not work
    assert store.water_interruptions("Basak", "2026-10-04")["found"] is False


def test_brownout_word_maps_to_power():
    assert store.disruptions("Basak", "2026-10-04", "brownout")["found"]


def test_bad_inputs_raise_with_a_clear_message():
    with pytest.raises(ValueError, match="Could not read the date"):
        store.brownouts("Basak", "next fiesta")
    with pytest.raises(ValueError, match="Unknown disruption type"):
        store.disruptions(type="aliens")


def test_malformed_events_file_raises_with_line_number(tmp_path, monkeypatch):
    bad = tmp_path / "events.jsonl"
    bad.write_text('{"event_id": "x"}\n')
    monkeypatch.setenv("ABISO_EVENTS", str(bad))
    with pytest.raises(ValueError, match=r"events.jsonl:1"):
        store.disruptions()


def test_missing_events_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ABISO_EVENTS", str(tmp_path / "nope.jsonl"))
    with pytest.raises(FileNotFoundError):
        store.disruptions()
