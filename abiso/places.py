"""Lapu-Lapu City barangays: canonical names and the spellings people actually use.

Source: Philippine Standard Geographic Code (PSGC), Lapu-Lapu City code 0731100000,
30 barangays. https://psa.gov.ph/classification/psgc/barangays/0731100000
Cross-checked 2026-10-03 against https://en.wikipedia.org/wiki/Lapu-Lapu_City#Barangays
(same 30 names, same spelling).

Canonical names follow PSGC spelling. ALIASES maps variants seen in official posts
(e.g. the city website writes "Caohagan" for PSGC "Cawhagan") to the canonical name.
"""
from __future__ import annotations

import unicodedata

CITY = "Lapu-Lapu City"
PSGC_CITY_CODE = "0731100000"

BARANGAYS: tuple[str, ...] = (
    "Agus",
    "Babag",
    "Bankal",
    "Baring",
    "Basak",
    "Buaya",
    "Calawisan",
    "Canjulao",
    "Caubian",
    "Caw-oy",
    "Cawhagan",
    "Gun-ob",
    "Ibo",
    "Looc",
    "Mactan",
    "Maribago",
    "Marigondon",
    "Pajac",
    "Pajo",
    "Pangan-an",
    "Poblacion",
    "Punta Engaño",
    "Pusok",
    "Sabang",
    "San Vicente",
    "Santa Rosa",
    "Subabasbas",
    "Talima",
    "Tingo",
    "Tungasan",
)

# Variant spelling (normalised key) -> canonical name. Keys are matched after _key().
# Hyphen, accent, case and "Brgy." differences are already absorbed by _key(), so only
# genuinely different spellings belong here.
ALIASES: dict[str, str] = {
    "caohagan": "Cawhagan",   # city website, articles/13 (2023)
    "sta rosa": "Santa Rosa",
}
# Deliberately absent: "Olango". It is an island holding several barangays, not one
# barangay, so it must be handled as an area (Area.detail), never mapped to a name here.


def barangays() -> list[str]:
    """The 30 canonical names as a fresh list (abiso.places_match accepts this or BARANGAYS)."""
    return list(BARANGAYS)


def _key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", "").replace(".", " ")
    words = [w for w in s.split() if w not in ("barangay", "brgy")]
    return " ".join(words)


_CANON = {_key(b): b for b in BARANGAYS}


def canonical(name: str) -> str | None:
    """Return the canonical barangay name for a spelling, or None if it is not one."""
    k = _key(name)
    return _CANON.get(k) or ALIASES.get(k)
