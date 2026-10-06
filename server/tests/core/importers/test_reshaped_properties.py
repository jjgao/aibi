"""Properties of tables a pack's importer unpivoted (SPEC §6.5, D401), over random grade matrices
through the test-only ``grid`` pack:

1. the store's answers agree with the reference evaluator's, and a re-import that fills an empty
   cell, with a grade or with ``0``, never turns an answer that carries no ``SCOPE_PARTIAL``
   from TRUE to FALSE or back: an empty cell, not assessed, holds every answer open;
2. a coverage that lists other cells than the matrix held (an empty cell listed, an absent cell
   left out, or both at once, which keeps the count) is refused at import;
3. a sequence of changes keeps the table's blob through allowed edits, and refuses every edit
   of what the importer owns.

Each example builds its own store in a temporary directory, since a fixture lives across the
examples of one test.
"""

import json
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import resolve
from aibi.core.engine.worker import Workers
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import import_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.limits import MAX_COHORTS, ImportLimits, QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.pack_api import ImportOptions, ImportResult, PackRegistry
from aibi.core.store.edits import EditRefused
from aibi.core.store.sessions import change, open_session
from aibi.core.store.sources import TypedSource
from aibi.core.store.store import Store

Grid = Any
ADA = "operator:ada"
SEASONS = ("autumn", "spring", "summer")
TREES = ("t1", "t2", "t3", "t4")
CELLS = st.sampled_from(["A", "B", "C", "0", ""])
SLOW = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


@dataclass(frozen=True)
class Matrix:
    header: tuple[str, ...]
    """The trees with a column, a subset of ``TREES``."""
    rows: tuple[tuple[str, ...], ...]
    """One row per season of ``SEASONS``: a cell per tree of the header."""


@st.composite
def matrices(draw: st.DrawFn) -> Matrix:
    header = tuple(t for t in TREES if draw(st.booleans()))
    rows = tuple(tuple(draw(CELLS) for _ in header) for _ in SEASONS)
    return Matrix(header, rows)


def _write(folder: Path, matrix: Matrix) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    trees = "".join(f"{tree}\tapple\n" for tree in TREES)
    (folder / "trees.tsv").write_bytes(f"tree_id\tvariety\n{trees}".encode())
    lines = ["season\t" + "\t".join(matrix.header)]
    lines += ["\t".join((season, *row)) for season, row in zip(SEASONS, matrix.rows, strict=True)]
    (folder / "grades.tsv").write_bytes(("\n".join(lines) + "\n").encode())
    (folder / "pickings.tsv").write_bytes(b"tree_id\tpicker_1\tpicker_2\nt1\tann\t\n")


@contextmanager
def _orchard(
    grid: Grid, matrix: Matrix, registry: PackRegistry | None = None
) -> Iterator[tuple[Store, str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write(root / "imports" / "orchard", matrix)
        store = Store(root / "data")
        try:
            confinement = Confinement.of(root / "imports")
            options = ImportOptions(
                dataset="d", reader=confinement, limits=ImportLimits(), at=store.now()
            )
            published = import_dataset(
                store,
                confinement.confine(root / "imports" / "orchard"),
                options,
                ADA,
                registry=registry or grid.registry,
                pack="grid",
            )
            yield store, published.manifest
        finally:
            store.close()


def _questions() -> list[dict[str, Any]]:
    def exists(*where: dict[str, Any], **given: Any) -> dict[str, Any]:
        return {"kind": "exists", "table": "yields", "where": list(where), **given}

    def value(column: str, *values: str) -> dict[str, Any]:
        return {"kind": "value", "column": f"yields.{column}", "values": list(values)}

    found: list[dict[str, Any]] = [exists(value("grade", grade)) for grade in ("A", "B", "C")]
    found += [exists(value("season", season)) for season in SEASONS]
    found += [exists(value("season", "autumn"), value("grade", "A", "B"))]
    found += [exists(value("grade", "A"), quantifier="every")]
    found += [exists(value("season", "spring"), min_count=1)]
    found += [{"not": exists(value("season", "summer"), value("grade", "C"))}]
    return found


def _answers(store: Store, manifest: str) -> list[list[tuple[str, bool]]]:
    """Each question's answers, per tree: the value and whether it is partial, the store's
    agreeing with the reference evaluator's; the questions asked six to a document, the most
    cohorts one holds."""
    questions = _questions()
    found: list[list[tuple[str, bool]]] = []
    for start in range(0, len(questions), MAX_COHORTS):
        found += _batch(store, manifest, questions[start : start + MAX_COHORTS])
    return found


def _batch(
    store: Store, manifest: str, questions: Sequence[dict[str, Any]]
) -> list[list[tuple[str, bool]]]:
    cohorts = {f"q{i}": {"all": [q]} for i, q in enumerate(questions)}
    written = {"aibi": "1", "dataset": "d", "unit": "trees", "cohorts": cohorts}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"d": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    outline = resolve(loaded.document, {"d": store.outline(manifest)}, loaded.positions)
    with store.pin() as pin:
        pin.manifest(manifest)
        counted = run_cohorts(
            [outline.cohorts[name] for name in cohorts],
            {manifest: store.sources(manifest)},
            Workers(QueryLimits()),
            values=True,
        )
    found: list[list[tuple[str, bool]]] = []
    for name, sql in zip(cohorts, counted, strict=True):
        expected = evaluate(resolution.cohorts[name])
        assert sql.values is not None
        assert tuple(sql.values) == expected.values
        found.append(
            [
                (v.value.value, any(m.flag.value == "SCOPE_PARTIAL" for m in v.marks))
                for v in expected.values
            ]
        )
    return found


@st.composite
def filled(draw: st.DrawFn) -> tuple[Matrix, Matrix]:
    """A matrix with an empty cell, and the same matrix with that cell filled."""
    matrix = draw(matrices().filter(lambda m: any("" in row for row in m.rows)))
    empties = [
        (r, c) for r, row in enumerate(matrix.rows) for c, cell in enumerate(row) if cell == ""
    ]
    r, c = draw(st.sampled_from(empties))
    fill = draw(st.sampled_from(["A", "B", "C", "0"]))
    rows = [list(row) for row in matrix.rows]
    rows[r][c] = fill
    return matrix, Matrix(matrix.header, tuple(tuple(row) for row in rows))


@settings(max_examples=12, deadline=None, suppress_health_check=SLOW)
@given(filled())
def test_an_empty_cell_holds_every_unflagged_answer_open(
    grid: Grid, pair: tuple[Matrix, Matrix]
) -> None:
    before, after = pair
    with _orchard(grid, before) as (store, manifest):
        first = _answers(store, manifest)
    with _orchard(grid, after) as (store, manifest):
        second = _answers(store, manifest)
    for question, (was, now) in enumerate(zip(first, second, strict=True)):
        for tree, ((a, partial_a), (b, partial_b)) in enumerate(zip(was, now, strict=True)):
            if partial_a or partial_b or "UNKNOWN" in (a, b):
                continue
            assert a == b, (question, TREES[tree], a, b)


def _cells(matrix: Matrix, keep: Callable[[str], bool]) -> set[tuple[str, str]]:
    return {
        (tree, season)
        for season, row in zip(SEASONS, matrix.rows, strict=True)
        for tree, cell in zip(matrix.header, row, strict=True)
        if keep(cell)
    }


def _dishonest(matrix: Matrix, how: str) -> Callable[[ImportResult], ImportResult] | None:
    """The grid's result with a coverage listing other cells than the matrix held: an empty cell
    listed (``add``), an absent cell left out (``drop``), or both (``swap``), which keeps the
    count; ``None`` when the matrix has no such cell."""
    held = _cells(matrix, lambda cell: cell != "")
    absent = sorted(_cells(matrix, lambda cell: cell == "0"))
    empty = sorted({(t, s) for t in TREES for s in SEASONS} - held)
    cells = set(held)
    if how in ("add", "swap"):
        if not empty:
            return None
        cells.add(empty[0])
    if how in ("drop", "swap"):
        if not absent:
            return None
        cells.discard(absent[0])

    def change(result: ImportResult) -> ImportResult:
        trees = sorted({tree for tree, _ in cells})
        return replace(
            result,
            sources={
                **result.sources,
                "tree_groups": TypedSource(("tree_id", "group"), tuple((t, t) for t in trees)),
                "group_seasons": TypedSource(("group", "season"), tuple(sorted(cells))),
            },
        )

    return change


@settings(max_examples=25, deadline=None, suppress_health_check=SLOW)
@given(matrices(), st.sampled_from(["add", "drop", "swap"]))
def test_a_coverage_listing_other_cells_is_refused(grid: Grid, matrix: Matrix, how: str) -> None:
    change = _dishonest(matrix, how)
    if change is None:
        return
    with pytest.raises(ImportRefused) as refused, _orchard(grid, matrix, grid.with_change(change)):
        pass
    assert {r.code for r in refused.value.refusals} == {"INVALID_VALUE"}


EDITS: list[tuple[str, dict[str, Any], bool]] = [
    (
        "relabel",
        {"op": "set", "descriptor": "yields", "pointer": "/label", "value": "Harvests"},
        True,
    ),
    (
        "widen",
        {
            "op": "set",
            "descriptor": "yields.grade",
            "pointer": "/fields/permissible_values",
            "value": {"values": [{"value": v} for v in "ABCD"], "ordered": True},
        },
        True,
    ),
    (
        "scope",
        {
            "op": "set",
            "descriptor": "cov:yields.tree_id",
            "pointer": "/fields/parent_scope",
            "value": {"kind": "value", "column": "trees.variety", "values": ["apple"]},
        },
        True,
    ),
    (
        "absent",
        {
            "op": "set",
            "descriptor": "yields.grade",
            "pointer": "/fields/absent",
            "value": ["0", "N"],
        },
        False,
    ),
    (
        "key",
        {
            "op": "set",
            "descriptor": "yields",
            "pointer": "/fields/primary_key",
            "value": ["tree_id", "season", "grade"],
        },
        False,
    ),
    (
        "parents",
        {
            "op": "set",
            "descriptor": "cov:yields.tree_id",
            "pointer": "/fields/parents",
            "value": "all",
        },
        False,
    ),
    (
        "relationship",
        {"op": "remove_descriptor", "descriptor": "rel:yields.tree_id"},
        False,
    ),
]


@settings(max_examples=15, deadline=None, suppress_health_check=SLOW)
@given(matrices(), st.lists(st.sampled_from(range(len(EDITS))), min_size=1, max_size=4))
def test_allowed_edits_keep_the_blob_and_owned_ones_are_refused(
    grid: Grid, matrix: Matrix, picked: Sequence[int]
) -> None:
    with _orchard(grid, matrix) as (store, manifest):
        blob = store.manifest(manifest).table("yields")
        assert blob is not None
        opened = open_session(store, "d", ADA)
        draft = opened.draft
        for index in picked:
            name, edit, allowed = EDITS[index]
            request = ChangeRequest.model_validate({"edits": [edit]})
            if allowed:
                draft = change(store, "d", opened.handle, draft, request, ADA)
                kept = store.manifest(draft).table("yields")
                assert kept is not None
                assert kept.hash == blob.hash, name
            else:
                with pytest.raises(EditRefused):
                    change(store, "d", opened.handle, draft, request, ADA)
