"""Regression tests for the three issues the collector found on real pages (2026-10-03)."""
import json
from pathlib import Path

from abiso.compile import ISSUER_HINTS, issuer_hint
from abiso.issuers import ISSUER_BY_PAGE
from abiso.places_match import match_barangays


def test_every_captured_page_maps_to_a_known_issuer():
    pages = {json.loads(l)["page"] for l in Path("data/raw/posts.jsonl").read_text().splitlines() if l.strip()}
    unknown = sorted(p for p in pages if issuer_hint(p).startswith("Unknown issuer"))
    assert unknown == [], f"pages with no issuer hint: {unknown}"


def test_issuer_map_values_are_hint_keys():
    assert set(ISSUER_BY_PAGE.values()) <= set(ISSUER_HINTS)


def test_meco_site_gets_meco_hint():
    assert issuer_hint("mecomactan.com") == ISSUER_HINTS["meco"]


def test_caohagan_resolves_to_cawhagan():
    assert match_barangays("Brgy. Caohagan") == ["Cawhagan"]


def test_mactan_is_not_city_wide():
    from serve import store
    assert "mactan" not in store.CITY_NAMES
