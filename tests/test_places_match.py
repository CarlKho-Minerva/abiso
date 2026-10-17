import pytest

from abiso.places_match import PlaceMatcher, match_barangays, normalise
from abiso.schema import Area
from tests.fixtures.barangays import LAPULAPU_BARANGAYS


@pytest.fixture(scope="module")
def m():
    return PlaceMatcher(LAPULAPU_BARANGAYS)


@pytest.mark.parametrize("text, expected", [
    ("Pusok", ["Pusok"]),
    ("portions of Brgy. Basak", ["Basak"]),
    ("PORTIONS OF BARANGAY BASAK", ["Basak"]),
    ("Mactan", ["Mactan"]),
    ("Brgy. Pajo, Gun-ob ug Basak", ["Pajo", "Gun-ob", "Basak"]),
    ("Sta. Rosa, Olango", ["Santa Rosa"]),
    ("Punta Engano", ["Punta Engaño"]),
    ("Punta Engaño", ["Punta Engaño"]),
    ("Cawoy", ["Caw-oy"]),
    ("Brgy. Pangan-an", ["Pangan-an"]),
    ("Sitio Lawis, Pajo", ["Pajo"]),
    ("San Vicente", ["San Vicente"]),
    ("Basak and Brgy. Basak (Feeder 12)", ["Basak"]),
])
def test_exact_and_alias(m, text, expected):
    assert m.barangays_in(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Marigondo", ["Marigondon"]),
    ("Maribagu", ["Maribago"]),
    ("Subabasbs", ["Subabasbas"]),
])
def test_fuzzy_typos(m, text, expected):
    assert m.barangays_in(text) == expected


@pytest.mark.parametrize("text", [
    "", None, "Mactan Island", "Mactan-Cebu International Airport", "Cebu City Hall",
    "ug sa ang mga", "Feeder 12", "tibuok siyudad",
])
def test_misses_are_misses(m, text):
    assert m.barangays_in(text) == []


def test_short_words_never_fuzz(m):
    # "Iba" is one edit from "Ibo" but too short to fuzz; exact "Ibo" still matches.
    assert m.barangays_in("iba") == []
    assert m.barangays_in("Ibo") == ["Ibo"]


def test_fuzzy_score_reported(m):
    hit = m.find("Marigondo")[0]
    assert hit.barangay == "Marigondon" and 0.85 <= hit.score < 1.0
    assert m.find("Pusok")[0].score == 1.0


def test_to_areas_keeps_unresolved_text(m):
    assert m.to_areas("Feeder 12") == [Area(barangay=None, detail="Feeder 12")]
    assert m.to_areas(None, None) == []
    areas = m.to_areas("portions of Pajo and Basak", "Feeder 7")
    assert [a.barangay for a in areas] == ["Pajo", "Basak"]
    assert all(a.detail == "portions of Pajo and Basak; Feeder 7" for a in areas)


def test_to_areas_falls_back_to_detail(m):
    assert [a.barangay for a in m.to_areas("Sitio Ibabao", "Brgy. Marigondon")] == ["Marigondon"]


def test_normalise():
    assert normalise("  Punta  Engaño! ") == "punta engano"
    assert normalise("Caw-oy") == "caw oy"


def test_matcher_rejects_empty_list():
    with pytest.raises(ValueError):
        PlaceMatcher([])


def test_match_barangays_with_explicit_list():
    assert match_barangays("Pusok", LAPULAPU_BARANGAYS) == ["Pusok"]


def test_alias_only_used_if_target_in_list():
    m2 = PlaceMatcher(["Basak"])
    assert m2.barangays_in("Sta. Rosa") == []
