"""Honouring an erasure request (SPEC §12.2, Erasure; D223, D408).

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
that row names the person too. Only rows make a release hold the person, and only such a
release is withdrawn.

**Coverage and scope tables** (D408) name keys too, and refuse rather than withdraw: a coverage
table's parent columns and the links scope columns compose with relationships and keys
(``links``), from every published release and from the registry the store writes at every
publish (``Store.commit_label``), which outlives the declaring release. In a live release that
does not hold the person, a cover row that can be read as a key that is an orphan there and
names one of the person's keys (``_cover_rows``, ``_names``) refuses the erasure
(``ERASURE_BLOCKED``), naming every live label of the release, and table positions and row
numbers, never a value. A row is read one way whatever its cells' shape: each value of a
position (a scalar cell's, every PRESENT item of a list's, of every column the position may be
read from) is kept as its raw canonical string, by which a reading is an orphan, exactly (no
parent row has that key over the link's declared parent columns), and as text is read for
erasure, by which it names the person, a forming rule that may match too much (a refusal costs
a remedy, never a withdrawal).

1. Under the store's lock, so that no session opens and no release is withdrawn between its
   checks and its withdrawals, it checks that no curation session is open on the dataset, since
   its draft may hold the person, and that the latest published release does not hold the person
   (refused with ``ERASURE_BLOCKED`` otherwise, naming its coverage hits too). The whole erasure
   holds the dataset's operation slot, so no import, re-import, withdrawal or session operation
   runs alongside (D236). A key that is not of the key columns' datatypes, or that no published
   release holds, is refused (``INVALID_KEY``), unless ``redact_only`` is given (below), or an
   erasure of it waits. A valid, trusted or waiting key is then scanned for coverage hits, and
   one in a release not withdrawn for rows refuses it before anything new is done; if the
   dataset has an erasure committed and not finished (a waiting redaction, or an upload area
   still to delete), its steps 4 and 5 run first, as ``_again`` runs them. Before all of it,
   the registry's work list is drained for the dataset (``Store.record_known_links``), and while
   a live release of it could not be recorded the erasure is refused.
2. From every other published release that holds the person it collects the terms: the keys of
   the person's rows, and their values in identifier columns (§5.4), each item of a list, as
   canonical strings, but not their foreign keys to rows not theirs (a book they borrowed).
   The person's own row gives the person's terms; the rows below give the terms below them
   (``redaction.Terms``); with them it keeps what the releases say of the places whose
   constants may name those rows (``redaction.Naming``, D290): the keys of the person's rows by
   table, their identifier values by column, and which columns are keys, foreign keys or
   identifiers, of which tables, in which units.
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
key suggest it; like any other, it is refused while a live release's coverage or scope table
names the key. It withdraws nothing for a key no live release holds by rows, and never on a
cover hit; a release that holds the person's rows is withdrawn as for any erasure.

What the transaction records is done whatever fails after it: the redaction runs when a pin is
released or the store opens, and erasing again with the same key while it still waits finishes
steps 4 and 5. Once it has run, the key is nowhere to recognise it by, so an upload area left to
delete is deleted by erasing with ``redact_only``; until then ``Erased.uploads_pending`` says so.

The audit trail records the erasure without the key, which would otherwise need erasing too:
its entry holds the table, the labels withdrawn, the mode and the number of terms, and
redaction leaves it as it is.

The published releases are loaded, their blobs verified, the person's rows found and the
coverage hits worked out (``_scan_ahead``) before the store's lock is taken, since a query takes
that lock to pin a release. Under the lock the published releases and the link registry are read
again, and if either changed meanwhile the rows and the hits are worked out again, loading any
release published since, with the links they and the registry declare: every decision is made
on the releases published, and the links recorded, while the lock is held. Finding the rows is
linear in the releases' rows. The coverage scan reads each cover's rows once per release and
stops at its third hit (the rows text E shows). A row with one value per position costs a few
set lookups; a row with several (a list's items, or several scope columns standing for one key
column) tests only the person's keys that its own values anchor (``_Keys``), or the product of
its values where that is smaller, and the orders a search visits, at most ``ROW_BUDGET`` steps
per value it holds, past which it hits (fail-closed). So the work of a row is linear in its
values, whatever the number of the person's keys, with a constant that is not small: one
``ROW_BUDGET`` unit is one bipartite matching (``_formable``), which costs about n² to n³ in the
key's width n. A hostile release (a key of six columns, 720 permutations of the person's key
as parent rows, and cover rows that list every value in every column) costs about 13 ms a row:
2,000 such rows take 25.7 s, and 500,000 about 1.8 hours. That time is outside the store's
lock but inside the dataset's operation slot, so a re-import is refused while it lasts;
queries are unaffected and no answer is wrong.
"""

import itertools
import math
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.engine.data import PRESENT, Cell, KeyPart, Release, key_part
from aibi.core.schema.output import Message, Segment, data, text
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store import links as declared
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.cells import ColumnCells
from aibi.core.store.links import Cover, Link, Scope
from aibi.core.store.redaction import ERASE_ACTION, Naming, Terms, normal
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
    withdrawn release. Raises ``StoreRefused``. It first waits for the store's housekeeping
    thread (``Store.housekept``), so that the pins tool calls released before it began count as
    released, as their releases would otherwise count them (D300)."""
    store.housekept()
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
    store.record_known_links(dataset)
    _refuse_unrecorded(store, dataset)
    live = _live(store, dataset)
    registry = store.db.cover_registry(dataset)
    person = _person(store, dataset, table, live, key, registry)
    _scan_ahead(store, dataset, person, redact_only)
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
        recorded = store.db.cover_registry(dataset)
        if manifests != [*person.releases] or recorded != registry:
            # Something was published or recorded since: the rows and the coverage hits are
            # worked out again, under the lock (fail-closed); otherwise those worked out ahead
            # are the releases' and the links' now.
            loaded = {
                m: person.releases[m] if m in person.releases else store.load(m) for m in manifests
            }
            person = _person(store, dataset, table, loaded, key, recorded)
        # Only rows withdraw a release: ``holding`` is the releases the person's rows are in, and
        # the key is valid when there is one (D223); a coverage or scope table refuses (D408).
        holding = [manifest for manifest in manifests if person.holds(manifest)]
        withdrawn_before = any(label.withdrawn for label in store.labels(dataset))
        trusted = redact_only and withdrawn_before
        waiting = {} if holding else _waiting(store, dataset, person)
        scan = bool(holding) or trusted or bool(waiting)
        if person.holds(latest.manifest):
            hits = person.cover_hits(latest.manifest) if scan else ()
            raise StoreRefused(
                RefusalCode.ERASURE_BLOCKED,
                "The latest release still holds the row, a row below it, or a row whose foreign "
                "key names one of them: re-import from a source without them first"
                + (
                    f". It also names the key in a coverage or scope table: {_at(hits)}"
                    if hits
                    else ""
                ),
            )
        if scan:
            blocking = [m for m in manifests if m not in holding and person.cover_hits(m)]
            if blocking:
                if waiting or store.db.upload_pending(dataset):
                    # An erasure of the dataset committed and has steps 4 and 5 still to finish:
                    # they run first, in ``_again``'s order, and nothing new is done (D408).
                    _delete_uploads(store, dataset, uploads)
                    store.run_pending_redactions()
                raise StoreRefused(
                    RefusalCode.ERASURE_BLOCKED, _blocked(store, dataset, person, blocking)
                )
        if holding:
            terms, mode = person.terms(), "withdraw"
        else:
            if waiting:
                return _again(store, dataset, waiting, uploads)
            if not trusted:
                reason = person.unheld(store, dataset, withdrawn_before, redact_only)
                raise StoreRefused(RefusalCode.INVALID_KEY, *reason)
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


def _labels(store: Store, dataset: str, manifests: Collection[str]) -> list[list[int]]:
    """The live labels of each manifest, each manifest's in order, in order of their first."""
    found: dict[str, list[int]] = {}
    for label in store.labels(dataset):
        if label.manifest in manifests and not label.withdrawn:
            found.setdefault(label.manifest, []).append(label.label)
    return sorted(found.values())


def _named(labels: Sequence[int]) -> str:
    return ", ".join(f"@{label}" for label in labels)


def _refuse_unrecorded(store: Store, dataset: str) -> None:
    """Refuse while a live release of the dataset published before the link registry existed
    has descriptors that cannot be read, so its links are not recorded (D408)."""
    pending = {manifest for manifest, _ in store.db.links_pending(dataset)}
    groups = _labels(store, dataset, pending)
    if groups:
        raise StoreRefused(
            RefusalCode.ERASURE_BLOCKED,
            "The link registry is incomplete: the descriptors of "
            + ", ".join(_named(labels) for labels in groups)
            + " cannot be read. Repair them or withdraw those releases, then erase again",
        )


Registry = tuple[frozenset[Link], frozenset[Scope], frozenset[Link]]
"""The dataset's link registry (``AppDB.cover_registry``): coverage links, scopes and graph."""


def _cover_links(
    registry: Registry, releases: Mapping[str, Release]
) -> tuple[frozenset[Cover], frozenset[Link]]:
    """The covers an erasure scans, and the relationships declared (D408): the coverage links
    of the registry and of every published release, and the covers their scope mappings compose
    with the relationships and keys of both (``links.compose``, one per scope table and edge).
    With them, every relationship either declares, which gives a refusal's wording. The live
    releases' own declarations are in the registry already (``Store.commit_label`` records
    them, and a live release whose links could not be recorded refuses the erasure first), so
    their union adds nothing today; it stays so that the scan of a live release never depends
    on the registry's write having happened (a registry restored without it, say)."""
    recorded_covers, recorded_scopes, recorded_edges = registry
    covers, scopes, edges = set(recorded_covers), set(recorded_scopes), set(recorded_edges)
    for release in releases.values():
        covers |= declared.covers(release.descriptors)
        scopes |= declared.scopes(release.descriptors)
        edges |= declared.graph(release.descriptors)
    scanned = {Cover.of(found) for found in covers} | declared.compose(scopes, edges)
    relationships = {
        edge
        for edge in edges
        if edge.child != edge.parent or edge.child_columns != edge.parent_columns
    }
    return frozenset(scanned), frozenset(relationships)


def _person(
    store: Store,
    dataset: str,
    table: str,
    releases: Mapping[str, Release],
    key: Sequence[SourceValue],
    registry: Registry | None = None,
) -> "_Person":
    """The person's rows and covers in the releases, with the dataset's link registry as read
    (``registry``), or read now."""
    if registry is None:
        registry = store.db.cover_registry(dataset)
    covers, relationships = _cover_links(registry, releases)
    return _Person(table, releases, key, covers=covers, declared=relationships)


def _scan_ahead(store: Store, dataset: str, person: "_Person", redact_only: bool) -> None:
    """Work out the coverage hits of the releases loaded before the store's lock is taken, as
    their rows are, since the scan reads every row of the covers and a query takes that lock to
    pin a release: under the lock the erasure reuses them when the published releases and the
    registry are those read, and works them out again otherwise. Only where the erasure may
    read them: when a release holds the key, ``redact_only`` is given or an erasure of the key
    waits; in the releases that do not hold the person, and the latest."""
    holding = {manifest for manifest in person.releases if person.holds(manifest)}
    if not (holding or redact_only or _waiting(store, dataset, person)):
        return
    published = [label.manifest for label in store.labels(dataset) if not label.withdrawn]
    for manifest in person.releases:
        if manifest not in holding or manifest == published[-1]:
            person.cover_hits(manifest)


def _at(hits: Iterable["_Hit"]) -> str:
    """Where hits are: table positions and row numbers, never a value (D269)."""
    return "; ".join(
        f"table {hit.table}, {'rows' if len(hit.rows) > 1 else 'row'} "
        + ", ".join(str(row) for row in hit.rows)
        for hit in hits
    )


def _blocked(store: Store, dataset: str, person: "_Person", manifests: Sequence[str]) -> str:
    """Text E (D408): the live labels of each release whose coverage or scope tables name the
    key, with where, and the remedies."""
    groups = _labels(store, dataset, manifests)
    by_label = {
        label.label: label.manifest for label in store.labels(dataset) if not label.withdrawn
    }
    where = "; ".join(
        f"{_named(labels)} ({_at(person.cover_hits(by_label[labels[0]]))})" for labels in groups
    )
    note = any(hit.note for m in manifests for hit in person.cover_hits(m))
    return (
        f"A live release names the key in a coverage or scope table: {where}. A coverage or "
        "scope table never withdraws a release by itself. To clear this, re-import from a "
        "source without the key; for a release that is not the latest, withdraw it yourself "
        "(withdrawing is irreversible and is your decision); withdrawing the release that "
        "declared the columns does not clear it, because the declaration is kept. Then erase "
        "again; if that is refused as an unknown key, use redact_only."
        + (
            " The hit may be a collision of keys: those columns are declared for another "
            "parent, or the release has no table or no such columns for the parent (a renamed "
            "key). If the key belongs to the other declaration, re-import with that column or "
            "table named otherwise, so the old declaration no longer applies (your judgement, "
            "like a withdrawal), or wait for the operator record of M4.0e-1c."
            if note
            else ""
        )
    )


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
    wanted = frozenset(normal(term) for term in person.key_terms())
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
                "The latest release with a table ",
                data(person.table),
                " has no key of those values' number and datatypes",
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


def _links(releases: Mapping[str, Release]) -> tuple[Link, ...]:
    """The relationships every published release declares, each once, its column pairs in
    order of the child's columns (``links.Link``: the same relationship whatever its id, and
    whichever release declares it). Never the registry's: the relationships followed as rows
    are those the published releases declare (D223)."""
    found: set[Link] = set()
    for release in releases.values():
        for relationship in release.relationships:
            fields = relationship.fields
            found.add(
                declared.link(
                    fields.child_table,
                    fields.child_columns,
                    fields.parent_table,
                    fields.parent_columns,
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


def _index(release: Release, link: Link) -> _Index:
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


@dataclass(repr=False)
class _Person:
    """The person's rows in each release (see the module's docstring). Its ``repr`` names its
    table and releases, never the key or the rows (D269)."""

    table: str
    releases: Mapping[str, Release]
    given: Sequence[SourceValue]
    covers: Collection[Cover] = ()
    """The coverage and scope covers to scan (``_cover_links``); never followed as rows."""
    declared: Collection[Link] = ()
    """The relationships any release declared, the registry's included: a refusal's wording
    only (``_Hit.note``)."""
    keys: dict[str, Key | None] = field(init=False)
    """The key, typed by each release's datatypes; ``None`` where it is not of them."""
    links: dict[str, dict[Link, _Index]] = field(init=False)
    """By release: the relationships of every published release whose child table it has with
    their columns, each with its rows there."""
    rows: dict[str, set[Row]] = field(init=False)
    """By release: the person's rows (the person's row being the one in ``table``)."""
    known: dict[Link, set[Canonical]] = field(init=False)
    """By relationship: the keys its parent columns have in the person's rows, in any release."""
    _hits: dict[str, tuple["_Hit", ...]] = field(
        init=False, default_factory=dict[str, tuple["_Hit", ...]]
    )
    _keys_of: dict[Cover, "_Keys"] = field(init=False, default_factory=dict[Cover, "_Keys"])

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

    def __repr__(self) -> str:
        return f"_Person(table={self.table!r}, releases={sorted(self.releases)!r})"

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

    def _orphaned(self, manifest: str, link: Link, value: Canonical) -> list[tuple[str, Row]]:
        rows = self.links[manifest][link].orphans.get(value, ())
        return [(manifest, (link.child, row)) for row in rows]

    def _named(self, manifest: str, link: Link, value: Canonical) -> bool:
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

    def _names(self, manifest: str, link: Link, row: int) -> bool:
        """Whether a row's foreign key through the relationship names one of the person's rows:
        its parent in the release, or, for an orphan, a key of theirs."""
        release = self.releases[manifest]
        parent = self.links[manifest][link].parent[row]
        if parent is not None:
            return (link.parent, parent) in self.rows[manifest]
        value = release.key(link.child, row, link.child_columns)
        return value is not None and self._named(manifest, link, _canonical(value))

    def _own_key(self, manifest: str, link: Link) -> Canonical | None:
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

    def _descends(self, release: Release, link: Link) -> bool:
        """Whether the person's rows go on through the relationship: not into the person's own
        table, nor into another table of things (other people)."""
        descriptor = release.table(link.child)
        return (
            link.child != self.table
            and descriptor is not None
            and descriptor.fields.role != "entity"
        )

    # --- Coverage and scope tables (D408): they refuse an erasure, and never withdraw ---------

    def cover_hits(self, manifest: str) -> tuple["_Hit", ...]:
        """Where the release's coverage and scope tables name the key, worked out once per
        release: for every cover that applies in it, the first rows (``SHOWN``) that can be read
        as a key that is an orphan there and names the person (``_cover_rows``), every item of
        a list cell being a value. In a release that does not hold the person, a cover into
        their own table reads a row whose key has a parent there too as K1 (a re-key, either
        way, that kept the other key as a column), and as K2 where the cover's columns are not
        the table's key columns there (``_rekeyed``, which does not see a change of the key's
        order alone: issue #95). Nothing else reads them: not ``holds``, ``rows``, ``known`` or
        ``terms``."""
        found = self._hits.get(manifest)
        if found is None:
            release = self.releases[manifest]
            hits: list[_Hit] = []
            for cover in sorted(self.covers):
                if not cover.applies(release):
                    continue
                own = cover.parent == self.table and not self.holds(manifest)
                rekeyed = own and self._rekeyed(release, cover)
                keys = self._cover_keys(cover)
                rows = _cover_rows(release, cover, keys, own=own, rekeyed=rekeyed)
                if rows:
                    position = release.table_ids.index(cover.child) + 1
                    shown = tuple(row + 1 for row in rows)
                    hits.append(_Hit(position, shown, self._collides(release, cover)))
            found = self._hits[manifest] = tuple(hits)
        return found

    def _rekeyed(self, release: Release, cover: Cover) -> bool:
        """Whether the cover's parent columns are not the columns of the key of the person's
        table in the release: a re-key that kept the old key as a column, where a parent row
        that carries the person's key, in any order, is no other person's by its key (D408).
        Over the key's own columns, a row that carries an order of the person's values is
        another person's (a collision of keys), and one that carries the key itself is the
        person's. The columns are compared as sets: a change of the key's ORDER alone is NOT
        detected as a re-key. A link's column pairs are sorted by the coverage's own column
        names, so ``cover.parent_columns`` is in an order that says nothing about any
        release's key order, and the registry records no key order (a documented residual,
        issue #95: the real fix records each release's key tuple and compares against it).
        It only forms hits: more of them, never fewer."""
        return set(cover.parent_columns) != set(release.primary_key(self.table) or ())

    def _cover_keys(self, cover: Cover) -> "_Keys":
        """The keys by which a row of the cover names the person in the table it points into,
        read from the live releases alone, so a key known only from a withdrawn release is not
        among them. They form hits, so they may match too much, which costs a refusal, never a
        withdrawal (D408): the key over the cover's parent columns of a row of the person's
        (K1); the key, in any order, of a row of the person's by its table's key, and, for the
        person's own table, the key as given and as each live release types it (K2); each as
        text is read for erasure (``_text``); and, for a cover of one column, their values as
        every datatype the cover's columns, and its parent column, have in the live releases
        reads them (``_Keys``)."""
        found = self._keys_of.get(cover)
        if found is not None:
            return found
        exact: set[Canonical] = set()
        unordered: set[Canonical] = set()
        count = len(cover.parent_columns)
        for manifest, release in self.releases.items():
            mine = [index for table, index in self.rows[manifest] if table == cover.parent]
            if set(cover.parent_columns) <= set(release.columns(cover.parent)):
                for index in mine:
                    typed = release.key(cover.parent, index, cover.parent_columns)
                    if typed is not None:
                        exact.add(_canonical(typed))
            columns = release.primary_key(cover.parent)
            if columns is not None and len(columns) == count:
                for index in mine:
                    typed = release.key(cover.parent, index, columns)
                    if typed is not None:
                        unordered.add(_canonical(typed))
        if cover.parent == self.table and len(self.given) == count:
            unordered.add(tuple(canonical_string(value) or "" for value in self.given))
            for typed in self.keys.values():
                if typed is not None:
                    unordered.add(_canonical(typed))
        cells = self._cover_datatypes(cover) if count == 1 else None
        found = self._keys_of[cover] = _Keys.of(exact, unordered, cells)
        return found

    def _cover_datatypes(self, cover: Cover) -> tuple[ColumnCells, ...]:
        """For a cover of one column: every datatype its columns have where it applies (a list
        column's items as its category) and its parent column has where the parent table has
        it, in the live releases, each as the cells of a column of it."""
        (parent_column,) = cover.parent_columns
        datatypes: set[str] = set()
        for release in self.releases.values():
            for column in cover.present(release)[0]:
                datatypes.add(release.datatype(cover.child, column) or "")
            if release.table(cover.parent) is not None and parent_column in release.columns(
                cover.parent
            ):
                datatypes.add(release.datatype(cover.parent, parent_column) or "")
        datatypes = {"category" if d == "list<category>" else d for d in datatypes} - {""}
        return tuple(ColumnCells(datatype, None) for datatype in sorted(datatypes))

    def _collides(self, release: Release, cover: Cover) -> bool:
        """Whether a hit may be a collision of keys, for its wording alone: another declaration,
        of coverage or of a relationship, from any release or the registry, maps columns of the
        same table that the cover reads to another parent, or the release lacks the parent
        table or its columns."""
        if release.table(cover.parent) is None or not set(cover.parent_columns) <= set(
            release.columns(cover.parent)
        ):
            return True
        columns = {column for alternatives in cover.columns for column in alternatives}
        others = [(o.child, {c for a in o.columns for c in a}, o.parent) for o in self.covers]
        others += [(o.child, set(o.child_columns), o.parent) for o in self.declared]
        return any(
            child == cover.child
            and len(read) <= len(cover.columns)
            and read <= columns
            and parent != cover.parent
            for child, read, parent in others
        )

    def terms(self) -> Terms:
        """The person's terms and those below them, with what the releases say of the places
        whose constants may name the person's rows (``redaction.Naming``, D290): the keys of
        the person's rows by table, the values of their identifier columns by column, and the
        releases' keys, foreign keys, identifier columns and units."""
        person: set[str] = set(self.key_terms())
        below: set[str] = set()
        numbers: set[str] = set()
        text: set[str] = set()
        keys: dict[str, set[Canonical]] = {}
        identifiers, key_columns, names, units = self._schema()
        identifying: set[str] = set()
        for manifest, release in self.releases.items():
            own = self.keys[manifest]
            if own is not None:
                keys.setdefault(self.table, set()).add(_canonical(own))
                identifying.update(
                    text
                    for text, (_, value) in zip(_canonical(own), own, strict=True)
                    if text and not _number(value)
                )
            for current, index in sorted(self.rows[manifest]):
                columns = release.primary_key(current)
                typed = None if columns is None else release.key(current, index, columns)
                if typed is not None:
                    keys.setdefault(current, set()).add(_canonical(typed))
                for column, found in self._identifying(manifest, current, index).items():
                    (person if current == self.table else below).update(found)
                    for term, number in found.items():
                        (numbers if number else text).add(term)
                    marked = identifiers.get(f"{current}.{column}")
                    if marked is not None:
                        marked.update(found)
                        identifying.update(term for term, number in found.items() if not number)
        naming = Naming.of(
            keys=keys,
            key_columns=key_columns,
            names=names,
            identifiers=identifiers,
            identifying=identifying,
            units=units,
        )
        return Terms(person, below, numbers=numbers - text, naming=naming)

    def _schema(
        self,
    ) -> tuple[
        dict[str, set[str]], dict[str, set[tuple[str, ...]]], dict[str, set[str]], dict[str, str]
    ]:
        """What every published release declares of its columns (D290): the identifier columns,
        each with no values yet; each table's key's columns; each key or foreign key column,
        with the tables whose rows it names by itself (its table's for its table's key of one
        column, the table it points into for a foreign key of one, none for a part of more);
        and each column's units."""
        identifiers: dict[str, set[str]] = {}
        key_columns: dict[str, set[tuple[str, ...]]] = {}
        names: dict[str, set[str]] = {}
        units: dict[str, str] = {}
        for manifest, release in self.releases.items():
            for table in release.table_ids:
                columns = release.primary_key(table) or ()
                if columns:
                    key_columns.setdefault(table, set()).add(columns)
                for column in columns:
                    named = names.setdefault(f"{table}.{column}", set())
                    named.update({table} if len(columns) == 1 else ())
                for column in release.columns(table):
                    descriptor = release.column(table, column)
                    if descriptor is None:
                        continue
                    if descriptor.fields.identifier:
                        identifiers.setdefault(descriptor.id, set())
                    if descriptor.fields.units is not None:
                        units[descriptor.id] = str(descriptor.fields.units)
            for link in self.links[manifest]:
                for column in link.child_columns:
                    named = names.setdefault(f"{link.child}.{column}", set())
                    named.update({link.parent} if len(link.child_columns) == 1 else ())
        return identifiers, key_columns, names, units

    def key_terms(self) -> frozenset[str]:
        """The canonical strings of the key's values, as the releases that type it type them,
        or as given if none does."""
        typed = [key for key in self.keys.values() if key is not None]
        values = [value for key in typed for _, value in key] if typed else self.given
        return frozenset(text for value in values if (text := canonical_string(value)))

    def _identifying(self, manifest: str, table: str, row: int) -> dict[str, dict[str, bool]]:
        """A row's values in its key and identifier columns, by column, each PRESENT item of a
        list, except its foreign keys to rows that are not the person's (a book they borrowed,
        say, even when it is part of a link table's key), each with whether it is a number by
        its column's datatype (D290). A foreign key to a row of theirs holds that row's key, a
        term already."""
        release = self.releases[manifest]
        columns = set(release.primary_key(table) or ())
        for column in release.columns(table):
            descriptor = release.column(table, column)
            if descriptor is not None and descriptor.fields.identifier:
                columns.add(column)
        for link in self.links[manifest]:
            if link.child == table and not self._names(manifest, link, row):
                columns.difference_update(link.child_columns)
        found: dict[str, dict[str, bool]] = {}
        for column in sorted(columns):
            cell = release.rows(table).cell(row, column)
            items = cell.value if isinstance(cell.value, tuple) else (cell,)
            for item in items if cell.state is PRESENT else ():
                if item.state is not PRESENT or item.value is None or isinstance(item.value, tuple):
                    continue
                text = canonical_string(item.value)
                if text:
                    values = found.setdefault(column, {})
                    values[text] = values.get(text, True) and _number(item.value)
        return found

    def unheld(
        self, store: Store, dataset: str, withdrawn_before: bool, redact_only: bool
    ) -> Message:
        """Why no release holds the key; ``redact_only`` is suggested only where it could do,
        the dataset having a withdrawn release that may have held the person. The table is named
        as data (D397)."""
        keyed = [
            columns
            for release in self.releases.values()
            if (columns := release.primary_key(self.table)) is not None
        ]
        if keyed and all(len(columns) != len(self.given) for columns in keyed):
            return (
                text(f"The key has {len(self.given)} values; the table's key has {len(keyed[-1])}"),
            )
        if keyed and all(key is None for key in self.keys.values()):
            return (text("The key's values are not of its columns' datatypes"),)
        reason: list[Segment]
        if keyed:
            reason = [text("No published release holds that row, and no erasure of it waits")]
        else:
            reason = [
                text("No published release has a table "),
                data(self.table),
                text(" with a key"),
            ]
        if not withdrawn_before:
            if redact_only:
                reason.append(
                    text("; redact_only needs a release withdrawn earlier, and there is none")
                )
            return tuple(reason)
        more = (
            ": check the key; if only a release withdrawn earlier held the person, erase with "
            "redact_only"
        )
        if store.db.upload_pending(dataset):
            more += " and uploads, to delete the upload area still to delete"
        return (*reason, text(more))


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


@dataclass(frozen=True, repr=False)
class _Hit:
    """Where a coverage or scope table of a release names the key (D408): the table's 1-based
    position among the release's tables, in order of their ids, its first rows (1-based,
    ``SHOWN`` at most), and whether the hit may be a collision of keys, which changes a
    refusal's wording, never its outcome. Positions only, never an id or a value (D269)."""

    table: int
    rows: tuple[int, ...]
    note: bool


SHOWN = 3
"""The rows of a cover that text E shows: a cover's scan stops at as many hits."""

ROW_BUDGET = 64
"""The work one row of a cover of several columns may take, per value the row holds: the keys
and the orders it tests and ``_formable``'s matchings (``_Work``), a unit being a test or one
matching, and a matching costing about n² to n³ in the key's width n. The keys a row tests are
those its values anchor (``_Keys``), so the person's other keys cost it nothing; past the
budget the row hits (fail-closed: a refusal, cleared by the remedies text E names), never
passes."""


class _SpentError(Exception):
    """A row's ``ROW_BUDGET`` is spent."""


class _Work:
    """What a row of a cover of several columns, of ``values`` values, may still spend
    (``ROW_BUDGET``), and what it spent."""

    def __init__(self, values: int) -> None:
        self.values = values
        self.budget = ROW_BUDGET * values
        self.left = self.budget

    @property
    def spent(self) -> int:
        return self.budget - self.left

    def spend(self, units: int = 1) -> None:
        self.left -= units
        if self.left < 0:
            raise _SpentError


Options = tuple[Mapping[str, frozenset[str]], ...]
"""A row of a cover, by position: its values as text is read for erasure (``_text``), each with
the raw canonical strings that read so."""


@dataclass(frozen=True, repr=False)
class _Keys:
    """The person's keys for a cover (``_Person._cover_keys``), as text is read for erasure: K1
    in order (``ordered``) and K2 as sorted multisets (``unordered``); for a cover of one column,
    every value of either, raw and read, with its forms as the cover's datatypes read them
    (``typed``, ``cells``), and K1's alone (``typed_ordered``). Each K1 key is indexed by one
    of its parts, the one the fewest K1 keys share at that position (``by_part``), and each K2
    multiset by its value the fewest share (``by_value``), so a row tests only the keys its own
    values anchor. Its ``repr`` shows no key (D269)."""

    ordered: frozenset[Canonical]
    unordered: frozenset[Canonical]
    typed: frozenset[str]
    typed_ordered: frozenset[str]
    cells: tuple[ColumnCells, ...]
    by_part: Mapping[tuple[int, str], tuple[Canonical, ...]]
    by_value: Mapping[str, tuple[Canonical, ...]]

    @classmethod
    def of(
        cls,
        exact: Collection[Canonical],
        unordered: Collection[Canonical],
        cells: tuple[ColumnCells, ...] | None,
    ) -> "_Keys":
        """From K1 and K2 as canonical strings; ``cells`` for a cover of one column."""
        ordered = frozenset(_normal(key) for key in exact)
        multisets = frozenset(tuple(sorted(_normal(key))) for key in unordered)
        typed: set[str] = set()
        typed_ordered: set[str] = set()
        if cells is not None:
            for found, keys in (
                (typed_ordered, (*exact, *ordered)),
                (typed, (*unordered, *multisets)),
            ):
                for key in keys:
                    found.add(key[0])
                    found.update(_forms(key[0], cells))
            typed |= typed_ordered
        parts = Counter((index, part) for key in ordered for index, part in enumerate(key))
        by_part: dict[tuple[int, str], list[Canonical]] = {}
        for key in sorted(ordered):
            anchor = min(enumerate(key), key=lambda part: (parts[part], part))
            by_part.setdefault(anchor, []).append(key)
        values = Counter(value for multiset in multisets for value in set(multiset))
        by_value: dict[str, list[Canonical]] = {}
        for multiset in sorted(multisets):
            anchor = min(set(multiset), key=lambda value: (values[value], value))
            by_value.setdefault(anchor, []).append(multiset)
        return cls(
            ordered,
            multisets,
            frozenset(typed),
            frozenset(typed_ordered),
            cells or (),
            {part: tuple(keys) for part, keys in by_part.items()},
            {value: tuple(keys) for value, keys in by_value.items()},
        )


@dataclass(frozen=True, repr=False)
class _Parents:
    """A cover's parent keys in a release: as raw canonical strings (``raw``, D223's orphan
    rule, exact), and those that read as one of the person's keys, by K1 key (``by_order``) and
    by K2 multiset (``by_multiset``)."""

    raw: frozenset[Canonical]
    by_order: Mapping[Canonical, tuple[Canonical, ...]]
    by_multiset: Mapping[Canonical, frozenset[Canonical]]


def _parents(release: Release, cover: Cover, keys: _Keys) -> _Parents:
    """The parent keys over the cover's own parent columns, in the cover's order. A release
    without the parent table, or without one of those columns, has none, so every key is an
    orphan there."""
    raw: set[Canonical] = set()
    if release.table(cover.parent) is not None and set(cover.parent_columns) <= set(
        release.columns(cover.parent)
    ):
        for row in range(len(release.rows(cover.parent))):
            key = release.key(cover.parent, row, cover.parent_columns)
            if key is not None:
                raw.add(_canonical(key))
    by_order: dict[Canonical, list[Canonical]] = {}
    by_multiset: dict[Canonical, set[Canonical]] = {}
    if len(cover.parent_columns) > 1:
        for key in raw:
            read = _normal(key)
            if read in keys.ordered:
                by_order.setdefault(read, []).append(key)
            multiset = tuple(sorted(read))
            if multiset in keys.unordered:
                by_multiset.setdefault(multiset, set()).add(key)
    return _Parents(
        frozenset(raw),
        {read: tuple(sorted(found)) for read, found in by_order.items()},
        {multiset: frozenset(found) for multiset, found in by_multiset.items()},
    )


def _cover_rows(
    release: Release, cover: Cover, keys: _Keys, *, own: bool, rekeyed: bool
) -> tuple[int, ...]:
    """The first ``SHOWN`` rows (0-based) of the cover's table in the release that name the
    person (``_names``). A row's values at a position are every PRESENT item of a list cell, as
    a scalar cell's value is, of every column the position may be read from; a row with no
    value at some position has no key. With ``own`` (a cover into the person's own table, in a
    release that does not hold them), a row is read as K1 whether or not its key has a parent
    there, and with ``rekeyed`` (its columns not the table's key columns there; a change of the
    key's order alone is not seen, issue #95) as K2 too: a re-key that kept the person's other
    key as a column."""
    parents = _parents(release, cover, keys)
    columns = cover.present(release)
    table = release.rows(cover.child)
    found: list[int] = []
    seen: dict[tuple[frozenset[str], ...], bool] = {}
    for row in range(len(table)):
        raws: list[frozenset[str]] = []
        for alternatives in columns:
            texts: set[str] = set()
            for column in alternatives:
                texts.update(_texts(table.cell(row, column)))
            if not texts:
                break
            raws.append(frozenset(texts))
        else:
            shape = tuple(raws)
            named = seen.get(shape)
            if named is None:
                named = seen[shape] = _names(shape, keys, parents, own=own, rekeyed=rekeyed)
            if named:
                found.append(row)
                if len(found) == SHOWN:
                    break
    return tuple(found)


def _names(
    raws: tuple[frozenset[str], ...], keys: _Keys, parents: _Parents, *, own: bool, rekeyed: bool
) -> bool:
    """Whether a row, its raw values at each position, can be read as a key that is an orphan
    and names the person: one value per position, whose raw canonical strings are no parent key
    (D223's orphan rule: it suppresses hits, so it is exact; ``own`` and ``rekeyed`` lift it
    for K1 and K2, see ``_cover_rows``), and which, as text
    is read for erasure, is a K1 key, or a K2 multiset in any order (forming rules, which may
    match too much). This is the one reading of a cover's values, a scalar cell's and a list's
    items' alike. A cover of one column reads each value as a key, also as each of its
    datatypes reads it. A cover of several matches its keys without enumerating the product of
    the positions' values: it tests only the keys its values anchor, or the product where that
    is smaller, and the orders ``_formable`` visits, within ``ROW_BUDGET`` per value of the
    row, past which the row hits."""
    if len(raws) == 1:
        for raw in sorted(raws[0]):
            forms = _readings(raw, keys.cells)
            if ((raw,) not in parents.raw or rekeyed) and not forms.isdisjoint(keys.typed):
                return True
            if own and not forms.isdisjoint(keys.typed_ordered):
                return True
        return False
    if all(len(values) == 1 for values in raws):
        # One reading: what the search below finds for it, without the search (its candidates
        # are this reading's key and multiset, its only order the reading itself).
        (reading,) = itertools.product(*raws)
        read = _normal(reading)
        orphan = reading not in parents.raw
        if read in keys.ordered and (orphan or own):
            return True
        return tuple(sorted(read)) in keys.unordered and (orphan or rekeyed)
    options = tuple(_read(values) for values in raws)
    work = _Work(sum(len(values) for values in raws))
    try:
        for key in _ordered(options, keys, work):
            if own or _free(key, options, parents.by_order.get(key, ()), work):
                return True
        for multiset in _unordered(options, keys, work):
            taken = frozenset[Canonical]() if rekeyed else parents.by_multiset.get(multiset)
            if _formable(multiset, options, taken or frozenset(), work):
                return True
    except _SpentError:
        return True
    return False


def _readings(raw: str, cells: Collection[ColumnCells]) -> set[str]:
    """A value of a cover of one column, raw and as read for erasure, with the forms of either
    as the cover's datatypes read them: it names the person when one is a value of ``_Keys``'
    ``typed``."""
    texts = {raw, _text(raw)}
    return texts.union(*(_forms(text, cells) for text in texts))


def _read(values: frozenset[str]) -> dict[str, frozenset[str]]:
    """A position's raw values by how text is read for erasure (``_text``)."""
    read: dict[str, set[str]] = {}
    for raw in values:
        read.setdefault(_text(raw), set()).add(raw)
    return {text: frozenset(found) for text, found in read.items()}


def _ordered(options: Options, keys: _Keys, work: _Work) -> list[Canonical]:
    """The K1 keys each of whose parts is among its position's values: those the row's values
    anchor (``_Keys.by_part``), or, where the product of the values is smaller, those of the
    product, each examined once paid for."""
    product = math.prod(len(option) for option in options)
    anchored = [keys.by_part.get((index, v), ()) for index, o in enumerate(options) for v in o]
    if product <= sum(len(found) for found in anchored):
        work.spend(product)
        return [key for key in itertools.product(*options) if key in keys.ordered]
    work.spend(sum(len(found) for found in anchored))
    return [
        key
        for found in anchored
        for key in found
        if all(part in option for part, option in zip(key, options, strict=True))
    ]


def _unordered(options: Options, keys: _Keys, work: _Work) -> list[Canonical]:
    """The K2 multisets each of whose values is among the row's values: those the row's values
    anchor (``_Keys.by_value``), or, where the product of the values is smaller, those of the
    product, sorted."""
    product = math.prod(len(option) for option in options)
    union = {value for option in options for value in option}
    anchored = [keys.by_value.get(value, ()) for value in sorted(union)]
    if product <= sum(len(found) for found in anchored):
        work.spend(product)
        formed = {tuple(sorted(order)) for order in itertools.product(*options)}
        return sorted(formed & keys.unordered)
    work.spend(sum(len(found) for found in anchored))
    return [
        multiset
        for found in anchored
        for multiset in found
        if all(value in union for value in multiset)
    ]


def _free(key: Canonical, options: Options, parents: Sequence[Canonical], work: _Work) -> bool:
    """Whether some reading of a K1 key from the row's raw values is no parent key: the row
    reads it in as many ways as the product of the raw values that read as each part, and
    ``parents`` are the parent keys that read as it."""
    work.spend(len(parents))
    readings = math.prod(len(option[part]) for part, option in zip(key, options, strict=True))
    held = sum(
        all(raw in option[part] for raw, part, option in zip(parent, key, options, strict=True))
        for parent in parents
    )
    return readings > held


def _texts(cell: Cell) -> set[str]:
    """A cell's values as canonical strings: a PRESENT scalar's, or each PRESENT item of a
    list; none for a missing cell."""
    if cell.state is not PRESENT or cell.value is None:
        return set()
    if isinstance(cell.value, tuple):
        return {
            canonical_string(item.value) or ""
            for item in cell.value
            if item.state is PRESENT
            and item.value is not None
            and not isinstance(item.value, tuple)
        }
    return {canonical_string(cell.value) or ""}


def _text(value: str) -> str:
    """A value as text is read for erasure (D290: NFC, no format characters), and without the
    spaces around it (a delimited list's items keep those after the delimiter). It is the same
    for its own result. Only hits are formed by it, so it may match too much (D408); letter case
    is not folded, as two keys may differ by it alone."""
    return normal(value).strip()


def _normal(key: Canonical) -> Canonical:
    """A key's values as text is read for erasure (``_text``)."""
    return tuple(_text(value) for value in key)


def _formable(
    multiset: Canonical, options: Options, taken: Collection[Canonical], work: _Work
) -> bool:
    """Whether the positions can take the values of ``multiset``, one each from their own
    values, in an order some raw reading of which is not a parent key (``taken``, the parent
    keys that read as an order of ``multiset``). A depth-first search that tries a value at a
    position only while the rest can still be filled (a bipartite matching of the positions
    left with the values left), so that every branch it enters ends in a reading: it visits at
    most as many readings as ``taken`` holds, plus one, and never enumerates the positions'
    product. Each matching is paid for (``work``); a spent budget raises ``_SpentError``, and
    the row hits."""
    count = len(options)
    if len(multiset) != count:
        return False
    left = dict(Counter(multiset))
    chosen: list[str] = []

    def fillable(start: int) -> bool:
        work.spend()
        slots = [value for value, times in left.items() for _ in range(times)]
        matched: list[int] = [-1] * len(slots)

        def augment(position: int, seen: set[int]) -> bool:
            for slot, value in enumerate(slots):
                if slot not in seen and value in options[position]:
                    seen.add(slot)
                    if matched[slot] == -1 or augment(matched[slot], seen):
                        matched[slot] = position
                        return True
            return False

        return all(augment(position, set()) for position in range(start, count))

    def search(position: int) -> bool:
        if position == count:
            return tuple(chosen) not in taken
        for value in sorted(left):
            if left[value] and value in options[position]:
                left[value] -= 1
                if fillable(position + 1):
                    for raw in sorted(options[position][value]):
                        chosen.append(raw)
                        if search(position + 1):
                            return True
                        chosen.pop()
                left[value] += 1
        return False

    return fillable(0) and search(0)


def _forms(value: str, datatypes: Collection[ColumnCells]) -> set[str]:
    """A canonical string as each datatype reads it, as cells are typed (``ColumnCells``, one of
    each datatype, made once), as canonical strings: where it is a value of that datatype."""
    forms: set[str] = set()
    for cells in datatypes:
        cell = cells.cell(value)
        if cell.state is PRESENT and cell.value is not None and not isinstance(cell.value, tuple):
            forms.add(canonical_string(cell.value) or "")
    return forms


__all__ = ["Erased", "erase"]
