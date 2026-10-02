"""The guard at the sites the catalogue's tools call a pack at, and the dynamic check of every site
(D303, D316, D343, D388).

- A facet, a translator and a requirement predicate, each against every kind of raise
  (``raisers``): a facet is left out, a translation refused (``PACK_FAILED``), a predicate does not
  hold, and the passed types pass as new instances.
- The facet twin: a facet that marks its view gives a dataset the same facets whether its entry is
  built alone or with the others'.
- The dynamic check: a test pack, ``watch``, whose every hook object, and the mappings it returns,
  record each read and whether ``Hook.call`` (or the importer's own guard) is on the stack, is
  driven through every path that calls a pack: an import and a re-import, a session's change and
  publish, the proposers, a proposal, the catalogue and its facets, a document translated, a
  cohort with a pack leaf and a caveat rule counted and explained, the analyses listed and
  applicable, and a pack's analysis run. Every read is under a guard, and what every ``call``
  returned is made of the core's objects alone (``provenance.core_made``).
"""

import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import JsonValue
from tests.core import provenance, raisers

import aibi
from aibi.core.catalog.service import Catalog
from aibi.core.engine.worker import Workers
from aibi.core.importers import checks
from aibi.core.importers.files import FileImporter
from aibi.core.importers.run import reimport_dataset
from aibi.core.schema import guards
from aibi.core.schema.catalog import AnalysisListing, CatalogHits
from aibi.core.schema.caveats import Severity
from aibi.core.schema.cohorts import AnalysisResults, CohortCounts, DocumentValidation
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import AnalysisDescriptor, DatasetDescriptor
from aibi.core.schema.document import PackLeaf
from aibi.core.schema.limits import ImportLimits, QueryLimits
from aibi.core.schema.output import Output, text
from aibi.core.schema.pack_api import (
    AnalysisInputs,
    ConfinedPath,
    ImportOptions,
    ImportResult,
    Pack,
    PackManifest,
    PackRegistry,
    Proposal,
    ReleaseView,
    TranslationNote,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.store import sessions

World = Any
Orchard = Callable[..., dict[str, bytes]]
ADA = "operator:Ada"
SYSTEM = "WATCH-CODES"
WORKERS = Workers(QueryLimits())
APPLE = {"kind": "value", "column": "trees.variety", "values": ["apple"]}


def _guarded_now() -> bool:
    """Whether a hook's guard is on the stack: ``Hook.call``'s, or the importer's own."""
    codes = (
        guards._contained.__code__,  # pyright: ignore[reportPrivateUsage]
        checks.run_importer.__code__,
    )
    frame = sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
    while frame is not None:
        if any(frame.f_code is code for code in codes):
            return True
        frame = frame.f_back
    return False


class _Log:
    def __init__(self) -> None:
        self.reads: list[tuple[str, bool]] = []

    def read(self, what: str) -> None:
        self.reads.append((what, _guarded_now()))


LOG = _Log()


class _Watched:
    """A hook object that records every attribute read, and whether a guard is on the stack."""

    def __getattribute__(self, name: str) -> Any:
        LOG.read(f"{type(self).__name__}.{name}")
        return object.__getattribute__(self, name)


class _Mapping(Mapping[str, Any]):
    """A mapping a hook returns, which records its reads."""

    def __init__(self, given: Mapping[str, Any]) -> None:
        self.given = dict(given)

    def __getitem__(self, key: str) -> Any:
        LOG.read("mapping[]")
        return self.given[key]

    def __iter__(self) -> Iterator[str]:
        LOG.read("mapping iter")
        return iter(self.given)

    def __len__(self) -> int:
        LOG.read("mapping len")
        return len(self.given)

    def items(self) -> Any:
        LOG.read("mapping items")
        return self.given.items()


class _Importer(_Watched):
    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        result = FileImporter().import_source(source, options)
        found: list[Any] = []
        for descriptor in result.descriptors:
            if isinstance(descriptor, DatasetDescriptor):
                written: dict[str, Any] = descriptor.model_dump(mode="json")
                written["fields"]["packs"] = ["watch"]
                written["fields"]["data_use"] = [
                    {"system": SYSTEM, "code": "w1", "label": "Watched", "relation": "exact"}
                ]
                entry = {"status": "imported", "by": "importer:watch@1.0.0", "at": options.at}
                written["curation"]["/fields/packs"] = {**entry, "inferred": ["watch"]}
                written["curation"]["/fields/data_use"] = {
                    **entry,
                    "inferred": written["fields"]["data_use"],
                }
                descriptor = DatasetDescriptor.model_validate(written)
            found.append(descriptor)
        return replace(result, descriptors=found)


class _Validator(_Watched):
    def validate_source(
        self, source: Any, result: ImportResult
    ) -> list[Refusal] | tuple[Refusal, ...]:
        return []

    def validate_descriptors(self, release: ReleaseView) -> list[Refusal] | tuple[Refusal, ...]:
        release.descriptors["trees"]
        return []


class _Call(_Watched):
    def __init__(self, answer: Callable[..., Any]) -> None:
        object.__setattr__(self, "answer", answer)

    def __call__(self, *given: Any) -> Any:
        return object.__getattribute__(self, "answer")(*given)


class _Leaf(_Watched):
    @property
    def schema(self) -> Mapping[str, JsonValue]:
        return {"type": "object"}

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Any:
        from aibi.core.schema.document import ValueLeaf

        release.descriptors["trees.variety"]
        return [ValueLeaf.model_validate(APPLE)]

    def summary(self, leaf: PackLeaf) -> Any:
        return [text("apples")]


class _Translator(_Watched):
    def translate(self, document: JsonValue) -> Any:
        written = {
            "aibi": "1",
            "dataset": "orchard",
            "unit": "trees",
            "cohorts": {"w": {"all": [{"kind": "watch.apples"}]}},
        }
        return _Mapping(written), [TranslationNote("/x", [text("a note")])]


def _entry() -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": "watch.echo",
            "version": "1.0.0",
            "label": "Echo",
            "fields": {
                "requires": [
                    {"role": "cohorts", "min": 1, "max": 6},
                    {"role": "ready", "predicate": "watch.ready"},
                ],
                "params": {"type": "object"},
                "returns": {"type": "object"},
                "methods": {},
                "assumptions": [],
                "uses_reference": False,
                "assumes_independent_groups": False,
                "cross_dataset": None,
                "caveats": [],
            },
        }
    )


class _Analysis(_Watched):
    @property
    def entry(self) -> AnalysisDescriptor:
        return _entry()

    def run(self, inputs: AnalysisInputs) -> Any:
        return _Mapping({"positions": [{} for _ in inputs.positions], "view": _Mapping({})})


def _proposals(release: ReleaseView) -> list[Proposal]:
    release.descriptors["trees"]
    return [Proposal("trees", "/definition", cast(Any, _Mapping({"text": "Trees"})))]


def watch_pack(**hooks: Any) -> Pack:
    given: dict[str, Any] = {
        "importer": _Importer(),
        "validator": _Validator(),
        "proposer": _Call(_proposals),
        "facet": _Call(lambda release: {"region": ["north"]}),
        "caveat_rule": _Call(lambda release, form: ["watch.SEEN"]),
        "ontology_systems": {SYSTEM: _Call(lambda code: True)},
        "translators": {"watch.flat": _Translator()},
        "leaf_kinds": {"watch.apples": _Leaf()},
        "requirement_predicates": {"ready": _Call(lambda release: True)},
        "analyses": (_Analysis(),),
    }
    given.update(hooks)
    return Pack(
        manifest=PackManifest(id="watch", version="1.0.0", results_version=1, requires_core=">=0"),
        caveat_codes={"watch.SEEN": Severity.INFO},
        **given,
    )


def watch(**hooks: Any) -> PackRegistry:
    return PackRegistry([watch_pack(**hooks)], core_version=aibi.__version__)


def _catalog(world: World, registry: PackRegistry) -> Catalog:
    return Catalog(world.store, workers=WORKERS, registry=registry)


def _document(**extra: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {"w": {"all": [{"kind": "watch.apples"}]}},
        **extra,
    }


def _request(*edits: dict[str, Any]) -> ChangeRequest:
    return ChangeRequest.model_validate({"edits": list(edits)})


def _all_paths(world: World, orchard: Orchard, registry: PackRegistry) -> None:
    """Every path that calls a pack (module docstring)."""
    store = world.store
    world.publish("orchard", orchard(), registry=registry, pack="watch")
    source, confinement = world._source("orchard", orchard(30))  # pyright: ignore[reportPrivateUsage]
    options = ImportOptions(
        dataset="orchard", reader=confinement, limits=ImportLimits(), at=store.now()
    )
    reimport_dataset(store, source, options, ADA, registry=registry, pack="watch")
    opened = sessions.open_session(store, "orchard", ADA)
    edit = {"op": "set", "descriptor": "trees", "pointer": "/label", "value": "Trees"}
    draft = sessions.change(
        store, "orchard", opened.handle, opened.draft, _request(edit), ADA, registry=registry
    )
    sessions.publish(store, "orchard", opened.handle, draft, ADA, registry=registry)
    catalog = _catalog(world, registry)
    proposed = world.tool(
        catalog,
        "propose_descriptor",
        {
            "dataset": "orchard",
            "agent": "agent:helper",
            "descriptor": "dataset",
            "pointer": "/fields/data_use",
            "value": [{"system": SYSTEM, "code": "w2", "label": "More", "relation": "exact"}],
        },
    )
    assert not isinstance(proposed, list), proposed
    found = world.tool(catalog, "search_catalog", {})
    assert isinstance(found, CatalogHits), found
    assert [list(hit.facets) for hit in found.hits] == [["watch.region"]]
    world.tool(catalog, "describe_dataset", {"dataset": "orchard"})
    listing = world.tool(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    assert isinstance(listing, AnalysisListing), listing
    translated = world.tool(
        catalog, "validate_document", {"document": {"x": 1}, "format": "watch.flat"}
    )
    assert isinstance(translated, DocumentValidation), translated
    assert translated.valid, translated.refusals
    counted = world.tool(catalog, "count_cohort", {"document": _document()})
    assert isinstance(counted, CohortCounts), counted
    world.tool(catalog, "explain", {"id": counted.counts[0].count.id})
    ran = world.tool(
        catalog,
        "run_analysis",
        {"document": _document(views=[{"analysis": "watch.echo", "cohorts": ["w"]}])},
    )
    assert isinstance(ran, AnalysisResults), ran


def test_every_read_of_a_pack_s_code_is_under_a_guard_and_every_copy_is_the_core_s(
    world: World, orchard: Orchard, monkeypatch: pytest.MonkeyPatch
) -> None:
    returned: list[tuple[str, object]] = []
    called = guards.Hook.call

    def recording(self: guards.Hook[Any], run: Callable[[Any], Any]) -> Any:
        found = called(self, run)
        returned.append((self.stage, found))
        return found

    monkeypatch.setattr(guards.Hook, "call", recording)
    registry = watch()
    LOG.reads.clear()
    _all_paths(world, orchard, registry)
    assert LOG.reads, "no hook was read"
    unguarded = sorted({what for what, guarded in LOG.reads if not guarded})
    assert unguarded == []
    stages = {stage for stage, _ in returned}
    assert stages == {
        "validator",
        "ontology validator",
        "proposer",
        "facet",
        "translator",
        "leaf compiler",
        "summary",
        "caveat rule",
        "requirement predicate",
        "analysis",
    }
    for _, found in returned:
        provenance.core_made(found)
    reads = {what for what, _ in LOG.reads}
    assert {"_Importer.import_source", "mapping items"} <= reads


# --- Each kind of raise, at the catalogue's sites ---------------------------------------------


def _raiser(site: str, raise_: Callable[..., Any]) -> PackRegistry:
    if site == "facet":
        return watch(facet=raise_)
    if site == "predicate":
        return watch(requirement_predicates={"ready": raise_})

    class Raising(_Translator):
        def translate(self, document: JsonValue) -> Any:
            raise_()

    return watch(translators={"watch.flat": Raising()})


def _ask(world: World, site: str, registry: PackRegistry) -> Output | list[Refusal]:
    catalog = _catalog(world, registry)
    if site == "facet":
        return world.tool(catalog, "search_catalog", {})
    if site == "predicate":
        return world.tool(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    return world.tool(catalog, "validate_document", {"document": {"x": 1}, "format": "watch.flat"})


_SITES = ("facet", "predicate", "translator")


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("kind", list(raisers.FAILURES))
def test_whatever_a_facet_predicate_or_translator_raises_fails_closed(
    world: World, orchard: Orchard, site: str, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    world.publish("orchard", orchard(), registry=watch(), pack="watch")
    found = _ask(world, site, _raiser(site, raisers.raising(raisers.FAILURES[kind])))
    if site == "facet":
        assert isinstance(found, CatalogHits), found
        assert found.hits[0].facets == {}
        assert "the watch pack's facet was left out of the catalogue" in caplog.messages
    elif site == "predicate":
        assert isinstance(found, AnalysisListing), found
        [echo] = [a for a in found.applicable or [] if a.analysis == "watch.echo"]
        assert echo.status == "unavailable"
        assert "ready" in echo.missing
    else:
        assert isinstance(found, DocumentValidation), found
        assert [r.code for r in found.refusals] == ["PACK_FAILED"]
    assert raisers.SECRET not in caplog.text
    assert raisers.SECRET not in found.model_dump_json()  # type: ignore[union-attr]


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("kind", list(raisers.PASSING))
def test_a_passed_type_a_facet_predicate_or_translator_raises_passes_anew(
    world: World, orchard: Orchard, site: str, kind: str
) -> None:
    world.publish("orchard", orchard(), registry=watch(), pack="watch")
    passed = raisers.PASSING[kind]
    with pytest.raises(passed) as raised:
        _ask(world, site, _raiser(site, raisers.passing(passed)))
    raisers.passed_anew(raised.value, passed)


def test_a_translation_larger_than_a_document_names_the_limit(
    world: World, orchard: Orchard
) -> None:
    class Large(_Translator):
        def translate(self, document: JsonValue) -> Any:
            return {"aibi": "1", "x": list(range(300_000))}, []

    world.publish("orchard", orchard(), registry=watch(), pack="watch")
    catalog = _catalog(world, watch(translators={"watch.flat": Large()}))
    found = world.tool(catalog, "validate_document", {"document": {}, "format": "watch.flat"})
    assert isinstance(found, DocumentValidation)
    [refusal] = found.refusals
    assert refusal.code == "PACK_FAILED"
    assert refusal.limit is not None
    assert refusal.limit.name == "json_values"


# --- The facet twin ---------------------------------------------------------------------------


def _marking(release: ReleaseView) -> dict[str, list[str] | tuple[str, ...]]:
    """A facet that marks its view, and says whether it found a mark there."""
    extensions: Any = release.descriptors["trees"].extensions
    seen = "marked" in extensions
    extensions["marked"] = {"by": "facet"}
    return {"seen": ["yes" if seen else "no"]}


def _facets(world: World, registry: PackRegistry) -> dict[str, dict[str, list[str]]]:
    found = world.tool(_catalog(world, registry), "search_catalog", {})
    assert isinstance(found, CatalogHits), found
    return {
        hit.dataset: {name: [v.data for v in values] for name, values in hit.facets.items()}
        for hit in found.hits
    }


def test_a_dataset_s_facets_are_the_same_built_alone_or_with_the_others(
    world: World, orchard: Orchard
) -> None:
    registry = watch(facet=_marking)
    world.publish("orchard", orchard(), registry=registry, pack="watch")
    alone = _facets(world, registry)
    world.store.db.connection.execute("DELETE FROM catalog")
    world.publish("grove", orchard(30), registry=registry, pack="watch")
    together = _facets(world, registry)
    assert alone == {"orchard": {"watch.seen": ["no"]}}
    assert together == {"orchard": {"watch.seen": ["no"]}, "grove": {"watch.seen": ["no"]}}
    store = world.store
    for dataset in ("orchard", "grove"):
        descriptors = store.descriptors(store.resolve(dataset).manifest)
        assert not any("marked" in d.extensions for d in descriptors)


def test_the_catalogue_keeps_what_it_indexed_whatever_the_facet_does_later(
    world: World, orchard: Orchard
) -> None:
    kept: list[dict[str, list[str]]] = []

    def keeping(release: ReleaseView) -> dict[str, list[str] | tuple[str, ...]]:
        given = {"region": ["north"]}
        kept.append(given)
        return cast(dict[str, list[str] | tuple[str, ...]], given)

    registry = watch(facet=keeping)
    world.publish("orchard", orchard(), registry=registry, pack="watch")
    first = _facets(world, registry)
    for given in kept:
        given["region"].append("changed")
        given["other"] = ["x"]
    assert _facets(world, registry) == first == {"orchard": {"watch.region": ["north"]}}


def _cast(value: object) -> Any:
    return cast(Any, value)


# --- No view is shared across packs -----------------------------------------------------------


class _Listing(_Importer):
    """The watch pack's importer, whose dataset lists every pack of ``PACKS``."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        result = _Importer.import_source(self, source, options)
        found: list[Any] = []
        for descriptor in result.descriptors:
            if isinstance(descriptor, DatasetDescriptor):
                written: dict[str, Any] = descriptor.model_dump(mode="json")
                written["fields"]["packs"] = list(PACKS)
                written["curation"]["/fields/packs"]["inferred"] = list(PACKS)
                descriptor = DatasetDescriptor.model_validate(written)
            found.append(descriptor)
        return replace(result, descriptors=found)


PACKS = ("other", "watch")


def _marks(release: ReleaseView, pack: str) -> bool:
    """Mark the view as ``pack``'s, and say whether another pack's mark is there."""
    extensions: Any = release.descriptors["trees"].extensions
    seen = any(key != f"mark-{pack}" for key in extensions if key.startswith("mark-"))
    extensions[f"mark-{pack}"] = {"x": 1}
    return seen


def _other(**hooks: Any) -> Pack:
    entry = _entry().model_dump(mode="json")
    entry["id"] = "other.echo"
    entry["fields"]["requires"][1]["predicate"] = "other.ready"

    class Other(_Analysis):
        @property
        def entry(self) -> AnalysisDescriptor:
            return AnalysisDescriptor.model_validate(entry)

    return Pack(
        manifest=PackManifest(id="other", version="1.0.0", results_version=1, requires_core=">=0"),
        analyses=(Other(),),
        **hooks,
    )


def test_one_pack_s_facet_or_predicate_never_sees_what_another_s_did_to_its_view(
    world: World, orchard: Orchard
) -> None:
    seen: list[tuple[str, bool]] = []

    def facet(pack: str) -> Callable[[ReleaseView], Mapping[str, Sequence[str]]]:
        def given(release: ReleaseView) -> dict[str, list[str] | tuple[str, ...]]:
            seen.append((f"{pack} facet", _marks(release, pack)))
            return {"seen": ["x"]}

        return given

    def predicate(pack: str) -> Callable[[ReleaseView], bool]:
        def given(release: ReleaseView) -> bool:
            seen.append((f"{pack} predicate", _marks(release, pack)))
            return True

        return given

    watched = watch_pack(
        importer=_Listing(),
        facet=facet("watch"),
        requirement_predicates={"ready": predicate("watch")},
    )
    other = _other(facet=facet("other"), requirement_predicates={"ready": predicate("other")})
    registry = PackRegistry([watched, other], core_version=aibi.__version__)
    world.publish("orchard", orchard(), registry=registry, pack="watch")
    catalog = _catalog(world, registry)
    found = world.tool(catalog, "search_catalog", {})
    assert isinstance(found, CatalogHits), found
    listing = world.tool(catalog, "list_analyses", {"dataset": "orchard", "unit": "trees"})
    assert isinstance(listing, AnalysisListing), listing
    assert {pack for pack, _ in seen} == {
        "watch facet",
        "other facet",
        "watch predicate",
        "other predicate",
    }
    assert not any(other_seen for _, other_seen in seen), seen
