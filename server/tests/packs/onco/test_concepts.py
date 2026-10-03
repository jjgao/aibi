"""The pack's concepts where the core reads them (SPEC §5.4, §5.7, §10.1, D247, D391): each one
named at a place of its sort is accepted on a descriptor write, and at a place of another sort is
refused; a misspelt id is refused at each kind of place; every ``onco:`` id this pull request
names is registered; and the concepts' own ontology references are valid and canonical."""

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.pack_api import PackRegistry

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
Refused = Callable[[list[Descriptor]], list[tuple[str, str | None]]]
SERVER = Path(__file__).resolve().parents[3]

MISSPELT = frozenset({"onco:orgin.diagnosis", "onco:sampel_type", "onco:sample.type"})
"""The ids the tests misspell on purpose, to see them refused."""


def _base(**changes: Mapping[str, Any]) -> list[Descriptor]:
    """Patients, their samples and an endpoint, each descriptor's fields updated by ``changes``
    (keyed by id, ``.`` as ``__`` and ``:`` as ``_``)."""
    descriptors = [
        build.dataset(),
        build.table("patients", ["patient_id"]),
        build.column("patients.patient_id", "string"),
        build.column("patients.os_months", "time_offset", units="mo"),
        build.column("patients.os_status", "category"),
        build.table("samples", ["sample_id"]),
        build.column("samples.sample_id", "string"),
        build.column("samples.patient_id", "string"),
        build.column("samples.sample_type", "category"),
        build.relationship("samples", ["patient_id"], "patients"),
        build.coverage("rel:samples.patient_id"),
        build.descriptor(
            "endpoint",
            "ep:os",
            {
                "table": "patients",
                "time_column": "os_months",
                "status_column": "os_status",
                "event_coding": {"event": ["1:DECEASED"], "censored": ["0:LIVING"]},
            },
        ),
    ]
    found: list[Descriptor] = []
    for descriptor in descriptors:
        update = changes.get(descriptor.id.replace(".", "__").replace(":", "_"))
        if update is not None:
            dumped = descriptor.model_dump(mode="json")
            dumped["fields"].update(update)
            for name in update:
                dumped["curation"][f"/fields/{name}"] = dumped["curation"]["/label"]
            descriptor = _ADAPTER.validate_python(dumped)
        found.append(descriptor)
    return found


AT = {descriptor.id: index for index, descriptor in enumerate(_base())}


def _mapped(id: str) -> dict[str, Any]:
    return {"maps_to": {"concept": id, "transform": None}}


PLACES: dict[str, Callable[[str], tuple[list[Descriptor], str]]] = {
    "column maps_to": lambda id: (
        _base(samples__sample_type=_mapped(id)),
        f"/{AT['samples.sample_type']}/fields/maps_to/concept",
    ),
    "table maps_to": lambda id: (
        _base(samples=_mapped(id)),
        f"/{AT['samples']}/fields/maps_to/concept",
    ),
    "table time_origin": lambda id: (
        _base(patients={"time_origin": id}),
        f"/{AT['patients']}/fields/time_origin",
    ),
    "endpoint maps_to": lambda id: (
        _base(ep_os=_mapped(id)),
        f"/{AT['ep:os']}/fields/maps_to/concept",
    ),
    "endpoint time_origin": lambda id: (
        _base(ep_os={"time_origin": id}),
        f"/{AT['ep:os']}/fields/time_origin",
    ),
    "parent_scope value leaf": lambda id: (
        _base(
            cov_samples__patient_id={
                "parent_scope": {"kind": "value", "column": id, "values": ["PRIMARY_SOLID_TUMOR"]}
            }
        ),
        f"/{AT['cov:samples.patient_id']}/fields/parent_scope/column",
    ),
}
"""Each place a descriptor names a concept: a release naming ``id`` there, and its path."""
SORT_OF_PLACE = {
    "column maps_to": "value",
    "table maps_to": "table",
    "table time_origin": "time_origin",
    "endpoint maps_to": "endpoint",
    "endpoint time_origin": "time_origin",
    "parent_scope value leaf": "value",
}


def _ids(registry: PackRegistry) -> list[str]:
    return sorted(registry.concept_sorts())


def test_the_base_release_is_accepted(refused: Refused) -> None:
    assert refused(_base()) == []


def test_each_concept_is_accepted_at_each_place_of_its_sort_alone(
    registry: PackRegistry, refused: Refused
) -> None:
    sorts = registry.concept_sorts()
    for id in _ids(registry):
        for place, make in PLACES.items():
            descriptors, path = make(id)
            found = refused(descriptors)
            if SORT_OF_PLACE[place] == sorts[id]:
                assert found == [], (id, place)
            else:
                assert found == [("INVALID_VALUE", path)], (id, place)


@pytest.mark.parametrize(
    ("place", "id", "code"),
    [
        ("table time_origin", "onco:orgin.diagnosis", "UNKNOWN_DESCRIPTOR"),
        ("endpoint time_origin", "onco:orgin.diagnosis", "UNKNOWN_DESCRIPTOR"),
        ("column maps_to", "onco:endpoint.os", "INVALID_VALUE"),
        ("parent_scope value leaf", "onco:sampel_type", "UNKNOWN_DESCRIPTOR"),
        ("column maps_to", "onco:sample.type", "UNKNOWN_DESCRIPTOR"),
    ],
)
def test_a_wrong_concept_is_refused_where_it_is_named(
    refused: Refused, place: str, id: str, code: str
) -> None:
    descriptors, path = PLACES[place](id)
    assert refused(descriptors) == [(code, path)]


def test_every_onco_id_this_pull_request_names_is_registered(registry: PackRegistry) -> None:
    named: set[str] = set()
    sources = [
        *(SERVER / "src" / "aibi" / "packs" / "onco").glob("*.py"),
        *(SERVER / "tests" / "packs" / "onco").glob("*.py"),
    ]
    assert len(sources) >= 10
    for source in sources:
        named.update(re.findall(r"onco:[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", source.read_text()))
    assert named - MISSPELT == set(registry.concept_sorts())
    assert MISSPELT.isdisjoint(registry.concept_sorts())


def _canonical(system: str, code: str) -> bool:
    """The canonical forms (D274 compares ``(system, code)`` exactly)."""
    if system == "OncoTree":
        return code == code.upper()
    if system == "HGNC":
        return code.startswith("HGNC:")
    if system == "NCBIGene":
        return not code.startswith("NCBIGene:")
    if system == "SO":
        return code.startswith("SO:")
    return False


@pytest.mark.parametrize(
    ("system", "code", "canonical"),
    [
        ("OncoTree", "BRCA", True),
        ("OncoTree", "brca", False),
        ("HGNC", "HGNC:11998", True),
        ("HGNC", "11998", False),
        ("NCBIGene", "7157", True),
        ("NCBIGene", "NCBIGene:7157", False),
        ("SO", "SO:0001583", True),
        ("SO", "SO_0001583", False),
        ("SO", "0001583", False),
    ],
)
def test_the_canonical_forms(system: str, code: str, canonical: bool) -> None:
    assert _canonical(system, code) is canonical


def test_each_reference_a_concept_carries_is_valid_and_canonical(registry: PackRegistry) -> None:
    references = [
        cited
        for concept in registry.concepts()
        if concept.fields.permissible_values is not None
        for value in concept.fields.permissible_values.values
        for cited in value.concepts or ()
    ]
    assert len(references) == 18
    for cited in references:
        validate = registry.ontology_validator(cited.system)
        assert validate is not None, cited.system
        code = cited.code
        assert validate.call(lambda check, code=code: check(code)) is True, code
        assert _canonical(cited.system, cited.code), cited.code
