"""Honouring an erasure request (SPEC §12.2, Erasure; D223).

The first step is a re-import from a source without the person's rows, since a curation session
cannot remove rows (#10). ``erase`` does the rest, for the parts of the app DB that exist so far.

**The person's rows** in a release are their row, found by its key typed by the key columns'
datatypes, and the rows below it: those whose foreign key holds the key of a row of theirs that
the same release holds, through every relationship except those into the person's own table or
into a table whose role is ``entity`` (other people: a member another referred, say), and so on
down. A key of a row of theirs in another release counts only for an orphan: a row whose foreign
key names no row of this release (a loan of the person's, left behind when a re-import dropped
the member). So a re-import that numbers its rows afresh, and gives loan 2 to someone else, does
not make that loan the person's. The person's own key is matched in every release. A release
holds the person when it holds one of their rows, or a row whose foreign key names one of them,
in the same way, through any relationship, since that row names the person too.

1. Under the store's lock, so that no session opens and no release is withdrawn between its
   checks and its withdrawals, it checks that no curation session is open on the dataset, since
   its draft may hold the person, and that the latest published release does not hold the person
   (refused with ``ERASURE_BLOCKED`` otherwise). Keeping an import of the dataset from running
   alongside is the per-dataset operation lock's (§12.3, #10). A key that is not of the key
   columns' datatypes, or that no published release holds, is refused (``INVALID_KEY``), unless
   ``redact_only`` is given (below).
2. From every other published release that holds the person it collects the terms: the keys of
   the person's rows, and their values in identifier columns (§5.4), as canonical strings, but
   not their foreign keys to rows not theirs (a book they borrowed). The person's own row gives
   the person's terms; the rows below give the terms below them (``redaction.Terms``).
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
from aibi.core.schema.descriptors import RelationshipDescriptor
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.cells import ColumnCells
from aibi.core.store.redaction import ERASE_ACTION, Terms
from aibi.core.store.sources import SourceValue, canonical_string
from aibi.core.store.store import Store, StoreRefused

Key = tuple[KeyPart, ...]
Row = tuple[str, int]


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
    terms = Terms([text for value in values if (text := canonical_string(value))])
    if not terms:
        raise StoreRefused(RefusalCode.INVALID_KEY, "The key has no value to redact")
    return terms


def _delete_uploads(store: Store, dataset: str, uploads: Callable[[str], None] | None) -> None:
    if uploads is not None:
        uploads(dataset)
        store.db.uploads_deleted(dataset)


@dataclass
class _Person:
    """The person's rows in each release (see the module's docstring)."""

    table: str
    releases: Mapping[str, Release]
    given: Sequence[SourceValue]
    keys: dict[str, Key | None] = field(init=False)
    """The key, typed by each release's datatypes; ``None`` where it is not of them."""
    rows: dict[str, set[Row]] = field(init=False)
    """By release: the person's rows (the person's row being the one in ``table``)."""
    known: dict[str, set[Key]] = field(init=False)
    """By relationship: the keys its parent columns have in the person's rows, in any release."""
    orphans: dict[str, dict[str, dict[Key, list[int]]]] = field(init=False)
    """By release and relationship: the child rows whose foreign key names no row of the release,
    by that key."""

    def __post_init__(self) -> None:
        self.keys = {
            m: _typed(release, self.table, self.given) for m, release in self.releases.items()
        }
        self.orphans = {m: _orphans(release) for m, release in self.releases.items()}
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
            for relationship in release.relationships:
                own = self._own_key(manifest, relationship)
                if own is not None and self._descends(release, relationship):
                    found.extend(self._orphaned(manifest, relationship, own))
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
        for relationship in release.relationships:
            fields = relationship.fields
            if fields.parent_table != current:
                continue
            if self._descends(release, relationship):
                reached.extend(
                    (manifest, (fields.child_table, child))
                    for child in release.children(relationship.id, index)
                )
            value = release.key(current, index, fields.parent_columns)
            known = self.known.setdefault(relationship.id, set())
            if value is None or value in known:
                continue
            known.add(value)
            for other, elsewhere in self.releases.items():
                same = elsewhere.relationship(relationship.id)
                if same is not None and self._descends(elsewhere, same):
                    reached.extend(self._orphaned(other, same, value))
        return reached

    def _orphaned(
        self, manifest: str, relationship: RelationshipDescriptor, value: Key
    ) -> list[tuple[str, Row]]:
        rows = self.orphans[manifest].get(relationship.id, {}).get(value, ())
        return [(manifest, (relationship.fields.child_table, row)) for row in rows]

    def _named(self, manifest: str, relationship: RelationshipDescriptor, value: Key) -> bool:
        """Whether an orphan's foreign key through the relationship names the person: a key of
        the person's rows in any release, or the person's own key."""
        return value in self.known.get(relationship.id, ()) or value == self._own_key(
            manifest, relationship
        )

    def holds(self, manifest: str) -> bool:
        """Whether the release holds one of the person's rows, or a row whose foreign key names
        one of them through any relationship (another member's ``referred_by``, say): a child
        of a row of theirs, which is a row of theirs or names one, or an orphan."""
        if self.rows[manifest]:
            return True
        release = self.releases[manifest]
        for relationship in release.relationships:
            orphans = self.orphans[manifest].get(relationship.id, {})
            if any(self._named(manifest, relationship, value) for value in orphans):
                return True
        return False

    def _names(self, manifest: str, relationship: RelationshipDescriptor, row: int) -> bool:
        """Whether a row's foreign key through the relationship names one of the person's rows:
        its parent in the release, or, for an orphan, a key of theirs."""
        release, fields = self.releases[manifest], relationship.fields
        parent = release.parent(relationship.id, row)
        if parent is not None:
            return (fields.parent_table, parent) in self.rows[manifest]
        value = release.key(fields.child_table, row, fields.child_columns)
        return value is not None and self._named(manifest, relationship, value)

    def _own_key(self, manifest: str, relationship: RelationshipDescriptor) -> Key | None:
        """The person's key as a foreign key through the relationship holds it, if the
        relationship is into the person's table: its columns in the relationship's order, since
        a relationship leads to its parent's key in any order (§5.6)."""
        release, fields, key = self.releases[manifest], relationship.fields, self.keys[manifest]
        columns = release.primary_key(self.table)
        if key is None or columns is None or fields.parent_table != self.table:
            return None
        parts = dict(zip(columns, key, strict=True))
        return tuple(parts[column] for column in fields.parent_columns)

    def _descends(self, release: Release, relationship: RelationshipDescriptor) -> bool:
        """Whether the person's rows go on through the relationship: not into the person's own
        table, nor into another table of things (other people)."""
        child = relationship.fields.child_table
        descriptor = release.table(child)
        return child != self.table and descriptor is not None and descriptor.fields.role != "entity"

    def terms(self) -> Terms:
        person: set[str] = set(self.key_terms())
        below: set[str] = set()
        for manifest in self.releases:
            for current, index in sorted(self.rows[manifest]):
                found = self._identifying(manifest, current, index)
                (person if current == self.table else below).update(found)
        return Terms(person, below)

    def key_terms(self) -> frozenset[str]:
        """The canonical strings of the key's values, as the releases that type it type them,
        or as given if none does."""
        typed = [key for key in self.keys.values() if key is not None]
        values = [value for key in typed for _, value in key] if typed else self.given
        return frozenset(text for value in values if (text := canonical_string(value)))

    def _identifying(self, manifest: str, table: str, row: int) -> set[str]:
        """A row's key and its values in identifier columns, except its foreign keys to rows that
        are not the person's (a book they borrowed, say, even when it is part of a link table's
        key). A foreign key to a row of theirs holds that row's key, a term already."""
        release = self.releases[manifest]
        columns = set(release.primary_key(table) or ())
        for column in release.columns(table):
            descriptor = release.column(table, column)
            if descriptor is not None and descriptor.fields.identifier:
                columns.add(column)
        for relationship in release.relationships:
            fields = relationship.fields
            if fields.child_table == table and not self._names(manifest, relationship, row):
                columns.difference_update(fields.child_columns)
        found: set[str] = set()
        for column in columns:
            cell = release.rows(table).cell(row, column)
            if (
                cell.state is PRESENT
                and cell.value is not None
                and not isinstance(cell.value, tuple)
            ):
                text = canonical_string(cell.value)
                if text:
                    found.add(text)
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


def _orphans(release: Release) -> dict[str, dict[Key, list[int]]]:
    """By relationship: the child rows whose foreign key names no row of the release, by that
    key (a null key names none)."""
    found: dict[str, dict[Key, list[int]]] = {}
    for relationship in release.relationships:
        fields = relationship.fields
        by_key: dict[Key, list[int]] = {}
        for row in range(len(release.rows(fields.child_table))):
            if release.parent(relationship.id, row) is None:
                value = release.key(fields.child_table, row, fields.child_columns)
                if value is not None:
                    by_key.setdefault(value, []).append(row)
        found[relationship.id] = by_key
    return found


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
