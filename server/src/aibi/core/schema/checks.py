"""Checks that need the whole document but no release (SPEC §7.1, §7.2, §7.4, §7.5).

Each check reports refusals with a JSON Pointer into the substituted document; the loader maps
pointers back to the document as written.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from aibi.core.schema.document import (
    AllClause,
    AnyClause,
    ClauseModel,
    Cohort,
    CohortLeaf,
    CoveredLeaf,
    Document,
    ExistsLeaf,
    IdsLeaf,
    KnownClause,
    NotClause,
    UnknownClause,
    ValueLeaf,
)
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.output import data, text
from aibi.core.schema.refusals import Refusal, RefusalCode

Path = list[str | int]
_ARTICLE = {"ids": "An ids", "cohort": "A cohort"}


@dataclass(frozen=True)
class Unknown:
    """What a document leaves unknown because a member was null, and so is not checked."""

    datasets: frozenset[str] = frozenset()
    """Cohorts whose datasets are unknown: a ``dataset`` or ``datasets`` member was null."""
    unmapped: frozenset[str] = frozenset()
    """Cohorts whose ``unmapped`` was null."""
    view_cohorts: frozenset[int] = frozenset()
    """Views whose ``cohorts`` was null."""


def _children(clause: ClauseModel, path: Path) -> Iterator[tuple[ClauseModel, Path, bool]]:
    """Direct sub-clauses, with their paths and whether they sit inside a ``where``."""
    if isinstance(clause, AllClause):
        for index, member in enumerate(clause.all):
            yield member, [*path, "all", index], False
    elif isinstance(clause, AnyClause):
        for index, member in enumerate(clause.any):
            yield member, [*path, "any", index], False
    elif isinstance(clause, NotClause):
        yield clause.not_, [*path, "not"], False
    elif isinstance(clause, KnownClause):
        yield clause.known, [*path, "known"], False
    elif isinstance(clause, UnknownClause):
        yield clause.unknown, [*path, "unknown"], False
    elif isinstance(clause, ExistsLeaf):
        for index, member in enumerate(clause.where or []):
            yield member, [*path, "where", index], True


def walk(
    clauses: list[ClauseModel], path: Path, in_where: bool = False
) -> Iterator[tuple[ClauseModel, Path, bool]]:
    """Every clause under ``path``, depth first, with whether it is inside some ``where``."""
    for index, clause in enumerate(clauses):
        stack: list[tuple[ClauseModel, Path, bool]] = [(clause, [*path, index], in_where)]
        while stack:
            current, current_path, inside = stack.pop()
            yield current, current_path, inside
            children = list(_children(current, current_path))
            stack.extend(
                (child, child_path, inside or nested)
                for child, child_path, nested in reversed(children)
            )


def _datasets(cohort: Cohort, document: Document) -> tuple[str, ...] | None:
    """The dataset ids a cohort draws on. Pins are left out: a document resolves each dataset to
    one release, which is checked when the document is resolved (SPEC §7.1)."""
    if cohort.datasets is not None:
        refs = cohort.datasets
    elif (dataset := cohort.dataset or document.dataset) is not None:
        refs = [dataset]
    else:
        return None
    return tuple(sorted(ref.split("@", 1)[0] for ref in refs))


def _is_concept(reference: str) -> bool:
    return ":" in reference


def _duplicates(values: list[str], path: Path, what: str) -> list[Refusal]:
    """A refusal at each repeated entry of a list, naming the entry."""
    seen: set[str] = set()
    refusals: list[Refusal] = []
    for index, value in enumerate(values):
        if value in seen:
            refusals.append(
                Refusal(
                    code=RefusalCode.DUPLICATE_ENTRY,
                    path=pointer([*path, index]),
                    message=[text(f"{what} appears more than once: "), data(value)],
                )
            )
        seen.add(value)
    return refusals


def _cross_dataset(name: str, cohort: Cohort, path: Path) -> list[Refusal]:
    """Static rules of a cross-dataset cohort (SPEC §7.5)."""
    refusals: list[Refusal] = []
    for clause, clause_path, _ in walk(list(cohort.all), [*path, "all"]):
        if cohort.unmapped is None:
            member = "column" if isinstance(clause, ValueLeaf) else "table"
            if isinstance(clause, ValueLeaf | ExistsLeaf | CoveredLeaf) and not _is_concept(
                getattr(clause, member)
            ):
                refusals.append(
                    Refusal(
                        code=RefusalCode.CONCEPT_REQUIRED,
                        path=pointer([*clause_path, member]),
                        message=[
                            text("Cross-dataset cohort "),
                            data(name),
                            text(" refers to columns and tables by concept, unless it says "),
                            text('"unmapped": "allow"'),
                        ],
                    )
                )
    return refusals


def _via_maps(name: str, cohort: Cohort, datasets: tuple[str, ...], path: Path) -> list[Refusal]:
    """``via`` by dataset belongs to cross-dataset cohorts, keyed by their datasets."""
    refusals: list[Refusal] = []
    cross = cohort.datasets is not None
    for clause, clause_path, _ in walk(list(cohort.all), [*path, "all"]):
        via: object = getattr(clause, "via", None)
        if not isinstance(via, dict):
            continue
        keys = list(cast(dict[str, object], via))
        via_path = [*clause_path, "via"]
        if not cross:
            refusals.append(
                Refusal(
                    code=RefusalCode.CROSS_DATASET_ONLY,
                    path=pointer(via_path),
                    message=[
                        text("A via by dataset is for cross-dataset cohorts; cohort "),
                        data(name),
                        text(" has one dataset, so give the steps as a list"),
                    ],
                )
            )
            continue
        for key in keys:
            if key not in datasets:
                refusals.append(
                    Refusal(
                        code=RefusalCode.UNKNOWN_DATASET,
                        path=pointer([*via_path, key]),
                        message=[text("Not one of the cohort's datasets: "), data(key)],
                        alternatives=[data(dataset) for dataset in datasets],
                    )
                )
    return refusals


def check_document(document: Document, unknown: Unknown | None = None) -> list[Refusal]:
    """The document checks; ``unknown`` names what a null left unknown, which they skip."""
    unknown = unknown or Unknown()
    refusals: list[Refusal] = []
    names = sorted(document.cohorts)
    references: dict[str, set[str]] = {name: set() for name in names}
    unit_is_concept = _is_concept(document.unit)

    for name, cohort in document.cohorts.items():
        cohort_path: Path = ["cohorts", name]
        datasets = _datasets(cohort, document)
        if cohort.datasets is not None:
            ids = [ref.split("@", 1)[0] for ref in cohort.datasets]
            refusals.extend(_duplicates(ids, [*cohort_path, "datasets"], "A dataset"))
            if name not in unknown.unmapped:
                refusals.extend(_cross_dataset(name, cohort, cohort_path))
            if not unit_is_concept:
                refusals.append(
                    Refusal(
                        code=RefusalCode.CONCEPT_REQUIRED,
                        path="/unit",
                        message=[
                            text("A cross-dataset cohort needs a table concept as the unit, "),
                            text('such as "core:person"'),
                        ],
                    )
                )
        if name not in unknown.datasets:
            refusals.extend(_via_maps(name, cohort, datasets or (), cohort_path))
        if datasets is None and name not in unknown.datasets:
            refusals.append(
                Refusal(
                    code=RefusalCode.DATASET_MISSING,
                    path=pointer(cohort_path),
                    message=[
                        text("Cohort "),
                        data(name),
                        text(" has no dataset: give the document's dataset or the cohort's"),
                    ],
                )
            )
        for clause, path, inside in walk(list(cohort.all), [*cohort_path, "all"]):
            if inside and isinstance(clause, IdsLeaf | CohortLeaf):
                refusals.append(
                    Refusal(
                        code=RefusalCode.LEAF_NOT_ALLOWED,
                        path=pointer(path),
                        message=[
                            text(f"{_ARTICLE[clause.kind]} leaf is not allowed inside a where")
                        ],
                    )
                )
            if isinstance(clause, CohortLeaf):
                target = document.cohorts.get(clause.cohort)
                if target is None:
                    refusals.append(
                        Refusal(
                            code=RefusalCode.UNKNOWN_COHORT,
                            path=pointer([*path, "cohort"]),
                            message=[text("Unknown cohort "), data(clause.cohort)],
                            alternatives=[data(other) for other in names],
                        )
                    )
                    continue
                references[name].add(clause.cohort)
                if unknown.datasets & {name, clause.cohort}:
                    continue
                if _datasets(target, document) != _datasets(cohort, document):
                    refusals.append(
                        Refusal(
                            code=RefusalCode.COHORT_MISMATCH,
                            path=pointer([*path, "cohort"]),
                            message=[
                                text("A referenced cohort must use the same dataset(s): "),
                                data(clause.cohort),
                            ],
                        )
                    )

    refusals.extend(_cycles(references))

    for index, view in enumerate(document.views or []):
        view_path: Path = ["views", index]
        refusals.extend(_duplicates(list(view.cohorts or []), [*view_path, "cohorts"], "A cohort"))
        if (
            view.cohorts is not None
            and view.reference is not None
            and view.reference not in view.cohorts
        ):
            refusals.append(
                Refusal(
                    code=RefusalCode.REFERENCE_NOT_IN_VIEW,
                    path=pointer([*view_path, "reference"]),
                    message=[
                        text("The reference must be one of the view's cohorts: "),
                        data(view.reference),
                    ],
                    alternatives=[data(cohort_name) for cohort_name in view.cohorts],
                )
            )
        # Cohorts whose datasets a null left unknown are left out: the others may still span
        # datasets.
        spans = {
            span
            for c in view.cohorts or names
            if c in document.cohorts
            and c not in unknown.datasets
            and (span := _datasets(document.cohorts[c], document))
        }
        if len(spans) > 1 and not unit_is_concept and index not in unknown.view_cohorts:
            refusals.append(
                Refusal(
                    code=RefusalCode.CONCEPT_REQUIRED,
                    path="/unit",
                    message=[
                        text("A view over cohorts of different datasets needs a table concept "),
                        text('as the unit, such as "core:person"'),
                    ],
                )
            )
        for position, cohort_name in enumerate(view.cohorts or []):
            if cohort_name not in document.cohorts:
                refusals.append(
                    Refusal(
                        code=RefusalCode.UNKNOWN_COHORT,
                        path=pointer(["views", index, "cohorts", position]),
                        message=[text("Unknown cohort "), data(cohort_name)],
                        alternatives=[data(other) for other in names],
                    )
                )
        if view.reference is not None and view.reference not in document.cohorts:
            refusals.append(
                Refusal(
                    code=RefusalCode.UNKNOWN_COHORT,
                    path=pointer(["views", index, "reference"]),
                    message=[text("Unknown cohort "), data(view.reference)],
                    alternatives=[data(other) for other in names],
                )
            )
    return refusals


def _cycles(references: dict[str, set[str]]) -> list[Refusal]:
    """One refusal per cohort that lies on a cycle of cohort references."""
    on_cycle: set[str] = set()
    for start in references:
        seen: set[str] = set()
        frontier = list(references[start])
        while frontier:
            current = frontier.pop()
            if current == start:
                on_cycle.add(start)
                break
            if current not in seen:
                seen.add(current)
                frontier.extend(references.get(current, ()))
    return [
        Refusal(
            code=RefusalCode.COHORT_CYCLE,
            path=pointer(["cohorts", name]),
            message=[text("Cohort "), data(name), text(" refers to itself through cohort leaves")],
        )
        for name in sorted(on_cycle)
    ]
