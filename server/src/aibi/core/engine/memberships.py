"""Memberships of a multi-valued categorical column, by the reference evaluator (SPEC §6.5, §9.2,
§13.3; D380, D381).

A variable with ``each: "category"`` (``resolve.ResolvedVariable`` of kind ``memberships``) asks,
of each unit and each category *c* of its column X, the question Q_c: its template
(``ResolvedVariable.question``) with ``values: [c]`` (``canonical.category_clause``), ``some`` row
reached at the last down step of its path whose X is *c*, or, for a list column, ``any`` of its
items. Q_c follows §6.5 in full, so its answer is the reference evaluator's (``Evaluator.truth``):
evidence, the parent scope, the record filter, closedness (a scope column of the last step is
closed for *c* where the coverage lists *c*, or covers every value), the lift, ``SCOPE_PARTIAL``
and ``COVERAGE_PROPOSED``; a NOT_APPLICABLE row or item is FALSE (§6.4). §6.4's refusal of a
constant outside a column's permissible values guards what a document writes; a category here is
read from the data, so a value X does not declare is asked about as any other.

A unit's **default**, D(u), is Q_c's answer for a category that no row it reaches holds and no
coverage lists for it: the answer for every such category (D381). It is asked here as Q_c of a
string that no cell of the release holds (``_fresh``), never of the template itself, whose value
leaf admits no value, so that its closedness would hold vacuously (§6.5, step 4).

**Which categories are listed** for a cohort (``listing``): the column's declared values, in their
order, zeros included (``false`` and ``true`` for a boolean), then the others, in UTF-16 order,
each where some member's answer for it is not its default (its truth value, reasons or flags),
as a row that holds it or a coverage that lists it makes it; ``MAX_CATEGORIES`` at most, else the
listing is ``over`` and nothing is counted. Where the last step's coverage filters X on the child
row (a record filter, §6.5 step 1), its categories are the filter's allowed values, the declared
ones in their order, then the others in UTF-16 order, zeros included, and no others: a question
may ask only about those, and the table records no row of another.

**Units** (``membership_units``): a unit's answers over the listed categories, or its default
alone when none is listed. It is known when one of them is known, and otherwise excluded under
the reasons of all of them; its flags are theirs. No unit is reclassified: nothing listed, a
unit whose default is known counts as known, with no category shown, and one whose default is
UNKNOWN is excluded under its reasons; ``NO_ROWS`` is never given. Where the listing is
``over``, no unit is given, and so no joint count.

``materialise`` counts a variable's memberships over a cohort's units (``Materialised`` with
``Memberships``); the SQL compiler counts the same (``sql.compile_materialised``), and the
differential tests hold the two together (§13.3). ``materialise_over`` counts a view's variables
over a cohort, memberships or not, and their joint counts, as ``run_analysis`` reads them by SQL
(D382).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from aibi.core.engine.canonical import category_clause
from aibi.core.engine.data import PRESENT, Release, key_part
from aibi.core.engine.evaluate import Evaluator
from aibi.core.engine.resolve import Coverage, ResolvedCohort, ResolvedVariable, levels
from aibi.core.engine.resolved import RExists, RValue
from aibi.core.engine.truth import Mark, TruthValue
from aibi.core.engine.variables import (
    Joint,
    Materialised,
    Membership,
    Memberships,
    UnitValue,
    Value,
    evaluate_variable,
    excluded_reason,
    joint,
)
from aibi.core.engine.variables import materialise as materialise_values
from aibi.core.schema.descriptors import DirectCoverage, GroupedCoverage
from aibi.core.schema.limits import MAX_CATEGORIES
from aibi.core.schema.semantics import ExclusionReason


@dataclass(frozen=True)
class Template:
    """A memberships' template taken apart: its questions, outermost first (none for a list with
    no down step), its value leaf (its ``via`` the lookups after the last down step, or, with no
    down step, those from the unit to the list), and the last step's coverage (``None`` with no
    down step)."""

    levels: tuple[RExists, ...]
    leaf: RValue
    coverage: Coverage | None

    @property
    def table(self) -> str:
        """The table of the column X."""
        return self.leaf.column.split(".", 1)[0]

    @property
    def column(self) -> str:
        """X's name in its table."""
        return self.leaf.column.split(".", 1)[1]

    @property
    def on_child(self) -> bool:
        """Whether X is a column of the last step's child row itself, with no lookup after it:
        what §6.5 calls a mention, which its scope columns and record filter read."""
        return self.coverage is not None and not self.leaf.via

    @property
    def scoped(self) -> bool:
        """Whether X is a scope column of the last step: closed for a category where the coverage
        lists it (§6.5, step 4)."""
        return self.on_child and self.column in cast(Coverage, self.coverage).scope_columns

    @property
    def allowed(self) -> frozenset[str] | None:
        """The allowed values of X where the last step's record filter filters it, else
        ``None``."""
        if not self.on_child:
            return None
        return dict(cast(Coverage, self.coverage).record_filter).get(self.column)


def template(variable: ResolvedVariable) -> Template:
    """A memberships' template taken apart (``Template``)."""
    if variable.kind != "memberships" or variable.question is None:
        raise ValueError("a template is a memberships'")
    question = variable.question
    if isinstance(question, RValue):
        return Template((), question, None)
    found = tuple(levels(cast(RExists, question), variable.depth))
    leaf = found[-1].where[0]
    assert isinstance(leaf, RValue)
    return Template(found, leaf, variable.coverage[found[-1].step.rel])


def utf16(value: Value) -> bytes:
    """A category's place among undeclared ones: its UTF-16 code units, as RFC 8785 sorts."""
    return str(value).encode("utf-16-be")


def fixed(variable: ResolvedVariable) -> tuple[Value, ...]:
    """The categories listed whatever the data: the declared values in their order (``false``
    and ``true`` for a boolean), or, for a column the last step's record filter filters, its
    allowed values, the declared ones in their order and then the others in UTF-16 order."""
    if variable.datatype == "boolean":
        declared: tuple[Value, ...] = (False, True)
    else:
        table, column = variable.column.split(".", 1)
        descriptor = variable.release.column(table, column)
        allowed = None if descriptor is None else descriptor.fields.permissible_values
        declared = () if allowed is None else tuple(entry.value for entry in allowed.values)
    filtered = template(variable).allowed
    if filtered is None:
        return declared
    others = sorted((value for value in filtered if value not in declared), key=utf16)
    return (*(value for value in declared if value in filtered), *others)


def closed_listing(variable: ResolvedVariable) -> bool:
    """Whether only ``fixed`` is listed: X is filtered by the last step's record filter."""
    return template(variable).allowed is not None


@dataclass(frozen=True)
class Answers:
    """A variable's memberships answered unit by unit, by row of the unit table: each unit's
    default (``defaults``) and its answers that are not its default (``paired``), by category,
    for every category that can be listed; any other such category's answer is its default.
    Where only ``fixed`` can be listed (``closed``), the answers of no other category are
    asked, and some may not be the default: a value the record filter disallows but the
    scope coverage lists is FALSE where the default is ``NOT_COVERED``. Such a category is
    never listed, so nothing counted reads it (D381)."""

    variable: ResolvedVariable
    fixed: tuple[Value, ...]
    closed: bool
    """Only ``fixed`` is listed (``closed_listing``)."""
    defaults: tuple[TruthValue, ...]
    paired: tuple[Mapping[Value, TruthValue], ...]

    def answer(self, row: int, category: Value) -> TruthValue:
        """The answer of the unit at ``row`` for a category that can be listed: its pair, or
        else its default."""
        return self.paired[row].get(category, self.defaults[row])


def evaluate(variable: ResolvedVariable) -> Answers:
    """Each unit's answers of a variable's memberships (module docstring): Q_c for every
    category that can be listed and whose answer could differ from a unit's default
    (``_candidates``), and the default."""
    release, unit = variable.release, variable.unit
    cohort = ResolvedCohort(
        variable.key, "", release, unit, (), {}, variable.fields, variable.coverage
    )
    evaluator = Evaluator(cohort)
    question = variable.question
    assert question is not None
    rows = range(len(release.rows(unit)))
    missing = category_clause(question, _fresh(release))
    defaults = tuple(evaluator.truth(missing, unit, row) for row in rows)
    paired: list[dict[Value, TruthValue]] = [{} for _ in rows]
    for category in _candidates(variable):
        asked = category_clause(question, category)
        for row in rows:
            found = evaluator.truth(asked, unit, row)
            if found != defaults[row]:
                paired[row][category] = found
    return Answers(
        variable,
        fixed(variable),
        closed_listing(variable),
        defaults,
        tuple(MappingProxyType(one) for one in paired),
    )


def listing(answers: Answers, members: Iterable[int]) -> tuple[tuple[Value, ...], bool]:
    """The categories listed over a cohort's members, in order, and whether they are more than
    ``MAX_CATEGORIES`` (module docstring)."""
    others: set[Value] = set()
    if not answers.closed:
        declared = set(answers.fixed)
        for row in members:
            others.update(value for value in answers.paired[row] if value not in declared)
    listed = (*answers.fixed, *sorted(others, key=utf16))
    return listed, len(listed) > MAX_CATEGORIES


def membership_units(answers: Answers, members: Iterable[int]) -> tuple[UnitValue, ...] | None:
    """Each unit of the unit table, by row, over the categories listed for a cohort's members
    (module docstring): ``True`` where some answer of its is known, else excluded under the
    reasons of all of them, with their flags. ``None`` where the listing is ``over``: nothing of
    the memberships is counted, so no unit is given and no joint count either, as SQL gives none
    (``sql.CompiledMaterialised.read``, D380)."""
    listed, over = listing(answers, members)
    if over:
        return None
    return tuple(_unit(answers, row, listed) for row in range(len(answers.defaults)))


def _unit(answers: Answers, row: int, listed: Sequence[Value]) -> UnitValue:
    given = [answers.answer(row, category) for category in listed] or [answers.defaults[row]]
    marks = frozenset(mark for found in given for mark in found.marks)
    if any(not found.is_unknown for found in given):
        return UnitValue(True, marks=marks)
    reasons = frozenset(excluded_reason(reason) for found in given for reason in found.reasons)
    return UnitValue(None, reasons, marks)


def materialise(answers: Answers, members: Iterable[int]) -> Materialised:
    """A variable's memberships over a cohort's members (``Materialised``): the units per listed
    category, and the units known or excluded as ``membership_units`` gives them; nothing is
    counted where the listing is ``over``."""
    rows = list(members)
    listed, over = listing(answers, rows)
    excluded = dict.fromkeys(ExclusionReason, 0)
    if over:
        return Materialised(
            MappingProxyType({}),
            0,
            MappingProxyType(excluded),
            frozenset(),
            None,
            Memberships((), 0, True),
        )
    known = excluded_units = 0
    marks: set[Mark] = set()
    for row in rows:
        found = _unit(answers, row, listed)
        marks.update(found.marks)
        if found.value is not None:
            known += 1
            continue
        excluded_units += 1
        for reason in found.excluded:
            excluded[reason] += 1
    categories = tuple(_membership(answers, rows, category) for category in listed)
    return Materialised(
        MappingProxyType({}),
        excluded_units,
        MappingProxyType(excluded),
        frozenset(marks),
        None,
        Memberships(categories, known),
    )


def _membership(answers: Answers, rows: Sequence[int], category: Value) -> Membership:
    true = false = unknown = 0
    by_reason = dict.fromkeys(ExclusionReason, 0)
    marks: set[Mark] = set()
    for row in rows:
        found = answers.answer(row, category)
        marks.update(found.marks)
        if found.is_true:
            true += 1
        elif found.is_false:
            false += 1
        else:
            unknown += 1
            for reason in found.reasons:
                by_reason[excluded_reason(reason)] += 1
    return Membership(category, true, false, unknown, MappingProxyType(by_reason), frozenset(marks))


# --- A view's variables ---------------------------------------------------------------------------

Evaluated = Answers | tuple[UnitValue, ...]
"""A variable by the reference evaluator, unit by unit: memberships' answers, or else each unit's
value (``variables.evaluate_variable``)."""


def evaluated(variable: ResolvedVariable) -> Evaluated:
    """A variable by the reference evaluator over the unit table: its memberships' answers
    (``evaluate``), or each unit's value."""
    if variable.kind == "memberships":
        return evaluate(variable)
    return evaluate_variable(variable)


def materialise_over(
    variables: Sequence[ResolvedVariable], evaluations: Sequence[Evaluated], members: Sequence[int]
) -> tuple[tuple[Materialised, ...], Joint | None]:
    """A view's variables over a cohort's members, each as ``evaluated`` gave it, by the reference
    evaluator (D382): each materialised (memberships by ``materialise``, a count of rows with its
    rows), and for two or more their joint counts over the units each gives, a membership's by
    ``membership_units``, so that a unit is known where some variable knows it; ``None`` for one
    variable, or where some memberships' listing is ``over``, which gives no units, as SQL gives
    none (``sql.CompiledMaterialised.read``). It lives here, not in ``variables``, since memberships
    are this module's and it reads them beside every other variable."""
    if len(variables) != len(evaluations):
        raise ValueError("each variable evaluated")
    found: list[Materialised] = []
    units: list[Sequence[UnitValue] | None] = []
    for variable, given in zip(variables, evaluations, strict=True):
        if isinstance(given, Answers):
            if variable.kind != "memberships":
                raise ValueError("memberships' answers are a memberships variable's")
            found.append(materialise(given, members))
            units.append(membership_units(given, members))
        else:
            found.append(materialise_values(given, members, rows=variable.kind == "rows"))
            units.append(given)
    if len(variables) < 2 or any(one is None for one in units):
        return tuple(found), None
    return tuple(found), joint([cast(Sequence[UnitValue], one) for one in units], members)


# --- Candidates -----------------------------------------------------------------------------------


def _candidates(variable: ResolvedVariable) -> list[Value]:
    """Every category that can be listed and whose answer could differ from a unit's default:
    ``fixed``, and but where only those are listed (``Answers.closed``), every PRESENT value (or
    item) of X and, for a scope column of the last step, every value its coverage tables list
    in it that is stored as X's categories are (``_of_kind``)."""
    found: dict[object, Value] = {key_part(value): value for value in fixed(variable)}
    if closed_listing(variable):
        return list(found.values())
    shape = template(variable)
    release = variable.release
    values: list[Value] = []
    rows = release.rows(shape.table)
    for row in range(len(rows)):
        cell = rows.cell(row, shape.column)
        if cell.state is not PRESENT:
            continue
        if isinstance(cell.value, tuple):
            values += [cast(Value, item.value) for item in cell.value if item.state is PRESENT]
        elif cell.value is not None:
            values.append(cast(Value, cell.value))
    if shape.scoped:
        listed = _listed_values(release, cast(Coverage, shape.coverage), shape.column)
        values += [value for value in listed if _of_kind(value, variable)]
    for value in values:
        found.setdefault(key_part(value), value)
    return list(found.values())


def _of_kind(value: Value, variable: ResolvedVariable) -> bool:
    """Whether a value a coverage table lists is stored as X's categories are, a boolean for a
    boolean column and a string otherwise (``key_part``): a value of another type is no
    category's, since no row or item of X can hold it, and is not asked about, as SQL lists none
    (``sql._Compiler.scope_values``, D381)."""
    if variable.datatype == "boolean":
        return isinstance(value, bool)
    return isinstance(value, str)


def _listed_values(release: Release, coverage: Coverage, column: str) -> list[Value]:
    """The values a coverage's tables list in a scope column (§5.6)."""
    descriptor = coverage.descriptor
    assert descriptor is not None
    parents = descriptor.fields.parents
    if isinstance(parents, DirectCoverage):
        table, mapped = parents.table, parents.scope_columns or {}
    elif isinstance(parents, GroupedCoverage):
        table, mapped = parents.groups.table, parents.groups.scope_columns or {}
    else:
        return []
    holder = next(own for own, child in mapped.items() if child == column)
    rows = release.rows(table)
    found: list[Value] = []
    for row in range(len(rows)):
        cell = rows.cell(row, holder)
        if cell.state is PRESENT and cell.value is not None and not isinstance(cell.value, tuple):
            found.append(cast(Value, cell.value))
    return found


def _fresh(release: Release) -> str:
    """A string that no cell of the release holds, nor any item of a list: longer than every
    one that does."""
    longest = 0
    for table in release.table_ids:
        rows = release.rows(table)
        for column in release.columns(table):
            for row in range(len(rows)):
                value = rows.cell(row, column).value
                items = value if isinstance(value, tuple) else ()
                for held in (value, *(item.value for item in items)):
                    if isinstance(held, str):
                        longest = max(longest, len(held))
    return "~" * (longest + 1)


__all__ = [
    "Answers",
    "Evaluated",
    "Template",
    "closed_listing",
    "evaluate",
    "evaluated",
    "fixed",
    "listing",
    "materialise",
    "materialise_over",
    "membership_units",
    "template",
    "utf16",
]
