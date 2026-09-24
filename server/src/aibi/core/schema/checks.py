"""Checks that need the whole document but no release (SPEC §7.1, §7.2, §7.4).

Each check reports refusals with a JSON Pointer into the substituted document; the loader maps
pointers back to the document as written.
"""

from collections.abc import Iterator

from aibi.core.schema.document import (
    AllClause,
    AnyClause,
    ClauseModel,
    Cohort,
    CohortLeaf,
    Document,
    ExistsLeaf,
    IdsLeaf,
    KnownClause,
    NotClause,
    UnknownClause,
)
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.refusals import Refusal, RefusalCode, data, text

Path = list[str | int]


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


def check_document(document: Document) -> list[Refusal]:
    refusals: list[Refusal] = []
    names = sorted(document.cohorts)
    references: dict[str, set[str]] = {name: set() for name in names}

    for name, cohort in document.cohorts.items():
        cohort_path: Path = ["cohorts", name]
        if _datasets(cohort, document) is None:
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
                        message=[text(f"A {clause.kind} leaf is not allowed inside a where")],
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
