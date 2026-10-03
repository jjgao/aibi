"""The pack's ontology validators (SPEC §5.4, §10.1, D247, D388), which read untrusted codes in
every dataset of a deployment that installs the pack.

They are tested as a class, against an independent oracle: an ASCII ``fullmatch`` of each form's
grammar, written here from the plan, never from the validators' code.

- **A brute force:** every code point, U+0000 to U+10FFFF, at each structural position of each
  form (alone, at either end, inside a prefix, after it, between digits, past the length bound).
- **A property:** any text at all, lone surrogates included, gives what the oracle gives.
- **Named cases:** the choices the plan made, each by name (lower-case prefixes, symbols, ``0``,
  leading zeros, negative ids, whitespace).
- **Exactness:** anything but an exact ``str``, a subclass included, is refused before anything
  of it is read.
"""

import re
from collections.abc import Callable
from typing import Any, NoReturn

import pytest
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st

Validators = dict[str, Callable[[str], bool]]

ORACLES = {
    "OncoTree": re.compile(r"[A-Za-z0-9_/-]{1,32}", re.ASCII),
    "HGNC": re.compile(r"(?:HGNC:)?[1-9][0-9]{0,6}", re.ASCII),
    "NCBIGene": re.compile(r"(?:NCBIGene:)?[1-9][0-9]{0,9}", re.ASCII),
    "SO": re.compile(r"(?:SO[:_])?[0-9]{7}", re.ASCII),
}
"""Each form's grammar, as the plan states it."""

TEMPLATES = {
    "OncoTree": [
        "{}",
        "{}AB",
        "A{}B",
        "AB{}",
        "A" * 31 + "{}",
        "A" * 32 + "{}",
    ],
    "HGNC": [
        "{}",
        "{}1",
        "1{}",
        "12{}34",
        "123456{}",
        "1234567{}",
        "{}HGNC:1",
        "{}GNC:1",
        "H{}NC:1",
        "HG{}C:1",
        "HGN{}:1",
        "HGNC{}1",
        "HGNC:{}",
        "HGNC:{}1",
        "HGNC:1{}",
        "HGNC:123456{}",
        "HGNC:1234567{}",
    ],
    "NCBIGene": [
        "{}",
        "{}7",
        "7{}",
        "71{}57",
        "123456789{}",
        "1234567890{}",
        "{}NCBIGene:7",
        "{}CBIGene:7",
        "NCBI{}ene:7",
        "NCBIGen{}:7",
        "NCBIGene{}7",
        "NCBIGene:{}",
        "NCBIGene:{}7",
        "NCBIGene:7{}",
        "NCBIGene:123456789{}",
        "NCBIGene:1234567890{}",
    ],
    "SO": [
        "{}000158",
        "000{}158",
        "000158{}",
        "0001583{}",
        "{}SO:0001583",
        "{}O:0001583",
        "S{}:0001583",
        "SO{}0001583",
        "SO:{}001583",
        "SO:000158{}",
        "SO_000158{}",
        "SO:0001583{}",
    ],
}
"""The structural positions of each form: ``{}`` is where each code point goes."""

INTERIOR = {"OncoTree": "A{}B", "HGNC": "HGNC:1{}", "NCBIGene": "NCBIGene:7{}", "SO": "SO:000158{}"}
"""One interior position of each form, after its prefix where it has one."""

EVERY = range(0x110000)
"""Every code point, surrogates included."""


def _cased_or_numeric(point: int) -> bool:
    character = chr(point)
    return (
        character.isnumeric()
        or character.isspace()
        or character.upper() != character
        or character.lower() != character
    )


BOUNDED = (
    *range(0x10000),
    *(point for point in range(0x10000, 0x110000) if point % 61 == 0 or _cased_or_numeric(point)),
)
"""Every code point of the Basic Multilingual Plane (ASCII, every script's letters and digits
there, the full-width forms and the surrogates), every other one that is a digit, numeric,
space or cased, and every 61st of the rest: the bound that keeps the sweep of every position
within CI's budget, since every code point is swept at an interior position of each form."""


def _disagreements(
    validate: Callable[[str], bool],
    oracle: re.Pattern[str],
    template: str,
    points: range | tuple[int, ...],
) -> list[str]:
    before, after = template.split("{}")
    found: list[str] = []
    for point in points:
        code = before + chr(point) + after
        if validate(code) is not (oracle.fullmatch(code) is not None):
            found.append(f"U+{point:04X}")
    return found


def test_the_templates_cover_each_form_and_its_interior() -> None:
    assert set(TEMPLATES) == set(ORACLES) == set(INTERIOR)
    for system, template in INTERIOR.items():
        assert template in TEMPLATES[system]
        assert all(template.count("{}") == 1 for template in TEMPLATES[system])
    assert len(BOUNDED) > 80_000
    assert {0xD800, 0xDFFF, 0xFF10, 0x0660, 0x1D7CE, 0x10FFFF - 0x10FFFF % 61} <= set(BOUNDED)


@pytest.mark.parametrize("system", sorted(INTERIOR))
def test_every_code_point_inside_each_form_agrees_with_the_oracle(
    validators: Validators, system: str
) -> None:
    assert _disagreements(validators[system], ORACLES[system], INTERIOR[system], EVERY) == []


@pytest.mark.parametrize("system", sorted(TEMPLATES))
def test_the_code_points_at_every_position_agree_with_the_oracle(
    validators: Validators, system: str
) -> None:
    found = {
        template: wrong
        for template in TEMPLATES[system]
        if (wrong := _disagreements(validators[system], ORACLES[system], template, BOUNDED))
    }
    assert found == {}


# --- The property -------------------------------------------------------------------------------

SUPPRESSED = (
    HealthCheck.too_slow,
    HealthCheck.filter_too_much,
    HealthCheck.function_scoped_fixture,
)
"""The validators fixture holds no state, and ``find`` searches on purpose."""
SURROGATES = st.integers(0xD800, 0xDFFF).map(chr)
"""Lone surrogates, which ``st.characters()`` leaves out unless asked."""
ANY_CHARACTER = st.one_of(
    st.characters(codec=None, categories=None, exclude_categories=()),
    SURROGATES,
    st.sampled_from(list("0123456789:_-/ \t\n\x00")),
)
PREFIXES = st.sampled_from(
    [
        "",
        "HGNC:",
        "hgnc:",
        "Hgnc:",
        "NCBIGene:",
        "ncbigene:",
        "NCBIGENE:",
        "SO:",
        "SO_",
        "so:",
        "so_",
        "So:",
    ]
)
DIGITS = st.text(st.sampled_from("0123456789"), max_size=12)
CODES = st.one_of(
    st.text(ANY_CHARACTER, max_size=70),
    st.builds(lambda prefix, digits: prefix + digits, PREFIXES, DIGITS),
    st.builds(
        lambda prefix, digits, extra, at: prefix + digits[:at] + extra + digits[at:],
        PREFIXES,
        DIGITS,
        ANY_CHARACTER,
        st.integers(0, 12),
    ),
    st.text(st.sampled_from("ABCZabcz09_-/"), max_size=40),
)
"""Any text, lone surrogates included, and text near each form, accepted or nearly so."""


def test_the_property_draws_lone_surrogates_and_accepted_codes(validators: Validators) -> None:
    quick = settings(database=None, max_examples=5_000, suppress_health_check=SUPPRESSED)
    found = find(CODES, lambda code: any("\ud800" <= c <= "\udfff" for c in code), settings=quick)
    assert any("\ud800" <= c <= "\udfff" for c in found)
    for system, validate in validators.items():
        assert validate(find(CODES, validate, settings=quick)), system


@settings(
    max_examples=3_000,
    database=None,
    suppress_health_check=SUPPRESSED,
)
@given(CODES)
def test_any_text_gives_what_the_oracle_gives(validators: Validators, code: str) -> None:
    for system, validate in validators.items():
        assert validate(code) is (ORACLES[system].fullmatch(code) is not None), system


# --- Named cases: the plan's choices ------------------------------------------------------------

ACCEPTED = {
    "OncoTree": ["MDS/MPN", "HCL-V", "ADRENAL_GLAND", "SOFT_TISSUE", "BRCA", "brca", "A", "A" * 32],
    "HGNC": ["HGNC:11998", "11998", "HGNC:1", "1", "HGNC:1234567", "1234567"],
    "NCBIGene": ["NCBIGene:7157", "7157", "1", "1234567890", "NCBIGene:9999999999"],
    "SO": ["SO:0001583", "SO_0001583", "0001583", "SO:0000000"],
}
REFUSED = {
    "OncoTree": {
        "empty": "",
        "33 characters": "A" * 33,
        "a symbol": "XX!",
        "a space": "A B",
        "a dot": "A.B",
        "a newline": "AB\n",
        "NUL": "A\x00",
        "a non-ASCII digit": "\u0661",
        "a non-ASCII letter": "\u00c9",
        "a lone surrogate": "A\ud800",
    },
    "HGNC": {
        "a symbol": "TP53",
        "a lower-case prefix": "hgnc:11998",
        "a mixed-case prefix": "Hgnc:11998",
        "zero": "0",
        "zero, prefixed": "HGNC:0",
        "a leading zero": "05",
        "a leading zero, prefixed": "HGNC:05",
        "8 digits": "12345678",
        "8 digits, prefixed": "HGNC:12345678",
        "a prefix alone": "HGNC:",
        "a doubled prefix": "HGNC:HGNC:1",
        "a leading space": " 11998",
        "a trailing newline": "11998\n",
        "a sign": "+11998",
        "non-ASCII digits": "\u0661\u0662",
        "full-width digits": "\uff11\uff12",
        "a lone surrogate": "1\ud800",
    },
    "NCBIGene": {
        "a lower-case prefix": "ncbigene:7157",
        "an upper-case prefix": "NCBIGENE:7157",
        "zero": "0",
        "zero, prefixed": "NCBIGene:0",
        "a negative id": "-1",
        "a negative id, prefixed": "NCBIGene:-7157",
        "a leading zero": "07157",
        "11 digits": "1" * 11,
        "a prefix alone": "NCBIGene:",
        "a leading space": " 7157",
        "a trailing space": "7157 ",
        "a symbol": "TP53",
        "non-ASCII digits": "\u0661\u0662",
        "a lone surrogate": "7\ud800",
    },
    "SO": {
        "a lower-case prefix": "so:0001583",
        "a lower-case OBO prefix": "so_0001583",
        "a mixed-case prefix": "So:0001583",
        "6 digits": "SO:000158",
        "8 digits": "SO:00015830",
        "a CURIE without a separator": "SO0001583",
        "a hyphen": "SO-0001583",
        "a name": "missense_variant",
        "a trailing newline": "SO:0001583\n",
        "non-ASCII digits": "SO:\u0660\u0660\u0660\u0661\u0665\u0668\u0663",
        "a lone surrogate": "SO:000158\ud800",
    },
}


@pytest.mark.parametrize(
    ("system", "code"), [(system, code) for system, codes in ACCEPTED.items() for code in codes]
)
def test_the_forms_are_accepted(validators: Validators, system: str, code: str) -> None:
    assert validators[system](code) is True


@pytest.mark.parametrize(
    ("system", "case"), [(system, case) for system, cases in REFUSED.items() for case in cases]
)
def test_what_the_plan_refuses_is_refused(validators: Validators, system: str, case: str) -> None:
    assert validators[system](REFUSED[system][case]) is False


@pytest.mark.parametrize("system", sorted(ORACLES))
def test_long_text_is_refused(validators: Validators, system: str) -> None:
    for length in (64, 65, 4096, 1_000_000):
        assert validators[system]("1" * length) is False
        assert validators[system]("A" * length) is False


# --- Exactness ----------------------------------------------------------------------------------


class Hostile(str):
    """A ``str`` subclass whose every method a validator might call raises (set below)."""


def _read(*given: object, **named: object) -> NoReturn:
    raise AssertionError("a validator read a str subclass")


for _method in (
    "__len__",
    "__iter__",
    "__getitem__",
    "__contains__",
    "__eq__",
    "__ne__",
    "__hash__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__add__",
    "__radd__",
    "__mul__",
    "__mod__",
    "__format__",
    "__str__",
    "__repr__",
    "startswith",
    "endswith",
    "upper",
    "lower",
    "isdigit",
    "isascii",
    "strip",
    "encode",
    "find",
    "index",
    "split",
    "partition",
    "removeprefix",
):
    setattr(Hostile, _method, _read)


@pytest.mark.parametrize("system", sorted(ORACLES))
def test_anything_but_an_exact_str_is_refused_before_it_is_read(
    validators: Validators, system: str
) -> None:
    validate: Callable[[Any], bool] = validators[system]
    accepted = ACCEPTED[system][0]
    assert validate(Hostile(accepted)) is False
    for other in (None, 7157, 1.0, b"7157", bytearray(b"7157"), [accepted], (accepted,), object()):
        assert validate(other) is False
