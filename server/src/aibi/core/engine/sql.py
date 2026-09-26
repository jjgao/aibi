"""Canonical cohorts compiled to SQL: SQLGlot expression trees that DuckDB runs over a release's
table blobs (SPEC §6, §12.2, §13.3, §14; D291–D294).

The reference evaluator (``evaluate``) defines what a cohort's clauses mean; the queries built
here compute the same thing for every row at once, and the differential tests hold them to it
(§13.3): the same truth value, reasons and flags (with their relationships) for every unit, and
the same accounting.

**The relations (D291).** Every node of a canonical cohort is compiled once for each table it is
evaluated on, into a relation with one row per row of that table: ``rid``, the row's number in
its table blob (the order ``Release`` numbers rows in); ``v``, its truth value coded ``0`` FALSE,
``1`` UNKNOWN, ``2`` TRUE, so that ``all`` is the least of its operands, ``any`` the greatest and
``not`` is ``2 - v``; ``r``, the reasons of an UNKNOWN as bits in ``Reason``'s order (0
otherwise); and ``m0``, ``m1``, … the flags as bits, one for each flag and relationship the
cohort's coverage can raise, sorted, ``MARK_BITS`` to a word. A combinator stacks its operands
and groups them by row. A lookup (an up step, or several)
is a relation from a row to the row it reaches, or none (``NO_PARENT``). An existence question
counts and ORs its children's values per parent row (per row it starts from, with
``exclude_self``), and a coverage's listing is aggregated per parent row, so §6.5's steps are
integer comparisons of what they aggregate.

**Identifiers and constants (D292).** Table and column names come from descriptors, and appear
only in the select lists that read table blobs, where each is renamed to a name of the
compiler's (``c0``, ``c1``, …); every other name is the compiler's own (``b0`` and ``q0`` for the
relations, ``rid``, ``v``, ``r``, ``m0`` and the like for their columns). Every value from a
document or a descriptor (constants, bounds, permissible values, allowed values of a record
filter, scope values, unit keys, ``min_count``, blob paths) is a bound named parameter; literals
are the compiler's codes only (truth values, bits, the names of cell states). Constants are
bound in their column's stored type: datetimes as microseconds since the epoch, compared with
``epoch_us`` of the column; an integer column compared with a double after a conversion of units
with the integer bound the comparison implies; keys of two columns stored in different types
compared as the evaluator compares them (numbers by value, exactly; nothing else equal).

**Determinism (D294).** The counts come from ``COUNT``, and flags and reasons from ``BIT_OR``
and ``BOOL_OR`` over integers; no aggregate over a double enters a digest (§9.3). Rows are
returned by ``rid``.

**Members (D333).** ``compile_members`` lists a cohort's members' unit keys, one row per member,
each key column as it is stored, dates and datetimes as integers; the server orders them as the
canonical form orders keys (``members.ordered``), since DuckDB's collation is not that order.
"""

import json
import math
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePath
from types import MappingProxyType
from typing import Literal, cast, overload

from pydantic import JsonValue
from sqlglot import exp
from sqlglot.expressions.core import Expression

from aibi.core.engine.canonical import canonical_clause, variable_form
from aibi.core.engine.data import KeyPart, Release, key_part
from aibi.core.engine.graph import Path, Step
from aibi.core.engine.resolve import (
    Coverage,
    Function,
    ResolvedCohort,
    ResolvedVariable,
    levels,
)
from aibi.core.engine.resolved import (
    Constant,
    RAll,
    RAny,
    RClause,
    RCovered,
    RExists,
    RIds,
    RKnown,
    RNot,
    RUnknown,
    RValue,
    Values,
    flipped,
    questions,
)
from aibi.core.engine.truth import Mark, Truth, TruthValue
from aibi.core.engine.units import scale
from aibi.core.engine.variables import Joint, Materialised, Value, aggregated, normalised
from aibi.core.engine.worker import CallerDeadline, QueryError, Rows
from aibi.core.schema.descriptors import DirectCoverage, GroupedCoverage
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.semantics import ExclusionReason, Flag, ObservationState, Reason
from aibi.core.store.parquet import PhysicalType
from aibi.core.store.tables import ITEM_STATE, STATE, TableSource, physical

FALSE_CODE, UNKNOWN_CODE, TRUE_CODE = 0, 1, 2
"""Truth values as the relations code them: ``all`` is the least, ``any`` the greatest."""
_CODES = {FALSE_CODE: Truth.FALSE, UNKNOWN_CODE: Truth.UNKNOWN, TRUE_CODE: Truth.TRUE}
_CODE_OF = {truth: code for code, truth in _CODES.items()}
REASON_BIT = {reason: 1 << index for index, reason in enumerate(Reason)}
_ALL_REASONS = sum(REASON_BIT.values())
EXCLUSION_BIT = {reason: 1 << index for index, reason in enumerate(ExclusionReason)}
"""An exclusion's reasons as bits in ``ExclusionReason``'s order, whose first reasons are
``Reason``'s in its order, so a truth value's reason bits are its exclusion bits (D327)."""
_ALL_EXCLUSIONS = sum(EXCLUSION_BIT.values())
assert all(
    EXCLUSION_BIT[ExclusionReason(reason.value)] == bit for reason, bit in REASON_BIT.items()
)
MARK_BITS = 63
"""Flags per word: the bits of a signed 64-bit integer below its sign."""
ROW_NUMBER = "file_row_number"
"""DuckDB's column of a Parquet file's row numbers, from 0 in the file's order."""
_DROP = {
    "strict": REASON_BIT[Reason.OUT_OF_SCOPE],
    "assessed": REASON_BIT[Reason.OUT_OF_SCOPE] | REASON_BIT[Reason.NOT_COVERED],
}
"""The reasons for which an intermediate question drops a child, by lift rule (§6.5, step 3)."""
_INT64 = (-(2**63), 2**63 - 1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_NUMERIC: tuple[PhysicalType, ...] = ("int64", "float64")

Scalar = int | float | str | bool | date
Parameter = Scalar | tuple[Scalar, ...]
"""A bound value: a scalar, or a list (held as a tuple) for a membership test or an unnested list
of keys."""


class CompileError(ValueError):
    """A release the compiler cannot query: a table without its blob, a column its blob lacks, or
    a table with a column named as DuckDB's row numbers (D292)."""


@dataclass(frozen=True)
class Accounting:
    """A cohort's accounting (§6.6), as ``CohortResult`` has it."""

    n_true: int
    n_false: int
    n_unknown: int
    unknown_by_reason: Mapping[Reason, int]
    unknown_by_clause: tuple[int, ...]
    lift_differs: int
    marks: frozenset[Mark]

    @property
    def flags(self) -> frozenset[Flag]:
        return frozenset(mark.flag for mark in self.marks)


@dataclass(frozen=True)
class CompiledCohort:
    """A cohort's two queries as rendered for DuckDB, their parameters, and how to read their
    rows; nothing of it can be changed."""

    counts_sql: str
    """One row: ``n_true``, ``n_false``, ``n_unknown``, the unknown units by reason (in
    ``Reason``'s order) and by top-level clause, ``lift_differs``, and the flag words."""
    values_sql: str
    """One row per unit, by ``rid``: ``rid``, ``v``, ``r`` and the flag words."""
    parameters: Mapping[str, Parameter]
    """By name, in a mapping that cannot be changed; a list is bound as a tuple."""
    marks: tuple[Mark, ...]
    """The flag each bit stands for, bit ``i`` in word ``i // MARK_BITS``."""
    clauses: int
    blobs: frozenset[str] = frozenset()
    """The parameters that are blob paths."""

    @property
    def paths(self) -> tuple[str, ...]:
        """The blobs the queries read, the only files they may open (D293)."""
        return tuple(sorted(str(self.parameters[name]) for name in self.blobs))

    @property
    def words(self) -> int:
        return _words(len(self.marks))

    @property
    def counts_columns(self) -> int:
        """The counts query's columns: three counts, one per reason and per top-level clause,
        ``lift_differs`` and the flag words."""
        return 3 + len(Reason) + self.clauses + 1 + self.words

    @property
    def values_columns(self) -> int:
        """The values query's columns: ``rid``, ``v``, ``r`` and the flag words."""
        return 3 + self.words

    def accounting(self, row: Sequence[object]) -> Accounting:
        """The counts query's row read. Raises ``QueryError`` for a row the query does not give:
        of another length, or with a count below 0 or a flag word outside its bits."""
        if len(row) != self.counts_columns or any(type(value) is not int for value in row):
            raise QueryError("the counts query gave a row of another shape")
        numbers = cast(Sequence[int], row)
        words = numbers[len(numbers) - self.words :]
        if min(numbers) < 0 or not _words_fit(words, self.marks):
            raise QueryError("the counts query gave a count or flag it cannot give")
        reasons = len(Reason)
        n_true, n_false, n_unknown = numbers[:3]
        by_reason = dict(zip(Reason, numbers[3 : 3 + reasons], strict=True))
        at = 3 + reasons
        by_clause = tuple(numbers[at : at + self.clauses])
        at += self.clauses
        return Accounting(
            n_true,
            n_false,
            n_unknown,
            MappingProxyType(by_reason),
            by_clause,
            numbers[at],
            _marks(self.marks, numbers[at + 1 :]),
        )

    def truth_values(self, rows: Rows) -> "TruthValues":
        """The values query's rows read, one truth value per unit in row order, each made as it
        is read (``TruthValues``). Raises ``QueryError`` for rows the query does not give: of
        another width, out of row order, with a code or reasons no truth value has, or a flag
        word outside its bits."""
        columns = rows.columns
        if len(columns) != self.values_columns:
            raise QueryError("the values query gave rows of another width")
        rid, v, r, *words = columns
        if not all(found == index for index, found in enumerate(rid)):
            raise QueryError("the values query gave its rows out of row order")
        if len(rows) and not (
            min(v) >= FALSE_CODE
            and max(v) <= TRUE_CODE
            and min(r) >= 0
            and max(r) <= _ALL_REASONS
            and all(min(word) >= 0 for word in words)
            and _words_fit([max(word) for word in words], self.marks)
            and all((code == UNKNOWN_CODE) == (bits != 0) for code, bits in zip(v, r, strict=True))
        ):
            raise QueryError("the values query gave a truth value it cannot give")
        return TruthValues(v, r, tuple(words), self.marks)

    def parameters_json(self) -> dict[str, JsonValue]:
        """The parameters as the derivation log records them beside the SQL: dates in ISO form,
        and blob paths as the blobs' digests, which name what was read and not where the server
        keeps it (D292)."""
        return {
            name: PurePath(str(value)).name if name in self.blobs else _json(value)
            for name, value in self.parameters.items()
        }


class TruthValues(Sequence[TruthValue]):
    """Each unit's truth value, in row order, read from the values query's columns as they are
    packed in the worker's answer: a truth value is made when it is read, so that the rows of an
    answer make no object per row (D293). ``CompiledCohort.truth_values`` checks the columns
    first, so reading one cannot fail."""

    __slots__ = ("_codes", "_marks", "_reasons", "_words")

    def __init__(
        self,
        codes: Sequence[int],
        reasons: Sequence[int],
        words: tuple[Sequence[int], ...],
        marks: tuple[Mark, ...],
    ) -> None:
        self._codes = codes
        self._reasons = reasons
        self._words = words
        self._marks = marks

    def __len__(self) -> int:
        return len(self._codes)

    @overload
    def __getitem__(self, index: int) -> TruthValue: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[TruthValue]: ...

    def __getitem__(self, index: int | slice) -> TruthValue | Sequence[TruthValue]:
        if isinstance(index, slice):
            return [self[at] for at in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        bits = self._reasons[index]
        return TruthValue(
            _CODES[self._codes[index]],
            frozenset(reason for reason, bit in REASON_BIT.items() if bits & bit),
            _marks(self._marks, [word[index] for word in self._words]),
        )

    def __iter__(self) -> Iterator[TruthValue]:
        for index in range(len(self)):
            yield self[index]

    @property
    def codes(self) -> Sequence[int]:
        """Each unit's truth value as its code: ``FALSE_CODE``, ``UNKNOWN_CODE`` or
        ``TRUE_CODE``."""
        return self._codes

    @property
    def reason_bits(self) -> Sequence[int]:
        """Each unit's reasons as bits in ``Reason``'s order (``REASON_BIT``); 0 unless
        UNKNOWN."""
        return self._reasons

    def marks_of(self, rows: Iterable[int]) -> frozenset[Mark]:
        """The flags, with their relationships, of the truth values of ``rows``, together."""
        words = [0] * len(self._words)
        for row in rows:
            for at, word in enumerate(self._words):
                words[at] |= word[row]
        return _marks(self._marks, words) if words else frozenset()

    @classmethod
    def of(cls, values: Sequence[TruthValue]) -> "TruthValues":
        """Truth values the reference evaluator gave, packed as the values query packs its
        rows, so that what reads one reads the other alike (§13.3)."""
        marks = tuple(sorted({mark for value in values for mark in value.marks}))
        index = {mark: at for at, mark in enumerate(marks)}
        words = [[0] * len(values) for _ in range(_words(len(marks)))]
        codes = [_CODE_OF[value.value] for value in values]
        reasons = [sum(REASON_BIT[reason] for reason in value.reasons) for value in values]
        for row, value in enumerate(values):
            for mark in value.marks:
                bit = index[mark]
                words[bit // MARK_BITS][row] |= 1 << (bit % MARK_BITS)
        return cls(codes, reasons, tuple(words), marks)


def _marks(marks: tuple[Mark, ...], words: Sequence[int]) -> frozenset[Mark]:
    """The flags whose bits are set in ``words``, bit ``i`` in word ``i // MARK_BITS``."""
    return frozenset(
        mark
        for index, mark in enumerate(marks)
        if words[index // MARK_BITS] >> (index % MARK_BITS) & 1
    )


def _words_fit(words: Sequence[int], marks: tuple[Mark, ...]) -> bool:
    """Whether the largest value of each flag word sets no bit that stands for no flag."""
    for at, word in enumerate(words):
        used = min(MARK_BITS, len(marks) - at * MARK_BITS)
        if word >= 1 << used:
            return False
    return True


def _json(value: Parameter) -> JsonValue:
    if isinstance(value, tuple):
        return [_scalar_json(item) for item in value]
    return _scalar_json(value)


def _scalar_json(value: Scalar) -> JsonValue:
    return value.isoformat() if isinstance(value, date) else value


def _words(marks: int) -> int:
    return -(-marks // MARK_BITS)


# --- Crossings (D318) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossedSplit:
    """A predicate over the units of a cohort: those for which it is TRUE, FALSE and UNKNOWN, the
    UNKNOWN ones by reason, those whose answer the other lift rule changes, and the flags its
    truth values carry there."""

    true: int
    false: int
    unknown: int
    unknown_by_reason: Mapping[Reason, int]
    lift_differs: int
    marks: frozenset[Mark]


@dataclass(frozen=True)
class Crossed:
    """What a crossing gives of one cohort: each predicate's split of its units, and its units
    for which some predicate is known and for which none is, those by every reason a predicate
    gave them."""

    splits: tuple[CrossedSplit, ...]
    known: int
    none_known: int
    none_known_by_reason: Mapping[Reason, int]


def pairs(count: int) -> list[tuple[int, int]]:
    """The pairs of ``count`` positions, each once, in order."""
    return [(first, second) for first in range(count) for second in range(first + 1, count)]


@dataclass(frozen=True)
class Crossing:
    """Cohorts crossed with predicates over one unit table: each cohort's ``Crossed``, and the
    units each pair of cohorts shares (``pairs``' order)."""

    cohorts: tuple[Crossed, ...]
    shared: tuple[int, ...]

    def overlapping(self) -> list[tuple[int, int]]:
        """The pairs of cohorts that share units (§7.4)."""
        return [
            pair for pair, count in zip(pairs(len(self.cohorts)), self.shared, strict=True) if count
        ]


def cross(
    cohorts: Sequence[TruthValues],
    predicates: Sequence[TruthValues],
    lifted: Sequence[TruthValues | None],
) -> Crossing:
    """The crossing of each unit's truth values, as the reference evaluator gives them: what
    ``compile_crossing``'s queries count, and the differential tests hold them to (§13.3)."""
    found: list[Crossed] = []
    held: list[set[int]] = []
    for cohort in cohorts:
        rows = [row for row, code in enumerate(cohort.codes) if code == TRUE_CODE]
        held.append(set(rows))
        splits: list[CrossedSplit] = []
        for predicate, other in zip(predicates, lifted, strict=True):
            codes, bits = predicate.codes, predicate.reason_bits
            counts = {FALSE_CODE: 0, UNKNOWN_CODE: 0, TRUE_CODE: 0}
            by_reason = dict.fromkeys(Reason, 0)
            for row in rows:
                counts[codes[row]] += 1
                for reason, bit in REASON_BIT.items():
                    if bits[row] & bit:
                        by_reason[reason] += 1
            lift = 0 if other is None else sum(other.codes[row] != codes[row] for row in rows)
            splits.append(
                CrossedSplit(
                    counts[TRUE_CODE],
                    counts[FALSE_CODE],
                    counts[UNKNOWN_CODE],
                    MappingProxyType(by_reason),
                    lift,
                    predicate.marks_of(rows),
                )
            )
        known = none = 0
        none_by_reason = dict.fromkeys(Reason, 0)
        for row in rows:
            if any(predicate.codes[row] != UNKNOWN_CODE for predicate in predicates):
                known += 1
                continue
            none += 1
            union = 0
            for predicate in predicates:
                union |= predicate.reason_bits[row]
            for reason, bit in REASON_BIT.items():
                if union & bit:
                    none_by_reason[reason] += 1
        found.append(Crossed(tuple(splits), known, none, MappingProxyType(none_by_reason)))
    shared = tuple(len(held[first] & held[second]) for first, second in pairs(len(cohorts)))
    return Crossing(tuple(found), shared)


@dataclass(frozen=True)
class CompiledCrossing:
    """The queries of a crossing as rendered for DuckDB (``compile_crossing``): one per cohort,
    one pass over its units joined once with every predicate (``GROUPING SETS``), whose rows
    count its units by each predicate's code and reasons, and by whether some
    predicate is known and the reasons of all, and, for two cohorts or more, one whose row counts
    the units each pair shares. Every count is an integer ``COUNT`` and every flag a ``BIT_OR``,
    so the answer's size depends on the number of cohorts and predicates, never on the units
    (D294, D318)."""

    statements: tuple[str, ...]
    parameters: Mapping[str, Parameter]
    marks: tuple[Mark, ...]
    cohorts: int
    predicates: int
    blobs: frozenset[str] = frozenset()

    @property
    def paths(self) -> tuple[str, ...]:
        """The blobs the queries read, the only files they may open (D293)."""
        return tuple(sorted(str(self.parameters[name]) for name in self.blobs))

    @property
    def columns(self) -> tuple[int, ...]:
        """Each statement's columns: a cohort's ``t``, ``v``, ``r``, ``n``, ``d`` and the flag
        words; the overlaps' one per pair."""
        words = _words(len(self.marks))
        found = [5 + words] * self.cohorts
        if self.cohorts > 1:
            found.append(len(pairs(self.cohorts)))
        return tuple(found)

    def parameters_json(self) -> dict[str, JsonValue]:
        """The parameters as the derivation log records them (``CompiledCohort``'s)."""
        return {
            name: PurePath(str(value)).name if name in self.blobs else _json(value)
            for name, value in self.parameters.items()
        }

    def read(self, answers: Sequence[Rows]) -> Crossing:
        """The queries' rows read. Raises ``QueryError`` for rows the queries do not give: of
        another width, with a tag, code, reasons, count or flag word no crossing has, or whose
        predicates count other units than the cohort has."""
        if len(answers) != len(self.columns):
            raise QueryError("a crossing gave another number of answers")
        for rows, width in zip(answers, self.columns, strict=True):
            if len(rows.columns) != width:
                raise QueryError("a crossing's query gave rows of another width")
        found = [self._cohort(answers[index]) for index in range(self.cohorts)]
        shared: tuple[int, ...] = ()
        if self.cohorts > 1:
            rows = answers[self.cohorts]
            if len(rows) != 1 or min(rows[0], default=0) < 0:
                raise QueryError("a crossing's overlaps query gives one row of counts")
            shared = tuple(rows[0])
        return Crossing(tuple(found), shared)

    def _cohort(self, rows: Rows) -> Crossed:
        predicates = self.predicates
        counts = [{FALSE_CODE: 0, UNKNOWN_CODE: 0, TRUE_CODE: 0} for _ in range(predicates)]
        by_reason = [dict.fromkeys(Reason, 0) for _ in range(predicates)]
        lifts = [0] * predicates
        words = [[0] * _words(len(self.marks)) for _ in range(predicates)]
        known = none = 0
        none_by_reason = dict.fromkeys(Reason, 0)
        for row in rows:
            tag, v, r, n, d, *flags = row
            if not (
                0 <= tag <= predicates
                and 0 <= r <= _ALL_REASONS
                and n > 0
                and 0 <= d <= n
                and all(word >= 0 for word in flags)
                and _words_fit(flags, self.marks)
            ):
                raise QueryError("a crossing's query gave a row it cannot give")
            if tag == predicates:
                if v not in (0, 1) or (v == 1) != (r == 0) or d or any(flags):
                    raise QueryError("a crossing's query gave a row it cannot give")
                if v:
                    known += n
                    continue
                none += n
                for reason, bit in REASON_BIT.items():
                    if r & bit:
                        none_by_reason[reason] += n
                continue
            if v not in counts[tag] or (v == UNKNOWN_CODE) != (r != 0):
                raise QueryError("a crossing's query gave a truth value it cannot give")
            counts[tag][v] += n
            lifts[tag] += d
            for reason, bit in REASON_BIT.items():
                if r & bit:
                    by_reason[tag][reason] += n
            for at, word in enumerate(flags):
                words[tag][at] |= word
        units = known + none
        splits: list[CrossedSplit] = []
        for tag in range(predicates):
            if sum(counts[tag].values()) != units:
                raise QueryError("a crossing's predicates count other units than its cohort")
            splits.append(
                CrossedSplit(
                    counts[tag][TRUE_CODE],
                    counts[tag][FALSE_CODE],
                    counts[tag][UNKNOWN_CODE],
                    MappingProxyType(by_reason[tag]),
                    lifts[tag],
                    _marks(self.marks, words[tag]) if words[tag] else frozenset(),
                )
            )
        return Crossed(tuple(splits), known, none, MappingProxyType(none_by_reason))


def compile_crossing(
    cohorts: Sequence[ResolvedCohort],
    predicates: Sequence[ResolvedCohort],
    sources: Mapping[str, TableSource],
) -> CompiledCrossing:
    """The queries that cross cohorts with predicates over one release's unit table, their
    parts compiled once each over one compiler (a node two of them share is one relation), with
    each predicate's other lift rule where it holds a lift. Raises ``CompileError``."""
    parts = [*cohorts, *predicates]
    if not cohorts or not predicates:
        raise CompileError("a crossing has cohorts and predicates")
    first = parts[0]
    if any(part.release is not first.release or part.unit != first.unit for part in parts):
        raise CompileError("a crossing's parts share one release and one unit table")
    coverage: dict[str, Coverage] = {}
    for part in parts:
        coverage.update(part.coverage)
    merged = replace(first, coverage=dict(sorted(coverage.items())))
    return _Compiler(merged, sources).crossing(cohorts, predicates)


# --- Materialised variables (D326, D327) ----------------------------------------------------------

_VALUE_ROWS, _EXCLUDED_ROWS, _UNIT_ROWS, _EMPTY_ROWS, _EXTREME_ROWS = 0, 1, 2, 3, 4
"""The kinds of a materialised variable's rows (``CompiledMaterialised``)."""
_DEADLINE_ROWS = 1 << 16
"""How many rows the server reads of a materialisation between looks at the call's deadline."""
_MATERIALISED_VALUE = 2
"""The index of the value column of a materialised variable's rows."""


@dataclass(frozen=True)
class _Shape:
    """What reading a materialised variable's rows needs of it."""

    kind: str
    datatype: str | None
    function: str | None
    order: tuple[str, ...] | None
    empty: Value | None


@dataclass(frozen=True)
class CompiledMaterialised:
    """The queries that materialise variables over cohorts of one release's unit table
    (``compile_materialised``): for each cohort, one per variable, whose rows are tagged ``t``,
    with ``r``, ``v``, ``x``, ``n`` and the flag words (``_ROWS`` kinds): the units of each
    value (``_UNIT_ROWS``: ``v`` and ``n`` units), those excluded for each set of reasons
    (``_EXCLUDED_ROWS``: ``x`` their bits, ``n`` units), those of an aggregate with no value to
    aggregate that take ``empty`` (``_EMPTY_ROWS``), for ``max`` and ``min`` the units of each
    greatest or least value (``_EXTREME_ROWS``: ``v`` and ``n`` units; an ordered category's by
    listed position), which pick a value and so are exact in SQL, and for ``mean`` each unit's
    pooled rows by value (``_VALUE_ROWS``: ``r`` the unit's row, ``v`` the value, ``n`` its
    rows), which the server aggregates (§9.3); and, for two variables or more, one joint query
    per cohort, whose rows count its units by whether some variable has a value (``k``) and, for
    none, the bits of every reason (``b``). Every count is an integer ``COUNT`` and every flag a
    ``BIT_OR``; values are grouped, and never summed or averaged, by DuckDB, whose ``MAX`` and
    ``MIN`` only pick one of them (D294, D327)."""

    statements: tuple[str, ...]
    parameters: Mapping[str, Parameter]
    marks: tuple[Mark, ...]
    cohorts: int
    shapes: tuple[_Shape, ...]
    blobs: frozenset[str] = frozenset()

    @property
    def paths(self) -> tuple[str, ...]:
        """The blobs the queries read, the only files they may open (D293)."""
        return tuple(sorted(str(self.parameters[name]) for name in self.blobs))

    @property
    def joint(self) -> bool:
        return len(self.shapes) > 1

    @property
    def columns(self) -> tuple[int, ...]:
        """Each statement's columns: a variable's ``t``, ``r``, ``v``, ``x``, ``n`` and the flag
        words, and a joint query's ``k``, ``b`` and ``n``."""
        words = _words(len(self.marks))
        found: list[int] = []
        for _ in range(self.cohorts):
            found += [5 + words] * len(self.shapes)
            if self.joint:
                found.append(3)
        return tuple(found)

    @property
    def values(self) -> tuple[frozenset[int], ...]:
        """Each statement's value columns: a variable's ``v``."""
        found: list[frozenset[int]] = []
        for _ in range(self.cohorts):
            found += [frozenset({_MATERIALISED_VALUE})] * len(self.shapes)
            if self.joint:
                found.append(frozenset())
        return tuple(found)

    def parameters_json(self) -> dict[str, JsonValue]:
        """The parameters as the derivation log records them (``CompiledCohort``'s)."""
        return {
            name: PurePath(str(value)).name if name in self.blobs else _json(value)
            for name, value in self.parameters.items()
        }

    def read(
        self, answers: Sequence[Rows], ends: float | None = None
    ) -> "tuple[tuple[tuple[Materialised, ...], Joint | None], ...]":
        """The queries' rows read: for each cohort, each variable materialised and, for two or
        more, their joint accounting. Raises ``QueryError`` for rows the queries do not give,
        and ``CallerDeadline`` once ``time.monotonic()`` passes ``ends`` (the call's deadline,
        looked at every ``_DEADLINE_ROWS`` rows, and every ``_DEADLINE_ROWS`` units while a
        ``mean``'s units are averaged: its rows are one per unit and value, read on the server
        after the worker has answered)."""
        if len(answers) != len(self.columns):
            raise QueryError("a materialisation gave another number of answers")
        for rows, width in zip(answers, self.columns, strict=True):
            if rows.width != width:
                raise QueryError("a materialisation's query gave rows of another width")
        found: list[tuple[tuple[Materialised, ...], Joint | None]] = []
        at = 0
        for _ in range(self.cohorts):
            read = tuple(
                self._variable(answers[at + index], shape, ends)
                for index, shape in enumerate(self.shapes)
            )
            at += len(self.shapes)
            together: Joint | None = None
            if self.joint:
                together = self._joint(answers[at])
                at += 1
            found.append((read, together))
        return tuple(found)

    def _variable(self, rows: Rows, shape: _Shape, ends: float | None) -> Materialised:
        tags, rids = rows.integer_column(0), rows.integer_column(1)
        bits, counts = rows.integer_column(3), rows.integer_column(4)
        words = [rows.integer_column(5 + at) for at in range(_words(len(self.marks)))]
        raw = rows.value_column(_MATERIALISED_VALUE)
        values: Counter[Value] = Counter()
        excluded = dict.fromkeys(ExclusionReason, 0)
        excluded_units = 0
        flags = [0] * len(words)
        by_unit: dict[int, list[tuple[Value, int]]] = {}
        for index in range(len(rows)):
            if ends is not None and not index % _DEADLINE_ROWS and time.monotonic() >= ends:
                raise CallerDeadline
            tag, count, bit = tags[index], counts[index], bits[index]
            if count <= 0 or not 0 <= bit <= _ALL_EXCLUSIONS:
                raise QueryError("a materialisation's query gave a row it cannot give")
            for at, word in enumerate(words):
                if word[index] < 0:
                    raise QueryError("a materialisation's query gave a flag it cannot give")
                flags[at] |= word[index]
            if tag == _EXCLUDED_ROWS:
                if not bit:
                    raise QueryError("a materialisation's query gave a row it cannot give")
                excluded_units += count
                for reason, flag in EXCLUSION_BIT.items():
                    if bit & flag:
                        excluded[reason] += count
            elif tag == _UNIT_ROWS and not bit:
                values[self._value(raw[index], shape)] += count
            elif tag == _EMPTY_ROWS and not bit and shape.empty is not None:
                values[shape.empty] += count
            elif tag == _EXTREME_ROWS and not bit and shape.function in ("max", "min"):
                values[self._extreme(raw[index], shape)] += count
            elif tag == _VALUE_ROWS and not bit and shape.function == "mean":
                by_unit.setdefault(rids[index], []).append((self._value(raw[index], shape), count))
            else:
                raise QueryError("a materialisation's query gave a row it cannot give")
        if not _words_fit(flags, self.marks):
            raise QueryError("a materialisation's query gave a flag it cannot give")
        for unit, groups in enumerate(by_unit.values()):
            if ends is not None and not unit % _DEADLINE_ROWS and time.monotonic() >= ends:
                raise CallerDeadline
            values[self._aggregate(groups, shape)] += 1
        return Materialised(
            MappingProxyType(dict(values)),
            excluded_units,
            MappingProxyType(excluded),
            _marks(self.marks, flags) if flags else frozenset(),
        )

    @staticmethod
    def _value(raw: object, shape: _Shape) -> Value:
        """A value as its query's rows hold it, in its stored type (``variables.normalised``)."""
        if shape.kind == "question" or (shape.datatype == "boolean" and shape.function is None):
            if raw not in (0, 1) or isinstance(raw, float):
                raise QueryError("a materialisation's query gave a value it cannot give")
            return bool(raw)
        if shape.function == "count":
            if not isinstance(raw, int) or raw < 0:
                raise QueryError("a materialisation's query gave a value it cannot give")
            return raw
        if shape.order is not None:
            if not isinstance(raw, int) or not 0 <= raw < len(shape.order):
                raise QueryError("a materialisation's query gave a value it cannot give")
            return raw
        if shape.datatype in ("category", "string"):
            if not isinstance(raw, str):
                raise QueryError("a materialisation's query gave a value it cannot give")
            return raw
        if isinstance(raw, str) or (shape.datatype == "integer" and isinstance(raw, float)):
            raise QueryError("a materialisation's query gave a value it cannot give")
        if isinstance(raw, float) and not math.isfinite(raw):
            raise QueryError("a materialisation's query gave a value it cannot give")
        return normalised(cast(Value, raw), shape.datatype)

    @classmethod
    def _extreme(cls, raw: object, shape: _Shape) -> Value:
        """A unit's ``max`` or ``min`` as SQL picked it: an ordered category's value at its
        listed position, else the value normalised as ``aggregated``'s is."""
        found = cls._value(raw, shape)
        if shape.order is not None:
            return shape.order[int(found)]
        return normalised(found, shape.datatype, cast(Function, shape.function))

    @staticmethod
    def _aggregate(groups: Sequence[tuple[Value, int]], shape: _Shape) -> Value:
        found = aggregated(
            cast(Function, shape.function), groups, sum(times for _, times in groups)
        )
        assert found is not None, "a unit's value rows hold values"
        if shape.order is not None:
            return shape.order[int(found)]
        return normalised(found, shape.datatype, cast(Function, shape.function))

    @staticmethod
    def _joint(rows: Rows) -> Joint:
        known = none = 0
        by_reason = dict.fromkeys(ExclusionReason, 0)
        for k, b, n in rows:
            if k not in (0, 1) or n <= 0 or not 0 <= b <= _ALL_EXCLUSIONS or (k == 1) != (b == 0):
                raise QueryError("a materialisation's joint query gave a row it cannot give")
            if k:
                known += n
                continue
            none += n
            for reason, flag in EXCLUSION_BIT.items():
                if b & flag:
                    by_reason[reason] += n
        return Joint(known, none, MappingProxyType(by_reason))


def compile_materialised(
    cohorts: Sequence[ResolvedCohort],
    variables: Sequence[ResolvedVariable],
    sources: Mapping[str, TableSource],
) -> CompiledMaterialised:
    """The queries that materialise variables over cohorts of one release's unit table, their
    parts compiled once each over one compiler. Raises ``CompileError``."""
    if not cohorts or not variables:
        raise CompileError("a materialisation has cohorts and variables")
    first = cohorts[0]
    if any(
        part.release is not first.release or part.unit != first.unit
        for part in (*cohorts, *variables)
    ):
        raise CompileError("a materialisation's parts share one release and one unit table")
    coverage: dict[str, Coverage] = {}
    for part in (*cohorts, *variables):
        coverage.update(part.coverage)
    merged = replace(first, coverage=dict(sorted(coverage.items())))
    return _Compiler(merged, sources).materialised(cohorts, variables)


_KEY_VALUES: tuple[PhysicalType, ...] = ("float64", "string")
"""The stored types of key columns a members query answers as value columns (D327): doubles and
text; the others it answers as integers (``CompiledMembers``)."""
_EPOCH_DAY = date(1970, 1, 1)


@dataclass(frozen=True)
class CompiledMembers:
    """The query that lists a cohort's members' unit keys (``compile_members``, D333): one row per
    member, by ``rid``, its key columns in key order, each as it is stored but a date as its days
    since 1970-01-01 and a datetime as its microseconds since the epoch (integers), a boolean as
    0 or 1; doubles and text are value columns (``Query.values``, D327). The rows are not in the
    keys' order: the server orders them (``members.ordered``), never DuckDB's collation."""

    statement: str
    parameters: Mapping[str, Parameter]
    kinds: tuple[PhysicalType, ...]
    """Each key column's stored type, in key order."""
    blobs: frozenset[str] = frozenset()

    @property
    def paths(self) -> tuple[str, ...]:
        """The blobs the query reads, the only files it may open (D293)."""
        return tuple(sorted(str(self.parameters[name]) for name in self.blobs))

    @property
    def columns(self) -> int:
        return len(self.kinds)

    @property
    def values(self) -> frozenset[int]:
        """The value columns: those of doubles and of text."""
        return frozenset(at for at, kind in enumerate(self.kinds) if kind in _KEY_VALUES)

    def parameters_json(self) -> dict[str, JsonValue]:
        """The parameters as the derivation log records them (``CompiledCohort``'s)."""
        return {
            name: PurePath(str(value)).name if name in self.blobs else _json(value)
            for name, value in self.parameters.items()
        }

    def read(self, rows: Rows, ends: float | None = None) -> tuple[tuple[Constant, ...], ...]:
        """The query's rows read, one key per member in the stored types the reference evaluator
        reads (``members.keys``). Raises ``QueryError`` for rows the query does not give, and
        ``CallerDeadline`` once ``time.monotonic()`` passes ``ends``, looked at every
        ``_DEADLINE_ROWS`` rows (a key per member, read after the worker has answered)."""
        if rows.width != self.columns:
            raise QueryError("a members query gave rows of another width")
        columns = [
            rows.value_column(at) if kind in _KEY_VALUES else rows.integer_column(at)
            for at, kind in enumerate(self.kinds)
        ]
        found: list[tuple[Constant, ...]] = []
        for index in range(len(rows)):
            if ends is not None and not index % _DEADLINE_ROWS and time.monotonic() >= ends:
                raise CallerDeadline
            found.append(
                tuple(
                    _key_value(column[index], kind)
                    for column, kind in zip(columns, self.kinds, strict=True)
                )
            )
        return tuple(found)


def _key_value(raw: object, kind: PhysicalType) -> Constant:
    """A key's value as a members query answers it, in its stored type (``CompiledMembers``)."""
    if kind == "string":
        if not isinstance(raw, str):
            raise QueryError("a members query gave a key it cannot give")
        return raw
    if kind == "float64":
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
            raise QueryError("a members query gave a key it cannot give")
        return float(raw)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise QueryError("a members query gave a key it cannot give")
    if kind == "int64":
        return raw
    if kind == "bool":
        if raw not in (0, 1):
            raise QueryError("a members query gave a key it cannot give")
        return bool(raw)
    try:
        if kind == "date32":
            return _EPOCH_DAY + timedelta(days=raw)
        if kind == "timestamp":
            return _EPOCH + raw * _MICROSECOND
    except OverflowError:
        raise QueryError("a members query gave a key it cannot give") from None
    raise QueryError("a members query gave a key of a type no key has")


def compile_members(cohort: ResolvedCohort, sources: Mapping[str, TableSource]) -> CompiledMembers:
    """The query that lists a cohort's members' keys over its release's table blobs (D333).
    Raises ``CompileError``."""
    return _Compiler(cohort, sources).members()


def compile_cohort(cohort: ResolvedCohort, sources: Mapping[str, TableSource]) -> CompiledCohort:
    """A resolved cohort's queries over its release's table blobs, ``sources`` by table id.
    Raises ``CompileError``."""
    return _Compiler(cohort, sources).compile()


# --- Expressions ------------------------------------------------------------------------------


def _id(name: str) -> exp.Identifier:
    return exp.Identifier(this=name, quoted=True)


def _col(alias: str, name: str) -> exp.Column:
    return exp.Column(this=_id(name), table=_id(alias))


def _num(value: int) -> Expression:
    return exp.Literal.number(value)


def _as(expression: Expression, name: str) -> exp.Alias:
    return exp.Alias(this=expression, alias=_id(name))


def _table(name: str, alias: str) -> exp.Table:
    return exp.Table(this=_id(name), alias=exp.TableAlias(this=_id(alias)))


def _fn(name: str, *arguments: Expression) -> exp.Anonymous:
    return exp.Anonymous(this=name, expressions=list(arguments))


def _eq(left: Expression, right: Expression) -> exp.EQ:
    return exp.EQ(this=left, expression=right)


def _and(*parts: Expression) -> Expression:
    given = list(parts)
    if not given:
        return exp.true()
    found = given[0]
    for part in given[1:]:
        found = exp.And(this=found, expression=part)
    return exp.Paren(this=found) if len(given) > 1 else found


def _bits(*parts: Expression) -> Expression:
    """The bitwise OR of the parts, 0 for none."""
    given = [part for part in parts if not (isinstance(part, exp.Literal) and part.this == "0")]
    if not given:
        return _num(0)
    found = given[0]
    for part in given[1:]:
        found = exp.BitwiseOr(this=found, expression=part)
    return exp.Paren(this=found) if len(given) > 1 else found


def _case(branches: Sequence[tuple[Expression, Expression]], default: Expression) -> Expression:
    if not branches:
        return default
    return exp.Case(
        ifs=[exp.If(this=condition, true=value) for condition, value in branches],
        default=default,
    )


def _zero(expression: Expression, default: Expression | None = None) -> Expression:
    return exp.Coalesce(this=expression, expressions=[default if default is not None else _num(0)])


def _has(bits: Expression, mask: int) -> Expression:
    return exp.NEQ(
        this=exp.Paren(this=exp.BitwiseAnd(this=bits, expression=_num(mask))), expression=_num(0)
    )


def _select(*columns: Expression) -> exp.Select:
    return exp.Select(expressions=list(columns))


@dataclass(frozen=True)
class _Answer:
    """One branch of an answer: its truth code, reasons and flag words."""

    v: Expression
    r: Expression
    m: tuple[Expression, ...]


def _answered(
    branches: Sequence[tuple[Expression, _Answer]], default: _Answer
) -> tuple[Expression, Expression, list[Expression]]:
    """``CASE`` expressions for the code, the reasons and each word, the first branch whose
    condition holds giving all three."""
    v = _case([(condition, answer.v) for condition, answer in branches], default.v)
    r = _case([(condition, answer.r) for condition, answer in branches], default.r)
    m = [
        _case([(condition, answer.m[word]) for condition, answer in branches], default.m[word])
        for word in range(len(default.m))
    ]
    return v, r, m


# --- The compiler -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Base:
    """A table blob's relation: ``rid`` and the columns read (``_Compiler.slots``), each
    renamed."""

    name: str
    table: str
    source: TableSource


class _Compiler:
    def __init__(self, cohort: ResolvedCohort, sources: Mapping[str, TableSource]) -> None:
        self.cohort = cohort
        self.release: Release = cohort.release
        self.sources = sources
        self.coverage: Mapping[str, Coverage] = cohort.coverage
        found: set[Mark] = set()
        for relationship, coverage in cohort.coverage.items():
            if coverage.proposed:
                found.add(Mark(Flag.COVERAGE_PROPOSED, relationship))
            if coverage.scope_columns:
                found.add(Mark(Flag.SCOPE_PARTIAL, relationship))
        self.marks = tuple(sorted(found))
        self.bit = {mark: index for index, mark in enumerate(self.marks)}
        self.words = _words(len(self.marks))
        self.parameters: dict[str, Parameter] = {}
        self.ctes: list[tuple[str, exp.Select]] = []
        self.bases: dict[str, _Base] = {}
        self.slots: dict[str, dict[str, str]] = {}
        """Each table's columns read, by the compiler's name for them."""
        self.memo: dict[object, str] = {}
        self.blobs: set[str] = set()

    # --- Plumbing ------------------------------------------------------------------------------

    def parameter(self, value: Scalar | Sequence[object]) -> exp.Placeholder:
        name = f"p{len(self.parameters)}"
        if isinstance(value, Scalar):
            self.parameters[name] = value
        else:
            self.parameters[name] = cast(tuple[Scalar, ...], tuple(value))
        return exp.Placeholder(this=name)

    def cte(self, select: exp.Select) -> str:
        name = f"q{len(self.ctes)}"
        self.ctes.append((name, select))
        return name

    def base(self, table: str) -> _Base:
        found = self.bases.get(table)
        if found is None:
            source = self.sources.get(table)
            if source is None:
                raise CompileError(f"no blob is given for table {table}")
            if ROW_NUMBER in source.columns:
                raise CompileError(
                    f"table {table} has a column named {ROW_NUMBER}, as DuckDB names row numbers"
                )
            found = self.bases[table] = _Base(f"b{len(self.bases)}", table, source)
            self.slots[table] = {}
        return found

    def slot(self, table: str, column: str, *, companion: bool = False) -> str | None:
        """The compiler's name for a stored column of a table; ``None`` for a companion the blob
        does not store. A value column the blob lacks is a ``CompileError``."""
        base = self.base(table)
        if column not in base.source.columns:
            if companion:
                return None
            raise CompileError(f"the blob of table {table} stores no column {column}")
        slots = self.slots[table]
        found = slots.get(column)
        if found is None:
            found = slots[column] = f"c{len(slots)}"
        return found

    def physical(self, table: str, column: str) -> PhysicalType:
        descriptor = self.release.column(table, column)
        if descriptor is None:
            raise CompileError(f"table {table} has no column {column}")
        return physical(descriptor.fields)

    def value_of(self, alias: str, table: str, column: str) -> Expression:
        """A column's stored value, as constants are bound: a datetime in microseconds."""
        found = _col(alias, cast(str, self.slot(table, column)))
        if self.physical(table, column) == "timestamp":
            return _fn("epoch_us", found)
        return found

    def state_of(self, alias: str, table: str, column: str) -> Expression | None:
        """A column's state column; ``None`` when every cell is PRESENT."""
        found = self.slot(table, column + STATE, companion=True)
        return None if found is None else _col(alias, found)

    def truth_row(
        self, alias: str, v: Expression, r: Expression, m: Sequence[Expression]
    ) -> list[Expression]:
        columns: list[Expression] = [_as(_col(alias, "rid"), "rid"), _as(v, "v"), _as(r, "r")]
        columns += [_as(word, f"m{index}") for index, word in enumerate(m)]
        return columns

    def zeros(self) -> tuple[Expression, ...]:
        return tuple(_num(0) for _ in range(self.words))

    def mark_words(self, marks: Iterable[Mark]) -> tuple[Expression, ...]:
        words = [0] * self.words
        for mark in marks:
            index = self.bit[mark]
            words[index // MARK_BITS] |= 1 << (index % MARK_BITS)
        return tuple(_num(word) for word in words)

    def words_of(self, alias: str, prefix: str = "m") -> tuple[Expression, ...]:
        return tuple(_col(alias, f"{prefix}{word}") for word in range(self.words))

    def constant(self, table: str, v: int, r: int = 0) -> str:
        """A relation that is the same for every row of a table."""
        key = ("constant", table, v, r)
        found = self.memo.get(key)
        if found is None:
            base = self.base(table).name
            select = _select(*self.truth_row("t", _num(v), _num(r), self.zeros()))
            found = self.memo[key] = self.cte(select.from_(_table(base, "t"), copy=False))
        return found

    # --- The cohort ----------------------------------------------------------------------------

    def compile(self) -> CompiledCohort:
        unit = self.cohort.unit
        clauses = [self.node(clause, unit) for clause in self.cohort.clauses]
        cohort = self.combine("all", clauses, unit)
        lifted = any(_lifted(clause) for clause in self.cohort.clauses)
        other = (
            self.combine("all", [self.node(flipped(c), unit) for c in self.cohort.clauses], unit)
            if lifted
            else None
        )
        counts = self.counts(cohort, clauses, other)
        values = _select(
            _col("c", "rid"), _col("c", "v"), _col("c", "r"), *self.words_of("c")
        ).from_(_table(cohort, "c"), copy=False)
        values = values.order_by(exp.Ordered(this=_col("c", "rid")), copy=False)
        ctes = [self.base_cte(base) for base in self.bases.values()]
        ctes += [
            exp.CTE(this=body, alias=exp.TableAlias(this=_id(name))) for name, body in self.ctes
        ]
        return CompiledCohort(
            counts_sql=self.with_ctes(counts, ctes).sql(dialect="duckdb"),
            values_sql=self.with_ctes(values, ctes).sql(dialect="duckdb"),
            parameters=MappingProxyType(dict(self.parameters)),
            marks=self.marks,
            clauses=len(clauses),
            blobs=frozenset(self.blobs),
        )

    def counts(self, cohort: str, clauses: Sequence[str], other: str | None) -> exp.Select:
        """One row: the cohort's counts, its unknown units by reason and by top-level clause,
        ``lift_differs`` and its flags (§6.6)."""
        v = _col("c", "v")
        unknown = _eq(v, _num(UNKNOWN_CODE))

        def count(condition: Expression) -> Expression:
            return exp.Filter(this=exp.Count(this=exp.Star()), expression=exp.Where(this=condition))

        totals: list[Expression] = [
            count(_eq(v, _num(TRUE_CODE))),
            count(_eq(v, _num(FALSE_CODE))),
            count(unknown),
        ]
        totals += [count(_and(unknown, _has(_col("c", "r"), bit))) for bit in REASON_BIT.values()]
        totals += [_zero(_fn("bit_or", word)) for word in self.words_of("c")]
        names = [f"n{index}" for index in range(len(totals))]
        parts: list[tuple[exp.Select, list[str]]] = [
            (
                _select(
                    *(_as(column, name) for column, name in zip(totals, names, strict=True))
                ).from_(_table(cohort, "c"), copy=False),
                names,
            )
        ]
        if clauses:
            tag = _col("x", "i")
            by_clause = [
                _as(count(_eq(tag, _num(index))), f"k{index}") for index in range(len(clauses))
            ]
            select = _select(*by_clause).from_(self.stacked(clauses, "x", tagged=True), copy=False)
            select = select.join(
                _table(cohort, "c"), on=_eq(_col("c", "rid"), _col("x", "rid")), copy=False
            )
            select = select.where(
                _and(unknown, _eq(_col("x", "v"), _num(UNKNOWN_CODE))), copy=False
            )
            parts.append((select, [f"k{index}" for index in range(len(clauses))]))
        if other is not None:
            select = _select(_as(exp.Count(this=exp.Star()), "lift")).from_(
                _table(cohort, "c"), copy=False
            )
            select = select.join(
                _table(other, "f"), on=_eq(_col("f", "rid"), _col("c", "rid")), copy=False
            )
            select = select.where(exp.NEQ(this=_col("f", "v"), expression=v), copy=False)
            parts.append((select, ["lift"]))
        found: dict[str, Expression] = {}
        for index, (_, columns) in enumerate(parts):
            for column in columns:
                found[column] = _col(f"t{index}", column)
        reasons = len(REASON_BIT)
        ordered = [found[f"n{index}"] for index in range(3 + reasons)]
        ordered += [found[f"k{index}"] for index in range(len(clauses))]
        ordered.append(found.get("lift", _num(0)))
        ordered += [found[f"n{3 + reasons + at}"] for at in range(self.words)]
        final = _select(*ordered).from_(
            exp.Subquery(this=parts[0][0], alias=exp.TableAlias(this=_id("t0"))), copy=False
        )
        for index, (select, _) in enumerate(parts[1:], start=1):
            final = final.join(
                exp.Subquery(this=select, alias=exp.TableAlias(this=_id(f"t{index}"))),
                join_type="cross",
                copy=False,
            )
        return final

    def with_ctes[Q: exp.Query](self, select: Q, ctes: Sequence[exp.CTE]) -> Q:
        found = select.copy()
        found.set("with_", exp.With(expressions=[cte.copy() for cte in ctes]))
        return found

    def base_cte(self, base: _Base) -> exp.CTE:
        path = self.parameter(base.source.path)
        self.blobs.add(path.name)
        source = exp.Table(
            this=_fn("read_parquet", path),
            alias=exp.TableAlias(this=_id("s")),
        )
        columns: list[Expression] = [_as(exp.column(ROW_NUMBER, table="s"), "rid")]
        columns += [_as(_col("s", column), slot) for column, slot in self.slots[base.table].items()]
        select = _select(*columns).from_(source, copy=False)
        return exp.CTE(this=select, alias=exp.TableAlias(this=_id(base.name)))

    # --- A crossing (D318) ---------------------------------------------------------------------

    def part(self, cohort: ResolvedCohort, *, lifted: bool = False) -> str | None:
        """The relation of a cohort's truth values over the unit table, or of its other lift
        rule (``None`` when it holds no lift)."""
        unit = cohort.unit
        if lifted:
            if not any(_lifted(clause) for clause in cohort.clauses):
                return None
            return self.combine("all", [self.node(flipped(c), unit) for c in cohort.clauses], unit)
        return self.combine("all", [self.node(c, unit) for c in cohort.clauses], unit)

    def crossing(
        self, cohorts: Sequence[ResolvedCohort], predicates: Sequence[ResolvedCohort]
    ) -> CompiledCrossing:
        members = [cast(str, self.part(cohort)) for cohort in cohorts]
        asked = [cast(str, self.part(predicate)) for predicate in predicates]
        other = [self.part(predicate, lifted=True) for predicate in predicates]
        selects: list[exp.Query] = [self.crossed(member, asked, other) for member in members]
        if len(members) > 1:
            selects.append(self.shared(members))
        ctes = [self.base_cte(base) for base in self.bases.values()]
        ctes += [
            exp.CTE(this=body, alias=exp.TableAlias(this=_id(name))) for name, body in self.ctes
        ]
        return CompiledCrossing(
            statements=tuple(
                self.with_ctes(select, ctes).sql(dialect="duckdb") for select in selects
            ),
            parameters=MappingProxyType(dict(self.parameters)),
            marks=self.marks,
            cohorts=len(members),
            predicates=len(asked),
            blobs=frozenset(self.blobs),
        )

    def crossed(self, member: str, asked: Sequence[str], other: Sequence[str | None]) -> exp.Query:
        """One cohort's rows (``CompiledCrossing``), in one pass over its units joined once with
        every predicate (and its other lift rule): grouped by each predicate's code and reasons,
        with those whose answer the other lift rule changes and the flags, and by whether some
        predicate is known and, where none is, their reasons, as ``GROUPING SETS`` of one
        ``GROUP BY``."""
        columns: list[Expression] = []
        for index, lifted in enumerate(other):
            p = f"p{index}"
            columns += [_as(_col(p, "v"), f"v{index}"), _as(_col(p, "r"), f"r{index}")]
            if lifted is not None:
                changed = exp.NEQ(this=_col(f"f{index}", "v"), expression=_col(p, "v"))
                columns.append(_as(changed, f"d{index}"))
            columns += [_as(word, f"w{index}_{at}") for at, word in enumerate(self.words_of(p))]
        conditions = [
            exp.NEQ(this=_col(f"p{index}", "v"), expression=_num(UNKNOWN_CODE))
            for index in range(len(asked))
        ]
        known = conditions[0]
        for condition in conditions[1:]:
            known = exp.Or(this=known, expression=condition)
        reasons = _bits(*(_col(f"p{index}", "r") for index in range(len(asked))))
        columns += [
            _as(_case([(exp.Paren(this=known.copy()), _num(1))], _num(0)), "k"),
            _as(_case([(exp.Paren(this=known.copy()), _num(0))], reasons), "b"),
        ]
        inner = _select(*columns).from_(_table(member, "c"), copy=False)
        for index, (name, lifted) in enumerate(zip(asked, other, strict=True)):
            inner = inner.join(
                _table(name, f"p{index}"),
                on=_eq(_col(f"p{index}", "rid"), _col("c", "rid")),
                copy=False,
            )
            if lifted is not None:
                inner = inner.join(
                    _table(lifted, f"f{index}"),
                    on=_eq(_col(f"f{index}", "rid"), _col("c", "rid")),
                    copy=False,
                )
        inner = inner.where(_eq(_col("c", "v"), _num(TRUE_CODE)), copy=False)

        def grouped(index: int) -> Expression:
            return _eq(_fn("grouping", _col("w", f"v{index}")), _num(0))

        def each(values: Sequence[Expression], default: Expression) -> Expression:
            return _case([(grouped(index), value) for index, value in enumerate(values)], default)

        count = exp.Count(this=exp.Star())
        lifts = [
            _num(0)
            if lifted is None
            else exp.Filter(this=count.copy(), expression=exp.Where(this=_col("w", f"d{index}")))
            for index, lifted in enumerate(other)
        ]
        outer = _select(
            _as(each([_num(index) for index in range(len(asked))], _num(len(asked))), "t"),
            _as(each([_col("w", f"v{i}") for i in range(len(asked))], _col("w", "k")), "v"),
            _as(each([_col("w", f"r{i}") for i in range(len(asked))], _col("w", "b")), "r"),
            _as(count, "n"),
            _as(each(lifts, _num(0)), "d"),
            *(
                _as(
                    each(
                        [_fn("bit_or", _col("w", f"w{i}_{at}")) for i in range(len(asked))],
                        _num(0),
                    ),
                    f"m{at}",
                )
                for at in range(self.words)
            ),
        ).from_(exp.Subquery(this=inner, alias=exp.TableAlias(this=_id("w"))), copy=False)
        sets = [
            exp.Tuple(expressions=[_col("w", f"v{index}"), _col("w", f"r{index}")])
            for index in range(len(asked))
        ]
        sets.append(exp.Tuple(expressions=[_col("w", "k"), _col("w", "b")]))
        outer.set("group", exp.Group(expressions=[exp.GroupingSets(expressions=sets)]))
        return outer

    def shared(self, members: Sequence[str]) -> exp.Select:
        """One row: the units each pair of cohorts shares."""
        counts = [
            _as(
                exp.Filter(
                    this=exp.Count(this=exp.Star()),
                    expression=exp.Where(
                        this=_and(
                            _eq(_col(f"c{first}", "v"), _num(TRUE_CODE)),
                            _eq(_col(f"c{second}", "v"), _num(TRUE_CODE)),
                        )
                    ),
                ),
                f"s{index}",
            )
            for index, (first, second) in enumerate(pairs(len(members)))
        ]
        select = _select(*counts).from_(_table(members[0], "c0"), copy=False)
        for index, name in enumerate(members[1:], start=1):
            select = select.join(
                _table(name, f"c{index}"),
                on=_eq(_col(f"c{index}", "rid"), _col("c0", "rid")),
                copy=False,
            )
        return select

    # --- Materialised variables (D326, D327) ---------------------------------------------------

    def materialised(
        self, cohorts: Sequence[ResolvedCohort], variables: Sequence[ResolvedVariable]
    ) -> CompiledMaterialised:
        members = [cast(str, self.part(cohort)) for cohort in cohorts]
        units = [self.variable(variable) for variable in variables]
        selects: list[exp.Query] = []
        for member in members:
            selects += [
                self.materialised_rows(member, variable, found)
                for variable, found in zip(variables, units, strict=True)
            ]
            if len(variables) > 1:
                selects.append(self.joint(member, [found[0] for found in units]))
        ctes = [self.base_cte(base) for base in self.bases.values()]
        ctes += [
            exp.CTE(this=body, alias=exp.TableAlias(this=_id(name))) for name, body in self.ctes
        ]
        shapes = tuple(
            _Shape(
                variable.kind,
                variable.datatype,
                variable.function,
                variable.order,
                None
                if variable.empty is None
                else normalised(cast(Value, variable.empty), variable.datatype, variable.function),
            )
            for variable in variables
        )
        return CompiledMaterialised(
            statements=tuple(
                self.with_ctes(select, ctes).sql(dialect="duckdb") for select in selects
            ),
            parameters=MappingProxyType(dict(self.parameters)),
            marks=self.marks,
            cohorts=len(members),
            shapes=shapes,
            blobs=frozenset(self.blobs),
        )

    def members(self) -> CompiledMembers:
        """A cohort's members' keys (``CompiledMembers``)."""
        cohort = self.cohort
        unit = cohort.unit
        columns = self.release.primary_key(unit)
        if not columns:
            raise CompileError(f"table {unit} has no declared key")
        member = cast(str, self.part(cohort))
        base = self.base(unit).name
        kinds: list[PhysicalType] = []
        read: list[Expression] = []
        for at, column in enumerate(columns):
            kind = self.physical(unit, column)
            if kind == "strings":
                raise CompileError(f"the key column {unit}.{column} holds lists")
            value: Expression = _col("u", cast(str, self.slot(unit, column)))
            if kind == "timestamp":
                value = _fn("epoch_us", value)
            elif kind == "date32":
                value = _fn(
                    "date_diff", exp.Literal.string("day"), self.parameter(_EPOCH_DAY), value
                )
            kinds.append(kind)
            read.append(_as(value, f"k{at}"))
        select = _select(*read).from_(_table(member, "c"), copy=False)
        select = select.join(
            _table(base, "u"), on=_eq(_col("u", "rid"), _col("c", "rid")), copy=False
        )
        select = select.where(_eq(_col("c", "v"), _num(TRUE_CODE)), copy=False)
        select = select.order_by(exp.Ordered(this=_col("c", "rid")), copy=False)
        ctes = [self.base_cte(one) for one in self.bases.values()]
        ctes += [
            exp.CTE(this=body, alias=exp.TableAlias(this=_id(name))) for name, body in self.ctes
        ]
        return CompiledMembers(
            statement=self.with_ctes(select, ctes).sql(dialect="duckdb"),
            parameters=MappingProxyType(dict(self.parameters)),
            kinds=tuple(kinds),
            blobs=frozenset(self.blobs),
        )

    def variable(self, variable: ResolvedVariable) -> tuple[str, str | None]:
        """A variable's relation over the unit table, one row per unit: ``rid``, ``x`` the bits of
        the reasons it is excluded (0 for a unit with a value), ``e`` 1 for a unit of an
        aggregate with no value to aggregate that takes ``empty``, ``val`` its value (a
        ``count``'s rows; nothing for ``max``, ``min`` and ``mean``), and the flag words; and,
        for ``max``, ``min`` and ``mean``, the relation of each unit's pooled rows by value
        (``rid``, ``val``, ``n``)."""
        key = ("variable", canonical(variable_form(variable)))
        found = self.memo.get(key)
        if found is not None:
            return found, self.memo.get((*key, "groups"))
        groups: str | None = None
        if variable.kind == "column":
            found = self.column_variable(variable)
        elif variable.kind == "question":
            assert variable.question is not None
            question = self.node(variable.question, variable.unit)
            unknown = _eq(_col("q", "v"), _num(UNKNOWN_CODE))
            select = _select(
                _as(_col("q", "rid"), "rid"),
                _as(_case([(unknown, _col("q", "r"))], _num(0)), "x"),
                _as(_num(0), "e"),
                _as(_eq(_col("q", "v"), _num(TRUE_CODE)), "val"),
                *(_as(word, f"m{at}") for at, word in enumerate(self.words_of("q"))),
            )
            found = self.cte(select.from_(_table(question, "q"), copy=False))
        else:
            found, groups = self.aggregate_variable(variable)
        self.memo[key] = found
        if groups is not None:
            self.memo[(*key, "groups")] = groups
        return found, groups

    def column_variable(self, variable: ResolvedVariable) -> str:
        """A column of the unit table, or of the row its up steps look up: its value, or the
        exclusion its cell's state or a missing row gives."""
        unit = variable.unit
        table, column = variable.column.split(".", 1)
        base = self.base(unit).name
        select = _select().from_(_table(base, "u"), copy=False)
        if variable.via:
            lookup = self.lookup(unit, variable.via)
            select = select.join(
                _table(lookup, "l"), on=_eq(_col("l", "rid"), _col("u", "rid")), copy=False
            )
            target: Expression = _col("l", "target")
        else:
            target = _col("u", "rid")
        select = select.join(
            _table(self.base(table).name, "o"),
            on=_eq(_col("o", "rid"), target),
            join_type="left",
            copy=False,
        )
        x = self.cell_exclusion(table, column, "o")
        if variable.via:
            missing = exp.Is(this=_col("l", "target"), expression=exp.Null())
            x = _case([(missing, _num(EXCLUSION_BIT[ExclusionReason.NO_PARENT]))], x)
        value = self.stored_value("o", table, column)
        select = select.select(
            _as(_col("u", "rid"), "rid"),
            _as(x, "x"),
            _as(_num(0), "e"),
            _as(_zero(value, self.default_value(table, column)), "val"),
            *(_as(word, f"m{at}") for at, word in enumerate(self.zeros())),
            copy=False,
        )
        return self.cte(select)

    def stored_value(self, alias: str, table: str, column: str) -> Expression:
        """A column's stored value as a variable reads it."""
        return _col(alias, cast(str, self.slot(table, column)))

    def default_value(self, table: str, column: str) -> Expression:
        """A value of a column's stored type that stands in the rows of a unit without one,
        which no reader takes as a value."""
        kind = self.physical(table, column)
        if kind == "bool":
            return exp.false()
        if kind in ("string", "strings"):
            return exp.Literal.string("")
        return _num(0)

    def cell_exclusion(self, table: str, column: str, alias: str) -> Expression:
        """The bits of the reason a cell excludes its unit: none where it is PRESENT, and
        ``NOT_APPLICABLE``, ``NOT_ASSESSED`` or ``NO_INFORMATION`` by its state (§6.2)."""
        state = self.state_of(alias, table, column)
        if state is None:
            return _num(0)
        present = _eq(state, exp.Literal.string(ObservationState.PRESENT.value))
        applicable = _eq(state, exp.Literal.string(ObservationState.NOT_APPLICABLE.value))
        assessed = _eq(state, exp.Literal.string(ObservationState.NOT_ASSESSED.value))
        return _case(
            [
                (present, _num(0)),
                (applicable, _num(EXCLUSION_BIT[ExclusionReason.NOT_APPLICABLE])),
                (assessed, _num(EXCLUSION_BIT[ExclusionReason.NOT_ASSESSED])),
            ],
            _num(EXCLUSION_BIT[ExclusionReason.NO_INFORMATION]),
        )

    def aggregate_variable(self, variable: ResolvedVariable) -> tuple[str, str | None]:
        """An aggregate (§9.2, D326): each unit's pooling, as a truth value (TRUE where it pooled
        its rows, UNKNOWN for its reasons), its pooled rows, and their values."""
        assert variable.rows is not None
        function = variable.function
        questions = levels(variable.rows, variable.depth)
        status, pooled = self.pooled(questions, 0, variable.unit)
        counted: list[Expression] = [
            _as(_col("p", "rid"), "rid"),
            _as(exp.Count(this=exp.Star()), "cnt"),
        ]
        groups: str | None = None
        select = _select().from_(_table(pooled, "p"), copy=False)
        if function != "count":
            values = self.row_values(variable, questions[-1].table)
            select = select.join(
                _table(values, "w"), on=_eq(_col("w", "rid"), _col("p", "leaf")), copy=False
            )
            usable = _and(_eq(_col("w", "vr"), _num(0)), exp.Not(this=_col("w", "sk")))
            counted += [
                _as(
                    exp.Filter(this=exp.Count(this=exp.Star()), expression=exp.Where(this=usable)),
                    "nv",
                ),
                _as(_zero(_fn("bit_or", _col("w", "vr"))), "vr"),
            ]
            grouped = _select(
                _as(_col("p", "rid"), "rid"),
                _as(_col("w", "val"), "val"),
                _as(exp.Count(this=exp.Star()), "n"),
            ).from_(_table(pooled, "p"), copy=False)
            grouped = grouped.join(
                _table(values, "w"), on=_eq(_col("w", "rid"), _col("p", "leaf")), copy=False
            )
            grouped = grouped.where(usable.copy(), copy=False)
            groups = self.cte(grouped.group_by(_col("p", "rid"), _col("w", "val"), copy=False))
        else:
            counted += [_as(_num(0), "nv"), _as(_num(0), "vr")]
        tallied = self.cte(
            select.select(*counted, copy=False).group_by(_col("p", "rid"), copy=False)
        )
        unknown = _eq(_col("s", "v"), _num(UNKNOWN_CODE))
        value_reasons = _zero(_col("a", "vr"))
        none = _eq(_zero(_col("a", "nv")), _num(0))
        branches: list[tuple[Expression, Expression]] = [
            (unknown, _col("s", "r")),
            (exp.NEQ(this=value_reasons, expression=_num(0)), value_reasons.copy()),
        ]
        empty: Expression = _num(0)
        if function != "count":
            if variable.empty is None:
                branches.append((none, _num(EXCLUSION_BIT[ExclusionReason.NO_ROWS])))
            else:
                empty = _case([(none.copy(), _num(1))], _num(0))
        unit = _select(
            _as(_col("s", "rid"), "rid"),
            _as(_case(branches, _num(0)), "x"),
            _as(empty, "e"),
            _as(_zero(_col("a", "cnt")), "val"),
            *(_as(word, f"m{at}") for at, word in enumerate(self.words_of("s"))),
        ).from_(_table(status, "s"), copy=False)
        unit = unit.join(
            _table(tallied, "a"),
            on=_eq(_col("a", "rid"), _col("s", "rid")),
            join_type="left",
            copy=False,
        )
        return self.cte(unit), groups

    def row_values(self, variable: ResolvedVariable, table: str) -> str:
        """Each row of the last step's table: its value read through the lookups after the last
        down step (``val``), whether it is skipped, NOT_APPLICABLE (``sk``), and the bits of the
        reasons it makes its unit unknown (``vr``): a cell not assessed or empty, a lookup that
        reaches no row, or an ordered category outside its listed values (§6.4); a skipped row
        has none, whatever its value, since §9.2 skips it (an ordered category's NOT_APPLICABLE
        cell is outside its list, but is skipped, not NO_INFORMATION)."""
        owner, column = variable.column.split(".", 1)
        base = self.base(table).name
        select = _select().from_(_table(base, "r"), copy=False)
        if variable.lookup:
            lookup = self.lookup(table, variable.lookup)
            select = select.join(
                _table(lookup, "l"), on=_eq(_col("l", "rid"), _col("r", "rid")), copy=False
            )
            target: Expression = _col("l", "target")
        else:
            target = _col("r", "rid")
        select = select.join(
            _table(self.base(owner).name, "o"),
            on=_eq(_col("o", "rid"), target),
            join_type="left",
            copy=False,
        )
        cell = self.cell_exclusion(owner, column, "o")
        applicable = EXCLUSION_BIT[ExclusionReason.NOT_APPLICABLE]
        skipped: Expression = _eq(cell, _num(applicable))
        reasons: Expression = _case([(skipped.copy(), _num(0))], cell.copy())
        value = self.stored_value("o", owner, column)
        if variable.order is not None:
            position = exp.Sub(
                this=_fn("list_position", self.parameter(variable.order), value),
                expression=_num(1),
            )
            listed = exp.Not(this=exp.Is(this=position.copy(), expression=exp.Null()))
            reasons = _case(
                [
                    (skipped.copy(), _num(0)),
                    (exp.NEQ(this=reasons.copy(), expression=_num(0)), reasons.copy()),
                    (listed, _num(0)),
                ],
                _num(EXCLUSION_BIT[ExclusionReason.NO_INFORMATION]),
            )
            value = position
        if variable.lookup:
            missing = exp.Is(this=_col("l", "target"), expression=exp.Null())
            skipped = _and(exp.Not(this=missing), skipped)
            reasons = _case(
                [(missing.copy(), _num(EXCLUSION_BIT[ExclusionReason.NO_PARENT]))], reasons
            )
        select = select.select(
            _as(_col("r", "rid"), "rid"),
            _as(skipped, "sk"),
            _as(reasons, "vr"),
            _as(
                _zero(
                    value,
                    _num(0) if variable.order is not None else self.default_value(owner, column),
                ),
                "val",
            ),
            copy=False,
        )
        return self.cte(select)

    def pooled(self, questions: Sequence[RExists], index: int, table: str) -> tuple[str, str]:
        """The pooling of an aggregate's chain from its ``index``-th question, for each row of
        ``table``: a truth relation, TRUE where it pooled rows and UNKNOWN for its reasons, with
        its flags; and the relation of its pooled rows (``rid``, ``leaf``), each row of the last
        step's table reached through the children kept at every earlier step
        (``variables``' module docstring states the rules, ``Evaluator.pool`` is held to)."""
        node = questions[index]
        key = ("pooled", canonical(canonical_clause(node)), index, len(questions), table)
        found = self.memo.get(key)
        if found is not None:
            return found, self.memo[(*key, "rows")]
        ups, step = node.via[:-1], node.step
        coverage = self.coverage[step.rel]
        child = coverage.child_table
        parent = coverage.parent_table
        up = self.lookup(child, (Step(step.rel, "up"),))
        last = index == len(questions) - 1
        if last:
            value = self.combine("all", [self.node(clause, child) for clause in node.where], child)
            if coverage.record_filter:
                value = self.combine("all", [value, self.record_filter(coverage)], child)
            stats = self.children(step.rel, value, None, 0, None)
            kept_rows = _select(_as(_col("u", "target"), "pid"), _as(_col("w", "rid"), "leaf"))
            kept_rows = kept_rows.from_(_table(value, "w"), copy=False).join(
                _table(up, "u"), on=_eq(_col("u", "rid"), _col("w", "rid")), copy=False
            )
            kept_rows = kept_rows.where(
                _and(
                    _eq(_col("w", "v"), _num(TRUE_CODE)),
                    exp.Not(this=exp.Is(this=_col("u", "target"), expression=exp.Null())),
                ),
                copy=False,
            )
        else:
            below, below_rows = self.pooled(questions, index + 1, child)
            drop = _DROP[node.lift or "strict"]
            stats = self.children(step.rel, below, None, drop, None)
            kept = exp.Not(
                this=_and(
                    _eq(_col("s", "v"), _num(UNKNOWN_CODE)),
                    _eq(
                        exp.Paren(
                            this=exp.BitwiseAnd(
                                this=_col("s", "r"), expression=_num(_ALL_REASONS ^ drop)
                            )
                        ),
                        _num(0),
                    ),
                )
            )
            kept_rows = _select(_as(_col("u", "target"), "pid"), _as(_col("b", "leaf"), "leaf"))
            kept_rows = kept_rows.from_(_table(below_rows, "b"), copy=False)
            kept_rows = kept_rows.join(
                _table(below, "s"), on=_eq(_col("s", "rid"), _col("b", "rid")), copy=False
            )
            kept_rows = kept_rows.join(
                _table(up, "u"), on=_eq(_col("u", "rid"), _col("b", "rid")), copy=False
            )
            kept_rows = kept_rows.where(
                _and(kept, exp.Not(this=exp.Is(this=_col("u", "target"), expression=exp.Null()))),
                copy=False,
            )
        closed = self.closedness(coverage, "some", _admitted(node))
        terms = _Terms(self, coverage)
        partial = self.mark_words(
            [Mark(Flag.SCOPE_PARTIAL, coverage.relationship)] if coverage.scope_columns else []
        )
        restricted = _and(terms.closed, _col("cl", "rs"))
        reasons = _bits(terms.open(terms.crs), terms.ru)
        if not last:
            reasons = _bits(
                reasons,
                _case([(_eq(terms.nk, _num(0)), _num(REASON_BIT[Reason.NOT_COVERED]))], _num(0)),
            )
        words = tuple(
            _bits(terms.prop[at], _case([(restricted.copy(), partial[at])], _num(0)), terms.ma[at])
            for at in range(self.words)
        )
        answered = _Answer(
            _case(
                [(exp.NEQ(this=reasons.copy(), expression=_num(0)), _num(UNKNOWN_CODE))],
                _num(TRUE_CODE),
            ),
            reasons,
            words,
        )
        rows = _col("n", "rid")
        answer = self.answer(
            _table(self.base(parent).name, "n"),
            rows,
            _answered([terms.out_of_scope], answered),
            [
                (stats, "s", "aid", rows),
                (self.scope(coverage), "sc", "rid", rows),
                (closed, "cl", "rid", rows),
            ],
        )
        at_parent = self.cte(kept_rows)
        status = self.through(table, ups, parent, answer)
        if ups:
            lookup = self.lookup(table, ups)
            reached = _select(_as(_col("l", "rid"), "rid"), _as(_col("k", "leaf"), "leaf"))
            reached = reached.from_(_table(lookup, "l"), copy=False).join(
                _table(at_parent, "k"), on=_eq(_col("k", "pid"), _col("l", "target")), copy=False
            )
            pooled_rows = self.cte(reached)
        else:
            pooled_rows = self.cte(
                _select(_as(_col("k", "pid"), "rid"), _as(_col("k", "leaf"), "leaf")).from_(
                    _table(at_parent, "k"), copy=False
                )
            )
        self.memo[key] = status
        self.memo[(*key, "rows")] = pooled_rows
        return status, pooled_rows

    def materialised_rows(
        self, member: str, variable: ResolvedVariable, found: tuple[str, str | None]
    ) -> exp.Query:
        """One cohort's rows of one variable (``CompiledMaterialised``)."""
        units, groups = found
        words = self.words_of("u")
        count = exp.Count(this=exp.Star())

        def of(*columns: Expression) -> exp.Select:
            select = _select(*columns).from_(_table(member, "c"), copy=False)
            select = select.join(
                _table(units, "u"), on=_eq(_col("u", "rid"), _col("c", "rid")), copy=False
            )
            return select

        def ored() -> list[Expression]:
            return [
                _as(_zero(_fn("bit_or", word.copy())), f"m{at}") for at, word in enumerate(words)
            ]

        member_of = _eq(_col("c", "v"), _num(TRUE_CODE))
        analysed = _and(member_of, _eq(_col("u", "x"), _num(0)))
        default = self.default_of(variable)
        excluded = of(
            _as(_num(_EXCLUDED_ROWS), "t"),
            _as(_num(0), "r"),
            _as(default, "v"),
            _as(_col("u", "x"), "x"),
            _as(count.copy(), "n"),
            *ored(),
        )
        excluded = excluded.where(
            _and(member_of.copy(), exp.NEQ(this=_col("u", "x"), expression=_num(0))), copy=False
        ).group_by(_col("u", "x"), copy=False)
        parts: list[exp.Select] = [excluded]
        if groups is None:
            by_value = of(
                _as(_num(_UNIT_ROWS), "t"),
                _as(_num(0), "r"),
                _as(_col("u", "val"), "v"),
                _as(_num(0), "x"),
                _as(count.copy(), "n"),
                *ored(),
            )
            parts.append(
                by_value.where(analysed.copy(), copy=False).group_by(_col("u", "val"), copy=False)
            )
        else:
            empty = of(
                _as(_num(_EMPTY_ROWS), "t"),
                _as(_num(0), "r"),
                _as(default.copy(), "v"),
                _as(_num(0), "x"),
                _as(count.copy(), "n"),
                *ored(),
            )
            parts.append(
                empty.where(
                    _and(analysed.copy(), _eq(_col("u", "e"), _num(1))), copy=False
                ).group_by(_col("u", "e"), copy=False)
            )
            pooled = _and(analysed.copy(), _eq(_col("u", "e"), _num(0)))
            if variable.function in ("max", "min"):
                pick = exp.Max if variable.function == "max" else exp.Min
                picked = (
                    _select(_col("g", "rid"), _as(pick(this=_col("g", "val")), "val"))
                    .from_(_table(groups, "g"), copy=False)
                    .group_by(_col("g", "rid"), copy=False)
                )
                extremes = of(
                    _as(_num(_EXTREME_ROWS), "t"),
                    _as(_num(0), "r"),
                    _as(_col("p", "val"), "v"),
                    _as(_num(0), "x"),
                    _as(count.copy(), "n"),
                    *ored(),
                )
                extremes = extremes.join(
                    exp.Subquery(this=picked, alias=exp.TableAlias(this=_id("p"))),
                    on=_eq(_col("p", "rid"), _col("u", "rid")),
                    copy=False,
                )
                parts.append(
                    extremes.where(pooled, copy=False).group_by(_col("p", "val"), copy=False)
                )
            else:
                values = of(
                    _as(_num(_VALUE_ROWS), "t"),
                    _as(_col("g", "rid"), "r"),
                    _as(_col("g", "val"), "v"),
                    _as(_num(0), "x"),
                    _as(_col("g", "n"), "n"),
                    *(_as(word.copy(), f"m{at}") for at, word in enumerate(words)),
                )
                values = values.join(
                    _table(groups, "g"), on=_eq(_col("g", "rid"), _col("u", "rid")), copy=False
                )
                parts.append(values.where(pooled, copy=False))
        union: exp.Query = parts[0]
        for part in parts[1:]:
            union = exp.Union(this=union, expression=part, distinct=False)
        return union

    def default_of(self, variable: ResolvedVariable) -> Expression:
        """The stand-in value of a variable's rows without one (``default_value``)."""
        if variable.kind == "question":
            return exp.false()
        if variable.kind == "aggregate" and (variable.function == "count" or variable.order):
            return _num(0)
        table, column = variable.column.split(".", 1)
        return self.default_value(table, column)

    def joint(self, member: str, units: Sequence[str]) -> exp.Select:
        """One cohort's joint rows: its units by whether some variable has a value, and, for
        those for which none has, the bits of every reason (``CompiledMaterialised``)."""
        conditions = [_eq(_col(f"u{index}", "x"), _num(0)) for index in range(len(units))]
        known = conditions[0]
        for condition in conditions[1:]:
            known = exp.Or(this=known, expression=condition)
        reasons = _bits(*(_col(f"u{index}", "x") for index in range(len(units))))
        inner = _select(
            _as(_case([(exp.Paren(this=known.copy()), _num(1))], _num(0)), "k"),
            _as(_case([(exp.Paren(this=known.copy()), _num(0))], reasons), "b"),
        ).from_(_table(member, "c"), copy=False)
        for index, name in enumerate(units):
            inner = inner.join(
                _table(name, f"u{index}"),
                on=_eq(_col(f"u{index}", "rid"), _col("c", "rid")),
                copy=False,
            )
        inner = inner.where(_eq(_col("c", "v"), _num(TRUE_CODE)), copy=False)
        outer = _select(_col("w", "k"), _col("w", "b"), _as(exp.Count(this=exp.Star()), "n"))
        outer = outer.from_(
            exp.Subquery(this=inner, alias=exp.TableAlias(this=_id("w"))), copy=False
        )
        return outer.group_by(_col("w", "k"), _col("w", "b"), copy=False)

    # --- Nodes ---------------------------------------------------------------------------------

    def node(self, node: RClause, table: str) -> str:
        """The relation of a node's truth values for every row of ``table``."""
        key = ("node", table, canonical(canonical_clause(node)))
        found = self.memo.get(key)
        if found is None:
            found = self.memo[key] = self._node(node, table)
        return found

    def _node(self, node: RClause, table: str) -> str:
        if isinstance(node, RAll):
            return self.combine("all", [self.node(m, table) for m in node.members], table)
        if isinstance(node, RAny):
            return self.combine("any", [self.node(m, table) for m in node.members], table)
        if isinstance(node, RNot):
            return self.negated(self.node(node.member, table))
        if isinstance(node, RKnown | RUnknown):
            return self.knowing(self.node(node.member, table), isinstance(node, RKnown))
        if isinstance(node, RValue):
            owner = node.column.split(".", 1)[0]
            return self.through(table, node.via, owner, self.cell(node))
        if isinstance(node, RExists):
            return self.exists(node, table)
        if isinstance(node, RCovered):
            return self.covered(node, table)
        return self.ids(node, table)

    def combine(self, kind: Literal["all", "any"], relations: Sequence[str], table: str) -> str:
        """``all`` or ``any`` of relations over one table (§6.3): the least or greatest code; the
        reasons of the UNKNOWN operands, for an UNKNOWN; the flags of the operands that have the
        result's value. The operands are stacked and grouped by row, so that a combinator of
        many operands is one aggregate rather than a join of as many relations."""
        if not relations:
            return self.constant(table, TRUE_CODE if kind == "all" else FALSE_CODE)
        if len(relations) == 1:
            return relations[0]
        v = _col("x", "v")
        picked = exp.Min(this=v) if kind == "all" else exp.Max(this=v)
        columns: list[Expression] = [
            _as(_col("x", "rid"), "rid"),
            _as(picked, "v"),
            _as(_zero(_fn("bit_or", _col("x", "r"))), "ra"),
        ]
        for at, word in enumerate(self.words_of("x")):
            columns += [
                _as(
                    _zero(
                        exp.Filter(
                            this=_fn("bit_or", word),
                            expression=exp.Where(this=_eq(v, _num(code))),
                        )
                    ),
                    f"m{at}_{code}",
                )
                for code in (FALSE_CODE, UNKNOWN_CODE, TRUE_CODE)
            ]
        select = _select(*columns).from_(self.stacked(relations, "x"), copy=False)
        grouped = self.cte(select.group_by(_col("x", "rid"), copy=False))
        result = _col("g", "v")
        reasons = _case([(_eq(result, _num(UNKNOWN_CODE)), _col("g", "ra"))], _num(0))
        words = [
            _case(
                [
                    (_eq(result, _num(FALSE_CODE)), _col("g", f"m{at}_{FALSE_CODE}")),
                    (_eq(result, _num(UNKNOWN_CODE)), _col("g", f"m{at}_{UNKNOWN_CODE}")),
                ],
                _col("g", f"m{at}_{TRUE_CODE}"),
            )
            for at in range(self.words)
        ]
        outer = _select(*self.truth_row("g", result, reasons, words))
        return self.cte(outer.from_(_table(grouped, "g"), copy=False))

    def stacked(
        self, relations: Sequence[str], alias: str, *, tagged: bool = False
    ) -> exp.Subquery:
        """Relations one above the other, each row tagged with its relation's position (``i``)
        when asked."""
        parts: list[exp.Select] = []
        for index, name in enumerate(relations):
            columns: list[Expression] = [_as(_num(index), "i")] if tagged else []
            columns += [_col("y", "rid"), _col("y", "v"), _col("y", "r"), *self.words_of("y")]
            parts.append(_select(*columns).from_(_table(name, "y"), copy=False))
        union: exp.Query = parts[0]
        for part in parts[1:]:
            union = exp.Union(this=union, expression=part, distinct=False)
        return exp.Subquery(this=union, alias=exp.TableAlias(this=_id(alias)))

    def negated(self, relation: str) -> str:
        """``not``: TRUE and FALSE swapped, reasons and flags kept."""
        v = exp.Sub(this=_num(TRUE_CODE), expression=_col("x", "v"))
        select = _select(*self.truth_row("x", v, _col("x", "r"), self.words_of("x")))
        return self.cte(select.from_(_table(relation, "x"), copy=False))

    def knowing(self, relation: str, known: bool) -> str:
        """``known`` or ``unknown``: TRUE or FALSE, the flags kept."""
        unknown = _eq(_col("x", "v"), _num(UNKNOWN_CODE))
        yes, no = (FALSE_CODE, TRUE_CODE) if known else (TRUE_CODE, FALSE_CODE)
        v = _case([(unknown, _num(yes))], _num(no))
        select = _select(*self.truth_row("x", v, _num(0), self.words_of("x")))
        return self.cte(select.from_(_table(relation, "x"), copy=False))

    # --- Lookups (§6.1) -------------------------------------------------------------------------

    def lookup(self, table: str, ups: Path) -> str:
        """A relation from each row of ``table`` (``rid``) to the row its up steps reach
        (``target``), ``NULL`` where a key is null or dangling."""
        key = ("lookup", table, ups)
        found = self.memo.get(key)
        if found is not None:
            return found
        if len(ups) == 1:
            found = self._step(table, ups[0])
        else:
            first = self.lookup(table, ups[:1])
            relationship = self.release.relationship(ups[0].rel)
            assert relationship is not None
            rest = self.lookup(relationship.fields.parent_table, ups[1:])
            select = _select(_as(_col("a", "rid"), "rid"), _as(_col("b", "target"), "target"))
            select = select.from_(_table(first, "a"), copy=False).join(
                _table(rest, "b"),
                on=_eq(_col("b", "rid"), _col("a", "target")),
                join_type="left",
                copy=False,
            )
            found = self.cte(select)
        self.memo[key] = found
        return found

    def _step(self, table: str, step: Step) -> str:
        relationship = self.release.relationship(step.rel)
        assert relationship is not None
        assert step.dir == "up"
        fields = relationship.fields
        assert fields.child_table == table
        pairs = list(zip(fields.child_columns, fields.parent_columns, strict=True))
        condition = _and(
            *(
                self.key_equal("c", table, child, "p", fields.parent_table, parent)
                for child, parent in pairs
            )
        )
        child_base = self.base(table).name
        parent_base = self.base(fields.parent_table).name
        # The first parent row with the key, as the evaluator takes it; a release has one.
        select = _select(
            _as(_col("c", "rid"), "rid"), _as(exp.Min(this=_col("p", "rid")), "target")
        )
        select = select.from_(_table(child_base, "c"), copy=False).join(
            _table(parent_base, "p"), on=condition, join_type="left", copy=False
        )
        return self.cte(select.group_by(_col("c", "rid"), copy=False))

    def key_equal(
        self,
        left: str,
        left_table: str,
        left_column: str,
        right: str,
        right_table: str,
        right_column: str,
    ) -> Expression:
        """Two key values equal as the evaluator compares them (``key_part``): values of one
        type by value, an integer and a double by value exactly, nothing else (D292)."""
        types = (self.physical(left_table, left_column), self.physical(right_table, right_column))
        a = _col(left, cast(str, self.slot(left_table, left_column)))
        b = _col(right, cast(str, self.slot(right_table, right_column)))
        if types[0] == types[1] and types[0] != "strings":
            return _eq(a, b)
        if set(types) == set(_NUMERIC):
            whole, double = (a, b) if types[0] == "int64" else (b, a)
            return _and(
                _eq(exp.Cast(this=whole, to=exp.DataType.build("DOUBLE")), double),
                _eq(exp.TryCast(this=double, to=exp.DataType.build("BIGINT")), whole),
            )
        return exp.false()

    def through(self, table: str, ups: Path, target: str, relation: str) -> str:
        """A relation on ``target`` read from ``table`` through up steps: UNKNOWN
        (``NO_PARENT``) where they reach no row."""
        if not ups:
            assert table == target
            return relation
        key = ("through", table, ups, relation)
        found = self.memo.get(key)
        if found is not None:
            return found
        lookup = self.lookup(table, ups)
        missing = exp.Is(this=_col("l", "target"), expression=exp.Null())
        v = _case([(missing, _num(UNKNOWN_CODE))], _col("x", "v"))
        r = _case([(missing, _num(REASON_BIT[Reason.NO_PARENT]))], _col("x", "r"))
        words = [_case([(missing, _num(0))], word) for word in self.words_of("x")]
        select = _select(*self.truth_row("l", v, r, words))
        select = select.from_(_table(lookup, "l"), copy=False).join(
            _table(relation, "x"),
            on=_eq(_col("x", "rid"), _col("l", "target")),
            join_type="left",
            copy=False,
        )
        found = self.memo[key] = self.cte(select)
        return found

    # --- Value predicates (§6.4) ---------------------------------------------------------------

    def cell(self, node: RValue) -> str:
        """A value leaf's relation on its column's table, for the row itself."""
        table, column = node.column.split(".", 1)
        key = ("cell", table, canonical(canonical_clause(_unrouted(node))))
        found = self.memo.get(key)
        if found is not None:
            return found
        base = self.base(table).name
        state = self.state_of("o", table, column)
        if node.match is not None:
            found = self.list_cell(node, table, column, state)
        else:
            v, r = self.compared(node, table, self.value_of("o", table, column))
            v, r = _based(state, v, r)
            if node.negate:
                v = exp.Sub(this=_num(TRUE_CODE), expression=v)
            select = _select(*self.truth_row("o", v, r, self.zeros()))
            found = self.cte(select.from_(_table(base, "o"), copy=False))
        self.memo[key] = found
        return found

    def list_cell(self, node: RValue, table: str, column: str, state: Expression | None) -> str:
        """A list column's items, each with its state and ``negate``, under ``match`` (§6.4)."""
        base = self.base(table).name
        values = _col("o", cast(str, self.slot(table, column)))
        items_state = self.slot(table, column + ITEM_STATE, companion=True)
        columns: list[Expression] = [
            _as(_col("o", "rid"), "rid"),
            _as(_fn("unnest", values), "item"),
        ]
        if items_state is not None:
            columns.append(_as(_fn("unnest", _col("o", items_state)), "state"))
        unnested = self.cte(_select(*columns).from_(_table(base, "o"), copy=False))
        v, r = self.compared(node, table, _col("i", "item"))
        v, r = _based(_col("i", "state") if items_state is not None else None, v, r)
        if node.negate:
            v = exp.Sub(this=_num(TRUE_CODE), expression=v)
        items = self.cte(
            _select(_as(_col("i", "rid"), "rid"), _as(v, "v"), _as(r, "r")).from_(
                _table(unnested, "i"), copy=False
            )
        )
        unknown = _eq(_col("i", "v"), _num(UNKNOWN_CODE))
        summary = _select(
            _as(_col("i", "rid"), "rid"),
            _as(exp.Max(this=_col("i", "v")), "hi"),
            _as(exp.Min(this=_col("i", "v")), "lo"),
            _as(
                _zero(
                    exp.Filter(
                        this=_fn("bit_or", _col("i", "r")), expression=exp.Where(this=unknown)
                    )
                ),
                "ru",
            ),
        ).from_(_table(items, "i"), copy=False)
        summarised = self.cte(summary.group_by(_col("i", "rid"), copy=False))
        empty = exp.Is(this=_col("a", "rid"), expression=exp.Null())
        if node.match == "any":
            items_v = _zero(_col("a", "hi"))
            items_r = _case([(_eq(items_v, _num(UNKNOWN_CODE)), _col("a", "ru"))], _num(0))
        else:
            items_v = _case([(empty, _num(UNKNOWN_CODE))], _col("a", "lo"))
            items_r = _case(
                [
                    (empty, _num(REASON_BIT[Reason.NO_ROWS])),
                    (_eq(_col("a", "lo"), _num(UNKNOWN_CODE)), _col("a", "ru")),
                ],
                _num(0),
            )
        v, r = _based(state, items_v, items_r)
        select = _select(*self.truth_row("o", v, r, self.zeros()))
        select = select.from_(_table(base, "o"), copy=False).join(
            _table(summarised, "a"),
            on=_eq(_col("a", "rid"), _col("o", "rid")),
            join_type="left",
            copy=False,
        )
        return self.cte(select)

    def compared(
        self, node: RValue, table: str, value: Expression
    ) -> tuple[Expression, Expression]:
        """The code and reasons of a PRESENT value (or item): TRUE or FALSE, or UNKNOWN
        (``NO_INFORMATION``) for a range over an ordered category the value is not listed in."""
        column = node.column.split(".", 1)[1]
        stored = self.physical(table, column)
        kind: PhysicalType = "string" if stored == "strings" else stored
        order = _order(self.release, node.column)
        if isinstance(node.predicate, Values):
            constants = self.converted(node, table, column, list(node.predicate.values))
            member = self.member(kind, value, constants)
            return _case([(member, _num(TRUE_CODE))], _num(FALSE_CODE)), _num(0)
        predicate = node.predicate
        bounds = [predicate.gt, predicate.gte, predicate.lt, predicate.lte]
        if order is not None:
            positions = [None if bound is None else order[str(bound)] for bound in bounds]
            within = [name for name, at in order.items() if _within(at, *positions)]
            listed = self.member(kind, value, cast(list[Constant | None], list(order)))
            inside = self.member(kind, value, cast(list[Constant | None], within))
            v = _case([(inside, _num(TRUE_CODE)), (listed, _num(FALSE_CODE))], _num(UNKNOWN_CODE))
            r = _case([(listed, _num(0))], _num(REASON_BIT[Reason.NO_INFORMATION]))
            return v, r
        converted = self.converted(node, table, column, bounds)
        condition = self.bounded(kind, value, converted)
        return _case([(condition, _num(TRUE_CODE))], _num(FALSE_CODE)), _num(0)

    def converted(
        self, node: RValue, table: str, column: str, constants: list[Constant | None]
    ) -> list[Constant | None]:
        """The constants in the column's units, as the evaluator converts them: one
        multiplication of doubles by the pinned factor (§6.4)."""
        descriptor = self.release.column(table, column)
        column_units = None if descriptor is None else descriptor.fields.units
        if node.units is None or column_units is None or node.units == column_units:
            return constants
        ratio = scale(node.units, column_units)
        assert ratio is not None, "units are checked when the document is resolved"
        return [
            None if constant is None else float(cast(int | float, constant)) * ratio
            for constant in constants
        ]

    def member(
        self, kind: PhysicalType, value: Expression, constants: Sequence[Constant | None]
    ) -> Expression:
        """Whether a value is one of the constants, as ``key_part`` compares them: a semi-join on
        the bound list, which DuckDB hashes, rather than a search of the list for every row."""
        bound = _bindable(kind, constants)
        if not bound:
            return exp.false()
        listed = _select(_fn("unnest", self.parameter(bound)))
        return exp.In(this=value, query=exp.Subquery(this=listed))

    def bounded(
        self, kind: PhysicalType, value: Expression, bounds: list[Constant | None]
    ) -> Expression:
        """Whether a value lies within ``gt``, ``gte``, ``lt`` and ``lte``."""
        parts: list[Expression] = []
        for name, bound in zip(("gt", "gte", "lt", "lte"), bounds, strict=True):
            if bound is None:
                continue
            given = _bound(kind, name, bound)
            if isinstance(given, bool):
                if not given:
                    return exp.false()
                continue
            placeholder = self.parameter(cast(Scalar, given))
            comparison = {"gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}[name]
            parts.append(comparison(this=value, expression=placeholder))
        return _and(*parts)

    # --- ids (§7.2) ----------------------------------------------------------------------------

    def ids(self, node: RIds, table: str) -> str:
        columns = self.release.primary_key(table)
        if not columns:
            raise CompileError(f"table {table} has no key for an ids leaf to name")
        kinds: list[PhysicalType] = [self.physical(table, column) for column in columns]
        keys: set[tuple[object, ...]] = set()
        for _, key in node.keys:
            converted = [_bindable(kind, [part]) for kind, part in zip(kinds, key, strict=True)]
            if all(converted):
                keys.add(tuple(part[0] for part in converted))
        base = self.base(table).name
        if not keys:
            return self.constant(table, FALSE_CODE)
        ordered = sorted(keys, key=repr)
        listed = [
            _as(
                _fn("unnest", self.parameter([key[at] for key in ordered])),
                f"k{at}",
            )
            for at in range(len(columns))
        ]
        given = self.cte(_select(*listed, _as(_num(1), "hit")).distinct(copy=False))
        condition = _and(
            *(
                _eq(self.value_of("t", table, column), _col("k", f"k{at}"))
                for at, column in enumerate(columns)
            )
        )
        hit = exp.Not(this=exp.Is(this=_col("k", "hit"), expression=exp.Null()))
        v = _case([(hit, _num(TRUE_CODE))], _num(FALSE_CODE))
        select = _select(*self.truth_row("t", v, _num(0), self.zeros()))
        select = select.from_(_table(base, "t"), copy=False).join(
            _table(given, "k"), on=condition, join_type="left", copy=False
        )
        return self.cte(select)

    # --- Coverage (§6.5) -----------------------------------------------------------------------

    def scope(self, coverage: Coverage) -> str:
        """The parent scope's relation on the parent table (TRUE without one)."""
        if coverage.parent_scope is None:
            return self.constant(coverage.parent_table, TRUE_CODE)
        return self.node(coverage.parent_scope, coverage.parent_table)

    def record_filter(self, coverage: Coverage) -> str:
        """The record filter's relation on the child table: the conjunction, per filtered
        column, of its base result or its value's membership in the allowed values (step 1)."""
        key = ("filter", coverage.relationship)
        found = self.memo.get(key)
        if found is not None:
            return found
        table = coverage.child_table
        base = self.base(table).name
        parts: list[tuple[Expression, Expression]] = []
        for column, allowed in coverage.record_filter:
            kind = self.physical(table, column)
            if kind == "string":
                value = _col("o", cast(str, self.slot(table, column)))
                member = self.member("string", value, sorted(allowed))
                v: Expression = _case([(member, _num(TRUE_CODE))], _num(FALSE_CODE))
            else:
                v = _num(FALSE_CODE)
            parts.append(_based(self.state_of("o", table, column), v, _num(0)))
        columns: list[Expression] = [_as(_col("o", "rid"), "rid")]
        for index, (v, r) in enumerate(parts):
            columns += [_as(v, f"v{index}"), _as(r, f"r{index}")]
        inner = self.cte(_select(*columns).from_(_table(base, "o"), copy=False))
        picked = _fn("least", *(_col("j", f"v{i}") for i in range(len(parts))))
        reasons = _case(
            [
                (
                    _eq(picked, _num(UNKNOWN_CODE)),
                    _bits(*(_col("j", f"r{i}") for i in range(len(parts)))),
                )
            ],
            _num(0),
        )
        select = _select(*self.truth_row("j", picked, reasons, self.zeros()))
        found = self.memo[key] = self.cte(select.from_(_table(inner, "j"), copy=False))
        return found

    def closedness(
        self, coverage: Coverage, quantifier: str, admitted: Mapping[str, frozenset[KeyPart]]
    ) -> str:
        """Per parent row, whether it is closed (``cl``), its closedness reasons (``crs``) and
        whether it is closed only for the listed scope tuples (``rs``), as step 4 says."""
        columns = [c for c in coverage.scope_columns if c in admitted]
        admitted_key = tuple((c, tuple(sorted(map(repr, admitted[c])))) for c in columns)
        key = ("closed", coverage.relationship, quantifier, admitted_key)
        found = self.memo.get(key)
        if found is not None:
            return found
        table = coverage.parent_table
        base = self.base(table).name
        not_covered = _num(REASON_BIT[Reason.NOT_COVERED])
        joined: str | None = None
        if coverage.form == "undeclared":
            select = _select(
                _as(_col("p", "rid"), "rid"),
                _as(exp.false(), "cl"),
                _as(_num(REASON_BIT[Reason.NO_INFORMATION]), "crs"),
                _as(exp.false(), "rs"),
            ).from_(_table(base, "p"), copy=False)
        elif coverage.form == "all":
            scope = self.scope(coverage)
            unknown = _eq(_col("p", "v"), _num(UNKNOWN_CODE))
            select = _select(
                _as(_col("p", "rid"), "rid"),
                _as(exp.Not(this=unknown), "cl"),
                _as(_case([(unknown, _col("p", "r"))], _num(0)), "crs"),
                _as(exp.false(), "rs"),
            ).from_(_table(scope, "p"), copy=False)
        else:
            joined = self.listing(coverage, columns, admitted)
            listed = exp.Not(this=exp.Is(this=_col("l", "rid"), expression=exp.Null()))
            if not coverage.scope_columns:
                closed: Expression = listed
                restricted: Expression = exp.false()
            else:
                every = _zero(_col("l", "every"), exp.false())
                if quantifier == "every":
                    otherwise: Expression = _zero(_col("l", "tuples"), exp.false())
                    partial: Expression = exp.true()
                else:
                    wanted = math.prod(len(admitted[c]) for c in columns)
                    otherwise = exp.GTE(
                        this=_zero(_col("l", "within")),
                        expression=self.parameter(min(wanted, _INT64[1])),
                    )
                    partial = (
                        exp.true() if len(columns) < len(coverage.scope_columns) else exp.false()
                    )
                closed = _case([(every, exp.true())], otherwise)
                restricted = _case([(every, exp.false())], partial)
            select = _select(
                _as(_col("p", "rid"), "rid"),
                _as(closed, "cl"),
                _as(not_covered, "crs"),
                _as(restricted, "rs"),
            )
            select = select.from_(_table(base, "p"), copy=False).join(
                _table(joined, "l"),
                on=_eq(_col("l", "rid"), _col("p", "rid")),
                join_type="left",
                copy=False,
            )
        found = self.memo[key] = self.cte(select)
        return found

    def listing(
        self,
        coverage: Coverage,
        columns: Sequence[str],
        admitted: Mapping[str, frozenset[KeyPart]],
    ) -> str:
        """What a coverage table lists for each parent row it lists (§5.6): ``tuples``, whether
        it lists some whole scope tuple; ``every``, whether a group covers every scope value; and
        ``within``, the distinct tuples of ``columns`` it lists within ``admitted``. A listing
        row whose scope cell is not PRESENT (or holds a list) lists no whole tuple, as the
        evaluator reads it, so under ``every`` the parent is not closed by it alone. The gate
        refuses such a cell (``COVERAGE_NULL``), so no published release holds one; releases
        built in memory can, and the differential tests hold the two readings together."""
        descriptor = coverage.descriptor
        assert descriptor is not None
        parents = descriptor.fields.parents
        table = coverage.parent_table
        base = self.base(table).name
        scope_holders: list[tuple[str, str, str]]
        """Each scope column of the child, with the alias and column holding it."""
        if isinstance(parents, DirectCoverage):
            holder = parents.table
            select = _select().from_(_table(base, "p"), copy=False)
            condition = _and(
                *(
                    self.key_equal("p", table, parent, "d", holder, own)
                    for own, parent in parents.parent_columns.items()
                )
            )
            select = select.join(_table(self.base(holder).name, "d"), on=condition, copy=False)
            mapped = parents.scope_columns or {}
            scope_holders = [
                ("d", holder, next(k for k, v in mapped.items() if v == column))
                for column in coverage.scope_columns
            ]
            every: Expression = exp.false()
        else:
            assert isinstance(parents, GroupedCoverage)
            assignment, groups = parents.assignment, parents.groups
            select = _select().from_(_table(base, "p"), copy=False)
            condition = _and(
                *(
                    self.key_equal("p", table, parent, "a", assignment.table, own)
                    for own, parent in assignment.parent_columns.items()
                )
            )
            select = select.join(
                _table(self.base(assignment.table).name, "a"), on=condition, copy=False
            )
            select = select.join(
                _table(self.base(groups.table).name, "g"),
                on=self.key_equal(
                    "a",
                    assignment.table,
                    assignment.group_column,
                    "g",
                    groups.table,
                    groups.group_column,
                ),
                copy=False,
            )
            mapped = groups.scope_columns or {}
            scope_holders = [
                ("g", groups.table, next(k for k, v in mapped.items() if v == column))
                for column in coverage.scope_columns
            ]
            every = exp.false()
            covers = groups.covers_all_column
            if covers is not None and self.physical(groups.table, covers) == "bool":
                every = _zero(_col("g", cast(str, self.slot(groups.table, covers))), exp.false())
        whole = _and(
            *(
                exp.false()
                if self.physical(holder, name) == "strings"
                else exp.Not(
                    this=exp.Is(
                        this=_col(alias, cast(str, self.slot(holder, name))),
                        expression=exp.Null(),
                    )
                )
                for alias, holder, name in scope_holders
            )
        )
        by_column = dict(zip(coverage.scope_columns, scope_holders, strict=True))
        if columns:
            inside: list[Expression] = []
            projected: list[Expression] = []
            for index, column in enumerate(columns):
                alias, holder, name = by_column[column]
                value = self.value_of(alias, holder, name)
                kind = self.physical(holder, name)
                constants = [part for _, part in admitted[column]]
                inside.append(self.member(kind, value, constants))
                projected.append(exp.PropertyEQ(this=_id(f"k{index}"), expression=value))
            within: Expression = exp.Filter(
                this=exp.Count(this=exp.Distinct(expressions=[_fn("struct_pack", *projected)])),
                expression=exp.Where(this=_and(whole, *inside)),
            )
        else:
            within = _case([(_zero(_fn("bool_or", whole), exp.false()), _num(1))], _num(0))
        select = select.select(
            _as(_col("p", "rid"), "rid"),
            _as(_fn("bool_or", whole), "tuples"),
            _as(_fn("bool_or", every), "every"),
            _as(within, "within"),
            copy=False,
        )
        return self.cte(select.group_by(_col("p", "rid"), copy=False))

    # --- Existence questions (§6.5) ------------------------------------------------------------

    def children(
        self,
        relationship: str,
        value: str,
        conditions: str | None,
        drop: int,
        anchor: str | None,
    ) -> str:
        """Each anchor's children's counts and ORs (``aid``): their values, whether each is kept,
        their reasons, flags and conditions. Without ``anchor``, the anchors are the parent
        rows. With it, they are the rows of a lookup whose children exclude the row itself
        (``exclude_self``): each parent's children are counted once, bit by bit for what is
        ORed, and each row's own contribution, when it is one of its parent's children, is
        taken away, so the work is linear in the rows (D291)."""
        link = self.release.relationship(relationship)
        assert link is not None
        child = link.fields.child_table
        up = self.lookup(child, (Step(relationship, "up"),))
        kept = exp.Not(
            this=_and(
                _eq(_col("w", "v"), _num(UNKNOWN_CODE)),
                _eq(
                    exp.Paren(
                        this=exp.BitwiseAnd(
                            this=_col("w", "r"), expression=_num(_ALL_REASONS ^ drop)
                        )
                    ),
                    _num(0),
                ),
            )
        )
        columns: list[Expression] = [
            _as(_col("u", "target"), "aid"),
            _as(_col("w", "rid"), "cid"),
            _as(_col("w", "v"), "v"),
            _as(_col("w", "r"), "r"),
            *(_as(word, f"m{at}") for at, word in enumerate(self.words_of("w"))),
            _as(kept, "k"),
        ]
        if conditions is not None:
            columns += [_as(_col("c", "v"), "cv"), _as(_col("c", "r"), "cr")]
        select = _select(*columns).from_(_table(value, "w"), copy=False)
        select = select.join(
            _table(up, "u"), on=_eq(_col("u", "rid"), _col("w", "rid")), copy=False
        )
        select = select.where(
            exp.Not(this=exp.Is(this=_col("u", "target"), expression=exp.Null())), copy=False
        )
        if conditions is not None:
            select = select.join(
                _table(conditions, "c"), on=_eq(_col("c", "rid"), _col("w", "rid")), copy=False
            )
        kids = self.cte(select)
        if anchor is None:
            return self.child_stats(kids, conditions is not None)
        return self.sibling_stats(kids, anchor, conditions is not None)

    def child_stats(self, kids: str, conditions: bool) -> str:
        """The children's counts and ORs per parent row."""
        k = _col("x", "k")
        v = _col("x", "v")

        def where(condition: Expression) -> exp.Where:
            return exp.Where(this=condition)

        def counted(code: int) -> Expression:
            condition = _and(k, _eq(v, _num(code)))
            return exp.Filter(this=exp.Count(this=exp.Star()), expression=where(condition))

        def ored(column: Expression, condition: Expression) -> Expression:
            return _zero(exp.Filter(this=_fn("bit_or", column), expression=where(condition)))

        stats: list[Expression] = [
            _as(_col("x", "aid"), "aid"),
            _as(counted(TRUE_CODE), "t"),
            _as(counted(FALSE_CODE), "f"),
            _as(counted(UNKNOWN_CODE), "u"),
            _as(ored(_col("x", "r"), _and(k, _eq(v, _num(UNKNOWN_CODE)))), "ru"),
        ]
        for at, word in enumerate(self.words_of("x")):
            stats += [
                _as(ored(word, _and(k, _eq(v, _num(TRUE_CODE)))), f"mt{at}"),
                _as(ored(word, _and(k, _eq(v, _num(FALSE_CODE)))), f"mf{at}"),
                _as(ored(word, _and(k, _eq(v, _num(UNKNOWN_CODE)))), f"mu{at}"),
                _as(ored(word, exp.Not(this=k)), f"md{at}"),
                _as(_zero(_fn("bit_or", word)), f"ma{at}"),
            ]
        if conditions:
            cv = _col("x", "cv")
            stats += [
                _as(_zero(_fn("bool_or", _and(k, _eq(cv, _num(TRUE_CODE)))), exp.false()), "met"),
                _as(ored(_col("x", "cr"), _and(k, _eq(cv, _num(UNKNOWN_CODE)))), "rc"),
            ]
        select = _select(*stats).from_(_table(kids, "x"), copy=False)
        return self.cte(select.group_by(_col("x", "aid"), copy=False))

    def tallies(self, alias: str, conditions: bool) -> list[tuple[str, Expression]]:
        """What a child adds to its parent's counts, each a condition on its row: its value if
        it is kept, and each bit of its reasons, flags and conditions, by what it is ORed
        into."""
        k, v = _col(alias, "k"), _col(alias, "v")
        kept = {
            "t": _and(k, _eq(v, _num(TRUE_CODE))),
            "f": _and(k, _eq(v, _num(FALSE_CODE))),
            "u": _and(k, _eq(v, _num(UNKNOWN_CODE))),
        }
        found = list(kept.items())
        found += [
            (f"ru_{index}", _and(kept["u"], _has(_col(alias, "r"), bit)))
            for index, bit in enumerate(REASON_BIT.values())
        ]
        for index in range(len(self.marks)):
            word = _col(alias, f"m{index // MARK_BITS}")
            bit = 1 << (index % MARK_BITS)
            flagged = _has(word, bit)
            found += [(f"m{kind}_{index}", _and(kept[kind], flagged)) for kind in ("t", "f", "u")]
            found += [(f"md_{index}", _and(exp.Not(this=k), flagged)), (f"ma_{index}", flagged)]
        if conditions:
            cv = _col(alias, "cv")
            found.append(("met", _and(k, _eq(cv, _num(TRUE_CODE)))))
            found += [
                (f"rc_{index}", _and(k, _eq(cv, _num(UNKNOWN_CODE)), _has(_col(alias, "cr"), bit)))
                for index, bit in enumerate(REASON_BIT.values())
            ]
        return found

    def sibling_stats(self, kids: str, anchor: str, conditions: bool) -> str:
        """The counts and ORs of each anchor row's parent's children but itself."""
        per_parent = [
            _as(exp.Filter(this=exp.Count(this=exp.Star()), expression=exp.Where(this=c)), name)
            for name, c in self.tallies("x", conditions)
        ]
        parents = self.cte(
            _select(_as(_col("x", "aid"), "aid"), *per_parent)
            .from_(_table(kids, "x"), copy=False)
            .group_by(_col("x", "aid"), copy=False)
        )

        def left(name: str, condition: Expression) -> Expression:
            own = _case([(condition, _num(1))], _num(0))
            return exp.Paren(this=exp.Sub(this=_zero(_col("p", name)), expression=own))

        remaining = {name: left(name, c) for name, c in self.tallies("o", conditions)}

        def any_of(name: str) -> Expression:
            return exp.GT(this=remaining[name], expression=_num(0))

        def ored(prefix: str, bits: Sequence[tuple[int, int]]) -> Expression:
            return _bits(*(_case([(any_of(f"{prefix}_{i}"), _num(b))], _num(0)) for i, b in bits))

        reasons = list(enumerate(REASON_BIT.values()))
        stats: list[Expression] = [
            _as(_col("n", "rid"), "aid"),
            _as(remaining["t"], "t"),
            _as(remaining["f"], "f"),
            _as(remaining["u"], "u"),
            _as(ored("ru", reasons), "ru"),
        ]
        for at in range(self.words):
            bits = [
                (index, 1 << (index % MARK_BITS))
                for index in range(len(self.marks))
                if index // MARK_BITS == at
            ]
            stats += [_as(ored(f"m{kind}", bits), f"m{kind}{at}") for kind in "tfuda"]
        if conditions:
            stats += [_as(any_of("met"), "met"), _as(ored("rc", reasons), "rc")]
        select = _select(*stats).from_(_table(anchor, "n"), copy=False)
        select = select.join(
            _table(parents, "p"),
            on=_eq(_col("p", "aid"), _col("n", "target")),
            join_type="left",
            copy=False,
        )
        select = select.join(
            _table(kids, "o"),
            on=_and(
                _eq(_col("o", "cid"), _col("n", "rid")), _eq(_col("o", "aid"), _col("n", "target"))
            ),
            join_type="left",
            copy=False,
        )
        return self.cte(select)

    def exists(self, node: RExists, table: str) -> str:
        """An existence question (§6.5): its children's values aggregated per parent row (or per
        row it starts from, under ``exclude_self``), and steps 2 to 7 on the aggregates."""
        ups, step = node.via[:-1], node.step
        coverage = self.coverage[step.rel]
        child = coverage.child_table
        some = node.quantifier == "some"
        value = self.combine("all", [self.node(clause, child) for clause in node.where], child)
        if coverage.record_filter:
            filtered = self.record_filter(coverage)
            value = (
                self.combine("all", [value, filtered], child)
                if some
                else self.combine("any", [self.negated(filtered), value], child)
            )
        final = node.lift is None
        conditions = None
        if some and not final:
            conditions = self.combine(
                "all",
                [
                    self.node(clause, child)
                    for clause in node.where
                    if next(questions((clause,)), None) is None
                ],
                child,
            )
        drop = 0 if final else _DROP[node.lift or "strict"]
        exclude = node.exclude_self and child == table
        anchor = None
        if exclude:
            anchor = self.lookup(table, ups) if ups else self.identity(table)
        stats = self.children(step.rel, value, conditions, drop, anchor)
        closed = self.closedness(coverage, node.quantifier, _admitted(node))
        terms = _Terms(self, coverage)
        branches: list[tuple[Expression, _Answer]] = []
        if exclude:
            missing = exp.Is(this=_col("n", "target"), expression=exp.Null())
            branches.append((missing, terms.none(Reason.NO_PARENT)))
        branches.append(terms.out_of_scope)
        t, f, u = terms.t, terms.f, terms.u
        if some:
            k = self.parameter(node.min_count or 1)
            branches += [
                (exp.GTE(this=t, expression=k), _Answer(_num(TRUE_CODE), _num(0), terms.mt)),
                (
                    exp.GTE(this=exp.Paren(this=exp.Add(this=t, expression=u)), expression=k),
                    terms.unknown(terms.ru),
                ),
            ]
            if final:
                branches.append((terms.closed, terms.closed_as(FALSE_CODE, terms.ma)))
                default = terms.unknown(_num(0))
            else:
                met = _zero(_col("s", "met"), exp.false())
                branches.append((_and(terms.closed, met), terms.closed_as(FALSE_CODE, terms.ma)))
                not_met = _bits(_num(REASON_BIT[Reason.NOT_COVERED]), _zero(_col("s", "rc")))
                default = terms.unknown(_case([(met, _num(0))], not_met))
        else:
            empty = REASON_BIT[Reason.NO_ROWS if final else Reason.NOT_COVERED]
            branches += [
                (exp.GTE(this=f, expression=_num(1)), _Answer(_num(FALSE_CODE), _num(0), terms.mf)),
                (_eq(terms.nk, _num(0)), terms.unknown(_num(empty))),
                (exp.GTE(this=u, expression=_num(1)), terms.unknown(terms.ru)),
                (terms.closed, terms.closed_as(TRUE_CODE, terms.ma)),
            ]
            default = terms.unknown(_num(0))
        if anchor is not None:
            rows, key = _col("n", "rid"), _col("n", "target")
            source = _table(anchor, "n")
        else:
            rows = key = _col("n", "rid")
            source = _table(self.base(coverage.parent_table).name, "n")
        answer = self.answer(
            source,
            rows,
            _answered(branches, default),
            [
                (stats, "s", "aid", rows),
                (self.scope(coverage), "sc", "rid", key),
                (closed, "cl", "rid", key),
            ],
        )
        if exclude:
            return answer
        return self.through(table, ups, coverage.parent_table, answer)

    def answer(
        self,
        source: exp.Table,
        rows: Expression,
        answered: tuple[Expression, Expression, list[Expression]],
        joins: Sequence[tuple[str, str, str, Expression]],
    ) -> str:
        """A question's answers: one row per row of ``source``, each join ``(relation, alias,
        column, key)`` matching ``column`` of its relation to ``key``."""
        v, r, m = answered
        columns: list[Expression] = [_as(rows, "rid"), _as(v, "v"), _as(r, "r")]
        columns += [_as(word, f"m{at}") for at, word in enumerate(m)]
        select = _select(*columns).from_(source, copy=False)
        for relation, alias, column, key in joins:
            select = select.join(
                _table(relation, alias),
                on=_eq(_col(alias, column), key),
                join_type="left",
                copy=False,
            )
        return self.cte(select)

    def identity(self, table: str) -> str:
        """Each row of a table, as a lookup to itself."""
        key = ("identity", table)
        found = self.memo.get(key)
        if found is None:
            base = self.base(table).name
            select = _select(_as(_col("t", "rid"), "rid"), _as(_col("t", "rid"), "target"))
            found = self.memo[key] = self.cte(select.from_(_table(base, "t"), copy=False))
        return found

    # --- covered (§6.5) ------------------------------------------------------------------------

    def covered(self, node: RCovered, table: str) -> str:
        segments: list[tuple[Path, Step]] = []
        ups: list[Step] = []
        for step in node.via:
            if step.dir == "up":
                ups.append(step)
            else:
                segments.append((tuple(ups), step))
                ups = []
        return self.segment(node, tuple(segments), 0, table)

    def segment(
        self, node: RCovered, segments: tuple[tuple[Path, Step], ...], index: int, table: str
    ) -> str:
        """``covered`` from its ``index``-th down step on, for each row of ``table`` (§6.5): the
        last step's closedness, and each earlier step's children's answers aggregated."""
        key = ("segment", canonical(canonical_clause(node)), index, table)
        found = self.memo.get(key)
        if found is not None:
            return found
        ups, down = segments[index]
        coverage = self.coverage[down.rel]
        parent = coverage.parent_table
        terms = _Terms(self, coverage)
        rows = _col("n", "rid")
        joins: list[tuple[str, str, str, Expression]] = [(self.scope(coverage), "sc", "rid", rows)]
        branches: list[tuple[Expression, _Answer]] = [terms.out_of_scope]
        if index == len(segments) - 1:
            admitted = {
                column: frozenset(key_part(value) for value in values)
                for column, values in node.scope or ()
            }
            joins.append((self.closedness(coverage, "some", admitted), "cl", "rid", rows))
            branches.append((terms.closed, _Answer(_num(TRUE_CODE), _num(0), terms.covm)))
            if coverage.form == "undeclared":
                default = terms.none(Reason.NO_INFORMATION)
            else:
                sv, sr = _col("sc", "v"), _col("sc", "r")
                branches.append(
                    (_eq(sv, _num(UNKNOWN_CODE)), _Answer(_num(UNKNOWN_CODE), sr, self.zeros()))
                )
                default = _Answer(_num(FALSE_CODE), _num(0), terms.prop)
        else:
            child_values = self.segment(node, segments, index + 1, coverage.child_table)
            lift = node.lift or "strict"
            quantifier = "every" if lift == "strict" else "some"
            joins += [
                (self.closedness(coverage, quantifier, {}), "cl", "rid", rows),
                (self.children(down.rel, child_values, None, _DROP[lift], None), "s", "aid", rows),
            ]
            t, f, u = terms.t, terms.f, terms.u
            branches.append(
                (_eq(terms.nk, _num(0)), terms.unknown(_num(REASON_BIT[Reason.NOT_COVERED])))
            )
            if lift == "strict":
                branches += [
                    (
                        exp.GTE(this=f, expression=_num(1)),
                        _Answer(_num(FALSE_CODE), _num(0), terms.mf),
                    ),
                    (exp.GTE(this=u, expression=_num(1)), terms.unknown(terms.ru)),
                    (terms.closed, terms.closed_as(TRUE_CODE, terms.ma)),
                ]
                default = terms.unknown(_num(0))
            else:
                branches += [
                    (
                        _and(terms.closed, exp.GTE(this=t, expression=_num(1))),
                        terms.closed_as(TRUE_CODE, terms.mt),
                    ),
                    (_and(terms.closed, _eq(f, terms.nk)), terms.closed_as(FALSE_CODE, terms.ma)),
                ]
                default = terms.unknown(terms.ru)
        source = _table(self.base(parent).name, "n")
        answer = self.answer(source, rows, _answered(branches, default), joins)
        found = self.memo[key] = self.through(table, ups, parent, answer)
        return found


class _Terms:
    """The terms of §6.5's answers for one question: its children's aggregates (alias ``s``),
    its parent row's closedness (``cl``) and parent scope (``sc``), and the flags its coverage
    adds (step 7)."""

    def __init__(self, compiler: _Compiler, coverage: Coverage) -> None:
        self.words = compiler.words
        self.zeros = compiler.zeros()

        def z(name: str) -> Expression:
            return _zero(_col("s", name))

        self.t, self.f, self.u = z("t"), z("f"), z("u")
        self.nk = exp.Paren(
            this=exp.Add(this=exp.Add(this=self.t, expression=self.f), expression=self.u)
        )
        self.ru = z("ru")
        self.mt, self.mf, self.mu, self.md, self.ma = (
            tuple(z(f"{kind}{at}") for at in range(self.words))
            for kind in ("mt", "mf", "mu", "md", "ma")
        )
        self.closed = _col("cl", "cl")
        self.crs = _col("cl", "crs")
        relationship = coverage.relationship
        self.prop = compiler.mark_words(
            [Mark(Flag.COVERAGE_PROPOSED, relationship)] if coverage.proposed else []
        )
        partial = compiler.mark_words(
            [Mark(Flag.SCOPE_PARTIAL, relationship)] if coverage.scope_columns else []
        )
        self.covm = tuple(
            _bits(_case([(_col("cl", "rs"), partial[at])], _num(0)), self.prop[at])
            for at in range(self.words)
        )
        """The flags an answer that relies on closedness adds: ``SCOPE_PARTIAL`` when it was
        restricted to the listed tuples, and ``COVERAGE_PROPOSED``."""
        self.out_of_scope = (
            _eq(_col("sc", "v"), _num(FALSE_CODE)),
            self.none(Reason.OUT_OF_SCOPE),
        )

    def none(self, reason: Reason) -> _Answer:
        """UNKNOWN for one reason, with no flags."""
        return _Answer(_num(UNKNOWN_CODE), _num(REASON_BIT[reason]), self.zeros)

    def open(self, expression: Expression) -> Expression:
        """The expression where the parent row is not closed, and 0 where it is."""
        return _case([(self.closed, _num(0))], expression)

    def unknown(self, reasons: Expression) -> _Answer:
        """UNKNOWN (step 5): the reasons, with the closedness reasons where the row is not
        closed; the flags of the unknown and dropped children, with ``COVERAGE_PROPOSED`` where
        a closedness reason was added (step 7)."""
        words = tuple(
            _bits(self.mu[at], self.md[at], self.open(self.prop[at])) for at in range(self.words)
        )
        return _Answer(_num(UNKNOWN_CODE), _bits(reasons, self.open(self.crs)), words)

    def closed_as(self, code: int, marks: tuple[Expression, ...]) -> _Answer:
        """An answer that relies on closedness: ``marks``, and the coverage's flags."""
        words = tuple(_bits(marks[at], self.covm[at]) for at in range(self.words))
        return _Answer(_num(code), _num(0), words)


# --- Helpers ----------------------------------------------------------------------------------


def _unrouted(node: RValue) -> RValue:
    """A value leaf without its lookups: the same cell relation serves every path to it."""
    return RValue(node.column, node.predicate, (), node.negate, node.units, node.match)


def _based(state: Expression | None, v: Expression, r: Expression) -> tuple[Expression, Expression]:
    """A cell's code and reasons: ``v`` and ``r`` where it is PRESENT, and otherwise its state's
    base result (§6.4): FALSE when NOT_APPLICABLE, UNKNOWN (``NOT_ASSESSED``) when NOT_ASSESSED,
    and UNKNOWN (``NO_INFORMATION``) otherwise."""
    if state is None:
        return v, r
    present = _eq(state, exp.Literal.string(ObservationState.PRESENT.value))
    applicable = _eq(state, exp.Literal.string(ObservationState.NOT_APPLICABLE.value))
    assessed = _eq(state, exp.Literal.string(ObservationState.NOT_ASSESSED.value))
    based_v = _case([(present, v), (applicable, _num(FALSE_CODE))], _num(UNKNOWN_CODE))
    based_r = _case(
        [
            (present, r),
            (applicable, _num(0)),
            (assessed, _num(REASON_BIT[Reason.NOT_ASSESSED])),
        ],
        _num(REASON_BIT[Reason.NO_INFORMATION]),
    )
    return based_v, based_r


def _order(release: Release, column: str) -> dict[str, int] | None:
    """The positions of an ordered category's listed values, as the evaluator reads them."""
    table, name = column.split(".", 1)
    descriptor = release.column(table, name)
    allowed = None if descriptor is None else descriptor.fields.permissible_values
    if allowed is None or not allowed.ordered:
        return None
    return {entry.value: at for at, entry in enumerate(allowed.values)}


def _within(value: int, gt: int | None, gte: int | None, lt: int | None, lte: int | None) -> bool:
    return (
        (gt is None or value > gt)
        and (gte is None or value >= gte)
        and (lt is None or value < lt)
        and (lte is None or value <= lte)
    )


def _admitted(node: RExists) -> dict[str, frozenset[KeyPart]]:
    """The values ``W_C`` admits on each child column it mentions in top-level ``values``
    conjuncts without ``negate``, as the evaluator reads them."""
    admitted: dict[str, frozenset[KeyPart]] = {}
    for clause in node.where:
        if (
            isinstance(clause, RValue)
            and not clause.via
            and not clause.negate
            and isinstance(clause.predicate, Values)
            and clause.column.split(".", 1)[0] == node.table
        ):
            column = clause.column.split(".", 1)[1]
            values = frozenset(key_part(value) for value in clause.predicate.values)
            admitted[column] = admitted[column] & values if column in admitted else values
    return admitted


def _lifted(clause: RClause) -> bool:
    pending = [clause]
    while pending:
        node = pending.pop()
        if isinstance(node, RExists | RCovered) and node.lift is not None:
            return True
        if isinstance(node, RExists):
            pending.extend(node.where)
        elif isinstance(node, RAll | RAny):
            pending.extend(node.members)
        elif isinstance(node, RNot | RKnown | RUnknown):
            pending.append(node.member)
    return False


def _micros(value: datetime) -> int:
    return (value - _EPOCH) // _MICROSECOND


def _bindable(kind: PhysicalType, constants: Sequence[Constant | None]) -> list[object]:
    """The constants a value stored as ``kind`` can equal, as ``key_part`` compares them, in the
    stored type: an integral double as an integer for an integer column, an integer a double
    holds exactly as a double for a double column, datetimes in microseconds; the rest dropped."""
    found: list[object] = []
    for constant in constants:
        if constant is None:
            continue
        if kind == "int64":
            if isinstance(constant, bool) or not isinstance(constant, int | float):
                continue
            if isinstance(constant, float):
                if not (math.isfinite(constant) and constant.is_integer()):
                    continue
                constant = int(constant)
            if _INT64[0] <= constant <= _INT64[1]:
                found.append(constant)
        elif kind == "float64":
            if isinstance(constant, bool) or not isinstance(constant, int | float):
                continue
            as_float = float(constant) if abs(constant) < 2**1024 else math.inf
            if isinstance(constant, int) and as_float != constant:
                continue
            found.append(as_float)
        elif kind in ("string", "strings"):
            if isinstance(constant, str):
                found.append(constant)
        elif kind == "bool":
            if isinstance(constant, bool):
                found.append(constant)
        elif kind == "date32":
            if isinstance(constant, date) and not isinstance(constant, datetime):
                found.append(constant)
        elif isinstance(constant, datetime):
            found.append(_micros(constant))
    unique: list[object] = []
    seen: set[str] = set()
    for value in found:
        marker = json.dumps(value, default=str)
        if marker not in seen:
            seen.add(marker)
            unique.append(value)
    return unique


def _bound(kind: PhysicalType, name: str, bound: Constant) -> object:
    """A range bound in the stored type, or ``True``/``False`` when it holds for every value or
    none: for an integer column, a double's floor or ceiling, as the comparison implies."""
    if kind == "timestamp":
        assert isinstance(bound, datetime)
        return _micros(bound)
    if kind == "float64":
        return float(cast(int | float, bound))
    if kind != "int64" or isinstance(bound, int):
        return bound
    number = cast(float, bound)
    if math.isinf(number):
        above = number > 0
        return (not above) if name in ("gt", "gte") else above
    whole = math.floor(number) if name in ("gt", "lte") else math.ceil(number)
    low, high = _INT64
    if name == "gt":
        return False if whole >= high else True if whole < low else whole
    if name == "gte":
        return False if whole > high else True if whole <= low else whole
    if name == "lt":
        return False if whole <= low else True if whole > high else whole
    return False if whole < low else True if whole >= high else whole


__all__ = [
    "EXCLUSION_BIT",
    "FALSE_CODE",
    "MARK_BITS",
    "REASON_BIT",
    "TRUE_CODE",
    "UNKNOWN_CODE",
    "Accounting",
    "CompileError",
    "CompiledCohort",
    "CompiledCrossing",
    "CompiledMaterialised",
    "CompiledMembers",
    "Crossed",
    "CrossedSplit",
    "Crossing",
    "Parameter",
    "TruthValues",
    "compile_cohort",
    "compile_crossing",
    "compile_materialised",
    "compile_members",
    "cross",
    "pairs",
]
