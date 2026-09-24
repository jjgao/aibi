import re

import pytest

from aibi.core.schema.ids import (
    COLUMN_REF_RE,
    CONCEPT_ID_RE,
    DATASET_REF_RE,
    DECIMAL_INTEGER_RE,
    DERIVATION_ID_RE,
    IDENTIFIER_RE,
    ISSUANCE_ID_RE,
    LEAF_KEY_RE,
    MAX_SAFE_INTEGER,
    RELATIONSHIP_ID_RE,
    SHA256_RE,
    integer_value,
    is_pack_id,
    normalise,
    normalise_names,
)
from aibi.core.schema.limits import MAX_IDENTIFIER


@pytest.mark.parametrize("value", ["a", "age", "age_years", "a1", "a_1", "a_", "x2_y3"])
def test_identifiers_accepted(value: str) -> None:
    assert IDENTIFIER_RE.match(value)


@pytest.mark.parametrize("value", ["", "A", "1a", "_a", "a__b", "a-b", "a.b", "é", "a__"])
def test_identifiers_refused(value: str) -> None:
    assert not IDENTIFIER_RE.match(value)


def test_descriptor_id_forms() -> None:
    assert COLUMN_REF_RE.match("members.age")
    assert CONCEPT_ID_RE.match("core:origin.birth")
    assert CONCEPT_ID_RE.match("testpack:origin.first_visit")
    assert not CONCEPT_ID_RE.match("core:")
    assert RELATIONSHIP_ID_RE.match("rel:loans.member_id")
    assert RELATIONSHIP_ID_RE.match("rel:enrolments.site_id+visit")
    assert not RELATIONSHIP_ID_RE.match("rel:loans")


@pytest.mark.parametrize(
    "value", ["lending", "lending@3", "lending@draft", "lending@sha256:" + "a" * 64]
)
def test_dataset_refs_accepted(value: str) -> None:
    assert DATASET_REF_RE.match(value)


@pytest.mark.parametrize(
    "value", ["lending@0", "lending@03", "lending@latest", "lending@sha256:" + "A" * 64, "lending@"]
)
def test_dataset_refs_refused(value: str) -> None:
    assert not DATASET_REF_RE.match(value)


def test_pack_ids_exclude_core_names() -> None:
    assert is_pack_id("testpack")
    for reserved in ("core", "summary", "compare", "survival", "value", "exists", "cohort"):
        assert not is_pack_id(reserved)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Member ID", "member_id"),
        ("  OS_MONTHS  ", "os_months"),
        ("Grade (≥3)", "grade_3"),
        ("ﬁle", "file"),  # NFKC folds the ligature
        ("Straße", "stra_e"),
        ("__x__", "x"),
        ("a--b__c", "a_b_c"),
    ],
)
def test_normalise(name: str, expected: str) -> None:
    assert normalise(name) == expected


def test_normalise_names_prefixes_and_positions() -> None:
    assert normalise_names(["", "2019 data", "!!!"], "column") == ["c_1", "c_2019_data", "c_3"]
    assert normalise_names(["1st"], "table") == ["t_1st"]


def test_collisions_take_the_smallest_free_suffix_in_source_order() -> None:
    assert normalise_names(["Name", "name", "Name_2"], "column") == ["name", "name_2", "name_2_2"]
    assert normalise_names(["a", "A", "a!"], "column") == ["a", "a_2", "a_3"]


def test_the_table_id_dataset_is_reserved() -> None:
    assert normalise_names(["Dataset", "dataset"], "table") == ["dataset_2", "dataset_3"]
    assert normalise_names(["Dataset"], "column") == ["dataset"]


def test_normalised_names_are_identifiers() -> None:
    names = ["Ω", "x" * 3, "9", "", "a b", "A_B", "a_b", "ÄÖÜ", "​"]
    for value in normalise_names(names, "table") + normalise_names(names, "column"):
        assert IDENTIFIER_RE.match(value), value


def test_reimport_keeps_the_ids_of_names_seen_before() -> None:
    names = ["Age (years)", "Age_years"]
    first = normalise_names(names, "column")
    assert first == ["age_years", "age_years_2"]
    previous = list(zip(names, first, strict=True))
    again = normalise_names(["AGE-YEARS", "Age (years)", "Age_years"], "column", previous)
    assert again == ["age_years_3", "age_years", "age_years_2"]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (["x", "x"], ["x", "x"], ["x", "x_2"]),
        (["x", "x"], ["X", "x", "x"], ["x_3", "x", "x_2"]),
        (["x", "x"], ["x"], ["x"]),
        (["", ""], ["x", "", ""], ["x", "c_1", "c_2"]),
        (["", ""], [""], ["c_1"]),
        (["a", "b"], ["b", "a"], ["b", "a"]),
    ],
)
def test_reimport_matches_repeated_names_by_occurrence(
    before: list[str], after: list[str], expected: list[str]
) -> None:
    previous = list(zip(before, normalise_names(before, "column"), strict=True))
    assert normalise_names(after, "column", previous) == expected


def test_ids_are_cut_to_the_identifier_limit() -> None:
    long = "Measured " + "value " * 30
    [first, second] = normalise_names([long, long], "column")
    assert len(first) == MAX_IDENTIFIER
    assert len(second) == MAX_IDENTIFIER
    assert second.endswith("_2")
    assert not first.endswith("_")
    assert IDENTIFIER_RE.fullmatch(first)
    assert IDENTIFIER_RE.fullmatch(second)
    assert normalise_names(["9" * 100], "table") == ["t_" + "9" * 62]


def test_patterns_are_matched_whole() -> None:
    assert not is_pack_id("lending\n")
    assert not is_pack_id("x" * (MAX_IDENTIFIER + 1))
    assert IDENTIFIER_RE.match("a\n")  # why Python code uses fullmatch
    assert not IDENTIFIER_RE.fullmatch("a\n")


@pytest.mark.parametrize(
    ("regex", "good", "bad"),
    [
        (SHA256_RE, "sha256:" + "0" * 64, "sha256:" + "0" * 63),
        (DERIVATION_ID_RE, "drv:" + "a" * 64, "drv:" + "A" * 64),
        (LEAF_KEY_RE, "leaf:" + "f" * 64, "leaf:" + "g" * 64),
        (ISSUANCE_ID_RE, "iss:01ARZ3NDEKTSV4RRFFQ69G5FAV", "iss:81ARZ3NDEKTSV4RRFFQ69G5FAV"),
        (ISSUANCE_ID_RE, "iss:7ZZZZZZZZZZZZZZZZZZZZZZZZZ", "iss:01ARZ3NDEKTSV4RRFFQ69G5FAU!"),
        (DECIMAL_INTEGER_RE, "-9007199254740993", "01"),
    ],
)
def test_hash_and_id_forms(regex: re.Pattern[str], good: str, bad: str) -> None:
    assert regex.fullmatch(good)
    assert not regex.fullmatch(bad)


def test_integers_beyond_the_safe_range_become_decimal_strings() -> None:
    assert integer_value(MAX_SAFE_INTEGER) == MAX_SAFE_INTEGER
    assert integer_value(-MAX_SAFE_INTEGER - 1) == "-9007199254740992"
