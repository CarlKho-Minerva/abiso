"""Which issuer each captured `page` belongs to (evidence per page: collect/SOURCES.md).

Values are the keys of abiso.compile.ISSUER_HINTS, so a caller can do
`ISSUER_HINTS[ISSUER_BY_PAGE[post.page]]` instead of guessing from the page name:
most page ids ("mecomactan.com", "NGCPph", "LLCInformationOffice") contain no
whole-word issuer key.
"""
from __future__ import annotations

ISSUER_BY_PAGE: dict[str, str] = {
    "mecomactan.com": "meco",
    "mecomactan": "meco",                     # Facebook; walled logged out, listed for completeness
    "mcwd.gov.ph": "mcwd",
    "metrocebuwater": "mcwd",
    "NGCPph": "ngcp",
    "depedlapulapu.net.ph": "deped",
    "DepEdTayoLapulapuCity": "deped",
    "DepEDLLC": "deped",
    "pagasa.dost.gov.ph": "pagasa",
    "LLCInformationOffice": "lapu lapu city",
    "lapulapucity.gov.ph": "lapu lapu city",
    "BasakSKCouncil": "barangay",             # the SK (youth council) of Barangay Basak
}


def issuer_of(page: str) -> str | None:
    return ISSUER_BY_PAGE.get(page)
