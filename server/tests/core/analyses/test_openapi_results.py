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
  holds the server's own answers, as the text it writes, for the eight public tools the web calls
  (D426's seam) over the orchard (``tests/core/catalog/world.py``'s CSV files, imported by the
  core's file importer, uncurated): each answer and two refusals, which the web package's tests
  decode, walk, hold to their ``Boxed`` types and scan for a raw string; then four answers the
  catalogue and dataset screens need (D431), from a world of their own (the orchard and a second
  dataset): a first page of the catalogue with its ``next_offset``, a dataset's first three
  columns with its ``columns_next``, and both tools under a disclosure floor of 50 (the trees'
  40 rows suppressed), appended after the thirteen, which stay as they were; then the builder's
  eight (D433), and the ten whose ``values`` the web's values seam reads (D434,
  ``_VALUES_DOCUMENTS``: runs over the orchard and ``fixtures/hostile_values`` under no floor and
  floors of 5 and 50, and one refusal), then the ten of ``describe_column`` (D442,
  ``_COLUMNS_ASKED``: a column of each distribution's shape, under no floor and floors of 5 and 50,
  the hostile values' column with a curated hostile label, and the last, categories under a floor
  of 5) and the four appended after them for the column screen (D443, ``_COLUMNS_APPENDED``: the
  pooled rows under floors of 14 and 21, then a declared date range curated and its histogram under
  a floor of 5 and under none), each from a world of its own. The
  file records each section's ``start`` and ``end`` (``sections``), which its readers use instead of
  an absolute index (a section appended moves none). The state is made
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

from aibi.core.api import openapi as openapi_module
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


HOSTILE_VALUES = Path(__file__).resolve().parents[4] / "fixtures" / "hostile_values" / "items.csv"
"""Category values that are hostile texts (D434): markup, a right-to-left override, a closing
``bdi``, 5,000 characters, zero-width characters and a script URL, each four times, and one
empty."""

HOSTILE_TEXTS = (
    "<img src=x onerror=alert(1)>",
    "‮txt.exe",
    "</bdi><b>bold</b><bdi>",
    "x" * 5000,
    "a​b⁠c",
    "javascript:alert(1)",
)


def _distribution(
    cohorts: dict[str, list[dict[str, Any]]],
    columns: list[dict[str, Any]],
    *,
    dataset: str = "orchard",
    unit: str = "trees",
) -> dict[str, Any]:
    """A document of one ``summary.distribution`` view of ``columns`` over ``cohorts``, in order."""
    return {
        "aibi": "1",
        "dataset": dataset,
        "unit": unit,
        "cohorts": {name: {"all": leaves} for name, leaves in cohorts.items()},
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": list(cohorts),
                "params": {"columns": columns},
            }
        ],
    }


_ZZZ = [{"kind": "value", "column": "trees.variety", "values": ["zzz"]}]
_PEAR_PLUM = [{"kind": "value", "column": "trees.variety", "values": ["pear", "plum"]}]
_HEIGHT = {"column": "trees.height_m"}
_BINNED = {"column": "trees.height_m", "bins": [2, 4, 6, 8, 10]}
_VARIETY = {"column": "trees.variety"}
_VALUES_DOCUMENTS = (
    # (a), (b): the builder's two-cohort document, run and run again (a cache hit).
    (None, _BUILT),
    (None, _BUILT),
    # (c): an empty cohort.
    (None, _distribution({"first": _ZZZ}, [_HEIGHT, _VARIETY])),
    # (d): floor 5, an empty cohort beside apple.
    (5, _distribution({"first": _APPLE, "second": _ZZZ}, [_VARIETY])),
    # (e): floor 50, every count suppressed.
    (50, _BUILT_CATEGORY),
    # (f): a view that counts rows of another table.
    (None, _distribution({"first": []}, [{"column": "harvests.grade", "count": "rows"}])),
    # (g): hostile category values.
    (
        None,
        _distribution(
            {"first": []}, [{"column": "items.kind"}], dataset="hostile_values", unit="items"
        ),
    ),
    # (h), (h'): floor 5, a numeric column with bins (an agent's document), apple beside pear and
    # plum, then beside all trees.
    (5, _distribution({"first": _APPLE, "second": _PEAR_PLUM}, [_BINNED, _VARIETY])),
    (5, _distribution({"first": _APPLE, "second": []}, [_BINNED, _VARIETY])),
    # (i): floor 5, a numeric column without bins on a column with no declared range: refused.
    (5, _distribution({"first": _APPLE}, [_HEIGHT])),
    # (j): the columns written in an order that is not sorted, of distinct kinds.
    (None, _distribution({"first": _APPLE, "second": []}, [_VARIETY, _HEIGHT])),
)
"""The values answers' documents (D434), each with its disclosure floor."""

HOSTILE_LABEL = "<img src=x onerror=alert(1)> \u202etxt.exe </bdi>"
"""A curated column label of markup, a bidi override and a closing isolate (A6, D442)."""

_COLUMNS_ASKED: tuple[tuple[int | None, str, str], ...] = (
    (None, "orchard", "trees.variety"),  # categories
    (None, "orchard", "trees.height_m"),  # a histogram from the data, numeric edges
    (None, "orchard", "trees.planted"),  # a histogram with date edges (Data)
    (None, "orchard", "trees.tags"),  # a list column: categories by membership
    (None, "orchard", "trees.tree_id"),  # an identifier: no distribution
    (None, "orchard", "harvests.kg"),  # another table's column
    (5, "orchard", "trees.height_m"),  # under a floor of 5: no declared range
    (50, "orchard", "trees.variety"),  # under a floor of 50: suppressed
    (None, "hostile_values", "items.kind"),  # hostile category values and a curated label
    # Categories under a floor of 5: the counts a floor shows, beside the references that name it.
    # A floor shows bins only for a column with a declared range, and none of the orchard's numeric
    # or date columns declares one (``no_declared_range``, as for ``trees.height_m`` above): the
    # histograms under a floor are ``_COLUMNS_APPENDED``'s, after a curation.
    (5, "orchard", "trees.variety"),
)
"""describe_column's answers (D442), each with its disclosure floor, dataset and column."""

PLANTED_RANGE: dict[str, Any] = {"min": "2010-01-01", "max": "2011-01-01"}
"""The range curated on ``trees.planted`` before ``_COLUMNS_APPENDED``'s last two (D443)."""

_COLUMNS_APPENDED: tuple[tuple[int | None, str, str], ...] = (
    (14, "orchard", "trees.variety"),  # pear's 14 shown, apple's and plum's 13 pooled: 26 (D271)
    (21, "orchard", "trees.tags"),  # a list column: no category shown, its pooled row suppressed
    (5, "orchard", "trees.planted"),  # the declared range: merged bins, no minimum or maximum
    (None, "orchard", "trees.planted"),  # the declared range's bins, with the minimum and maximum
)
"""The column screen's answers (D443), appended after ``_COLUMNS_ASKED``'s ten, so that none of
them moves: a curation publishes a release, which would move every orchard manifest recorded
before it. ``PLANTED_RANGE`` is curated after the first two, so the last two are of release 2."""


def _column_answers(world: World) -> list[dict[str, Any]]:
    """describe_column's answers (D442, ``_COLUMNS_ASKED``, then D443's ``_COLUMNS_APPENDED``), from
    a world of their own: the orchard and ``fixtures/hostile_values`` published, ``items.kind``
    curated to a hostile label, and ``trees.planted``'s range curated before the last two."""
    world.publish("orchard", orchard_files(rows=40, harvests=60))
    world.publish("hostile_values", {"items.csv": HOSTILE_VALUES.read_bytes()})
    world.curate(
        "hostile_values",
        {"op": "set", "descriptor": "items.kind", "pointer": "/label", "value": HOSTILE_LABEL},
    )
    catalogs = {
        floor: Catalog(world.store, workers=Workers(QueryLimits()), floor=floor)
        for floor in (None, 5, 14, 21, 50)
    }
    found = [
        _answer(world, catalogs[floor], "describe_column", {"dataset": dataset, "column": column})
        for floor, dataset, column in (*_COLUMNS_ASKED, *_COLUMNS_APPENDED[:2])
    ]
    world.curate(
        "orchard",
        {
            "op": "set",
            "descriptor": "trees.planted",
            "pointer": "/fields/range",
            "value": PLANTED_RANGE,
        },
    )
    found.extend(
        _answer(world, catalogs[floor], "describe_column", {"dataset": dataset, "column": column})
        for floor, dataset, column in _COLUMNS_APPENDED[2:]
    )
    return found


_SECTIONS = ("orchard", "screens", "builder", "values", "columns")
"""The file's sections, in order, each from a world of its own: its ``sections`` member gives each
one's ``start`` and ``end`` (exclusive), so that no reader pins an absolute index."""


def _section(answers_file: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The answers of one section of the file, by the bounds it records."""
    bounds = answers_file["sections"][name]
    return answers_file["answers"][bounds["start"] : bounds["end"]]


def _values_answers(world: World) -> list[dict[str, Any]]:
    """The answers whose ``values`` the web's values seam reads (D434), from a world of their own:
    the orchard and ``fixtures/hostile_values`` published, and ``_VALUES_DOCUMENTS`` run in
    order."""
    world.publish("orchard", orchard_files(rows=40, harvests=60))
    world.publish("hostile_values", {"items.csv": HOSTILE_VALUES.read_bytes()})
    catalogs = {
        floor: Catalog(world.store, workers=Workers(QueryLimits()), floor=floor)
        for floor in (None, 5, 50)
    }
    return [
        _answer(world, catalogs[floor], "run_analysis", {"document": document})
        for floor, document in _VALUES_DOCUMENTS
    ]


def test_the_web_client_s_orchard_answers_are_the_server_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchard's answers as the server writes them, on a store whose clock ticks from a fixed
    start and whose issuance ids are made from a fixed clock and fixed bits (``derivations``'
    one source of ULIDs), so that the file is the same on every run."""
    sections: dict[str, dict[str, int]] = {}

    def made(name: str, found: list[dict[str, Any]]) -> None:
        start = sum(bounds["end"] - bounds["start"] for bounds in sections.values())
        sections[name] = {"start": start, "end": start + len(found)}
        answers.extend(found)

    answers: list[dict[str, Any]] = []
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
        made("orchard", _orchard_answers(world))
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
        made("screens", _screen_answers(screens))
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
        made("builder", _builder_answers(builder))
    finally:
        builder.store.close()
    fourth = tmp_path / "values"
    (fourth / "imports").mkdir(parents=True)
    last = iter(range(10_000))
    values = World(
        fourth,
        Store(
            fourth / "data",
            clock=lambda: datetime(2026, 4, 1, tzinfo=UTC) + timedelta(seconds=next(last)),
        ),
    )
    try:
        made("values", _values_answers(values))
    finally:
        values.store.close()
    fifth = tmp_path / "columns"
    (fifth / "imports").mkdir(parents=True)
    after = iter(range(10_000))
    columns = World(
        fifth,
        Store(
            fifth / "data",
            clock=lambda: datetime(2026, 5, 1, tzinfo=UTC) + timedelta(seconds=next(after)),
        ),
    )
    try:
        made("columns", _column_answers(columns))
    finally:
        columns.store.close()
    assert tuple(sections) == _SECTIONS
    rendered = (
        json.dumps({"answers": answers, "sections": sections}, indent=2, ensure_ascii=False) + "\n"
    )
    if os.environ.get("AIBI_WRITE_WEB_FIXTURES") == "1":
        ORCHARD_ANSWERS.write_text(rendered, encoding="utf-8")
    assert ORCHARD_ANSWERS.read_text(encoding="utf-8") == rendered


def test_the_orchard_answers_hold_what_the_web_client_s_tests_need() -> None:
    """Every typed operation answers, the first validation is valid and the second is not (a 200
    either way), the three analyses run, and two refusals: a 404 and a 422; the sections the file
    records tile it, in order."""
    written = json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8"))
    answers = written["answers"]
    assert tuple(written["sections"]) == _SECTIONS
    ends = [0, *(bounds["end"] for bounds in written["sections"].values())]
    assert [bounds["start"] for bounds in written["sections"].values()] == ends[:-1]
    assert ends[-1] == len(answers)
    assert [len(_section(written, name)) for name in _SECTIONS] == [13, 4, 8, 11, 14]
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
        *[("run_analysis", 200)] * 9,
        ("run_analysis", 422),
        ("run_analysis", 200),
        *[("describe_column", 200)] * (len(_COLUMNS_ASKED) + len(_COLUMNS_APPENDED)),
    ]
    orchard = _section(written, "orchard")
    assert [json.loads(orchard[n]["body"])["valid"] for n in (3, 4)] == [True, False]
    builder = _section(written, "builder")
    built, counted, category, floored, ran, numeric, refused_count, text = (
        json.loads(found["body"]) for found in builder
    )
    requests = [json.loads(found["request"])["document"] for found in builder]
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
    assert all(
        "request" not in found
        for name in _SECTIONS
        if name != "builder"
        for found in _section(written, name)
    )
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
    page, columns, hits, described = (
        json.loads(found["body"]) for found in _section(written, "screens")
    )
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
        "describe_column",
        "list_analyses",
        "validate_document",
        "count_cohort",
        "run_analysis",
        "explain",
    }
    assert all(name in BY_NAME for name, _ in by)


def test_the_column_answers_hold_what_the_column_screen_needs() -> None:
    """describe_column's answers (D442, ``_COLUMNS_ASKED``): each names the column asked, its table
    and its label, and holds the distribution the next screen reads: categories, a list's
    memberships, a histogram from the data with numeric and with date edges, an identifier's
    none, ``no_declared_range`` under a floor of 5, ``suppressed`` under 50, the hostile values'
    column with its curated hostile label, and categories with their counts under a floor of 5;
    then the column screen's four (D443, ``_COLUMNS_APPENDED``): a pooled row shown, a list's
    pooled row suppressed, and a declared range's histogram under a floor of 5 (merged bins, no
    minimum or maximum, D329) and under none (both shown)."""
    written = json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8"))
    found = [json.loads(answer["body"]) for answer in _section(written, "columns")]
    asked = (*_COLUMNS_ASKED, *_COLUMNS_APPENDED)
    assert len(found) == len(asked)
    for body, (floor, dataset, column) in zip(found, asked, strict=True):
        assert (body["dataset"], body["column"], body["table"]) == (
            dataset,
            column,
            column.split(".", 1)[0],
        )
        assert body["label"] == {"data": body["descriptor"]["label"]}
        assert body["descriptor"]["id"] == column
        assert body["disclosure"]["min_cell_count"] == floor
    shapes = [
        (
            body["label"]["data"],
            body["statistics"]["distribution"]["kind"],
            body["statistics"]["distribution"].get("reason"),
            body["statistics"]["distribution"].get("edges_from"),
            body["statistics"]["distribution"].get("multi_membership"),
            body["identifier"],
        )
        for body in found
    ]
    assert shapes == [
        ("variety", "categories", None, None, False, False),
        ("height_m", "histogram", None, "data", None, False),
        ("planted", "histogram", None, "data", None, False),
        ("tags", "categories", None, None, True, False),
        ("tree_id", "none", "identifier", None, None, True),
        ("kg", "histogram", None, "data", None, False),
        ("height_m", "none", "no_declared_range", None, None, False),
        ("variety", "none", "suppressed", None, None, False),
        (HOSTILE_LABEL, "categories", None, None, False, False),
        ("variety", "categories", None, None, False, False),
        ("variety", "categories", None, None, False, False),
        ("tags", "categories", None, None, True, False),
        ("planted", "histogram", None, "range", None, False),
        ("planted", "histogram", None, "range", None, False),
    ]
    variety, tags, floored, ranged = (body["statistics"]["distribution"] for body in found[10:])
    assert [one["value"]["data"] for one in variety["categories"]] == ["pear"]
    assert variety["pooled"]["count"] == 26
    assert variety["pooled"]["reference"].endswith("/trees.variety/pooled?floor=14")
    assert tags["categories"] == []
    assert tags["pooled"]["count"] is None
    assert tags["pooled"]["not_estimable"] == {"/count": "suppressed"}
    orchard = [body["release"]["label"] for body in found if body["dataset"] == "orchard"]
    assert orchard == [1] * 11 + [2, 2]
    assert [body["descriptor"]["fields"]["range"] for body in found[12:]] == [PLANTED_RANGE] * 2
    assert "min" not in floored
    assert "max" not in floored
    assert {"min", "max"} <= set(ranged)
    assert len(floored["bins"]) < len(ranged["bins"])
    assert all(
        set(bin_.get(end, {"data": ""})) == {"data"}
        for bin_ in (*floored["bins"], *ranged["bins"])
        for end in ("low", "high")
    )
    # The column screen's notice (D443) says that under a floor describe_column serves the
    # descriptor without its curation evidence (D271's ``_served``): every answer under a floor has
    # none, every other one has some.
    evidence = [
        any("evidence" in member for member in body["descriptor"].get("curation", {}).values())
        for body in found
    ]
    assert evidence == [body["disclosure"]["min_cell_count"] is None for body in found]
    edges = [
        [
            bin_[end]
            for bin_ in body["statistics"]["distribution"]["bins"]
            for end in ("low", "high")
            if end in bin_
        ]
        for body in found[1:3]
    ]
    assert edges[0]
    assert all(isinstance(edge, float) for edge in edges[0])
    assert edges[1]
    assert all(set(edge) == {"data"} for edge in edges[1])


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


def _values_of(answer: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """An answer's one result's values: its positions' columns."""
    [result] = json.loads(answer["body"])["results"]
    return [position["columns"] for position in result["values"]["positions"]]


_NUMBER_POINTERS = ("/mean", "/sd", "/median", "/q1", "/q3", "/min", "/max", "/histogram")


def test_the_values_answers_hold_what_the_values_seam_needs() -> None:
    """The values answers (D434, ``_VALUES_DOCUMENTS``): each ``values`` is valid against the
    component the document publishes for its analysis and version (``x-aibi-values``, an independent
    validator), and each holds exactly the facts the web's cells read of it."""
    found = _section(json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8")), "values")
    assert len(found) == len(_VALUES_DOCUMENTS)
    document: dict[str, Any] = json.loads(json.dumps(openapi_module.document()))
    roots = document["x-aibi-values"]
    ran, again, empty, five, fifty, rows, hostile, binned, merged, refused_, unsorted = found
    ran_ = [answer for answer in found if answer is not refused_]
    results = [json.loads(a["body"])["results"][0] for a in ran_]
    validators = []
    for result in results:
        analysis = result["derivation"]["analysis"]
        root = roots[analysis["id"]][analysis["version"]]
        schema = {"components": document["components"], "$ref": root["$ref"]}
        validators.append(jsonschema.Draft202012Validator(schema))
        assert validators[-1].is_valid(result["values"])
    # The validator's control: a number sent as a string, and a view that gives values, are refused.
    changed = copy.deepcopy(results[0]["values"])
    changed["positions"][0]["columns"][0]["n"] = "12"
    assert not validators[0].is_valid(changed)
    assert not validators[0].is_valid({**results[0]["values"], "view": {"n": 1}})
    floors = [r["derivation"]["disclosure"]["min_cell_count"] for r in results]
    assert floors == [None, None, None, 5, 50, None, None, 5, 5, None]
    # (a), (b): the builder's document, computed and then served from the result cache.
    first, second = results[0]["issuance"], results[1]["issuance"]
    assert (first["cache_hit"], first["values_from"]) == (False, first["id"])
    assert (second["cache_hit"], second["values_from"]) == (True, first["id"])
    assert _values_of(again) == _values_of(ran)
    labels = [
        c["column"] for c in results[0]["derivation"]["document"]["view"]["params"]["columns"]
    ]
    assert labels == ["trees.height_m", "trees.variety"]
    assert [[c["kind"] for c in p] for p in _values_of(ran)] == [["numbers", "categories"]] * 2
    bins = _values_of(ran)[0][0]["histogram"]["bins"]
    assert (bins[0]["low"], bins[-1]["high"]) == (None, None)
    assert all(b["low"] is not None for b in bins[1:])
    assert all(b["high"] is not None for b in bins[:-1])
    # (c): an empty cohort.
    [[numbers, categories]] = _values_of(empty)
    assert numbers["n"] == 0
    assert all(numbers[pointer[1:]] is None for pointer in _NUMBER_POINTERS)
    assert numbers["not_estimable"] == dict.fromkeys(_NUMBER_POINTERS, "no_units")
    assert categories == {"kind": "categories", "categories": []}
    # (d): floor 5, one row that names no value per position, the empty cohort's estimate null.
    [[apple], [nothing]] = _values_of(five)
    assert [len(apple["categories"]), len(nothing["categories"])] == [1, 1]
    [shown], [none] = apple["categories"], nothing["categories"]
    assert (shown["values"], shown["other_values"]) == ([], True)
    assert [shown["proportion"][m] for m in ("numerator", "denominator")] == [13, 13]
    assert (none["values"], none["other_values"]) == ([], True)
    proportion = none["proportion"]
    assert (proportion["estimate"], proportion["numerator"], proportion["denominator"]) == (
        None,
        0,
        0,
    )
    assert proportion["not_estimable"] == {"/estimate": "no_units"}
    # (e): floor 50, the categories and the analysed counts suppressed.
    [[suppressed]] = _values_of(fifty)
    assert suppressed == {
        "kind": "categories",
        "categories": None,
        "not_estimable": {"/categories": "suppressed"},
    }
    [analysed] = results[4]["analysed"]
    assert (analysed["n"], analysed["excluded"], analysed["excluded_units"]) == (None, None, None)
    # (f): rows of another table counted.
    [[counted]] = _values_of(rows)
    assert (counted["kind"], counted["categories"]) == ("category_rows", [])
    assert set(counted["excluded_rows"].values()) == {0}
    [analysed] = results[5]["analysed"]
    assert {k: v for k, v in analysed["excluded"].items() if v} == {"NO_INFORMATION": 40}
    # (g): hostile values come back exactly, from a file that holds them as code points.
    raw = HOSTILE_VALUES.read_bytes().decode("utf-8")
    assert all(point in raw for point in ("\u202e", "\u200b", "\u2060"))
    [[listed]] = _values_of(hostile)
    texts = [row["values"][0]["data"] for row in listed["categories"]]
    assert sorted(texts) == sorted(HOSTILE_TEXTS)
    [analysed] = results[6]["analysed"]
    assert {k: v for k, v in analysed["excluded"].items() if v} == {"NO_INFORMATION": 1}
    # (h): floor 5 with bins: every member of each number column null and suppressed.
    every = (*_NUMBER_POINTERS, "/n", "/q1_bin", "/median_bin", "/q3_bin")
    for [column, _] in _values_of(binned):
        assert all(column[pointer[1:]] is None for pointer in every)
        assert column["not_estimable"] == dict.fromkeys(every, "suppressed")
    for analysed in results[7]["analysed"]:
        assert analysed["variables"][0]["n"] is None
        assert analysed["variables"][1]["n"] is not None
    # (h'): beside all trees, position 1's numbers carry a count, bins and bin indices.
    [[held, _], [numbers, _]] = _values_of(merged)
    assert all(held[pointer[1:]] is None for pointer in every)
    assert (numbers["n"], numbers["histogram"]["edges_from"], numbers["q1_bin"]) == (
        35,
        "params",
        0,
    )
    last = numbers["histogram"]["bins"][-1]
    assert (last["low"], last["high"], last["count"]) == (8, None, 11)
    # (i): a number column with no bins and no declared range is refused.
    assert refused_["status"] == 422
    refusals = json.loads(refused_["body"])["refusals"]
    assert [(r["code"], r["path"]) for r in refusals] == [
        ("MISSING_MEMBER", "/views/0/params/columns/0/bins")
    ]
    # (j): the canonical document keeps the columns in the order written, and the values follow it.
    labels = [
        c["column"] for c in results[9]["derivation"]["document"]["view"]["params"]["columns"]
    ]
    assert labels == ["trees.variety", "trees.height_m"]
    assert [[c["kind"] for c in p] for p in _values_of(unsorted)] == [["categories", "numbers"]] * 2


ARMS_OF = {
    "number": {"numbers", "number_rows"},
    "integer": {"numbers", "number_rows"},
    "category": {"categories", "category_rows", "memberships"},
}
"""The arms a values column may be of, by its column's datatype."""


def test_every_live_label_names_a_column_of_its_arm_s_type() -> None:
    """The labels and the values columns share one order (D434): in every live
    ``summary.distribution`` answer over the orchard, the column the canonical document names at
    ``view.params.columns[j]`` has a datatype (the server's own ``describe_dataset`` answer, the
    orchard's answers' second) whose arms include the kind of the values' column ``j`` at every
    position; and one answer writes its columns in an order that is not sorted, so that a document
    whose columns were reordered apart from the values would fail here."""
    written = json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8"))
    answers = written["answers"]
    described = json.loads(_section(written, "orchard")[1]["body"])
    assert described["dataset"] == "orchard"
    datatype = {c["id"]: c["datatype"] for t in described["tables"] for c in t["columns"]}
    checked, orders = 0, set()
    for answer in answers:
        if answer["operation"] != "run_analysis" or answer["status"] != 200:
            continue
        for result in json.loads(answer["body"])["results"]:
            derivation = result["derivation"]
            if derivation["analysis"]["id"] != "summary.distribution":
                continue
            if [r["dataset"] for r in derivation["releases"]] != ["orchard"]:
                continue
            labels = [c["column"] for c in derivation["document"]["view"]["params"]["columns"]]
            orders.add(labels == sorted(labels))
            for position in result["values"]["positions"]:
                kinds = [column["kind"] for column in position["columns"]]
                assert len(kinds) == len(labels)
                for label, kind in zip(labels, kinds, strict=True):
                    assert kind in ARMS_OF[datatype[label]], (label, kind)
                    checked += 1
    assert orders == {True, False}
    assert checked >= 30


def test_every_live_variable_count_is_its_column_s() -> None:
    """The analysed counts' ``variables`` and the values columns share one order (D434): in every
    live answer with no floor and two columns or more, ``analysed[i].variables[j].n`` is the ``n``
    of the ``numbers`` column at position ``i``, column ``j``, or each category row's
    ``denominator`` there. (The labels' order against the values' is the cell above.)"""
    answers = json.loads(ORCHARD_ANSWERS.read_text(encoding="utf-8"))["answers"]
    checked = 0
    for answer in answers:
        if answer["operation"] != "run_analysis" or answer["status"] != 200:
            continue
        for result in json.loads(answer["body"])["results"]:
            columns = result["derivation"]["document"]["view"]["params"].get("columns", [])
            floor = result["derivation"]["disclosure"]["min_cell_count"]
            analysis = result["derivation"]["analysis"]["id"]
            if analysis != "summary.distribution" or floor is not None or len(columns) < 2:
                continue
            for i, position in enumerate(result["values"]["positions"]):
                variables = result["analysed"][i]["variables"]
                assert len(variables) == len(columns) == len(position["columns"])
                for j, column in enumerate(position["columns"]):
                    if column["kind"] == "numbers":
                        assert variables[j]["n"] == column["n"]
                        checked += 1
                    elif column["categories"]:
                        assert all(
                            row["proportion"]["denominator"] == variables[j]["n"]
                            for row in column["categories"]
                        )
                        checked += 1
    assert checked >= 12
