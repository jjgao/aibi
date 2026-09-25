"""The query tools of M2 over a store (SPEC §7.7, §8.1, §8.4, §8.6, §11.1, §12.2; D299–D303):
``validate_document``, ``count_cohort`` and ``explain`` on the orchard, a non-biomedical dataset
imported by the core's file importer, as both transports call them."""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.catalog.cohorts import (
    ANSWER_SECONDS,
    NEGATION_MESSAGE,
    RECORD_SECONDS,
    validate_document,
)
from aibi.core.catalog.service import Catalog, Deadline, within
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.resolve import resolve
from aibi.core.engine.worker import CallerDeadline, Query, QueryRefused, Rows, Workers
from aibi.core.schema.cohorts import (
    CohortCounts,
    DocumentValidation,
    Explanation,
    ValidateDocument,
)
from aibi.core.schema.limits import MIN_LOG_BYTES, LogLimits, QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import DataSegment, Output, TextSegment, text
from aibi.core.schema.pack_api import (
    Pack,
    PackManifest,
    PackRegistry,
    TranslationNote,
)
from aibi.core.schema.refusals import Limit, Refusal, RefusalCode
from aibi.core.store.appdb import LOG_ISSUANCE_BYTES
from aibi.core.store.derivations import text_of
from aibi.core.store.erasure import erase
from aibi.core.store.store import Store

World = Any
Orchard = Callable[..., dict[str, bytes]]
WORKERS = Workers(QueryLimits())
TALL = {"kind": "value", "column": "trees.height_m", "range": {"gte": 5}}
HEAVY = {
    "kind": "exists",
    "table": "harvests",
    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
}


def document(cohorts: Mapping[str, Sequence[Any]] | None = None, **extra: Any) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"tall": [TALL]}
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        **extra,
    }


def catalog_of(world: World, **given: Any) -> Catalog:
    return Catalog(world.store, workers=given.pop("workers", WORKERS), **given)


def answer(catalog: Catalog, name: str, body: JsonValue) -> Output | list[Refusal]:
    return call(catalog, BY_NAME[name], json.dumps(body).encode())


def counted(catalog: Catalog, written: Mapping[str, Any]) -> CohortCounts:
    found = answer(catalog, "count_cohort", {"document": dict(written)})
    assert isinstance(found, CohortCounts), found
    return found


def validated(catalog: Catalog, body: Mapping[str, Any]) -> DocumentValidation:
    found = answer(catalog, "validate_document", dict(body))
    assert isinstance(found, DocumentValidation), found
    return found


def explained(catalog: Catalog, identifier: str) -> Explanation:
    found = answer(catalog, "explain", {"id": identifier})
    assert isinstance(found, Explanation), found
    return found


def refused(found: Output | list[Refusal]) -> list[Refusal]:
    assert isinstance(found, list), found
    return found


def evaluated(store: Store, manifest: str, clauses: Sequence[Any]) -> Any:
    loaded = load_document(json.dumps(document({"c": clauses})))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"orchard": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    return evaluate(resolution.cohorts["c"])


# --- count_cohort ----------------------------------------------------------------------------


def test_count_cohort_counts_each_cohort_as_the_reference_evaluator_does(
    world: World, orchard: Orchard
) -> None:
    published = world.publish("orchard", orchard())
    catalog = catalog_of(world)
    found = counted(catalog, document({"tall": [TALL], "heavy": [HEAVY], "all": []}))
    assert [count.cohort.data for count in found.counts] == ["all", "heavy", "tall"]
    for named, clauses in zip(found.counts, ([], [HEAVY], [TALL]), strict=True):
        count = named.count
        expected = evaluated(world.store, published.manifest, clauses)
        population = count.population
        assert (population.n_true, population.n_false, population.n_unknown) == (
            expected.n_true,
            expected.n_false,
            expected.n_unknown,
        )
        assert count.size.numerator == expected.n_true
        assert count.size.denominator == 24
        assert count.releases[0].manifest == published.manifest
        assert count.releases[0].label == 1
        assert count.disclosure.min_cell_count is None
        assert count.issuance.values_from == count.issuance.id
        assert not count.issuance.cache_hit
    tall = found.counts[2].count
    assert (tall.population.n_true, tall.population.n_false, tall.population.n_unknown) == (
        8,
        13,
        3,
    )
    codes = {caveat.code for caveat in tall.caveats}
    assert {"UNKNOWN_EXCLUDED", "UNCONFIRMED_SEMANTICS"} <= codes
    assert found.counts[2].leaves == {
        "/cohorts/tall/all/0": list(tall.population.unknown_by_leaf or {})
    }
    assert len({named.count.issuance.id for named in found.counts}) == 3


def test_a_count_s_readback_comes_from_templates(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    [named] = counted(catalog_of(world), document()).counts
    readback = named.count.readback
    assert readback[:6] == [
        text("Rows of "),
        DataSegment(data="trees"),
        text(" in "),
        DataSegment(data="orchard"),
        text(" @"),
        DataSegment(data="1"),
    ]
    assert DataSegment(data="5") in readback
    assert "tall" not in "".join(s.text for s in readback if isinstance(s, TextSegment))


def test_equivalent_documents_count_to_one_id_and_one_digest(
    world: World, orchard: Orchard
) -> None:
    """Names, notes, parameters and equivalent syntax change neither (§7.6, §13.4)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    first = counted(catalog, document({"tall": [TALL, HEAVY]}))
    second = counted(
        catalog,
        document(
            {
                "renamed": [
                    HEAVY,
                    {
                        "all": [
                            {"kind": "value", "column": "trees.height_m", "op": ">=", "value": "$h"}
                        ]
                    },
                    HEAVY,
                ]
            },
            params={"h": 5},
            notes="a note",
            drafted_by="agent:tester",
        ),
    )
    [one], [other] = first.counts, second.counts
    assert (one.count.id, one.count.digest) == (other.count.id, other.count.digest)
    assert one.count.issuance.id != other.count.issuance.id
    assert second.params.used == {"h": 5}


def test_a_count_is_issued_and_explain_gives_its_derivation_and_its_sql(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document()
    [named] = counted(catalog, written).counts
    count = named.count
    derivation = explained(catalog, count.id)
    assert derivation.status == "issued"
    assert derivation.derivation is not None
    assert derivation.derivation.kind == "cohort"
    hashed = derivation.derivation.object
    assert hashed is not None
    assert set(hashed) == {"cohort", "unit", "semantics_version", "disclosure", "packs"}
    assert [release.manifest for release in derivation.derivation.releases] == [
        count.releases[0].manifest
    ]
    issued = explained(catalog, count.issuance.id)
    assert issued.status == "issued"
    assert issued.issuance is not None
    assert issued.issuance.tool == "count_cohort"
    assert issued.issuance.engine == f"aibi {aibi.__version__}"
    sql = issued.issuance.sql
    assert isinstance(sql, dict)
    statements = sql["statements"]
    assert isinstance(statements, list)
    assert statements
    assert all("SELECT" in str(statement) for statement in statements)
    parameters = json.dumps(sql["parameters"])
    assert str(world.store.blobs.root) not in parameters
    trees = world.store.manifest(count.releases[0].manifest).table("trees")
    assert trees is not None
    assert trees.hash in parameters


def test_explain_works_after_the_store_is_opened_again(
    world: World, orchard: Orchard, tmp_path: Path
) -> None:
    """Nothing kept in memory is needed: the log alone answers (§13.4, Provenance)."""
    world.publish("orchard", orchard())
    [named] = counted(catalog_of(world), document()).counts
    clock = world.store.clock
    world.store.close()
    world.store = Store(tmp_path / "data", clock=clock)
    catalog = catalog_of(world)
    assert explained(catalog, named.count.id).status == "issued"
    issued = explained(catalog, named.count.issuance.id)
    assert issued.issuance is not None
    assert issued.issuance.sql is not None
    logged = world.store.derivations.issuance(named.count.issuance.id)
    assert logged is not None
    assert logged.written == document()


def test_explain_says_what_an_id_it_does_not_hold_is(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    assert explained(catalog, "drv:" + "0" * 64).status == "not_issued"
    assert explained(catalog, "iss:01J8Z3S4T5V6W7X8Y9ZABCDEFG").status == "unknown"
    wrong = refused(answer(catalog, "explain", {"id": "leaf:" + "0" * 64}))
    assert wrong[0].code == RefusalCode.INVALID_VALUE
    assert wrong[0].path == "/id"


def test_a_withdrawn_release_s_ids_resolve_to_withdrawn(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    [named] = counted(catalog_of(world), document()).counts
    world.reimport("orchard", orchard(25))
    world.store.withdraw("orchard", 1, "operator:Ada")
    catalog = catalog_of(world)
    assert explained(catalog, named.count.id).status == "withdrawn"
    assert explained(catalog, named.count.issuance.id).status == "withdrawn"


# --- validate_document -----------------------------------------------------------------------


def test_validate_document_reads_back_and_marks_ids_not_yet_issued(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document(
        {"tall": [{"kind": "value", "column": "trees.height_m", "range": {"gte": "$h"}}]},
        params={"h": 5, "unused": 1},
        views=[{"analysis": "summary.counts", "cohorts": ["tall"]}],
    )
    found = validated(catalog, {"document": written})
    assert found.valid
    assert found.refusals == []
    [check] = found.cohorts
    assert check.status == "not_issued"
    assert check.cohort.data == "tall"
    assert check.unit == "trees"
    assert check.release.label == 1
    assert [view.model_dump(mode="json") for view in found.views] == [
        {"position": 0, "analysis": {"data": "summary.counts"}, "status": "unchecked"}
    ]
    assert found.params is not None
    assert found.params.used == {"h": 5}
    assert [name.data for name in found.params.unused] == ["unused"]
    assert {caveat.code for caveat in check.caveats} == {"UNCONFIRMED_SEMANTICS"}
    [named] = counted(catalog, written).counts
    assert named.count.id == check.id
    assert named.count.readback == check.readback
    again = validated(catalog, {"document": written})
    assert again.cohorts[0].status == "issued"


def test_validate_document_returns_every_refusal_and_count_cohort_the_first(
    world: World, orchard: Orchard
) -> None:
    """Each refusal names its code, its path into the document as written and what is
    available instead (§8.6, §13.4)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document(
        {
            "a": [{"kind": "value", "column": "trees.girth", "values": [1]}],
            "b": [{"kind": "value", "column": "trees.height_m", "values": ["tall"]}],
            "c": [{"kind": "value", "column": "trees.variety", "range": {"gt": "apple"}}],
            "d": [dict(HEAVY, quantifier=["some", "every"])],
            "e": [TALL],
        }
    )
    found = validated(catalog, {"document": written})
    assert not found.valid
    assert [(refusal.code, refusal.path) for refusal in found.refusals] == [
        (RefusalCode.UNKNOWN_COLUMN, "/cohorts/a/all/0/column"),
        (RefusalCode.INVALID_CONSTANT, "/cohorts/b/all/0/values/0"),
        (RefusalCode.RANGE_NOT_ALLOWED, "/cohorts/c/all/0/range"),
        (RefusalCode.QUANTIFIER_MISMATCH, "/cohorts/d/all/0/quantifier"),
    ]
    unknown = found.refusals[0]
    assert DataSegment(data="height_m") in unknown.alternatives
    assert [check.cohort.data for check in found.cohorts] == ["e"]
    first = refused(answer(catalog, "count_cohort", {"document": written}))
    assert first == found.refusals[:1]


def test_references_to_releases_are_refused_where_they_are_written(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.reimport("orchard", orchard(25))
    world.store.withdraw("orchard", 1, "operator:Ada")
    catalog = catalog_of(world)
    cases: list[tuple[dict[str, Any], RefusalCode, str, list[str]]] = [
        (document(dataset="grove"), RefusalCode.UNKNOWN_DATASET, "/dataset", ["orchard"]),
        (document(dataset="orchard@9"), RefusalCode.UNKNOWN_RELEASE, "/dataset", ["2"]),
        (document(dataset="orchard@1"), RefusalCode.RELEASE_WITHDRAWN, "/dataset", ["2"]),
        (document(dataset="orchard@draft"), RefusalCode.UNKNOWN_RELEASE, "/dataset", ["2"]),
        (
            document(dataset="$ds", params={"ds": "orchard@7"}),
            RefusalCode.UNKNOWN_RELEASE,
            "/dataset",
            ["2"],
        ),
    ]
    for written, code, path, alternatives in cases:
        found = validated(catalog, {"document": written})
        [refusal] = found.refusals
        assert (refusal.code, refusal.path) == (code, path), written
        assert [a.data for a in refusal.alternatives if isinstance(a, DataSegment)] == alternatives
        assert found.cohorts == []
    parameter = validated(catalog, {"document": cases[-1][0]}).refusals[0]
    assert DataSegment(data="ds") in parameter.message
    mixed = document(dataset="orchard@2")
    mixed["cohorts"]["other"] = {"all": [], "dataset": "orchard"}
    mixed["cohorts"]["third"] = {"all": [], "dataset": "orchard@sha256:" + "0" * 64}
    found = validated(catalog, {"document": mixed})
    assert [(refusal.code, refusal.path) for refusal in found.refusals] == [
        (RefusalCode.UNKNOWN_RELEASE, "/cohorts/third/dataset")
    ]


def test_mixed_releases_of_one_dataset_are_refused(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.reimport("orchard", orchard(25))
    written = document(dataset="orchard@1")
    written["cohorts"]["other"] = {"all": [], "dataset": "orchard"}
    found = validated(catalog_of(world), {"document": written})
    assert [(refusal.code, refusal.path) for refusal in found.refusals] == [
        (RefusalCode.MIXED_RELEASES, "/cohorts/other/dataset")
    ]


def test_unit_keys_are_refused_without_row_id_access(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": False},
        },
    )
    found = validated(
        catalog_of(world),
        {"document": document({"c": [{"kind": "ids", "ids": ["orchard:tree1"]}]})},
    )
    assert [(refusal.code, refusal.path) for refusal in found.refusals] == [
        (RefusalCode.ROW_IDS_NOT_ALLOWED, "/cohorts/c/all/0")
    ]


def test_a_request_s_own_problems_point_into_the_request(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    extra = refused(answer(catalog, "count_cohort", {"document": document(), "extra": 1}))
    assert (extra[0].code, extra[0].path) == (RefusalCode.UNKNOWN_MEMBER, "/extra")
    loose = refused(answer(catalog, "count_cohort", {"document": [1]}))
    assert (loose[0].code, loose[0].path) == (RefusalCode.WRONG_TYPE, "/document")
    inner = validated(catalog, {"document": {"aibi": "1"}})
    assert (RefusalCode.MISSING_MEMBER, "/unit") in {
        (refusal.code, refusal.path) for refusal in inner.refusals
    }
    assert inner.params is None


def test_too_many_cohorts_are_refused_naming_the_limit(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    found = validated(catalog_of(world), {"document": document({f"c{n}": [] for n in range(7)})})
    [refusal] = found.refusals
    assert refusal.code == RefusalCode.LIMIT_EXCEEDED
    assert refusal.limit is not None
    assert refusal.limit.name == "cohorts"


class _Busy(Workers):
    """Workers that are always busy, as ``Workers.run`` refuses a run with no place."""

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        raise QueryRefused(
            Refusal(
                code=RefusalCode.LIMIT_EXCEEDED,
                path=None,
                message=[text("Every query worker was busy")],
                limit=Limit(name="query_workers", max=2),
            )
        )


def test_a_query_the_workers_refuse_is_refused_naming_their_limit(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, workers=_Busy(QueryLimits()))
    [refusal] = refused(answer(catalog, "count_cohort", {"document": document()}))
    assert refusal.limit == Limit(name="query_workers", max=2)
    assert world.store.derivations.derivation("drv:" + "0" * 64) is None


def test_a_catalogue_without_query_workers_counts_nothing(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = Catalog(world.store)
    [refusal] = refused(answer(catalog, "count_cohort", {"document": document()}))
    assert refusal.code == RefusalCode.NOT_SUPPORTED


# --- Disclosure ------------------------------------------------------------------------------


def test_counts_are_disclosed_under_the_floor_and_the_dataset_s_setting(
    world: World, orchard: Orchard
) -> None:
    """3 unknown trees are suppressed with the smallest other count; the size's numerator
    stays; the id hashes the effective setting, and the digest what the pass left (§8.4)."""
    world.publish("orchard", orchard())
    plain = counted(catalog_of(world), document()).counts[0].count
    floored = counted(catalog_of(world, floor=5), document()).counts[0].count
    assert floored.disclosure.min_cell_count == 5
    assert floored.id != plain.id
    assert floored.digest != plain.digest
    population = floored.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, 13, None)
    assert floored.size.numerator is None
    assert floored.size.denominator == 24
    codes = {caveat.code for caveat in floored.caveats}
    assert {"SUPPRESSED", "UNKNOWN_EXCLUDED"} <= codes
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": True},
        },
    )
    own = counted(catalog_of(world), document()).counts[0].count
    assert own.disclosure.min_cell_count == 5
    assert own.population.n_unknown is None
    assert own.releases[0].label == 2


def test_a_draft_counts_under_at_least_its_published_setting(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 5, "allow_row_ids": True},
        },
    )
    opened = world.open("orchard")
    world.change(
        "orchard",
        opened,
        opened.draft,
        {
            "op": "set",
            "descriptor": "dataset",
            "pointer": "/fields/disclosure",
            "value": {"min_cell_count": 2, "allow_row_ids": True},
        },
    )
    [named] = counted(catalog_of(world), document(dataset="orchard@draft")).counts
    count = named.count
    assert count.disclosure.min_cell_count == 5
    assert count.releases[0].status == "draft"
    assert not count.issuance.cache_hit
    assert "DRAFT_RELEASE" in {caveat.code for caveat in count.caveats}


# --- Translation -----------------------------------------------------------------------------


class _Flat:
    """A test-only format: ``{"trees_where": {"<column>": [values]}, "except": {...}}``, one
    cohort of trees whose columns hold one of the values, and not those of ``except``."""

    def __init__(self, fail: bool = False, junk: object = None) -> None:
        self.fail = fail
        self.junk = junk

    def translate(
        self, document: JsonValue
    ) -> tuple[Mapping[str, JsonValue], Sequence[TranslationNote]]:
        if self.fail:
            raise RuntimeError("the translator broke on " + json.dumps(document))
        if self.junk is not None:
            return self.junk  # type: ignore[return-value]
        given = document if isinstance(document, dict) else {}
        wanted = given.get("trees_where", {})
        unwanted = given.get("except", {})
        assert isinstance(wanted, dict)
        assert isinstance(unwanted, dict)
        clauses: list[JsonValue] = [
            {"kind": "value", "column": f"trees.{column}", "values": values}
            for column, values in sorted(wanted.items())
        ]
        for column, values in sorted(unwanted.items()):
            negated: dict[str, JsonValue] = {
                "kind": "value",
                "column": f"trees.{column}",
                "values": values,
            }
            clauses.append({"not": negated})
        translated: dict[str, JsonValue] = {
            "aibi": "1",
            "dataset": "orchard",
            "unit": "trees",
            "cohorts": {"flat": {"all": clauses}},
        }
        notes = [TranslationNote("/except", [text("except is read as a not")])] if unwanted else []
        return translated, notes


def registry_of(translator: _Flat) -> PackRegistry:
    pack = Pack(
        manifest=PackManifest(id="flat", version="1.0.0", results_version=1, requires_core=">=0"),
        translators={"flat.where": translator},
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def test_a_document_in_another_format_is_translated_and_every_not_flagged(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, registry=registry_of(_Flat()))
    given = {"trees_where": {"variety": ["apple"]}, "except": {"tags": ["old"]}}
    found = validated(catalog, {"document": given, "format": "flat.where"})
    assert found.valid, found.refusals
    translation = found.translation
    assert translation is not None
    assert translation.format == "flat.where"
    assert translation.document["cohorts"] == {
        "flat": {
            "all": [
                {"kind": "value", "column": "trees.variety", "values": ["apple"]},
                {"not": {"kind": "value", "column": "trees.tags", "values": ["old"]}},
            ]
        }
    }
    assert [(note.pointer, note.translated) for note in translation.notes] == [
        ("/except", False),
        ("/cohorts/flat/all/1/not", True),
    ]
    assert translation.notes[1].message == [text(NEGATION_MESSAGE)]
    assert [check.cohort.data for check in found.cohorts] == ["flat"]
    wrong = validated(
        catalog, {"document": {"trees_where": {"girth": [1]}}, "format": "flat.where"}
    )
    assert [(refusal.code, refusal.path) for refusal in wrong.refusals] == [
        (RefusalCode.UNKNOWN_COLUMN, "/cohorts/flat/all/0/column")
    ]


def test_an_unknown_format_is_refused_listing_the_formats(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, registry=registry_of(_Flat()))
    for format in ("flat.other", "none.where"):
        found = validated(catalog, {"document": {}, "format": format})
        [refusal] = found.refusals
        assert refusal.code == RefusalCode.NOT_SUPPORTED
        assert refusal.alternatives == [DataSegment(data="flat.where")]
    bad = refused(answer(catalog, "validate_document", {"document": {}, "format": "flat__x.y"}))
    assert bad[0].path == "/format"


def test_a_translator_that_fails_is_refused_as_the_pack_s_failure(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    for translator in (
        _Flat(fail=True),
        _Flat(junk=("not a document", [])),
        _Flat(junk=({"aibi": "1"}, ["not a note"])),
        _Flat(junk={"aibi": "1"}),
        _Flat(junk=({"x": float("nan")}, [])),
    ):
        catalog = catalog_of(world, registry=registry_of(translator))
        found = validated(catalog, {"document": {"secret": "the leaf"}, "format": "flat.where"})
        [refusal] = found.refusals
        assert refusal.code == RefusalCode.PACK_FAILED
        assert "the leaf" not in json.dumps(refusal.model_dump(mode="json"))


def test_a_document_holding_a_secret_s_shape_is_never_counted(
    world: World, orchard: Orchard
) -> None:
    """The log keeps the document as written, so it is refused as kept text is (D265, D300)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    handle = "ses_" + "A" * 43
    [refusal] = refused(answer(catalog, "count_cohort", {"document": document(notes=handle)}))
    assert (refusal.code, refusal.path) == (RefusalCode.INVALID_VALUE, "/document/notes")
    assert handle not in json.dumps(refusal.model_dump(mode="json"))
    assert world.store.derivations.issuances(counted(catalog, document()).counts[0].count.id)


class _Given:
    """A test-only translator that gives ``made`` whatever it is given, after writing into what
    it was given."""

    def __init__(self, made: object) -> None:
        self.made = made

    def translate(
        self, document: JsonValue
    ) -> tuple[Mapping[str, JsonValue], Sequence[TranslationNote]]:
        if isinstance(document, dict):
            document["written by the translator"] = True
        return self.made  # type: ignore[return-value]


def given_registry(*names: str, made: object = None) -> PackRegistry:
    translators = {name: _Given(made) for name in names}
    pack = Pack(
        manifest=PackManifest(id="flat", version="1.0.0", results_version=1, requires_core=">=0"),
        translators=translators,
    )
    return PackRegistry([pack], core_version=aibi.__version__)


def test_every_negation_of_a_translation_is_flagged_in_pointer_order(
    world: World, orchard: Orchard
) -> None:
    """not, negate, != and every each keep a unit whose answer is unknown out of both sides
    (§6.3, D303); the notes are sorted by pointer, whatever order they were found in."""
    world.publish("orchard", orchard())
    translated = document(
        {
            "b": [
                {"not": TALL},
                {"kind": "value", "column": "trees.height_m", "op": "!=", "value": 5},
                {"kind": "value", "column": "trees.tags", "values": ["old"], "negate": True},
                {**HEAVY, "quantifier": "every"},
                {**HEAVY, "quantifier": ["every"]},
            ],
            "a": [{"any": [{"not": HEAVY}, TALL]}],
        }
    )
    catalog = catalog_of(world, registry=given_registry("flat.where", made=(translated, [])))
    found = validated(catalog, {"document": {}, "format": "flat.where"})
    assert found.valid, found.refusals
    assert found.translation is not None
    assert [note.pointer for note in found.translation.notes] == [
        "/cohorts/a/all/0/any/0/not",
        "/cohorts/b/all/0/not",
        "/cohorts/b/all/1/op",
        "/cohorts/b/all/2/negate",
        "/cohorts/b/all/3/quantifier",
        "/cohorts/b/all/4/quantifier/0",
    ]
    assert all(note.translated for note in found.translation.notes)
    assert all(note.message == [text(NEGATION_MESSAGE)] for note in found.translation.notes)


def test_a_translator_writes_into_its_own_copy_of_the_request_s_document(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    catalog = catalog_of(world, registry=given_registry("flat.where", made=(document(), [])))
    request = ValidateDocument(document={"trees": "tall"}, format="flat.where")
    assert validate_document(catalog, request).valid
    assert request.document == {"trees": "tall"}


@pytest.mark.parametrize(
    ("notes", "segments", "accepted"),
    [(1_000, 1, True), (1_001, 1, False), (1, 64, True), (1, 65, False)],
)
def test_a_translation_holds_at_most_1000_notes_of_at_most_64_segments(
    world: World, orchard: Orchard, notes: int, segments: int, accepted: bool
) -> None:
    world.publish("orchard", orchard())
    made = (document(), [TranslationNote("/trees", [text("a")] * segments)] * notes)
    catalog = catalog_of(world, registry=given_registry("flat.where", made=made))
    found = validated(catalog, {"document": {}, "format": "flat.where"})
    if accepted:
        assert found.translation is not None
        assert len(found.translation.notes) == notes
    else:
        [refusal] = found.refusals
        assert refusal.code == RefusalCode.PACK_FAILED


def test_a_long_list_of_formats_is_cut_and_counted(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    names = [f"flat.f{index:03d}" for index in range(65)]
    catalog = catalog_of(world, registry=given_registry(*names))
    [refusal] = validated(catalog, {"document": {}, "format": "flat.other"}).refusals
    assert refusal.alternatives == [
        *(DataSegment(data=name) for name in names[:64]),
        text("and 1 more"),
    ]


# --- The derivation log and the call's deadline ----------------------------------------------


def test_explain_gives_neither_an_issuance_s_request_nor_when_a_derivation_was_recorded(
    world: World, orchard: Orchard
) -> None:
    """The request may hold another client's cohorts, notes and names, and when a cohort was
    first counted is its own business: the log keeps both for the operator (D302)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document(
        {"tall": [{"kind": "value", "column": "trees.height_m", "range": {"gte": "$h"}}]},
        params={"h": 5},
        notes="asked for the neighbours",
    )
    [named] = counted(catalog, written).counts
    issued = explained(catalog, named.count.issuance.id).model_dump(mode="json")
    assert set(issued["issuance"]) == {
        "id",
        "derivation",
        "tool",
        "sql",
        "values_from",
        "engine",
        "packs",
        "at",
    }
    assert set(issued["derivation"]) == {"id", "kind", "object", "releases"}
    assert "neighbours" not in json.dumps(issued)
    logged = world.store.derivations.issuance(named.count.issuance.id)
    assert logged is not None
    assert (logged.written, logged.params) == (written, {"h": 5})


def _texts(world: World) -> int:
    return int(world.store.db.connection.execute("SELECT count(*) FROM log_texts").fetchone()[0])


def test_a_call_s_cohorts_share_its_stored_request_and_a_count_made_again_adds_no_text(
    world: World, orchard: Orchard
) -> None:
    """A count made again adds its issuances' rows alone, which ``log_bytes`` counts too (D300)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world)
    written = document({"a": [TALL], "b": [HEAVY], "c": []})
    first = counted(catalog, written)
    assert _texts(world) == 1 + 3  # the request, and each cohort's SQL
    log = world.store.derivations
    used = log.usage()
    again = counted(catalog, written)
    assert _texts(world) == 4
    rows = [log.issuance(named.count.issuance.id) for named in again.counts]
    assert log.usage() - used == sum(
        LOG_ISSUANCE_BYTES + len(text_of(row.packs).encode()) for row in rows if row is not None
    )
    assert log.usage() == log.measured()
    ids = [named.count.issuance.id for named in (*first.counts, *again.counts)]
    assert len(set(ids)) == 6


def _grown(world: World, cohorts: Mapping[str, Sequence[Any]]) -> tuple[int, int]:
    """What counting a document adds to the log, in bytes, and the document's size."""
    written = document(cohorts)
    before = world.store.derivations.usage()
    counted(catalog_of(world), written)
    return world.store.derivations.usage() - before, len(json.dumps(written))


def test_what_a_count_adds_to_the_log_grows_with_its_document_not_its_cohorts(
    world: World, orchard: Orchard
) -> None:
    """The request is stored once for all the call's cohorts, so six cohorts of 1,500 keys each
    add about what one cohort of all 9,000 does: the document, and each constant once in the
    SQL and once in the derivations' objects (D300)."""
    world.publish("orchard", orchard())

    def leaf(start: int) -> dict[str, Any]:
        keys = [f"key{index:06d}" for index in range(start, start + 1_500)]
        return {"kind": "value", "column": "trees.tree_id", "values": keys}

    one, one_size = _grown(world, {"all": [leaf(1_500 * part) for part in range(6)]})
    six, six_size = _grown(world, {f"c{part}": [leaf(90_000 + 1_500 * part)] for part in range(6)})
    assert one <= 4 * one_size
    assert six <= 4 * six_size
    assert six < 1.25 * one


class _Recording(Workers):
    """Workers that note the deadline they were given and, when ``late``, answer once too little
    of the call's deadline is left to record its issuances and answer."""

    def __init__(self, *, late: bool = False) -> None:
        super().__init__(QueryLimits())
        self.ends: list[float | None] = []
        self.late = late

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        self.ends.append(ends)
        rows = super().run(paths, queries, ends=ends)
        if self.late and ends is not None:
            answered = ends + RECORD_SECONDS - ANSWER_SECONDS
            time.sleep(max(0.0, answered + 0.1 - time.monotonic()))
        return rows


def _within(seconds: float, catalog: Catalog, body: JsonValue) -> tuple[Deadline, Any]:
    deadline = Deadline(time.monotonic() + seconds, 30.0)
    return deadline, within(deadline, lambda: answer(catalog, "count_cohort", body))


def test_the_queries_end_in_time_for_the_call_to_record_and_answer(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    workers = _Recording()
    deadline, found = _within(20, catalog_of(world, workers=workers), {"document": document()})
    assert isinstance(found, CohortCounts)
    assert workers.ends == [deadline.at - RECORD_SECONDS]


def test_a_call_too_late_to_run_its_queries_records_nothing(world: World, orchard: Orchard) -> None:
    world.publish("orchard", orchard())
    workers = _Recording()
    _, found = _within(RECORD_SECONDS / 2, catalog_of(world, workers=workers), {"document": {}})
    assert refused(found)[0].code == RefusalCode.MISSING_MEMBER  # the document is refused first
    _, found = _within(
        RECORD_SECONDS / 2, catalog_of(world, workers=workers), {"document": document()}
    )
    [refusal] = refused(found)
    assert refusal.limit == Limit(name="tool_seconds", max=30)
    assert workers.ends == []
    assert _texts(world) == 0


def test_a_call_that_could_not_be_answered_records_no_issuance(
    world: World, orchard: Orchard
) -> None:
    """Its queries ran, but too little of its deadline is left to answer: the transaction that
    would record its issuances, and their derivations, is rolled back (D300)."""
    world.publish("orchard", orchard())
    catalog = catalog_of(world, workers=_Recording(late=True))
    written = document({"a": [TALL], "b": [HEAVY]})
    _, found = _within(RECORD_SECONDS + 3.0, catalog, {"document": written})
    [refusal] = refused(found)
    assert refusal.limit == Limit(name="tool_seconds", max=30)
    checks = validated(catalog_of(world), {"document": written}).cohorts
    assert [check.status for check in checks] == ["not_issued", "not_issued"]
    assert _texts(world) == 0


class _Stopped(Workers):
    """Workers whose run the caller's deadline stops."""

    def __init__(self) -> None:
        super().__init__(QueryLimits())

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        raise CallerDeadline


def test_a_run_the_call_s_deadline_stops_is_refused_naming_tool_seconds(
    world: World, orchard: Orchard
) -> None:
    """No limit of the worker's was hit, so none is named: the call's own is."""
    world.publish("orchard", orchard())
    _, found = _within(20, catalog_of(world, workers=_Stopped()), {"document": document()})
    [refusal] = refused(found)
    assert refusal.limit == Limit(name="tool_seconds", max=30)
    assert _texts(world) == 0


class _Withdrawing(Workers):
    """Workers that withdraw a release once they have run the queries."""

    def __init__(self, world: World, dataset: str) -> None:
        super().__init__(QueryLimits())
        self.world = world
        self.dataset = dataset

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        rows = super().run(paths, queries, ends=ends)
        self.world.store.withdraw(self.dataset, 1, "operator:Ada")
        return rows


def test_a_release_withdrawn_during_a_call_refuses_it_and_it_records_nothing(
    world: World, orchard: Orchard
) -> None:
    world.publish("orchard", orchard())
    world.publish("grove", orchard())
    world.reimport("grove", orchard(25))
    written = document({"a": [TALL]})
    written["cohorts"]["b"] = {"all": [TALL], "dataset": "grove@1"}
    catalog = catalog_of(world, workers=_Withdrawing(world, "grove"))
    [refusal] = refused(answer(catalog, "count_cohort", {"document": written}))
    assert refusal.code == RefusalCode.RELEASE_WITHDRAWN
    [check] = validated(catalog_of(world), {"document": document({"a": [TALL]})}).cohorts
    assert check.status == "not_issued"
    assert _texts(world) == 0


class _Erasing(Workers):
    """Workers that erase a tree of an older release once they have run the queries."""

    def __init__(self, world: World, dataset: str, key: str) -> None:
        super().__init__(QueryLimits())
        self.world = world
        self.dataset = dataset
        self.key = key

    def run(
        self, paths: Sequence[str], queries: Sequence[Query], *, ends: float | None = None
    ) -> list[Rows]:
        rows = super().run(paths, queries, ends=ends)
        erase(self.world.store, self.dataset, "trees", [self.key], "operator:Ada")
        return rows


def test_an_erasure_during_a_call_refuses_it_and_it_records_nothing(
    world: World, orchard: Orchard
) -> None:
    """The erasure leaves the release counted alone, but it ran over the log before the call
    recorded, so the call records nothing (D290)."""
    world.publish("orchard", orchard(25))
    world.reimport("orchard", orchard())
    written = document(notes="tree25 among them")
    catalog = catalog_of(world, workers=_Erasing(world, "orchard", "tree25"))
    [refusal] = refused(answer(catalog, "count_cohort", {"document": written}))
    assert refusal.code == RefusalCode.RELEASE_WITHDRAWN
    [check] = validated(catalog_of(world), {"document": document()}).cohorts
    assert check.status == "not_issued"
    assert _texts(world) == 0
    [named] = counted(catalog_of(world), written).counts
    assert named.count.issuance.id is not None


def test_a_full_log_refuses_counts_until_an_operator_prunes_it(
    world: World, orchard: Orchard
) -> None:
    """Distinct cohorts, each a derivation of its own, fill the log until it refuses the next
    count, naming ``log_bytes``; pruning frees all they hold, and counting works again (D300)."""
    world.publish("orchard", orchard())
    log = world.store.derivations
    log.limits = LogLimits(log_bytes=MIN_LOG_BYTES)
    catalog = catalog_of(world)
    ids: list[str] = []
    for part in range(10):
        keys = [f"key{index:06d}" for index in range(9_000 * part, 9_000 * (part + 1))]
        written = document({"c": [{"kind": "value", "column": "trees.tree_id", "values": keys}]})
        found = answer(catalog, "count_cohort", {"document": written})
        if not isinstance(found, CohortCounts):
            [refusal] = refused(found)
            break
        ids += [named.count.id for named in found.counts]
    else:
        raise AssertionError("the log never filled")
    assert refusal.limit == Limit(name="log_bytes", max=MIN_LOG_BYTES)
    assert len(ids) >= 2
    assert log.usage() == log.measured() <= MIN_LOG_BYTES
    assert log.prune("9999-01-01T00:00:00Z") == len(ids)
    assert log.usage() == log.measured() == 0
    assert all(log.derivation(identifier) is None for identifier in ids)
    [named] = counted(catalog, document()).counts
    assert log.issuances(named.count.id) == [named.count.issuance.id]
