"""Fixtures of the erasure tests through coverage and scope tables (D408): small datasets of a
library (members, loans, fines and an ``audited`` coverage of fines' loans; visits and an
``enrolled`` coverage of members), an orchard (trees, pickings and ``picked``), staff and their
rounds, each published as one release of the dataset ``lib``. A step is a release to import and
publish, or a callable run on the store. ``attempt`` erases and gives the outcome in a short
form that names labels and positions, as the refusal texts do; ``Oracle`` reads what the live
releases hold from their table blobs alone, independently of the erasure's code."""

import contextlib
import itertools
import logging
import re
import traceback
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from aibi.core.engine import build
from aibi.core.schema.descriptors import Descriptor, TableDescriptor
from aibi.core.schema.output import TextSegment
from aibi.core.store import parquet
from aibi.core.store.build import Layout
from aibi.core.store.erasure import erase
from aibi.core.store.sources import SourceValue, TypedSource
from aibi.core.store.store import Store, StoreRefused

Release = tuple[list[Descriptor], dict[str, TypedSource], dict[str, Layout]]
Step = Release | Callable[[Store], None]
ADA = "operator:ada"
M2 = ["m-1", "m-17"]


def clock() -> Callable[[], datetime]:
    ticks = itertools.count()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: start + timedelta(seconds=next(ticks))


@contextlib.contextmanager
def opened(root: Path) -> Iterator[Store]:
    store = Store(root, clock=clock())
    try:
        yield store
    finally:
        store.close()


def src(name: str) -> dict[str, str]:
    return {"kind": "sheet", "name": name, "original_name": name}


def lay_of(sources: dict[str, TypedSource]) -> dict[str, Layout]:
    return {k: Layout(k, tuple((c, c) for c in v.columns)) for k, v in sources.items()}


def publish(store: Store, release: Release) -> str:
    descriptors, sources, layouts = release
    with store.pin() as pin:
        built = store.import_release(pin, "lib", descriptors, sources, layouts)
        store.publish("lib", built.manifest.hash, ADA)
    return built.manifest.hash


def run(store: Store, steps: Sequence[Step]) -> None:
    for step in steps:
        if callable(step):
            step(store)
        else:
            publish(store, step)


def withdraw(label: int) -> Callable[[Store], None]:
    def step(store: Store) -> None:
        store.withdraw("lib", label, ADA)

    return step


def erased(table: str, key: Sequence[SourceValue], **given: Any) -> Callable[[Store], None]:
    """A step: an erasure, whatever its outcome."""

    def step(store: Store) -> None:
        with contextlib.suppress(StoreRefused):
            erase(store, "lib", table, key, ADA, **given)

    return step


# --- Outcomes -----------------------------------------------------------------------------------

B1 = (
    "The latest release still holds the row, a row below it, or a row whose foreign key names "
    "one of them: re-import from a source without them first"
)


def hits_of(text: str) -> list[tuple[int, tuple[int, ...]]]:
    """``table 2, rows 1, 4; table 5, row 2`` as positions."""
    found: list[tuple[int, tuple[int, ...]]] = []
    for part in text.split("; "):
        matched = re.fullmatch(r"table (\d+), rows? ([\d, ]+)", part.strip())
        assert matched, part
        found.append((int(matched[1]), tuple(int(row) for row in matched[2].split(", "))))
    return found


def short(message: str) -> str:
    """A refusal's text in short: ``B1`` (``+cover`` and where), ``E`` with the labels and where
    (sorted) and ``NOTE`` for the collision wording, ``F`` with the labels; any other as is."""
    if message.startswith(B1):
        rest = message[len(B1) :]
        if not rest:
            return "B1"
        lead = ". It also names the key in a coverage or scope table: "
        assert rest.startswith(lead), rest
        return f"B1 +cover {sorted(hits_of(rest[len(lead) :]))}"
    if message.startswith("A live release names the key"):
        listed = message.split("scope table: ", 1)[1].split(". A coverage", 1)[0]
        groups = re.findall(r"((?:@\d+(?:, )?)+) \(([^)]*)\)", listed)
        labels = sorted(int(n) for group, _ in groups for n in re.findall(r"@(\d+)", group))
        hits = sorted(hit for _, inside in groups for hit in hits_of(inside))
        note = " NOTE" if "The hit may be a collision" in message else ""
        return f"E {labels} {hits}{note}"
    if message.startswith("The link registry is incomplete"):
        return "F " + str(sorted(int(n) for n in re.findall(r"@(\d+)", message)))
    return message


def text_of(refused: StoreRefused) -> str:
    """A refusal's text, its segments joined."""
    return "".join(
        segment.text if isinstance(segment, TextSegment) else segment.data
        for segment in refused.refusal.message
    )


def chained(error: BaseException) -> list[str]:
    """Everything an exception shows, and every exception in its chain (``__cause__`` and
    ``__context__``): its ``str``, its ``repr`` and its formatted line (the type and message a
    traceback ends with, not its source lines, which the test's own literals would fill)."""
    found: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found += [str(current), repr(current), *traceback.format_exception_only(current)]
        pending += [e for e in (current.__cause__, current.__context__) if e is not None]
    return found


def logged(records: Sequence[logging.LogRecord]) -> list[str]:
    """Everything the log records can show: each one formatted (its message, its ``exc_text``
    and its stack), its message alone, and its exception (``exc_info``) with its whole chain,
    which the request-protection middleware logs with a traceback."""
    formatter = logging.Formatter()
    found: list[str] = []
    for record in records:
        found += [formatter.format(record), record.getMessage(), record.exc_text or ""]
        if record.exc_info is not None and record.exc_info[1] is not None:
            found += chained(record.exc_info[1])
    return found


def attempt(store: Store, table: str, key: Sequence[SourceValue], **given: Any) -> str:
    """The outcome of an erasure: ``done (labels) terms=n``, or ``CODE short-text``."""
    try:
        done = erase(store, "lib", table, key, ADA, **given)
    except StoreRefused as refused:
        code = str(refused.refusal.code)
        return f"{code} {short(text_of(refused))}"
    return f"done {done.withdrawn} terms={done.terms}"


def state(store: Store) -> dict[str, Any]:
    """What an erasure changes: labels and their statuses, audit entries, waiting redactions,
    the upload area's flag."""
    with store.lock:
        connection = store.db.connection
        audit = connection.execute("SELECT count(*) FROM audit").fetchone()[0]
        pending = connection.execute("SELECT count(*) FROM pending_redactions").fetchone()[0]
    return {
        "labels": [(label.label, label.withdrawn) for label in store.labels("lib")],
        "audit": audit,
        "pending": pending,
        "upload_pending": store.db.upload_pending("lib"),
    }


# --- The oracle: what the live releases' blobs hold ---------------------------------------------


def variants(text: str) -> set[bytes]:
    """A term as stored bytes may spell it: NFC, NFD, and with zero-width spaces between its
    characters."""
    forms = {unicodedata.normalize("NFC", text), unicodedata.normalize("NFD", text)}
    forms.add("​".join(text))
    return {form.encode() for form in forms}


Text = str | tuple[str, ...] | None
"""A cell as the oracle reads it: its value's text, a list's items' texts, or ``None``."""


def table_columns(store: Store, manifest: str, table: str) -> dict[str, list[Text]]:
    """A table of a release as its blob holds it: each column's cells as text, a list cell as
    the texts of its items (never the list's own ``str``)."""
    for entry in store.manifest(manifest).tables:
        if entry.id == table:
            return {
                column.name: [_cell_text(value) for value in column.values]
                for column in parquet.read(store.blobs.read(entry.hash))
            }
    return {}


def _cell_text(value: object) -> Text:
    if value is None:
        return None
    if isinstance(value, list | tuple):
        items = cast(list[object] | tuple[object, ...], value)
        return tuple(_text(item) for item in items if item is not None)
    return _text(value)


def _text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def items_of(cells: Sequence[Text]) -> list[str]:
    """The values of a column's cells, every item of a list cell one of them, as stored (an
    item of a delimited list keeps the spaces after the delimiter; ``.strip()`` reads it as
    the forming side of D408 does)."""
    found: list[str] = []
    for cell in cells:
        if isinstance(cell, tuple):
            found.extend(cell)
        elif cell is not None:
            found.append(cell)
    return found


def live_holds(store: Store, text: str, *, raw: bool = True) -> list[int]:
    """The live labels whose table blobs hold ``text``: as a whole cell or a list's item,
    decoded, or (``raw``) in the bytes as stored, in any of its ``variants``."""
    found: list[int] = []
    for label in store.labels("lib"):
        if label.withdrawn:
            continue
        held = False
        for entry in store.manifest(label.manifest).tables:
            data = store.blobs.read(entry.hash)
            if raw and any(form in data for form in variants(text)):
                held = True
            for column in parquet.read(data):
                values = items_of([_cell_text(value) for value in column.values])
                if text in {value.strip() for value in values}:
                    held = True
        if held:
            found.append(label.label)
    return found


# --- Datasets -----------------------------------------------------------------------------------


def covlib(
    members: Sequence[str],
    loans: Sequence[tuple[Any, str]],
    audited: Sequence[Any],
    *,
    declare: bool = True,
    loan_dtype: str = "integer",
    aud_dtype: str = "integer",
    fines: Sequence[tuple[Any, ...]] = (),
    fines_rel: bool = True,
    fine_dtype: str | None = None,
) -> Release:
    """Members, their loans, fines of loans, and ``audited``, the coverage of fines' loans."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", loan_dtype),
        build.column("loans.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.loan_id", fine_dtype or loan_dtype),
        build.table("audited", None, role="coverage", source=src("audited")),
        build.column("audited.loan_id", aud_dtype),
        build.relationship("loans", ["member_id"], "members"),
    ]
    if fines_rel:
        d.append(build.relationship("fines", ["loan_id"], "loans"))
    if declare:
        d.append(
            build.coverage(
                "rel:fines.loan_id", {"table": "audited", "parent_columns": {"loan_id": "loan_id"}}
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "member_id"), tuple(loans)),
        "fines": TypedSource(("fine_id", "loan_id"), tuple(fines)),
        "audited": TypedSource(("loan_id",), tuple((a,) for a in audited)),
    }
    return d, so, lay_of(so)


def lib(
    members: Sequence[str],
    loans: Sequence[tuple[Any, str]],
    audited: Sequence[Any],
    *,
    key: str = "loan_id",
    declare: bool = False,
    ldt: str = "integer",
    adt: str = "integer",
) -> Release:
    """``covlib`` with the loans' key column named ``key`` (renamed, say)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", [key], role="event", source=src("loans")),
        build.column(f"loans.{key}", ldt),
        build.column("loans.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.loan_id", ldt),
        build.table("audited", None, role="coverage", source=src("audited")),
        build.column("audited.loan_id", adt),
        build.relationship("loans", ["member_id"], "members"),
        build.relationship("fines", ["loan_id"], "loans", [key]),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:fines.loan_id", {"table": "audited", "parent_columns": {"loan_id": key}}
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource((key, "member_id"), tuple(loans)),
        "fines": TypedSource(("fine_id", "loan_id"), ()),
        "audited": TypedSource(("loan_id",), tuple((a,) for a in audited)),
    }
    return d, so, lay_of(so)


def libr(
    members: Sequence[str], loans: Sequence[tuple[Any, str]], audited: Sequence[Any]
) -> Release:
    """``audited``'s column renamed ``loan_ref`` (a string) and declared as the coverage."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.loan_id", "integer"),
        build.table("audited", None, role="coverage", source=src("audited")),
        build.column("audited.loan_ref", "string"),
        build.relationship("loans", ["member_id"], "members"),
        build.relationship("fines", ["loan_id"], "loans"),
        build.coverage(
            "rel:fines.loan_id", {"table": "audited", "parent_columns": {"loan_ref": "loan_id"}}
        ),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "member_id"), tuple(loans)),
        "fines": TypedSource(("fine_id", "loan_id"), ()),
        "audited": TypedSource(("loan_ref",), tuple((a,) for a in audited)),
    }
    return d, so, lay_of(so)


def mem(
    members: Sequence[str], loans: Sequence[tuple[Any, str]], audited: Sequence[Any]
) -> Release:
    """Loans whose member column is renamed ``mem``, with no relationship on it (D223's
    erosion)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.mem", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.loan_id", "integer"),
        build.table("audited", None, role="coverage", source=src("audited")),
        build.column("audited.loan_id", "integer"),
        build.relationship("fines", ["loan_id"], "loans"),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "mem"), tuple(loans)),
        "fines": TypedSource(("fine_id", "loan_id"), ()),
        "audited": TypedSource(("loan_id",), tuple((a,) for a in audited)),
    }
    return d, so, lay_of(so)


def enrol(
    members: Sequence[str],
    visits: Sequence[tuple[Any, str]],
    enrolled: Sequence[str],
    *,
    declare: bool = True,
    edt: str = "string",
) -> Release:
    """Members, their visits, and ``enrolled``, the coverage of visits' members, its column of
    datatype ``edt`` (a list's items written ``a; b``)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", edt),
        build.relationship("visits", ["member_id"], "members"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits.member_id",
                {"table": "enrolled", "parent_columns": {"member_id": "member_id"}},
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(("visit_id", "member_id"), tuple(visits)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def bare(enrolled: Sequence[str]) -> Release:
    """No members table: visits and ``enrolled`` alone."""
    d = [
        build.dataset(),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
    ]
    so = {
        "visits": TypedSource(("visit_id", "member_id"), ((1, "m-1"),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def renamed(members: Sequence[str], enrolled: Sequence[str]) -> Release:
    """Members keyed by ``mid``; ``enrolled`` undeclared."""
    d = [
        build.dataset(),
        build.table("members", ["mid"], source=src("members")),
        build.column("members.mid", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
    ]
    so = {
        "members": TypedSource(("mid",), tuple((m,) for m in members)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def rekeyed(
    members: Sequence[tuple[str, str]],
    enrolled: Sequence[str],
    *,
    cover: bool = True,
    edt: str = "string",
) -> Release:
    """Members re-keyed by ``mid``, the old ``member_id`` kept as a plain column; without
    ``cover``, no ``enrolled`` table; its column of datatype ``edt``."""
    d = [
        build.dataset(),
        build.table("members", ["mid"], source=src("members")),
        build.column("members.mid", "string"),
        build.column("members.member_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.mid", "string"),
        build.relationship("visits", ["mid"], "members"),
    ]
    so = {
        "members": TypedSource(("mid", "member_id"), tuple(members)),
        "visits": TypedSource(("visit_id", "mid"), ((1, "x-1"),)),
    }
    if cover:
        d += [
            build.table("enrolled", None, role="coverage", source=src("enrolled")),
            build.column("enrolled.member_id", edt),
        ]
        so["enrolled"] = TypedSource(("member_id",), tuple((e,) for e in enrolled))
    return d, so, lay_of(so)


def patrons(
    members: Sequence[str],
    visits: Sequence[tuple[Any, str]],
    enrolled: Sequence[str],
    *,
    declare: bool = False,
) -> Release:
    """The members table renamed ``patrons``."""
    d = [
        build.dataset(),
        build.table("patrons", ["member_id"], source=src("patrons")),
        build.column("patrons.member_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
        build.relationship("visits", ["member_id"], "patrons"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits.member_id",
                {"table": "enrolled", "parent_columns": {"member_id": "member_id"}},
            )
        )
    so = {
        "patrons": TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(("visit_id", "member_id"), tuple(visits)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def es(
    members: Sequence[str],
    staff: Sequence[str],
    enrolled: Sequence[str],
    cover_to: str | None,
    *,
    visits: Sequence[tuple[str, str]] = (("1", "m-1"),),
    visits_rel: bool = True,
    enrolled_rel: str | None = None,
    enrolled_role: str = "coverage",
) -> Release:
    """Members and their visits, staff and their rounds, and ``enrolled``: declared as the
    coverage of visits' members, of rounds' staff, or (``enrolled_rel``) as a relationship."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("staff", ["staff_id"], role="entity", source=src("staff")),
        build.column("staff.staff_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "string"),
        build.column("visits.member_id", "string"),
        build.table("rounds", ["round_id"], role="event", source=src("rounds")),
        build.column("rounds.round_id", "integer"),
        build.column("rounds.staff_id", "string"),
        build.table("enrolled", None, role=enrolled_role, source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
        build.relationship("rounds", ["staff_id"], "staff"),
    ]
    if visits_rel:
        d.append(build.relationship("visits", ["member_id"], "members"))
    if enrolled_rel:
        parent_column = "staff_id" if enrolled_rel == "staff" else "member_id"
        d.append(build.relationship("enrolled", ["member_id"], enrolled_rel, [parent_column]))
    if cover_to == "members":
        d.append(
            build.coverage(
                "rel:visits.member_id",
                {"table": "enrolled", "parent_columns": {"member_id": "member_id"}},
            )
        )
    elif cover_to == "staff":
        d.append(
            build.coverage(
                "rel:rounds.staff_id",
                {"table": "enrolled", "parent_columns": {"member_id": "staff_id"}},
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "staff": TypedSource(("staff_id",), tuple((s,) for s in staff)),
        "visits": TypedSource(("visit_id", "member_id"), tuple(visits)),
        "rounds": TypedSource(("round_id", "staff_id"), ((1, staff[0]),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def es_renamed(members: Sequence[str], staff: Sequence[str], refs: Sequence[str]) -> Release:
    """As ``es``, with ``enrolled``'s column renamed ``staff_ref`` and declared as the coverage
    of rounds' staff: the earlier link (``enrolled.member_id``) no longer applies."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("staff", ["staff_id"], role="entity", source=src("staff")),
        build.column("staff.staff_id", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "string"),
        build.column("visits.member_id", "string"),
        build.table("rounds", ["round_id"], role="event", source=src("rounds")),
        build.column("rounds.round_id", "integer"),
        build.column("rounds.staff_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.staff_ref", "string"),
        build.relationship("rounds", ["staff_id"], "staff"),
        build.relationship("visits", ["member_id"], "members"),
        build.coverage(
            "rel:rounds.staff_id",
            {"table": "enrolled", "parent_columns": {"staff_ref": "staff_id"}},
        ),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "staff": TypedSource(("staff_id",), tuple((s,) for s in staff)),
        "visits": TypedSource(("visit_id", "member_id"), (("1", "m-1"),)),
        "rounds": TypedSource(("round_id", "staff_id"), ((1, staff[0]),)),
        "enrolled": TypedSource(("staff_ref",), tuple((e,) for e in refs)),
    }
    return d, so, lay_of(so)


def ints_staff(
    members: Sequence[int], staff: Sequence[int], enrolled: Sequence[int], cover_to: str
) -> Release:
    """Integer surrogate keys: ``enrolled`` the coverage of visits' members or of rounds' staff."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "integer"),
        build.table("staff", ["staff_id"], role="entity", source=src("staff")),
        build.column("staff.staff_id", "integer"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", "integer"),
        build.table("rounds", ["round_id"], role="event", source=src("rounds")),
        build.column("rounds.round_id", "integer"),
        build.column("rounds.staff_id", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "integer"),
        build.relationship("rounds", ["staff_id"], "staff"),
        build.relationship("visits", ["member_id"], "members"),
    ]
    if cover_to == "members":
        parents = {"table": "enrolled", "parent_columns": {"member_id": "member_id"}}
        d.append(build.coverage("rel:visits.member_id", parents))
    else:
        parents = {"table": "enrolled", "parent_columns": {"member_id": "staff_id"}}
        d.append(build.coverage("rel:rounds.staff_id", parents))
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "staff": TypedSource(("staff_id",), tuple((s,) for s in staff)),
        "visits": TypedSource(("visit_id", "member_id"), ((1, 1),)),
        "rounds": TypedSource(("round_id", "staff_id"), ((1, 1),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def ints(
    members: Sequence[Any],
    enrolled: Sequence[Any],
    *,
    table: bool = True,
    declare: bool = True,
    mdt: str = "integer",
    edt: str = "integer",
) -> Release:
    """Members with a key of one datatype, ``enrolled`` with one of another, or no members."""
    d = [
        build.dataset(),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.member_id", mdt),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", edt),
    ]
    so = {
        "visits": TypedSource(("visit_id", "member_id"), ((1, members[0] if members else 1),)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    if table:
        d += [
            build.table("members", ["member_id"], source=src("members")),
            build.column("members.member_id", mdt),
            build.relationship("visits", ["member_id"], "members"),
        ]
        so["members"] = TypedSource(("member_id",), tuple((m,) for m in members))
        if declare:
            d.append(
                build.coverage(
                    "rel:visits.member_id",
                    {"table": "enrolled", "parent_columns": {"member_id": "member_id"}},
                )
            )
    return d, so, lay_of(so)


def picked(
    dtype: str,
    members: Sequence[Any],
    pickings: Sequence[tuple[str, Any]],
    listed: Sequence[tuple[str, Any]],
    *,
    rel: bool = True,
    cover: bool = True,
) -> Release:
    """An orchard: pickers (members), trees, pickings, and ``picked``, the coverage of pickings'
    trees with the scope column ``picker`` standing for pickings' picker, a foreign key into
    members when ``rel``."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", dtype),
        build.table("trees", ["tree_id"], source=src("trees")),
        build.column("trees.tree_id", "string"),
        build.table("pickings", ["tree_id", "picker"], role="event", source=src("pickings")),
        build.column("pickings.tree_id", "string"),
        build.column("pickings.picker", dtype),
        build.table("picked", None, role="coverage", source=src("picked")),
        build.column("picked.tree_id", "string"),
        build.column("picked.picker", dtype),
        build.relationship("pickings", ["tree_id"], "trees"),
    ]
    if rel:
        d.append(build.relationship("pickings", ["picker"], "members", ["member_id"]))
    if cover:
        d.append(
            build.coverage(
                "rel:pickings.tree_id",
                {
                    "table": "picked",
                    "parent_columns": {"tree_id": "tree_id"},
                    "scope_columns": {"picker": "picker"},
                },
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "trees": TypedSource(("tree_id",), (("t1",), ("t2",))),
        "pickings": TypedSource(("tree_id", "picker"), tuple(pickings)),
        "picked": TypedSource(("tree_id", "picker"), tuple(listed)),
    }
    return d, so, lay_of(so)


def grouped(
    dtype: str,
    members: Sequence[Any],
    pickings: Sequence[tuple[str, Any]],
    assigned: Sequence[tuple[str, str]],
    teams: Sequence[tuple[str, Any]],
) -> Release:
    """The orchard with a grouped coverage: ``trees_teams`` assigns trees to teams, and
    ``team_pickers`` lists each team's pickers (its scope column)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", dtype),
        build.table("trees", ["tree_id"], source=src("trees")),
        build.column("trees.tree_id", "string"),
        build.table("pickings", ["tree_id", "picker"], role="event", source=src("pickings")),
        build.column("pickings.tree_id", "string"),
        build.column("pickings.picker", dtype),
        build.table("trees_teams", None, role="coverage", source=src("trees_teams")),
        build.column("trees_teams.tree_id", "string"),
        build.column("trees_teams.team", "string"),
        build.table("team_pickers", None, role="coverage", source=src("team_pickers")),
        build.column("team_pickers.team", "string"),
        build.column("team_pickers.picker", dtype),
        build.relationship("pickings", ["tree_id"], "trees"),
        build.relationship("pickings", ["picker"], "members", ["member_id"], role="owner"),
        build.coverage(
            "rel:pickings.tree_id",
            {
                "assignment": {
                    "table": "trees_teams",
                    "parent_columns": {"tree_id": "tree_id"},
                    "group_column": "team",
                },
                "groups": {
                    "table": "team_pickers",
                    "group_column": "team",
                    "scope_columns": {"picker": "picker"},
                },
            },
        ),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "trees": TypedSource(("tree_id",), (("t1",), ("t2",))),
        "pickings": TypedSource(("tree_id", "picker"), tuple(pickings)),
        "trees_teams": TypedSource(("tree_id", "team"), tuple(assigned)),
        "team_pickers": TypedSource(("team", "picker"), tuple(teams)),
    }
    return d, so, lay_of(so)


def scoped(
    members: Sequence[str],
    loans: Sequence[tuple[Any, str, str]],
    assessed: Sequence[tuple[str, str]],
) -> Release:
    """Loans with a topic, and ``assessed``, a keyed coverage of loans' members scoped by
    topic."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.member_id", "string"),
        build.column("loans.topic", "string"),
        build.table("assessed", ["member_id", "topic"], role="coverage", source=src("assessed")),
        build.column("assessed.member_id", "string"),
        build.column("assessed.topic", "string"),
        build.relationship("loans", ["member_id"], "members"),
        build.coverage(
            "rel:loans.member_id",
            {
                "table": "assessed",
                "parent_columns": {"member_id": "member_id"},
                "scope_columns": {"topic": "topic"},
            },
        ),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "member_id", "topic"), tuple(loans)),
        "assessed": TypedSource(("member_id", "topic"), tuple(assessed)),
    }
    return d, so, lay_of(so)


def selfscope(
    members: Sequence[str],
    loans: Sequence[tuple[int, str]],
    assessed: Sequence[tuple[str, int]],
    *,
    cover: bool = True,
) -> Release:
    """``assessed``, the coverage of loans' members, with the scope column ``loan`` standing for
    loans' own key."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.member_id", "string"),
        build.table("assessed", None, role="coverage", source=src("assessed")),
        build.column("assessed.member_id", "string"),
        build.column("assessed.loan", "integer"),
        build.relationship("loans", ["member_id"], "members"),
    ]
    if cover:
        d.append(
            build.coverage(
                "rel:loans.member_id",
                {
                    "table": "assessed",
                    "parent_columns": {"member_id": "member_id"},
                    "scope_columns": {"loan": "loan_id"},
                },
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "member_id"), tuple(loans)),
        "assessed": TypedSource(("member_id", "loan"), tuple(assessed)),
    }
    return d, so, lay_of(so)


def pairs(
    members: Sequence[tuple[str, int]],
    enrolled: Sequence[tuple[Any, Any]],
    *,
    table: bool = True,
    declare: bool = True,
    cover: bool = True,
) -> Release:
    """Members keyed by (site, num), and ``enrolled`` (n, s), the coverage of visits' members
    with its columns in the other order."""
    d = [
        build.dataset(),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.site", "string"),
        build.column("visits.num", "integer"),
    ]
    so = {"visits": TypedSource(("visit_id", "site", "num"), ((1, "north", 1),))}
    if table:
        d += [
            build.table("members", ["site", "num"], source=src("members")),
            build.column("members.site", "string"),
            build.column("members.num", "integer"),
            build.relationship("visits", ["site", "num"], "members"),
        ]
        so["members"] = TypedSource(("site", "num"), tuple(members))
    if cover:
        d += [
            build.table("enrolled", None, role="coverage", source=src("enrolled")),
            build.column("enrolled.n", "integer"),
            build.column("enrolled.s", "string"),
        ]
        so["enrolled"] = TypedSource(("n", "s"), tuple(enrolled))
        if declare and table:
            d.append(
                build.coverage(
                    "rel:visits.site+num",
                    {"table": "enrolled", "parent_columns": {"n": "num", "s": "site"}},
                )
            )
    return d, so, lay_of(so)


def ab(
    members: Sequence[tuple[int, int]],
    enrolled: Sequence[tuple[Any, Any]],
    *,
    keys: tuple[str, str] = ("a", "b"),
    declare: bool = True,
    edt: str = "integer",
) -> Release:
    """Members keyed by two integers (``keys``), and ``enrolled`` (x, y), the coverage of
    visits' members, x for the first key column and y for the second."""
    a, b = keys
    d = [
        build.dataset(),
        build.table("members", [a, b], source=src("members")),
        build.column(f"members.{a}", "integer"),
        build.column(f"members.{b}", "integer"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column(f"visits.{a}", "integer"),
        build.column(f"visits.{b}", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", edt),
        build.column("enrolled.y", edt),
        build.relationship("visits", [a, b], "members"),
    ]
    if declare:
        d.append(
            build.coverage(
                f"rel:visits.{a}+{b}", {"table": "enrolled", "parent_columns": {"x": a, "y": b}}
            )
        )
    so = {
        "members": TypedSource((a, b), tuple(members)),
        "visits": TypedSource(("visit_id", a, b), ((1, 5, 5),)),
        "enrolled": TypedSource(("x", "y"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def region(
    members: Sequence[tuple[str, int]], enrolled: Sequence[tuple[str, int]], *, declare: bool = True
) -> Release:
    d = [
        build.dataset(),
        build.table("members", ["region", "num"], source=src("members")),
        build.column("members.region", "string"),
        build.column("members.num", "integer"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.region", "string"),
        build.column("visits.num", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.r", "string"),
        build.column("enrolled.n", "integer"),
        build.relationship("visits", ["region", "num"], "members"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits.region+num",
                {"table": "enrolled", "parent_columns": {"r": "region", "n": "num"}},
            )
        )
    so = {
        "members": TypedSource(("region", "num"), tuple(members)),
        "visits": TypedSource(("visit_id", "region", "num"), ((1, "north", 1),)),
        "enrolled": TypedSource(("r", "n"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def onecol(members: Sequence[int], enrolled: Sequence[int]) -> Release:
    d = [
        build.dataset(),
        build.table("members", ["num"], source=src("members")),
        build.column("members.num", "integer"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.num", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.n", "integer"),
        build.relationship("visits", ["num"], "members"),
        build.coverage("rel:visits.num", {"table": "enrolled", "parent_columns": {"n": "num"}}),
    ]
    so = {
        "members": TypedSource(("num",), tuple((m,) for m in members)),
        "visits": TypedSource(("visit_id", "num"), ((1, 1),)),
        "enrolled": TypedSource(("n",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


def region2(members: Sequence[tuple[str, int]], enrolled: Sequence[int]) -> Release:
    """Members re-keyed by (region, code); ``enrolled.n`` undeclared."""
    d = [
        build.dataset(),
        build.table("members", ["region", "code"], source=src("members")),
        build.column("members.region", "string"),
        build.column("members.code", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.n", "integer"),
    ]
    so = {
        "members": TypedSource(("region", "code"), tuple(members)),
        "enrolled": TypedSource(("n",), tuple((e,) for e in enrolled)),
    }
    return d, so, lay_of(so)


WIDE_KEYS = [f"k{i:02d}" for i in range(16)]
WIDE_COVER = [f"c{i:02d}" for i in range(16)]


def wide(
    members: Sequence[tuple[int, ...]], enrolled: Sequence[tuple[int, ...]], *, declare: bool = True
) -> Release:
    """Members keyed by sixteen integers."""
    d = [
        build.dataset(),
        build.table("members", WIDE_KEYS, source=src("members")),
        *[build.column(f"members.{k}", "integer") for k in WIDE_KEYS],
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        *[build.column(f"visits.{k}", "integer") for k in WIDE_KEYS],
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        *[build.column(f"enrolled.{c}", "integer") for c in WIDE_COVER],
        build.relationship("visits", WIDE_KEYS, "members"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits." + "+".join(WIDE_KEYS),
                {
                    "table": "enrolled",
                    "parent_columns": dict(zip(WIDE_COVER, WIDE_KEYS, strict=True)),
                },
            )
        )
    so = {
        "members": TypedSource(tuple(WIDE_KEYS), tuple(members)),
        "visits": TypedSource(("visit_id", *WIDE_KEYS), ((1, *members[0]),)),
        "enrolled": TypedSource(tuple(WIDE_COVER), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def surr(
    members: Sequence[int],
    staff: Sequence[int],
    enrolled: Sequence[int],
    *,
    with_members: bool = True,
    declare: bool = True,
) -> Release:
    """Staff, and members with integer surrogate keys (or none), and ``enrolled``, the coverage
    of visits' members."""
    d = [
        build.dataset(),
        build.table("staff", ["staff_id"], source=src("staff")),
        build.column("staff.staff_id", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "integer"),
    ]
    so = {
        "staff": TypedSource(("staff_id",), tuple((s,) for s in staff)),
        "enrolled": TypedSource(("member_id",), tuple((e,) for e in enrolled)),
    }
    if with_members:
        d += [
            build.table("members", ["member_id"], source=src("members")),
            build.column("members.member_id", "integer"),
            build.table("visits", ["visit_id"], role="event", source=src("visits")),
            build.column("visits.visit_id", "integer"),
            build.column("visits.member_id", "integer"),
            build.relationship("visits", ["member_id"], "members"),
        ]
        so["members"] = TypedSource(("member_id",), tuple((m,) for m in members))
        so["visits"] = TypedSource(("visit_id", "member_id"), ((1, 1),))
        if declare:
            d.append(
                build.coverage(
                    "rel:visits.member_id",
                    {"table": "enrolled", "parent_columns": {"member_id": "member_id"}},
                )
            )
    return d, so, lay_of(so)


def notes(
    members: Sequence[int],
    staff: Sequence[int],
    written: Sequence[tuple[str, int]],
    rel_to: str | None,
) -> Release:
    """Members, staff, and notes whose author is a member or a staff member (or undeclared)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], role="entity", source=src("members")),
        build.column("members.member_id", "integer"),
        build.table("staff", ["staff_id"], role="entity", source=src("staff")),
        build.column("staff.staff_id", "integer"),
        build.table("notes", ["note_id"], role="event", source=src("notes")),
        build.column("notes.note_id", "string"),
        build.column("notes.author", "integer"),
    ]
    if rel_to is not None:
        parent_column = "member_id" if rel_to == "members" else "staff_id"
        d.append(build.relationship("notes", ["author"], rel_to, [parent_column]))
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "staff": TypedSource(("staff_id",), tuple((s,) for s in staff)),
        "notes": TypedSource(("note_id", "author"), tuple(written)),
    }
    return d, so, lay_of(so)


def with_coverage(release: Release, relationship: str, parents: dict[str, Any]) -> Release:
    """A release with a coverage added."""
    descriptors, sources, layouts = release
    return [*descriptors, build.coverage(relationship, parents)], sources, layouts


def assessed(
    members: Sequence[str],
    loans: Sequence[tuple[int, str]],
    listed: Sequence[str],
    variant: str = "declared",
) -> Release:
    """repro.py's shape: loans of members under a relationship with a role, and ``assessed``,
    a direct coverage of it, declared, proposed or removed (``variant``)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string", identifier=True),
        build.table("loans", ["loan_id"], role="event", source=src("loans")),
        build.column("loans.loan_id", "integer"),
        build.column("loans.member_id", "string"),
        build.table("assessed", None, role="coverage", source=src("assessed")),
        build.column("assessed.member_id", "string"),
        build.relationship("loans", ["member_id"], "members", role="member"),
    ]
    parents = {"table": "assessed", "parent_columns": {"member_id": "member_id"}}
    if variant == "declared":
        d.append(build.coverage("rel:loans.member", parents))
    elif variant == "proposed":
        d.append(build.coverage("rel:loans.member", parents, status="proposed"))
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(("loan_id", "member_id"), tuple(loans)),
        "assessed": TypedSource(("member_id",), tuple((m,) for m in listed)),
    }
    return d, so, lay_of(so)


def checklists(
    sites: Sequence[str],
    lists: Sequence[tuple[str, str]],
    counts: Sequence[tuple[str, str]],
    assignments: Sequence[tuple[str, str]],
    *,
    declare: bool = True,
) -> Release:
    """repro2.py's shape: sites, their checklists, the counts on each, and a grouped coverage of
    counts: ``assignments`` gives each checklist a protocol, ``protocol_species`` its species."""
    d = [
        build.dataset(),
        build.table("sites", ["site_id"], source=src("sites")),
        build.column("sites.site_id", "string"),
        build.table("checklists", ["checklist_id"], role="event", source=src("checklists")),
        build.column("checklists.checklist_id", "string"),
        build.column("checklists.site_id", "string"),
        build.table(
            "counts", ["checklist_id", "species"], role="measurement", source=src("counts")
        ),
        build.column("counts.checklist_id", "string"),
        build.column("counts.species", "string"),
        build.table("assignments", None, role="coverage", source=src("assignments")),
        build.column("assignments.checklist_id", "string"),
        build.column("assignments.protocol", "string"),
        build.table("protocol_species", None, role="coverage", source=src("protocol_species")),
        build.column("protocol_species.protocol", "string"),
        build.column("protocol_species.species", "string"),
        build.relationship("checklists", ["site_id"], "sites"),
        build.relationship("counts", ["checklist_id"], "checklists"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:counts.checklist_id",
                {
                    "assignment": {
                        "table": "assignments",
                        "parent_columns": {"checklist_id": "checklist_id"},
                        "group_column": "protocol",
                    },
                    "groups": {
                        "table": "protocol_species",
                        "group_column": "protocol",
                        "scope_columns": {"species": "species"},
                    },
                },
            )
        )
    so = {
        "sites": TypedSource(("site_id",), tuple((s,) for s in sites)),
        "checklists": TypedSource(("checklist_id", "site_id"), tuple(lists)),
        "counts": TypedSource(("checklist_id", "species"), tuple(counts)),
        "assignments": TypedSource(("checklist_id", "protocol"), tuple(assignments)),
        "protocol_species": TypedSource(("protocol", "species"), (("all", "wren"),)),
    }
    return d, so, lay_of(so)


def checked(
    members: Sequence[tuple[int, int]],
    visits: Sequence[tuple[int, str, int, int]],
    listed: Sequence[tuple[str, int, int]],
    *,
    cover: bool = True,
) -> Release:
    """Members keyed by (a, b), visits at a site by a member, and ``checked``, the coverage of
    visits' sites whose scope columns x and y stand for visits' composite foreign key (a, b)."""
    d = [
        build.dataset(),
        build.table("members", ["a", "b"], source=src("members")),
        build.column("members.a", "integer"),
        build.column("members.b", "integer"),
        build.table("sites", ["site"], source=src("sites")),
        build.column("sites.site", "string"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.site", "string"),
        build.column("visits.a", "integer"),
        build.column("visits.b", "integer"),
        build.table("checked", None, role="coverage", source=src("checked")),
        build.column("checked.site", "string"),
        build.column("checked.x", "integer"),
        build.column("checked.y", "integer"),
        build.relationship("visits", ["site"], "sites"),
        build.relationship("visits", ["a", "b"], "members"),
    ]
    if cover:
        d.append(
            build.coverage(
                "rel:visits.site",
                {
                    "table": "checked",
                    "parent_columns": {"site": "site"},
                    "scope_columns": {"x": "a", "y": "b"},
                },
            )
        )
    so = {
        "members": TypedSource(("a", "b"), tuple(members)),
        "sites": TypedSource(("site",), (("s1",),)),
        "visits": TypedSource(("visit_id", "site", "a", "b"), tuple(visits)),
        "checked": TypedSource(("site", "x", "y"), tuple(listed)),
    }
    return d, so, lay_of(so)


def scoped_wide(
    keys: int,
    members: Sequence[str],
    visits: Sequence[tuple[tuple[int, ...], str]],
    enrolled: Sequence[tuple[int, ...]],
    *,
    declare: bool = True,
) -> Release:
    """compose_one's shape (PR #88's review, M2): visits keyed by ``keys`` integer columns, at a
    member, a site and a room; ``enrolled`` the coverage of each of the three, its scope columns
    ``s{i}_{j}`` (``j`` for the coverage) standing for visits' key column ``k{i}``, so three
    scope columns stand for every key column. An ``enrolled`` row gives every scope column, in
    the order ``s{i}_{j}`` by ``j`` then ``i``."""
    names = [f"k{i:02d}" for i in range(keys)]
    parents = ["members", "sites", "rooms"]
    scope = [f"s{i:02d}_{j}" for j in range(3) for i in range(keys)]
    d = [build.dataset()]
    for parent in parents:
        d += [
            build.table(parent, [f"{parent}_id"], source=src(parent)),
            build.column(f"{parent}.{parent}_id", "string"),
        ]
    d += [
        build.table("visits", names, role="event", source=src("visits")),
        *[build.column(f"visits.{k}", "integer") for k in names],
        *[build.column(f"visits.{p}_id", "string") for p in parents],
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        *[build.column(f"enrolled.{p}_id", "string") for p in parents],
        *[build.column(f"enrolled.{c}", "integer") for c in scope],
    ]
    for j, parent in enumerate(parents):
        d.append(build.relationship("visits", [f"{parent}_id"], parent))
        if declare:
            d.append(
                build.coverage(
                    f"rel:visits.{parent}_id",
                    {
                        "table": "enrolled",
                        "parent_columns": {f"{parent}_id": f"{parent}_id"},
                        "scope_columns": {f"s{i:02d}_{j}": names[i] for i in range(keys)},
                    },
                )
            )
    so = {
        "members": TypedSource(("members_id",), tuple((m,) for m in members)),
        "sites": TypedSource(("sites_id",), (("x",),)),
        "rooms": TypedSource(("rooms_id",), (("x",),)),
        "visits": TypedSource(
            (*names, "members_id", "sites_id", "rooms_id"),
            tuple((*key, member, "x", "x") for key, member in visits),
        ),
        "enrolled": TypedSource(
            ("members_id", "sites_id", "rooms_id", *scope),
            tuple(("m-1", "x", "x", *row) for row in enrolled),
        ),
    }
    return d, so, lay_of(so)


def ab_lists(members: Sequence[tuple[int, int]], enrolled: Sequence[tuple[str, str]]) -> Release:
    """``ab``'s shape, ``enrolled``'s x and y lists of categories, undeclared."""
    d = [
        build.dataset(),
        build.table("members", ["a", "b"], source=src("members")),
        build.column("members.a", "integer"),
        build.column("members.b", "integer"),
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        build.column("visits.a", "integer"),
        build.column("visits.b", "integer"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", "list<category>"),
        build.column("enrolled.y", "list<category>"),
        build.relationship("visits", ["a", "b"], "members"),
    ]
    so = {
        "members": TypedSource(("a", "b"), tuple(members)),
        "visits": TypedSource(("visit_id", "a", "b"), ((1, 5, 5),)),
        "enrolled": TypedSource(("x", "y"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def ab_text(
    members: Sequence[tuple[str, ...]],
    enrolled: Sequence[tuple[str, str]],
    *,
    declare: bool = True,
    xdt: str = "string",
    ydt: str = "string",
    rekeyed: bool = False,
    reordered: bool = False,
) -> Release:
    """``ab``'s shape with string keys, ``enrolled``'s x and y of datatypes ``xdt`` and
    ``ydt``. With ``rekeyed``, members are keyed by ``n`` (a member's first value), a and b kept
    as plain columns, and visits point at ``n``; with ``reordered``, the key is (b, a), the
    members' rows still (a, b)."""
    key = ["n"] if rekeyed else ["b", "a"] if reordered else ["a", "b"]
    columns = ("n", "a", "b") if rekeyed else ("a", "b")
    d = [
        build.dataset(),
        build.table("members", key, source=src("members")),
        *[build.column(f"members.{c}", "string") for c in columns],
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        *[build.column(f"visits.{c}", "string") for c in key],
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", xdt),
        build.column("enrolled.y", ydt),
        build.relationship("visits", key, "members"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:visits.a+b", {"table": "enrolled", "parent_columns": {"x": "a", "y": "b"}}
            )
        )
    first: dict[str, str] = dict(zip(columns, members[0], strict=True))
    so = {
        "members": TypedSource(columns, tuple(members)),
        "visits": TypedSource(("visit_id", *key), ((1, *(first[column] for column in key)),)),
        "enrolled": TypedSource(("x", "y"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def visits_keyed(
    members: Sequence[str],
    visits: Sequence[tuple[Any, ...]],
    enrolled: Sequence[tuple[str, str]],
    *,
    rekeyed: bool,
    declare: bool = False,
    xdt: str = "string",
) -> Release:
    """``visits_text``'s shape (visits at a member, fines of visits, ``enrolled`` (x, y) the
    coverage of the fines' visits by (k0, k1)), with the visits keyed by (k0, k1), or, with
    ``rekeyed``, by a ``vid`` that comes first in each of ``visits``' tuples, k0 and k1 kept as
    plain columns: a table below the person whose key the coverage's columns are no longer."""
    key = ["vid"] if rekeyed else ["k0", "k1"]
    columns = ("vid", "k0", "k1", "member_id") if rekeyed else ("k0", "k1", "member_id")
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("visits", key, role="event", source=src("visits")),
        *([build.column("visits.vid", "integer")] if rekeyed else []),
        build.column("visits.k0", "string"),
        build.column("visits.k1", "string"),
        build.column("visits.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        *[build.column(f"fines.{c}", "integer" if c == "vid" else "string") for c in key],
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", xdt),
        build.column("enrolled.y", "string"),
        build.relationship("visits", ["member_id"], "members"),
        build.relationship("fines", key, "visits"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:fines.k0+k1",
                {"table": "enrolled", "parent_columns": {"x": "k0", "y": "k1"}},
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(columns, tuple(visits)),
        "fines": TypedSource(("fine_id", *key), ()),
        "enrolled": TypedSource(("x", "y"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def wide_members(
    columns: Sequence[str],
    key: Sequence[str],
    mapping: dict[str, str],
    members: Sequence[dict[str, str]],
    enrolled: Sequence[dict[str, str]],
    *,
    declare: bool,
) -> Release:
    """Members of any number of string ``columns`` keyed by ``key`` (those columns, in that
    order), visits at the first member by the key, and ``enrolled``, a coverage of the visits
    whose columns are the ``mapping``'s keys (child column to parent column): its pairs sorted
    by the child's name give the parent columns' order, which has nothing to do with the key's.
    Each of ``members`` and ``enrolled`` is a row by column name (``enrolled``'s by the parent
    columns it lists); ``declare`` declares the coverage."""
    relationship = build.relationship("visits", list(key), "members")
    child = sorted(mapping)
    d = [
        build.dataset(),
        build.table("members", list(key), source=src("members")),
        *[build.column(f"members.{c}", "string") for c in columns],
        build.table("visits", ["visit_id"], role="event", source=src("visits")),
        build.column("visits.visit_id", "integer"),
        *[build.column(f"visits.{c}", "string") for c in key],
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        *[build.column(f"enrolled.{c}", "string") for c in child],
        relationship,
    ]
    if declare:
        d.append(build.coverage(relationship.id, {"table": "enrolled", "parent_columns": mapping}))
    so = {
        "members": TypedSource(
            tuple(columns), tuple(tuple(m[c] for c in columns) for m in members)
        ),
        "visits": TypedSource(("visit_id", *key), ((1, *(members[0][c] for c in key)),)),
        "enrolled": TypedSource(
            tuple(child), tuple(tuple(row[mapping[c]] for c in child) for row in enrolled)
        ),
    }
    return d, so, lay_of(so)


def loans_keyed(
    members: Sequence[str],
    loans: Sequence[tuple[Any, ...]],
    enrolled: Sequence[str],
    *,
    rekeyed: bool,
    declare: bool = False,
    xdt: str = "string",
) -> Release:
    """``visits_keyed``'s shape with a key of ONE column: loans at a member, fines of loans,
    ``enrolled`` (x) the coverage of the fines' loans by ``loan_id`` (a string, of the datatype
    ``xdt`` as ``x``). The loans are keyed by ``loan_id``, or, with ``rekeyed``, by a ``lid`` that
    comes first in each of ``loans``' tuples, ``loan_id`` kept as a plain column: a table below
    the person whose key the coverage's one column is no longer."""
    key = ["lid"] if rekeyed else ["loan_id"]
    columns = ("lid", "loan_id", "member_id") if rekeyed else ("loan_id", "member_id")
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("loans", key, role="event", source=src("loans")),
        *([build.column("loans.lid", "integer")] if rekeyed else []),
        build.column("loans.loan_id", "string"),
        build.column("loans.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column(f"fines.{key[0]}", "integer" if rekeyed else "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", xdt),
        build.relationship("loans", ["member_id"], "members"),
        build.relationship("fines", key, "loans"),
    ]
    if declare:
        d.append(
            build.coverage(
                f"rel:fines.{key[0]}", {"table": "enrolled", "parent_columns": {"x": "loan_id"}}
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "loans": TypedSource(columns, tuple(loans)),
        "fines": TypedSource(("fine_id", *key), ()),
        "enrolled": TypedSource(("x",), tuple((x,) for x in enrolled)),
    }
    return d, so, lay_of(so)


def visits_text(
    members: Sequence[str],
    visits: Sequence[tuple[str, str, str]],
    enrolled: Sequence[tuple[str, str]],
    *,
    declare: bool = False,
    xdt: str = "string",
) -> Release:
    """Visits keyed by two strings (k0, k1), at a member, each with its fines; ``enrolled`` (x,
    y) the coverage of the fines' visits, x for k0 (of datatype ``xdt``) and y for k1: a cover of
    two columns into a table below the person."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("visits", ["k0", "k1"], role="event", source=src("visits")),
        build.column("visits.k0", "string"),
        build.column("visits.k1", "string"),
        build.column("visits.member_id", "string"),
        build.table("fines", ["fine_id"], role="event", source=src("fines")),
        build.column("fines.fine_id", "integer"),
        build.column("fines.k0", "string"),
        build.column("fines.k1", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.x", xdt),
        build.column("enrolled.y", "string"),
        build.relationship("visits", ["member_id"], "members"),
        build.relationship("fines", ["k0", "k1"], "visits"),
    ]
    if declare:
        d.append(
            build.coverage(
                "rel:fines.k0+k1",
                {"table": "enrolled", "parent_columns": {"x": "k0", "y": "k1"}},
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(("k0", "k1", "member_id"), tuple(visits)),
        "fines": TypedSource(("fine_id", "k0", "k1"), ()),
        "enrolled": TypedSource(("x", "y"), tuple(enrolled)),
    }
    return d, so, lay_of(so)


def paired(
    members: Sequence[str],
    visits: Sequence[tuple[int, int, str]],
    enrolled: Sequence[tuple[SourceValue, ...]],
    columns: Sequence[str],
    *,
    scope: dict[str, str] | None = None,
    sdt: str = "integer",
) -> Release:
    """Visits keyed by two integers (k0, k1), at a member; ``enrolled`` the coverage of visits'
    members, with the scope columns ``columns`` (of datatype ``sdt``), ``scope`` mapping some
    of them to k0 and k1 when it declares the coverage (PR #88's review round 2, M2)."""
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("visits", ["k0", "k1"], role="event", source=src("visits")),
        build.column("visits.k0", "integer"),
        build.column("visits.k1", "integer"),
        build.column("visits.member_id", "string"),
        build.table("enrolled", None, role="coverage", source=src("enrolled")),
        build.column("enrolled.member_id", "string"),
        *[build.column(f"enrolled.{c}", sdt) for c in columns],
        build.relationship("visits", ["member_id"], "members"),
    ]
    if scope is not None:
        d.append(
            build.coverage(
                "rel:visits.member_id",
                {
                    "table": "enrolled",
                    "parent_columns": {"member_id": "member_id"},
                    "scope_columns": scope,
                },
            )
        )
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "visits": TypedSource(("k0", "k1", "member_id"), tuple(visits)),
        "enrolled": TypedSource(("member_id", *columns), tuple(("m-1", *e) for e in enrolled)),
    }
    return d, so, lay_of(so)


def twoscope(
    members: Sequence[str],
    listed: Sequence[tuple[str, str, str, str]],
    *,
    both: bool = True,
) -> Release:
    """An orchard whose pickings' picker is a foreign key into two parents, members and staff,
    and whose table ``picked`` is the coverage of two relationships of pickings (to trees, by
    ``tree_id`` and by ``spot``), the first with the scope column ``p1``, the second, with
    ``both``, with ``p2``, both standing for the picker: the covers composed from them read the
    same columns, ``p1`` and ``p2`` (or ``p1`` alone), into each parent. A listed row is
    (tree_id, spot, p1, p2)."""
    parents = {"table": "picked"}
    d = [
        build.dataset(),
        build.table("members", ["member_id"], source=src("members")),
        build.column("members.member_id", "string"),
        build.table("staff", ["staff_id"], source=src("staff")),
        build.column("staff.staff_id", "string"),
        build.table("trees", ["tree_id"], source=src("trees")),
        build.column("trees.tree_id", "string"),
        build.table("pickings", ["tree_id", "picker"], role="event", source=src("pickings")),
        build.column("pickings.tree_id", "string"),
        build.column("pickings.spot", "string"),
        build.column("pickings.picker", "string"),
        build.table("picked", None, role="coverage", source=src("picked")),
        *[build.column(f"picked.{c}", "string") for c in ("tree_id", "spot", "p1", "p2")],
        build.relationship("pickings", ["tree_id"], "trees"),
        build.relationship("pickings", ["spot"], "trees", ["tree_id"]),
        build.relationship("pickings", ["picker"], "members", ["member_id"], role="owner"),
        build.relationship("pickings", ["picker"], "staff", ["staff_id"], role="staff"),
        build.coverage(
            "rel:pickings.tree_id",
            {
                **parents,
                "parent_columns": {"tree_id": "tree_id"},
                "scope_columns": {"p1": "picker"},
            },
        ),
        *(
            [
                build.coverage(
                    "rel:pickings.spot",
                    {
                        **parents,
                        "parent_columns": {"spot": "tree_id"},
                        "scope_columns": {"p2": "picker"},
                    },
                )
            ]
            if both
            else []
        ),
    ]
    so = {
        "members": TypedSource(("member_id",), tuple((m,) for m in members)),
        "staff": TypedSource(("staff_id",), (("m-1",), ("s-1",))),
        "trees": TypedSource(("tree_id",), (("t1",), ("t2",))),
        "pickings": TypedSource(("tree_id", "spot", "picker"), (("t1", "t1", "m-1"),)),
        "picked": TypedSource(("tree_id", "spot", "p1", "p2"), tuple(listed)),
    }
    return d, so, lay_of(so)


def keyed_cover(release: Release) -> Release:
    """``release`` with its ``enrolled`` table keyed by ``member_id``, so that the table's key is
    a link into itself (a relationship that is no declaration of another parent)."""
    descriptors, sources, layouts = release
    changed = [
        build.table("enrolled", ["member_id"], role="coverage", source=src("enrolled"))
        if isinstance(d, TableDescriptor) and d.id == "enrolled"
        else d
        for d in descriptors
    ]
    return changed, sources, layouts
