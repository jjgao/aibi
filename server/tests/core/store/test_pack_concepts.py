"""The concepts a descriptor names outside ``core:``, checked on every descriptor write (SPEC
§5.7, §10.1, D247, D391), with a test-only pack of shelf marks whose concepts are of all four
sorts: every place a concept is named, found from the descriptors' JSON Schema and from the
checker's code; the codes, paths and listings of the refusals; ``only``; and what a registry of
``None`` registers (nothing)."""

import ast
import functools
import inspect
import random
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, NamedTuple, cast

import pytest
from pydantic import TypeAdapter

import aibi
from aibi.core.engine import build
from aibi.core.schema import release as release_module
from aibi.core.schema.concepts import CORE_IDS, core_ids
from aibi.core.schema.descriptors import (
    RELEASE_KINDS,
    ConceptDescriptor,
    ConceptFields,
    Descriptor,
)
from aibi.core.schema.export import descriptor_schema
from aibi.core.schema.ids import CONCEPT_NAMESPACE_SCHEMA
from aibi.core.schema.jsonschemas import STEPS_BASE, STEPS_PER_VALUE
from aibi.core.schema.limits import MAX_REFUSALS
from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import Pack, PackManifest, PackRegistry
from aibi.core.schema.params import NEAREST, nearest
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.release import check_release, pack_concepts
from aibi.core.store.writes import by_id, check_writes, failed

_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)
RIGHT = {
    "value": "shelf:mark",
    "table": "shelf:shelf",
    "endpoint": "shelf:loan.returned",
    "time_origin": "shelf:origin.shelved",
}
"""The shelf pack's concept of each sort."""
WRONG = {"value": "table", "table": "endpoint", "endpoint": "time_origin", "time_origin": "value"}
"""For each sort, another one."""


def concept(id: str, sort: str) -> ConceptDescriptor:
    return ConceptDescriptor(
        kind="concept",
        id=id,
        version=1,
        label=id,
        definition=f"A {sort} concept of the shelf.",
        fields=ConceptFields(sort=sort),  # type: ignore[arg-type]
    )


def registry(*concepts: tuple[str, str], version: str = "1.0.0") -> PackRegistry:
    """The shelf pack, with ``concepts`` (id, sort), or its own of each sort."""
    given = concepts or tuple((id, sort) for sort, id in RIGHT.items())
    manifest = PackManifest(id="shelf", version=version, results_version=1, requires_core=">=0")
    pack = Pack(manifest=manifest, concepts=[concept(id, sort) for id, sort in given])
    return PackRegistry([pack], core_version=aibi.__version__)


SHELF = registry()
NONE = PackRegistry([], core_version=aibi.__version__)


def ids(sort: str, packs: PackRegistry | None = SHELF) -> list[str]:
    return list(CORE_IDS.get(sort, ()) if packs is None else packs.concept_ids(sort))


# --- A release with each place a concept is named ------------------------------------------------


def base(**changes: Mapping[str, Any]) -> list[Descriptor]:
    """A whole release: members, loans and their relationship, its coverage and an endpoint,
    each descriptor's fields updated by ``changes`` (keyed by id, ``.`` as ``__``)."""
    descriptors = [
        build.dataset(),
        build.table("members", ["member_id"]),
        build.column("members.member_id", "string"),
        build.column("members.grade", "category"),
        build.table("loans", ["loan_id"], role="event"),
        build.column("loans.loan_id", "string"),
        build.column("loans.member_id", "string"),
        build.column("loans.days", "time_offset", units="d"),
        build.column("loans.returned", "category"),
        build.relationship("loans", ["member_id"], "members"),
        build.coverage("rel:loans.member_id"),
        build.descriptor(
            "endpoint",
            "ep:returned",
            {
                "table": "loans",
                "time_column": "days",
                "status_column": "returned",
                "event_coding": {"event": ["yes"], "censored": ["no"]},
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


AT = {descriptor.id: index for index, descriptor in enumerate(base())}


def mapped(id: str) -> dict[str, Any]:
    return {"maps_to": {"concept": id, "transform": None}}


def scoped(leaf: dict[str, Any]) -> dict[str, Any]:
    return {"parent_scope": leaf}


Place = Callable[[str], tuple[list[Descriptor], str]]


def _column(id: str) -> tuple[list[Descriptor], str]:
    return base(members__grade=mapped(id)), f"/{AT['members.grade']}/fields/maps_to/concept"


def _table(id: str) -> tuple[list[Descriptor], str]:
    return base(members=mapped(id)), f"/{AT['members']}/fields/maps_to/concept"


def _table_origin(id: str) -> tuple[list[Descriptor], str]:
    return base(members={"time_origin": id}), f"/{AT['members']}/fields/time_origin"


def _endpoint(id: str) -> tuple[list[Descriptor], str]:
    return base(ep_returned=mapped(id)), f"/{AT['ep:returned']}/fields/maps_to/concept"


def _endpoint_origin(id: str) -> tuple[list[Descriptor], str]:
    return base(ep_returned={"time_origin": id}), f"/{AT['ep:returned']}/fields/time_origin"


_SCOPE = f"/{AT['cov:loans.member_id']}/fields/parent_scope"


def _value_leaf(id: str) -> tuple[list[Descriptor], str]:
    leaf = {"kind": "value", "column": id, "values": ["x"]}
    return base(cov_loans__member_id=scoped(leaf)), f"{_SCOPE}/column"


def _exists_leaf(id: str) -> tuple[list[Descriptor], str]:
    leaf = {"kind": "exists", "table": id}
    return base(cov_loans__member_id=scoped(leaf)), f"{_SCOPE}/table"


def _covered_leaf(id: str) -> tuple[list[Descriptor], str]:
    leaf = {"kind": "covered", "table": id}
    return base(cov_loans__member_id=scoped(leaf)), f"{_SCOPE}/table"


SITES: dict[tuple[str, str, str], tuple[str, Place, tuple[str, str]]] = {
    ("column", "ColumnFields", "maps_to"): ("value", _column, ("references", "value")),
    ("table", "TableFields", "maps_to"): ("table", _table, ("references", "table")),
    ("table", "TableFields", "time_origin"): (
        "time_origin",
        _table_origin,
        ("references", "time_origin"),
    ),
    ("endpoint", "EndpointFields", "maps_to"): ("endpoint", _endpoint, ("_endpoint", "endpoint")),
    ("endpoint", "EndpointFields", "time_origin"): (
        "time_origin",
        _endpoint_origin,
        ("_endpoint", "time_origin"),
    ),
    ("coverage", "ValueLeaf", "column"): ("value", _value_leaf, ("_named", "value")),
    ("coverage", "ExistsLeaf", "table"): ("table", _exists_leaf, ("_named", "table")),
    ("coverage", "CoveredLeaf", "table"): ("table", _covered_leaf, ("_named", "table")),
}
"""Each place a release descriptor names a concept, by (kind, the ``$def`` that holds it, its
property): the sort it needs, a release naming a concept there and its path, and the call of
``_Checker.concept`` that checks it (the method, the sort)."""


def codes(found: list[Any]) -> list[tuple[str, str | None]]:
    return [(str(refusal.code), refusal.path) for refusal in found]


def listed(refusal: Any) -> list[str]:
    return [segment.model_dump()["data"] for segment in refusal.alternatives]


def message(refusal: Any) -> str:
    return "".join(
        segment.model_dump().get("text") or segment.model_dump()["data"]
        for segment in refusal.message
    )


# --- The places: the schema's, the checker's, and this table's -----------------------------------


_SKIPPED = frozenset({"$ref", "pattern", "const", "enum", "default", "description"})


def _places_of(kind: str, defs: dict[str, Any], root: str) -> set[tuple[str, str, str]]:
    """The places of ``kind``, walked from its descriptor's ``$def`` ``root``, each ``$def``
    once: (node, the ``$def`` that holds it, the property it is under)."""
    found: set[tuple[str, str, str]] = set()
    seen = {root}
    pending: list[tuple[Any, str, str | None]] = [(defs[root], root, None)]
    while pending:
        node, owner, member = pending.pop()
        if isinstance(node, list):
            pending.extend((item, owner, member) for item in node)  # pyright: ignore[reportUnknownVariableType]
            continue
        if not isinstance(node, dict):
            continue
        reference = node.get("$ref")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(reference, str):
            target = reference.rsplit("/", 1)[-1]
            if target == "ConceptMapping":
                assert member is not None
                found.add((kind, owner, member))
            elif target not in seen:
                seen.add(target)
                pending.append((defs[target], target, None))
        if node.get("pattern") == CONCEPT_NAMESPACE_SCHEMA:  # pyright: ignore[reportUnknownMemberType]
            assert member is not None
            found.add((kind, owner, member))
        for key, value in node.items():  # pyright: ignore[reportUnknownVariableType]
            if key == "properties":
                pending.extend((inner, owner, name) for name, inner in value.items())  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            elif key not in _SKIPPED:
                pending.append((value, owner, member))
    return found


def _schema_places() -> set[tuple[str, str, str]]:
    """Every (kind, ``$def``, property) of a release kind whose value is a concept id (a string
    the schema refuses in a namespace that is neither ``core`` nor a pack's) or a
    ``ConceptMapping``, walked from each kind's descriptor through every ``$ref``. Opaque JSON
    (extensions, ``inferred``) has none, and is outside the check."""
    defs = cast(dict[str, Any], descriptor_schema()["$defs"])
    kinds = {
        definition["properties"]["kind"]["const"]: name
        for name, definition in defs.items()
        if name.endswith("Descriptor")
    }
    return {place for kind in RELEASE_KINDS for place in _places_of(kind, defs, kinds[kind])}


def test_every_place_the_schema_names_a_concept_has_a_row_and_no_row_is_stale() -> None:
    """A new concept field with no row (and so, perhaps, no check) fails here (D391)."""
    assert _schema_places() == set(SITES)


def test_each_call_of_the_checker_s_concept_check_has_a_row_and_each_row_a_call() -> None:
    """A new ``self.concept(`` call with no fixture fails here, and so does a row whose call is
    gone: the calls, by method and sort, are exactly the rows'."""
    tree = ast.parse(inspect.getsource(release_module))
    calls: set[tuple[str, str]] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "concept"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                sort = node.args[2]
                assert isinstance(sort, ast.Constant)
                assert isinstance(sort.value, str)
                calls.add((function.name, sort.value))
    assert calls == {call for _, _, call in SITES.values()}


@pytest.mark.parametrize("place", sorted(_schema_places()))
def test_a_typo_is_refused_at_every_place_the_schema_names_a_concept(
    place: tuple[str, str, str],
) -> None:
    sort, at, _ = SITES[place]
    written, path = at(RIGHT[sort] + "x")
    assert codes(check_writes(written, SHELF)) == [("UNKNOWN_DESCRIPTOR", path)]


# --- Each place --------------------------------------------------------------------------------

PLACES = sorted(SITES)


@pytest.mark.parametrize("place", PLACES)
def test_the_right_concept_is_accepted(place: tuple[str, str, str]) -> None:
    sort, at, _ = SITES[place]
    written, _ = at(RIGHT[sort])
    assert check_writes(written, SHELF) == []
    assert check_release(written) == []


@pytest.mark.parametrize("place", PLACES)
def test_an_unregistered_concept_is_refused_listing_the_ids_of_the_sort_needed(
    place: tuple[str, str, str],
) -> None:
    sort, at, _ = SITES[place]
    for given in (RIGHT[sort] + "x", "other:thing", "ncit:c123"):
        written, path = at(given)
        [refusal] = check_writes(written, SHELF)
        assert (refusal.code, refusal.path) == ("UNKNOWN_DESCRIPTOR", path)
        assert message(refusal) == f"No installed pack registers the concept {given}"
        assert listed(refusal) == ids(sort)
        assert check_release(written) == []  # check_release checks core: concepts alone


@pytest.mark.parametrize("place", PLACES)
def test_a_concept_of_another_sort_is_refused_listing_the_ids_of_the_sort_needed(
    place: tuple[str, str, str],
) -> None:
    sort, at, _ = SITES[place]
    written, path = at(RIGHT[WRONG[sort]])
    [refusal] = check_writes(written, SHELF)
    assert (refusal.code, refusal.path) == ("INVALID_VALUE", path)
    assert message(refusal) == (
        f"Expected a concept of sort {sort}, not {WRONG[sort]}: {RIGHT[WRONG[sort]]}"
    )
    assert listed(refusal) == ids(sort)
    assert RIGHT[WRONG[sort]] not in listed(refusal)


@pytest.mark.parametrize("place", PLACES)
def test_no_registry_and_an_empty_one_register_no_pack_concept(place: tuple[str, str, str]) -> None:
    """A registry of ``None`` registers nothing, so every pack concept is refused, never left
    unchecked (D391); the refusal lists the core's ids of the sort."""
    sort, at, _ = SITES[place]
    written, path = at(RIGHT[sort])
    for packs in (None, NONE):
        [refusal] = check_writes(written, packs)
        assert (refusal.code, refusal.path) == ("UNKNOWN_DESCRIPTOR", path)
        assert listed(refusal) == ids(sort, None)


@pytest.mark.parametrize("place", PLACES)
def test_core_concepts_are_check_release_s_alone(place: tuple[str, str, str]) -> None:
    """A ``core:`` concept is refused once, by ``check_release``, listing the core's ids of the
    sort needed; the write check leaves it alone."""
    sort, at, _ = SITES[place]
    written, path = at("core:nothing")
    assert check_writes(written, SHELF) == []
    [refusal] = check_release(written)
    assert (refusal.code, refusal.path) == ("UNKNOWN_DESCRIPTOR", path)
    assert listed(refusal) == ids(sort, None)


def test_a_core_refusal_with_no_core_concept_of_the_sort_says_so() -> None:
    written, path = _endpoint("core:origin.birth")
    [refusal] = check_release(written)
    assert (refusal.code, refusal.path) == ("INVALID_VALUE", path)
    assert message(refusal) == (
        "Expected a concept of sort endpoint, not time_origin: core:origin.birth; "
        "the core has no concept of sort endpoint"
    )
    assert listed(refusal) == []


def test_a_pack_refusal_with_no_concept_of_the_sort_says_so() -> None:
    written, path = _endpoint("shelf:mark")
    for packs in (None, NONE, registry(("shelf:mark", "value"))):
        [refusal] = check_writes(written, packs)
        assert refusal.path == path
        assert message(refusal).endswith(
            "; the core has no concept of sort endpoint, and no installed pack registers one"
        )
        assert listed(refusal) == []


# --- Nested clauses ------------------------------------------------------------------------------


def test_every_clause_of_a_parent_scope_is_checked_at_its_path() -> None:
    typo = {"kind": "value", "column": "shelf:mrak", "values": ["x"]}
    good = {"kind": "value", "column": "shelf:mark", "values": ["x"]}
    scope = {
        "all": [
            {"any": [good, typo]},
            {"not": typo},
            {"known": typo},
            {"unknown": typo},
            {"kind": "exists", "table": "loans", "where": [good, typo]},
            {"kind": "exists", "table": "shelf:mark"},
        ]
    }
    written = base(cov_loans__member_id=scoped(scope))
    here = f"{_SCOPE}/all"
    assert codes(check_writes(written, SHELF)) == [
        ("UNKNOWN_DESCRIPTOR", f"{here}/0/any/1/column"),
        ("UNKNOWN_DESCRIPTOR", f"{here}/1/not/column"),
        ("UNKNOWN_DESCRIPTOR", f"{here}/2/known/column"),
        ("UNKNOWN_DESCRIPTOR", f"{here}/3/unknown/column"),
        ("UNKNOWN_DESCRIPTOR", f"{here}/4/where/1/column"),
        ("INVALID_VALUE", f"{here}/5/table"),
    ]
    assert check_release(written) == []


# --- The listing ---------------------------------------------------------------------------------

MANY = registry(*((f"shelf:v{index:02d}", "value") for index in range(40)))


def test_a_refusal_lists_the_nearest_ids_with_their_count() -> None:
    known = ids("value", MANY)
    assert len(known) == 42  # the core's two and the pack's forty
    for given, window in (
        ("shelf:v20x", nearest("shelf:v20x", known)),
        ("aaa:x", known[:NEAREST]),
        ("zzz:x", known[-NEAREST:]),
    ):
        [refusal] = check_writes(_column(given)[0], MANY)
        assert listed(refusal) == list(window)
        assert message(refusal) == (
            f"No installed pack registers the concept {given}; the core and the installed "
            f"packs have 42 concepts of sort value, and the {NEAREST} nearest are listed"
        )
    assert listed(check_writes(_column("shelf:v20x")[0], MANY)[0]) == known[15:31]


def test_a_refusal_lists_every_id_of_the_sort_when_they_are_few() -> None:
    [refusal] = check_writes(_table("shelf:shelve")[0], SHELF)
    assert listed(refusal) == ["core:person", "shelf:shelf"]
    assert message(refusal) == "No installed pack registers the concept shelf:shelve"


# --- What the registry hands the check -----------------------------------------------------------


def test_the_registry_s_views_are_built_once_and_read_only() -> None:
    assert SHELF.concept_sorts() is SHELF.concept_sorts()
    assert isinstance(SHELF.concept_sorts(), MappingProxyType)
    assert dict(SHELF.concept_sorts()) == {id: sort for sort, id in RIGHT.items()}
    for sort in RIGHT:
        assert SHELF.concept_ids(sort) is SHELF.concept_ids(sort)
        assert isinstance(SHELF.concept_ids(sort), tuple)
    assert SHELF.concept_ids("value") == ("core:age_years", "core:sex", "shelf:mark")
    assert SHELF.concept_ids("endpoint") == ("shelf:loan.returned",)
    assert SHELF.concept_ids("nothing") == ()
    assert NONE.concept_sorts() == {}
    assert NONE.concept_ids("time_origin") == CORE_IDS["time_origin"]


def _out_of_order(chooser: random.Random) -> PackRegistry:
    """Two packs given in reverse, each registering its concepts of every sort in a shuffled
    order."""
    packs: list[Pack] = []
    for pack_id in ("shelf", "birds"):
        given = [
            concept(f"{pack_id}:{sort}.{name}", sort)
            for sort in ("value", "table", "endpoint", "time_origin")
            for name in chooser.sample("abcdefghijklmnopqrstuvwxyz", chooser.randrange(1, 12))
        ]
        chooser.shuffle(given)
        manifest = PackManifest(id=pack_id, version="1.0.0", results_version=1, requires_core=">=0")
        packs.append(Pack(manifest=manifest, concepts=given))
    return PackRegistry(packs, core_version=aibi.__version__)


def test_the_listing_of_each_sort_is_sorted_whatever_the_order_of_registration() -> None:
    """The listing is what ``nearest`` bisects (D391): sorted, of the core's ids and of every
    pack's, however they were registered; and the core's are pinned here by their text, not by
    the code that makes them."""
    assert CORE_IDS["time_origin"] == (
        "core:origin.birth",
        "core:origin.calendar",
        "core:origin.entry",
    )
    chooser = random.Random(391)
    for _ in range(25):
        packs = _out_of_order(chooser)
        for sort in (*RIGHT, "nothing"):
            listing = packs.concept_ids(sort)
            assert list(listing) == sorted(listing)
            assert set(CORE_IDS.get(sort, ())) <= set(listing)
            assert len(set(listing)) == len(listing)
            assert list(core_ids(sort)) == sorted(core_ids(sort))
    for listing in CORE_IDS.values():
        assert list(listing) == sorted(listing)


def test_a_record_holds_the_registry_s_listing_and_never_a_copy() -> None:
    """A million refusals hold a million listings: each holds the one shared tuple of its sort
    (D391), the registry's for a pack concept and the core's for a ``core:`` one."""
    for sort, place, _ in SITES.values():
        written, _ = place("shelf:typo")
        records = pack_concepts(written, SHELF.concept_sorts(), SHELF.concept_ids)
        assert records
        for record in records:
            assert record[3][3] is SHELF.concept_ids(sort)
        written, _ = place("core:typo")
        checker = release_module._Checker(written)  # pyright: ignore[reportPrivateUsage]
        for index, descriptor in enumerate(written):
            checker.references(index, descriptor)
        assert checker.records
        for record in checker.records:
            assert record[3][3] is core_ids(sort)


def test_a_write_reads_no_concept(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check reads the registry's views, built at registration, and no concept: neither the
    registry's copies nor ``concepts()``."""
    reads: list[str] = []
    real = ConceptDescriptor.__getattribute__

    def reading(self: Any, name: str) -> Any:
        reads.append(name)
        return real(self, name)

    def refused(self: Any) -> Any:
        raise AssertionError("concepts() was called")

    monkeypatch.setattr(PackRegistry, "concepts", refused)
    monkeypatch.setattr(ConceptDescriptor, "__getattribute__", reading)
    for sort, at, _ in SITES.values():
        for given in (RIGHT[sort], RIGHT[sort] + "x", RIGHT[WRONG[sort]]):
            check_writes(at(given)[0], SHELF)
    assert reads == []


# --- What is and is not checked ------------------------------------------------------------------


def test_a_dataset_listing_no_pack_may_name_a_pack_s_concept() -> None:
    """A concept is a shared term, not a schema a dataset opts into, as an ontology system is
    and unlike an extension (D391)."""
    for packs in ([], ["shelf"]):
        written = [build.dataset(packs=packs), *_column("shelf:mark")[0][1:]]
        assert check_writes(written, SHELF) == []


def test_value_map_targets_and_units_are_not_checked_against_the_concept() -> None:
    """D391's stated limit, as for ``core:`` concepts (§5.7)."""
    value_map = {"concept": "shelf:mark", "transform": {"value_map": {"x": "not-a-mark"}}}
    units = {"concept": "shelf:mark", "transform": {"unit_from": "d", "unit_to": "a"}}
    for mapping in (value_map, units):
        assert check_writes(base(members__grade={"maps_to": mapping}), SHELF) == []


def test_the_pass_returns_no_refusal_of_the_other_rules() -> None:
    """Dangling references, a missing ``core:`` concept and every other rule are
    ``check_release``'s: publish runs only the write checks, and a proposal is not refused
    twice."""
    dangling = base(
        members__grade={"derived": {"op": "value_map", "input": "ghost", "map": {"a": "b"}}},
        ep_returned={"table": "ghost", "time_origin": "core:nothing"},
    )
    assert codes(check_release(dangling)) != []
    assert check_writes(dangling, SHELF) == []
    assert pack_concepts(dangling, SHELF.concept_sorts(), SHELF.concept_ids) == []


# --- Only what a change or a proposal touches ----------------------------------------------------


def test_only_the_descriptors_a_write_touches_are_checked() -> None:
    written, path = _column("shelf:gone")
    assert codes(check_writes(written, SHELF)) == [("UNKNOWN_DESCRIPTOR", path)]
    assert check_writes(written, SHELF, only={"members", "loans.days"}) == []
    assert codes(check_writes(written, SHELF, only={"members.grade"})) == [
        ("UNKNOWN_DESCRIPTOR", path)
    ]
    assert check_writes(written, SHELF, only=set()) == []


def test_a_touched_descriptor_is_checked_whole_not_by_member() -> None:
    """A change to a descriptor's label is refused for the stale concept it names (D391)."""
    written, path = _column("shelf:gone")
    dumped = written[AT["members.grade"]].model_dump(mode="json")
    dumped["label"] = "Grade"
    written[AT["members.grade"]] = _ADAPTER.validate_python(dumped)
    assert codes(check_writes(written, SHELF, only={"members.grade"})) == [
        ("UNKNOWN_DESCRIPTOR", path)
    ]


def test_paths_point_at_the_descriptor_s_place_in_the_release_not_among_those_checked() -> None:
    written, _ = _endpoint_origin("shelf:gone")
    found = check_writes(written, SHELF, only={"ep:returned"})
    assert codes(found) == [("UNKNOWN_DESCRIPTOR", f"/{AT['ep:returned']}/fields/time_origin")]
    assert codes(by_id(found, written, "draft")) == [
        ("UNKNOWN_DESCRIPTOR", "/draft/ep:returned/fields/time_origin")
    ]


# --- Many refused references: one count, built lazily, bounded ------------------------------------


def _stale(count: int, concept: str = "shelf:gone", **dataset: Any) -> list[Descriptor]:
    """A release of ``count`` columns, each mapped to ``concept``."""
    column = build.column("members.c", "string", maps_to={"concept": concept, "transform": None})
    return [
        build.dataset(**dataset),
        build.table("members"),
        *(column.model_copy(update={"id": f"members.c{index:07d}"}) for index in range(count)),
    ]


def test_many_stale_concepts_and_an_extension_give_one_exact_count_through_by_id() -> None:
    """What the operator receives (``by_id`` of ``check_writes``, as every caller applies it):
    the first ``MAX_REFUSALS`` by path, then one ``LIMIT_EXCEEDED``, last, with the exact
    remainder, the extension refusal counted among them."""
    written = _stale(MAX_REFUSALS + 500)
    dumped = written[0].model_dump(mode="json")
    dumped["extensions"] = {"zz": {"a": 1}}
    dumped["curation"]["/extensions/zz/a"] = dumped["curation"]["/label"]
    written[0] = _ADAPTER.validate_python(dumped)
    found = check_writes(written, SHELF)
    received = by_id(found, written, "draft")
    for given in (found, received):
        assert len(given) == MAX_REFUSALS + 1
        assert [str(r.code) for r in given].count("LIMIT_EXCEEDED") == 1
        assert given[-1].path is None
        assert message(given[-1]) == "501 more refusals were left out"
    assert received[0].path == "/draft/dataset/extensions/zz"
    assert received[1].path == "/draft/members.c0000000/fields/maps_to/concept"


def test_check_writes_builds_only_the_refusals_it_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refusals and segments are counted: a check that built every refusal, or every listing
    when it recorded a reference, would build three times as many as it returns. The columns
    are mapped to a concept no pack registers and half of them carry an extension of a pack
    neither registered nor listed, and the dataset, last (its paths sort after the ones
    returned), lists unregistered packs: so that the refusals ``check_writes`` itself makes,
    as well as the concepts', are built only if returned (the ``million`` job's test, small)."""
    stale = _stale(3 * MAX_REFUSALS)
    written = stale[1:]  # the table, then the columns
    extended = range(0, len(written), 2)
    for index in extended:
        dumped = written[index].model_dump(mode="json")
        dumped["extensions"] = {"zz": {"a": 1}}
        dumped["curation"]["/extensions/zz/a"] = dumped["curation"]["/label"]
        written[index] = _ADAPTER.validate_python(dumped)
    written.append(build.dataset(packs=[f"pack{index}" for index in range(16)]))
    counts = {"refusals": 0, "segments": 0}

    def counted(model: Any, name: str) -> None:
        real = model.__init__

        def counting(this: Any, *args: Any, **kwargs: Any) -> None:
            counts[name] += 1
            real(this, *args, **kwargs)

        monkeypatch.setattr(model, "__init__", counting)

    counted(Refusal, "refusals")
    counted(TextSegment, "segments")
    counted(DataSegment, "segments")
    found = check_writes(written, MANY)
    assert len(found) == MAX_REFUSALS + 1
    assert counts["refusals"] == MAX_REFUSALS + 1
    assert counts["segments"] <= 20 * (MAX_REFUSALS + 1)
    unregistered = [r for r in found if str(r.code) == "UNKNOWN_DESCRIPTOR"]
    assert unregistered
    assert all(len(r.alternatives) == NEAREST for r in unregistered)
    kinds = {str(refusal.code) for refusal in found}
    assert {"UNKNOWN_DESCRIPTOR", "INVALID_EXTENSION", "LIMIT_EXCEEDED"} <= kinds
    assert not any(refusal.path and "/packs/" in refusal.path for refusal in found)
    # The dataset's refusals are all among those left out, none built.
    every = 3 * MAX_REFUSALS + len(extended) + 16  # the columns' concepts, extensions, packs
    assert message(found[-1]) == f"{every - MAX_REFUSALS} more refusals were left out"


def test_many_stale_references_are_refused_in_bounded_time() -> None:
    written = _stale(50_000)
    started = time.perf_counter()
    found = check_writes(written, MANY)
    elapsed = time.perf_counter() - started
    assert len(found) == MAX_REFUSALS + 1
    assert elapsed < 10, elapsed


# --- What a write quoted is data, and the core's words are text ----------------------------------

Segments = list[tuple[str, str]]


def words(core: str) -> tuple[str, str]:
    return ("text", core)


def quote(quoted: str) -> tuple[str, str]:
    return ("data", quoted)


def segments(found: list[Any]) -> Segments:
    """Each segment as ``(its type, its string)``."""
    return [
        ("text", segment.text) if isinstance(segment, TextSegment) else ("data", segment.data)
        for segment in found
    ]


P_UNREG, P_STRAY, P_REG, P_FAIL, P_STEPS = (
    "pkunregzq",
    "pkstrayzq",
    "pkregzq",
    "pkfailzq",
    "pkstepszq",
)
"""Pack ids: listed by the dataset and not registered; of an extension, neither registered nor
listed; registered, with the concepts and a schema; registered, with an ontology validator that
raises; registered, with a schema for tables that the steps run out on."""
S_BAD, S_RAISE = "sysbadzq", "sysraisezq"
"""Ontology systems: one whose validator rejects the codes, one that raises."""
G_VALUE, G_ORIGIN, G_ENDPOINT, G_LEAF = (
    "nszq:valuezq",
    "nszq:originzq",
    "nszq:endpointzq",
    "nszq:leafzq",
)
"""Concepts no installed pack registers."""
G_CORE = "core:nozq"
"""A ``core:`` concept the core does not have."""
VALUE = "valzq"
"""What an extension holds, which a refusal never repeats (A6)."""
CEILING = 100
"""The steps an extension may take, so that a small schema runs out of them."""


def _extended(descriptor: Descriptor, extensions: Mapping[str, Mapping[str, Any]]) -> Descriptor:
    dumped = descriptor.model_dump(mode="json")
    dumped["extensions"] = {pack: dict(members) for pack, members in extensions.items()}
    for pack, members in extensions.items():
        for member in members:
            dumped["curation"][f"/extensions/{pack}/{member}"] = dumped["curation"]["/label"]
    return _ADAPTER.validate_python(dumped)


def _quoting_registry() -> PackRegistry:
    def pack(id: str, **given: Any) -> Pack:
        manifest = PackManifest(id=id, version="1.0.0", results_version=1, requires_core=">=0")
        return Pack(manifest=manifest, **given)

    def raises(code: str) -> bool:
        raise ValueError(code)

    schema = {
        "type": "object",
        "properties": {"mark": {"type": "string", "enum": ["A", "B"]}},
        "additionalProperties": False,
    }
    exhausting = {"required": [f"m{index}" for index in range(CEILING)]}
    packs = [
        pack(
            P_REG,
            extension_schemas={"dataset": schema},
            ontology_systems={S_BAD: lambda code: False},
            concepts=[concept(f"{P_REG}:{sort}zq", sort) for sort in RIGHT],
        ),
        pack(P_FAIL, ontology_systems={S_RAISE: raises}),
        pack(P_STEPS, extension_schemas={"table": exhausting}),
    ]
    return PackRegistry(packs, core_version=aibi.__version__)


QUOTING = _quoting_registry()


def _quoting_release() -> list[Descriptor]:
    """A release that names a distinct sentinel at each place a refusal quotes what a write
    named: a pack id, a concept id, an ontology system, a table, column, relationship or id."""
    concepts = [
        {"system": S_BAD, "code": "c1", "label": "One", "relation": "exact"},
        {"system": S_RAISE, "code": "c2", "label": "Two", "relation": "exact"},
    ]
    leaf = {"kind": "value", "column": G_LEAF, "values": ["x"]}
    wrong_sort = f"{P_REG}:endpointzq"
    written = base(
        dataset={"packs": [P_UNREG, P_REG, P_STEPS]},
        members={**mapped(wrong_sort), "time_origin": G_ORIGIN},
        members__grade={**mapped(G_VALUE), "concepts": concepts},
        loans=mapped("core:origin.birth"),
        loans__returned=mapped(G_CORE),
        ep_returned=mapped(G_ENDPOINT),
        cov_loans__member_id=scoped(leaf),
    )
    at = {descriptor.id: index for index, descriptor in enumerate(written)}
    written[at["dataset"]] = _extended(written[at["dataset"]], {P_REG: {"mark": VALUE}})
    written[at["members.grade"]] = _extended(
        written[at["members.grade"]], {P_REG: {"x": VALUE}, P_STRAY: {"x": VALUE}}
    )
    written[at["loans"]] = _extended(written[at["loans"]], {P_STEPS: {"mark": VALUE}})
    derived = {"op": "value_map", "input": "missingzq", "map": {"a": "b"}}
    return [
        *written,
        concept("nszq:kindzq", "value"),
        build.table("dupzq"),
        build.table("dupzq"),
        build.column("ghostzq.c", "string"),
        build.column("members.d", "category", derived=derived),
        build.coverage("rel:loans.ghostzq"),
        build.relationship("loans", ["loan_id"], "members", ["grade"]),
    ]


QUOTING_RELEASE = _quoting_release()
WHERE = {descriptor.id: index for index, descriptor in enumerate(QUOTING_RELEASE)}


class Row(NamedTuple):
    """A refusal a write is expected to get: the check that makes it, its code and path, and its
    message and alternatives as ``(type, string)`` pairs."""

    source: str
    code: str
    path: str | None
    message: Segments
    alternatives: Segments


def _listing(sort: str, packs: PackRegistry | None) -> Segments:
    return [quote(id_) for id_ in ids(sort, packs)]


def _rows() -> dict[str, Row]:
    first = [descriptor.id for descriptor in QUOTING_RELEASE].index("dupzq")
    at, here = WHERE, "/{}/".format
    grade, members, loans = (
        here(WHERE["members.grade"]),
        here(WHERE["members"]),
        here(WHERE["loans"]),
    )
    no_pack = "No installed pack registers the concept "
    not_of = "Expected a concept of sort {}, not {}: ".format
    steps_text = (
        "Evaluating the extension against its pack's schema takes more than its "
        f"{CEILING} steps ({STEPS_BASE} and {STEPS_PER_VALUE} per JSON value of the extension, at "
        f"most {CEILING}): a smaller extension object, or a pack schema that evaluates it in "
        "fewer steps, or an operator's curation session, which allows more"
    )
    return {
        "a dataset lists a pack that is not registered": Row(
            "writes",
            "INVALID_VALUE",
            f"/{at['dataset']}/fields/packs/0",
            [words("The dataset lists a pack that is not registered: "), quote(P_UNREG)],
            [],
        ),
        "an extension of a pack neither registered nor listed": Row(
            "writes",
            "INVALID_EXTENSION",
            f"{grade}extensions/{P_STRAY}",
            [words("No registered pack has the extension's pack id: "), quote(P_STRAY)],
            [],
        ),
        "an extension of a pack that has no schema for the kind": Row(
            "writes",
            "INVALID_EXTENSION",
            f"{grade}extensions/{P_REG}",
            [words("The pack has no extension schema for column descriptors")],
            [],
        ),
        "an extension that breaks its pack's schema": Row(
            "writes",
            "INVALID_EXTENSION",
            f"/{at['dataset']}/extensions/{P_REG}/mark",
            [words("The extension does not match its pack's schema (enum)")],
            [],
        ),
        "an extension the steps run out on": Row(
            "writes",
            "LIMIT_EXCEEDED",
            f"{loans}extensions/{P_STEPS}",
            [words(steps_text)],
            [],
        ),
        "an ontology code its system's validator rejects": Row(
            "writes",
            "INVALID_VALUE",
            f"{grade}fields/concepts/0/code",
            [words("The ontology system "), quote(S_BAD), words(" has no such code")],
            [],
        ),
        "an ontology validator that raised": Row(
            "writes",
            "PACK_FAILED",
            f"{grade}fields/concepts/1/code",
            [words("The ontology validator of the pack "), quote(P_FAIL), words(" failed")],
            [],
        ),
        "a validator that raised": Row(
            "failed",
            "PACK_FAILED",
            None,
            [words("The validator of the pack "), quote(P_FAIL), words(" failed")],
            [],
        ),
        "a pack's concept of another sort": Row(
            "writes",
            "INVALID_VALUE",
            f"{members}fields/maps_to/concept",
            [words(not_of("table", "endpoint")), quote(f"{P_REG}:endpointzq")],
            _listing("table", QUOTING),
        ),
        "a pack's concept no pack registers (a column)": Row(
            "writes",
            "UNKNOWN_DESCRIPTOR",
            f"{grade}fields/maps_to/concept",
            [words(no_pack), quote(G_VALUE)],
            _listing("value", QUOTING),
        ),
        "a pack's concept no pack registers (a time origin)": Row(
            "writes",
            "UNKNOWN_DESCRIPTOR",
            f"{members}fields/time_origin",
            [words(no_pack), quote(G_ORIGIN)],
            _listing("time_origin", QUOTING),
        ),
        "a pack's concept no pack registers (an endpoint)": Row(
            "writes",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['ep:returned']}/fields/maps_to/concept",
            [words(no_pack), quote(G_ENDPOINT)],
            _listing("endpoint", QUOTING),
        ),
        "a pack's concept no pack registers (a parent scope)": Row(
            "writes",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['cov:loans.member_id']}/fields/parent_scope/column",
            [words(no_pack), quote(G_LEAF)],
            _listing("value", QUOTING),
        ),
        "a core concept of another sort": Row(
            "release",
            "INVALID_VALUE",
            f"{loans}fields/maps_to/concept",
            [words(not_of("table", "time_origin")), quote("core:origin.birth")],
            _listing("table", None),
        ),
        "a core concept the core lacks": Row(
            "release",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['loans.returned']}/fields/maps_to/concept",
            [words("The core has no concept "), quote(G_CORE)],
            _listing("value", None),
        ),
        "a descriptor of a kind no release holds": Row(
            "release",
            "INVALID_VALUE",
            f"/{at['nszq:kindzq']}/kind",
            [words("A release holds no concept descriptors")],
            [words(kind) for kind in RELEASE_KINDS],
        ),
        "an id used twice": Row(
            "release",
            "DUPLICATE_ENTRY",
            f"/{at['dupzq']}/id",
            [words(f"The id is already used by descriptor {first}: "), quote("dupzq")],
            [],
        ),
        "a table the release lacks": Row(
            "release",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['ghostzq.c']}/id",
            [words("The release has no table "), quote("ghostzq")],
            [],
        ),
        "a column the release lacks": Row(
            "release",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['members.d']}/fields/derived/input",
            [words("The release has no column "), quote("members.missingzq")],
            [],
        ),
        "a relationship the release lacks": Row(
            "release",
            "UNKNOWN_DESCRIPTOR",
            f"/{at['cov:loans.ghostzq']}/fields/relationship",
            [words("The release has no relationship "), quote("rel:loans.ghostzq")],
            [],
        ),
        "a relationship that leads to no key": Row(
            "release",
            "INVALID_VALUE",
            f"/{at['rel:loans.loan_id']}/fields/parent_columns",
            [
                words("A relationship leads to the key of "),
                quote("members"),
                words(", in any order: "),
                quote("member_id"),
            ],
            [],
        ),
        "an extension of a pack the dataset does not list": Row(
            "release",
            "INVALID_VALUE",
            f"{grade}extensions/{P_STRAY}",
            [words("Extensions of a pack the dataset does not list in packs: "), quote(P_STRAY)],
            [],
        ),
    }


ROWS = _rows()


@functools.cache
def _quoted() -> dict[tuple[str, str, str | None], Refusal]:
    """Every refusal the release's checks make, by (check, code, path), with the validator that
    raised on a refusal of its own, as the checks build and return them."""
    made = {
        "writes": check_writes(QUOTING_RELEASE, QUOTING, ceiling=CEILING),
        "release": check_release(QUOTING_RELEASE),
        "failed": [failed(P_FAIL, "validator")],
    }
    return {
        (source, str(refusal.code), refusal.path): refusal
        for source, found in made.items()
        for refusal in found
    }


@pytest.mark.parametrize("name", sorted(ROWS))
def test_a_refusal_quotes_what_the_write_named_as_data_and_says_its_own_words_as_text(
    name: str,
) -> None:
    """Each refusal of every kind ``check_writes`` and ``check_release`` make (a pack id, a
    concept id, an ontology system, a table, a column, a relationship, an id) holds the core's
    words as text and what the write named as data, alternating, and its alternatives as the
    core makes them: data for concept ids, text for the kinds of a release."""
    row = ROWS[name]
    refusal = _quoted()[(row.source, row.code, row.path)]
    assert segments(refusal.message) == row.message
    assert segments(refusal.alternatives) == row.alternatives


def test_no_sentinel_is_text_each_is_data_and_no_refusal_goes_unchecked() -> None:
    """Every sentinel contains ``zq``, which no word of the core does: none is in a text segment,
    each is in a data segment of some refusal, what an extension holds in none, and the refusals
    are exactly the rows'."""
    made = _quoted()
    assert set(made) == {(row.source, row.code, row.path) for row in ROWS.values()}
    every = [
        segment
        for refusal in made.values()
        for segment in [*refusal.message, *refusal.alternatives]
    ]
    assert not [s for s in segments(every) if s[0] == "text" and "zq" in s[1]]
    assert not [s for s in segments(every) if VALUE in s[1]]
    quoted = {string for kind, string in segments(every) if kind == "data"}
    registered = {id_ for sort in RIGHT for id_ in QUOTING.concept_ids(sort) if P_REG in id_}
    assert len(registered) == len(RIGHT)
    named = {P_UNREG, P_STRAY, P_FAIL, S_BAD, G_VALUE, G_ORIGIN, G_ENDPOINT, G_LEAF, G_CORE}
    named |= {"dupzq", "ghostzq", "members.missingzq", "rel:loans.ghostzq"}
    for sentinel in sorted(registered | named):
        assert sentinel in quoted, sentinel
    assert VALUE not in "".join(refusal.model_dump_json() for refusal in made.values())


NO_ENDPOINT = "; the core has no concept of sort endpoint"
NEAREST_LISTED = (
    f"; the core and the installed packs have 42 concepts of sort value, and the {NEAREST}"
)
SUFFIXES = [
    (NONE, "endpoint", "shelf:mark", NO_ENDPOINT + ", and no installed pack registers one"),
    (MANY, "value", "shelf:v20x", NEAREST_LISTED + " nearest are listed"),
    (None, "endpoint", "core:origin.birth", NO_ENDPOINT),
]


@pytest.mark.parametrize(("packs", "sort", "given", "suffix"), SUFFIXES)
def test_the_count_a_refusal_ends_with_is_the_core_s_text(
    packs: PackRegistry | None, sort: str, given: str, suffix: str
) -> None:
    written, _ = (_endpoint if sort == "endpoint" else _column)(given)
    [refusal] = check_release(written) if packs is None else check_writes(written, packs)
    quoted = segments(refusal.message)
    assert [kind for kind, _ in quoted] == ["text", "data", "text"]
    assert quoted[1] == quote(given)
    assert quoted[2] == words(suffix)


# --- Every builder of a refusal is called for the refusals returned only --------------------------

P_BARE, P_SCHEMA = "pkbarezq", "pkschemazq"
"""Registered packs for the counting below: one with no schema, one with a schema of columns."""
BUILDS = 3 * MAX_REFUSALS
"""How many refusals of a kind a release makes, three times as many as a list holds."""


def _counting_registry() -> PackRegistry:
    def pack(id: str, **given: Any) -> Pack:
        manifest = PackManifest(id=id, version="1.0.0", results_version=1, requires_core=">=0")
        return Pack(manifest=manifest, **given)

    def raises(code: str) -> bool:
        raise ValueError(code)

    strings = {"type": "object", "properties": {"x": {"type": "string"}}}
    exhausting = {"required": [f"m{index}" for index in range(CEILING)]}
    return PackRegistry(
        [
            pack(P_BARE, ontology_systems={S_BAD: lambda code: False, S_RAISE: raises}),
            pack(P_SCHEMA, extension_schemas={"column": strings}),
            pack(P_STEPS, extension_schemas={"column": exhausting}),
        ],
        core_version=aibi.__version__,
    )


COUNTING = _counting_registry()


def _of_columns(count: int, **fields: Any) -> list[Descriptor]:
    """A release of ``count`` columns with ``fields``, none of its other descriptors refused."""
    column = build.column("members.c", "string", **fields)
    return [
        build.dataset(),
        build.table("members"),
        *(column.model_copy(update={"id": f"members.c{index:07d}"}) for index in range(count)),
    ]


def _with_extension(pack: str, member: str, value: Any) -> list[Descriptor]:
    column = _extended(build.column("members.c", "string"), {pack: {member: value}})
    return [
        build.dataset(),
        build.table("members"),
        *(column.model_copy(update={"id": f"members.c{index:07d}"}) for index in range(BUILDS)),
    ]


def _with_codes(system: str) -> list[Descriptor]:
    concepts = [{"system": system, "code": "c", "label": "One", "relation": "exact"}]
    return _of_columns(BUILDS, concepts=concepts)


def _packs_refused_last() -> list[Descriptor]:
    """The dataset's 16 refusals of unregistered packs (a dataset lists at most 16, so these are
    the nearest to ``BUILDS``), last, so that their paths sort after those returned, among the
    ``BUILDS`` refusals of concepts no pack registers."""
    stale = _of_columns(BUILDS, maps_to={"concept": G_VALUE, "transform": None})
    return [*stale[1:], build.dataset(packs=[f"pack{index}" for index in range(16)])]


def _not_of_releases() -> list[Descriptor]:
    kind = concept("nszq:kindzq", "value")
    return [
        build.dataset(),
        *(kind.model_copy(update={"id": f"nszq:kind{index:07d}"}) for index in range(BUILDS)),
    ]


def _derived_from_missing() -> list[Descriptor]:
    derived = {"op": "value_map", "input": "missingzq", "map": {"a": "b"}}
    return _of_columns(BUILDS, derived=derived)


Check = Callable[[list[Descriptor]], list[Refusal]]


def _writes(written: list[Descriptor]) -> list[Refusal]:
    return check_writes(written, COUNTING, ceiling=CEILING)


COUNTED: dict[str, tuple[Callable[[], list[Descriptor]], Check, int]] = {
    "said: an extension of a pack neither registered nor listed": (
        lambda: _with_extension(P_STRAY, "x", 1),
        _writes,
        BUILDS,
    ),
    "said: a pack the dataset lists that is not registered": (
        _packs_refused_last,
        _writes,
        BUILDS + 16,
    ),
    "said: an extension of a pack with no schema for the kind": (
        lambda: _with_extension(P_BARE, "x", 1),
        _writes,
        BUILDS,
    ),
    "said: an extension that breaks its pack's schema": (
        lambda: _with_extension(P_SCHEMA, "x", 1),
        _writes,
        BUILDS,
    ),
    "said: an ontology code its validator rejects": (lambda: _with_codes(S_BAD), _writes, BUILDS),
    "_failed: an ontology validator that raised": (lambda: _with_codes(S_RAISE), _writes, BUILDS),
    "_out_of_steps: an extension the steps run out on": (
        lambda: _with_extension(P_STEPS, "mark", 1),
        _writes,
        BUILDS,
    ),
    "_concept_refused: a pack concept no pack registers": (
        lambda: _of_columns(BUILDS, maps_to={"concept": G_VALUE, "transform": None}),
        _writes,
        BUILDS,
    ),
    "_concept_refused: a core concept the core lacks": (
        lambda: _of_columns(BUILDS, maps_to={"concept": G_CORE, "transform": None}),
        check_release,
        BUILDS,
    ),
    "_not_of_a_release: a descriptor of a kind no release holds": (
        _not_of_releases,
        check_release,
        BUILDS,
    ),
    "_Checker.say: a column a rule of the release needs": (
        _derived_from_missing,
        check_release,
        BUILDS,
    ),
}


@pytest.mark.parametrize("name", sorted(COUNTED))
def test_each_builder_of_a_refusal_is_called_for_the_refusals_returned_alone(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that makes ``BUILDS`` refusals of a kind builds ``MAX_REFUSALS`` and the one that
    counts the rest (D391): the builders ``check_writes`` and ``check_release`` record their
    refusals with (``said``, ``_failed``, ``_out_of_steps``, ``_concept_refused``,
    ``_not_of_a_release``, and ``_Checker.say``), each by a case of its own. A dataset lists at
    most 16 packs, so the refusal of an unregistered one is made 16 times, among ``BUILDS`` others
    that sort first."""
    make, check, total = COUNTED[name]
    written = make()
    made = 0
    real = Refusal.__init__

    def counting(this: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal made
        made += 1
        real(this, *args, **kwargs)

    monkeypatch.setattr(Refusal, "__init__", counting)
    found = check(written)
    assert len(found) == MAX_REFUSALS + 1
    assert made == MAX_REFUSALS + 1
    assert found[-1].path is None
    assert message(found[-1]) == f"{total - MAX_REFUSALS} more refusals were left out"
