"""Readbacks: a canonical cohort in plain language, from templates (SPEC §3.2 A2, §7.7; D296).

``readback`` renders a cohort's canonical form as segments (§8.1): the server's template text,
and data tokens for everything that comes from a descriptor or a document (labels, constants,
units, keys, the release's label). It is a function of the canonical form and the release's
descriptors alone, so two documents with one canonical form have one readback, and no model
writes any of it; a pack's summary, rendered from its leaf as written, is the one part that two
such documents may word differently (§7.3, D296).

It states, in the canonical order (step 8 of §7.6):

- the unit table, the dataset and the release's label;
- every path step: each lookup (*the Books row reached through …*), each down step with its
  relationship, and ``exclude_self``;
- every condition, with every negation (``negate``, ``not``, ``known``, ``unknown``), every
  quantifier (*some*, *at least k*, *every*), the lift rule of each intermediate question, and
  the units of every numeric constant;
- for each down step, the coverage the question relies on (all, listed, undeclared, and whether
  it is only proposed), its record filter and its parent scope;
- what is excluded: a unit whose answer cannot be decided is unknown and not counted;
- and, labelled as theirs, the pack summaries of the pack leaves as written (§7.3), each labelled
  and ordered by the leaf keys of the conditions its leaf became part of, never by its pointer,
  which holds the cohort's name.

Long lists are cut: at most ``LISTED`` constants of a ``values`` list, keys of an ``ids`` leaf or
values of a scope, then how many more there are; each data token is cut at 200 characters and
marked (§8.1). Everything read from data is a data token, never template text (A6).

``variable_readback`` states a view's variable the same way (D325): its column and lookups; for
an aggregate, the function, the rows reached at its last down step with their conditions, then
each earlier step they are pooled through, and what a unit with no value takes; for ``some`` or
``every``, its question.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from aibi.core.engine.canonical import CanonicalCohort, CanonicalVariable, canonical_clause
from aibi.core.engine.data import Release
from aibi.core.engine.graph import Path
from aibi.core.engine.ids import order_key, sorted_unique
from aibi.core.engine.resolve import Coverage, ResolvedVariable, levels
from aibi.core.engine.resolved import (
    Bounds,
    Constant,
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RIds,
    RKnown,
    RNot,
    RValue,
    constant_json,
)
from aibi.core.schema.jsonio import canonical, number_text
from aibi.core.schema.output import Segment, data, text

LISTED = 64
"""Constants, keys or scope values a readback lists before it counts the rest."""

_BOUNDS = (
    ("gt", "greater than "),
    ("gte", "at least "),
    ("lt", "less than "),
    ("lte", "at most "),
)


def _written(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return number_text(value)
    return str(value)


def constant_text(value: Constant) -> str:
    """A constant as the canonical form writes it, as text: numbers as RFC 8785 writes them."""
    return _written(constant_json(value))


def _in_order(values: Iterable[JsonValue]) -> list[str]:
    """Values in the canonical order (§7.6, step 8), each once, as text."""
    return [_written(value) for value in sorted_unique(values)]


def _sorted(nodes: Sequence[RClause]) -> list[RClause]:
    """Clauses in the canonical order (§7.6, step 8)."""
    return sorted(nodes, key=lambda node: order_key(canonical_clause(node)))


def _listed_items(items: Sequence[Sequence[Segment]]) -> list[Segment]:
    """At most ``LISTED`` items, then how many more there are."""
    shown: list[Segment] = []
    for index, item in enumerate(items[:LISTED]):
        if index:
            shown.append(text(", "))
        shown += item
    if len(items) > LISTED:
        shown.append(text(f", and {len(items) - LISTED} more"))
    return shown


def _listed(values: Sequence[str]) -> list[Segment]:
    return _listed_items([[data(value)] for value in values])


def _key(parts: Sequence[JsonValue]) -> list[Segment]:
    """A unit key, each of its parts a data token of its own: a composite one in parentheses."""
    tokens: list[Segment] = [data(_written(part)) for part in parts]
    if len(tokens) == 1:
        return tokens
    found: list[Segment] = [text("(")]
    for index, token in enumerate(tokens):
        found += [text(", "), token] if index else [token]
    return [*found, text(")")]


@dataclass(frozen=True)
class _Reader:
    """What a readback reads of a release: its descriptors' labels and its coverage."""

    release: Release
    coverage: Mapping[str, Coverage]

    def label(self, identifier: str) -> Segment:
        descriptor = self.release.by_id.get(identifier)
        return data(identifier if descriptor is None else descriptor.label)

    def lookups(self, steps: Path) -> list[Segment]:
        """The rows up steps reach, the last first: *of the Books row reached through …*."""
        found: list[Segment] = []
        for index, step in enumerate(reversed(steps)):
            relationship = self.release.relationship(step.rel)
            parent = step.rel if relationship is None else relationship.fields.parent_table
            found += [
                text(" of the " if index == 0 else " from the "),
                self.label(parent),
                text(" row reached through "),
                self.label(step.rel),
            ]
        return found

    # --- Clauses -----------------------------------------------------------------------------

    def clause(self, node: RClause) -> list[Segment]:
        if isinstance(node, RValue):
            return self.value(node)
        if isinstance(node, RExists):
            return self.exists(node)
        if isinstance(node, RCovered):
            return self.covered(node)
        if isinstance(node, RIds):
            return self.ids(node)
        if isinstance(node, RAll):
            if not node.members:
                return [text("every row (an empty all holds for every row)")]
            return self.joined(node.members, " and ")
        if isinstance(node, RAny):
            if not node.members:
                return [text("no row (an empty any holds for none)")]
            return self.joined(node.members, " or ")
        if isinstance(node, RNot):
            return [text("not ("), *self.clause(node.member), text(")")]
        if isinstance(node, RKnown):
            return [text("it is known whether ("), *self.clause(node.member), text(")")]
        return [text("it is unknown whether ("), *self.clause(node.member), text(")")]

    def joined(self, members: Sequence[RClause], word: str) -> list[Segment]:
        found: list[Segment] = []
        for index, member in enumerate(_sorted(members)):
            if index:
                found.append(text(word))
            if isinstance(member, RAll | RAny) and member.members:
                found += [text("("), *self.clause(member), text(")")]
            else:
                found += self.clause(member)
        return found

    def value(self, node: RValue) -> list[Segment]:
        found: list[Segment] = [text("the "), self.label(node.column), *self.lookups(node.via)]
        if node.match == "any":
            found.append(text(" has some item that"))
        elif node.match == "all":
            found.append(text(" has only items that each"))
        found += self.predicate(node)
        if node.match == "all":
            found.append(text(" (an empty list is unknown)"))
        return found

    def predicate(self, node: RValue) -> list[Segment]:
        predicate = node.predicate
        found: list[Segment] = []
        if isinstance(predicate, Bounds):
            found.append(text(" is not (" if node.negate else " is "))
            parts = [
                (words, bound)
                for name, words in _BOUNDS
                if (bound := getattr(predicate, name)) is not None
            ]
            for index, (words, bound) in enumerate(parts):
                found += [text(" and " if index else ""), text(words), data(constant_text(bound))]
            if node.negate:
                found.append(text(")"))
        else:
            listed = _in_order(constant_json(value) for value in predicate.values)
            if len(listed) == 1:
                found += [text(" is not " if node.negate else " is "), data(listed[0])]
            else:
                found.append(text(" is none of " if node.negate else " is one of "))
                found += _listed(listed)
        if node.units is not None:
            found += [text(" (in "), data(node.units), text(")")]
        return found

    def exists(self, node: RExists) -> list[Segment]:
        if node.quantifier == "every":
            quantified = "every "
        elif node.min_count is not None and node.min_count > 1:
            quantified = f"at least {node.min_count} "
        else:
            quantified = "some "
        found: list[Segment] = [
            text(quantified),
            self.label(node.table),
            text(" rows" if node.min_count is not None and node.min_count > 1 else " row"),
            *self.reached(node),
        ]
        if node.where:
            found.append(
                text(
                    ", each of which is such that "
                    if node.quantifier == "every"
                    else ", such that "
                )
            )
            found += self.joined(node.where, " and ")
        return found

    def reached(self, node: RExists) -> list[Segment]:
        """How a question reaches its rows: its relationship and lookups, ``exclude_self``, the
        coverage it relies on and its lift rule."""
        step = node.step
        found: list[Segment] = [
            text(" linked through "),
            self.label(step.rel),
            *self.lookups(node.via[:-1]),
        ]
        if node.exclude_self:
            found.append(text(", other than the row itself"))
        found += self.recorded(step.rel)
        if node.lift == "strict":
            found += [
                text(" (any "),
                self.label(node.table),
                text(" row that cannot be assessed makes this unknown)"),
            ]
        elif node.lift == "assessed":
            found += [
                text(" (counting only the "),
                self.label(node.table),
                text(" rows that could be assessed)"),
            ]
        return found

    def pooled(self, variable: ResolvedVariable) -> list[Segment]:
        """An aggregate's rows: those reached at its last down step, then each earlier step
        they are pooled through, innermost first (§9.2)."""
        assert variable.rows is not None
        chain = levels(variable.rows, variable.depth)
        last = chain[-1]
        found: list[Segment] = [self.label(last.table), text(" rows")]
        found += self.reached(last)
        if last.where:
            found += [text(", such that "), *self.joined(last.where, " and ")]
        for node in reversed(chain[:-1]):
            found += [text(", of the "), self.label(node.table), text(" rows")]
            found += self.reached(node)
        return found

    def recorded(self, relationship: str) -> list[Segment]:
        """The coverage a down step relies on: its form, its record filter and its parent
        scope (§6.5)."""
        coverage = self.coverage.get(relationship)
        if coverage is None:
            return []
        child = self.label(coverage.child_table)
        if coverage.form == "all":
            found: list[Segment] = [
                text(" [coverage: every row's "),
                child,
                text(" rows are recorded"),
            ]
        elif coverage.form == "undeclared":
            found = [
                text(" [coverage: undeclared, so no row is known to have no further "),
                child,
                text(" rows"),
            ]
        else:
            found = [
                text(" [coverage: "),
                child,
                text(" rows are recorded only for the rows the coverage lists"),
            ]
            if coverage.scope_columns:
                found.append(text(", by "))
                found += _listed(
                    [coverage.child_table + "." + column for column in coverage.scope_columns]
                )
        if coverage.proposed:
            found.append(text("; this coverage is proposed, not confirmed"))
        for column, allowed in coverage.record_filter:
            found += [
                text("; counting only "),
                child,
                text(" rows whose "),
                self.label(f"{coverage.child_table}.{column}"),
                text(" is one of "),
                *_listed(_in_order(allowed)),
            ]
        if coverage.parent_scope is not None:
            found += [
                text("; asked only of the "),
                self.label(coverage.parent_table),
                text(" rows for which "),
            ]
            found += self.clause(coverage.parent_scope)
        found.append(text("]"))
        return found

    def covered(self, node: RCovered) -> list[Segment]:
        downs = [index for index, step in enumerate(node.via) if step.dir == "down"]
        last = downs[-1]
        found: list[Segment] = []
        start = 0
        for index in downs[:-1]:
            step = node.via[index]
            relationship = self.release.relationship(step.rel)
            child = step.rel if relationship is None else relationship.fields.child_table
            found += [
                text("for the " if node.lift == "assessed" else "for every "),
                self.label(child),
                text(
                    " rows linked through " if node.lift == "assessed" else " row linked through "
                ),
                self.label(step.rel),
                *self.lookups(node.via[start:index]),
            ]
            if node.lift == "assessed":
                found.append(text(" that could be assessed"))
            found += self.recorded(step.rel)
            found.append(text(", "))
            start = index + 1
        found += [
            text("the "),
            self.label(node.table),
            text(" rows linked through "),
            self.label(node.via[last].rel),
            *self.lookups(node.via[start:last]),
            text(" are recorded"),
        ]
        for index, (column, values) in enumerate(node.scope or ()):
            found += [
                text(" for " if index == 0 else " and "),
                self.label(f"{node.table}.{column}"),
                text(" in "),
                *_listed(_in_order(constant_json(value) for value in values)),
            ]
        return found + self.recorded(node.via[last].rel)

    def ids(self, node: RIds) -> list[Segment]:
        keys = sorted_unique([constant_json(part) for part in key] for _, key in node.keys)
        shown = [_key(cast(list[JsonValue], key)) for key in keys]
        return [text("the row's key is one of "), *_listed_items(shown)]


def readback(cohort: CanonicalCohort) -> list[Segment]:
    """The readback of a canonical cohort (module docstring)."""
    resolved = cohort.resolved
    reader = _Reader(resolved.release, dict(resolved.coverage))
    dataset = resolved.release.dataset_descriptor
    found: list[Segment] = [
        text("Rows of "),
        reader.label(resolved.unit),
        text(" in "),
        data(resolved.release.dataset if dataset is None else dataset.label),
        text(" @"),
        data(str(cohort.release.label)),
    ]
    if not resolved.clauses:
        found.append(text(": every row, since the cohort has no conditions."))
    else:
        found.append(text(" for which "))
        found += reader.joined(resolved.clauses, " and ")
        found.append(
            text(
                ". A row for which this cannot be decided (a value that is missing or not "
                "assessed, a row it refers to that is missing, or related rows not known to be "
                "recorded) is unknown and not counted."
            )
        )
    return found + _summaries(cohort)


_FUNCTIONS = {"max": "greatest", "min": "least", "mean": "mean"}


def variable_readback(variable: CanonicalVariable) -> list[Segment]:
    """A view's variable (D325), for its view's readback: the column and the rows it reads,
    from the release's descriptors."""
    resolved = variable.resolved
    reader = _Reader(resolved.release, dict(resolved.coverage))
    if resolved.kind == "column":
        return [text("the "), reader.label(resolved.column), *reader.lookups(resolved.via)]
    if resolved.kind == "question":
        assert resolved.question is not None
        return [text("whether "), *reader.clause(resolved.question)]
    function = resolved.function
    assert function is not None
    if function == "count":
        return [text("the number of "), *reader.pooled(resolved)]
    found: list[Segment] = [
        text(f"the {_FUNCTIONS[function]} "),
        reader.label(resolved.column),
        *reader.lookups(resolved.lookup),
        text(" over the "),
        *reader.pooled(resolved),
    ]
    if resolved.order is not None:
        found.append(text(", in the column's listed order"))
    if resolved.empty is None:
        found.append(text("; a unit with no value there is left out"))
    else:
        found += [text("; "), data(constant_text(resolved.empty)), text(" for a unit with none")]
    return found


def conditions(cohort: CanonicalCohort) -> list[Segment]:
    """The conditions of a canonical cohort without its opening and closing words: a view's
    predicate, which its view's readback states for the units of its cohorts (D317), followed
    by the pack summaries of its pack leaves."""
    resolved = cohort.resolved
    reader = _Reader(resolved.release, dict(resolved.coverage))
    if not resolved.clauses:
        return [text("every row (an empty all holds for every row)"), *_summaries(cohort)]
    return [*reader.joined(resolved.clauses, " and "), *_summaries(cohort)]


def _summaries(cohort: CanonicalCohort) -> list[Segment]:
    """The pack summaries, each labelled with the leaf keys of the conditions its leaf became
    part of, never with its pointer as written, which holds the cohort's name; in the order of
    those keys, then of the summaries' own text, each once (D296)."""
    labelled = sorted(
        {
            (cohort.leaves.get(at, ()), canonical(_segments(summary)).decode()): summary
            for at, summary in cohort.summaries.items()
        }.items()
    )
    found: list[Segment] = []
    for (keys, _), summary in labelled:
        if keys:
            found.append(text(" The pack's summary of its leaf in the condition "))
            for index, key in enumerate(keys):
                found += [text(" and "), data(key)] if index else [data(key)]
        else:
            found.append(text(" The pack's summary of its leaf, which adds no condition"))
        found += [text(": "), *summary]
    return found


def _segments(summary: Sequence[Segment]) -> JsonValue:
    return [segment.model_dump(mode="json") for segment in summary]


__all__ = ["LISTED", "conditions", "constant_text", "readback", "variable_readback"]
