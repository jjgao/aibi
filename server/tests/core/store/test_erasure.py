"""Erasure (SPEC §12.2, D223): after a re-import without a person's rows, no blob and no page of
the app DB holds their key, nothing else is erased (not the people linked to them), a query
still running on a withdrawn release, or a reader of the app DB, holds off the redaction until
it ends, and what fails after the withdrawals is finished later."""

import json
import re
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from pydantic import JsonValue

from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor
from aibi.core.store import parquet
from aibi.core.store.build import BuildRefused, Layout
from aibi.core.store.erasure import Erased, erase
from aibi.core.store.manifest import hex_of
from aibi.core.store.redaction import MARK, Terms
from aibi.core.store.sources import TypedSource
from aibi.core.store.store import CHECKPOINT_RETRY, Store, StoreRefused

Library = Any
KEY = "m-17"


def _without_the_member(
    store: Store, library: Library, descriptors: list[Descriptor] | None = None
) -> str:
    """The library re-imported without m-17 and their loans, published as the next label."""
    members = b"\n".join(line for line in library.members.split(b"\n") if KEY.encode() not in line)
    loans = [loan for loan in library.loans if KEY not in loan]
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors() if descriptors is None else descriptors,
            library.sources(members=members, loans=loans),
            library.layouts,
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    return built.manifest.hash


def _mention(store: Store, manifest: str) -> None:
    """A proposal and an audit entry that name the member."""
    with store.db.transaction() as db:
        store.db.add_proposal(
            db,
            dataset="lib",
            release=manifest,
            descriptor="members.member_id",
            pointer="/fields/label",
            value={"note": f"as {KEY} said", KEY: 17},
            proposer="agent:helper",
            evidence=f"row {KEY}; not m-170",
            at=store.now(),
        )
        store.db.audit(db, store.now(), "lib", "operator:ada", "note", {"about": KEY})


def _note(store: Store, detail: JsonValue) -> None:
    with store.db.transaction() as db:
        store.db.audit(db, store.now(), "lib", "operator:ada", "note", detail)


def _propose(store: Store, manifest: str, pointer: str, value: JsonValue, evidence: str) -> None:
    with store.db.transaction() as db:
        store.db.add_proposal(
            db,
            dataset="lib",
            release=manifest,
            descriptor="books.genre",
            pointer=pointer,
            value=value,
            proposer="agent:helper",
            evidence=evidence,
            at=store.now(),
        )


def _details(store: Store, action: str) -> list[JsonValue]:
    rows = store.db.connection.execute(
        "SELECT detail FROM audit WHERE action = ? ORDER BY id", (action,)
    )
    return [json.loads(row[0]) for row in rows]


def _proposals(store: Store) -> list[tuple[str, JsonValue, str]]:
    rows = store.db.connection.execute("SELECT pointer, value, evidence FROM proposals ORDER BY id")
    return [(pointer, json.loads(value), evidence) for pointer, value, evidence in rows]


def _holds(store: Store, text: str) -> list[str]:
    """Where the store still holds ``text``: blobs as stored and as decoded, and the app DB's
    files, the write-ahead log included."""
    token = re.compile(rb"(?<![0-9A-Za-z])" + re.escape(text.encode()) + rb"(?![0-9A-Za-z])")
    found: list[str] = []
    for digest in store.blobs.digests():
        data = store.blobs.read(digest)
        if token.search(data):
            found.append(f"blob {digest}")
        try:
            columns = parquet.read(data)
        except Exception:
            continue
        if any(token.search(json.dumps(column.values, default=str).encode()) for column in columns):
            found.append(f"table {digest}")
    for path in Path(store.db.path).parent.glob("app.db*"):
        if token.search(path.read_bytes()):
            found.append(path.name)
    return found


def test_an_erased_person_is_left_nowhere(store: Store, library: Library, imported: str) -> None:
    _mention(store, imported)
    latest = _without_the_member(store, library)
    deleted: list[str] = []
    erased = erase(store, "lib", "members", [KEY], "operator:ada", uploads=deleted.append)
    assert erased.withdrawn == (1,)
    assert erased.redacted
    assert deleted == ["lib"]
    assert _holds(store, KEY) == []
    assert store.resolve("lib").manifest == latest
    assert store.resolve("lib", 1).status == "withdrawn"
    [(value, evidence)] = store.db.connection.execute(
        "SELECT value, evidence FROM proposals"
    ).fetchall()
    assert evidence == f"row {MARK}; not m-170"  # only whole tokens are erased
    assert MARK in value
    [(detail,)] = store.db.connection.execute(
        "SELECT detail FROM audit WHERE action = 'erase'"
    ).fetchall()
    assert KEY not in detail
    # Only the person's terms go: the books they borrowed, and the labels, stay.
    details = [row[0] for row in store.db.connection.execute("SELECT detail FROM audit")]
    assert any('"label":2' in detail for detail in details)
    assert _holds(store, "b-1")


def test_erasure_waits_for_a_re_import_without_the_person(store: Store, imported: str) -> None:
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "ERASURE_BLOCKED"


def test_a_query_that_pins_the_release_holds_off_the_redaction(
    store: Store, library: Library, imported: str
) -> None:
    _mention(store, imported)
    _without_the_member(store, library)
    query = store.pin()
    query.manifest(imported)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert not erased.redacted
    assert store.load(imported).rows("members").cell(1, "member_id").value == KEY
    query.release()
    assert _holds(store, KEY) == []


def test_a_redaction_left_waiting_runs_when_the_store_opens_again(
    tmp_path: Path, library: Library
) -> None:
    root = tmp_path / "data"
    store = Store(root)
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _mention(store, built.manifest.hash)
    _without_the_member(store, library)
    query = store.pin()
    query.manifest(built.manifest.hash)
    assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
    store.close()  # the process ends, and its pins with it
    reopened = Store(root)
    try:
        assert _holds(reopened, KEY) == []
    finally:
        reopened.close()


def test_the_keys_of_the_rows_below_the_person_are_erased_as_whole_values(
    store: Store, library: Library, imported: str
) -> None:
    """m-17's loans are 2 and 3: those numbers go where they are whole values, and stay in prose,
    in JSON Pointers and in the erasure's own counts."""
    _propose(store, imported, "/fields/permissible_values/values/2/label", {"min": 3}, "page 2")
    _propose(store, imported, "/fields/label", ["2", 2.0, 4, "loan 3", True], "3")
    _note(store, {"rows": 3, "text": "chapter 2 of m-17", "loan": [2]})
    _without_the_member(store, library)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert erased.terms == 3  # m-17, and the loans 2 and 3
    assert _proposals(store) == [
        ("/fields/permissible_values/values/2/label", {"min": MARK}, "page 2"),
        ("/fields/label", [MARK, MARK, 4, "loan 3", True], MARK),
    ]
    assert _details(store, "note") == [
        {"loan": [MARK], "rows": MARK, "text": f"chapter 2 of {MARK}"}
    ]
    assert _details(store, "erase") == [
        {"mode": "withdraw", "table": "members", "terms": 3, "withdrawn": [1]}
    ]


def test_the_person_s_values_in_identifier_columns_are_erased(
    store: Store, library: Library
) -> None:
    def named(descriptors: list[Descriptor]) -> list[Descriptor]:
        return [
            build.column("members.name", "string", identifier=True)
            if descriptor.id == "members.name"
            else descriptor
            for descriptor in descriptors
        ]

    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", named(library.descriptors()), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _note(store, {"about": "Grace Hopper", "not": "Ada"})
    _without_the_member(store, library, named(library.descriptors()))
    assert erase(store, "lib", "members", [KEY], "operator:ada").terms == 4
    assert _details(store, "note") == [{"about": f"{MARK} Hopper", "not": "Ada"}]


def test_labels_and_the_erasure_s_own_entry_are_kept_when_they_equal_a_term(
    store: Store, library: Library, imported: str
) -> None:
    with store.pin() as pin:
        pin.manifest(imported)
        assert store.publish("lib", imported, "operator:ada") == 2  # 2 is also a loan of m-17
    _without_the_member(store, library)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert erased.withdrawn == (1, 2)
    assert [detail["labels"] for detail in _details(store, "withdraw")] == [[1, 2]]  # type: ignore[index]
    assert [detail["label"] for detail in _details(store, "publish")] == [1, 2, 3]  # type: ignore[index]
    assert _details(store, "erase") == [
        {"mode": "withdraw", "table": "members", "terms": 3, "withdrawn": [1, 2]}
    ]


def _publish_shelf(
    store: Store,
    holds: tuple[tuple[str, str], ...],
    *,
    members: tuple[str, ...] | None = None,
    books: tuple[str, ...] = ("b-1", "b-2"),
    status: str = "asserted",
) -> None:
    """Members (those holding a book unless ``members`` are given), ``books``, and a link table
    of the books each member holds, keyed by both, with relationships of ``status``."""

    def source(name: str) -> dict[str, Any]:
        return {"kind": "sheet", "name": name, "original_name": name}

    descriptors = [
        build.dataset(),
        build.table("members", ["member_id"], source=source("members")),
        build.column("members.member_id", "string", identifier=True),
        build.table("books", ["book_id"], source=source("books")),
        build.column("books.book_id", "string"),
        build.table("holds", ["member_id", "book_id"], role="link", source=source("holds")),
        build.column("holds.member_id", "string"),
        build.column("holds.book_id", "string"),
        build.relationship("holds", ["member_id"], "members", role="member", status=status),
        build.relationship("holds", ["book_id"], "books", role="book", status=status),
    ]
    if members is None:
        members = tuple(sorted({member for member, _ in holds}))
    sources = {
        "members": TypedSource(("member_id",), tuple((member,) for member in members)),
        "books": TypedSource(("book_id",), tuple((book,) for book in books)),
        "holds": TypedSource(("member_id", "book_id"), holds),
    }
    layouts = {
        "members": Layout("members", (("member_id", "member_id"),)),
        "books": Layout("books", (("book_id", "book_id"),)),
        "holds": Layout("holds", (("member_id", "member_id"), ("book_id", "book_id"))),
    }
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, "operator:ada")


def test_a_link_table_s_foreign_key_to_a_row_not_the_person_s_is_no_term(store: Store) -> None:
    _publish_shelf(store, (("m-1", "b-1"), (KEY, "b-2")))
    _note(store, {"book": "b-2", "member": KEY})
    _publish_shelf(store, (("m-1", "b-1"),))
    assert erase(store, "lib", "members", [KEY], "operator:ada").terms == 1
    assert _details(store, "note") == [{"book": "b-2", "member": MARK}]


def test_an_orphan_s_foreign_key_to_a_row_not_the_person_s_is_no_term(store: Store) -> None:
    """The hold left behind names neither its member nor its book in the second release: its
    member is the person, so it is theirs, but its book is no one's there."""
    _publish_shelf(store, (("m-1", "b-1"), (KEY, "b-2")))
    _note(store, {"book": "b-2", "member": KEY})
    left = (("m-1", "b-1"), (KEY, "b-2"))
    _publish_shelf(store, left, members=("m-1",), books=("b-1",), status="proposed")
    _publish_shelf(store, (("m-1", "b-1"),))
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1, 2), 1)
    assert _details(store, "note") == [{"book": "b-2", "member": MARK}]


def test_erasure_is_refused_while_a_session_is_open_on_the_dataset(
    store: Store, library: Library, imported: str
) -> None:
    with store.db.transaction() as db:
        store.db.open_session(db, "lib", "h" * 64, imported, store.now(), "operator:ada")
    _without_the_member(store, library)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "ERASURE_BLOCKED"
    assert store.resolve("lib", 1).status == "published"


def test_a_query_on_the_new_release_does_not_hold_off_the_redaction(
    store: Store, library: Library, imported: str
) -> None:
    _mention(store, imported)
    latest = _without_the_member(store, library)
    with store.pin() as query:
        query.manifest(latest)
        assert erase(store, "lib", "members", [KEY], "operator:ada").redacted
        assert _holds(store, KEY) == []


def test_a_pin_on_the_withdrawn_manifest_alone_holds_off_the_redaction(
    store: Store, library: Library, imported: str
) -> None:
    _mention(store, imported)
    _without_the_member(store, library)
    with store.pin() as operation:
        operation.add(hex_of(imported))
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        assert _holds(store, KEY)
    assert _holds(store, KEY) == []


def test_a_reader_of_the_app_db_holds_off_the_redaction_until_it_ends(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mention(store, imported)
    _without_the_member(store, library)
    reader = sqlite3.connect(store.db.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM audit").fetchone()
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        assert _holds(store, KEY)  # the old pages are still in the WAL
    finally:
        reader.close()
    later = monotonic() + CHECKPOINT_RETRY
    monkeypatch.setattr("aibi.core.store.store.monotonic", lambda: later)
    with store.pin():
        pass  # releasing a pin retries the checkpoint, a second later
    assert _holds(store, KEY) == []


def test_a_release_withdrawn_before_is_passed_over(
    store: Store, library: Library, imported: str
) -> None:
    store.withdraw("lib", 1, "operator:ada")  # its blobs are swept
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(loans=library.loans[1:]),
            library.layouts,
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], "operator:ada").withdrawn == (2,)


def test_the_person_s_numeric_key_is_erased_from_free_text_too(
    store: Store, library: Library
) -> None:
    """A patient number is the person's; a surrogate key below them stays in prose."""
    number = "104233"
    members = library.members.replace(KEY.encode(), number.encode())
    loans = tuple(tuple(number if v == KEY else v for v in loan) for loan in library.loans)
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(members, loans), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _propose(store, built.manifest.hash, "/fields/label", "x", f"member {number} asked, twice")
    _note(store, {"text": f"call member {number} about loan 2", "n": 104233, "loan": 2})
    with store.pin() as pin:
        rest = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(
                b"\n".join(line for line in members.split(b"\n") if number.encode() not in line),
                tuple(loan for loan in loans if number not in loan),
            ),
            library.layouts,
        )
        store.publish("lib", rest.manifest.hash, "operator:ada")
    assert erase(store, "lib", "members", [number], "operator:ada").terms == 3
    assert _proposals(store) == [("/fields/label", "x", f"member {MARK} asked, twice")]
    assert _details(store, "note") == [
        {"loan": MARK, "n": MARK, "text": f"call member {MARK} about loan 2"}
    ]


def test_a_key_is_typed_by_its_columns_datatypes(store: Store, library: Library) -> None:
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
        rest = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(loans=[loan for loan in library.loans if loan[0] != 3]),
            library.layouts,
        )
        store.publish("lib", rest.manifest.hash, "operator:ada")
    _note(store, {"loan": 3, "loans": [3, 4]})
    erased = erase(store, "lib", "loans", ["3"], "operator:ada")  # as an operator types it
    assert (erased.withdrawn, erased.terms) == ((1,), 1)
    assert _details(store, "note") == [{"loan": MARK, "loans": [MARK, 4]}]


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("loans", ["three"]),
        ("loans", [True]),
        ("members", ["m-99"]),
        ("members", [KEY, 1]),
        ("shelves", [KEY]),
    ],
)
def test_a_key_not_of_its_datatypes_or_held_by_no_release_is_refused(
    store: Store, library: Library, imported: str, table: str, key: list[Any]
) -> None:
    _without_the_member(store, library)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", table, key, "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"
    assert "redact_only" not in str(refused.value)  # no release is withdrawn
    assert [label.withdrawn for label in store.labels("lib")] == [False, False]
    assert _details(store, "erase") == []


def _without_the_members_table(store: Store, library: Library) -> None:
    """The library re-imported without its members table, and m-17's loans."""
    descriptors = [
        descriptor
        for descriptor in library.descriptors()
        if descriptor.id != "rel:loans.member"
        and descriptor.id != "members"
        and not descriptor.id.startswith("members.")
    ]
    sources = library.sources(loans=[loan for loan in library.loans if KEY not in loan])
    del sources["members"]
    layouts = {name: layout for name, layout in library.layouts.items() if name != "members"}
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, "operator:ada")


def test_a_latest_release_without_the_person_s_table_does_not_hold_them(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_members_table(store, library)
    assert erase(store, "lib", "members", [KEY], "operator:ada").withdrawn == (1,)
    assert _holds(store, KEY) == []


# --- Rows below the person, and rows that name them ------------------------------------------

Rows = tuple[tuple[Any, ...], ...]
CLUB: dict[str, tuple[tuple[str, ...], Rows]] = {
    "members": (("member_id", "referred_by"), (("m-1", None), (KEY, "m-1"), ("m-3", KEY))),
    "guests": (("guest_id", "host_id"), (("g-1", KEY),)),
    "loans": (("loan_id", "member_id"), ((2, KEY), (3, KEY), (4, "m-3"))),
    "renewals": (("renewal_id", "loan_id"), ((7, 2), (8, 4))),
    "staff": (("staff_id", "handled"), (("s-1", 3),)),
}
"""Members, who may refer one another, their guests (other people), their loans and the loans'
renewals, and the staff (other people again), each with the loan they last handled: m-17
referred m-3, hosts g-1, and has loans 2 and 3, loan 2 was renewed as 7, and s-1 handled 3."""
GONE: dict[str, Rows] = {
    "members": (("m-1", None), ("m-3", None)),
    "guests": (("g-1", None),),
    "loans": ((4, "m-3"),),
    "renewals": ((8, 4),),
    "staff": (("s-1", None),),
}
"""The club re-imported without m-17, their loans and renewals, and every mention of them."""


def _publish_club(
    store: Store,
    rows: Mapping[str, Rows] | None = None,
    *,
    status: str = "asserted",
    loan_ids: str = "integer",
) -> str:
    """The club, with ``rows`` in place of its own, and relationships of ``status``: a
    ``proposed`` one that a row left behind breaks is dropped by the gate (D230), as a re-import
    that no longer finds it would leave it out, so the release keeps the row without it. Loan
    ids, and the columns that name them, have the datatype ``loan_ids``."""

    def source(name: str) -> dict[str, Any]:
        return {"kind": "sheet", "name": name, "original_name": name}

    descriptors = [
        build.dataset(),
        build.table("members", ["member_id"], source=source("members")),
        build.column("members.member_id", "string", identifier=True),
        build.column("members.referred_by", "string"),
        build.table("guests", ["guest_id"], source=source("guests")),
        build.column("guests.guest_id", "string"),
        build.column("guests.host_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=source("loans")),
        build.column("loans.loan_id", loan_ids),
        build.column("loans.member_id", "string"),
        build.table("renewals", ["renewal_id"], role="event", source=source("renewals")),
        build.column("renewals.renewal_id", "integer"),
        build.column("renewals.loan_id", loan_ids),
        build.table("staff", ["staff_id"], source=source("staff")),
        build.column("staff.staff_id", "string"),
        build.column("staff.handled", loan_ids),
        build.relationship(
            "members", ["referred_by"], "members", ["member_id"], role="referrer", status=status
        ),
        build.relationship(
            "guests", ["host_id"], "members", ["member_id"], role="host", status=status
        ),
        build.relationship("loans", ["member_id"], "members", role="member", status=status),
        build.relationship("renewals", ["loan_id"], "loans", role="loan", status=status),
        build.relationship(
            "staff", ["handled"], "loans", ["loan_id"], role="handler", status=status
        ),
    ]
    tables = {
        name: (columns, (rows or {}).get(name, given)) for name, (columns, given) in CLUB.items()
    }
    sources = {name: TypedSource(columns, given) for name, (columns, given) in tables.items()}
    layouts = {
        name: Layout(name, tuple((column, column) for column in columns))
        for name, (columns, _) in tables.items()
    }
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, "operator:ada")
    return built.manifest.hash


def test_the_rows_below_the_person_are_theirs_at_every_depth_and_other_people_are_not(
    store: Store,
) -> None:
    _publish_club(store)
    _note(
        store,
        {"member": KEY, "referred": "m-3", "guest": "g-1", "loans": [2, 3, 4], "renewals": [7, 8]},
    )
    _publish_club(store, GONE)
    assert erase(store, "lib", "members", [KEY], "operator:ada").terms == 4  # m-17, 2, 3 and 7
    assert _details(store, "note") == [
        {"guest": "g-1", "loans": [MARK, MARK, 4], "member": MARK, "referred": "m-3",
         "renewals": [MARK, 8]}
    ]  # fmt: skip
    assert _holds(store, KEY) == []


LEFT = [
    ({"loans": ((2, KEY), (4, "m-3"))}, "rel:loans.member"),  # a loan of theirs
    ({"renewals": ((7, 2), (8, 4))}, "rel:renewals.loan"),  # a renewal of a loan of theirs
    ({"members": (("m-1", None), ("m-3", KEY))}, "rel:members.referrer"),  # a member naming them
    ({"guests": (("g-1", KEY),)}, "rel:guests.host"),  # a guest of theirs
    ({"staff": (("s-1", 3),)}, "rel:staff.handler"),  # staff who handled a loan of theirs
]
"""A re-import that leaves a row behind whose parent is gone, and the relationship it breaks."""


@pytest.mark.parametrize(("left", "broken"), LEFT)
def test_a_declared_relationship_never_leaves_a_row_below_the_person_in_a_release(
    store: Store, left: dict[str, Rows], broken: str
) -> None:
    _publish_club(store)
    with pytest.raises(BuildRefused) as refused:
        _publish_club(store, {**GONE, **left})
    assert [refusal.code for refusal in refused.value.refusals] == ["DANGLING_REFERENCE"]
    assert [label.label for label in store.labels("lib")] == [1]


@pytest.mark.parametrize(("left", "broken"), LEFT)
def test_a_latest_release_still_holding_a_row_below_the_person_or_naming_them_is_refused(
    store: Store, left: dict[str, Rows], broken: str
) -> None:
    """The relationship the row left behind breaks is dropped from the latest release, so only
    the earlier release's relationship finds the row."""
    _publish_club(store)
    latest = _publish_club(store, {**GONE, **left}, status="proposed")
    relationships = [relationship.id for relationship in store.load(latest).relationships]
    assert broken not in relationships
    assert len(relationships) == 4
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "ERASURE_BLOCKED"
    assert [label.withdrawn for label in store.labels("lib")] == [False, False]


def test_an_earlier_release_holding_only_rows_below_the_person_holds_them(store: Store) -> None:
    _publish_club(store)
    kept = {"loans": CLUB["loans"][1], "renewals": CLUB["renewals"][1]}
    second = _publish_club(store, {**GONE, **kept}, status="proposed")
    assert "rel:loans.member" not in [r.id for r in store.load(second).relationships]
    _publish_club(store, GONE)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1, 2), 4)
    assert _holds(store, KEY) == []


def test_rows_below_a_person_no_release_holds_are_no_one_s(store: Store) -> None:
    """With the person's row in no release, a relationship to it dangles in every release: the
    gate refuses a declared one and drops a proposed one, so no release says whose the loans
    are, and the key is held by none."""
    kept = {"loans": CLUB["loans"][1], "renewals": CLUB["renewals"][1]}
    with pytest.raises(BuildRefused) as refused:
        _publish_club(store, {**GONE, **kept})
    assert [refusal.code for refusal in refused.value.refusals] == ["DANGLING_REFERENCE"]
    _publish_club(store, {**GONE, **kept}, status="proposed")
    with pytest.raises(StoreRefused) as unheld:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert unheld.value.refusal.code == "INVALID_KEY"


def test_other_people_s_rows_that_name_the_person_hold_them_but_are_not_theirs(
    store: Store,
) -> None:
    _publish_club(store)
    _publish_club(store, {**GONE, "staff": CLUB["staff"][1]}, status="proposed")  # loan 3 gone
    _publish_club(store, GONE)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1, 2), 4)  # s-1 is no term


def test_a_release_only_naming_a_person_no_release_holds_holds_them(store: Store) -> None:
    _publish_club(store, {**GONE, "guests": CLUB["guests"][1]}, status="proposed")  # g-1's host
    _publish_club(store, GONE)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1,), 1)  # g-1 is no term


RENUMBERED: dict[str, Rows] = {
    **GONE,
    "loans": ((2, "m-3"), (4, "m-3")),
    "renewals": ((8, 4), (9, 2)),
}
"""The club re-imported without m-17, its loans numbered afresh: loan 2 is m-3's now, renewed as
9."""


def test_a_key_reused_by_a_re_import_names_another_person_s_row_there(store: Store) -> None:
    _publish_club(store)
    _note(store, {"renewals": [7, 9]})
    _publish_club(store, RENUMBERED)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1,), 4)  # m-17, 2, 3 and 7
    assert _details(store, "note") == [{"renewals": [MARK, 9]}]


def test_a_release_that_never_held_the_person_is_not_withdrawn_for_a_key_it_reuses(
    store: Store,
) -> None:
    _publish_club(store, RENUMBERED)
    _publish_club(store)
    _note(store, {"renewals": [7, 9]})
    _publish_club(store, GONE)
    assert erase(store, "lib", "members", [KEY], "operator:ada").withdrawn == (2,)
    assert [label.withdrawn for label in store.labels("lib")] == [False, True, False]
    assert _details(store, "note") == [{"renewals": [MARK, 9]}]


def test_a_row_left_dangling_on_a_row_of_theirs_in_a_later_release_is_theirs(
    store: Store,
) -> None:
    _publish_club(store)
    renewed = {**GONE, "renewals": ((8, 4), (9, 2))}  # 9 renews loan 2, gone
    _publish_club(store, renewed, status="proposed")
    _note(store, {"renewals": [7, 8, 9]})
    _publish_club(store, GONE)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1, 2), 5)  # m-17, 2, 3, 7 and 9
    assert _details(store, "note") == [{"renewals": [MARK, 8, MARK]}]


def test_a_row_left_dangling_is_theirs_whatever_datatype_a_re_import_gives_its_key(
    store: Store,
) -> None:
    """Loan 2 is the integer 2 in the first release and the string ``2`` in the second, whose
    renewal 7 names it: the keys are compared by their canonical strings."""
    _publish_club(store)
    renewed = {**GONE, "renewals": ((7, "2"), (8, "4"))}
    _publish_club(store, renewed, status="proposed", loan_ids="string")
    _publish_club(store, GONE)
    erased = erase(store, "lib", "members", [KEY], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1, 2), 4)  # m-17, 2, 3 and 7


def _publish_branches(
    store: Store, members: Rows, loans: Rows, *, status: str = "asserted"
) -> None:
    """Members, keyed by branch and number, with a phone number (an identifier), and their loans,
    whose foreign key names the key's columns the other way round, by a relationship of
    ``status`` (a ``proposed`` one that a loan left behind breaks is dropped, D230)."""

    def source(name: str) -> dict[str, Any]:
        return {"kind": "sheet", "name": name, "original_name": name}

    descriptors = [
        build.dataset(),
        build.table("members", ["branch", "number"], source=source("members")),
        build.column("members.branch", "string"),
        build.column("members.number", "integer"),
        build.column("members.phone", "integer", identifier=True),
        build.table("loans", ["loan_id"], role="event", source=source("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.number", "integer"),
        build.column("loans.branch", "string"),
        build.relationship(
            "loans",
            ["number", "branch"],
            "members",
            ["number", "branch"],
            role="member",
            status=status,
        ),
    ]
    tables = {
        "members": (("branch", "number", "phone"), members),
        "loans": (("loan_id", "number", "branch"), loans),
    }
    sources = {name: TypedSource(columns, rows) for name, (columns, rows) in tables.items()}
    layouts = {
        name: Layout(name, tuple((column, column) for column in columns))
        for name, (columns, _) in tables.items()
    }
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, "operator:ada")


BRANCH_MEMBERS: Rows = (("n-1", 17, 5550123), ("n-1", 3, 5))


def test_the_person_s_own_key_is_matched_in_the_order_a_relationship_names_its_columns(
    store: Store,
) -> None:
    _publish_branches(store, BRANCH_MEMBERS[1:], ((5, 17, "n-1"), (6, 3, "n-1")), status="proposed")
    _publish_branches(store, BRANCH_MEMBERS[1:], ((6, 3, "n-1"),))
    erased = erase(store, "lib", "members", ["n-1", "17"], "operator:ada")
    assert (erased.withdrawn, erased.terms) == ((1,), 3)  # n-1, 17 and the loan 5


def test_a_latest_release_holding_an_orphan_named_by_the_person_s_key_is_refused(
    store: Store,
) -> None:
    _publish_branches(store, BRANCH_MEMBERS, ((5, 17, "n-1"),))
    _publish_branches(store, BRANCH_MEMBERS[1:], ((5, 17, "n-1"),), status="proposed")
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", ["n-1", 17], "operator:ada")
    assert refused.value.refusal.code == "ERASURE_BLOCKED"


def test_the_person_s_numeric_identifier_values_are_erased_from_free_text(store: Store) -> None:
    _publish_branches(store, BRANCH_MEMBERS, ())
    _note(store, {"text": "phone 5550123 was lost"})
    _publish_branches(store, BRANCH_MEMBERS[1:], ())
    assert erase(store, "lib", "members", ["n-1", 17], "operator:ada").terms == 3
    assert _details(store, "note") == [{"text": f"phone {MARK} was lost"}]


def _publish_visits(store: Store, visits: Rows) -> None:
    """Visits, each of which may follow another: a table of events, not of people."""
    source = {"kind": "sheet", "name": "visits", "original_name": "visits"}
    descriptors = [
        build.dataset(),
        build.table("visits", ["visit_id"], role="event", source=source),
        build.column("visits.visit_id", "string"),
        build.column("visits.follows", "string"),
        build.relationship("visits", ["follows"], "visits", ["visit_id"], role="previous"),
    ]
    layouts = {"visits": Layout("visits", (("visit_id", "visit_id"), ("follows", "follows")))}
    sources = {"visits": TypedSource(("visit_id", "follows"), visits)}
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, "operator:ada")


def test_the_rows_below_never_reach_into_the_erased_row_s_own_table(store: Store) -> None:
    _publish_visits(store, (("v-1", None), ("v-2", "v-1")))
    _note(store, {"erased": "v-1", "kept": "v-2"})
    _publish_visits(store, (("v-2", None),))
    assert erase(store, "lib", "visits", ["v-1"], "operator:ada").terms == 1
    assert _details(store, "note") == [{"erased": MARK, "kept": "v-2"}]


def test_a_key_with_no_term_is_no_retry_of_a_waiting_erasure(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    with store.pin() as query:
        query.manifest(imported)
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        with pytest.raises(StoreRefused) as refused:
            erase(store, "lib", "members", [""], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"


def test_another_person_s_waiting_erasure_is_no_retry_of_a_key(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    with store.pin() as query:
        query.manifest(imported)
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        with pytest.raises(StoreRefused) as refused:
            erase(store, "lib", "members", ["m-99"], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"


def test_a_key_among_only_the_terms_below_a_waiting_erasure_is_no_retry_of_it(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    with store.pin() as query:
        query.manifest(imported)
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        with pytest.raises(StoreRefused) as refused:  # loan 2, m-17's, is a term below them
            erase(store, "lib", "loans", [2], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"


def test_a_retry_is_recognised_by_the_key_as_its_columns_type_it(
    store: Store, library: Library, imported: str
) -> None:
    rest = [loan for loan in library.loans if loan[0] != 3]
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(loans=rest), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _note(store, {"loan": 3})
    with store.pin() as query:
        query.manifest(imported)
        assert not erase(store, "lib", "loans", ["3"], "operator:ada").redacted
        again = erase(store, "lib", "loans", ["3.0"], "operator:ada")
        assert (again.withdrawn, again.terms, again.redacted) == ((), 1, False)
    assert _details(store, "note") == [{"loan": MARK}]


# --- A person only withdrawn releases held ------------------------------------------------------


def _withdrawn_at_once(store: Store, library: Library) -> None:
    """m-17 named in the app DB, then re-imported without and the release holding them
    withdrawn, before any erasure."""
    _note(store, {"member": KEY, "text": f"member {KEY} asked"})
    _without_the_member(store, library)
    store.withdraw("lib", 1, "operator:ada")


def test_a_person_only_a_withdrawn_release_held_is_refused_without_redact_only(
    store: Store, library: Library, imported: str
) -> None:
    _withdrawn_at_once(store, library)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"
    assert (
        "check the key; if only a release withdrawn earlier held the person, erase with "
        "redact_only" in str(refused.value)
    )
    assert _details(store, "erase") == []


def test_redact_only_is_refused_while_no_release_is_withdrawn(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {"member": "m-99"})
    _without_the_member(store, library)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", ["m-99"], "operator:ada", redact_only=True)
    assert refused.value.refusal.code == "INVALID_KEY"
    assert "check the key" not in str(refused.value)
    assert _details(store, "erase") == []
    assert _details(store, "note") == [{"member": "m-99"}]


def test_redact_only_erases_the_key_from_the_app_db_alone(
    store: Store, library: Library, imported: str
) -> None:
    _withdrawn_at_once(store, library)
    deleted: list[str] = []
    erased = erase(
        store, "lib", "members", [KEY], "operator:ada", uploads=deleted.append, redact_only=True
    )
    assert erased == Erased((), 1, True, False)
    assert deleted == ["lib"]
    assert _details(store, "note") == [{"member": MARK, "text": f"member {MARK} asked"}]
    assert _details(store, "erase") == [
        {"mode": "redact_only", "table": "members", "terms": 1, "withdrawn": []}
    ]
    assert _holds(store, KEY) == []


def test_redact_only_still_withdraws_a_release_that_holds_the_person(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    assert erase(store, "lib", "members", [KEY], "operator:ada", redact_only=True).withdrawn == (1,)
    assert _details(store, "erase")[0]["mode"] == "withdraw"  # type: ignore[index]


@pytest.mark.parametrize("key", [["three"], [3, 1], [""]])
def test_redact_only_types_the_key_by_the_latest_release_with_its_table(
    store: Store, library: Library, imported: str, key: list[Any]
) -> None:
    _withdrawn_at_once(store, library)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "loans", key, "operator:ada", redact_only=True)
    assert refused.value.refusal.code == "INVALID_KEY"
    assert _details(store, "erase") == []


def test_redact_only_takes_the_key_as_given_when_no_live_release_has_its_table(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {"text": f"member {KEY} asked"})
    _without_the_members_table(store, library)
    store.withdraw("lib", 1, "operator:ada")
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"
    assert "erase with redact_only" in str(refused.value)
    erased = erase(store, "lib", "members", [KEY], "operator:ada", redact_only=True)
    assert erased == Erased((), 1, True, False)
    assert _details(store, "note") == [{"text": f"member {MARK} asked"}]
    assert _details(store, "erase") == [
        {"mode": "redact_only", "table": "members", "terms": 1, "withdrawn": []}
    ]


def test_redact_only_erases_a_numeric_key_as_its_column_types_it(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {"loan": 3, "text": "loan 3.0"})
    rest = [loan for loan in library.loans if loan[0] != 3]
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(loans=rest), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    store.withdraw("lib", 1, "operator:ada")
    erased = erase(store, "lib", "loans", ["3.0"], "operator:ada", redact_only=True)
    assert erased.terms == 1
    assert _details(store, "note") == [{"loan": MARK, "text": f"loan {MARK}.0"}]


# --- Failures after the withdrawals ------------------------------------------------------------


def _failing(dataset: str) -> None:
    raise OSError("the upload area is unavailable")


def test_an_erasure_whose_upload_deletion_fails_is_finished_by_erasing_again(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {"loans": [2, 3], "member": KEY})
    _without_the_member(store, library)
    with pytest.raises(OSError, match="upload area"):
        erase(store, "lib", "members", [KEY], "operator:ada", uploads=_failing)
    assert store.resolve("lib", 1).status == "withdrawn"  # and its blobs are swept
    deleted: list[str] = []
    again = erase(store, "lib", "members", [KEY], "operator:ada", uploads=deleted.append)
    assert again == Erased((), 3, True, False)
    assert deleted == ["lib"]
    assert _details(store, "note") == [{"loans": [MARK, MARK], "member": MARK}]
    assert _details(store, "erase") == [
        {"mode": "withdraw", "table": "members", "terms": 3, "withdrawn": [1]}
    ]
    assert _holds(store, KEY) == []
    with pytest.raises(StoreRefused) as refused:  # nothing is left to do
        erase(store, "lib", "members", [KEY], "operator:ada", uploads=deleted.append)
    assert refused.value.refusal.code == "INVALID_KEY"


def test_an_upload_area_left_to_delete_is_no_retry_of_another_key_and_is_reported(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    with pytest.raises(OSError, match="upload area"):
        erase(store, "lib", "members", [KEY], "operator:ada", uploads=_failing)
    for table, key in [("members", "m-99"), ("books", "nope")]:
        with pytest.raises(StoreRefused) as refused:
            erase(store, "lib", table, [key], "operator:ada")
        assert refused.value.refusal.code == "INVALID_KEY"
    again = erase(store, "lib", "members", [KEY], "operator:ada")  # without uploads
    assert (again.redacted, again.uploads_pending) == (True, True)
    assert _holds(store, KEY) == []


def test_a_later_erasure_reports_an_upload_area_still_to_delete(
    store: Store, library: Library, imported: str
) -> None:
    _without_the_member(store, library)
    with pytest.raises(OSError, match="upload area"):
        erase(store, "lib", "members", [KEY], "operator:ada", uploads=_failing)
    members = b"\n".join(line for line in library.members.split(b"\n") if b"m-3," not in line)
    with store.pin() as pin:
        built = store.import_release(
            pin,
            "lib",
            library.descriptors(),
            library.sources(
                members.replace(b"m-17,", b"m-0,"),
                [loan for loan in library.loans if loan[1] == "m-1"],
            ),
            library.layouts,
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    erased = erase(store, "lib", "members", ["m-3"], "operator:ada")
    assert (erased.withdrawn, erased.uploads_pending) == ((2,), True)


def test_an_erasure_whose_upload_deletion_fails_is_redacted_when_the_store_opens_again(
    tmp_path: Path, library: Library
) -> None:
    root = tmp_path / "data"
    store = Store(root)
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _note(store, {"loans": [2, 3], "member": KEY})
    _without_the_member(store, library)
    with pytest.raises(OSError, match="upload area"):
        erase(store, "lib", "members", [KEY], "operator:ada", uploads=_failing)
    store.close()  # the process ends before it redacts
    reopened = Store(root)
    try:
        assert _holds(reopened, KEY) == []
        assert _details(reopened, "note") == [{"loans": [MARK, MARK], "member": MARK}]
        with pytest.raises(StoreRefused) as refused:  # the key is nowhere to recognise it by
            erase(reopened, "lib", "members", [KEY], "operator:ada")
        assert refused.value.refusal.code == "INVALID_KEY"
        assert "upload area" in str(refused.value)
        deleted: list[str] = []
        again = erase(
            reopened,
            "lib",
            "members",
            [KEY],
            "operator:ada",
            uploads=deleted.append,
            redact_only=True,
        )
        assert (again.withdrawn, again.redacted, again.uploads_pending) == ((), True, False)
        assert deleted == ["lib"]
    finally:
        reopened.close()


def test_erasure_holds_the_store_s_lock_from_its_checks_to_its_withdrawals(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_the_member(store, library)
    opening: list[threading.Thread] = []
    latest = store.latest

    def open_session() -> None:
        with store.db.transaction() as db:
            store.db.open_session(db, "lib", "h" * 64, imported, store.now(), "operator:ada")

    def checking(dataset: str) -> Any:
        if not opening:
            opening.append(threading.Thread(target=open_session))
            opening[0].start()
            opening[0].join(0.2)
            assert opening[0].is_alive()  # the session waits for the erasure
        return latest(dataset)

    monkeypatch.setattr(store, "latest", checking)
    assert erase(store, "lib", "members", [KEY], "operator:ada").withdrawn == (1,)
    opening[0].join()
    assert any(session.open for session in store.db.sessions("lib"))


def test_erasure_decides_on_the_releases_published_once_it_holds_the_store_s_lock(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_the_member(store, library)
    load = store.load
    loaded: list[str] = []

    def loading(manifest: str) -> Any:
        if not loaded:  # a re-import that still holds the member is published meanwhile
            with store.pin() as pin:
                built = store.import_release(
                    pin,
                    "lib",
                    library.descriptors(),
                    library.sources(loans=library.loans[1:]),
                    library.layouts,
                )
                store.publish("lib", built.manifest.hash, "operator:ada")
        loaded.append(manifest)
        return load(manifest)

    monkeypatch.setattr(store, "load", loading)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "ERASURE_BLOCKED"
    assert len(loaded) == 3  # the release published meanwhile, loaded under the lock


def test_a_release_withdrawn_while_the_erasure_loads_is_passed_over(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_the_member(store, library)
    load = store.load

    def loading(manifest: str) -> Any:
        if manifest == imported:
            store.withdraw("lib", 1, "operator:ada")
        return load(manifest)

    monkeypatch.setattr(store, "load", loading)
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", "members", [KEY], "operator:ada")
    assert refused.value.refusal.code == "INVALID_KEY"


# --- Redaction ----------------------------------------------------------------------------------


def test_tokens_are_bounded_by_letters_digits_and_marks_of_any_script_and_match_exactly() -> None:
    terms = Terms(["Jos", KEY, "Ann"])
    kept = ["José", "Anné", "M-17", "Жm-17", "m-17٣"]
    assert [terms.text(text) for text in kept] == kept
    assert Terms(["Jose"]).text("Jose\u0301") == "Jose\u0301"  # é as e and a combining accent
    marked = ["कि", "กิ", "محمدٌ"]  # a Devanagari vowel sign, a Thai one, an Arabic tanwin
    assert [Terms(["क", "ก", "محمد"]).text(text) for text in marked] == marked
    assert Terms(["क", "محمد"]).text("क और محمد") == f"{MARK} और {MARK}"
    assert [terms.text(text) for text in ["_m-17_", "x.m-17", "m-17-b", "Ann Lee", "(Jos)"]] == [
        f"_{MARK}_",
        f"x.{MARK}",
        f"{MARK}-b",
        f"{MARK} Lee",
        f"({MARK})",
    ]


def test_the_longest_term_is_erased_first() -> None:
    assert Terms(["m-1"], ["m-1-a"]).text("m-1-a and m-1") == f"{MARK} and {MARK}"


def test_numbers_below_the_person_are_erased_only_as_whole_values() -> None:
    terms = Terms(["104233"], ["2"])
    assert terms.text("page 2 of 104233") == f"page 2 of {MARK}"
    assert terms.json({"n": 2, "m": "2", "k": 104233}) == {"k": MARK, "m": MARK, "n": MARK}


def test_object_keys_that_are_terms_are_erased_and_kept_apart(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {KEY: "a", "2": "b", "loan 3": "c"})
    _without_the_member(store, library)
    erase(store, "lib", "members", [KEY], "operator:ada")
    assert _details(store, "note") == [{MARK: "b", f"{MARK} (2)": "a", "loan 3": "c"}]


def test_only_the_store_s_own_entries_keep_their_labels_and_manifests(
    store: Store, library: Library, imported: str
) -> None:
    _note(store, {"label": KEY, "labels": [2], "manifest": KEY})
    _without_the_member(store, library)
    erase(store, "lib", "members", [KEY], "operator:ada")
    assert _details(store, "note") == [{"label": MARK, "labels": [MARK], "manifest": MARK}]
    assert [detail["label"] for detail in _details(store, "publish")] == [1, 2]  # type: ignore[index]


def test_another_dataset_s_rows_are_left_alone(
    store: Store, library: Library, imported: str
) -> None:
    with store.db.transaction() as db:
        store.db.audit(db, store.now(), "other", "operator:ada", "note", {"about": KEY})
        store.db.add_proposal(
            db,
            dataset="other",
            release=imported,
            descriptor="members.member_id",
            pointer="/fields/label",
            value=KEY,
            proposer="agent:helper",
            evidence=KEY,
            at=store.now(),
        )
    _without_the_member(store, library)
    erase(store, "lib", "members", [KEY], "operator:ada")
    assert _details(store, "note") == [{"about": KEY}]
    assert _proposals(store) == [("/fields/label", KEY, KEY)]


def test_a_pin_on_a_blob_of_the_withdrawn_release_alone_holds_off_the_redaction(
    store: Store, library: Library, imported: str
) -> None:
    _mention(store, imported)
    _without_the_member(store, library)
    members = store.manifest(imported).table("members")
    assert members is not None
    with store.pin() as operation:
        operation.add(members.hash)
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
    assert _holds(store, KEY) == []


def test_redact_says_whether_its_own_redaction_ran(store: Store, imported: str) -> None:
    with store.db.transaction() as db:
        store.request_redaction(db, "lib", Terms(["x"]), [])  # free, but not run yet
    with store.pin() as query:
        query.manifest(imported)
        assert not store.redact("lib", Terms(["y"]), [imported])
        assert list(store.pending_redactions("lib")) == [2]
        assert store.redact("lib", Terms(["z"]), [])
    assert store.pending_redactions("lib") == {}


def test_a_checkpoint_a_reader_kept_from_finishing_is_retried_at_most_once_a_second(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mention(store, imported)
    _without_the_member(store, library)
    now = [monotonic()]
    monkeypatch.setattr("aibi.core.store.store.monotonic", lambda: now[0])
    reader = sqlite3.connect(store.db.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM audit").fetchone()
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        checkpoints: list[bool] = []
        checkpoint = store.db.checkpoint

        def counted(wait: float = 0.1) -> bool:
            checkpoints.append(checkpoint(wait))
            return checkpoints[-1]

        monkeypatch.setattr(store.db, "checkpoint", counted)
        for _ in range(20):
            with store.pin():
                pass
        assert checkpoints == []
        now[0] += CHECKPOINT_RETRY
        with store.pin():
            pass
        assert checkpoints == [False]
    finally:
        reader.close()


def test_a_checkpoint_left_unfinished_is_retried_when_the_store_opens(
    tmp_path: Path, library: Library
) -> None:
    root = tmp_path / "data"
    store = Store(root)
    with store.pin() as pin:
        built = store.import_release(
            pin, "lib", library.descriptors(), library.sources(), library.layouts
        )
        store.publish("lib", built.manifest.hash, "operator:ada")
    _mention(store, built.manifest.hash)
    _without_the_member(store, library)
    reader = sqlite3.connect(root / "app.db")
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM audit").fetchone()
        assert not erase(store, "lib", "members", [KEY], "operator:ada").redacted
        store.close()
        reader.commit()  # the reader stays connected, so closing checkpointed nothing
        assert _wal_holds(root, KEY)
        Store(root).close()
        assert not _wal_holds(root, KEY)
    finally:
        reader.close()


def _wal_holds(root: Path, text: str) -> bool:
    wal = root / "app.db-wal"
    return wal.exists() and text.encode() in wal.read_bytes()
