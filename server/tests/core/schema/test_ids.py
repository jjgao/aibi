import pytest

from aibi.core.schema.ids import (
    COLUMN_REF_RE,
    CONCEPT_ID_RE,
    DATASET_REF_RE,
    IDENTIFIER_RE,
    RELATIONSHIP_ID_RE,
    is_pack_id,
    normalise,
    normalise_names,
)


@pytest.mark.parametrize("value", ["a", "age", "age_years", "a1", "a_1", "a_", "x2_y3"])
def test_identifiers_accepted(value: str) -> None:
    assert IDENTIFIER_RE.match(value)


@pytest.mark.parametrize("value", ["", "A", "1a", "_a", "a__b", "a-b", "a.b", "é", "a__"])
def test_identifiers_refused(value: str) -> None:
    assert not IDENTIFIER_RE.match(value)


def test_descriptor_id_forms() -> None:
    assert COLUMN_REF_RE.match("patients.age")
    assert CONCEPT_ID_RE.match("core:origin.birth")
    assert CONCEPT_ID_RE.match("onco:origin.first_sequencing")
    assert not CONCEPT_ID_RE.match("core:")
    assert RELATIONSHIP_ID_RE.match("rel:samples.patient_id")
    assert RELATIONSHIP_ID_RE.match("rel:enrolments.site_id+visit")
    assert not RELATIONSHIP_ID_RE.match("rel:samples")


@pytest.mark.parametrize("value", ["trial", "trial@3", "trial@draft", "trial@sha256:" + "a" * 64])
def test_dataset_refs_accepted(value: str) -> None:
    assert DATASET_REF_RE.match(value)


@pytest.mark.parametrize(
    "value", ["trial@0", "trial@03", "trial@latest", "trial@sha256:" + "A" * 64, "trial@"]
)
def test_dataset_refs_refused(value: str) -> None:
    assert not DATASET_REF_RE.match(value)


def test_pack_ids_exclude_core_names() -> None:
    assert is_pack_id("onco")
    for reserved in ("core", "summary", "compare", "survival", "value", "exists", "cohort"):
        assert not is_pack_id(reserved)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Patient ID", "patient_id"),
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
    first = normalise_names(["Age (years)", "Age_years"], "column")
    assert first == ["age_years", "age_years_2"]
    previous = dict(zip(["Age (years)", "Age_years"], first, strict=True))
    again = normalise_names(["AGE-YEARS", "Age (years)", "Age_years"], "column", previous)
    assert again == ["age_years_3", "age_years", "age_years_2"]


def test_duplicate_source_names_keep_one_id_and_derive_the_rest() -> None:
    assert normalise_names(["a", "a"], "column", {"a": "a"}) == ["a", "a_2"]
