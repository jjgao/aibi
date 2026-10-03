"""The pack's extension schemas and ontology systems through descriptor writes (SPEC §5.4,
§10.1, D247): each schema is accepted by the core's checks of a pack's schemas, accepts what it
lists, and refuses each violation ``INVALID_EXTENSION`` at the member; every one of cBioPortal's
28 ``meta`` pairs is accepted and any other text refused; and the pack's systems check codes in
every dataset, one that does not list the pack included."""

from collections.abc import Callable, Mapping, Sequence
from types import ModuleType

import pytest
from pydantic import JsonValue, ValidationError

from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.jsonschemas import problems

Extended = Callable[[Descriptor, Mapping[str, JsonValue]], Descriptor]
Refused = Callable[[Sequence[Descriptor]], list[tuple[str, str | None]]]


def _base(*, listed: bool = True) -> list[Descriptor]:
    return [
        build.dataset(packs=["onco"]) if listed else build.dataset(),
        build.table("samples", key=["sample_id"]),
        build.column("samples.sample_id", "string"),
        build.table("patients", key=["patient_id"]),
        build.column("patients.patient_id", "string"),
        build.column("samples.patient_id", "string"),
        build.relationship("samples", ["patient_id"], "patients"),
    ]


KIND_AT = {"dataset": 0, "table": 1, "column": 2, "relationship": 6}


def _with(
    extended: Extended, kind: str, members: Mapping[str, JsonValue], *, listed: bool = True
) -> list[Descriptor]:
    descriptors = _base(listed=listed)
    at = KIND_AT[kind]
    descriptors[at] = extended(descriptors[at], members)
    return descriptors


def test_the_core_accepts_each_schema(onco: ModuleType) -> None:
    for kind, schema in onco.schemas.SCHEMAS.items():
        assert problems(schema) == [], kind


ACCEPTED: list[tuple[str, dict[str, JsonValue]]] = [
    (
        "dataset",
        {
            "cancer_study_identifier": "x" * 200,
            "type_of_cancer": "brca",
            "reference_genome": "hg19",
        },
    ),
    ("dataset", {"cancer_study_identifier": "x", "type_of_cancer": "t" * 64}),
    ("dataset", {"reference_genome": "hg38"}),
    ("dataset", {"reference_genome": "mm10"}),
    ("table", {"meta": "MUTATION_EXTENDED:MAF", "stable_id": "s" * 200}),
    ("table", {"case_list_category": "c" * 64, "stable_id": "s"}),
    ("column", {"attribute_priority": -1}),
    ("column", {"attribute_priority": 0}),
    ("column", {"attribute_priority": 2**53 - 1}),
    ("column", {"attribute_priority": 1.0}),
]


@pytest.mark.parametrize(("kind", "members"), ACCEPTED)
def test_what_each_schema_lists_is_accepted(
    extended: Extended, refused: Refused, kind: str, members: dict[str, JsonValue]
) -> None:
    assert refused(_with(extended, kind, members)) == []


REFUSED: list[tuple[str, str, JsonValue]] = [
    ("dataset", "cancer_study_identifier", ""),
    ("dataset", "cancer_study_identifier", "x" * 201),
    ("dataset", "cancer_study_identifier", 7),
    ("dataset", "type_of_cancer", ""),
    ("dataset", "type_of_cancer", "t" * 65),
    ("dataset", "type_of_cancer", ["brca"]),
    ("dataset", "reference_genome", "GRCh37"),
    ("dataset", "reference_genome", "HG19"),
    ("dataset", "reference_genome", "hg19 "),
    ("dataset", "name", "x"),
    ("table", "meta", "CLINICAL:SAMPLE"),
    ("table", "meta", "clinical:sample_attributes"),
    ("table", "meta", "MUTATION_EXTENDED:FUSION"),
    ("table", "meta", "MUTATION_EXTENDED"),
    ("table", "meta", "MUTATION_EXTENDED:MAF "),
    ("table", "meta", {"genetic_alteration_type": "MUTATION_EXTENDED", "datatype": "MAF"}),
    ("table", "stable_id", ""),
    ("table", "stable_id", "s" * 201),
    ("table", "case_list_category", ""),
    ("table", "case_list_category", "c" * 65),
    ("table", "genetic_alteration_type", "MUTATION_EXTENDED"),
    ("column", "attribute_priority", -2),
    ("column", "attribute_priority", 1.5),
    ("column", "attribute_priority", "1"),
    ("column", "attribute_priority", True),
    ("column", "priority", 1),
]


SCHEMA_MEMBERS = {
    "dataset": {"cancer_study_identifier", "type_of_cancer", "reference_genome"},
    "table": {"meta", "stable_id", "case_list_category"},
    "column": {"attribute_priority"},
}


@pytest.mark.parametrize(("kind", "member", "value"), REFUSED)
def test_each_violation_is_refused_at_its_member(
    extended: Extended, refused: Refused, kind: str, member: str, value: JsonValue
) -> None:
    listed = member in SCHEMA_MEMBERS[kind]
    # A member the schema does not list is refused at the extension object, by
    # ``additionalProperties``; a listed member's wrong value, at the member.
    path = f"/{KIND_AT[kind]}/extensions/onco" + (f"/{member}" if listed else "")
    assert refused(_with(extended, kind, {member: value})) == [("INVALID_EXTENSION", path)]


def test_a_priority_past_json_s_exact_integers_is_refused_by_the_descriptor_model(
    extended: Extended,
) -> None:
    with pytest.raises(ValidationError):
        _with(extended, "column", {"attribute_priority": 2**53})


def test_a_kind_without_a_schema_refuses_the_extension(
    extended: Extended, refused: Refused
) -> None:
    descriptors = _with(extended, "relationship", {"meta": "MUTATION_EXTENDED:MAF"})
    assert refused(descriptors) == [
        ("INVALID_EXTENSION", f"/{KIND_AT['relationship']}/extensions/onco")
    ]


def test_every_meta_pair_is_accepted_and_no_other(
    onco: ModuleType, extended: Extended, refused: Refused
) -> None:
    pairs: tuple[str, ...] = onco.schemas.PAIRS
    assert len(pairs) == len(set(pairs)) == 28
    for pair in pairs:
        assert refused(_with(extended, "table", {"meta": pair})) == [], pair
    alteration_types = {pair.split(":")[0] for pair in pairs}
    datatypes = {pair.split(":")[1] for pair in pairs}
    for alteration in sorted(alteration_types):
        for datatype in sorted(datatypes):
            pair = f"{alteration}:{datatype}"
            if pair not in pairs:
                found = refused(_with(extended, "table", {"meta": pair}))
                assert found == [("INVALID_EXTENSION", "/1/extensions/onco/meta")], pair


CODES: list[tuple[str, str, bool]] = [
    ("OncoTree", "MDS/MPN", True),
    ("OncoTree", "XX!", False),
    ("HGNC", "HGNC:11998", True),
    ("HGNC", "TP53", False),
    ("NCBIGene", "NCBIGene:7157", True),
    ("NCBIGene", "0", False),
    ("SO", "SO_0001583", True),
    ("SO", "so:0001583", False),
]


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize(("system", "code", "valid"), CODES)
def test_codes_are_checked_in_every_dataset(
    refused: Refused, listed: bool, system: str, code: str, valid: bool
) -> None:
    cited = {"system": system, "code": code, "label": "a term", "relation": "exact"}
    descriptors = _base(listed=listed)
    descriptors[0] = (
        build.dataset(packs=["onco"], data_use=[cited])
        if listed
        else build.dataset(data_use=[cited])
    )
    descriptors[2] = build.column("samples.sample_id", "string", concepts=[cited])
    descriptors.append(
        build.column(
            "samples.kind",
            "category",
            permissible_values={"values": [{"value": "a", "concepts": [cited]}]},
        )
    )
    paths = [
        "/0/fields/data_use/0/code",
        "/2/fields/concepts/0/code",
        f"/{len(descriptors) - 1}/fields/permissible_values/values/0/concepts/0/code",
    ]
    expected = [] if valid else [("INVALID_VALUE", path) for path in paths]
    assert sorted(refused(descriptors), key=str) == sorted(expected, key=str)


def test_a_system_is_named_exactly(refused: Refused) -> None:
    cited = {"system": "hgnc", "code": "TP53", "label": "a term", "relation": "exact"}
    descriptors = _base(listed=False)
    descriptors[2] = build.column("samples.sample_id", "string", concepts=[cited])
    assert refused(descriptors) == []
