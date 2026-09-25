"""Redacting a person's keys and values from the app DB (SPEC §12.2, Erasure; D223, D290).

A term is a value's canonical string (§12.2). The person's terms are their row's key and its
values in identifier columns (§5.4), each item of a list; the terms below them are the keys of
the rows below theirs, and those rows' values in identifier columns. A string is read under
every datatype it may be of (``readings``: as text; as a number, as RFC 8785 writes it; as a
date; as an instant, in UTC), by the store's cell reader and by the engine's reader of a
document's constants alike, so that every spelling either accepts has its value:
``2024-02-03T12:30:00.0000000+01:00``, ``2024-02-03T11:30:00Z`` and a datetime cell's
``2024-02-03T11:30:00+00:00`` are one instant, and ``2``, ``2.0`` and ``2e0`` one number.

How a value is matched depends on where it is (``Place``, D290):

- ``NAMING``, where its values name the person's rows: a key or identifier column of a table that
  holds them or a foreign key into one (a ``covered`` clause's scope column being one of its
  table's), a column the core cannot name (a parameter, a value concept, a scope of a table
  given as a parameter or a concept), a part of a unit key of such a table (or of a unit that is
  not known to be a table), a pack leaf as written. A value is a term when they share a reading,
  JSON numbers and booleans included, and a string also when it holds a term as a token by
  every reading (below), but only a term of the tables whose rows the place names (``Where``):
  each term is kept with the tables it came from, and a key or identifier column names its own
  table's rows, a foreign key those of the table it points into, a unit key its table's, and
  what the core cannot name every table's; no other term, text or number, is matched there;
- ``UNKNOWN``, where nothing says what it is: free text, structure, a parameter no clause refers
  to, a result's strings, the audit trail and the proposal queue. The same, but for JSON
  numbers and booleans, and for the terms below the person that are numbers by their columns'
  datatypes (a surrogate key ``2``), which are matched in neither;
- ``OTHER``, a constant on a known column whose values do not name the person's rows, a part
  of a unit key of a table that holds none of them, and anything in a tree, or a cohort or a
  view as written, over another dataset: a string by its text alone, the whole of it or a whole
  token of it that is no part of a longer number, date, time or range, so that ``07``,
  ``7.0``, ``+7``, ``6-7`` and ``7-8`` do not hold ``7``, nor ``2024-01-01T00:00:00Z`` or
  ``2024-01-01 10:00`` the date ``2024-01-01``; JSON numbers and booleans, and the numbers
  below the person, never.

A token of a term, in a string matched by every reading (``Terms.text``), is:

- the term as it is written, next to no letter, digit or mark (Unicode category ``M``), in any
  script, so ``m-17`` is erased from ``member m-17`` but not from ``m-170``, nor ``Jos`` from
  ``José``, nor ``क`` from ``कि``; text terms match exactly, case included: keys are exact;
- a date and time (``T``, ``t`` or a space between them; to the minute or the second, with a
  fraction after ``.`` or ``,`` of any length; ``Z``, ``z``, ``UTC``, ``GMT``, ``±HH:MM``,
  ``±HHMM``, ``±HH`` or none, after a space or not) whose instant, read at its offset or as
  UTC, is a term's;
- a number (``0017``, ``+17``, ``17.0``, ``1.7e1``: a sign, digits, a fraction and an exponent)
  that no ``.``, sign or ``-`` joins to more text, nor a ``,``, ``:`` or ``/`` to another digit,
  whose value is one of the person's terms' (so ``017`` in ``member 017: Grace``, but not in
  ``10:017`` or ``3/017``). Digits grouped by separators (``1,017``) are read as no number,
  neither the whole nor a group.

The terms below the person that are numbers by their columns' datatypes are never tokens, so
``page 2`` keeps its ``2``; every other term, the person's or below them, is.

Two object keys that both become ``[erased]`` are both kept, the second as ``[erased] (2)``, the
third as ``[erased] (3)``. A name a document gives (a cohort's, a parameter's) is redacted as an
object key, and every reference to it as the name is, so that it still names what it named.

JSON Pointers are never redacted: they name places in descriptors, not people. Neither are the
labels, manifest hashes, descriptor ids, pointers and proposal ids the store writes into its own
lifecycle entries (imports, publishes, sessions, withdrawals, rejections, D252), nor an erasure's
own entry, which holds its table, its mode and counts only.

Each redactor redacts one table of the app DB, for one dataset. M1 has the audit trail, the
proposal queue and the catalogue index, whose entry for the dataset is deleted: a JSON string or
number of the first two that is a term under a reading both have, or an object key or evidence
that is one, is erased whole, and free text as above. M2 has the derivation log (D290): a
derivation over one of the dataset's releases whose object holds a term loses its object (it
keeps its id, which resolves to *erased*) and its issuances. Only the object's data can hold
one: the constants of a cohort's canonical clause trees (``values``, range bounds, ``scope``
values and unit keys) and a result's parameters (their clauses as a cohort's, their strings),
never its versions, disclosure setting, descriptor ids, paths or counts. Every other issuance
that names the dataset, through its derivation or in its document as written or its parameters,
has its document, parameters and SQL redacted by the same rules (``_Written``), so that what
names no one (``aibi``, counts, other columns' bounds) stays as it was; the log's derivations
and issuances are each read a batch at a time. ``redact`` holds the log's permit for its
transaction (D289), refuses to run outside one, and compiles no pattern of the terms (they are
found in text by its tokens, looked up in sets), emptying ``re``'s cache however it ends. It is
one pass over what the log holds when it runs (D290). Later milestones register
theirs (saved documents, cached results) with ``register``.
"""

import json
import math
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from enum import IntEnum
from functools import cache
from typing import Any, cast

from pydantic import JsonValue

from aibi.core.engine.resolve import typed_constant
from aibi.core.schema.ids import CONCEPT_ID_RE
from aibi.core.schema.jsonio import canonical, number_text
from aibi.core.store.appdb import loads
from aibi.core.store.cells import typed
from aibi.core.store.derivations import text_of
from aibi.core.store.sources import canonical_string

MARK = "[erased]"
ERASE_ACTION = "erase"
"""The audit action of an erasure, whose entry names no one (``erasure.erase``)."""

RELEASE_ACTIONS = frozenset(
    {
        "import",
        "reimport",
        "publish",
        "withdraw",
        "open",
        "change",
        "take_over",
        "discard",
        "reject_proposal",
    }
)
"""The audit actions of the entries the store writes about releases, sessions and proposals
(D252): their details name releases and places in descriptors, never values."""

RELEASE_REFERENCES = frozenset(
    {"label", "labels", "manifest", "base", "draft", "previous", "edits", "proposal", "proposals"}
)
"""Members of those entries' details that name releases, descriptor fields and proposals: labels,
manifest hashes, (descriptor id, pointer) pairs and proposal ids, which hold no person's data and
are left as they are."""


class Place(IntEnum):
    """How a constant is matched (D290; the module's docstring): the kinds of place a ``Where``
    is made of."""

    OTHER = 0
    """On a known column whose values do not name the person's rows, or a part of a unit key of
    a table that holds none of them: a string by its text alone."""
    UNKNOWN = 1
    """Free text, structure, or a value nothing says the place of: by every reading, but never
    a JSON number or boolean, nor a number below the person."""
    NAMING = 2
    """Where its values name the person's rows: by every reading, numbers too, but only the
    terms of the tables whose rows it names (``Where``)."""


@dataclass(frozen=True)
class Where:
    """Where a constant is (D290). ``naming`` is the tables whose terms it holds as a naming
    place does: those whose rows its values name (a key or identifier column's table, the
    table a foreign key points into, a unit key's table), ``None`` for every table's where the
    core cannot say which rows they name, and none elsewhere. ``rest`` says how it holds every
    other term: as ``Place.UNKNOWN`` or ``Place.OTHER`` does, or not at all (``None``), since a
    value on a naming place is a key of its tables' rows, and one that is only another table's
    term there names a row that is not the person's. A value in more than one place (a
    parameter referred to from several) is matched as in each: ``a | b`` holds what either
    holds."""

    naming: frozenset[str] | None = frozenset()
    rest: Place | None = None

    def __post_init__(self) -> None:
        if self.rest is Place.NAMING:
            raise ValueError("a naming place is given by its tables")

    def __or__(self, other: "Where") -> "Where":
        tables = None if self.naming is None or other.naming is None else self.naming | other.naming
        rests = [rest for rest in (self.rest, other.rest) if rest is not None]
        return Where(tables, max(rests) if rests else None)

    @classmethod
    def of(cls, place: "Place | Where") -> "Where":
        """A place as a ``Where``; ``Place.NAMING`` alone may name any table's rows."""
        if isinstance(place, Where):
            return place
        return cls(None) if place is Place.NAMING else cls(frozenset(), place)


NOWHERE = Where()
"""A place that holds no term: where nothing has referred to a parameter yet."""
_OTHER = Where(frozenset(), Place.OTHER)
_UNKNOWN = Where(frozenset(), Place.UNKNOWN)
_NAMING = Where(None)


@cache
def _word() -> str:
    """A letter or a digit of any script (``\\w`` but ``_``), or a mark of any script (a
    character whose Unicode category is ``M*``, as this Python's ``unicodedata`` has them)."""
    marks = [c for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c))[0] == "M"]
    ranges: list[tuple[int, int]] = []
    for c in marks:
        if ranges and ranges[-1][1] == c - 1:
            ranges[-1] = (ranges[-1][0], c)
        else:
            ranges.append((c, c))
    spans = "".join(rf"\U{start:08x}-\U{end:08x}" for start, end in ranges)
    return rf"(?:[^\W_]|[{spans}])"


_INSTANT = (
    r"(?P<day>[0-9]{4}-[0-9]{2}-[0-9]{2})[Tt ](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
    r"(?::(?P<second>[0-9]{2})(?:[.,](?P<fraction>[0-9]+))?)?"
    r"(?: ?(?P<offset>[Zz]|UTC|GMT|[+-][0-9]{2}(?::?[0-9]{2})?))?"
)
"""A date and time in free text: RFC 3339's and ISO 8601's common spellings and their
variants, with ``T``, ``t`` or a space, to the minute or the second, a fraction of any length,
and an offset (``Z``, ``UTC``, ``GMT``, ``±HH:MM``, ``±HHMM``, ``±HH``), after a space or not,
or none."""

_NUMBER = (
    r"(?<![.+\-])(?<![0-9][,:/])[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    r"(?![+\-])(?![.,:/][0-9])"
)
"""A number in free text, as a number cell reads one, that no ``.``, sign or ``-`` joins to more
text, nor a ``,``, ``:`` or ``/`` to another digit (so neither ``02`` in ``2024-02-03``, ``17``
in ``m-17``, ``10:17`` or ``3/17``, nor either group of ``1,017``, is one, while ``017`` in
``member 017: Grace`` is)."""


@cache
def _instants() -> re.Pattern[str]:
    return re.compile(rf"(?<!{_word()}){_INSTANT}(?!{_word()})")


@cache
def _numbers() -> re.Pattern[str]:
    return re.compile(rf"(?<!{_word()}){_NUMBER}(?!{_word()})")


Reading = tuple[str, str]
"""A value as one datatype reads it: ``("text", s)``, or ``("number" | "date" | "datetime",
its canonical string)``, the number as RFC 8785 writes it and the datetime in UTC."""


CACHED_READING = 256
"""The longest string whose readings a ``Terms`` keeps while it redacts, so that long text (SQL,
notes) is read each time rather than held."""
CACHED_READINGS = 65_536
"""The most strings whose readings a ``Terms`` keeps; it forgets them all when it has more."""


def readings(value: str) -> frozenset[Reading]:
    """The values a string is under every datatype it may be read as, by a cell of a column of
    that datatype (``cells.typed``) and by a document's constant on one (``typed_constant``): as
    text, and as an integer or a number, a date and a datetime where either reads it as one,
    each in its canonical form (D290). ``2024-02-03T12:30:00+01:00``,
    ``2024-02-03T11:30:00.0000000000Z`` and ``2024-02-03 11:30:00`` are one instant; ``2``,
    ``2.0`` and ``2e0`` one number."""
    found: set[Reading] = {("text", value)}
    for datatype in ("integer", "number"):
        number = typed(value, datatype)
        if isinstance(number, int | float) and not isinstance(number, bool):
            found.add(("number", number_text(number)))
    for datatype in ("date", "datetime"):
        for moment in (typed(value, datatype), typed_constant(value, datatype)):
            if isinstance(moment, date):
                found.add((datatype, cast(str, canonical_string(moment))))
    return frozenset(found)


def _json_readings(value: JsonValue) -> frozenset[Reading]:
    """A JSON constant's readings but a string's: a finite number's as a number, a boolean's as
    its text; nothing for anything else."""
    if isinstance(value, bool):
        return frozenset({("text", "true" if value else "false")})
    if isinstance(value, int | float):
        return frozenset({("number", number_text(value))}) if math.isfinite(value) else frozenset()
    return frozenset()


def _moments(found: re.Match[str]) -> frozenset[Reading]:
    """The instants a date and time found in free text may be: at the offset written, and, the
    offset aside, as UTC, each as a reading; none for a date or time that does not exist."""
    day, hour, minute = found.group("day", "hour", "minute")
    second, fraction, offset = found.group("second", "fraction", "offset")
    year, month, mday = (int(part) for part in day.split("-"))
    micro = int((fraction or "")[:6].ljust(6, "0"))
    try:
        wall = datetime(
            year, month, mday, int(hour), int(minute), int(second or 0), micro, tzinfo=UTC
        )
    except ValueError:
        return frozenset()
    moments = [wall]
    if offset is not None and offset[0] in "+-":
        digits = offset[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:] or 0)
        if hours <= 23 and minutes <= 59:
            shift = timedelta(hours=hours, minutes=minutes) * (1 if offset[0] == "+" else -1)
            with suppress(OverflowError):
                moments.append(wall.replace(tzinfo=timezone(shift)).astimezone(UTC))
    return frozenset(("datetime", cast(str, canonical_string(moment))) for moment in moments)


_DIGITS = frozenset("0123456789")
_JOINS = frozenset(".,:/")
_CLOCK = re.compile(r" [0-9]{2}:[0-9]{2}")


def _digit(value: str, at: int) -> bool:
    return 0 <= at < len(value) and value[at] in _DIGITS


def _bounded(value: str, term: str, start: int, stop: int) -> bool:
    """Whether a term written at ``value[start:stop]`` is a token where only its text counts
    (``Place.OTHER``): one that starts or ends with a digit is no part of a longer number, date,
    time or range, so no sign comes before it, no ``.``, ``,``, ``:``, ``/`` or ``-`` joins it
    to another digit on either side, and no time of day (`` HH:MM``) follows it, so that
    ``2020-01-02`` is not the date of ``2020-01-02 10:00``, nor ``7`` a bound of ``6-7`` or
    ``7-8``."""
    if term[0] in _DIGITS and start > 0:
        before = value[start - 1]
        if before in "+-" or (before in _JOINS and _digit(value, start - 2)):
            return False
    if term[-1] in _DIGITS and stop < len(value):
        after = value[stop]
        if (after in _JOINS or after == "-") and _digit(value, stop + 1):
            return False
        if _CLOCK.match(value, stop):
            return False
    return True


@cache
def _runs() -> re.Pattern[str]:
    """A token of text: a run of letters, digits and marks (``_word``), or one other
    character."""
    return re.compile(rf"(?P<run>{_word()}+)|.", re.DOTALL)


class Terms:
    """The values to erase: the person's and those below them, each with the tables it came
    from, each matched as its place says (``Where``, D290). A term is found in text by
    splitting the text into tokens (``_runs``) and looking up the runs of them that may be one
    in sets, so that no pattern is compiled of the terms, however many there are."""

    def __init__(
        self,
        person: Collection[str],
        below: Collection[str] = (),
        *,
        numbers: Collection[str] = (),
        tables: Collection[str] | None = None,
        columns: Mapping[str, Collection[str]] | None = None,
        origins: Mapping[str, Collection[str]] | None = None,
    ) -> None:
        self.person = frozenset(term for term in person if term)
        self.below = frozenset(term for term in below if term) - self.person
        self.terms = self.person | self.below
        self.numeric = frozenset(numbers) & self.terms
        """The terms, the person's or below them, that are numbers by their columns'
        datatypes."""
        self.numbers = self.numeric & self.below
        """The terms below the person that are numbers by their columns' datatypes (a surrogate
        key ``2``); a term below them of any other datatype is text, however it reads (D290)."""
        self.tables = None if tables is None else frozenset(tables)
        """The tables that hold the person's rows; ``None`` when no release says (D290)."""
        self.columns = (
            None
            if columns is None
            else {column: frozenset(named) for column, named in columns.items()}
        )
        """The columns, by descriptor id, whose values name the person's rows, each with the
        tables whose rows its values name: a key or identifier column of a table that holds
        them, that table, and a foreign key into one, the table it points into; ``None`` when
        no release says (D290)."""
        self.origins = (
            None
            if origins is None
            else {term: frozenset(found) for term, found in origins.items() if term in self.terms}
        )
        """The tables each term came from (a row of the person's there holds it); a term with
        none given, or every term when ``None``, may be any table's (D290)."""
        self._read: dict[str, frozenset[Reading]] = {}
        read = {term: readings(term) for term in self.terms}
        found: dict[Reading, set[str]] = {}
        for term, those in read.items():
            for reading in those:
                found.setdefault(reading, set()).add(term)
        self.index = {reading: frozenset(terms) for reading, terms in found.items()}
        """Each reading of a term, and the terms that read as it."""
        self.exact = self.person | (self.below - self.numbers)
        """The terms matched as text and as tokens: all but the numbers below the person."""
        self.instants = frozenset(
            reading for term in self.exact for reading in read[term] if reading[0] == "datetime"
        )
        """The instants of the terms that are tokens, whichever spelling a token gives."""
        self.values = frozenset(
            reading for term in self.person for reading in read[term] if reading[0] == "number"
        )
        """The values of the person's terms that read as numbers, whichever spelling a token
        gives (``0017``, ``1.7e1``)."""
        starts: dict[str, set[int]] = {}
        for term in self.exact:
            tokens = list(_runs().finditer(term))
            starts.setdefault(tokens[0].group(), set()).add(len(tokens))
        self.starts = {first: sorted(counts, reverse=True) for first, counts in starts.items()}
        """The first token of each term that is a token of text, and how many tokens the
        terms that start with it have, the most first, so that the longest term written at a
        place is the one found there."""

    def __bool__(self) -> bool:
        return bool(self.terms)

    def __len__(self) -> int:
        return len(self.terms)

    def readings(self, value: str) -> frozenset[Reading]:
        """``readings``, kept for strings of at most ``CACHED_READING`` characters by this
        ``Terms`` alone, so that no module-level cache holds what a redaction read."""
        if len(value) > CACHED_READING:
            return readings(value)
        found = self._read.get(value)
        if found is None:
            if len(self._read) >= CACHED_READINGS:
                self._read.clear()
            found = self._read[value] = readings(value)
        return found

    def dumps(self) -> str:
        """The terms as the app DB holds a waiting redaction's."""

        def listed(given: Mapping[str, frozenset[str]] | None) -> dict[str, list[str]] | None:
            if given is None:
                return None
            return {key: sorted(found) for key, found in sorted(given.items())}

        return json.dumps(
            {
                "below": sorted(self.below),
                "numbers": sorted(self.numeric),
                "person": sorted(self.person),
                "tables": None if self.tables is None else sorted(self.tables),
                "columns": listed(self.columns),
                "origins": listed(self.origins),
            }
        )

    @classmethod
    def loads(cls, text: str) -> "Terms":
        stored = cast(dict[str, Any], json.loads(text))
        return cls(
            stored["person"] or (),
            stored["below"] or (),
            numbers=stored.get("numbers") or (),
            tables=stored.get("tables"),
            columns=stored.get("columns"),
            origins=stored.get("origins"),
        )

    def names(self, term: str, tables: frozenset[str] | None) -> bool:
        """Whether a term may be one of these tables' (every table's, given ``None``)."""
        if tables is None or self.origins is None:
            return True
        found = self.origins.get(term)
        return found is None or not found.isdisjoint(tables)

    def _every(self, term: str, where: Where) -> bool:
        """Whether a term is matched by every reading where it is: a term of the naming
        place's tables, or any term on ``Place.UNKNOWN``."""
        return where.rest is Place.UNKNOWN or (
            where.naming != frozenset() and self.names(term, where.naming)
        )

    def text(self, value: str, place: Place | Where = Place.UNKNOWN) -> str:
        """Text with every token of a term erased (the module's docstring): where every reading
        counts, a term as it is written, a date and time whose instant is a term's, and a
        number whose value is one of the person's terms'; on ``Place.OTHER``, a term as it is
        written only, and no part of a longer number, date or time; on a naming place, every
        reading of its tables' terms only."""
        where = Where.of(place)
        if where == NOWHERE:
            return value
        value = self._tokens(value, where)
        if where.naming == frozenset() and where.rest is Place.OTHER:
            return value
        if self.instants:
            value = _instants().sub(lambda found: self._instant(found, where), value)
        if self.values:
            value = _numbers().sub(lambda found: self._number(found, where), value)
        return value

    def _tokens(self, value: str, where: Where) -> str:
        """Text with every term that is a token of it, as it is written, erased: at each token
        that starts a term and has no letter, digit or mark before it, the longest term that
        ends where no letter, digit or mark follows and that its place holds."""
        if not self.starts:
            return value
        tokens = [
            (found.start(), found.end(), found.lastgroup) for found in _runs().finditer(value)
        ]
        pieces: list[str] = []
        last = 0
        at = 0
        while at < len(tokens):
            start, end, run = tokens[at]
            counts = self.starts.get(value[start:end])
            if counts is not None and (run is not None or at == 0 or tokens[at - 1][2] is None):
                for count in counts:
                    final = at + count - 1
                    if final >= len(tokens):
                        continue
                    stop = tokens[final][1]
                    joined = final + 1 < len(tokens) and tokens[final + 1][2] is not None
                    if joined and tokens[final][2] is None:
                        continue
                    term = value[start:stop]
                    if term in self.exact and (
                        self._every(term, where)
                        or (where.rest is Place.OTHER and _bounded(value, term, start, stop))
                    ):
                        pieces += [value[last:start], MARK]
                        last = stop
                        at = final
                        break
            at += 1
        return "".join(pieces) + value[last:] if pieces else value

    def _instant(self, found: re.Match[str], where: Where) -> str:
        for reading in _moments(found) & self.instants:
            if any(term in self.exact and self._every(term, where) for term in self.index[reading]):
                return MARK
        return found.group()

    def _number(self, found: re.Match[str], where: Where) -> str:
        for reading in self.readings(found.group()) & self.values:
            if any(
                term in self.person and self._every(term, where) for term in self.index[reading]
            ):
                return MARK
        return found.group()

    def whole(self, value: JsonValue, place: Place | Where) -> bool:
        """Whether a constant is, as a whole, a term, as its place says (D290). A JSON number or
        boolean is one only on a naming place, where it shares a reading with a term of its
        tables (a count bound, an age or a surrogate key of another table that happens to equal
        one names no one). A string on ``Place.OTHER`` is one when it is the text of a term but
        the numbers below the person; elsewhere, when it shares a reading with a term: on a
        naming place any term of its tables, and on ``Place.UNKNOWN`` any of the person's and
        one below them read as anything but a number (``"2"`` names no loan there, but
        ``"104233"`` is a member)."""
        where = Where.of(place)
        naming = where.naming != frozenset()
        if not isinstance(value, str):
            return naming and any(
                self.names(term, where.naming)
                for reading in _json_readings(value)
                for term in self.index.get(reading, ())
            )
        if where.rest is Place.OTHER and value in self.exact:
            return True
        for reading in self.readings(value):
            for term in self.index.get(reading, ()):
                if naming and self.names(term, where.naming):
                    return True
                if where.rest is Place.UNKNOWN and (
                    term in self.person or (term not in self.numbers and reading[0] != "number")
                ):
                    return True
        return False

    def in_constant(self, value: JsonValue, place: Place | Where) -> bool:
        """Whether a constant holds a term (D290): as a whole (``whole``), or, for a string, as
        a token (``text``)."""
        if self.whole(value, place):
            return True
        return isinstance(value, str) and self.text(value, place) != value

    def constant(self, value: JsonValue, place: Place | Where) -> JsonValue:
        """A constant, ``[erased]`` if it is a term as a whole; a string that holds one as a
        token keeps the rest of its text."""
        if self.whole(value, place):
            return MARK
        return self.text(value, place) if isinstance(value, str) else value

    def string(self, value: str) -> str:
        """A JSON string or key: erased whole if it shares a reading with any term, else as
        free text."""
        return MARK if not self.readings(value).isdisjoint(self.index) else self.text(value)

    def json(self, value: JsonValue, keep: Collection[str] = ()) -> JsonValue:
        """A JSON value, redacted; an object's members named in ``keep`` are left as they are
        (at the top level only)."""
        if isinstance(value, str):
            return self.string(value)
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int | float):
            return MARK if not _json_readings(value).isdisjoint(self.index) else value
        if isinstance(value, list):
            return [self.json(item) for item in value]
        return self._object(value, keep)

    def _object(self, value: Mapping[str, JsonValue], keep: Collection[str]) -> JsonValue:
        return self.keyed(value, lambda _, member: self.json(member), keep)

    def keyed(
        self,
        value: Mapping[str, JsonValue],
        redact: Callable[[str, JsonValue], JsonValue],
        keep: Collection[str] = (),
    ) -> dict[str, JsonValue]:
        """An object whose keys are redacted as strings (``renamed``) and whose members by
        ``redact`` (given the key as written), but for the members named in ``keep``, left as
        they are."""
        names = self.renamed(value, keep)
        return {
            names[key]: member if key in keep else redact(key, member)
            for key, member in value.items()
        }

    def renamed(self, keys: Collection[str], keep: Collection[str] = ()) -> dict[str, str]:
        """Each of an object's keys as it is redacted: a key that shares a reading with a term
        is ``[erased]``, and one that holds a term as a token loses it, the keys in ``keep``
        staying as they are; two that come out alike are kept apart, the second as
        ``[erased] (2)``, the third as ``[erased] (3)``."""
        found: dict[str, str] = {}
        redacted: list[tuple[str, str]] = []
        for key in keys:
            name = key if key in keep else self.string(key)
            if name == key:
                found[key] = key
            else:
                redacted.append((key, name))
        taken = set(found.values())
        for key, name in redacted:
            unique, count = name, 1
            while unique in taken:
                count += 1
                unique = f"{name} ({count})"
            found[key] = unique
            taken.add(unique)
        return found


Redactor = Callable[[sqlite3.Connection, str, Terms], int]
"""Redacts one table for a dataset; returns how many rows changed."""


def _audit(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
    changed = 0
    rows = db.execute(
        "SELECT id, action, detail FROM audit WHERE dataset = ? AND action != ?",
        (dataset, ERASE_ACTION),
    ).fetchall()
    for identifier, action, detail in rows:
        keep = RELEASE_REFERENCES if action in RELEASE_ACTIONS else ()
        redacted = canonical(terms.json(loads(cast(str, detail)), keep)).decode()
        if redacted != detail:
            db.execute("UPDATE audit SET detail = ? WHERE id = ?", (redacted, identifier))
            changed += 1
    return changed


def _proposals(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
    changed = 0
    rows = db.execute(
        "SELECT id, value, evidence FROM proposals WHERE dataset = ?", (dataset,)
    ).fetchall()
    for identifier, value, evidence in rows:
        new_value = None if value is None else canonical(terms.json(loads(value))).decode()
        new_evidence = None if evidence is None else terms.string(cast(str, evidence))
        if (new_value, new_evidence) != (value, evidence):
            db.execute(
                "UPDATE proposals SET value = ?, evidence = ? WHERE id = ?",
                (new_value, new_evidence, identifier),
            )
            changed += 1
    return changed


def _catalog(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
    """The catalogue index holds no cell values (D273), but it is derived from releases an
    erasure withdraws: its entry for the dataset goes, and is built again from the latest
    release when the catalogue is next read."""
    return db.execute("DELETE FROM catalog WHERE dataset = ?", (dataset,)).rowcount


def _names_dataset(written: JsonValue, params: JsonValue, dataset: str) -> bool:
    """Whether a document as written names the dataset (its ``dataset``, a cohort's ``dataset``
    or ``datasets``), or a parameter does, one given with the issuance or in the document's own
    ``params``, or one in a list of them."""

    def named(value: JsonValue) -> bool:
        return isinstance(value, str) and value.split("@", 1)[0] == dataset

    found: list[JsonValue] = []
    given = [params]
    if isinstance(written, dict):
        found.append(written.get("dataset"))
        given.append(written.get("params"))
        cohorts = written.get("cohorts")
        if isinstance(cohorts, dict):
            for cohort in cohorts.values():
                if isinstance(cohort, dict):
                    found.append(cohort.get("dataset"))
                    datasets = cohort.get("datasets")
                    if isinstance(datasets, list):
                        found.extend(datasets)
    for values in given:
        if isinstance(values, dict):
            found.extend(values.values())
            for value in values.values():
                if isinstance(value, list):
                    found.extend(value)
    return any(named(value) for value in found)


DERIVATION_BATCH = 256
"""Derivations an erasure reads at a time: the log is never read whole into memory."""


def _derivations(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
    """Take the object of every derivation over the dataset that holds a term, and delete its
    issuances, reading the derivations in id order ``DERIVATION_BATCH`` at a time; how many
    derivations changed."""
    changed = 0
    after = ""
    while True:
        rows = cast(
            list[tuple[str, str, str]],
            db.execute(
                "SELECT d.id, d.hashed, r.manifest FROM derivations d"
                " JOIN derivation_releases r ON r.derivation = d.id"
                " WHERE r.dataset = ? AND d.hashed IS NOT NULL AND d.id > ?"
                " ORDER BY d.id LIMIT ?",
                (dataset, after, DERIVATION_BATCH),
            ).fetchall(),
        )
        for identifier, hashed, manifest in rows:
            if _object_holds(loads(hashed), manifest, terms):
                db.execute("UPDATE derivations SET hashed = NULL WHERE id = ?", (identifier,))
                db.execute("DELETE FROM issuances WHERE derivation = ?", (identifier,))
                changed += 1
        if len(rows) < DERIVATION_BATCH:
            return changed
        after = rows[-1][0]


def _object_holds(hashed: JsonValue, manifest: str, terms: Terms) -> bool:
    """Whether the object a derivation id hashes holds a term (D290): a constant of a cohort's
    canonical clause tree, or a result's canonical parameters. Versions, the disclosure setting,
    descriptor ids, paths and counts such as ``min_count`` are the derivation's structure, never
    a person's data. ``manifest`` is the dataset's release among the derivation's: only its
    clause trees and unit keys name the dataset's rows."""
    if not isinstance(hashed, dict):
        return False
    cohort, view = hashed.get("cohort"), hashed.get("view")
    if isinstance(cohort, dict):
        unit = hashed.get("unit")
        return any(
            _clause_holds(tree, terms, unit if isinstance(unit, str) else None, at == manifest)
            for at, tree in cohort.items()
        )
    if isinstance(view, dict):
        return _params_hold(view.get("params"), terms)
    return _naming_holds(hashed, terms)


def _naming_holds(value: JsonValue, terms: Terms) -> bool:
    """Whether what the core cannot read holds a term, as a constant in a naming place of every
    table does (JSON numbers and booleans too), or as an object key."""
    if isinstance(value, list):
        return any(_naming_holds(item, terms) for item in value)
    if isinstance(value, dict):
        return any(
            terms.string(key) != key or _naming_holds(member, terms)
            for key, member in value.items()
        )
    return terms.in_constant(value, Place.NAMING)


def _place(terms: Terms, column: JsonValue) -> Where:
    """Where a column puts its constants: a naming place of the tables whose rows one of
    ``terms.columns`` names, and of every table when no release says or the column is not a
    descriptor id as written (a ``"$name"`` parameter, or a value concept, from M6, whose column
    each dataset maps, as a concept unit's table is, D290); ``Place.OTHER`` for any other."""
    if (
        terms.columns is None
        or not isinstance(column, str)
        or _reference(column) is not None
        or CONCEPT_ID_RE.fullmatch(column) is not None
    ):
        return _NAMING
    named = terms.columns.get(column)
    return _OTHER if named is None else Where(named)


def _scope_place(terms: Terms, table: JsonValue, column: str) -> Where:
    """Where a ``covered`` clause's scope column puts its constants: a scope names a column of
    the clause's ``table`` by its name alone, so it is that table's column (``_place``); a table
    the core cannot name (a ``"$name"`` parameter, or a concept, from M6) puts them in a naming
    place of every table."""
    known = (
        isinstance(table, str) and _reference(table) is None and not CONCEPT_ID_RE.fullmatch(table)
    )
    return _place(terms, f"{table}.{column}" if known else None)


def _unit_place(terms: Terms, unit: str | None) -> Where:
    """Where a unit puts its keys' parts: a naming place of the unit's table when it holds the
    person's rows, and of every table when no release says or the unit is not known to be a
    table (a concept unit, from M6, whose table each dataset maps, D290); ``Place.OTHER`` for
    any other."""
    if terms.tables is None or unit is None or CONCEPT_ID_RE.fullmatch(unit) is not None:
        return _NAMING
    return Where(frozenset({unit})) if unit in terms.tables else _OTHER


def _clause_holds(clause: JsonValue, terms: Terms, unit: str | None, ours: bool) -> bool:
    """Whether a canonical clause holds a term in its constants: ``values``, range bounds,
    ``scope`` values and unit keys (§7.6), each where its column or unit puts it (``Place``).
    ``ours`` says whether the tree is over the dataset's release, whose columns and unit keys
    alone may name its rows (another's are ``Place.OTHER``); ``unit`` is the unit table, or
    ``None`` when it is not known to be one."""
    if isinstance(clause, list):
        return any(_clause_holds(member, terms, unit, ours) for member in clause)
    if not isinstance(clause, dict):
        return False
    for combinator in _COMBINATORS:
        if combinator in clause:
            return _clause_holds(clause[combinator], terms, unit, ours)
    kind = clause.get("kind")
    if kind == "value":
        place = _place(terms, clause.get("column")) if ours else _OTHER
        given = clause.get("values")
        bounds = clause.get("range")
        constants = list(given) if isinstance(given, list) else []
        if isinstance(bounds, dict):
            constants.extend(bounds.values())
        if "value" in clause:
            constants.append(clause["value"])
        return any(terms.in_constant(constant, place) for constant in constants)
    if kind == "exists":
        return _clause_holds(clause.get("where"), terms, unit, ours)
    if kind == "covered":
        scope = clause.get("scope")
        if not isinstance(scope, dict):
            return False
        for column, values in scope.items():
            place = _scope_place(terms, clause.get("table"), column) if ours else _OTHER
            if isinstance(values, list) and any(terms.in_constant(v, place) for v in values):
                return True
        return False
    if kind == "ids":
        keys = clause.get("ids")
        place = _unit_place(terms, unit) if ours else _OTHER
        for key in keys if isinstance(keys, list) else []:
            parts = key.get("key") if isinstance(key, dict) else None
            if isinstance(parts, list) and any(terms.in_constant(part, place) for part in parts):
                return True
        return False
    return _naming_holds(clause, terms)


def _params_hold(params: JsonValue, terms: Terms) -> bool:
    """Whether a result's canonical parameters hold a term. A clause among them (a predicate or
    a covariate, canonicalised as in phase 1, §7.6) holds one by ``_clause_holds``'s rules
    alone, its unit not known, so that a constant on a column that names no one is matched as
    ``Place.OTHER`` there and nowhere else; a string elsewhere holds one as a constant in
    ``Place.UNKNOWN`` does, while numbers outside clauses are an analysis's settings, never a
    unit's key."""
    if _clause_shaped(params):
        return _clause_holds(params, terms, None, True)
    if isinstance(params, dict):
        return any(
            terms.string(key) != key or _params_hold(member, terms)
            for key, member in params.items()
        )
    if isinstance(params, list):
        return any(_params_hold(item, terms) for item in params)
    return isinstance(params, str) and terms.in_constant(params, Place.UNKNOWN)


_COMBINATORS = ("all", "any", "not", "known", "unknown")
_TEXT = ("notes", "note", "drafted_by")


def _clause_shaped(value: JsonValue) -> bool:
    """Whether a value is a clause: an object with a ``kind``, or a combinator."""
    return isinstance(value, dict) and (
        isinstance(value.get("kind"), str) or any(key in value for key in _COMBINATORS)
    )


def _reference(value: JsonValue) -> str | None:
    """The parameter a ``"$name"`` string refers to (§7.1); ``None`` for any other value,
    ``"$$…"`` included."""
    if isinstance(value, str) and value.startswith("$") and not value.startswith("$$"):
        return value[1:]
    return None


class _Written:
    """An issuance's document as written, its parameters and its SQL, redacted by the rules
    ``_object_holds`` applies to constants (D290): each constant where its column or unit puts
    it (``Where``), and everything else (``notes``, structure, the SQL) as ``Place.UNKNOWN``,
    so that a number is erased only as a constant on a column, or a part of a unit key, that
    names the person's rows, never as ``aibi``, a count bound, a quantifier, an analysis's
    setting or a bound on another column. A cohort over another dataset than the one erased
    (its ``dataset``, or every one of its ``datasets``, or the document's) puts every constant
    of its clauses, and its pack leaves, in ``Place.OTHER``, as a tree over another dataset's
    release is; a view is over the cohorts it names (its ``cohorts`` and ``reference``), or,
    naming none, over every cohort of the document, and may be over the dataset when one of
    them may. A parameter is redacted as the places that refer to it are (``"$name"``): as a
    clause if it is one, and otherwise as a constant in each of those places, structure being
    ``Place.UNKNOWN``, as is a parameter nothing refers to. A pack leaf as written, whose
    columns the core cannot know, is a naming place of every table throughout. A name the
    document gives (a cohort's, a parameter's) is redacted as an object key is, and so is every
    reference to it (a view's ``cohorts`` and ``reference``, a ``cohort`` leaf, ``"$name"``), so
    that each still names what it named."""

    def __init__(self, terms: Terms, unit: str | None, dataset: str) -> None:
        self.terms = terms
        self.unit = unit
        self.dataset = dataset
        """The dataset erased, whose cohorts' columns and unit keys alone may name its rows."""
        self.default: JsonValue = None
        """The document's ``dataset``, that of every cohort that names none."""
        self.ours = True
        """Whether the cohort or view being redacted may be over ``dataset``."""
        self.over_cohorts: dict[str, bool] = {}
        """Each cohort, by its name as written, and whether it may be over ``dataset``."""
        self.cohort_names: dict[str, str] = {}
        """Each cohort's name, and the name it is redacted to."""
        self.param_names: dict[str, str] = {}
        """Each parameter's name, and the name it is redacted to."""
        self.used: dict[str, Where] = {}
        """Each parameter referred to, and the places that refer to it, joined."""
        self.structural: set[str] = set()
        """The parameters referred to where structure is (a cohort, a member of a clause that
        is no constant), which may stand for anything."""
        self.keys: dict[str, Where] = {}
        """The parameters referred to from an ``ids`` clause, as its list or a member of it,
        whose strings are unit keys as written (``"lib:2"``), and the places that refer to them,
        joined."""
        self.verbatim = False
        """Whether what is redacted is taken verbatim, as parameter values and SQL are, so that
        a string starting with ``$`` is never a reference (§7.1)."""

    def reference(self, value: JsonValue) -> str | None:
        return None if self.verbatim else _reference(value)

    def refer(self, name: str, place: Where) -> str:
        """Record a reference to the parameter in a place; the reference, redacted."""
        self.used[name] = self.used.get(name, NOWHERE) | place
        return "$" + self.param_names.get(name, self.terms.string(name))

    def place(self, column: JsonValue) -> Where:
        return _place(self.terms, column) if self.ours else _OTHER

    def unit_place(self) -> Where:
        return _unit_place(self.terms, self.unit) if self.ours else _OTHER

    def over(self, value: JsonValue) -> bool:
        """Whether a dataset reference as written may name the dataset erased: it does, or it
        is not known (a parameter, or nothing)."""
        return (
            not isinstance(value, str)
            or self.reference(value) is not None
            or value.split("@", 1)[0] == self.dataset
        )

    def cohort_over(self, cohort: Mapping[str, JsonValue]) -> bool:
        """Whether a cohort as written may be over the dataset erased: one of its ``datasets``
        may be, or its ``datasets`` is not a list (a ``"$name"`` parameter), or its ``dataset``,
        or, naming neither, the document's, may be."""
        if "datasets" in cohort:
            listed = cohort["datasets"]
            return not isinstance(listed, list) or any(self.over(item) for item in listed)
        return self.over(cohort.get("dataset", self.default))

    def view_over(self, view: JsonValue) -> bool:
        """Whether a view may be over the dataset erased: one of the cohorts it names (its
        ``cohorts`` and ``reference``) may be, or one it names is not known (a ``"$name"``
        parameter, or a name no cohort has), or, naming none, one of the document's cohorts
        may be, or the document's dataset when it has none."""
        if not isinstance(view, dict):
            return True
        names: list[JsonValue] = []
        cohorts = view.get("cohorts")
        if isinstance(cohorts, list):
            names.extend(cohorts)
        elif "cohorts" in view:
            return True
        if "reference" in view:
            names.append(view["reference"])
        if not names:
            if self.over_cohorts:
                return any(self.over_cohorts.values())
            return self.over(self.default)
        return any(not isinstance(name, str) or self.over_cohorts.get(name, True) for name in names)

    def document(self, written: JsonValue) -> JsonValue:
        if not isinstance(written, dict):
            return self.pack(written)
        self.default = written.get("dataset")
        params, cohorts = written.get("params"), written.get("cohorts")
        if isinstance(params, dict):
            self.param_names = self.terms.renamed(params)
        if isinstance(cohorts, dict):
            self.cohort_names = self.terms.renamed(cohorts)
            self.over_cohorts = {
                name: not isinstance(cohort, dict) or self.cohort_over(cohort)
                for name, cohort in cohorts.items()
            }
        found: dict[str, JsonValue] = {}
        for key, member in written.items():
            if key == "aibi" or key == "params":
                continue
            if key in _TEXT:
                found[key] = self.text(member)
            elif key == "cohorts" and isinstance(member, dict):
                found[key] = {
                    self.cohort_names[name]: self.cohort(cohort) for name, cohort in member.items()
                }
            elif key == "views" and isinstance(member, list):
                found[key] = [self.view(view) for view in member]
            else:
                found[key] = self.plain(member)
        if "aibi" in written:
            found["aibi"] = written["aibi"]
        if "params" in written:
            found["params"] = self.parameters(written["params"])
        return found

    def parameters(self, params: JsonValue) -> JsonValue:
        """Parameters by name, redacted as the places that refer to them are; call it after
        ``document``, which finds those places."""
        self.verbatim = True
        try:
            if not isinstance(params, dict):
                return self.pack(params)
            return self.terms.keyed(params, self.parameter)
        finally:
            self.verbatim = False

    def parameter(self, name: str, value: JsonValue) -> JsonValue:
        if name in self.keys:
            place = self.keys[name]
            value = (
                [self.key(item, place) for item in value]
                if isinstance(value, list)
                else self.key(value, place)
            )
        if _clause_shaped(value) or (
            isinstance(value, list) and value and all(_clause_shaped(item) for item in value)
        ):
            return self.clause(value)
        scalars = value if isinstance(value, list) else [value]
        if name in self.structural and any(isinstance(item, dict | list) for item in scalars):
            return self.pack(value)
        return self.constants(value, self.used.get(name, _UNKNOWN))

    def sql(self, sql: JsonValue) -> JsonValue:
        """The SQL as run: its text and its parameters' strings as ``Place.UNKNOWN``; its
        numbers are the derivation's constants, which held no term, or its structure."""
        self.verbatim = True
        try:
            return self.plain(sql)
        finally:
            self.verbatim = False

    def name(self, value: JsonValue) -> JsonValue:
        """A reference to a cohort by its name, redacted as the name is."""
        if not isinstance(value, str) or self.reference(value) is not None:
            return self.plain(value)
        return self.cohort_names.get(value, self.terms.string(value))

    def cohort(self, cohort: JsonValue) -> JsonValue:
        if not isinstance(cohort, dict):
            return self.pack(cohort)
        self.ours = self.cohort_over(cohort)
        try:
            return {
                key: self.clause(member)
                if key == "all"
                else self.text(member)
                if key in _TEXT
                else self.plain(member)
                for key, member in cohort.items()
            }
        finally:
            self.ours = True

    def view(self, view: JsonValue) -> JsonValue:
        if not isinstance(view, dict):
            return self.pack(view)
        self.ours = self.view_over(view)
        try:
            found: dict[str, JsonValue] = {}
            for key, member in view.items():
                if key == "params":
                    found[key] = self.settings(member)
                elif key in _TEXT:
                    found[key] = self.text(member)
                elif key == "cohorts" and isinstance(member, list):
                    found[key] = [self.name(item) for item in member]
                elif key == "reference":
                    found[key] = self.name(member)
                else:
                    found[key] = self.plain(member)
            return found
        finally:
            self.ours = True

    def settings(self, value: JsonValue) -> JsonValue:
        """A view's parameters: clauses as clauses, anything else an analysis's settings."""
        if _clause_shaped(value):
            return self.clause(value)
        if isinstance(value, list):
            return [self.settings(item) for item in value]
        if isinstance(value, dict):
            return self.terms.keyed(value, lambda _, member: self.settings(member))
        return self.plain(value)

    def clause(self, clause: JsonValue) -> JsonValue:
        if isinstance(clause, list):
            return [self.clause(member) for member in clause]
        if not isinstance(clause, dict):
            name = self.reference(clause)
            if name is not None:
                return self.refer(name, _NAMING if self.ours else _OTHER)
            return self.plain(clause)
        if any(combinator in clause for combinator in _COMBINATORS):
            return {
                key: self.clause(member) if key in _COMBINATORS else self.plain(member)
                for key, member in clause.items()
            }
        kind = clause.get("kind")
        if kind == "value":
            place = self.place(clause.get("column"))
            return {
                key: self.constants(member, place)
                if key in ("values", "value", "range")
                else self.plain(member)
                for key, member in clause.items()
            }
        if kind == "exists":
            return {
                key: self.clause(member) if key == "where" else self.plain(member)
                for key, member in clause.items()
            }
        if kind == "covered":
            table = clause.get("table")
            return {
                key: self.scope(member, table) if key == "scope" else self.plain(member)
                for key, member in clause.items()
            }
        if kind == "ids":
            return {
                key: self.ids(member) if key == "ids" else self.plain(member)
                for key, member in clause.items()
            }
        if kind == "cohort":
            return {
                key: self.name(member) if key == "cohort" else self.plain(member)
                for key, member in clause.items()
            }
        return self.pack(clause)

    def scope(self, scope: JsonValue, table: JsonValue) -> JsonValue:
        """A ``covered`` clause's scope, each column's values where the column of ``table`` it
        names puts them (``_scope_place``)."""
        if not isinstance(scope, dict):
            return self.pack(scope)

        def values(column: str, given: JsonValue) -> JsonValue:
            place = _scope_place(self.terms, table, column) if self.ours else _OTHER
            return self.constants(given, place)

        return self.terms.keyed(scope, values)

    def ids(self, ids: JsonValue) -> JsonValue:
        """An ``ids`` clause's keys (``key``); a parameter that stands for the list is
        redacted as it is."""
        place = self.unit_place()
        if isinstance(ids, list):
            return [self.key(member, place) for member in ids]
        name = self.reference(ids)
        if name is not None:
            self.keyed_by(name, place)
        return self.constants(ids, place)

    def keyed_by(self, name: str, place: Where) -> None:
        self.keys[name] = self.keys.get(name, NOWHERE) | place

    def key(self, member: JsonValue, place: Where) -> JsonValue:
        """A unit key as written (``"<dataset>:<key>"``, split at the first ``:``, or
        ``{dataset, key}``), its key's parts by ``Terms.constant`` in the unit's ``place``; a
        parameter that stands for it is redacted as it is."""
        name = self.reference(member)
        if name is not None:
            self.keyed_by(name, place)
        elif isinstance(member, str) and ":" in member:
            dataset, key = member.split(":", 1)
            return f"{self.plain(dataset)}:{self.terms.constant(key, place)}"
        elif isinstance(member, dict):
            return {
                key: self.constants(part, place) if key == "key" else self.plain(part)
                for key, part in member.items()
            }
        return self.constants(member, place)

    def constants(self, value: JsonValue, place: Where) -> JsonValue:
        """Constants (and the lists and ranges of them), by ``Terms.constant``'s rules."""
        if isinstance(value, list):
            return [self.constants(item, place) for item in value]
        if isinstance(value, dict):
            return self.terms.keyed(value, lambda _, member: self.constants(member, place))
        name = self.reference(value)
        if name is not None:
            return self.refer(name, place)
        return self.terms.constant(value, place)

    def plain(self, value: JsonValue) -> JsonValue:
        """Structure and text: strings as ``Place.UNKNOWN``, numbers as they are."""
        if isinstance(value, list):
            return [self.plain(item) for item in value]
        if isinstance(value, dict):
            return self.terms.keyed(value, lambda _, member: self.plain(member))
        name = self.reference(value)
        if name is not None:
            self.structural.add(name)
            return self.refer(name, _UNKNOWN)
        return self.terms.constant(value, _UNKNOWN)

    def text(self, value: JsonValue) -> JsonValue:
        """Plain text, never substituted (``notes``, ``note``, ``drafted_by``), as free text."""
        if isinstance(value, str):
            return self.terms.constant(value, Place.UNKNOWN)
        return self.pack(value)

    def pack(self, value: JsonValue) -> JsonValue:
        """What the core cannot read (a pack leaf as written): every constant in a naming place
        of every table, JSON numbers and booleans too, and every parameter it refers to
        redacted as one; in a cohort over another dataset, in ``Place.OTHER``."""
        place = _NAMING if self.ours else _OTHER
        if isinstance(value, list):
            return [self.pack(item) for item in value]
        if isinstance(value, dict):
            return self.terms.keyed(value, lambda _, member: self.pack(member))
        name = self.reference(value)
        if name is not None:
            return self.refer(name, place)
        return self.terms.constant(value, place)


ISSUANCE_BATCH = 256
"""Issuances an erasure reads at a time: the log is never read whole into memory."""


def _naming_issuances(
    db: sqlite3.Connection, dataset: str
) -> Iterator[tuple[str, str, str, str | None, int]]:
    """The issuances that may name the dataset, in id order, ``ISSUANCE_BATCH`` at a time: those
    of a derivation over one of its releases, and those whose document as written or
    parameters hold the dataset id as the start of a JSON string (``_names_dataset`` decides),
    each with whether it is of the first kind (D290)."""
    after = ""
    quoted = json.dumps(dataset)[:-1]
    while True:
        rows = cast(
            list[tuple[str, str, str, str | None, int]],
            db.execute(
                "SELECT * FROM (SELECT i.id, i.written, i.params, i.sql, EXISTS ("
                "SELECT 1 FROM derivation_releases r"
                " WHERE r.derivation = i.derivation AND r.dataset = ?) AS ours"
                " FROM issuances i WHERE i.id > ?)"
                " WHERE ours OR instr(written, ?) > 0 OR instr(params, ?) > 0"
                " ORDER BY id LIMIT ?",
                (dataset, after, quoted, quoted, ISSUANCE_BATCH),
            ).fetchall(),
        )
        yield from rows
        if len(rows) < ISSUANCE_BATCH:
            return
        after = rows[-1][0]


def _issuances(db: sqlite3.Connection, dataset: str, terms: Terms) -> int:
    """Redact the document, parameters and SQL of every issuance that names the dataset,
    through its derivation or in its document, by ``_Written``'s rules; how many changed."""
    changed = 0
    for identifier, written, params, sql, ours in _naming_issuances(db, dataset):
        document, parameters = loads(written), loads(params)
        if not ours and not _names_dataset(document, parameters, dataset):
            continue
        unit = document.get("unit") if isinstance(document, dict) else None
        table = unit if isinstance(unit, str) and _reference(unit) is None else None
        redactor = _Written(terms, table, dataset)
        new = (
            text_of(redactor.document(document)),
            text_of(redactor.parameters(parameters)),
            None if sql is None else text_of(redactor.sql(loads(sql))),
        )
        if new != (written, params, sql):
            db.execute(
                "UPDATE issuances SET written = ?, params = ?, sql = ? WHERE id = ?",
                (*new, identifier),
            )
            changed += 1
    return changed


REDACTORS: dict[str, Redactor] = {
    "audit": _audit,
    "proposals": _proposals,
    "catalog": _catalog,
    "derivations": _derivations,
    "issuances": _issuances,
}


def register(table: str, redactor: Redactor) -> None:
    """Add the redactor of a table a later milestone adds to the app DB."""
    if table in REDACTORS:
        raise ValueError(f"{table} already has a redactor")
    REDACTORS[table] = redactor


def redact(db: sqlite3.Connection, dataset: str, terms: Terms) -> dict[str, int]:
    """Run every redactor in one transaction's connection; rows changed per table. The
    redaction's permit, which the derivation log's triggers ask of every change to it but
    pruning's (D289), is written first and removed last, so it is never seen outside the
    transaction. Raises ``RuntimeError`` outside a transaction, where the permit would be
    committed at once and outlive a redactor that raises. However it ends, ``re``'s cache of
    compiled patterns is emptied, so that no pattern a redactor compiled outlives it."""
    if not db.in_transaction:
        raise RuntimeError("a redaction runs within a transaction")
    try:
        db.execute("INSERT OR REPLACE INTO log_permits (kind) VALUES ('redaction')")
        changed = {table: redactor(db, dataset, terms) for table, redactor in REDACTORS.items()}
        db.execute("DELETE FROM log_permits WHERE kind = 'redaction'")
        return changed
    finally:
        re.purge()


__all__ = [
    "ERASE_ACTION",
    "MARK",
    "REDACTORS",
    "RELEASE_ACTIONS",
    "RELEASE_REFERENCES",
    "Place",
    "Redactor",
    "Terms",
    "redact",
    "register",
]
