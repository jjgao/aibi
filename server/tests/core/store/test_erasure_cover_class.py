"""The class properties of erasure through coverage and scope tables (D408), over histories of
four releases (plus a hand withdrawal, and ``redact_only``), each family varying one shape of the
problem. The oracles read the releases' table blobs alone, never the erasure's code:

- (i) only rows withdraw: a completed erasure withdraws exactly the releases whose rows hold the
  person;
- (ii) no completion while a live release that does not hold the person lists the key, by D223's
  orphan rule read over the link's declared parent columns (a listing whose parent row exists
  there is not one), keys below the person taken from the live releases only (R6), scope
  columns only where some release declared the mapping; the stated residuals are carved out:
  a renamed table (TREN, checked by its remedy instead) and rows D223 does not follow;
- (iii) the code: ``INVALID_KEY`` exactly when the key is not valid, not trusted and nothing
  waits; ``ERASURE_BLOCKED`` exactly when (ii) would fail or the latest holds rows;
- (iv) liveness: every refusal E is cleared by the remedy its text names;
- (v) identity: the person's rows, ``holds``, ``terms`` and ``holding`` are those of the module
  before D408 (``base_erasure``), with the registry's graph present.

The families are those of the reviewers' brute forces (KEY, BELOW, SCOPE, COMP, BRR, TYPED, TREN,
SURR); a sample of each runs here, and ``AIBI_COVER_FULL=1`` runs every history of the product.
"""

import itertools
import os
import re
import tempfile
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.core.store import base_erasure
from tests.core.store import cover_fixtures as fx
from tests.core.store.cover_fixtures import ADA, M2, Release, ab_text, lay_of, picked, src

from aibi.core.engine import build
from aibi.core.store import erasure
from aibi.core.store.build import BuildRefused
from aibi.core.store.sources import SourceValue, TypedSource
from aibi.core.store.store import Store

FULL = os.environ.get("AIBI_COVER_FULL") == "1"
EXAMPLES = 14


DATATYPE = {"s": "string", "c": "category", "l": "list<category>", "i": "integer", "n": "number"}
"""The datatypes a cover column takes in the families, by code (D408's class: every datatype
a key's text reads as; a list's items are values)."""


def keyrel(key: str, holds: bool, lists: bool, declare: bool, edt: str = "s") -> Release:
    """``edt`` the cover column's datatype: a list holds one cell of its items."""
    members = ["m-1", "m-17"] if holds else ["m-1"]
    enrolled = ["m-1", "m-17"] if lists else ["m-1"]
    if edt == "l":
        enrolled = ["m-1; m-17"] if lists else ["m-1; m-3"]
    d = [
        build.dataset(),
        build.table("members", [key], source=src("members")),
        build.column(f"members.{key}", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", DATATYPE[edt]),
        build.relationship("visits", ["member_id"], "members", [key]),
    ]
    if declare:
        parents = {"table": "enrolled", "parent_columns": {"member_id": key}}
        d.append(build.coverage("rel:visits.member_id", parents))
    so = {
        "members": TypedSource((key,), tuple((m,) for m in members)),
        "visits": TypedSource(("visit_id", "member_id"), ((1, "m-1"),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def belowrel(
    key: str,
    holds: bool,
    lists: bool,
    declare: bool,
    *,
    other: bool = False,
    number: int = 2,
    erode: bool = False,
) -> Release:
    """Loans keyed by ``key``; with ``erode``, their member column is ``mem`` and no
    relationship follows it (a parent row D223 does not follow)."""
    member = "mem" if erode else "member_id"
    members = ["m-1", "m-17"] if holds else ["m-1"]
    loans: list[tuple[int, str]] = [(1, "m-1")]
    if holds:
        loans.append((number, "m-17"))
    if other:
        loans.append((2, "m-1"))
    audited = [1, 2] if lists else [1]
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", [key], role="event", source=src("loans")),
        build.column(f"loans.{key}", "integer"),
        build.column(f"loans.{member}", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.loan_id", "integer"),
        build.table("audited", None, role="coverage", source=src("audited")),
        build.column("audited.loan_id", "integer"),
        build.relationship("fines", ["loan_id"], "loans", [key]),
    ]
    if not erode:
        d.append(build.relationship("loans", ["member_id"], "members"))
    if declare:
        parents = {"table": "audited", "parent_columns": {"loan_id": key}}
        d.append(build.coverage("rel:fines.loan_id", parents))
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource((key, member), tuple(loans)),
        "fines": TypedSource(("fine_id", "loan_id"), ()),
        "audited": TypedSource(("loan_id",), tuple((a,) for a in audited)),
    }
    return d, so, lay_of(so)


def comprel(
    keys: tuple[str, str], holds: bool, other: bool, lists: bool, declare: bool = False
) -> Release:

    members = [(5, 5)] + ([(2, 1)] if holds else []) + ([(1, 2)] if other else [])
    enrolled = [(5, 5)] + ([(2, 1)] if lists else [])
    return fx.ab(members, enrolled, keys=keys, declare=declare)


def scoperel(rel: bool, cover: bool, lists: bool, holds: bool) -> Release:
    members = ["p-1", "p-5"] if holds else ["p-1"]
    listed = [("t1", "p-1"), ("t2", "p-5")] if lists else [("t1", "p-1")]
    return picked("string", members, [("t1", "p-1")], listed, rel=rel, cover=cover)


def loanrel(
    ldt: str, holds: bool, other: bool, adt: str, listed: SourceValue | None, declare: bool = False
) -> Release:
    """Loans of datatype ``ldt`` (``s`` or ``i``), the person's loan ``"002"`` or 2; another
    member's loan ``"2"`` or 2 (``other``); ``audited`` of datatype ``adt`` listing ``listed``."""
    members = ["m-1", "m-17"] if holds else ["m-1"]
    s = ldt == "s"
    loans: list[tuple[SourceValue, str]] = [("001" if s else 1, "m-1")]
    if holds:
        loans.append(("002" if s else 2, "m-17"))
    if other:
        loans.append(("2" if s else 2, "m-1"))
    audited: list[SourceValue] = ["001" if adt in "sc" else 1]
    if adt == "l":
        audited = ["001; " + str(listed) if listed is not None else "001"]
    elif listed is not None:
        audited.append(listed)
    return fx.lib(
        members,
        loans,
        audited,
        declare=declare,
        ldt="string" if s else "integer",
        adt=DATATYPE[adt],
    )


def trenrel(name: str, holds: bool, lists: bool, declare: bool) -> Release:
    members = ["m-1", "m-17"] if holds else ["m-1"]
    enrolled = ["m-1", "m-17"] if lists else ["m-1"]
    d = [
        build.dataset(),
        build.table(name, ["member_id"], source=src(name)),
        build.column(f"{name}.member_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
        build.relationship("visits", ["member_id"], name),
    ]
    if declare:
        parents = {"table": "enrolled", "parent_columns": {"member_id": "member_id"}}
        d.append(build.coverage("rel:visits.member_id", parents))
    so = {
        name: TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(("visit_id", "member_id"), ((1, "m-1"),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def surrrel(with_members: bool, mholds: bool, sholds: bool, lists: bool, declare: bool) -> Release:
    return fx.surr(
        [1, 2] if mholds else [1],
        [1, 2, 3] if sholds else [1, 3],
        [1, 2] if lists else [1],
        with_members=with_members,
        declare=declare,
    )


# --- The oracle ----------------------------------------------------------------------------------


def cells(store: Store, manifest: str, table: str, column: str) -> list[str]:
    """A column's values as the blob holds them, every item of a list cell one of them."""
    return fx.items_of(fx.table_columns(store, manifest, table).get(column, []))


def pairs_of(store: Store, manifest: str, table: str, c1: str, c2: str) -> set[tuple[Any, Any]]:
    """Each row's (c1, c2) values, every pair of their items where a cell is a list."""
    columns = fx.table_columns(store, manifest, table)
    if c1 not in columns or c2 not in columns:
        return set()
    found: set[tuple[Any, Any]] = set()
    for first, second in zip(columns[c1], columns[c2], strict=True):
        found.update(itertools.product(fx.items_of([first]), fx.items_of([second])))
    return found


@dataclass
class Family:
    """A family of histories: R1, then two releases drawn from ``dims`` (made by ``make``), then
    ``last``; a hand withdrawal of none or one; ``redact_only`` or not. ``rows`` and ``lists``
    are the oracle; ``fresh`` the clean re-import a remedy publishes."""

    table: str
    key: list[SourceValue]
    first: Release
    dims: list[tuple[Any, ...]]
    make: Callable[..., Release]
    last: Release
    rows: Callable[[Store, str], bool]
    lists: Callable[[Store, str, "History"], bool]
    hands: Sequence[int | None] = (None, 1, 2, 3)
    modes: Sequence[bool] = (False, True)
    fresh: Callable[[int], Release] | None = None
    declared_by: Callable[["History"], bool] = lambda history: True


@dataclass
class History:
    second: tuple[Any, ...]
    third: tuple[Any, ...]
    hand: int | None
    redact_only: bool
    person_loans: set[str] = field(default_factory=set[str])
    """The person's loans in the live releases, by a relationship D223 follows."""
    some_int: bool = False
    """Whether a live release types the loans' key or the cover column as an integer."""


def _person_loans(store: Store, manifests: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for manifest in manifests:
        columns = fx.table_columns(store, manifest, "loans")
        if "member_id" not in columns:  # ``mem``, which no relationship follows
            continue
        member = columns["member_id"]
        for key in ("loan_id", "lid"):
            if key in columns:
                found |= {str(k) for k, m in zip(columns[key], member, strict=True) if m == "m-17"}
    return found


def _fresh_members(release: Release, n: int) -> Release:
    """A clean re-import: the last release with one new member, so its manifest is new."""
    d, so, lay = release
    table = so["members"]
    first = table.rows[0][0]
    extra = (
        tuple(90 + n for _ in table.rows[0])
        if isinstance(first, int)
        else tuple(f"zz-{n}" for _ in table.rows[0])
    )
    return d, {**so, "members": TypedSource(table.columns, (*table.rows, extra))}, lay


FAMILIES: dict[str, Family] = {
    "KEY": Family(
        "members",
        ["m-17"],
        keyrel("member_id", True, True, True),
        list(
            itertools.product(
                ("member_id", "mid"), (True, False), (True, False), (True, False), "scl"
            )
        ),
        keyrel,
        keyrel("mid", False, False, False),
        lambda s, m: any("m-17" in cells(s, m, "members", c) for c in ("member_id", "mid")),
        lambda s, m, h: "m-17" in {v.strip() for v in cells(s, m, "enrolled", "member_id")},
    ),
    "BELOW": Family(
        "members",
        ["m-17"],
        belowrel("loan_id", True, True, True),
        list(itertools.product(("loan_id", "lid"), (True, False), (True, False), (True, False))),
        belowrel,
        belowrel("lid", False, False, False),
        lambda s, m: "m-17" in cells(s, m, "members", "member_id"),
        lambda s, m, h: "2" in cells(s, m, "audited", "loan_id") and "2" in h.person_loans,
    ),
    "BRR": Family(
        "members",
        ["m-17"],
        belowrel("loan_id", True, True, True),
        [
            x
            for x in itertools.product(
                ("loan_id", "lid"),
                (True, False),
                (2, 7),
                (True, False),
                (True, False),
                (False, True),
            )
            if (x[1] or x[2] == 2) and not (x[5] and x[0] == "lid")
        ],
        lambda key, holds, number, other, lists, erode: belowrel(
            key, holds, lists, False, other=other, number=number, erode=erode
        ),
        belowrel("lid", False, False, False),
        lambda s, m: "m-17" in cells(s, m, "members", "member_id"),
        # D223's orphan rule by the declared column, loans.loan_id: a listing of 2 counts unless
        # the release has a loans row whose loan_id is 2 (the eroded IGNORED-ROW included).
        lambda s, m, h: (
            "2" in cells(s, m, "audited", "loan_id")
            and "2" in h.person_loans
            and "2" not in cells(s, m, "loans", "loan_id")
        ),
    ),
    "COMP": Family(
        "members",
        [2, 1],
        comprel(("a", "b"), True, False, True, declare=True),
        list(
            itertools.product((("a", "b"), ("p", "q")), (True, False), (True, False), (True, False))
        ),
        comprel,
        comprel(("p", "q"), False, False, False),
        lambda s, m: any(
            ("2", "1") in pairs_of(s, m, "members", *c) for c in (("a", "b"), ("p", "q"))
        ),
        lambda s, m, h: ("2", "1") in pairs_of(s, m, "enrolled", "x", "y"),
    ),
    "SCOPE": Family(
        "members",
        ["p-5"],
        scoperel(True, False, False, True),
        list(itertools.product((True, False), (True, False), (True, False), (True, False))),
        scoperel,
        scoperel(False, False, False, False),
        lambda s, m: "p-5" in cells(s, m, "members", "member_id"),
        lambda s, m, h: "p-5" in cells(s, m, "picked", "picker"),
        declared_by=lambda h: h.second[1] or h.third[1],
    ),
}


def _typed_family(first: str, last: str) -> Family:
    """TYPED: datatypes of a below key and of the cover column varying across the releases,
    the latest's included (``last``)."""
    opts: list[tuple[str, SourceValue | None]] = [
        ("s", None),
        ("s", "002"),
        ("s", "2"),
        ("i", None),
        ("i", 2),
        ("n", 2.0),
        ("c", "002"),
        ("c", "2"),
        ("l", "002"),
        ("l", "2"),
    ]
    dims = [
        (ldt, holds, other, adt, listed)
        for ldt, holds, other in itertools.product("si", (True, False), (True, False))
        if not (ldt == "i" and holds and other)
        for adt, listed in opts
    ]
    return Family(
        "members",
        ["m-17"],
        loanrel(first, True, False, first, "002" if first == "s" else 2, declare=True),
        dims,
        loanrel,
        loanrel(last[0], False, False, last[1], None),
        lambda s, m: "m-17" in cells(s, m, "members", "member_id"),
        _typed_lists,
        hands=(None, 1),
        fresh=lambda n: loanrel(last[0], False, False, last[1], None),
    )


def _typed_lists(store: Store, manifest: str, history: History) -> bool:
    """(ii-typed): a listing (a cell, or a list's item) is integer-equal to the person's live
    loan where some live release types the link's child or parent column as a number,
    canonical-equal otherwise; and an orphan by the declared column, exactly."""
    parents = {str(v) for v in cells(store, manifest, "loans", "loan_id")}
    for value in cells(store, manifest, "audited", "loan_id"):
        if value in parents:  # the orphan rule: exact, as stored
            continue
        for loan in history.person_loans:
            listed = value.strip()  # a listing: as the forming side reads it
            if listed == loan or (history.some_int and _int(listed) == _int(loan)):
                return True
    return False


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


DAY_LISTED = {
    "date": "2024-01-02",
    "datetime": "2024-01-02T00:00:00Z",
    "string": "2024-01-02",
    "category": "2024-01-02",
    "list<category>": "2024-01-01; 2024-01-02",
}


def dayrel(holds: bool, lists: bool, declare: bool, edt: str, extra: int = 0) -> Release:
    """DAY: the person is a day (a key of datatype date); ``enrolled.day``, of datatype ``edt``,
    lists it as that datatype writes it (a datetime at midnight UTC)."""
    days = ["2024-01-01", "2024-01-02"] if holds else ["2024-01-01"]
    if extra:  # a clean re-import of a new manifest, for a remedy
        days.append(f"2023-01-{extra:02d}")
    listed = ["2024-01-01"]
    if lists:
        listed = [DAY_LISTED[edt]] if edt == "list<category>" else ["2024-01-01", DAY_LISTED[edt]]
    elif edt == "datetime":
        listed = ["2024-01-01T00:00:00Z"]
    d = [
        build.dataset(),
        build.table("days", ["day"], source=src("days")),
        build.column("days.day", "date"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.day", "date"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.day", edt),
        build.relationship("visits", ["day"], "days"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits.day", {"table": "enrolled", "parent_columns": {"day": "day"}}
            )
        )
    so = {
        "days": TypedSource(("day",), tuple((day,) for day in days)),
        "visits": TypedSource(("visit_id", "day"), ((1, "2024-01-01"),)),
        "enrolled": TypedSource(("day",), tuple((e,) for e in listed)),
    }
    return d, so, lay_of(so)


def _day(text: str) -> str:
    """A date or a datetime at midnight UTC as its day, as D290 reads them alike."""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T]00:00:00(?:\+00:00|Z)?)?", text.strip())
    return match[1] if match else text


FAMILIES["DAY"] = Family(
    "days",
    ["2024-01-02"],
    dayrel(True, True, True, "date"),
    list(itertools.product((True, False), (True, False), (True, False), sorted(DAY_LISTED))),
    dayrel,
    dayrel(False, False, False, "date"),
    lambda s, m: "2024-01-02" in {_day(v) for v in cells(s, m, "days", "day")},
    lambda s, m, h: (
        "2024-01-02" in {_day(v) for v in cells(s, m, "enrolled", "day")}
        and "2024-01-02" not in {_day(v) for v in cells(s, m, "days", "day")}
    ),
    fresh=lambda n: dayrel(False, False, False, "date", extra=n + 1),
)


for _first, _last in itertools.product("si", ("ii", "ss")):
    FAMILIES[f"TYPED-{_first}-{_last}"] = _typed_family(_first, _last)


def histories(name: str) -> list[History]:
    family = FAMILIES[name]
    return [
        History(a, b, hand, ro)
        for a, b, hand, ro in itertools.product(
            family.dims, family.dims, family.hands, family.modes
        )
        if not (ro and hand is None)
    ]


def _attempt(store: Store, family: Family, redact_only: bool) -> tuple[str, Any, str]:
    try:
        done = erasure.erase(store, "lib", family.table, family.key, ADA, redact_only=redact_only)
    except fx.StoreRefused as refused:
        return str(refused.refusal.code), None, fx.short(fx.text_of(refused))
    return "done", done, ""


def _remedy(store: Store, family: Family, history: History, text: str) -> bool:
    """Clear an E by the remedy its text names: re-import from a source without the key when it
    names the latest, withdraw every other release it names; then erase again, with
    ``redact_only`` if that is refused as an unknown key."""
    fresh = family.fresh or (lambda n: _fresh_members(family.last, n))
    for step in range(5):
        named = [int(n) for n in text.split("]")[0].split("[")[1].split(",") if n.strip()]
        latest = store.latest("lib")
        assert latest is not None
        current = {lab.label for lab in store.labels("lib") if lab.manifest == latest.manifest}
        if any(n in current for n in named):
            fx.publish(store, fresh(step))
        for n in named:
            if n not in current and not store.labels("lib")[n - 1].withdrawn:
                store.withdraw("lib", n, ADA)
        code, _, text = _attempt(store, family, history.redact_only)
        if code == "done":
            return True
        if code == "INVALID_KEY":
            return _attempt(store, family, True)[0] == "done"
        if not text.startswith("E "):
            return False
    return False


def _identity(store: Store, family: Family) -> list[str]:
    live = erasure._live(store, "lib")  # pyright: ignore[reportPrivateUsage]
    before = base_erasure._Person(family.table, live, family.key)  # pyright: ignore[reportPrivateUsage]
    now = erasure._person(store, "lib", family.table, live, family.key)  # pyright: ignore[reportPrivateUsage]
    bad: list[str] = []
    if before.rows != now.rows:
        bad.append("rows")
    if any(before.holds(m) != now.holds(m) for m in live):
        bad.append("holds")
    if before.terms().dumps() != now.terms().dumps():
        bad.append("terms")
    return bad


def _judge(name: str, history: History) -> None:
    family = FAMILIES[name]
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        try:
            for release in (
                family.first,
                family.make(*history.second),
                family.make(*history.third),
                family.last,
            ):
                fx.publish(store, release)
        except BuildRefused:
            return  # the gate refuses the history
        if history.hand is not None:
            store.withdraw("lib", history.hand, ADA)
        live = [lab for lab in store.labels("lib") if not lab.withdrawn]
        history.person_loans = _person_loans(store, [lab.manifest for lab in live])
        if name.startswith("TYPED"):
            types: set[str | None] = set()
            for lab in live:
                release = store.load(lab.manifest)
                types |= {
                    release.datatype("audited", "loan_id"),
                    release.datatype("loans", "loan_id"),
                }
            history.some_int = bool({"integer", "number"} & types)
        assert _identity(store, family) == [], "(v)"
        latest = store.latest("lib")
        assert latest is not None
        rows = {lab.label for lab in live if family.rows(store, lab.manifest)}
        declared = family.declared_by(history)
        lists = {
            lab.label for lab in live if declared and family.lists(store, lab.manifest, history)
        }
        withdrawn_before = any(lab.withdrawn for lab in store.labels("lib"))
        latest_rows = any(lab.label in rows for lab in live if lab.manifest == latest.manifest)
        if not rows and not (history.redact_only and withdrawn_before):
            expected = "INVALID_KEY"
        elif latest_rows or (lists - rows):
            expected = "ERASURE_BLOCKED"
        else:
            expected = "done"
        code, done, text = _attempt(store, family, history.redact_only)
        if code == "done":
            by_label = {lab.label: lab.manifest for lab in store.labels("lib")}
            assert {by_label[n] for n in done.withdrawn} == {by_label[n] for n in rows}, "(i)"
            left = {lab.label for lab in store.labels("lib") if not lab.withdrawn}
            assert not (left & (lists - rows)), "(ii)"
        assert code == expected, "(iii)"
        if code == "ERASURE_BLOCKED" and text.startswith("E "):
            assert _remedy(store, family, history, text), "(iv)"


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_the_class_properties_hold_over_a_sample_of_each_family(name: str) -> None:
    if FULL:
        for history in histories(name):
            _judge(name, history)
        return

    @settings(
        max_examples=EXAMPLES,
        deadline=None,
        database=None,
        derandomize=True,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(st.sampled_from(histories(name)))
    def check(history: History) -> None:
        _judge(name, history)

    check()


# --- TREN and SURR: a renamed table, and surrogate keys with stale links ----------------------


def _tren(second: tuple[Any, ...], third: tuple[Any, ...], hand: int | None) -> str:
    """TREN: erase under the latest's name; a completion while a live release lists the key
    under the old name is the stated residual (R6), which ``redact_only`` under the old name
    must see and its remedy clear."""
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        try:
            for release in (
                trenrel("members", True, True, True),
                trenrel(*second),
                trenrel(*third),
                trenrel("patrons", False, False, False),
            ):
                fx.publish(store, release)
        except BuildRefused:
            return "skipped"
        if hand is not None:
            store.withdraw("lib", hand, ADA)
        live = [lab for lab in store.labels("lib") if not lab.withdrawn]
        rows = {
            lab.label
            for lab in live
            if "m-17" in cells(store, lab.manifest, "patrons", "member_id")
        }
        named = {
            lab.label
            for lab in live
            if "m-17" in cells(store, lab.manifest, "members", "member_id")
        }
        lists = {
            lab.label
            for lab in live
            if "m-17" in cells(store, lab.manifest, "enrolled", "member_id")
        }
        family = Family(
            "patrons",
            ["m-17"],
            trenrel("members", True, True, True),
            [],
            trenrel,
            trenrel("patrons", False, False, False),
            lambda s, m: False,
            lambda s, m, h: False,
        )
        assert _identity(store, family) == []
        code, done, text = _attempt(store, family, False)
        if code != "done":
            if text.startswith("E "):
                assert _remedy(store, family, History(second, third, hand, False), text)
            return code
        by_label = {lab.label: lab.manifest for lab in store.labels("lib")}
        assert {by_label[n] for n in done.withdrawn} == {by_label[n] for n in rows}
        left = {lab.label for lab in store.labels("lib") if not lab.withdrawn} & (lists - named)
        if left:
            old = Family(
                "members",
                ["m-17"],
                family.first,
                [],
                trenrel,
                family.last,
                lambda s, m: False,
                lambda s, m, h: False,
            )
            code, _, text = _attempt(store, old, True)
            assert code == "ERASURE_BLOCKED"
            assert text.startswith("E ")
            assert _remedy(store, old, History(second, third, hand, True), text)
            return "residual"
        return "done"


TREN_DIMS = list(
    itertools.product(("members", "patrons"), (True, False), (True, False), (True, False))
)


@settings(max_examples=EXAMPLES, deadline=None, database=None, derandomize=True)
@given(st.sampled_from(TREN_DIMS), st.sampled_from(TREN_DIMS), st.sampled_from([None, 1, 2]))
def test_a_renamed_table_completes_only_as_the_residual_its_remedy_clears(
    second: tuple[Any, ...], third: tuple[Any, ...], hand: int | None
) -> None:
    _tren(second, third, hand)


def test_the_renamed_table_residual_is_seen_by_its_remedy() -> None:
    """A history the sample may miss: R2 keeps ``members`` and lists the key, R3 renames it."""
    assert _tren(("members", False, True, False), ("patrons", True, False, False), 1) == "residual"


SURR_DIMS = [
    x
    for x in itertools.product(
        (True, False), (True, False), (True, False), (True, False), (True, False)
    )
    if x[0] or not (x[1] or x[4])
]


@settings(max_examples=EXAMPLES, deadline=None, database=None, derandomize=True)
@given(st.sampled_from(SURR_DIMS), st.sampled_from(SURR_DIMS), st.sampled_from([None, 1]))
def test_surrogate_keys_with_stale_links_give_no_false_positive(
    second: tuple[Any, ...], third: tuple[Any, ...], hand: int | None
) -> None:
    """No coverage points into staff: erasing staff 2 never refuses with E, whatever stale link
    into a dropped members table lists 2."""
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        try:
            for release in (
                surrrel(True, True, True, True, True),
                surrrel(*second),
                surrrel(*third),
                surrrel(False, False, False, False, False),
            ):
                fx.publish(store, release)
        except BuildRefused:
            return
        if hand is not None:
            store.withdraw("lib", hand, ADA)
        assert not fx.attempt(store, "staff", [2]).startswith("ERASURE_BLOCKED E")


# --- One reader of a cover value (PR #88's review round 2, M1) ----------------------------------

LOAN = "lé-2"
"""The person's loan (a cover of one column, into loans)."""
VISIT = ("sé-1", "vé-2")
"""The person's visit (a cover of two columns, into visits keyed (k0, k1))."""
SPELLED: dict[str, Callable[[str], str]] = {
    "none": lambda value: value,
    "space": lambda value: " " + value,
    "nfd": lambda value: unicodedata.normalize("NFD", value),
    "zwsp": lambda value: value[:1] + "​" + value[1:],
}
"""How a cover cell may spell a value that D290 reads alike: as written, after a delimited
list's space, in NFD, and with a format character."""


def _read(text: str) -> str:
    """Text as the forming side reads it (D290), written here apart from the code: without
    format characters, in NFC, without surrounding spaces."""
    kept = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFC", kept).strip()


def _reader_history(
    arity: int, kind: str, shape: str, spelling: str, parented: bool
) -> list[Release]:
    """R1 holds the person (m-17) and declares the coverage; R2, the latest, drops them and
    lists their key once, its first position's value spelled ``spelling`` in a ``shape`` cell
    (a scalar, or a list's second item); with ``parented``, R2 gives that reading, as written,
    to another member as their loan or visit. ``kind`` K2 lists the visit's values in the
    other order."""
    dt = "list<category>" if shape == "list" else "string"
    if arity == 1:
        spelled = SPELLED[spelling](LOAN)
        cell = f"l-1;{spelled}" if shape == "list" else spelled
        first = fx.lib(
            M2,
            [("l-1", "m-1"), (LOAN, "m-17")],
            ["l-1", LOAN],
            declare=True,
            ldt="string",
            adt="string",
        )
        loans = [("l-1", "m-1"), *([(spelled, "m-1")] if parented else [])]
        return [first, fx.lib(["m-1"], loans, [cell], ldt="string", adt=dt)]
    values = VISIT if kind == "K1" else (VISIT[1], VISIT[0])
    spelled = SPELLED[spelling](values[0])
    cell = f"zz;{spelled}" if shape == "list" else spelled
    other = [("a", "b", "m-1")]
    first = fx.visits_text(M2, [*other, (*VISIT, "m-17")], [("a", "b"), VISIT], declare=True)
    visits = [*other, *([(spelled, values[1], "m-1")] if parented else [])]
    return [first, fx.visits_text(["m-1"], visits, [("a", "b"), (cell, values[1])], xdt=dt)]


def _reader_lists(store: Store, manifest: str, arity: int) -> bool:
    """The oracle, from the blobs: some reading of a cover row (one value per position, a list's
    items each a value) is no parent key as stored, and reads as the person's key (K1, in
    order) or its values in any order (K2)."""
    if arity == 1:
        parents = {(v,) for v in cells(store, manifest, "loans", "loan_id")}
        rows = [[fx.items_of([c])] for c in fx.table_columns(store, manifest, "audited")["loan_id"]]
        wanted = {(LOAN,)}
    else:
        visits = fx.table_columns(store, manifest, "visits")
        parents = set(zip(visits["k0"], visits["k1"], strict=True))
        enrolled = fx.table_columns(store, manifest, "enrolled")
        rows = [
            [fx.items_of([x]), fx.items_of([y])]
            for x, y in zip(enrolled["x"], enrolled["y"], strict=True)
        ]
        wanted = {VISIT, (VISIT[1], VISIT[0])}
    return any(
        reading not in parents and tuple(_read(value) for value in reading) in wanted
        for row in rows
        for reading in itertools.product(*row)
    )


READER = [
    (arity, kind, shape, spelling, parented)
    for arity, kind in ((1, "K1"), (2, "K1"), (2, "K2"))
    for shape in ("scalar", "list")
    for spelling in SPELLED
    for parented in (False, True)
]


@pytest.mark.parametrize(
    ("arity", "kind", "shape", "spelling", "parented"),
    [pytest.param(*case, id="-".join(map(str, case))) for case in READER],
)
def test_one_reader_of_a_cover_value(
    arity: int, kind: str, shape: str, spelling: str, parented: bool
) -> None:
    """The class of round 2's M1: spelling x cell shape x arity x K1/K2, each with and without a
    parent row that holds the reading as written. The erasure refuses exactly when the oracle
    finds a listing (a scalar cell and a list's item read alike, the parent rule exact), and a
    reading spelled as written is passed over exactly when a parent row holds it."""
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        for release in _reader_history(arity, kind, shape, spelling, parented):
            fx.publish(store, release)
        latest = store.latest("lib")
        assert latest is not None
        lists = _reader_lists(store, latest.manifest, arity)
        if spelling == "none":
            assert lists == (not parented)
        got = fx.attempt(store, "members", ["m-17"])
        assert got.startswith("ERASURE_BLOCKED E [2]" if lists else "done (1,)")


# --- K1 is not K2 (PR #88's review round 3, m1) -------------------------------------------------

K1_A, K1_B, K1_C = "sé-1", "vé-2", "vé-3"
"""The parts of the person's key: the visits (A, B), and with an anchor at position 1 (A, C) too."""
LISTING = ("a", "b")
"""The key's parts of the members the coverage lists, none the person's."""


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def _k1_cell(shape: str, spelling: str) -> tuple[str, str]:
    """The cell that lists the key's first part, and that part as a parent row would hold it
    as written: a scalar, or a list's second item; ``mixed``, a list of the part as written and
    after the delimiter's space; ``stored``, the part as written where the person's key is
    stored in NFD."""
    if spelling == "mixed":
        return f"{K1_A}; {K1_A}", K1_A
    spelled = K1_A if spelling == "stored" else SPELLED[spelling](K1_A)
    return (f"zz;{spelled}" if shape == "list" else spelled), spelled


def _k1_history(
    kind: str, anchor: int, shape: str, spelling: str, parented: bool, arity: int = 2
) -> list[fx.Step]:
    """K1 (the person's key over the coverage's columns) is no live release's key.

    ``below``: R1 (visits keyed (k0, k1), declares the coverage) and R2 (re-keyed by vid) hold
    the person's visits, R1 is withdrawn by hand, and R3, the latest, lists one in a ``shape``
    cell, spelled ``spelling``, among a row of only its first part; with ``anchor`` 1 the person
    has a second visit that shares its k0, so each K1 key is anchored at position 1; with
    ``parented`` R3 gives the reading, as written, to another member's visit.

    ``own``: the members are keyed (a, b) in R1 (declares) and R2 (lists, and holds the same
    way a parent row), re-keyed by ``n`` in R3, which holds the person (a, b kept as columns),
    and in the latest R4.

    ``arity`` 1 (``below`` only, no anchor): loans keyed by ``loan_id`` in R1 (declares the
    coverage by it), re-keyed by ``lid`` in R2, which holds the person and keeps ``loan_id`` as a
    column (the review's probe P8: only K1 types the cover's one column), and R3 lists the
    person's loan id as above."""
    dt = "list<category>" if shape == "list" else "string"
    cell, written = _k1_cell(shape, spelling)
    if arity == 1:
        assert kind == "below"
        person = _nfd(K1_A) if spelling == "stored" else K1_A
        return [
            fx.loans_keyed(
                M2, [("a", "m-1"), (person, "m-17")], ["a"], rekeyed=False, declare=True
            ),
            fx.loans_keyed(M2, [(1, "a", "m-1"), (2, person, "m-17")], ["a"], rekeyed=True),
            fx.withdraw(1),
            fx.loans_keyed(
                ["m-1"],
                [(1, "a", "m-1"), *([(3, written, "m-1")] if parented else [])],
                ["a", cell],
                rekeyed=True,
                xdt=dt,
            ),
        ]
    target = K1_C if anchor else K1_B
    partial = (f"{K1_A};zz" if shape == "list" else K1_A, "q9")
    rows = [LISTING, partial, (cell, target)]
    keep = _nfd if spelling == "stored" else str
    if kind == "own":
        mine = (keep(K1_A), keep(K1_B))
        parents = [(written, K1_B)] if parented else []
        return [
            ab_text([LISTING, mine], [LISTING]),
            ab_text([LISTING, *parents], rows, declare=False, xdt=dt),
            ab_text([("n-1", *LISTING), ("x-9", *mine)], [], declare=False, rekeyed=True),
            ab_text([("n-1", *LISTING)], [], declare=False, rekeyed=True),
        ]
    mine = [(keep(K1_A), keep(K1_B)), *([(keep(K1_A), keep(K1_C))] if anchor else [])]
    other = ("a", "b", "m-1")
    return [
        fx.visits_keyed(
            M2, [other, *[(*key, "m-17") for key in mine]], [LISTING], rekeyed=False, declare=True
        ),
        fx.visits_keyed(
            M2,
            [(1, *other), *[(2 + 2 * i, *key, "m-17") for i, key in enumerate(mine)]],
            [LISTING],
            rekeyed=True,
        ),
        fx.withdraw(1),
        fx.visits_keyed(
            ["m-1"],
            [(1, *other), *([(3, written, target, "m-1")] if parented else [])],
            rows,
            rekeyed=True,
            xdt=dt,
        ),
    ]


def _k1_lists(store: Store, kind: str, anchor: int, arity: int = 2) -> bool:
    """The oracle, from the blobs: some reading of a row of the coverage that lists the key (one
    value per position, a list's items each a value) reads as the person's key over the
    coverage's columns, and is, in the latest release, no parent key as stored, or, for the
    person's own table, in a release that does not hold them, is any."""
    own = kind == "own"
    manifest = store.labels("lib")[1 if own else -1].manifest
    table, columns = ("members", ("a", "b")) if own else ("visits", ("k0", "k1"))
    cover = ("x", "y")
    if arity == 1:
        table, columns, cover = "loans", ("loan_id",), ("x",)
    held = fx.table_columns(store, manifest, table)
    parents = set(zip(*(held[column] for column in columns), strict=True))
    enrolled = fx.table_columns(store, manifest, "enrolled")
    rows = [
        [fx.items_of([cell]) for cell in cells]
        for cells in zip(*(enrolled[column] for column in cover), strict=True)
    ]
    wanted = {(K1_A,)} if arity == 1 else {(K1_A, K1_B), *([(K1_A, K1_C)] if anchor else [])}
    return any(
        (own or reading not in parents) and tuple(_read(value) for value in reading) in wanted
        for row in rows
        for reading in itertools.product(*row)
    )


K1_CASES = [
    (kind, anchor, shape, spelling, parented, arity)
    for arity, kind, anchor in (
        (2, "below", 0),
        (2, "below", 1),
        (2, "own", 0),
        (1, "below", 0),
    )
    for shape in ("scalar", "list")
    for spelling in (*SPELLED, "stored", "mixed")
    if spelling != "mixed" or shape == "list"
    for parented in (False, True)
]


@pytest.mark.parametrize(
    ("kind", "anchor", "shape", "spelling", "parented", "arity"),
    [pytest.param(*case, id="-".join(map(str, case))) for case in K1_CASES],
)
def test_a_key_over_the_coverage_s_columns_that_is_no_live_release_s_key_is_read_alike(
    kind: str, anchor: int, shape: str, spelling: str, parented: bool, arity: int
) -> None:
    """The class of round 3's m1, paths read by K1 alone (where K2 is empty): the coverage's
    declared columns are no longer the key of the table they point into. Kind (a table below
    the person re-keyed, or the person's own) x the anchor's position in the person's keys
    (0, or 1 where their keys share a first part) x scalar or listed x spelling (as written,
    after a delimiter's space, NFD, with a format character, the person's key stored in NFD, a
    list of both the part as written and after the space) x a parent row that holds the
    reading as written, x arity (2, or 1: the review's probe P8, a table below the person
    re-keyed by another column, which kept the old key as a column of one, so that only K1 types
    the cover's column). The erasure refuses exactly when the oracle finds a listing, and a row
    that holds only some parts of a key is never one."""
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        fx.run(store, _k1_history(kind, anchor, shape, spelling, parented, arity))
        lists = _k1_lists(store, kind, anchor, arity)
        if kind == "own" or spelling == "mixed":
            assert lists  # a parent row never passes the person's own old key over
        elif spelling in ("none", "stored"):
            assert lists == (not parented)
        got = fx.attempt(store, "members", ["x-9" if kind == "own" else "m-17"])
        label = 2 if kind == "own" else 3  # the release that lists: R2, or the latest
        row = 2 if arity == 1 else 3  # the row of the listing: the second, or the third
        assert got.startswith(
            f"ERASURE_BLOCKED E [{label}] [(1, ({row},))]" if lists else "done (2,)"
        )


# --- The order of a composite key (PR #88's extra review round, M1; issue #95) -------------------


def _orders() -> list[tuple[int, list[str], list[str], dict[str, str]]]:
    """Widths 2 and 3 x every order of the key x every order the coverage's columns sort into
    (the child columns ``x0``, ``x1``, ... map to the parent columns in that order, which a
    renaming of the coverage's columns changes and nothing about the data)."""
    found = []
    for width in (2, 3):
        columns = ["a", "b", "c"][:width]
        orders = [list(order) for order in itertools.permutations(columns)]
        for key in orders:
            for sigma in orders:
                found.append((width, columns, key, {f"x{i}": sigma[i] for i in range(width)}))
    return found


def _order_id(case: tuple[int, list[str], list[str], dict[str, str]]) -> str:
    return f"w{case[0]}-key-{''.join(case[2])}-cover-{''.join(case[3].values())}"


@pytest.mark.parametrize("mode", ["withdraw", "redact_only"])
@pytest.mark.parametrize("case", _orders(), ids=_order_id)
def test_an_ordinary_erasure_completes_for_every_order_of_the_coverage_s_columns(
    case: tuple[int, list[str], list[str], dict[str, str]], mode: str
) -> None:
    """PERM-LEGIT for every coverage column order: no release is re-keyed, and another member's
    key is a cyclic permutation of the person's (a collision of keys, which is no listing of the
    person). The latest holds only the other member and lists them; the erasure completes
    whatever order the coverage's columns sort into (a refusal that follows that order alone
    would let a renaming of the coverage's columns change an erasure's outcome)."""
    width, columns, key, mapping = case
    values = [f"v{i}" for i in range(width)]
    person = dict(zip(columns, values, strict=True))
    other = dict(zip(columns, values[1:] + values[:1], strict=True))
    given = [person[column] for column in key]
    steps: list[fx.Step] = [
        fx.wide_members(columns, key, mapping, [person, other], [person, other], declare=True),
        fx.wide_members(columns, key, mapping, [other], [other], declare=False),
    ]
    if mode == "redact_only":
        steps.append(fx.withdraw(1))
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        fx.run(store, steps)
        got = fx.attempt(
            store, "members", given, **({"redact_only": True} if mode == "redact_only" else {})
        )
    # What it withdrew (the terms redacted depend on the width): the release that holds the
    # person, or, once that was withdrawn by hand, none.
    assert got.partition(" terms=")[0] == ("done ()" if mode == "redact_only" else "done (1,)")


def _reorderings() -> list[tuple[int, list[str], list[str], list[str], dict[str, str]]]:
    return [
        (width, columns, first, second, mapping)
        for width, columns, first, mapping in _orders()
        for second in (list(order) for order in itertools.permutations(columns))
        if second != first
    ]


@pytest.mark.parametrize(
    "case",
    _reorderings(),
    ids=lambda case: (
        f"w{case[0]}-{''.join(case[2])}-to-{''.join(case[3])}-cover-{''.join(case[4].values())}"
    ),
)
def test_a_change_of_the_key_s_order_alone_is_not_detected_as_a_re_key_issue_95(
    case: tuple[int, list[str], list[str], list[str], dict[str, str]],
) -> None:
    """A residual, pinned (issue #95): R1 declares the coverage and keys the members by one
    order, R2, the latest, by another, and holds and lists the person's row; R1 withdrawn by
    hand, the erasure by R1's key order completes with ``redact_only`` whatever order the
    coverage's columns sort into, as the code stands: a link's column pairs are sorted by the
    coverage's own column names, and the registry records no key order. The real fix records
    each release's key tuple, and turns this into a refusal."""
    _, columns, first, second, mapping = case
    person = {column: f"p{column}" for column in columns}
    other = {column: f"o{column}" for column in columns}
    steps: list[fx.Step] = [
        fx.wide_members(columns, first, mapping, [other, person], [other], declare=True),
        fx.wide_members(columns, second, mapping, [other, person], [person], declare=False),
        fx.withdraw(1),
    ]
    with tempfile.TemporaryDirectory() as tmp, fx.opened(Path(tmp) / "data") as store:
        fx.run(store, steps)
        got = fx.attempt(store, "members", [person[column] for column in first], redact_only=True)
    assert got.partition(" terms=")[0] == "done ()"  # the latest holds and lists the row
