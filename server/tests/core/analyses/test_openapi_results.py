"""Result envelopes against the OpenAPI document (SPEC §12.4, D416; ``api.openapi``).

The golden envelopes (``golden/results.json``, run by the reference evaluator over the shop) and
two synthetic ones, as their JSON goes over the wire:

- **the agreement test**, a backstop: each envelope and its perturbations (at every distinct
  generalized position of the golden ones, at spread positions of the synthetic ones) are judged
  alike by the builder's schema (``result.schema.json``, MCP's ``outputSchema``) and the
  document's ``ResultEnvelope``. Its blind spots: a rewrite that changes only what no instance or
  perturbation reaches (a type R2 gives a member no fixture's value matches, a condition moved
  whose branch no fixture takes). The synthetic envelopes reach two of them (a pack's caveat
  code, ``demo.SOMETHING``, which only the pattern member of ``Caveat.code`` takes; an envelope
  over a draft release, perturbed to a cache hit), and the structural tests of
  ``api/test_openapi.py`` (R2's parent type, the never-split comparison) carry the class;
- **the instance oracle**: every number of an envelope lands on a position marked
  ``x-aibi-server-number`` or in a response's ``x-aibi-json`` value (the web client's rule for
  numbers it may not compute with, M5.1c-1);
- **the web client's fixture**: ``web/tests/fixtures/envelopes.json``, which the web package's
  gate compiles as ``ResultEnvelope`` literals against the generated types (D419) and walks with
  the typed answers' seam (D426), is what ``scripts/export_envelopes.py`` writes from these
  envelopes now (``AIBI_WRITE_WEB_FIXTURES=1`` writes it from this test too);
- **the orchard's answers**: ``web/tests/fixtures/orchard-answers.json``, a second writer here,
  holds the server's own answers, as the text it writes, for the seven public tools the web calls
  (D426's seam) over the orchard (``tests/core/catalog/world.py``'s CSV files, imported by the
  core's file importer, uncurated): each answer and two refusals, which the web package's tests
  decode, walk, hold to their ``Boxed`` types and scan for a raw string; then four answers the
  catalogue and dataset screens need (D431), from a world of their own (the orchard and a second
  dataset): a first page of the catalogue with its ``next_offset``, a dataset's first three
  columns with its ``columns_next``, and both tools under a disclosure floor of 50 (the trees'
  40 rows suppressed), appended after the thirteen, which stay as they were. The state is made
  with a clock and issuance ids that are the same on every run; a change to what the server
  answers fails here until the file is written again (from ``server/``:
  ``AIBI_WRITE_WEB_FIXTURES=1 uv run pytest tests/core/analyses/test_openapi_results.py``).
"""

import copy
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
from tests.core.catalog.world import World, orchard_files

from aibi.core.api.errors import refused
from aibi.core.catalog.service import Catalog
from aibi.core.catalog.tools import BY_NAME
from aibi.core.engine.worker import Workers
from aibi.core.schema.export import result_schema
from aibi.core.schema.limits import QueryLimits
from aibi.core.store import derivations
from aibi.core.store.store import Store

GOLDEN = Path(__file__).parent / "golden" / "results.json"
EXPORT = Path(__file__).resolve().parents[3] / "scripts" / "export_envelopes.py"
SYNTHETIC_CAP = 40
"""Positions perturbed in each synthetic envelope; the golden ones are perturbed at every distinct
generalized position (``OpenApi.agreement``)."""


@pytest.fixture(scope="module")
def envelopes(
    analyse: Callable[..., list[Any]], shop: Callable[..., Any]
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for name, case in json.loads(GOLDEN.read_text(encoding="utf-8")).items():
        [analysed] = analyse(
            case["document"], shop(**case.get("shop", {})), floor=case.get("floor")
        )
        found[name] = json.loads(analysed.result.model_dump_json())
    return found


def pack_caveat(envelope: dict[str, Any]) -> dict[str, Any]:
    """An envelope with a pack's caveat, ``demo.SOMETHING``."""
    changed = copy.deepcopy(envelope)
    changed["caveats"].append(
        {
            "code": "demo.SOMETHING",
            "severity": "warn",
            "message": [{"text": "A pack's caveat"}],
            "affects": ["/values"],
        }
    )
    return changed


def over_a_draft(envelope: dict[str, Any], *, cache_hit: bool) -> dict[str, Any]:
    """An envelope over a draft release, a cache hit or not."""
    changed = copy.deepcopy(envelope)
    for release in changed["derivation"]["releases"]:
        release.update(label="draft", status="draft")
    changed["issuance"]["cache_hit"] = cache_hit
    return changed


def test_the_golden_envelopes_are_the_ones_the_document_describes(
    envelopes: dict[str, dict[str, Any]],
) -> None:
    assert len(envelopes) == 19
    builder = jsonschema.Draft202012Validator(result_schema())
    for name, envelope in envelopes.items():
        assert builder.is_valid(envelope), name


def test_the_builder_and_the_document_judge_every_envelope_alike(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    """Each envelope, and one change (four kinds) at every distinct generalized position of any:
    a position's shape, a list index being ``[]``, is perturbed once, in the smallest envelope
    that has it."""
    assert openapi.agreement(result_schema(), "ResultEnvelope", envelopes) == []


def test_the_builder_and_the_document_judge_the_synthetic_envelopes_alike(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    base = envelopes["two_tiers"]
    builder = result_schema()
    validator = jsonschema.Draft202012Validator(builder)
    caveat, draft = pack_caveat(base), over_a_draft(base, cache_hit=False)
    assert validator.is_valid(caveat)
    assert validator.is_valid(draft)
    assert not validator.is_valid(over_a_draft(base, cache_hit=True))
    found: list[str] = []
    for instance in (caveat, draft, over_a_draft(base, cache_hit=True)):
        found += openapi.disagreements(builder, "ResultEnvelope", instance, SYNTHETIC_CAP)
    assert found == []


def test_every_number_of_an_envelope_lands_on_a_marked_position(
    openapi: Any, envelopes: dict[str, dict[str, Any]]
) -> None:
    instances = [*envelopes.values(), pack_caveat(envelopes["two_tiers"])]
    checked = 0
    for envelope in instances:
        assert openapi.unmarked_numbers(envelope, "ResultEnvelope") == []
        checked += json.dumps(envelope).count(":")
    assert checked > 1000


def _export() -> ModuleType:
    """``scripts/export_envelopes.py`` as a module, by its path (it is no package)."""
    spec = importlib.util.spec_from_file_location("export_envelopes", EXPORT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def test_the_web_client_s_fixture_is_the_golden_envelopes(
    envelopes: dict[str, dict[str, Any]],
) -> None:
    """A changed envelope (a member, a type, a new golden case) fails here until the fixture the
    web client's type gate compiles is written again (``scripts/export_envelopes.py``, or this test
    with ``AIBI_WRITE_WEB_FIXTURES=1``)."""
    script = _export()
    assert script.GOLDEN == GOLDEN
    assert list(envelopes) == list(json.loads(GOLDEN.read_text(encoding="utf-8")))
    rendered = script.render(envelopes)
    if os.environ.get("AIBI_WRITE_WEB_FIXTURES") == "1":
        script.TARGET.write_text(rendered, encoding="utf-8")
    assert script.TARGET.read_text(encoding="utf-8") == rendered


ORCHARD_ANSWERS = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "orchard-answers.json"
)
"""The orchard's answers, for the web client's typed answers (D426)."""

_COHORTS = {
    "apple": {"all": [{"kind": "value", "column": "trees.variety", "values": ["apple"]}]},
    "all_trees": {"all": []},
    "pear": {"all": [{"kind": "value", "column": "trees.variety", "values": ["pear"]}]},
}


def _document(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": _COHORTS,
        "views": [view],
    }


_DISTRIBUTION = _document(
    {
        "analysis": "summary.distribution",
        "cohorts": ["apple", "all_trees"],
        "params": {"columns": [{"column": "trees.height_m"}, {"column": "trees.variety"}]},
    }
)
_MEMBERS = _document({"analysis": "summary.members", "cohorts": ["apple"], "params": {}})
_EXISTENCE = _document(
    {
        "analysis": "compare.existence",
        "cohorts": ["apple", "pear"],
        "params": {
            "predicates": [
                {
                    "kind": "exists",
                    "table": "harvests",
                    "where": [{"kind": "value", "column": "harvests.kg", "range": {"gte": 15}}],
                }
            ]
        },
    }
)
_WRONG = _document(
    {
        "analysis": "summary.distribution",
        "cohorts": ["apple"],
        "params": {"columns": ["trees.height_m"]},
    }
)


def _answer(world: World, catalog: Any, operation: str, body: Any) -> dict[str, Any]:
    """One answer as the HTTP route writes it (``api.tools``): the output's JSON, or the
    refusals with the first one's status and its headers (``errors.refused``)."""
    found = world.tool(catalog, operation, body)
    if isinstance(found, list):
        response = refused(found)
        headers = {name: value for name, value in response.headers.items() if name == "retry-after"}
        text = bytes(response.body).decode("utf-8")
        return {
            "operation": operation,
            "status": response.status_code,
            "headers": headers,
            "body": text,
        }
    return {"operation": operation, "status": 200, "headers": {}, "body": found.model_dump_json()}


def _orchard_answers(world: World) -> list[dict[str, Any]]:
    world.publish("orchard", orchard_files(rows=40, harvests=60))
    catalog = Catalog(world.store, workers=Workers(QueryLimits()))
    answers = [
        _answer(world, catalog, "search_catalog", {}),
        _answer(world, catalog, "describe_dataset", {"dataset": "orchard"}),
        _answer(world, catalog, "list_analyses", {"dataset": "orchard"}),
        _answer(world, catalog, "validate_document", {"document": _DISTRIBUTION}),
        _answer(world, catalog, "validate_document", {"document": _WRONG}),
        _answer(world, catalog, "count_cohort", {"document": {**_DISTRIBUTION, "views": []}}),
        _answer(world, catalog, "run_analysis", {"document": _DISTRIBUTION}),
        _answer(world, catalog, "run_analysis", {"document": _MEMBERS}),
        _answer(world, catalog, "run_analysis", {"document": _EXISTENCE}),
    ]
    result = json.loads(answers[6]["body"])["results"][0]
    answers.append(_answer(world, catalog, "explain", {"id": result["derivation"]["id"]}))
    answers.append(_answer(world, catalog, "explain", {"id": result["issuance"]["id"]}))
    answers.append(_answer(world, catalog, "describe_dataset", {"dataset": "no_such_dataset"}))
    answers.append(_answer(world, catalog, "run_analysis", {"document": _WRONG}))
    return answers


def _screen_answers(world: World) -> list[dict[str, Any]]:
    """The four answers of D431's screens, from a world of their own: the orchard and a second
    small dataset published, the catalogue's first page of one hit (``next_offset``), the
    orchard's first three columns (``columns_next``), and the catalogue and the orchard under a
    disclosure floor of 50 (the trees' 40 rows suppressed, *k* shown)."""
    world.publish("orchard", orchard_files(rows=40, harvests=60))
    world.publish("walnuts", {"walnuts.csv": b"walnut_id,kg\nw1,3\nw2,4\n"})
    catalog = Catalog(world.store, workers=Workers(QueryLimits()))
    floored = Catalog(world.store, workers=Workers(QueryLimits()), floor=50)
    return [
        _answer(world, catalog, "search_catalog", {"limit": 1}),
        _answer(world, catalog, "describe_dataset", {"dataset": "orchard", "columns_limit": 3}),
        _answer(world, floored, "search_catalog", {}),
        _answer(world, floored, "describe_dataset", {"dataset": "orchard"}),
    ]


def _builder(
    first: list[dict[str, Any]], second: list[dict[str, Any]] | None, columns: list[str]
) -> dict[str, Any]:
    """A document as the web's document seam writes it (D432's ``documentOf``): cohorts
    ``first`` and, when given, ``second``, and one ``summary.distribution`` view of ``columns``."""
    cohorts: dict[str, Any] = {"first": {"all": first}}
    if second is not None:
        cohorts["second"] = {"all": second}
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": "trees",
        "cohorts": cohorts,
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": list(cohorts),
                "params": {"columns": [{"column": column} for column in columns]},
            }
        ],
    }


_APPLE = [{"kind": "value", "column": "trees.variety", "values": ["apple"]}]
_BUILT = _builder(_APPLE, [], ["trees.height_m", "trees.variety"])
_BUILT_CATEGORY = _builder(_APPLE, None, ["trees.variety"])
_BUILT_NUMBER = _builder(_APPLE, None, ["trees.height_m"])
_BUILT_STRING = _builder(_APPLE, None, ["trees.tree_id"])


def _asked(world: World, catalog: Any, operation: str, document: dict[str, Any]) -> dict[str, Any]:
    """An answer of the builder's with the request it answers (``request``: the body as the web
    page sends it, ``JSON.stringify`` of ``{document}``), so that the web's cells hold the page's
    body to the request the server answered."""
    body = {"document": document}
    return {
        **_answer(world, catalog, operation, body),
        "request": json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    }


def _builder_answers(world: World) -> list[dict[str, Any]]:
    """The eight answers of the builder's screens (D433), from a world of their own, each with its
    request: the two-cohort document validated, counted and run; its category view validated and
    counted under a floor of 50 (every count suppressed); a numeric view validated and counted under
    that floor; and a string view column validated."""
    world.publish("orchard", orchard_files(rows=40, harvests=60))
    catalog = Catalog(world.store, workers=Workers(QueryLimits()))
    floored = Catalog(world.store, workers=Workers(QueryLimits()), floor=50)
    return [
        _asked(world, catalog, "validate_document", _BUILT),
        _asked(world, catalog, "count_cohort", _BUILT),
        _asked(world, floored, "validate_document", _BUILT_CATEGORY),
        _asked(world, floored, "count_cohort", _BUILT_CATEGORY),
        _asked(world, catalog, "run_analysis", _BUILT),
        _asked(world, floored, "validate_document", _BUILT_NUMBER),
        _asked(world, floored, "count_cohort", _BUILT_NUMBER),
        _asked(world, catalog, "validate_document", _BUILT_STRING),
    ]


def test_the_web_client_s_orchard_answers_are_the_server_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchard's answers as the server writes them, on a store whose clock ticks from a fixed
    start and whose issuance ids are made from a fixed clock and fixed bits (``derivations``'
    one source of ULIDs), so that the file is the same on every run."""
    ticks = iter(range(1_767_225_600_000, 1_767_225_600_000 + 10_000))
    monkeypatch.setattr(derivations._ULIDS, "_clock", lambda: next(ticks) * 1_000_000)
    monkeypatch.setattr(derivations._ULIDS, "_entropy", lambda size: bytes(size))
    monkeypatch.setattr(derivations._ULIDS, "_last", -1)
    (tmp_path / "imports").mkdir()
    clock = iter(range(10_000))
    world = World(
        tmp_path,
        Store(
            tmp_path / "data",
            clock=lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=next(clock)),
        ),
    )
    try:
        answers = _orchard_answers(world)
    finally:
        world.store.close()
    second = tmp_path / "screens"
    (second / "imports").mkdir(parents=True)
    later = iter(range(10_000))
    screens = World(
        second,
        Store(
            second / "data",
            clock=lambda: datetime(2026, 2, 1, tzinfo=UTC) + timedelta(seconds=next(later)),
        ),
    )
    try:
        answers += _screen_answers(screens)
    finally:
        screens.store.close()
    third = tmp_path / "builder"
    (third / "imports").mkdir(parents=True)
    latest = iter(range(10_000))
    builder = World(
        third,
        Store(
            third / "data",
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC) + timedelta(seconds=next(latest)),
        ),
    )
    try:
        answers += _builder_answers(builder)
    finally:
        builder.store.close()
    rendered = json.dumps({"answers": answers}, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("AIBI_WRITE_WEB_FIXTURES") == "1":
        ORCHARD_ANSWERS.write_text(rendered, encoding="utf-8")
    assert ORCHARD_ANSWERS.read_text(encoding="utf-8") == rendered


def test_the_orchard_answers_hold_what_the_web_client_s_tests_need() -> None:
    """Every typed operation answers, the first validation is valid and the second is not (a 200
    either way), the three analyses run, and two refusals: a 404 and a 422."""
    answers = json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8"))["answers"]
    by = [(found["operation"], found["status"]) for found in answers]
    assert by == [
        ("search_catalog", 200),
        ("describe_dataset", 200),
        ("list_analyses", 200),
        ("validate_document", 200),
        ("validate_document", 200),
        ("count_cohort", 200),
        ("run_analysis", 200),
        ("run_analysis", 200),
        ("run_analysis", 200),
        ("explain", 200),
        ("explain", 200),
        ("describe_dataset", 404),
        ("run_analysis", 422),
        ("search_catalog", 200),
        ("describe_dataset", 200),
        ("search_catalog", 200),
        ("describe_dataset", 200),
        ("validate_document", 200),
        ("count_cohort", 200),
        ("validate_document", 200),
        ("count_cohort", 200),
        ("run_analysis", 200),
        ("validate_document", 200),
        ("count_cohort", 422),
        ("validate_document", 200),
    ]
    assert [json.loads(answers[n]["body"])["valid"] for n in (3, 4)] == [True, False]
    built, counted, category, floored, ran, numeric, refused_count, text = (
        json.loads(answers[n]["body"]) for n in range(17, 25)
    )
    requests = [json.loads(answers[n]["request"])["document"] for n in range(17, 25)]
    assert requests == [
        _BUILT,
        _BUILT,
        _BUILT_CATEGORY,
        _BUILT_CATEGORY,
        _BUILT,
        _BUILT_NUMBER,
        _BUILT_NUMBER,
        _BUILT_STRING,
    ]
    assert all("request" not in answers[n] for n in range(17))
    population = [found["count"]["population"] for found in counted["counts"]]
    assert built["valid"] is True
    assert category["valid"] is True
    assert [(p["n_true"], p["n_false"], p["n_unknown"]) for p in population] == [
        (13, 27, 0),
        (40, 0, 0),
    ]
    [suppressed] = [found["count"]["population"] for found in floored["counts"]]
    assert suppressed["n_true"] is None
    assert len(suppressed["suppressed"]) == 6
    assert len(ran["results"]) == 1
    bins = "/views/0/params/columns/0/bins"
    assert numeric["valid"] is False
    assert [(r["code"], r["path"]) for r in numeric["refusals"]] == [("MISSING_MEMBER", bins)]
    assert [(r["code"], r["path"]) for r in refused_count["refusals"]] == [("MISSING_MEMBER", bins)]
    assert [(r["code"], r["path"]) for r in text["refusals"]] == [
        ("NOT_SUPPORTED", "/views/0/params/columns/0/column")
    ]
    page, columns, hits, described = (json.loads(answers[n]["body"]) for n in range(13, 17))
    assert (len(page["hits"]), page["next_offset"], page["total"]) == (1, 1, 2)
    assert (len([c for t in columns["tables"] for c in t["columns"]]), columns["columns_next"]) == (
        3,
        3,
    )
    assert "next_offset" not in hits
    assert "columns_next" not in described
    assert [hit["disclosure"]["min_cell_count"] for hit in hits["hits"]] == [50, 50]
    assert described["disclosure"]["min_cell_count"] == 50
    rows = {t["rows"]["reference"].split("/")[-2]: t["rows"]["count"] for t in described["tables"]}
    assert rows == {"harvests": 60, "trees": None}
    assert {found["operation"] for found in answers} == {
        "search_catalog",
        "describe_dataset",
        "list_analyses",
        "validate_document",
        "count_cohort",
        "run_analysis",
        "explain",
    }
    assert all(name in BY_NAME for name, _ in by)


def test_the_web_client_s_fixture_is_rendered_one_envelope_a_line() -> None:
    script = _export()
    text = script.render({"a": {"n": 1.10, "s": "é"}, "b": [1, None]})
    assert text == '{\n"a": {"n":1.1,"s":"\\u00e9"},\n"b": [1,null]\n}\n'
    assert json.loads(text) == {"a": {"n": 1.1, "s": "é"}, "b": [1, None]}


def test_the_validator_cache_is_by_a_schema_s_content_not_its_identity(openapi: Any) -> None:
    """A schema that is a temporary (or a copy) gets its own validator, never the one of an object
    that had its ``id``."""
    first = openapi._of({"anyOf": [{"type": "string"}]})
    assert openapi._of({"anyOf": [{"type": "string"}]}) is first
    other = openapi._of({"anyOf": [{"type": "integer"}]})
    assert other is not first
    assert first.is_valid("a")
    assert not first.is_valid(1)
    assert other.is_valid(1)
    assert not other.is_valid("a")
