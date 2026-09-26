"""Redacting a person's keys and values from the app DB (SPEC §12.2, Erasure; D223).

A term is a value's canonical string (§12.2). The person's terms are their row's key and its
values in identifier columns (§5.4); the terms below them are the keys of the rows below theirs,
and those rows' values in identifier columns. Redaction writes ``[erased]`` in place of:

- a JSON string or number whose canonical string is a term (a number by its RFC 8785 form),
  and an object key or a proposal's evidence that is a term;
- in free text (evidence, and JSON strings and keys that hold more than a term), a term as a
  whole token: not preceded or followed by a letter, a digit or a mark (Unicode category
  ``M``), in any script, so ``m-17`` is erased from ``member m-17`` but not from ``m-170``, nor
  ``Jos`` from ``José``, nor ``क`` from ``कि``. Every term of the person is, numeric or not (a
  membership number ``104233``); a term below them written as a number (such as a surrogate key
  ``2``) is erased only as a whole value, never from ``page 2``. Terms match exactly, case
  included: keys are exact.

Two object keys that both become ``[erased]`` are both kept, the second as ``[erased] (2)``.

JSON Pointers are never redacted: they name places in descriptors, not people. Neither are the
labels, manifest hashes, descriptor ids, pointers and proposal ids the store writes into its own
lifecycle entries (imports, publishes, sessions, withdrawals, rejections, D252), nor an erasure's
own entry, which holds its table, its mode and counts only.

Each redactor redacts one table of the app DB, for one dataset. M1 has the audit trail, the
proposal queue and the catalogue index, whose entry for the dataset is deleted; later
milestones register theirs (saved documents, cached results, issuances and derivations) with
``register``.
"""

import json
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Collection, Mapping
from functools import cache
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.jsonio import canonical, number_text
from aibi.core.store.appdb import loads

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

_NUMERIC = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


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


class Terms:
    """The values to erase: the person's and those below them, as whole JSON values, and in free
    text as whole tokens, but for the numbers below them."""

    def __init__(self, person: Collection[str], below: Collection[str] = ()) -> None:
        self.person = frozenset(term for term in person if term)
        self.below = frozenset(term for term in below if term) - self.person
        self.terms = self.person | self.below
        in_text = [*self.person, *(term for term in self.below if not _NUMERIC.fullmatch(term))]
        ordered = sorted(in_text, key=lambda term: (-len(term), term))
        alternatives = "|".join(re.escape(term) for term in ordered)
        self.pattern = (
            re.compile(rf"(?<!{_word()})(?:{alternatives})(?!{_word()})") if ordered else None
        )

    def __bool__(self) -> bool:
        return bool(self.terms)

    def __len__(self) -> int:
        return len(self.terms)

    def dumps(self) -> str:
        """The terms as the app DB holds a waiting redaction's."""
        return json.dumps({"below": sorted(self.below), "person": sorted(self.person)})

    @classmethod
    def loads(cls, text: str) -> "Terms":
        stored = cast(dict[str, list[str]], json.loads(text))
        return cls(stored["person"], stored["below"])

    def text(self, value: str) -> str:
        """Free text, with every whole token of a term erased, but for the numbers below the
        person."""
        return self.pattern.sub(MARK, value) if self.pattern is not None else value

    def string(self, value: str) -> str:
        """A JSON string or key: erased whole if it is a term, else as free text."""
        return MARK if value in self.terms else self.text(value)

    def json(self, value: JsonValue, keep: Collection[str] = ()) -> JsonValue:
        """A JSON value, redacted; an object's members named in ``keep`` are left as they are
        (at the top level only)."""
        if isinstance(value, str):
            return self.string(value)
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int | float):
            return MARK if number_text(value) in self.terms else value
        if isinstance(value, list):
            return [self.json(item) for item in value]
        return self._object(value, keep)

    def _object(self, value: Mapping[str, JsonValue], keep: Collection[str]) -> JsonValue:
        found: dict[str, JsonValue] = {}
        redacted: list[tuple[str, JsonValue]] = []
        for key, member in value.items():
            if key in keep:
                found[key] = member
                continue
            name = self.string(key)
            if name == key:
                found[key] = self.json(member)
            else:
                redacted.append((name, self.json(member)))
        for name, member in redacted:
            unique, count = name, 1
            while unique in found:
                count += 1
                unique = f"{name} ({count})"
            found[unique] = member
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


REDACTORS: dict[str, Redactor] = {"audit": _audit, "proposals": _proposals, "catalog": _catalog}


def register(table: str, redactor: Redactor) -> None:
    """Add the redactor of a table a later milestone adds to the app DB."""
    if table in REDACTORS:
        raise ValueError(f"{table} already has a redactor")
    REDACTORS[table] = redactor


def redact(db: sqlite3.Connection, dataset: str, terms: Terms) -> dict[str, int]:
    """Run every redactor in one transaction's connection; rows changed per table."""
    return {table: redactor(db, dataset, terms) for table, redactor in REDACTORS.items()}


__all__ = [
    "ERASE_ACTION",
    "MARK",
    "REDACTORS",
    "RELEASE_ACTIONS",
    "RELEASE_REFERENCES",
    "Redactor",
    "Terms",
    "redact",
    "register",
]
