"""cBioPortal's missing values in a clinical file's cells (M4.2a-1b, D437): the classifier is Java's
``has`` after its trim, with no ASCII carve-out, and ``FIXED`` is a literal list.

The oracle ``java_has`` is written here again from the plan's statement of Java's test (its trim,
then ``[``, ``]`` and ``|`` removed, each space turned into ``_``, upper-cased, one of the eight
names), with Python's ``str.upper()``. The domain is the cells that can reach the classifier: no
tab, line end or control character. The class tests:

- **(a)** each name in five cases, ``_`` or a space between its words, brackets five ways, ``|``
  at every position, padding of up to two characters on each side from five spaces;
- **(b)** every cell of up to 5 characters over ``N A a n [ ] | _``, a space, U+00A0, ``\u00df``,
  ``\u0131`` and ``\u017f``;
- **(c)** each of the ten code points that upper-case to ASCII letters substituted at every
  position of each name's three spellings, bare and in brackets;
- **(d)** the set pin: over U+0080 to U+10FFFF, the code points whose ``str.upper()`` is ASCII
  letters alone are exactly those ten (recorded from OpenJDK 21 in the plan's round 2, so that a
  Python Unicode change that alters the set fails here);
- **(e)** Hypothesis over the domain, 2,000 examples on every run.
"""

import importlib
import itertools
from types import ModuleType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

NAMES = ("NOT_APPLICABLE", "NOT_AVAILABLE", "PENDING", "DISCREPANCY", "COMPLETED", "NULL")
NAMES += ("MISSING", "NA")
TEN = "\u00df\u0131\u017f\ufb00\ufb01\ufb02\ufb03\ufb04\ufb05\ufb06"
"""The ten code points past ASCII whose upper case is ASCII letters: U+00DF, U+0131, U+017F and
U+FB00 to U+FB06."""
CONTROLS = {chr(c) for c in (*range(0x20), 0x7F)}

FIXED = {
    " ": "UNKNOWN",
    "COMPLETED": "UNKNOWN",
    "Completed": "UNKNOWN",
    "DISCREPANCY": "UNKNOWN",
    "Discrepancy": "UNKNOWN",
    "MISSING": "UNKNOWN",
    "Missing": "UNKNOWN",
    "NA": "UNKNOWN",
    "NOT APPLICABLE": "NOT_APPLICABLE",
    "NOT AVAILABLE": "UNKNOWN",
    "NULL": "UNKNOWN",
    "Na": "UNKNOWN",
    "Not Applicable": "NOT_APPLICABLE",
    "Not Available": "UNKNOWN",
    "Not applicable": "NOT_APPLICABLE",
    "Not available": "UNKNOWN",
    "Null": "UNKNOWN",
    "PENDING": "UNKNOWN",
    "Pending": "UNKNOWN",
    "[COMPLETED]": "UNKNOWN",
    "[Completed]": "UNKNOWN",
    "[DISCREPANCY]": "UNKNOWN",
    "[Discrepancy]": "UNKNOWN",
    "[MISSING]": "UNKNOWN",
    "[Missing]": "UNKNOWN",
    "[NA]": "UNKNOWN",
    "[NOT APPLICABLE]": "NOT_APPLICABLE",
    "[NOT AVAILABLE]": "UNKNOWN",
    "[NULL]": "UNKNOWN",
    "[Na]": "UNKNOWN",
    "[Not Applicable]": "NOT_APPLICABLE",
    "[Not Available]": "UNKNOWN",
    "[Not applicable]": "NOT_APPLICABLE",
    "[Not available]": "UNKNOWN",
    "[Null]": "UNKNOWN",
    "[PENDING]": "UNKNOWN",
    "[Pending]": "UNKNOWN",
    "[completed]": "UNKNOWN",
    "[discrepancy]": "UNKNOWN",
    "[missing]": "UNKNOWN",
    "[na]": "UNKNOWN",
    "[not applicable]": "NOT_APPLICABLE",
    "[not available]": "UNKNOWN",
    "[null]": "UNKNOWN",
    "[pending]": "UNKNOWN",
    "completed": "UNKNOWN",
    "discrepancy": "UNKNOWN",
    "missing": "UNKNOWN",
    "na": "UNKNOWN",
    "not applicable": "NOT_APPLICABLE",
    "not available": "UNKNOWN",
    "null": "UNKNOWN",
    "pending": "UNKNOWN",
}
"""The 53 spellings, written out: the golden list ``missing.FIXED`` is held to."""


def java_has(cell: str) -> str | None:
    """Java's test after its trim, written here again: ``None`` for a value."""
    trimmed = cell.strip(" ")
    if trimmed == "":
        return "UNKNOWN" if cell else None
    folded = trimmed.replace("[", "").replace("]", "").replace("|", "").replace(" ", "_").upper()
    if folded not in NAMES:
        return None
    return "NOT_APPLICABLE" if folded == "NOT_APPLICABLE" else "UNKNOWN"


@pytest.fixture
def missing() -> ModuleType:
    return importlib.import_module("aibi.packs.onco.missing")


def test_the_fixed_list_is_the_golden_list(missing: ModuleType) -> None:
    assert dict(missing.FIXED) == FIXED
    assert list(missing.FIXED) == sorted(FIXED)
    assert len(FIXED) == 53
    assert all(java_has(spelling) == state for spelling, state in FIXED.items())


def test_the_names(missing: ModuleType) -> None:
    assert frozenset(NAMES) == missing.NAMES


@pytest.mark.parametrize(
    ("cell", "state"),
    [
        ("[Missing]", "UNKNOWN"),
        ("N|A", "UNKNOWN"),
        ("Mi\u00dfing", "UNKNOWN"),
        ("MI\u00dfING", "UNKNOWN"),
        ("Pend\u0131ng", "UNKNOWN"),
        ("Di\u017fcrepancy", "UNKNOWN"),
        ("[Not Applicable]", "NOT_APPLICABLE"),
        ("not_applicable", "NOT_APPLICABLE"),
        (" NA", "UNKNOWN"),
        ("  ", "UNKNOWN"),
        (" ", "UNKNOWN"),
        ("", None),
        ("\xa0NA", None),
        ("NA\u3000", None),
        ("[]", None),
        ("|", None),
        ("N/A", None),
        ("[Unknown]", None),
        ("[Not Evaluated]", None),
        ("N A", None),
        ("Pendi\u0307ng", None),
    ],
)
def test_pinned_cells(missing: ModuleType, cell: str, state: str | None) -> None:
    """``Pendi\u0307ng`` (``i`` and a combining dot above) is a value here and in Java under every
    locale but ``lt``, which drops the dot (D409 lists ``tr``, ``az`` and ``lt``)."""
    assert missing.classify(cell) == state
    assert java_has(cell) == state


def _cased(word: str) -> list[str]:
    alternating = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(word))
    return [word.lower(), word.upper(), word.title(), word.capitalize(), alternating]


def test_a_the_names_enumerated(missing: ModuleType) -> None:
    pads = ["", " ", "  ", "\xa0", "\u0085", "\u2028", "\u3000", " \xa0"]
    count = 0
    for name in NAMES:
        for joiner in ("_", " "):
            for word in _cased(name.replace("_", joiner)):
                bracketed = [word, f"[{word}]", f"[[{word}]]", f"[{word}", f"{word}]"]
                piped = [word[:i] + "|" + word[i:] for i in range(len(word) + 1)]
                for cell in [*bracketed, *piped]:
                    for left, right in itertools.product(pads, repeat=2):
                        padded = left + cell + right
                        assert missing.classify(padded) == java_has(padded), padded
                        count += 1
    assert count == 73_600


def test_b_the_brute_force(missing: ModuleType) -> None:
    alphabet = ["N", "A", "a", "n", "[", "]", "|", "_", " ", "\xa0", "\u00df", "\u0131", "\u017f"]
    for size in range(6):
        for letters in itertools.product(alphabet, repeat=size):
            cell = "".join(letters)
            assert missing.classify(cell) == java_has(cell), cell


def test_c_the_ten_code_points(missing: ModuleType) -> None:
    for name in NAMES:
        word = name.replace("_", " ")
        for spelling in (word.lower(), word.title(), word.upper()):
            for i in range(len(spelling)):
                for special in TEN:
                    cell = spelling[:i] + special + spelling[i + 1 :]
                    for written in (cell, f"[{cell}]"):
                        assert missing.classify(written) == java_has(written), written


def test_d_the_ten_code_points_are_pinned() -> None:
    found = {
        chr(code)
        for code in range(0x80, 0x110000)
        if (upper := chr(code).upper()).isascii() and upper.isalpha()
    }
    assert found == set(TEN)


CELLS = st.text(
    alphabet=st.characters(blacklist_characters="".join(CONTROLS), blacklist_categories=("Cs",))
    | st.sampled_from(list("NAnaPpDdCcMmLlUuTtIiBbEeGgRrSsOoVvY[]|_ " + TEN + "\xa0\u3000")),
    max_size=24,
)


@settings(max_examples=2_000, deadline=None)
@given(cell=CELLS)
def test_e_hypothesis(cell: str) -> None:
    missing = importlib.import_module("aibi.packs.onco.missing")
    assert missing.classify(cell) == java_has(cell)


def test_additions(missing: ModuleType) -> None:
    assert missing.additions(["NA", "x", " NA", "N|A", "N|A", "Pend\u0131ng"]) == {
        " NA": "UNKNOWN",
        "N|A": "UNKNOWN",
        "Pend\u0131ng": "UNKNOWN",
    }
    assert missing.additions([]) == {}
    assert missing.additions(["[" * 31 + "NA" + "]" * 31]) is not None
    assert missing.additions(["[" * 31 + "NA" + "]" * 32]) is None
    many = ["N" + "|" * k + "A" for k in range(1, 63)]
    assert len(many) == 62
    assert missing.additions(["NA |"]) == {}
    assert missing.additions([*many, "NA|", "|NA"]) is not None
    assert missing.additions([*many, "NA|", "|NA", "N|A|"]) is None
    assert missing.additions(["[Not Applicable]|"]) == {"[Not Applicable]|": "NOT_APPLICABLE"}
