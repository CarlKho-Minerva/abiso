"""Free text -> canonical Lapu-Lapu barangay names (subproblem 4).

Input is whatever a notice or the compiler wrote for a place: "portions of Brgy.
Basak", "Pusok", "Sta. Rosa, Olango", "Marigondo (typo)". Output is the list of
canonical barangay names it mentions, in order of first appearance, plus what
was left over as detail.

Deterministic and dependency-free (difflib). The canonical list comes from
`abiso.places` (owned by the collect agent); every function also takes an
explicit `barangays` argument so tests and other cities can pass their own.

Matching order, cheapest and safest first:
  1. exact match of the normalised name or a known alias (multi-word first)
  2. fuzzy match of a 1-3 word window, only for windows of >= 5 letters, so
     short Bisaya words never fuzz into a short barangay name.
A miss is returned as a miss (empty list); this module never guesses.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from abiso.schema import Area

# Spellings seen on official pages that differ from the canonical name.
# Keys and values are compared after normalise(); values must be canonical names.
ALIASES: dict[str, str] = {
    "sta rosa": "Santa Rosa",
    "sta. rosa": "Santa Rosa",
    "pob": "Poblacion",
    "punta engano": "Punta Engaño",
    "pta engano": "Punta Engaño",
    "cawoy": "Caw-oy",
    "gunob": "Gun-ob",
    "panganan": "Pangan-an",
    "san vicente olango": "San Vicente",
    "caohagan": "Cawhagan",          # the city website's spelling (abiso/places.py ALIASES)
}

# Words that introduce a place but are not part of its name.
_PREFIXES = re.compile(
    r"\b(brgy|bgy|bgry|barangay|barrio|portions? of|parts? of|some parts of|"
    r"selected areas? (?:of|in)|areas? of|sitio|purok|zone)\b\.?",
    re.IGNORECASE,
)

# "Mactan" alone is Barangay Mactan; "Mactan Island" / "Mactan-Cebu" is the island
# or the airport, i.e. not a barangay claim.
_NOT_BARANGAY = re.compile(r"\bmactan[\s-]+(island|cebu|newtown|airport|economic)\b", re.IGNORECASE)

FUZZY_CUTOFF = 0.85
_FUZZY_MIN_LETTERS = 5


def normalise(text: str) -> str:
    """Lowercase, strip accents (ñ -> n), turn punctuation and hyphens into spaces."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load_barangays() -> list[str]:
    """Canonical list from abiso.places. Fails loudly if it is not there yet."""
    try:
        from abiso import places  # type: ignore[attr-defined]
    except ImportError as e:  # pragma: no cover - depends on the other agent's module
        raise ImportError(
            "abiso.places is missing: pass `barangays=` explicitly until it lands"
        ) from e
    if hasattr(places, "BARANGAYS"):
        names = list(places.BARANGAYS)
    elif hasattr(places, "barangays"):
        names = list(places.barangays())
    else:  # pragma: no cover
        raise ImportError("abiso.places defines neither BARANGAYS nor barangays()")
    names = [n if isinstance(n, str) else getattr(n, "name") for n in names]
    if not names:  # pragma: no cover
        raise ValueError("abiso.places returned an empty barangay list")
    return names


@dataclass(frozen=True)
class Match:
    barangay: str        # canonical name
    matched: str         # the normalised window that matched
    score: float         # 1.0 exact or alias, else difflib ratio
    start: int           # token index of the window, for ordering


class PlaceMatcher:
    """Build once per barangay list; `find` is pure."""

    def __init__(self, barangays: Sequence[str], cutoff: float = FUZZY_CUTOFF):
        if not barangays:
            raise ValueError("barangays must be non-empty")
        self.cutoff = cutoff
        self.canonical = list(dict.fromkeys(barangays))
        self.lookup: dict[str, str] = {normalise(b): b for b in self.canonical}
        canon_set = set(self.canonical)
        for alias, target in ALIASES.items():
            if target in canon_set:
                self.lookup.setdefault(normalise(alias), target)
        self.max_words = max(len(k.split()) for k in self.lookup)

    def find(self, text: Optional[str]) -> list[Match]:
        if not text:
            return []
        cleaned = _NOT_BARANGAY.sub(" ", text)
        cleaned = _PREFIXES.sub(" ", cleaned)
        tokens = normalise(cleaned).split()
        used = [False] * len(tokens)
        found: list[Match] = []

        # Pass 1: exact / alias, longest window first so "san vicente" beats "san".
        for n in range(min(self.max_words, len(tokens)), 0, -1):
            for i in range(len(tokens) - n + 1):
                if any(used[i:i + n]):
                    continue
                window = " ".join(tokens[i:i + n])
                if window in self.lookup:
                    found.append(Match(self.lookup[window], window, 1.0, i))
                    for k in range(i, i + n):
                        used[k] = True

        # Pass 2: fuzzy on what is left.
        keys = list(self.lookup)
        for n in range(min(3, len(tokens)), 0, -1):
            for i in range(len(tokens) - n + 1):
                if any(used[i:i + n]):
                    continue
                window = " ".join(tokens[i:i + n])
                if len(window.replace(" ", "")) < _FUZZY_MIN_LETTERS:
                    continue
                best = difflib.get_close_matches(window, keys, n=1, cutoff=self.cutoff)
                if best:
                    score = difflib.SequenceMatcher(None, window, best[0]).ratio()
                    found.append(Match(self.lookup[best[0]], window, score, i))
                    for k in range(i, i + n):
                        used[k] = True

        found.sort(key=lambda m: m.start)
        out: list[Match] = []
        seen: set[str] = set()
        for m in found:
            if m.barangay not in seen:
                seen.add(m.barangay)
                out.append(m)
        return out

    def barangays_in(self, text: Optional[str]) -> list[str]:
        return [m.barangay for m in self.find(text)]

    def to_areas(self, place: Optional[str], detail: Optional[str] = None) -> list[Area]:
        """One Area per barangay found. A place that matches nothing is kept as
        an unresolved Area (barangay None, the text in detail), never dropped."""
        names = self.barangays_in(place)
        if not names and detail:
            names = self.barangays_in(detail)
        written = "; ".join(x for x in (place, detail) if x) or None
        if not names:
            return [Area(barangay=None, detail=written)] if written else []
        return [Area(barangay=n, detail=written) for n in names]


def match_barangays(text: Optional[str], barangays: Optional[Iterable[str]] = None) -> list[str]:
    """Convenience: canonical names mentioned in `text`."""
    return PlaceMatcher(list(barangays) if barangays is not None else load_barangays()).barangays_in(text)
