"""Honouring an erasure request (SPEC §12.2, Erasure; D223).

The first step is a re-import from a source without the person's rows, since a curation session
cannot remove rows (#10). ``erase`` does the rest, for the parts of the app DB that exist so far.

**The person's rows** in a release are their row, found by its key typed by the key columns'
datatypes, and the rows below it: those whose foreign key holds the key of a row of theirs that
the same release holds, through every relationship except those into the person's own table or
into a table whose role is ``entity`` (other people: a member another referred, say), and so on
down. The relationships are those that any published release declares, the same tables and
columns counting once, each followed in every release whose child table has its columns: the
gate refuses a dangling foreign key under a declared relationship (D230) but drops a proposed
one, and the importer proposes none that no longer holds, so a re-import may keep a loan of the
person's without the relationship that makes it theirs. A key of a row of theirs in another
release counts only for an orphan: a row whose foreign key names no row of this release (that
loan, left behind when a re-import dropped the member). So a re-import that numbers its rows
afresh, and gives loan 2 to someone else, does not make that loan the person's. Keys are
compared across releases by their values' canonical strings (§12.2), since a re-import may type
a key column otherwise (loan 2 as the integer 2, then as the string ``2``). The person's own
key is matched in every release. A release holds the person when it holds one of their rows, or
a row whose foreign key names one of them, in the same way, through any relationship, since
that row names the person too.

1. Under the store's lock, so that no session opens and no release is withdrawn between its
   checks and its withdrawals, it checks that no curation session is open on the dataset, since
   its draft may hold the person, and that the latest published release does not hold the person
   (refused with ``ERASURE_BLOCKED`` otherwise). The whole erasure holds the dataset's operation
   slot, so no import, re-import, withdrawal or session operation runs alongside (D236). A key
   that is not of the key columns' datatypes, or that no published release holds, is refused
   (``INVALID_KEY``), unless ``redact_only`` is given (below).
2. From every other published release that holds the person it collects the terms: the keys of
   the person's rows, and their values in identifier columns (§5.4), each item of a list, as
   canonical strings, but not their foreign keys to rows not theirs (a book they borrowed).
   The person's own row gives the person's terms; the rows below give the terms below them
   (``redaction.Terms``); each term is kept with the tables of the rows that gave it, and each
   column whose values name their rows with the tables whose rows it names (D290).
3. In one transaction it withdraws every such release, so that the sweep deletes its blobs
   except its manifest and what a live release shares with it, writes its audit entry, and
   records the redaction and, given ``uploads``, that the upload area is still to be deleted.
4. It asks ``uploads`` to delete the dataset's source files from the upload area (#9).
5. It redacts the terms from the dataset's rows in the app DB, now or, while a query or an
   operation still pins a withdrawn release, once it finishes.

A person whom only releases withdrawn already held (withdrawn at once, before the request, to
stop them being read) is in no release ``erase`` can read. With ``redact_only`` the operator
says so, and is trusted: the key, typed by the latest published release that has the table (or,
if none has it, as given), is then the only term, and steps 3 to 5 run with no release to
withdraw. It is refused unless the dataset has a withdrawn release, and only then does a refused
key suggest it.

What the transaction records is done whatever fails after it: the redaction runs when a pin is
released or the store opens, and erasing again with the same key while it still waits finishes
steps 4 and 5. Once it has run, the key is nowhere to recognise it by, so an upload area left to
delete is deleted by erasing with ``redact_only``; until then ``Erased.uploads_pending`` says so.

The audit trail records the erasure without the key, which would otherwise need erasing too:
its entry holds the table, the labels withdrawn, the mode and the number of terms, and
redaction leaves it as it is.

The published releases are loaded, their blobs verified, and the person's rows found, before
the store's lock is taken, since a query takes that lock to pin a release. Under the lock the
published releases are read again, and if they changed meanwhile the rows are found again,
loading any release published since: every decision is made on the releases published while
the lock is held. Finding the rows is linear in the releases' rows.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.engine.data import PRESENT, KeyPart, Release, key_part
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.cells import ColumnCells
from aibi.core.store.redaction import ERASE_ACTION, Terms
from aibi.core.store.sources import SourceValue, canonical_string
from aibi.core.store.store import Store, StoreRefused

Key = tuple[KeyPart, ...]
Canonical = tuple[str, ...]
"""A key as its values' canonical strings, compared across releases."""
Row = tuple[str, int]


def _canonical(key: Key) -> Canonical:
    return tuple(canonical_string(value) or "" for _, value in key)


@dataclass(frozen=True)
class Erased:
    withdrawn: tuple[int, ...]
    """The labels withdrawn."""
    terms: int
    """How many distinct terms were redacted."""
    redacted: bool
    """Whether the app DB was redacted and its WAL checkpointed now; otherwise that happens once
    no pin blocks the redaction and no reader the checkpoint."""
    uploads_pending: bool = False
    """Whether the dataset's upload area is still to be deleted, an erasure's ``uploads`` having
    failed: erasing again with ``uploads`` deletes it (with ``redact_only`` once the redaction
    has run)."""


def erase(
    store: Store,
    dataset: str,
    table: str,
    key: Sequence[SourceValue],
    by: str,
    *,
    uploads: Callable[[str], None] | None = None,
    redact_only: bool = False,
) -> Erased:
    """Erase the person whose row in ``table`` has ``key`` (its primary key's values, in the
    key's order, typed by its columns' datatypes). With ``redact_only``, a key that no live
    release holds is redacted from the app DB alone, rather than refused, if the dataset has a
    withdrawn release. Raises ``StoreRefused``."""
    with store.exclusive(dataset, "erase"):
        return _erase(store, dataset, table, key, by, uploads=uploads, redact_only=redact_only)


def _erase(
    store: Store,
    dataset: str,
    table: str,
    key: Sequence[SourceValue],
    by: str,
    *,
    uploads: Callable[[str], None] | None,
    redact_only: bool,
) -> Erased:
    person = _Person(table, _live(store, dataset), key)
    with store.lock:
        if any(session.open for session in store.db.sessions(dataset)):
            raise StoreRefused(
                RefusalCode.ERASURE_BLOCKED,
                "A curation session is open on the dataset: its draft may hold the row; end it "
                "first",
            )
        latest = store.latest(dataset)
        if latest is None:
            raise StoreRefused(RefusalCode.UNKNOWN_RELEASE, "The dataset has no release")
        manifests = _published(store, dataset)
        if manifests != [*person.releases]:
            loaded = {
                m: person.releases[m] if m in person.releases else store.load(m) for m in manifests
            }
            person = _Person(table, loaded, key)
        if person.holds(latest.manifest):
            raise StoreRefused(
                RefusalCode.ERASURE_BLOCKED,
                "The latest release still holds the row, a row below it, or a row whose foreign "
                "key names one of them: re-import from a source without them first",
            )
        holding = [manifest for manifest in manifests if person.holds(manifest)]
        if holding:
            terms, mode = person.terms(), "withdraw"
        else:
            waiting = _waiting(store, dataset, person)
            if waiting:
                return _again(store, dataset, waiting, uploads)
            withdrawn_before = any(label.withdrawn for label in store.labels(dataset))
            if not redact_only or not withdrawn_before:
                reason = person.unheld(store, dataset, withdrawn_before, redact_only)
                raise StoreRefused(RefusalCode.INVALID_KEY, reason)
            terms, mode = _redact_only(store, dataset, person), "redact_only"
        withdrawn: list[int] = []
        with store.db.transaction() as db:
            for manifest in holding:
                withdrawn.extend(store.record_withdrawal(db, dataset, manifest, by))
            detail: JsonValue = {
                "mode": mode,
                "table": table,
                "withdrawn": [*sorted(withdrawn)],
                "terms": len(terms),
            }
            store.db.audit(db, store.now(), dataset, by, ERASE_ACTION, detail)
            request = store.request_redaction(db, dataset, terms, holding)
            if uploads is not None:
                store.db.add_pending_upload(db, dataset)
        store.withdrawn(holding)
    _delete_uploads(store, dataset, uploads)
    store.run_pending_redactions()
    return Erased(
        tuple(sorted(withdrawn)),
        len(terms),
        store.redacted([request]),
        store.db.upload_pending(dataset),
    )


def _published(store: Store, dataset: str) -> list[str]:
    return sorted({label.manifest for label in store.labels(dataset) if not label.withdrawn})


def _live(store: Store, dataset: str) -> dict[str, Release]:
    """The dataset's published releases, loaded without the store's lock; one withdrawn and swept
    meanwhile is left out. No pin is needed: a blob is whole or missing, and one that is missing
    was a withdrawn release's, since the sweep deletes no live release's blobs. (A pin's release
    would run the redaction an erasure again is to recognise.)"""
    loaded: dict[str, Release] = {}
    for manifest in _published(store, dataset):
        try:
            loaded[manifest] = store.load(manifest)
        except MissingBlobError:
            continue
    return loaded


def _waiting(store: Store, dataset: str, person: "_Person") -> dict[int, Terms]:
    """The dataset's waiting redactions whose person terms hold the key: the erasures that erasing
    the key again finishes."""
    wanted = person.key_terms()
    return {
        request: terms
        for request, terms in store.pending_redactions(dataset).items()
        if wanted and wanted <= terms.person
    }


def _again(
    store: Store, dataset: str, waiting: Mapping[int, Terms], uploads: Callable[[str], None] | None
) -> Erased:
    """Finish an erasure whose transaction committed and whose redaction still waits."""
    _delete_uploads(store, dataset, uploads)
    store.run_pending_redactions()
    count = max(len(terms) for terms in waiting.values())
    return Erased((), count, store.redacted(list(waiting)), store.db.upload_pending(dataset))


def _redact_only(store: Store, dataset: str, person: "_Person") -> Terms:
    """The key alone, as the person's terms: typed by the latest published release that has the
    person's table, or, if none has it, the given values' canonical strings (as ``key_terms``
    takes them when no release types the key)."""
    for label in reversed(store.labels(dataset)):
        release = person.releases.get(label.manifest)
        if label.withdrawn or release is None or release.table(person.table) is None:
            continue
        typed = person.keys[label.manifest]
        if typed is None:
            raise StoreRefused(
                RefusalCode.INVALID_KEY,
                f"The latest release with a table {person.table} has no key of those values' "
                "number and datatypes",
            )
        values: Sequence[SourceValue] = [value for _, value in typed]
        break
    else:
        values = person.given
    texts = [(text, value) for value in values if (text := canonical_string(value))]
    terms = Terms(
        [text for text, _ in texts],
        numbers=[text for text, value in texts if _number(value)],
    )
    if not terms:
        raise StoreRefused(RefusalCode.INVALID_KEY, "The key has no value to redact")
    return terms


def _delete_uploads(store: Store, dataset: str, uploads: Callable[[str], None] | None) -> None:
    if uploads is not None:
        uploads(dataset)
        store.db.uploads_deleted(dataset)


@dataclass(frozen=True, order=True)
class _Link:
    """A relationship's tables and columns, as some published release declares it: the same
    relationship whatever its id, and whichever release declares it."""

    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]

    def applies(self, release: Release) -> bool:
        """Whether the release's child table has the relationship's columns."""
        return release.table(self.child) is not None and set(self.child_columns) <= set(
            release.columns(self.child)
        )


def _links(releases: Mapping[str, Release]) -> tuple[_Link, ...]:
    """The relationships every published release declares, each once, its column pairs in
    order of the child's columns."""
    found: set[_Link] = set()
    for release in releases.values():
        for relationship in release.relationships:
            fields = relationship.fields
            pairs = sorted(zip(fields.child_columns, fields.parent_columns, strict=True))
            found.add(
                _Link(
                    fields.child_table,
                    tuple(child for child, _ in pairs),
                    fields.parent_table,
                    tuple(parent for _, parent in pairs),
                )
            )
    return tuple(sorted(found))


@dataclass(frozen=True)
class _Index:
    """One relationship's rows in one release: each child's parent (``None`` for an orphan or a
    null key), each parent's children, and the orphans by their foreign key, canonical."""

    parent: tuple[int | None, ...]
    children: Mapping[int, tuple[int, ...]]
    orphans: Mapping[Canonical, tuple[int, ...]]


def _index(release: Release, link: _Link) -> _Index:
    parents: dict[Key, int] = {}
    for row in range(len(release.rows(link.parent))):
        key = release.key(link.parent, row, link.parent_columns)
        if key is not None:
            parents.setdefault(key, row)
    found: list[int | None] = []
    children: dict[int, list[int]] = {}
    orphans: dict[Canonical, list[int]] = {}
    for row in range(len(release.rows(link.child))):
        key = release.key(link.child, row, link.child_columns)
        parent = None if key is None else parents.get(key)
        found.append(parent)
        if parent is not None:
            children.setdefault(parent, []).append(row)
        elif key is not None:
            orphans.setdefault(_canonical(key), []).append(row)
    return _Index(
        tuple(found),
        {p: tuple(rows) for p, rows in children.items()},
        {k: tuple(rows) for k, rows in orphans.items()},
    )


@dataclass
class _Person:
    """The person's rows in each release (see the module's docstring)."""

    table: str
    releases: Mapping[str, Release]
    given: Sequence[SourceValue]
    keys: dict[str, Key | None] = field(init=False)
    """The key, typed by each release's datatypes; ``None`` where it is not of them."""
    links: dict[str, dict[_Link, _Index]] = field(init=False)
    """By release: the relationships of every published release whose child table it has with
    their columns, each with its rows there."""
    rows: dict[str, set[Row]] = field(init=False)
    """By release: the person's rows (the person's row being the one in ``table``)."""
    known: dict[_Link, set[Canonical]] = field(init=False)
    """By relationship: the keys its parent columns have in the person's rows, in any release."""

    def __post_init__(self) -> None:
        self.keys = {
            m: _typed(release, self.table, self.given) for m, release in self.releases.items()
        }
        every = _links(self.releases)
        self.links = {
            m: {link: _index(release, link) for link in every if link.applies(release)}
            for m, release in self.releases.items()
        }
        self.rows = {manifest: set() for manifest in self.releases}
        self.known = {}
        found: list[tuple[str, Row]] = []
        for manifest, release in self.releases.items():
            key, columns = self.keys[manifest], release.primary_key(self.table)
            if key is None or columns is None:
                continue
            found.extend(
                (manifest, (self.table, row))
                for row in range(len(release.rows(self.table)))
                if release.key(self.table, row, columns) == key
            )
            for link in self.links[manifest]:
                own = self._own_key(manifest, link)
                if own is not None and self._descends(release, link):
                    found.extend(self._orphaned(manifest, link, own))
        while found:
            manifest, row = found.pop()
            if row not in self.rows[manifest]:
                self.rows[manifest].add(row)
                found.extend(self._below(manifest, row))

    def _below(self, manifest: str, row: Row) -> list[tuple[str, Row]]:
        """The rows a row of the person's reaches: its children in its own release, and the
        orphans in every release whose foreign key holds one of its keys that is new."""
        release, (current, index) = self.releases[manifest], row
        reached: list[tuple[str, Row]] = []
        for link, rows in self.links[manifest].items():
            if link.parent != current:
                continue
            if self._descends(release, link):
                reached.extend(
                    (manifest, (link.child, child)) for child in rows.children.get(index, ())
                )
            typed = release.key(current, index, link.parent_columns)
            known = self.known.setdefault(link, set())
            value = None if typed is None else _canonical(typed)
            if value is None or value in known:
                continue
            known.add(value)
            for other, elsewhere in self.releases.items():
                if link in self.links[other] and self._descends(elsewhere, link):
                    reached.extend(self._orphaned(other, link, value))
        return reached

    def _orphaned(self, manifest: str, link: _Link, value: Canonical) -> list[tuple[str, Row]]:
        rows = self.links[manifest][link].orphans.get(value, ())
        return [(manifest, (link.child, row)) for row in rows]

    def _named(self, manifest: str, link: _Link, value: Canonical) -> bool:
        """Whether an orphan's foreign key through the relationship names the person: a key of
        the person's rows in any release, or the person's own key."""
        return value in self.known.get(link, ()) or value == self._own_key(manifest, link)

    def holds(self, manifest: str) -> bool:
        """Whether the release holds one of the person's rows, or a row whose foreign key names
        one of them through any relationship (another member's ``referred_by``, say): a child
        of a row of theirs, which is a row of theirs or names one, or an orphan."""
        if self.rows[manifest]:
            return True
        for link, rows in self.links[manifest].items():
            if any(self._named(manifest, link, value) for value in rows.orphans):
                return True
        return False

    def _names(self, manifest: str, link: _Link, row: int) -> bool:
        """Whether a row's foreign key through the relationship names one of the person's rows:
        its parent in the release, or, for an orphan, a key of theirs."""
        release = self.releases[manifest]
        parent = self.links[manifest][link].parent[row]
        if parent is not None:
            return (link.parent, parent) in self.rows[manifest]
        value = release.key(link.child, row, link.child_columns)
        return value is not None and self._named(manifest, link, _canonical(value))

    def _own_key(self, manifest: str, link: _Link) -> Canonical | None:
        """The person's key as a foreign key through the relationship holds it, if the
        relationship is into the person's key in this release: its columns in the relationship's
        order, since a relationship leads to its parent's key in any order (§5.6)."""
        release, key = self.releases[manifest], self.keys[manifest]
        columns = release.primary_key(self.table)
        if key is None or columns is None or link.parent != self.table:
            return None
        if sorted(columns) != sorted(link.parent_columns):
            return None
        parts = dict(zip(columns, key, strict=True))
        return _canonical(tuple(parts[column] for column in link.parent_columns))

    def _descends(self, release: Release, link: _Link) -> bool:
        """Whether the person's rows go on through the relationship: not into the person's own
        table, nor into another table of things (other people)."""
        descriptor = release.table(link.child)
        return (
            link.child != self.table
            and descriptor is not None
            and descriptor.fields.role != "entity"
        )

    def terms(self) -> Terms:
        """The person's terms and those below them, each with the tables whose rows of theirs
        hold it, which are the tables that hold them (D290)."""
        person: set[str] = set(self.key_terms())
        below: set[str] = set()
        numbers: set[str] = set()
        text: set[str] = set()
        origins: dict[str, set[str]] = {term: {self.table} for term in person}
        for manifest in self.releases:
            for current, index in sorted(self.rows[manifest]):
                found = self._identifying(manifest, current, index)
                (person if current == self.table else below).update(found)
                for term, number in found.items():
                    (numbers if number else text).add(term)
                    origins.setdefault(term, set()).add(current)
        holding = {self.table} | {table for rows in self.rows.values() for table, _ in rows}
        return Terms(
            person,
            below,
            numbers=numbers - text,
            tables=holding,
            columns=self._naming(holding),
            origins=origins,
        )

    def _naming(self, holding: set[str]) -> dict[str, frozenset[str]]:
        """The columns, by descriptor id, whose values name the person's rows, each with the
        tables whose rows its values name (D290): the key and identifier columns of the tables
        that hold them, but for foreign keys to rows of other tables, their own table's, and
        every foreign key into those tables, the table it points into."""
        found: dict[str, set[str]] = {}
        for manifest, release in self.releases.items():
            links = self.links[manifest]
            for table in holding:
                columns = set(release.primary_key(table) or ())
                for column in release.columns(table):
                    descriptor = release.column(table, column)
                    if descriptor is not None and descriptor.fields.identifier:
                        columns.add(column)
                for link in links:
                    if link.child == table and link.parent not in holding:
                        columns.difference_update(link.child_columns)
                for column in columns:
                    found.setdefault(f"{table}.{column}", set()).add(table)
            for link in links:
                if link.parent in holding:
                    for column in link.child_columns:
                        found.setdefault(f"{link.child}.{column}", set()).add(link.parent)
        return {column: frozenset(tables) for column, tables in found.items()}

    def key_terms(self) -> frozenset[str]:
        """The canonical strings of the key's values, as the releases that type it type them,
        or as given if none does."""
        typed = [key for key in self.keys.values() if key is not None]
        values = [value for key in typed for _, value in key] if typed else self.given
        return frozenset(text for value in values if (text := canonical_string(value)))

    def _identifying(self, manifest: str, table: str, row: int) -> dict[str, bool]:
        """A row's key and its values in identifier columns, each PRESENT item of a list, except
        its foreign keys to rows that are not the person's (a book they borrowed, say, even when
        it is part of a link table's key), each with whether it is a number by its column's
        datatype (D290). A foreign key to a row of theirs holds that row's key, a term
        already."""
        release = self.releases[manifest]
        columns = set(release.primary_key(table) or ())
        for column in release.columns(table):
            descriptor = release.column(table, column)
            if descriptor is not None and descriptor.fields.identifier:
                columns.add(column)
        for link in self.links[manifest]:
            if link.child == table and not self._names(manifest, link, row):
                columns.difference_update(link.child_columns)
        found: dict[str, bool] = {}
        for column in sorted(columns):
            cell = release.rows(table).cell(row, column)
            items = cell.value if isinstance(cell.value, tuple) else (cell,)
            for item in items if cell.state is PRESENT else ():
                if item.state is not PRESENT or item.value is None or isinstance(item.value, tuple):
                    continue
                text = canonical_string(item.value)
                if text:
                    found[text] = found.get(text, True) and _number(item.value)
        return found

    def unheld(self, store: Store, dataset: str, withdrawn_before: bool, redact_only: bool) -> str:
        """Why no release holds the key; ``redact_only`` is suggested only where it could do,
        the dataset having a withdrawn release that may have held the person."""
        keyed = [
            columns
            for release in self.releases.values()
            if (columns := release.primary_key(self.table)) is not None
        ]
        if keyed and all(len(columns) != len(self.given) for columns in keyed):
            return f"The key has {len(self.given)} values; the table's key has {len(keyed[-1])}"
        if keyed and all(key is None for key in self.keys.values()):
            return "The key's values are not of its columns' datatypes"
        if keyed:
            reason = "No published release holds that row, and no erasure of it waits"
        else:
            reason = f"No published release has a table {self.table} with a key"
        if not withdrawn_before:
            if redact_only:
                reason += "; redact_only needs a release withdrawn earlier, and there is none"
            return reason
        reason += (
            ": check the key; if only a release withdrawn earlier held the person, erase with "
            "redact_only"
        )
        if store.db.upload_pending(dataset):
            reason += " and uploads, to delete the upload area still to delete"
        return reason


def _number(value: SourceValue) -> bool:
    """Whether a value is a number, as an integer or number column holds one, a boolean not
    (D290)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _typed(release: Release, table: str, key: Sequence[SourceValue]) -> Key | None:
    """The key typed by the release's key columns' datatypes, as cells are (``ColumnCells``);
    ``None`` if the release has no such table or key, or a value is not of its datatype."""
    columns = release.primary_key(table)
    if release.table(table) is None or columns is None or len(columns) != len(key):
        return None
    parts: list[KeyPart] = []
    for column, value in zip(columns, key, strict=True):
        cell = ColumnCells(release.datatype(table, column), None).cell(value)
        if cell.state is not PRESENT or cell.value is None or isinstance(cell.value, tuple):
            return None
        parts.append(key_part(cell.value))
    return tuple(parts)


__all__ = ["Erased", "erase"]
