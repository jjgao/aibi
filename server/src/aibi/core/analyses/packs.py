"""A pack's analysis run on the inputs its ``requires`` name (SPEC §8, §9.1, §10.1; D341–D344).

A pack registers an analysis as an entry and a ``run`` (§10.1); the registry lists it and matches
it for applicability (D316), and this module runs it (M3.2d, D324).

**Parameters** (D341). A view of a pack's analysis takes ``columns``, the variables (§9.2) each of
its entry's ``column`` requirements takes, by role, and ``options``, which the entry's ``params``
schema checks as they are written; phase 2 binds them (``views``). An input column's values are
categories, booleans, numbers or text (``taken``): a date or datetime column is ``NOT_SUPPORTED``,
and an identifier column's values (§5.4, ``resolve.identifying``), which name units, rows or
people, are never handed, nor a value whose ``where`` tests one (``ROW_IDS_NOT_ALLOWED``; its
rows may be counted).

**Inputs** (D342). Per position, the cohort's members with each input column's value or the reasons
it is excluded, in the order of §9.3 (``engine.inputs``: listed by SQL, ``sql.compile_inputs``, or
by the reference evaluator), handed to ``run`` column by column (``AnalysisInputs``), their keys
never: an analysis reads values, not identities. The inputs hold at most ``MAX_INPUT_CELLS`` cells
over the view's positions, one cell per member and column (one per member without columns), or the
call is refused (``TooManyCells``); the SQL listing takes no more members than that, and knows a
cohort over it from its first rows. The seed is the SHA-256 of the RFC 8785 text of
``{"computation"}``, the view's computation id, so that resampling in a pack is as reproducible as
the core's (§9.3, D338).

**Running** (D343). ``run`` gets inputs of its own, ``options`` a copy, and runs once, in the
server's process, as a pack's code runs everywhere: packs are installed through code review
(§9.1), and their code is trusted not to read what it is not handed. What it gives must be JSON
that text carries unchanged (finite numbers within ±(2^53 − 1), Unicode text, no value that holds
itself), at most ``MAX_RESULT_VALUES`` values and ``MAX_RESULT_CHARACTERS`` characters of text,
each string at most ``MAX_TEXT``, counted before each is copied, satisfying the entry's ``returns``
schema within the steps of an object of that many values, shaped as a result's ``values``
(``positions``, an object per position, and ``view``, an object) with ``not_estimable`` maps that
name null members (``Values``). Anything else, and anything raised while ``run`` runs or while
what it gave is read (which runs the pack's code too), ``BaseException``'s subclasses included but
``_PASSED`` themselves (a pack's subclass of one is its failure), refuses the call
(``PackFailed``: ``PACK_FAILED``, with a message of the core's that quotes nothing the pack gave;
the log names a built-in exception's type alone, never its message).
The call's deadline is looked at before ``run``, after it, after its values are checked and after
they are counted, and throughout the check (``TimedBudget``) and the counting (before each
column, and every ``DEADLINE_UNITS`` members): a pack's code cannot be stopped, so a call overruns
its deadline by what ``run`` takes past it, and after it by the copy, which the caps bound, and
one stretch of the core's own work between two looks.

**The result** (§8.1). The population is each position's cohort count; ``analysed`` counts, per
position, each input column's units with a value and those excluded by reason, and with two columns
or more the units some column has a value for, as ``summary.distribution``'s does (D328); without
columns, the members, none excluded. The caveats are the cohorts' (§8.3), ``UNKNOWN_EXCLUDED``
naming the values where a member is excluded for a reason other than ``NOT_APPLICABLE``, the flags
of the values read, ``COHORTS_OVERLAP`` where an analysis that assumes independent groups is
allowed cohorts that share units and they do (``AnalysisInputs.overlapping`` tells every pack
whether they do, so that it computes no between-cohort value then, §7.4), and the view's static
caveats; ``NOT_ESTIMABLE`` is the envelope's. A pack's analysis has no chart: a visual output is a
render specification among its values (§10.1).

**Disclosure** (§8.4, D344). A pack's values are an arbitrary function of every member's values,
so no rule over counts can protect them: a view of a pack's analysis is refused under any
disclosure setting, a deployment's floor included (``views.checked``), and applicability calls it
``unavailable`` there (``registry``). It is refused and unavailable where the dataset allows no
row ids too, as ``summary.members`` is (D332): it is handed each member's values in the keys'
order, which it can give back as a table of units. Without either, every count is shown and so is
what the pack gives.
"""

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue, ValidationError

from aibi.core.analyses.common import (
    CohortAt,
    Shown,
    analysed_of,
    caveat,
    cohort_caveats,
    flag_caveats,
    populations,
)
from aibi.core.engine.canonical import CanonicalVariable
from aibi.core.engine.inputs import Listed, TooManyCells, cells
from aibi.core.engine.readback import variable_readback
from aibi.core.engine.resolve import ResolvedVariable, pack_failed
from aibi.core.engine.variables import Joint, Materialised, Value
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.analyses import PackParams
from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.jsonschemas import (
    OUT_OF_STEPS,
    UNEVALUABLE,
    WRITE_STEPS_MAX,
    Checker,
    OutOfTime,
    TimedBudget,
    steps,
)
from aibi.core.schema.limits import (
    MAX_INPUT_CELLS,
    MAX_RESULT_CHARACTERS,
    MAX_RESULT_VALUES,
    MAX_TEXT,
)
from aibi.core.schema.output import Segment, data, text
from aibi.core.schema.pack_api import (
    Analysis,
    AnalysisInputs,
    InputColumn,
    InputPosition,
    JsonTooLarge,
    plain_json,
)
from aibi.core.schema.refusals import Limit
from aibi.core.schema.results import Analysed, Population, Values
from aibi.core.schema.semantics import ExclusionReason

TAKEN = ("category", "boolean", "number", "integer", "time_offset", "string")
"""The datatypes of the columns whose values an input column reads as they are (D341)."""


def taken(variable: ResolvedVariable) -> bool:
    """Whether an input column's values are ones a pack's analysis is handed (module docstring):
    an aggregate's and a question's always, a column's when it holds categories, booleans,
    numbers or text."""
    return variable.kind != "column" or variable.datatype in TAKEN


def value_datatype(variable: ResolvedVariable) -> str | None:
    """What an input column's values are (``InputColumn.datatype``)."""
    if variable.kind == "question":
        return "boolean"
    if variable.function == "count":
        return "integer"
    if variable.function == "mean":
        return "number"
    return variable.datatype


class PackFailed(Exception):  # noqa: N818 - "failed" is the refusal's word, PACK_FAILED
    """A pack's analysis that raised, or gave what is not its values (module docstring); the
    message is the core's, never the pack's, and ``limit`` the one it passed, if any."""

    def __init__(self, *message: Segment, limit: Limit | None = None) -> None:
        super().__init__("a pack's analysis failed")
        self.message = tuple(message)
        self.limit = limit


DEADLINE_UNITS = 1 << 16
"""Members counted between looks at the call's deadline."""


@dataclass(frozen=True)
class Outcome:
    """A pack analysis's digested parts, before its digest is taken, and its caveats (D343)."""

    population: list[Population]
    analysed: list[Analysed]
    values: Values
    caveats: list[Caveat]


def seed(computation: str) -> int:
    """The seed a pack's analysis is handed: the SHA-256 of the RFC 8785 text of
    ``{"computation"}``, as an integer (§9.3)."""
    return int.from_bytes(hashlib.sha256(canonical({"computation": computation})).digest())


_NAMES: dict[frozenset[ExclusionReason], tuple[str, ...]] = {}


def _names(reasons: frozenset[ExclusionReason]) -> tuple[str, ...]:
    found = _NAMES.get(reasons)
    if found is None:
        found = tuple(sorted(reason.value for reason in reasons))
        _NAMES[reasons] = found
    return found


def inputs(
    entry_id: str,
    version: str,
    variables: Sequence[tuple[str, CanonicalVariable]],
    listed: Sequence[Listed],
    params: PackParams,
    *,
    reference: int,
    overlapping: bool,
    computation: str,
) -> AnalysisInputs:
    """What ``run`` is handed (module docstring): ``variables`` each input column with its role,
    in the order ``listed`` gives their values, each listing in the order of §9.3."""
    columns = tuple(
        InputColumn(
            role=role,
            column=variable.resolved.column,
            kind=variable.resolved.kind,
            function=variable.resolved.function or variable.resolved.aggregate,
            datatype=value_datatype(variable.resolved),
            form=cast(JsonValue, json.loads(canonical(variable.form))),
        )
        for role, variable in variables
    )
    positions = tuple(
        InputPosition(
            units=one.members,
            values=tuple(one.values),
            excluded=tuple(
                tuple(_names(reasons) for reasons in excluded) for excluded in one.excluded
            ),
        )
        for one in listed
    )
    options = cast(dict[str, JsonValue], json.loads(canonical(dict(params.options or {}))))
    return AnalysisInputs(
        analysis=entry_id,
        version=version,
        columns=columns,
        positions=positions,
        reference=reference,
        overlapping=overlapping,
        options=options,
        seed=seed(computation),
    )


def run_pack(
    analysis: Analysis,
    returns: Checker,
    positions: Sequence[CohortAt],
    variables: Sequence[tuple[str, CanonicalVariable]],
    listed: Sequence[Listed],
    params: PackParams,
    *,
    reference: int,
    overlapping: bool,
    computation: str,
    ends: float | None = None,
) -> Outcome:
    """A view of a pack's analysis run (module docstring): ``listed`` each position's members
    with its input columns' values in the order of §9.3. Raises ``TooManyCells``, ``PackFailed``
    and ``CallerDeadline``."""
    if len(listed) != len(positions):
        raise ValueError("a listing per position")
    found = cells(listed)
    if found > MAX_INPUT_CELLS:
        raise TooManyCells(found, MAX_INPUT_CELLS)
    entry = analysis.entry
    handed = inputs(
        entry.id,
        entry.version,
        variables,
        listed,
        params,
        reference=reference,
        overlapping=overlapping,
        computation=computation,
    )
    _look(ends)
    given = _guarded(entry.id, "analysis", lambda: cast(object, analysis.run(handed)))
    _look(ends)
    values = _checked(entry.id, given, returns, len(positions), ends)
    _look(ends)
    population = populations(positions, None)
    analysed = [_analysed(one, ends) for one in listed]
    _look(ends)
    independent = entry.fields.assumes_independent_groups
    caveats = _caveats(positions, population, listed, overlapping and independent)
    return Outcome(population, analysed, values, caveats)


def _look(ends: float | None) -> None:
    if ends is not None and time.monotonic() >= ends:
        raise CallerDeadline


_PASSED = (MemoryError, KeyboardInterrupt, SystemExit)
"""What a pack's code raises that is no failure of the pack's: the process out of memory, or
asked to stop. Only these types themselves pass, compared by identity, never a subclass or a
type that says it equals one, and each as a new instance raised outside the handler, without the
pack's message, traceback or context."""


def _guarded[T](entry: str, stage: str, run: Callable[[], T]) -> T:
    """``run``, which runs a pack's code, its every exception but ``_PASSED`` (``BaseException``
    subclasses included, which a pack may define) a ``PackFailed`` with a message of the core's;
    the log names only a built-in exception's type (``pack_failed``)."""
    passed: type[BaseException] | None = None
    try:
        return run()
    except BaseException as error:
        kind = type(error)
        passed = next((one for one in _PASSED if kind is one), None)
        if passed is None:
            pack_failed(entry.partition(".")[0], stage, error)
    if passed is not None:
        raise passed()
    raise PackFailed(text("The pack's analysis "), data(entry), text(" failed"))


def _copied(given: object) -> JsonValue | JsonTooLarge | None:
    """What ``run`` gave, copied within a result's caps (``plain_json``): ``JsonTooLarge`` when it
    passes one, ``None`` when it is not JSON."""
    try:
        return plain_json(
            given, values=MAX_RESULT_VALUES, characters=MAX_RESULT_CHARACTERS, text=MAX_TEXT
        )
    except JsonTooLarge as large:
        return large
    except (ValueError, RecursionError):
        return None


def _checked(
    entry: str, given: object, returns: Checker, positions: int, ends: float | None
) -> Values:
    """What ``run`` gave, checked as values (module docstring): copied within its caps while the
    pack's code that reading it runs is guarded, then checked by the core's code alone, the
    ``returns`` schema within its steps and the call's deadline. No message quotes what the pack
    gave: a failing keyword is the schema's."""

    def failed(*said: Segment, limit: Limit | None = None) -> PackFailed:
        return PackFailed(
            text("The pack's analysis "), data(entry), text(" gave "), *said, limit=limit
        )

    found = _guarded(entry, "analysis's values", lambda: _copied(given))
    if isinstance(found, JsonTooLarge):
        raise failed(
            text(f"values larger than a result holds ({found.name}, at most {found.most})"),
            limit=Limit(name=found.name, max=found.most),
        )
    if found is None:
        raise failed(text("what JSON text cannot carry unchanged"))
    try:
        failures = returns.failures(found, budget=TimedBudget(steps(found, WRITE_STEPS_MAX), ends))
    except OutOfTime:
        raise CallerDeadline from None
    for failure in failures:
        if failure.keyword in (OUT_OF_STEPS, UNEVALUABLE):
            raise failed(text("values its returns schema could not evaluate"))
        raise failed(
            text("values that do not satisfy its returns schema: its keyword "),
            data(failure.keyword),
            text(" fails"),
        )
    if (
        not isinstance(found, dict)
        or set(found) != {"positions", "view"}
        or not isinstance(found["positions"], list)
        or len(found["positions"]) != positions
        or not all(isinstance(one, dict) for one in found["positions"])
        or not isinstance(found["view"], dict)
    ):
        raise failed(
            text(f"what is not values: an object of positions, {positions} objects in view "),
            text("order, and view, an object"),
        )
    try:
        return Values.model_validate(found)
    except ValidationError:
        raise failed(text("values whose not_estimable maps name no null member")) from None


def _analysed(one: Listed, ends: float | None = None) -> Analysed:
    """A position's ``analysed`` (module docstring), the call's deadline looked at before each
    column is counted and every ``DEADLINE_UNITS`` members of the joint count."""
    if not one.values:
        return Analysed(n=one.members, excluded=dict.fromkeys(ExclusionReason, 0), excluded_units=0)
    found: list[Materialised] = []
    for values, excluded, marks in zip(one.values, one.excluded, one.marks, strict=True):
        _look(ends)
        found.append(_materialised(values, excluded, marks))
    together: Joint | None = None
    if len(found) > 1:
        known = sum(
            any(reasons[member] == frozenset() for reasons in one.excluded)
            for member in range(one.members)
        )
        by_reason = dict.fromkeys(ExclusionReason, 0)
        for member in range(one.members):
            if not member % DEADLINE_UNITS:
                _look(ends)
            if any(not reasons[member] for reasons in one.excluded):
                continue
            for reason in {r for reasons in one.excluded for r in reasons[member]}:
                by_reason[reason] += 1
        together = Joint(known, one.members - known, MappingProxyType(by_reason))
    return analysed_of(found, together, [Shown(True, True)] * len(found), None)


def _materialised(
    values: Sequence[Value | None],
    excluded: Sequence[frozenset[ExclusionReason]],
    marks: frozenset[object],
) -> Materialised:
    counted: Counter[Value] = Counter(value for value in values if value is not None)
    reasons = dict.fromkeys(ExclusionReason, 0)
    units = 0
    for given in excluded:
        if given:
            units += 1
            for reason in given:
                reasons[reason] += 1
    return Materialised(
        MappingProxyType(dict(counted)), units, MappingProxyType(reasons), frozenset()
    )


def _caveats(
    positions: Sequence[CohortAt],
    population: Sequence[Population],
    listed: Sequence[Listed],
    overlapping: bool,
) -> list[Caveat]:
    """The caveats the view's data raise (module docstring)."""
    unknown = any(
        reason is not ExclusionReason.NOT_APPLICABLE
        for one in listed
        for excluded in one.excluded
        for reasons in excluded
        for reason in reasons
    )
    found = cohort_caveats(positions, population, None, values_unknown=unknown)
    found += flag_caveats(
        (mark for one in listed for marks in one.marks for mark in marks), "/values"
    )
    if overlapping:
        found.append(
            caveat(
                CaveatCode.COHORTS_OVERLAP,
                ["/values"],
                text(
                    "Cohorts of the view share units, and the pack's analysis was told to compute "
                    "no between-cohort value; compare a subset with the rest of its base instead "
                    "(§7.4)"
                ),
            )
        )
    return found


def view_readback(
    label: str,
    analysis: str,
    cohorts: int,
    reference: int | None,
    variables: Sequence[tuple[str, CanonicalVariable]],
    options: Mapping[str, JsonValue],
) -> list[Segment]:
    """The view's readback (§7.7): the analysis's label and id as data, its cohorts counted and
    its reference by position, each input column by role, from the release's descriptors, and
    its options as data."""
    found: list[Segment] = [
        text("The pack's analysis "),
        data(label),
        text(" ("),
        data(analysis),
        text(f") over the {cohorts} cohorts in view order" if cohorts > 1 else ") over the cohort"),
    ]
    if reference is not None and cohorts > 1:
        found.append(text(f", the cohort at position {reference} the reference"))
    found.append(text(", reading each member's value of each column."))
    for index, (role, variable) in enumerate(variables):
        found += [
            text(f" Column {index}, for "),
            data(role),
            text(": "),
            *variable_readback(variable),
            text("."),
        ]
    if options:
        found += [text(" Options: "), data(canonical(dict(options)).decode()), text(".")]
    found.append(
        text(
            " A member whose value of a column cannot be decided, or that has none, is handed "
            "to the analysis with its reasons and counted by reason."
        )
    )
    return found


__all__ = [
    "TAKEN",
    "Outcome",
    "PackFailed",
    "TooManyCells",
    "cells",
    "inputs",
    "run_pack",
    "seed",
    "taken",
    "value_datatype",
    "view_readback",
]
