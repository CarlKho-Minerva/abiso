"""compile.py uses the issuing page as context (spec section 2a)."""
import pytest

from abiso import compile as C
from tests.fixtures.barangays import LAPULAPU_BARANGAYS as B
from tests.test_compile import post


@pytest.mark.parametrize("page, expect", [
    ("MECO", "Mactan Electric"),
    ("Mactan Electric Company, Inc.", "Mactan Electric"),
    ("MCWD", "Metropolitan Cebu Water"),
    ("DepEd Lapu-Lapu City Division", "DepEd"),          # not the city government
    ("Barangay Basak, Lapu-Lapu City", "barangay's own page"),
    ("DOST-PAGASA", "PAGASA"),
    ("LLCPIO", "Public Information Office"),
    ("Lapu-Lapu City Government", "Lapu-Lapu City government"),
    ("Homecoming Committee", "Unknown issuer"),          # whole words only
    ("", "Unknown issuer"),
])
def test_issuer_hint(page, expect):
    assert expect in C.issuer_hint(page)


def test_issuer_line_reaches_the_prompt():
    p = post()
    p = p.model_copy(update={"page": "MECO"})
    text = C.build_user_text(p, B)
    assert text.splitlines()[0] == "Page: MECO"
    assert text.splitlines()[1].startswith("Issuer: Mactan Electric Company")


def test_system_prompt_names_several_issuers():
    for word in ("power or water utility", "DepEd", "PAGASA", "barangay", "the content decides"):
        assert word in C.SYSTEM_PROMPT
