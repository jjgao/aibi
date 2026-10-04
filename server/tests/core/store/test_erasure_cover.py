"""Erasure through coverage and scope tables (SPEC §12.2, D223, D408): only rows withdraw a
release; a live release whose coverage or scope table names the key refuses the erasure
(``ERASURE_BLOCKED``), naming labels and table and row positions, on the valid, ``redact_only``
and waiting paths, after finishing what a committed erasure left to do. The links are recorded
when a release is published, so a declaration outlives its release; scope columns compose with
the recorded relationships and keys. A rule that passes a row over is exact; the rules that
match a key may match too much, which costs a refusal and never a withdrawal.

Each case runs on a fresh store; its expected outcome is in ``attempt``'s short form: ``done
(labels) terms=n``, or the refusal's code with ``B1`` (the latest release holds a row), ``E
[labels] [(table, rows)]`` with ``NOTE`` for the collision wording, or ``F`` (an incomplete
registry)."""

import contextlib
import itertools
import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from tests.core.store import base_erasure
from tests.core.store import cover_fixtures as fx
from tests.core.store.cover_fixtures import (
    ADA,
    M2,
    Step,
    ab,
    ab_text,
    attempt,
    bare,
    covlib,
    enrol,
    erased,
    es,
    es_renamed,
    grouped,
    ints,
    ints_staff,
    lib,
    libr,
    mem,
    notes,
    onecol,
    paired,
    pairs,
    patrons,
    picked,
    region,
    region2,
    rekeyed,
    renamed,
    scoped,
    selfscope,
    state,
    surr,
    visits_keyed,
    visits_text,
    wide,
    withdraw,
)

from aibi.core.engine import build
from aibi.core.schema.curation import ChangeRequest
from aibi.core.store import appdb, erasure, redaction, sessions
from aibi.core.store.appdb import MIGRATIONS
from aibi.core.store.blobs import BlobStore
from aibi.core.store.build import BuildRefused
from aibi.core.store.erasure import erase
from aibi.core.store.links import Link, Scope, link
from aibi.core.store.manifest import Manifest, hex_of
from aibi.core.store.sources import SourceValue
from aibi.core.store.store import Store, StoreRefused


def _pinned_erasure(
    key: Sequence[SourceValue], held: list[Any], **given: Any
) -> Callable[[Store], None]:
    """A step: an erasure while a pin on the first release holds off its redaction."""

    def step(store: Store) -> None:
        pin = store.pin()
        pin.manifest(store.labels("lib")[0].manifest)
        held.append(pin)
        erased("members", key, **given)(store)

    return step


def _again(key: Sequence[SourceValue], first: Sequence[Step], later: Step) -> list[Step]:
    held: list[Any] = []
    return [*first, _pinned_erasure(key, held), later]


R1 = enrol(M2, [(1, "m-1"), (2, "m-17")], M2)
JOSE = unicodedata.normalize("NFC", "jos\u00e9")
JOSE_NFD = unicodedata.normalize("NFD", JOSE)
LIST = "list<category>"
AB1 = ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1"), ("c1", "m-17")])
VT_OTHER = [("a", "b", "m-1")]
VT1 = visits_text(M2, [*VT_OTHER, ("sé-1", "vé-2", "m-17")], [("a", "b")], declare=True)
LISTED_VT = [("a", "b"), ("zz;sé-1", "vé-2")]
P4B = [
    enrol(["m-1", "z"], [(1, "m-1")], ["m-1", "z"]),
    enrol(["m-1"], [(1, "m-1")], ["m-1", "m-17"], declare=False),
    erased("members", ["z"]),
    enrol(["m-1"], [(1, "m-1")], ["m-1", "m-17"], declare=False),
]
P1 = pairs([("north", 1), ("north", 17)], [(1, "north"), (17, "north")])
X1 = [es(M2, M2, M2, "members"), es(["m-1"], M2, ["m-1"], "staff"), es(["m-1"], M2, M2, None)]
R10L = [es(M2, M2, M2, "members"), es(["m-1"], M2, M2, "staff")]
BELOW = [
    lib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], declare=True),
    lib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], key="lid"),
    lib(["m-1"], [(1, "m-1")], [1, 2], key="lid"),
    lib(["m-1"], [(1, "m-1")], [1], key="lid"),
]
BRR = [
    lib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], declare=True),
    lib(M2, [(1, "m-1"), (7, "m-17")], [1], key="lid"),
    lib(["m-1"], [(1, "m-1"), (2, "m-1")], [1, 2], key="lid"),
    lib(["m-1"], [(1, "m-1")], [1], key="lid"),
]
CR = [
    ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
    ab([(1, 2), (5, 5)], [(2, 1), (5, 5)], keys=("p", "q"), declare=False),
]
PC = [
    ab([(1, 2), (2, 1), (5, 5)], [(1, 2), (2, 1), (5, 5)]),
    ab([(5, 5)], [(5, 5), (2, 1)], declare=False),
    ab([(5, 5)], [(5, 5)]),
]
PICK = ["p-1", "p-5"]
SCOPE_SPLIT = [
    picked("string", PICK, [("t1", "p-1"), ("t2", "p-5")], [("t1", "p-1")], cover=False),
    picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1"), ("t2", "p-5")], rel=False),
]
TREN = [
    enrol(M2, [(1, "m-1")], M2),
    patrons(M2, [(1, "m-1")], ["m-1"]),
    patrons(["m-1"], [(1, "m-1")], M2),
    patrons(["m-1"], [(1, "m-1")], ["m-1"]),
]
GU = [ints([1, 17], [1, 17]), ints([], [1, 17], table=False), withdraw(1)]
GC1 = [
    ints([1, 17], [1, 17]),
    ints([1, 17], [1], declare=False),
    ints([1], ["1", "00017"], table=False, edt="string"),
]
TG = [
    lib(
        M2,
        [("001", "m-1"), ("002", "m-17")],
        ["001", "002"],
        ldt="string",
        adt="string",
        declare=True,
    ),
    lib(["m-1"], [(1, "m-1")], ["1", "2"], adt="string"),
]
TC = [
    lib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], declare=True),
    lib(["m-1"], [(1, "m-1"), (2, "m-1")], ["1", "002"], adt="string"),
]
SS = [
    selfscope(M2, [(1, "m-1"), (2, "m-17")], [("m-1", 1), ("m-17", 2)]),
    selfscope(M2, [(1, "m-1"), (2, "m-17")], [("m-1", 1)], cover=False),
    selfscope(["m-1"], [(1, "m-1")], [("m-1", 1), ("x-0", 2)], cover=False),
    selfscope(["m-1"], [(1, "m-1")], [("m-1", 1)], cover=False),
    withdraw(1),
]
WIDE = tuple(range(1, 17))
ZERO = (0,) * 16
ENROLLED = {"table": "enrolled", "parent_columns": {"member_id": "member_id"}}

FREE_1 = [
    visits_keyed(
        M2, [("a", "b", "m-1"), ("s1", "v2", "m-17")], [("a", "b")], rekeyed=False, declare=True
    ),
    visits_keyed(M2, [(1, "a", "b", "m-1"), (2, "s1", "v2", "m-17")], [("a", "b")], rekeyed=True),
    withdraw(1),
]
"""R1 (visits keyed (k0, k1), declares the coverage) and R2 (re-keyed by vid) hold the person's
visit ('s1', 'v2'), R1 withdrawn by hand: K1 is the visit's (k0, k1), and no live release's key."""
FREE_3 = [(1, "a", "b", "m-1"), (3, "s1", "v2", "m-1")]
"""The latest's visits: another member's visit has the person's (k0, k1)."""
VT_REKEYED = [(1, "a", "b", "m-1")]
ANCHOR_1 = [
    visits_keyed(
        M2,
        [("a", "b", "m-1"), ("s1", "v2", "m-17"), ("s1", "v3", "m-17")],
        [("a", "b")],
        rekeyed=False,
        declare=True,
    ),
    visits_keyed(
        M2,
        [(1, "a", "b", "m-1"), (2, "s1", "v2", "m-17"), (4, "s1", "v3", "m-17")],
        [("a", "b")],
        rekeyed=True,
    ),
    withdraw(1),
]
"""As ``FREE_1``, with a second visit of the person's that shares its k0: each K1 key is
anchored at its k1, position 1."""

Case = tuple[str, list[Step], str, list[SourceValue], dict[str, Any], str]
CASES: list[Case] = [
    # repro.py: a direct coverage left listing the member, its declaration removed or proposed.
    *[
        (
            f"repro {variant}",
            [
                fx.assessed(M2, [(1, "m-1"), (2, "m-17")], M2),
                fx.assessed(["m-1"], [(1, "m-1")], M2, variant),
            ],
            "members",
            ["m-17"],
            {},
            "ERASURE_BLOCKED E [2] [(1, (2,))]",
        )
        for variant in ("removed", "proposed")
    ],
    # repro2.py: a grouped coverage's assignment table left listing a checklist of the site.
    *[
        (
            f"repro2 grouped, declared={declare}",
            [
                fx.checklists(
                    ["s1", "s2"],
                    [("c1", "s1"), ("c2", "s2")],
                    [("c1", "wren"), ("c2", "wren")],
                    [("c1", "all"), ("c2", "all")],
                ),
                fx.checklists(
                    ["s2"],
                    [("c2", "s2")],
                    [("c2", "wren")],
                    [("c1", "all"), ("c2", "all")],
                    declare=declare,
                ),
            ],
            "sites",
            ["s1"],
            {},
            "ERASURE_BLOCKED E [2] [(1, (1,))]",
        )
        for declare in (False,)
    ],
    # A list cell's items are values (PR #88's review, M1), every datatype of a cover column.
    *[
        (
            f"LIST-COVER {name}",
            [R1, enrol(["m-1"], [(1, "m-1")], listed, declare=False, edt=edt)],
            "members",
            ["m-17"],
            {},
            expected,
        )
        for name, edt, listed, expected in (
            (
                "list<category>",
                "list<category>",
                ["m-1; m-17"],
                "ERASURE_BLOCKED E [2] [(1, (1,))]",
            ),
            (
                "list<category>, no space",
                "list<category>",
                ["m-3;m-17;m-1"],
                "ERASURE_BLOCKED E [2] [(1, (1,))]",
            ),
            ("list<category>, control", "list<category>", ["m-1; m-3"], "done (1,) terms=2"),
            ("string, control", "string", ["m-1", "m-17"], "ERASURE_BLOCKED E [2] [(1, (2,))]"),
            ("category", "category", ["m-1", "m-17"], "ERASURE_BLOCKED E [2] [(1, (2,))]"),
        )
    ],
    (
        "LIST-COVER composite",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            fx.ab_lists([(5, 5)], [("5", "5"), ("1; 7", "2; 5")]),
        ],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "LIST-COVER composite, control",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            fx.ab_lists([(5, 5)], [("5", "5"), ("3; 7", "2; 5")]),
        ],
        "members",
        [2, 1],
        {},
        "done (1,) terms=2",
    ),
    # Spellings D290 reads alike (m3): NFD, format characters, surrounding spaces; case is not.
    *[
        (
            f"SPELLING {name}",
            [R1, enrol(["m-1"], [(1, "m-1")], ["m-1", listed], declare=False)],
            "members",
            ["m-17"],
            {},
            expected,
        )
        for name, listed, expected in (
            ("zero-width space", "m-\u200b17", "ERASURE_BLOCKED E [2] [(1, (2,))]"),
            ("trailing space", "m-17 ", "ERASURE_BLOCKED E [2] [(1, (2,))]"),
            ("case (R1)", "M-17", "done (1,) terms=2"),
        )
    ],
    (
        "SPELLING NFD",
        [
            enrol(["m-1", "jos\u00e9"], [(1, "m-1"), (2, "jos\u00e9")], ["m-1", "jos\u00e9"]),
            enrol(["m-1"], [(1, "m-1")], ["m-1", "jose\u0301"], declare=False),
        ],
        "members",
        ["jos\u00e9"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    # A re-keyed person's table that keeps the old key as a column (m1): the parent row found
    # is the person's own, which D223 does not find by the old key; a forming hit refuses.
    (
        "REKEY-PARENT",
        [R1, rekeyed([("x-1", "m-1"), ("x-9", "m-17")], M2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "REKEY-PARENT shape in a release that holds the person: B1 alone",
        [enrol(M2, [(1, "m-1"), (2, "m-17")], ["m-17", "m-1"])],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED B1",
    ),
    (
        "REKEY-PARENT control (FORMER-KEY shape)",
        [R1, rekeyed([("x-1", "m-1")], M2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "REKEY-PARENT by the new key: @1 names the person by the old",
        [R1, rekeyed([("x-1", "m-1"), ("x-9", "m-17")], M2), rekeyed([("x-1", "m-1")], ["m-1"])],
        "members",
        ["x-9"],
        {},
        "ERASURE_BLOCKED E [1] [(1, (2,))]",
    ),
    (
        "REKEY-PARENT remedy: @1 withdrawn by hand, erase by the new key",
        [
            R1,
            rekeyed([("x-1", "m-1"), ("x-9", "m-17")], M2),
            rekeyed([("x-1", "m-1")], ["m-1"]),
            withdraw(1),
        ],
        "members",
        ["x-9"],
        {},
        "done (2,) terms=1",
    ),
    # redact_only withdraws a release that holds the person (m2).
    (
        "RO-VALID",
        [
            R1,
            enrol(M2, [(1, "m-1"), (3, "m-17")], M2),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            withdraw(1),
        ],
        "members",
        ["m-17"],
        {"redact_only": True},
        "done (2,) terms=2",
    ),
    # The mutants of m4.
    (
        "RO-NOTHING-WITHDRAWN",
        [enrol(["m-1"], [(1, "m-1")], M2, declare=False), enrol(["m-1"], [(1, "m-1")], ["m-1"])],
        "members",
        ["m-17"],
        {"redact_only": True},
        "INVALID_KEY No published release holds that row, and no erasure of it waits; redact_only "
        "needs a release withdrawn earlier, and there is none",
    ),
    (
        "FORMER-KEY, the former key's release without the cover table",
        [
            enrol(M2, [(1, "m-1")], M2),
            rekeyed([("x-1", "m-1"), ("x-9", "m-17")], [], cover=False),
            rekeyed([("x-1", "m-1")], M2),
            rekeyed([("x-1", "m-1")], ["m-1"]),
            withdraw(1),
        ],
        "members",
        ["x-9"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    # The relationships stay D223's (REG1 to REG4, EROSION, T2).
    (
        "REG1",
        [
            notes([1, 3, 17], [5], [("n1", 1)], "members"),
            notes([1, 17], [5, 17], [("n1", 17)], "staff"),
            erased("members", [3]),
            notes([1], [5, 17], [("n1", 17)], "staff"),
        ],
        "members",
        [17],
        {},
        "done (2,) terms=1",
    ),
    (
        "REG3",
        [
            notes([1, 3, 17], [5], [("n1", 1)], "members"),
            notes([1, 17], [5], [("n1", 17)], None),
            erased("members", [3]),
            notes([1], [5], [("n1", 17)], None),
        ],
        "members",
        [17],
        {},
        "done (2,) terms=1",
    ),
    (
        "REG4",
        [
            notes([1, 17], [1, 17], [("n1", 1), ("n17", 17)], "members"),
            notes([1], [1, 17], [("n1", 1), ("n17", 17)], "staff"),
        ],
        "members",
        [17],
        {},
        "ERASURE_BLOCKED B1",
    ),
    (
        "REG4b",
        [
            notes([1, 17], [1, 17], [("n1", 1), ("n17", 17)], "members"),
            notes([1], [1, 17], [("n1", 1), ("n17", 17)], "staff"),
            notes([1], [1, 17], [("n1", 1)], "members"),
        ],
        "members",
        [17],
        {},
        "done (1, 2) terms=2",
    ),
    (
        "EROSION",
        [
            notes([1, 17], [5], [("n1", 1), ("n17", 17)], "members"),
            notes([1], [5], [("n1", 1), ("n17", 17)], None),
        ],
        "members",
        [17],
        {},
        "ERASURE_BLOCKED B1",
    ),
    (
        "T2",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [], declare=False),
            covlib(
                ["m-1"],
                [(1, "m-1"), (2, "m-1")],
                [],
                declare=False,
                fines_rel=False,
                fine_dtype="string",
                fines=[(1, "2")],
            ),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED B1",
    ),
    # The orphan rule, by canonical strings over the link's own columns.
    (
        "COV1",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(["m-1"], [(1, "m-1"), (2, "m-1")], [1, 2]),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "COV2",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(["m-1"], [(1, "m-1")], [1, 2], declare=False),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COV2-nonlatest",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(["m-1"], [(1, "m-1")], [1, 2], declare=False),
            covlib(["m-1"], [(1, "m-1")], [1]),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COV2-int-to-string",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(
                ["m-1"],
                [("1", "m-1")],
                ["1", "2"],
                declare=False,
                loan_dtype="string",
                aud_dtype="string",
            ),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COV3",
        [
            covlib(["m-1", "m-17", "z"], [(1, "m-1"), (2, "m-17"), (3, "z")], [1, 2, 3]),
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], declare=False),
            erased("members", ["z"]),
            covlib(["m-1"], [(1, "m-1")], [1, 2], declare=False),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    (
        "T1",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(
                ["m-1"], [("1", "m-1"), ("2", "m-1")], [1, 2], declare=False, loan_dtype="string"
            ),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "T1-orphan",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(["m-1"], [("1", "m-1")], [1, 2], declare=False, loan_dtype="string"),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "T1-number",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(
                ["m-1"], [(1, "m-1"), (2, "m-1")], [1.0, 2.0], declare=False, aud_dtype="number"
            ),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "OWN1",
        [
            enrol(M2, [(1, "m-1")], M2),
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    # The scan runs only for a valid, trusted or waiting key; INVALID_KEY is the base's.
    (
        "P4",
        [enrol(["m-1"], [(1, "m-1")], ["m-1"]), enrol(["m-1"], [(1, "m-1")], ["m-1"])],
        "members",
        ["flag_q1"],
        {},
        "INVALID_KEY No published release holds that row, and no erasure of it waits",
    ),
    (
        "P4b-cov",
        P4B,
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2, 3] [(1, (2,))]",
    ),
    (
        "P4c-cov",
        P4B,
        "members",
        ["m-17"],
        {},
        "INVALID_KEY No published release holds that row, and no erasure of it waits: check the "
        "key; if only a release withdrawn earlier held the person, erase with redact_only",
    ),
    (
        "RO-plain",
        [
            enrol(["m-1", "z"], [(1, "m-1")], ["m-1", "z"]),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            erased("members", ["z"]),
        ],
        "members",
        ["z"],
        {"redact_only": True},
        "done () terms=1",
    ),
    (
        "RO-finish",
        [
            R1,
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            erased("members", ["m-17"]),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
        ],
        "members",
        ["m-17"],
        {"redact_only": True, "uploads": lambda dataset: None},
        "done () terms=1",
    ),
    # Grouped and direct scope columns, of both datatypes.
    *[
        (
            f"G1 {dtype}",
            [
                grouped(
                    dtype,
                    [a, b],
                    [("t1", a), ("t2", b)],
                    [("t1", "A"), ("t2", "B")],
                    [("A", a), ("B", b)],
                ),
                grouped(dtype, [a], [("t1", a)], [("t1", "A"), ("t2", "B")], [("A", a), ("B", b)]),
            ],
            "members",
            [b],
            {},
            "ERASURE_BLOCKED E [2] [(3, (2,))]",
        )
        for dtype, a, b in (("integer", 1, 5), ("string", "p-1", "p-5"))
    ],
    *[
        (
            f"P7 {dtype}",
            [
                picked(dtype, [a, b], [("t1", a), ("t2", b)], [("t1", a), ("t2", b)]),
                picked(dtype, [a], [("t1", a)], [("t1", a), ("t2", b)]),
            ],
            "members",
            [b],
            {},
            "ERASURE_BLOCKED E [2] [(2, (2,))]",
        )
        for dtype, a, b in (("integer", 1, 5), ("string", "p-1", "p-5"))
    ],
    (
        "P6b",
        [
            scoped(
                M2,
                [(1, "m-1", "poetry"), (2, "m-17", "fiction")],
                [("m-1", "poetry"), ("m-17", "fiction")],
            ),
            scoped(["m-1"], [(1, "m-1", "poetry")], [("m-1", "poetry")]),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    # No parent table, or no such columns: every key is an orphan, and the wording notes it.
    (
        "NOTABLE-valid latest",
        [enrol(M2, [(1, "m-1"), (2, "m-17")], M2), bare(M2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "NOTABLE-valid nonlatest",
        [enrol(M2, [(1, "m-1"), (2, "m-17")], M2), bare(M2), enrol(["m-1"], [(1, "m-1")], ["m-1"])],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "NOTABLE-redact_only",
        [enrol(M2, [(1, "m-1"), (2, "m-17")], M2), bare(M2), withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "NOTABLE-redact_only, members back",
        [
            enrol(M2, [(1, "m-1"), (2, "m-17")], M2),
            bare(M2),
            withdraw(1),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
        ],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    # A coverage re-declared for another parent: no supersession, the hit refuses with NOTE.
    ("R10 latest", R10L, "members", ["m-17"], {}, "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE"),
    (
        "R10 nonlatest",
        [*R10L, es(["m-1"], M2, ["m-1"], "staff")],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "R10 twin",
        [
            es(M2, ["m-1", "s-5"], M2, "members"),
            es(["m-1"], ["m-1", "s-5"], ["m-1", "s-5"], "staff"),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=1",
    ),
    (
        "R10 control",
        [es(M2, M2, M2, "members"), es(["m-1"], M2, M2, None)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "R10 moot",
        [
            es(M2, M2, M2, "members", visits=(("1", "m-1"), ("2", "m-17"))),
            es(["m-1"], M2, M2, "staff", visits=(("1", "m-1"), ("2", "m-17")), visits_rel=False),
            es(["m-1"], M2, ["m-1"], "staff"),
        ],
        "members",
        ["m-17"],
        {},
        "done (1, 2) terms=2",
    ),
    (
        "X1 nonlatest",
        [*X1, es(["m-1"], M2, ["m-1"], None)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    ("X1 latest", X1, "members", ["m-17"], {}, "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE"),
    (
        "X1r registry-only conflict",
        [*X1, es(["m-1"], M2, ["m-1"], None), withdraw(2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "X2 relationship re-declaration",
        [
            es(M2, M2, M2, "members"),
            es(["m-1"], M2, M2, None, enrolled_rel="staff", enrolled_role="event"),
            es(["m-1"], M2, ["m-1"], None),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED key column, nonlatest",
        [R1, renamed(["m-1"], M2), enrol(["m-1"], [(1, "m-1")], ["m-1"])],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED redact_only",
        [R1, renamed(["m-1"], M2), withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED again",
        _again(["m-17"], [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"])], renamed(["m-1"], M2)),
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED valid latest",
        [R1, renamed(M2, ["m-1"]), renamed(["m-1"], M2), withdraw(1)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED valid nonlatest",
        [R1, renamed(M2, ["m-1"]), renamed(["m-1"], M2), renamed(["m-1"], ["m-1"]), withdraw(1)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "RENAMED valid control",
        [R1, renamed(M2, ["m-1"]), renamed(["m-1"], M2), renamed(["m-1"], ["m-1"])],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    # Composite keys: a multiset of the key, never enumerated permutations.
    (
        "COMPOSITE reversed order, redact_only",
        [P1, pairs([("north", 1)], [(1, "north"), (17, "north")], declare=False), withdraw(1)],
        "members",
        ["north", 17],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COMPOSITE D",
        [P1, pairs([], [(1, "north"), (17, "north")], table=False), withdraw(1)],
        "members",
        ["north", 17],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "COMPOSITE D control",
        [P1, pairs([], [], table=False, cover=False), withdraw(1)],
        "members",
        ["north", 17],
        {"redact_only": True},
        "done () terms=2",
    ),
    (
        "COMPOSITE D null-cover control",
        [P1, pairs([], [(None, None)], table=False), withdraw(1)],
        "members",
        ["north", 17],
        {"redact_only": True},
        "done () terms=2",
    ),
    (
        "D over-refusal",
        [P1, pairs([], [(1, "north")], table=False), withdraw(1)],
        "members",
        ["north", 17],
        {"redact_only": True},
        "done () terms=2",
    ),
    # The waiting path scans before it finishes (_again).
    (
        "AGAIN",
        _again(
            ["m-17"],
            [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"])],
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
        ),
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    (
        "AGAIN-D",
        _again(
            ["north", 17],
            [P1, pairs([("north", 1)], [(1, "north")])],
            pairs([], [(1, "north"), (17, "north")], table=False),
        ),
        "members",
        ["north", 17],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "AGAIN-loans",
        _again(
            ["m-17"],
            [covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]), covlib(["m-1"], [(1, "m-1")], [1])],
            covlib(["m-1"], [(1, "m-1")], [1, 2], declare=False),
        ),
        "members",
        ["m-17"],
        {},
        "done () terms=2",
    ),
    (
        "RO-again",
        [
            R1,
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            erased("members", ["m-17"]),
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
        ],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    (
        "RO-again without redact_only",
        [
            R1,
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            erased("members", ["m-17"]),
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
        ],
        "members",
        ["m-17"],
        {},
        "INVALID_KEY No published release holds that row, and no erasure of it waits: check the "
        "key; if only a release withdrawn earlier held the person, erase with redact_only",
    ),
    # The remedies the text names.
    (
        "RENAME-1",
        [*R10L, es_renamed(["m-1"], M2, M2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "RENAME-2",
        [*R10L, es_renamed(["m-1"], M2, M2), withdraw(2)],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=1",
    ),
    (
        "RENAME-3",
        [*R10L, withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "RENAME-4",
        [*R10L, es(["m-1"], M2, M2, "staff", visits=(("1", "m-1"), ("3", "m-1"))), withdraw(2)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    *[
        (
            f"INT-COLLISION {k}",
            [
                ints_staff(list(range(1, 11)), list(range(1, 11)), list(range(1, 11)), "members"),
                ints_staff(
                    [m for m in range(1, 11) if m != k],
                    list(range(1, 11)),
                    list(range(1, 11)),
                    "staff",
                ),
            ],
            "members",
            [k],
            {},
            f"ERASURE_BLOCKED E [2] [(1, ({k},))] NOTE",
        )
        for k in (2, 5, 9)
    ],
    # Scope columns compose with the recorded relationships and keys.
    ("SCOPE-SPLIT", SCOPE_SPLIT, "members", ["p-5"], {}, "ERASURE_BLOCKED E [2] [(2, (2,))]"),
    (
        "SCOPE-SPLIT control",
        [
            SCOPE_SPLIT[0],
            picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1"), ("t2", "p-5")]),
        ],
        "members",
        ["p-5"],
        {},
        "ERASURE_BLOCKED E [2] [(2, (2,))]",
    ),
    (
        "SCOPE-REG",
        [
            picked("string", PICK, [("t1", "p-1"), ("t2", "p-5")], [("t1", "p-1")], cover=False),
            picked("string", PICK, [("t1", "p-1")], [("t1", "p-1")], rel=False),
            picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1"), ("t2", "p-5")], cover=False),
            picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1")], cover=False),
            withdraw(2),
        ],
        "members",
        ["p-5"],
        {},
        "ERASURE_BLOCKED E [3] [(2, (2,))]",
    ),
    (
        "SCOPE-ERODED",
        [
            picked(
                "string",
                ["p-1", "p-5", "z"],
                [("t1", "p-1"), ("t2", "p-5"), ("t1", "z")],
                [("t1", "p-1"), ("t1", "z")],
                cover=False,
            ),
            picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1"), ("t2", "p-5")], rel=False),
            picked("string", PICK, [("t1", "p-1")], [("t1", "p-1")], rel=False, cover=False),
            picked("string", ["p-1"], [("t1", "p-1")], [("t1", "p-1")], rel=False, cover=False),
            erased("members", ["z"]),
        ],
        "members",
        ["p-5"],
        {},
        "ERASURE_BLOCKED E [2] [(2, (2,))]",
    ),
    (
        "SCOPE into a composite foreign key",
        [
            fx.checked(
                [(2, 1), (5, 5)], [(1, "s1", 5, 5), (2, "s1", 2, 1)], [("s1", 5, 5), ("s1", 2, 1)]
            ),
            fx.checked([(5, 5)], [(1, "s1", 5, 5)], [("s1", 5, 5), ("s1", 2, 1)], cover=False),
        ],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    ("SCOPE-SELF", SS, "members", ["m-17"], {}, "ERASURE_BLOCKED E [3] [(1, (2,))]"),
    # Renamed keys below the person, and renumbered ones (R8).
    (
        "BELOW-RENAMED",
        [*BELOW, withdraw(1)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "BELOW-RENAMED control",
        BELOW,
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "BELOW-RENUMBER-RENAMED",
        BRR,
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "BELOW-RENUMBER-RENAMED, R1 withdrawn",
        [*BRR, withdraw(1)],
        "members",
        ["m-17"],
        {},
        "done (2,) terms=2",
    ),
    (
        "BELOW-RENAMED-ORPHAN",
        [BRR[0], BRR[1], lib(["m-1"], [(1, "m-1")], [1, 2], key="lid"), BRR[3]],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "FORMER-KEY",
        [
            enrol(M2, [(1, "m-1")], M2),
            rekeyed([("x-1", "m-1"), ("x-9", "m-17")], ["m-1"]),
            rekeyed([("x-1", "m-1")], M2),
            rekeyed([("x-1", "m-1")], ["m-1"]),
            withdraw(1),
        ],
        "members",
        ["x-9"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    (
        "COMPOSITE-RENUMBER-RENAMED",
        CR,
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))] NOTE",
    ),
    (
        "COMPOSITE-RENUMBER-RENAMED nonlatest",
        [*CR, ab([(5, 5)], [(5, 5)], keys=("p", "q"))],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,)), (1, (1,))] NOTE",
    ),
    (
        "COMPOSITE-RENUMBER control",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(1, 2), (5, 5)], [(2, 1), (5, 5)], declare=False),
        ],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))]",
    ),
    (
        "COMPOSITE-RENAMED",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(5, 5)], [(2, 1), (5, 5)], keys=("p", "q"), declare=False),
        ],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))] NOTE",
    ),
    (
        "PERM-LEGIT",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(1, 2), (5, 5)], [(1, 2), (5, 5)], declare=False),
        ],
        "members",
        [2, 1],
        {},
        "done (1,) terms=2",
    ),
    # A composite key given in another spelling, typed by a live release that does not hold
    # the person: the typed key is a form of its own, which typing the cover value (one-column
    # links only) does not give (the mutant ``given_only``).
    (
        "COMPOSITE GIVEN-SPELLING",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(5, 5)], [(5, 5), (2, 1)], declare=False),
            withdraw(1),
        ],
        "members",
        ["2", "01"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    ("PERM-COLLISION", PC, "members", [1, 2], {}, "ERASURE_BLOCKED E [2] [(1, (2,))]"),
    ("PERM-COLLISION remedy", [*PC, withdraw(2)], "members", [1, 2], {}, "done (1,) terms=2"),
    ("PERM-COLLISION other", PC, "members", [2, 1], {}, "ERASURE_BLOCKED E [2] [(1, (2,))]"),
    # Typed forms, both ways, by every datatype of the link's columns (one-column links).
    (
        "BELOW-TYPED",
        [
            lib(
                M2,
                [("001", "m-1"), ("002", "m-17")],
                ["001", "002"],
                declare=True,
                ldt="string",
                adt="string",
            ),
            lib(["m-1"], [("001", "m-1")], [1, 2], ldt="string"),
            lib(["m-1"], [("001", "m-1")], [1], ldt="string"),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "GIVEN-SPELLING",
        GU,
        "members",
        ["00017"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "GIVEN-SPELLING control",
        GU,
        "members",
        [17],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "GIVEN-SPELLING live table",
        [
            ints([1, 17], [1, 17]),
            ints([1, 17], [1, 17], declare=False),
            ints([1], [1, 17], declare=False),
        ],
        "members",
        ["00017"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
    ),
    (
        "GIVEN (a)",
        [ints([1, 17], [1, 17]), ints([], [1, 17], table=False), withdraw(1)],
        "members",
        ["00017"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    (
        "GIVEN (b)",
        [
            ints(["m-1", "00017"], ["m-1", "00017"], mdt="string", edt="string"),
            ints([], ["m-1", "00017"], table=False, edt="string"),
            withdraw(1),
        ],
        "members",
        ["00017"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))] NOTE",
    ),
    ("GIVEN (c1)", GC1, "members", ["00017"], {}, "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE"),
    (
        "GIVEN (c2)",
        [
            ints([1, 17], [1, 17]),
            ints([1, 17], [1], declare=False),
            ints([1], ["1", "17"], table=False, edt="string"),
        ],
        "members",
        ["00017"],
        {},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    ("GIVEN (c3)", GC1, "members", [17], {}, "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE"),
    (
        "GIVEN (c4)",
        [
            ints([1, 17], [1, 17]),
            ints([1], ["1"], declare=False, edt="string"),
            ints([1], ["1", "17"], table=False, edt="string"),
            withdraw(1),
        ],
        "members",
        ["00017"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    ("TYPED-GAP", TG, "members", ["m-17"], {}, "ERASURE_BLOCKED E [2] [(1, (2,))]"),
    (
        "TYPED-GAP control",
        [TG[0], lib(["m-1"], [(1, "m-1")], ["1", "002"], adt="string")],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "TYPED-GAP mirror",
        [
            lib(M2, [(1, "m-1"), (2, "m-17")], [1, 2], declare=True),
            lib(["m-1"], [("001", "m-1")], ["001", "002"], ldt="string", adt="string"),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    ("TYPED-COLLISION", TC, "members", ["m-17"], {}, "ERASURE_BLOCKED E [2] [(1, (2,))]"),
    (
        "TYPED-COLLISION control",
        [TC[0], lib(["m-1"], [(1, "m-1"), (2, "m-1")], ["1", "2"], adt="string")],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "TYPED-COLLISION remedy",
        [*TC, lib(["m-1"], [(1, "m-1")], [1]), withdraw(2)],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "TYPED-COLLISION rename without re-declaring",
        [*TC, lib(["m-1"], [(1, "m-1"), (2, "m-1")], [])],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COMPOSITE-TYPED (R6)",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(5, 5)], [("02", "1"), ("5", "5")], declare=False, edt="string"),
        ],
        "members",
        [2, 1],
        {},
        "done (1,) terms=2",
    ),
    (
        "COMPOSITE-TYPED control",
        [
            ab([(2, 1), (5, 5)], [(2, 1), (5, 5)]),
            ab([(5, 5)], [("2", "1"), ("5", "5")], declare=False, edt="string"),
        ],
        "members",
        [2, 1],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))]",
    ),
    # Arity: a key matches a link of its own number of columns.
    (
        "ARITY-SUBSET",
        [
            region([("north", 1), ("north", 17)], [("north", 1)]),
            region([("north", 1), ("north", 17)], [("north", 1), ("north", 99)], declare=False),
            region([("north", 1)], [("north", 1)]),
        ],
        "members",
        ["north", 17],
        {},
        "done (1, 2) terms=2",
    ),
    (
        "ARITY-SUBSET-1",
        [
            onecol([1, 17], [1, 17]),
            region2([("north", 1), ("north", 17)], [1]),
            region2([("north", 1)], [1, 17]),
            region2([("north", 1)], [1]),
            withdraw(1),
        ],
        "members",
        ["north", 17],
        {},
        "done (2,) terms=2",
    ),
    # Stated residuals: a renamed table (R6) and a row D223 does not follow (its erosion).
    ("TABLE-RENAMED", [*TREN, withdraw(1)], "patrons", ["m-17"], {}, "done (2,) terms=1"),
    ("TABLE-RENAMED, no hand withdrawal", TREN, "patrons", ["m-17"], {}, "done (2,) terms=1"),
    (
        "TABLE-RENAMED remedy",
        [*TREN, withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [3] [(1, (2,))] NOTE",
    ),
    (
        "TABLE-RENAMED re-declared",
        [
            enrol(M2, [(1, "m-1")], M2),
            patrons(M2, [(1, "m-1")], M2, declare=True),
            patrons(["m-1"], [(1, "m-1")], M2),
            patrons(["m-1"], [(1, "m-1")], ["m-1"]),
        ],
        "patrons",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [1, 3] [(1, (2,)), (1, (2,))] NOTE",
    ),
    (
        "STALE-LINK",
        [
            surr([1, 2, 3], [1, 2, 3], [1, 2, 3]),
            surr([], [1, 2, 3], [1, 2, 3], with_members=False),
            surr([], [1, 3], [1, 2, 3], with_members=False),
            withdraw(1),
        ],
        "staff",
        [2],
        {},
        "done (2,) terms=1",
    ),
    (
        "IGNORED-ROW",
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            mem(["m-1"], [(1, "m-1"), (2, "m-17")], [1, 2]),
        ],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=2",
    ),
    # One reader of a cover value (PR #88's review round 2, M1): a list's items are read as a
    # scalar cell is, in a cover of several columns too, its spellings included (the space a
    # delimited list keeps, NFD, a format character); a reading whose raw form is a parent key
    # is passed over, exactly.
    *[
        (
            f"COMP-{shape}-{spelling}",
            [
                ab_text([("c1", "m-1"), (part, value)], [("c1", "m-1"), (part, value)]),
                ab_text([("c1", "m-1")], [("c1", cell)], declare=False, ydt=ydt),
            ],
            "members",
            [part, value],
            {},
            "ERASURE_BLOCKED E [2] [(1, (1,))]",
        )
        for part, value in [("c1", "m-17"), ("c1", JOSE)]
        for shape, ydt, written in [
            ("LIST", "list<category>", "m-1;{}"),
            ("SCALAR", "string", "{}"),
        ]
        for spelling, cell in [
            ("SPACE", written.format(" " + value).replace("; ", "; ").replace(";  ", "; ")),
            ("NFD", written.format(unicodedata.normalize("NFD", value))),
            ("ZWSP", written.format(value[:1] + "\u200b" + value[1:])),
        ]
        if (value == JOSE) == (spelling == "NFD")
    ],
    (
        "COMP-SCALAR-ZWSP-SPACE: a format character before a space (read, then stripped)",
        [AB1, ab_text([("c1", "m-1")], [("c1", "\u200b m-17")], declare=False)],
        "members",
        ["c1", "m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))]",
    ),
    (
        "COMP-LIST-REVERSED: the key in another order (K2)",
        [AB1, ab_text([("c1", "m-1")], [("m-17;zz", "c1;qq")], declare=False, xdt=LIST, ydt=LIST)],
        "members",
        ["c1", "m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))]",
    ),
    (
        "COMP-LIST-REVERSED-PARENT: the only order formed is another's key, exactly",
        [
            AB1,
            ab_text([("c1", "m-1"), ("m-17", "c1")], [("m-17", "c1;qq")], declare=False, ydt=LIST),
        ],
        "members",
        ["c1", "m-17"],
        {},
        "done (1,) terms=2",
    ),
    (
        "COMP-LISTED-K1-PARENT: a listed row's K1 reading is another's visit, exactly",
        [VT1, visits_text(["m-1"], [*VT_OTHER, ("sé-1", "vé-2", "m-1")], LISTED_VT, xdt=LIST)],
        "members",
        ["m-17"],
        {},
        "done (1,) terms=3",
    ),
    (
        "COMP-LISTED-TWO-SPELLINGS: one item is another's visit as written, one an orphan",
        [
            VT1,
            visits_text(
                ["m-1"],
                [*VT_OTHER, ("sé-1", "vé-2", "m-1")],
                [("a", "b"), ("zz;sé-1; sé-1", "vé-2")],
                xdt=LIST,
            ),
        ],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "COMP-LISTED-K1-PARENT, control: no such visit",
        [VT1, visits_text(["m-1"], VT_OTHER, LISTED_VT, xdt=LIST)],
        "members",
        ["m-17"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "REKEY-COMP-LISTED: erased by the new key, @2's listed row is the old key's parent (K1)",
        [
            ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1")]),
            ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1;m-17")], declare=False, ydt=LIST),
            ab_text([("x-1", "c1", "m-1"), ("x-9", "c1", "m-17")], [], declare=False, rekeyed=True),
            ab_text([("x-1", "c1", "m-1")], [], declare=False, rekeyed=True),
        ],
        "members",
        ["x-9"],
        {},
        "ERASURE_BLOCKED E [2] [(1, (1,))]",
    ),
    # The re-key hit by the given key (m1): a re-keyed release that keeps the person's old-keyed
    # row and lists it refuses, as it does without the row.
    (
        "REKEY-PARENT-RO: @2 keeps the old-keyed row and lists it",
        [R1, rekeyed([("x-1", "m-1"), ("x-17", "m-17")], M2), withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "REKEY-PARENT-RO, control: without the row",
        [R1, rekeyed([("x-1", "m-1")], M2), withdraw(1)],
        "members",
        ["m-17"],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    (
        "REKEY-PARENT-RO-NFD: the kept row and its listing spelled in NFD",
        [
            enrol(["m-1", JOSE], [(1, "m-1")], ["m-1", JOSE]),
            rekeyed([("x-1", "m-1"), ("x-17", JOSE_NFD)], ["m-1", JOSE_NFD]),
            withdraw(1),
        ],
        "members",
        [JOSE],
        {"redact_only": True},
        "ERASURE_BLOCKED E [2] [(1, (2,))]",
    ),
    # K1 is not K2 (PR #88's review round 3, m1): a table re-keyed, the declaring release
    # withdrawn, so the person's key over the cover's columns is in no live release's key.
    *[
        (
            f"FREE-1: R3 lists x {x!r} (K1 only; a visit of another's holds the reading 's1')",
            [
                *FREE_1,
                visits_keyed(["m-1"], FREE_3, [("a", "b"), (x, "v2")], rekeyed=True, xdt=xdt),
            ],
            "members",
            ["m-17"],
            {},
            expected,
        )
        for x, xdt, expected in (
            ("s1; s1", LIST, "ERASURE_BLOCKED E [3] [(1, (2,))]"),
            ("s1;zz", LIST, "done (2,) terms=2"),
            (" s1", "string", "ERASURE_BLOCKED E [3] [(1, (2,))]"),
            ("s1", "string", "done (2,) terms=2"),
        )
    ],
    *[
        (
            f"ANCHOR-1: the person's two visits share k0, R3 lists x {x!r}, y 'v3'",
            [
                *ANCHOR_1,
                visits_keyed(["m-1"], VT_REKEYED, [("a", "b"), (x, "v3")], rekeyed=True, xdt=LIST),
            ],
            "members",
            ["m-17"],
            {},
            expected,
        )
        for x, expected in (
            ("s1;zz", "ERASURE_BLOCKED E [3] [(1, (2,))]"),
            ("zz;qq", "done (2,) terms=3"),
        )
    ],
    (
        "PARTIAL-BELOW: a listed row holds only the first part of the person's visit key",
        [
            *FREE_1,
            visits_keyed(["m-1"], FREE_3, [("a", "b"), ("s1;zz", "qq")], rekeyed=True, xdt=LIST),
        ],
        "members",
        ["m-17"],
        {},
        "done (2,) terms=2",
    ),
    *[
        (
            f"REKEY-COMP-SCALAR: the own table re-keyed, @2 lists {listed} (K1, a parent holds it)",
            [
                ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1")]),
                ab_text([("c1", "m-1"), ("c1", "m-17")], [listed], declare=False),
                ab_text(
                    [("x-1", "c1", "m-1"), ("x-9", "c1", "m-17")], [], declare=False, rekeyed=True
                ),
                ab_text([("x-1", "c1", "m-1")], [], declare=False, rekeyed=True),
            ],
            "members",
            ["x-9"],
            {},
            expected,
        )
        for listed, expected in (
            (("c1", "m-17"), "ERASURE_BLOCKED E [2] [(1, (1,))]"),
            (("c1", "m-1"), "done (3,) terms=1"),
        )
    ],
    (
        "PARTIAL-OWN: a listed row holds only the first part of the person's old key",
        [
            ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1")]),
            ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1;zz", "q9")], declare=False, xdt=LIST),
            ab_text([("x-1", "c1", "m-1"), ("x-9", "c1", "m-17")], [], declare=False, rekeyed=True),
            ab_text([("x-1", "c1", "m-1")], [], declare=False, rekeyed=True),
        ],
        "members",
        ["x-9"],
        {},
        "done (3,) terms=1",
    ),
    # A residual, pinned (issue #95; the extra review round's M1): a change of the key's ORDER
    # alone is NOT detected as a re-key. R1 withdrawn by hand, R2 keyed (b, a) keeps the row
    # (a, b) = (c1, m-17) and lists it, and the erasure completes, as the code stands, although
    # the live latest holds and lists the row (the given key is read in R1's order, the
    # coverage's columns order their pairs by their own names, and no release's key order is
    # recorded): a fix records each release's key tuple (#95) and turns this case into a refusal.
    *[
        (
            f"KEY-ORDER-RESIDUAL (#95): R2 keyed (b, a) lists {listed}, redact_only",
            [
                ab_text([("c1", "m-1"), ("c1", "m-17")], [("c1", "m-1")]),
                ab_text([("c1", "m-1"), ("c1", "m-17")], [listed], declare=False, reordered=True),
                withdraw(1),
            ],
            "members",
            ["c1", "m-17"],
            {"redact_only": True},
            "done () terms=2",
        )
        for listed in (("c1", "m-17"), ("c1", "m-1"))
    ],
]

RESIDUAL: dict[str, list[int]] = {
    # Another table's key written alike: staff member m-17 is not the member m-17.
    "R10 moot": [3],
    "RENAME-2": [3],
    # R6: a person erased under a table that was renamed (erase the old name with redact_only).
    "TABLE-RENAMED": [3],
    "TABLE-RENAMED, no hand withdrawal": [1, 3],
    # D223's erosion: a loan whose member column no relationship follows (until M4.0e-1c).
    "IGNORED-ROW": [2],
}
"""The live labels that still hold a completed erasure's key, by case: stated residuals."""
SPELLED_OTHERWISE = {
    "GIVEN-SPELLING",
    "GIVEN-SPELLING live table",
    "GIVEN (a)",
    "GIVEN (c2)",
    "GIVEN (c4)",
    "REKEY-PARENT-RO-NFD: the kept row and its listing spelled in NFD",
}
"""Cases whose key is given in a spelling the data does not use: no blob holds its text."""

REFUSED_IMPORTS: list[tuple[str, list[Step], list[str]]] = [
    (
        "X2c a coverage table as a relationship's child",
        [es(M2, M2, M2, "members"), es(["m-1"], M2, M2, None, enrolled_rel="staff")],
        ["INVALID_VALUE"],
    ),
    (
        "TYPED-COLLISION rename and re-declare",
        [*TC, libr(["m-1"], [(1, "m-1"), (2, "m-1")], ["1", "002"])],
        ["COVERAGE_UNKNOWN"],
    ),
]


@pytest.mark.parametrize(
    ("steps", "table", "key", "given", "expected"),
    [pytest.param(*case[1:], id=case[0]) for case in CASES],
)
def test_an_erasure_through_coverage_and_scope_tables(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    steps: list[Step],
    table: str,
    key: list[SourceValue],
    given: dict[str, Any],
    expected: str,
) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, steps)
        before = state(store)
        text = key[0] if len(key) == 1 and isinstance(key[0], str) else None
        raw = text is not None and len(text) >= 3 and not text.isdigit()
        held = fx.live_holds(store, text, raw=raw) if text is not None else []
        got = attempt(store, table, key, **given)
        assert got == expected
        name = request.node.callspec.id
        if text is not None and name not in SPELLED_OTHERWISE:
            # The oracle, from the blobs: a key a live release holds before (unless only
            # releases withdrawn already held it, ``done ()``, or the key is unknown), and after
            # a completion only where a stated residual leaves it.
            left = fx.live_holds(store, text, raw=raw)
            if not got.startswith(("INVALID_KEY", "done ()")):
                assert held
            assert left == (RESIDUAL.get(name, []) if got.startswith("done") else held)
        if not got.startswith("done"):
            assert state(store) == before  # a refusal changes nothing
        else:  # only the releases named were withdrawn now
            now = dict(state(store)["labels"])
            named = {int(n) for n in re.findall(r"\d+", got.split(")")[0])}
            assert {lab for lab, was in before["labels"] if not was and now[lab]} == named


@pytest.mark.parametrize(
    ("steps", "codes"), [pytest.param(*case[1:], id=case[0]) for case in REFUSED_IMPORTS]
)
def test_the_gate_refuses_what_a_remedy_cannot_be(
    tmp_path: Path, steps: list[Step], codes: list[str]
) -> None:
    with fx.opened(tmp_path / "data") as store, pytest.raises(BuildRefused) as refused:
        fx.run(store, steps)
    assert [str(r.code) for r in refused.value.refusals] == codes


def test_a_completed_erasure_leaves_the_key_in_no_live_release(tmp_path: Path) -> None:
    """The oracle reads the blobs: the key is held before, by the releases the person's rows
    are in, and by none after."""
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"])])
        assert fx.live_holds(store, "m-17") == [1]
        assert attempt(store, "members", ["m-17"]) == "done (1,) terms=2"
        assert fx.live_holds(store, "m-17") == []


def test_a_refused_erasure_leaves_the_listing_live_until_its_remedy(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, [R1, enrol(["m-1"], [(1, "m-1")], M2, declare=False)])
        assert fx.live_holds(store, "m-17") == [1, 2]
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2] [(1, (2,))]"
        assert fx.live_holds(store, "m-17") == [1, 2]
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
        store.withdraw("lib", 2, ADA)
        assert attempt(store, "members", ["m-17"]) == "done (1,) terms=2"
        assert fx.live_holds(store, "m-17") == []


@contextlib.contextmanager
def _work(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """The work of every row of a cover of several columns matched meanwhile (``_Work``: its
    values and what it spent: the keys and orders tested and the matchings)."""
    rows: list[Any] = []
    made = erasure._Work.__init__  # pyright: ignore[reportPrivateUsage]

    def recording(self: Any, values: int) -> None:
        made(self, values)
        rows.append(self)

    with monkeypatch.context() as patched:
        patched.setattr(erasure._Work, "__init__", recording)  # pyright: ignore[reportPrivateUsage]
        yield rows


def test_a_sixteen_value_key_matches_as_a_multiset_quickly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                wide([WIDE, ZERO], [WIDE, ZERO]),
                wide([ZERO], [ZERO, tuple(reversed(WIDE))], declare=False),
                wide([ZERO], [ZERO]),
            ],
        )
        with _work(monkeypatch) as rows:
            assert attempt(store, "members", list(WIDE)) == "ERASURE_BLOCKED E [2] [(1, (2,))]"
        assert rows == []  # a row of one value per position has one reading: no search


# --- Finish, then refuse (4.4 step 8) -----------------------------------------------------------

LATER = enrol(["m-1"], [(1, "m-1")], M2, declare=False)
CALLS: list[str] = []


def _hook(dataset: str) -> None:
    CALLS.append(dataset)


def _boom(dataset: str) -> None:
    CALLS.append(dataset)
    raise OSError("upload area busy")


def _fails(key: Sequence[SourceValue] = ("m-17",), held: list[Any] | None = None) -> Step:
    """An erasure that commits and whose upload deletion fails."""

    def step(store: Store) -> None:
        if held is not None:
            pin = store.pin()
            pin.manifest(store.labels("lib")[0].manifest)
            held.append(pin)
        with contextlib.suppress(OSError):
            erase(store, "lib", "members", list(key), ADA, uploads=_boom)

    return step


FIN: list[tuple[str, list[Step], dict[str, Any], str, int, dict[str, Any], dict[str, Any]]] = [
    (
        "FIN-1",
        [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"]), _fails(), LATER],
        {"redact_only": True, "uploads": _hook},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
        1,
        {"upload_pending": True, "pending": 0},
        {"upload_pending": False, "pending": 0},
    ),
    (
        "FIN-2",
        [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"]), _fails(held=[]), LATER],
        {"uploads": _hook},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
        1,
        {"upload_pending": True, "pending": 1},
        {"upload_pending": False, "pending": 1},
    ),
    (
        "FIN-3",
        [
            enrol(["m-1", "z"], [(1, "m-1")], ["m-1", "z"]),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
            erased("members", ["z"]),
            LATER,
        ],
        {"redact_only": True, "uploads": _hook},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
        0,
        {"upload_pending": False, "pending": 0},
        {"upload_pending": False, "pending": 0},
    ),
    (
        "FIN-4",
        [
            enrol(["m-1", "m-17", "z"], [(1, "m-1"), (2, "m-17")], ["m-1", "m-17", "z"]),
            enrol(M2, [(1, "m-1")], M2),
            _fails(key=("z",)),
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
            enrol(["m-1"], [(1, "m-1")], ["m-1"]),
        ],
        {"uploads": _hook},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
        1,
        {"upload_pending": True, "pending": 0},
        {"upload_pending": False, "pending": 0},
    ),
    (
        "FIN-5",
        [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"]), _fails(), LATER],
        {"redact_only": True, "uploads": _boom},
        "raised OSError",
        1,
        {"upload_pending": True, "pending": 0},
        {"upload_pending": True, "pending": 0},
    ),
    (
        "FIN-6",
        [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"]), _fails(), LATER],
        {"redact_only": True},
        "ERASURE_BLOCKED E [3] [(1, (2,))]",
        0,
        {"upload_pending": True, "pending": 0},
        {"upload_pending": True, "pending": 0},
    ),
]


@pytest.mark.parametrize(
    ("steps", "given", "expected", "calls", "before", "after"),
    [pytest.param(*case[1:], id=case[0]) for case in FIN],
)
def test_a_committed_erasure_is_finished_before_the_refusal(
    tmp_path: Path,
    steps: list[Step],
    given: dict[str, Any],
    expected: str,
    calls: int,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, steps)
        CALLS.clear()
        was = state(store)
        assert {k: was[k] for k in before} == before
        try:
            got = attempt(store, "members", ["m-17"], **given)
        except OSError:
            got = "raised OSError"
        assert got == expected
        assert len(CALLS) == calls
        now = state(store)
        assert {k: now[k] for k in after} == after
        # Nothing new is withdrawn, audited or requested.
        assert (now["labels"], now["audit"]) == (was["labels"], was["audit"])


def _unpin_quietly(held: list[Any]) -> Step:
    """Release the pin while the redaction runner is held off, so the redaction still waits
    with no pin holding it."""

    def step(store: Store) -> None:
        run = store.run_pending_redactions
        store.run_pending_redactions = lambda: 0
        try:
            held.pop().release()
            store.housekept()
        finally:
            store.run_pending_redactions = run

    return step


def test_a_hook_that_raises_on_the_finishing_path_leaves_the_redaction_to_the_next_call(
    tmp_path: Path,
) -> None:
    """FIN-5w: the hook runs first, as in ``_again``; when it raises, the redaction still waits
    and the upload area is still to delete, so the next call recognises the key, finishes and
    refuses; then the key is unknown, and ``redact_only`` completes once the hit is cleared."""
    held: list[Any] = []
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                R1,
                enrol(["m-1"], [(1, "m-1")], ["m-1"]),
                _fails(held=held),
                LATER,
                _unpin_quietly(held),
            ],
        )
        assert (state(store)["pending"], state(store)["upload_pending"]) == (1, True)
        for _ in range(2):
            CALLS.clear()
            with pytest.raises(OSError, match="busy"):
                erase(store, "lib", "members", ["m-17"], ADA, uploads=_boom)
            assert len(CALLS) == 1
            assert (state(store)["pending"], state(store)["upload_pending"]) == (1, True)
        CALLS.clear()
        assert (
            attempt(store, "members", ["m-17"], uploads=_hook)
            == "ERASURE_BLOCKED E [3] [(1, (2,))]"
        )
        assert len(CALLS) == 1
        assert (state(store)["pending"], state(store)["upload_pending"]) == (0, False)
        again = attempt(store, "members", ["m-17"])
        assert again.startswith("INVALID_KEY")
        assert "redact_only" in again
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
        store.withdraw("lib", 3, ADA)
        assert attempt(store, "members", ["m-17"], redact_only=True) == "done () terms=1"


# --- Texts ---------------------------------------------------------------------------------------


def _refused(store: Store, table: str, key: list[SourceValue], **given: Any) -> str:
    with pytest.raises(StoreRefused) as refused:
        erase(store, "lib", table, key, ADA, **given)
    return fx.text_of(refused.value)


def test_text_e_names_every_label_and_the_remedies(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, P4B)
        text = _refused(store, "members", ["m-17"], redact_only=True)
    assert text == (
        "A live release names the key in a coverage or scope table: @2, @3 (table 1, row 2). A "
        "coverage or scope table never withdraws a release by itself. To clear this, re-import "
        "from a source without the key; for a release that is not the latest, withdraw it "
        "yourself (withdrawing is irreversible and is your decision); withdrawing the release "
        "that declared the columns does not clear it, because the declaration is kept. Then "
        "erase again; if that is refused as an unknown key, use redact_only."
    )


def test_text_e_with_a_collision_names_the_rename_remedy(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, R10L)
        text = _refused(store, "members", ["m-17"])
    assert text.endswith(
        " The hit may be a collision of keys: those columns are declared for another parent, or "
        "the release has no table or no such columns for the parent (a renamed key). If the key "
        "belongs to the other declaration, re-import with that column or table named otherwise, "
        "so the old declaration no longer applies (your judgement, like a withdrawal), or wait "
        "for the operator record of M4.0e-1c."
    )


def test_text_b1_adds_the_coverage_tables_of_the_latest_release(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        # The person's loan 3 in @1; @2, the latest, holds them and lists loan 3, an orphan there.
        fx.publish(store, covlib(M2, [(1, "m-1"), (3, "m-17")], [1, 3]))
        fx.publish(store, covlib(M2, [(1, "m-1"), (2, "m-17")], [3, 1, 3], declare=False))
        text = _refused(store, "members", ["m-17"])
        assert text == (
            fx.B1 + ". It also names the key in a coverage or scope table: table 1, rows 1, 3"
        )
        fx.publish(store, covlib(M2, [(1, "m-1"), (3, "m-17")], [3, 1], declare=False))
        assert _refused(store, "members", ["m-17"]) == fx.B1  # the listing finds its parent


def test_a_hit_names_its_first_three_rows_only(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, [R1, enrol(["m-1"], [(1, "m-1")], ["m-17"] * 5 + ["m-1"], declare=False)])
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]"


# --- The registry ---------------------------------------------------------------------------------


def _registry(store: Store) -> list[tuple[str, list[tuple[Any, ...]]]]:
    rows: list[tuple[str, list[tuple[Any, ...]]]] = []
    for name in ("cover_links", "cover_scopes", "cover_graph"):
        found = store.db.connection.execute(f"SELECT * FROM {name} ORDER BY id").fetchall()
        rows.append((name, [tuple(row) for row in found]))
    return rows


def test_publishing_records_the_links_scopes_and_graph(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, picked("string", PICK, [("t1", "p-1")], [("t1", "p-1")]))
        covers, scopes, edges = store.db.cover_registry("lib")
    assert covers == {link("picked", ["tree_id"], "trees", ["tree_id"])}
    assert scopes == {Scope("picked", "picker", "pickings", "picker")}
    assert edges == {
        link("pickings", ["tree_id"], "trees", ["tree_id"]),
        link("pickings", ["picker"], "members", ["member_id"]),
        link("members", ["member_id"], "members", ["member_id"]),
        link("trees", ["tree_id"], "trees", ["tree_id"]),
        link("pickings", ["picker", "tree_id"], "pickings", ["picker", "tree_id"]),
    }


def test_a_session_s_publish_records_its_links(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, enrol(M2, [(1, "m-1")], M2, declare=False))
        assert store.db.cover_registry("lib")[0] == frozenset()
        opened = sessions.open_session(store, "lib", ADA)
        coverage = {
            "kind": "coverage",
            "id": "cov:visits.member_id",
            "label": "Enrolment",
            "fields": {"relationship": "rel:visits.member_id", "parents": ENROLLED},
        }
        request = ChangeRequest.model_validate({"edits": [{"op": "put", "descriptor": coverage}]})
        draft = sessions.change(store, "lib", opened.handle, opened.draft, request, ADA)
        sessions.publish(store, "lib", opened.handle, draft, ADA)
        assert store.db.cover_registry("lib")[0] == {
            link("enrolled", ["member_id"], "members", ["member_id"])
        }


def test_publishing_reads_the_descriptors_never_the_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fx.opened(tmp_path / "data") as store:

        def load(manifest: str) -> Any:
            raise AssertionError("commit_label loaded a release")

        monkeypatch.setattr(store, "load", load)
        fx.publish(store, R1)
        assert store.db.cover_registry("lib")[0]


def test_two_releases_declaring_one_coverage_in_two_orders_give_one_row(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, ab([(1, 2), (5, 5)], [(1, 2)]))
        descriptors, sources, layouts = ab([(1, 2), (5, 5)], [(1, 2)], declare=False)
        flipped = build.coverage(
            "rel:visits.a+b", {"table": "enrolled", "parent_columns": {"y": "b", "x": "a"}}
        )
        fx.publish(store, ([*descriptors, flipped], sources, layouts))
        rows = store.db.connection.execute(
            "SELECT child_columns, parent_columns FROM cover_links"
        ).fetchall()
    assert rows == [('["x","y"]', '["a","b"]')]


def test_registry_rows_keep_their_ids_and_are_never_removed_or_changed(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, picked("string", PICK, [("t1", "p-1")], [("t1", "p-1")]))
        first = _registry(store)
        fx.publish(store, picked("string", PICK, [("t1", "p-1")], [("t1", "p-1")]))  # again
        assert _registry(store) == first
        connection = store.db.connection
        for name in ("cover_links", "cover_scopes", "cover_graph"):
            with pytest.raises(sqlite3.IntegrityError, match="never removed"):
                connection.execute(f"DELETE FROM {name}")
            with pytest.raises(sqlite3.IntegrityError, match="never changed"):
                connection.execute(f"UPDATE {name} SET dataset = 'x'")
        # INSERT OR REPLACE would delete the row past the triggers and give it a new id.
        with store.db.transaction() as db:
            store.db.record_cover_links(db, "lib", store.db.cover_registry("lib")[0])
            store.db.record_cover_scopes(db, "lib", store.db.cover_registry("lib")[1])
            store.db.record_cover_graph(db, "lib", store.db.cover_registry("lib")[2])
        assert _registry(store) == first


def test_the_registry_writes_raise_on_a_row_that_breaks_a_check(tmp_path: Path) -> None:
    """``ON CONFLICT … DO NOTHING``, never ``OR IGNORE``, which drops such a row silently."""
    with fx.opened(tmp_path / "data") as store:
        bad: list[tuple[str, tuple[Any, ...]]] = [
            ("cover_links", ("lib", "e", "not json", "m", '["a"]')),
            ("cover_links", ("lib", "e", '["x"]', None, '["a"]')),
            ("cover_graph", ("lib", "e", '["x"]', "m", "not json")),
            ("cover_graph", ("lib", None, '["x"]', "m", '["a"]')),
        ]
        for name, row in bad:
            with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
                db.execute(
                    f"INSERT INTO {name}"
                    " (dataset, child_table, child_columns, parent_table, parent_columns)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT"
                    " (dataset, child_table, child_columns, parent_table, parent_columns)"
                    " DO NOTHING",
                    row,
                )
        with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
            db.execute(
                "INSERT INTO cover_scopes"
                " (dataset, scope_table, scope_column, child_table, child_column)"
                " VALUES ('lib', 't', NULL, 'c', 'k') ON CONFLICT"
                " (dataset, scope_table, scope_column, child_table, child_column) DO NOTHING"
            )
        # And through the store's own writes: a column list that is no list of names raises.
        nothing: Any = None
        with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
            store.db.record_cover_links(db, "lib", [Link("e", ("x",), nothing, ("a",))])
        with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
            store.db.record_cover_scopes(db, "lib", [Scope("t", nothing, "c", "k")])
        with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
            store.db.record_cover_graph(db, "lib", [Link("e", ("x",), nothing, ("a",))])
        assert _registry(store) == [("cover_links", []), ("cover_scopes", []), ("cover_graph", [])]


def test_a_non_canonical_spelling_gives_a_second_row(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        with store.db.transaction() as db:
            store.db.record_cover_links(db, "lib", [link("e", ["x"], "m", ["a"])])
            db.execute(
                "INSERT INTO cover_links"
                " (dataset, child_table, child_columns, parent_table, parent_columns)"
                " VALUES ('lib', 'e', '[ \"x\"]', 'm', '[\"a\"]')"
            )
        rows = store.db.connection.execute("SELECT id FROM cover_links").fetchall()
        assert len(rows) == 2
        assert store.db.cover_registry("lib")[0] == {link("e", ["x"], "m", ["a"])}


# --- Migration 8 and the backfill ------------------------------------------------------------


def _version_7(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: Sequence[Step]) -> Path:
    """A store made by the code before migration 8: its releases recorded no links."""
    root = tmp_path / "data"
    with monkeypatch.context() as patched:
        patched.setattr(appdb, "MIGRATIONS", MIGRATIONS[:7])
        patched.setattr(Store, "record_known_links", lambda self, dataset=None: None)
        patched.setattr(Store, "_record_links", lambda self, db, dataset, descriptors: None)
        store = Store(root, clock=fx.clock())
        try:
            assert store.db.version == 7
            fx.run(store, steps)
        finally:
            store.close()
    return root


def test_migration_8_queues_every_manifest_once_and_the_open_drains_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _version_7(
        tmp_path,
        monkeypatch,
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
        ],
    )
    raw = sqlite3.connect(root / "app.db")
    try:
        assert raw.execute("SELECT count(*) FROM labels").fetchone()[0] == 2  # one manifest
    finally:
        raw.close()
    queued: list[list[tuple[str, str]]] = []
    record = Store.record_known_links

    def recording(self: Store, dataset: str | None = None) -> None:
        queued.append(self.db.links_pending(dataset))
        record(self, dataset)

    monkeypatch.setattr(Store, "record_known_links", recording)
    with fx.opened(root) as store:
        assert store.db.version == len(MIGRATIONS) == 8
        assert len(queued[0]) == 1  # one row for the manifest the two labels share
        assert store.db.links_pending() == []
        assert store.db.cover_registry("lib")[0] == {
            link("audited", ["loan_id"], "loans", ["loan_id"])
        }


def test_the_backfill_runs_before_the_sweep_of_the_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release withdrawn under a pin, the store closed with the pin held: its descriptors are
    still stored at the next open, which records its links before its sweep deletes them."""

    def held_withdrawal(store: Store) -> None:
        pin = store.pin()
        pin.manifest(store.labels("lib")[0].manifest)
        store.withdraw("lib", 1, ADA)  # the pin keeps its blobs; close() keeps the pin

    root = _version_7(
        tmp_path,
        monkeypatch,
        [
            enrol(M2, [(1, "m-1"), (2, "m-17")], M2),
            enrol(["m-1"], [(1, "m-1")], M2, declare=False),
            held_withdrawal,
        ],
    )
    with fx.opened(root) as store:
        assert store.db.cover_registry("lib")[0] == {
            link("enrolled", ["member_id"], "members", ["member_id"])
        }
        assert (
            attempt(store, "members", ["m-17"], redact_only=True)
            == "ERASURE_BLOCKED E [2] [(1, (2,))]"
        )


def _damage(root: Path, manifest: str, how: str) -> str:
    """Damage a release's descriptors blob on disk, the store closed; its digest. ``unreadable``
    leaves the file whole (a test run as root reads any file): the test makes its read raise
    ``PermissionError``."""
    blobs = BlobStore(root)
    descriptors = Manifest.from_bytes(blobs.read(hex_of(manifest))).descriptors
    path = blobs.path(descriptors)
    if how == "missing":
        path.unlink()
    elif how == "symlink":  # ``BlobStore.read`` opens without following links: ELOOP
        path.unlink()
        path.symlink_to(root / "app.db")
    elif how == "directory":  # EISDIR
        path.unlink()
        path.mkdir()
    elif how == "corrupt":
        path.chmod(0o644)
        path.write_bytes(path.read_bytes()[:-1] + b"x")
    return descriptors


@pytest.mark.parametrize("how", ["corrupt", "missing", "symlink", "directory", "unreadable"])
def test_a_live_release_whose_descriptors_cannot_be_read_refuses_its_dataset_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    def other(store: Store) -> None:
        # Descriptors of their own: a blob two datasets share would be damaged for both.
        descriptors, sources, layouts = covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2])
        with store.pin() as pin:
            built = store.import_release(pin, "other", descriptors, sources, layouts)
            store.publish("other", built.manifest.hash, ADA)
            store.publish("other", built.manifest.hash, ADA)  # @1 and @2, one manifest

    root = _version_7(tmp_path, monkeypatch, [R1, enrol(["m-1"], [(1, "m-1")], ["m-1"]), other])
    raw = sqlite3.connect(root / "app.db")
    try:
        manifest = raw.execute("SELECT manifest FROM labels WHERE dataset = 'other'").fetchone()[0]
    finally:
        raw.close()
    descriptors = _damage(root, manifest, how)
    if how == "unreadable":
        read = BlobStore.read

        def refusing(self: BlobStore, digest: str, *, verify: bool = True) -> bytes:
            if digest == descriptors:
                raise PermissionError(13, "Permission denied")
            return read(self, digest, verify=verify)

        monkeypatch.setattr(BlobStore, "read", refusing)
    with fx.opened(root) as store:  # the store opens (m1: whatever the read raises, an OSError too)
        assert [m for m, _ in store.db.links_pending()] == [manifest]
        assert attempt(store, "members", ["m-17"]) == "done (1,) terms=2"
        with pytest.raises(StoreRefused) as refused:
            erase(store, "other", "members", ["m-17"], ADA)
        assert refused.value.refusal.code == "ERASURE_BLOCKED"
        text = fx.text_of(refused.value)
        assert text == (
            "The link registry is incomplete: the descriptors of @1, @2 cannot be read. Repair "
            "them or withdraw those releases, then erase again"
        )
        assert [m for m, _ in store.db.links_pending()] == [manifest]  # kept while live
        store.withdraw("other", 1, ADA)
        with pytest.raises(StoreRefused) as again:
            erase(store, "other", "members", ["m-17"], ADA)
        assert again.value.refusal.code == "UNKNOWN_RELEASE"  # no live release: not F
        assert store.db.links_pending() == []


def test_a_withdrawn_release_whose_descriptors_were_swept_leaves_the_queue_at_the_first_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _version_7(
        tmp_path,
        monkeypatch,
        [
            covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]),
            covlib(["m-1"], [(1, "m-1")], [1]),
            withdraw(1),
        ],
    )
    with fx.opened(root) as store:
        assert store.db.links_pending() == []
        # R4: its links are lost; the live release's are recorded.
        assert store.db.cover_registry("lib")[0] == {
            link("audited", ["loan_id"], "loans", ["loan_id"])
        }


def test_with_nothing_queued_the_backfill_reads_no_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, R1)
    reads: list[str] = []
    descriptors = Store.descriptors

    def counting(self: Store, manifest: str) -> Any:
        reads.append(manifest)
        return descriptors(self, manifest)

    monkeypatch.setattr(Store, "descriptors", counting)
    with fx.opened(tmp_path / "data") as store:
        store.record_known_links("lib")
        assert reads == []


# --- Labels, memoisation, the lock -------------------------------------------------------------


def test_the_refusal_names_every_label_of_a_manifest(tmp_path: Path) -> None:
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, R1)
        later = fx.publish(store, LATER)
        store.publish("lib", later, ADA)  # @2 and @3, one manifest
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2, 3] [(1, (2,))]"


def test_the_orphans_of_a_link_in_a_release_are_found_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Across the latest check, the scan and the refusal's text (``cover_hits`` is memoised)."""
    calls: list[tuple[str, Any]] = []
    found = erasure._cover_rows  # pyright: ignore[reportPrivateUsage]

    def counting(release: Any, of: Any, keys: Any, **given: Any) -> Any:
        calls.append((release.manifest, of))
        return found(release, of, keys, **given)

    monkeypatch.setattr(erasure, "_cover_rows", counting)
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                covlib(M2, [(1, "m-1"), (3, "m-17")], [1, 3]),
                covlib(M2, [(1, "m-1"), (2, "m-17")], [3, 1, 3], declare=False),
            ],
        )
        assert attempt(store, "members", ["m-17"]).startswith("ERASURE_BLOCKED B1 +cover")
        assert len(calls) == len(set(calls)) == 1
        calls.clear()
        fx.run(store, [R1, enrol(["m-1"], [(1, "m-1")], M2, declare=False)])
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [4] [(1, (2,))]"
    assert calls
    assert len(calls) == len(set(calls))


def test_erasure_rebuilds_the_links_and_decides_validity_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publish between the unlocked reading of the links and the lock adds a release that
    holds the person and declares the coverage another release's orphan breaks: the key,
    unknown before it, is decided again under the lock, with the links it declares."""
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                enrol(["m-1"], [(1, "m-1")], ["m-1"]),
                enrol(["m-1"], [(1, "m-1")], M2, declare=False),
            ],
        )
        registry = store.db.cover_registry
        read: list[str] = []

        def reading(dataset: str) -> Any:
            found = registry(dataset)
            if not read:  # after the unlocked read: two releases are published meanwhile
                fx.publish(store, enrol(M2, [(1, "m-1"), (2, "m-17")], M2))
                fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
            read.append(dataset)
            return found

        monkeypatch.setattr(store.db, "cover_registry", reading)
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2] [(1, (2,))]"
        assert len(read) == 2  # read again under the lock


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        pytest.param(
            [R1, enrol(["m-1"], [(1, "m-1")], M2, declare=False)],
            "ERASURE_BLOCKED E [2] [(1, (2,))]",
            id="a release that does not hold the person",
        ),
        pytest.param(
            [
                covlib(M2, [(1, "m-1"), (3, "m-17")], [1, 3]),
                covlib(M2, [(1, "m-1"), (2, "m-17")], [3, 1, 3], declare=False),
            ],
            "ERASURE_BLOCKED B1 +cover [(1, (1, 3))]",
            id="a latest that holds it and names the key in a coverage table",
        ),
    ],
)
def test_the_coverage_scan_runs_before_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: list[Step], expected: str
) -> None:
    """PR #88's review round 2, M2: the hits are worked out before the store's lock, which a
    query takes to pin a release, and reused under it while nothing changed; a latest that holds
    the person (text B1) has its hits worked out before it too (round 3, m2)."""
    held: list[bool] = []
    found = erasure._cover_rows  # pyright: ignore[reportPrivateUsage]
    with fx.opened(tmp_path / "data") as store:
        fx.run(store, steps)

        def recording(*args: Any, **given: Any) -> Any:
            held.append(store.lock._is_owned())  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            return found(*args, **given)

        monkeypatch.setattr(erasure, "_cover_rows", recording)
        assert attempt(store, "members", ["m-17"]) == expected
    assert held
    assert not any(held)


def test_hits_worked_out_ahead_are_not_reused_once_the_registry_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release published and withdrawn between the scan ahead and the lock leaves the
    published releases as they were but records its coverage: under the lock the hits are
    worked out again, with it (fail-closed)."""
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                enrol(M2, [(1, "m-1"), (2, "m-17")], M2, declare=False),
                enrol(["m-1"], [(1, "m-1")], M2, declare=False),
            ],
        )
        ahead = erasure._scan_ahead  # pyright: ignore[reportPrivateUsage]

        def meanwhile(*args: Any) -> None:
            ahead(*args)
            manifest = fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
            with store.db.transaction() as db:
                store.record_withdrawal(db, "lib", manifest, ADA)
            store.withdrawn([manifest])

        monkeypatch.setattr(erasure, "_scan_ahead", meanwhile)
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2] [(1, (2,))]"


# --- Leaks --------------------------------------------------------------------------------------

SPELLINGS = ["Ab-17", "ab_17", "AB 17", "ab-17-export", "ab.17"]


@pytest.mark.parametrize("key", SPELLINGS)
def test_no_refusal_log_audit_entry_or_repr_shows_the_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, key: str
) -> None:
    caplog.set_level(logging.DEBUG)
    keys = ["m-1", key]
    texts: list[str] = []
    chains: list[str] = []

    def refused(store: Store) -> str:
        """The refusal's text, and (in ``chains``) what its exception and its chain show."""
        with pytest.raises(StoreRefused) as raised:
            erase(store, "lib", "members", [key], ADA)
        chains.extend(fx.chained(raised.value))
        return fx.text_of(raised.value)

    with fx.opened(tmp_path / "data") as store:
        fx.run(store, [enrol(keys, [(1, "m-1"), (2, key)], keys), bare(keys)])
        texts.append(refused(store))  # E with the note
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], keys, declare=False))
        fx.publish(store, enrol(keys, [(1, "m-1")], keys))
        texts.append(refused(store))  # B1 with cover
        fx.publish(store, enrol(["m-1"], [(1, "m-1")], ["m-1"]))
        store.withdraw("lib", 2, ADA)
        texts.append(refused(store))  # E without the note
        audit = [row[0] for row in store.db.connection.execute("SELECT detail FROM audit")]
        live = erasure._live(store, "lib")  # pyright: ignore[reportPrivateUsage]
        person = erasure._person(store, "lib", "members", live, [key])  # pyright: ignore[reportPrivateUsage]
        shown = [repr(person), *(repr(hit) for m in live for hit in person.cover_hits(m))]
        shown += [repr(found) for found in person.covers]
    assert any("@3 (table 1" in text for text in texts)
    assert sum("The hit may be a collision" in text for text in texts) == 1
    assert any(text.startswith(fx.B1) for text in texts)
    assert any("StoreRefused" in text for text in chains)  # the refusals' own exceptions
    spelled = {key, key.lower(), key.upper(), key.replace("-", "_"), key.replace(" ", "_")}
    # The log records are scanned as formatted, with their tracebacks (``exc_text``, ``exc_info``
    # and the exception chain, ``__cause__`` and ``__context__``), not their messages alone.
    for text in [*texts, *chains, *audit, *shown, *fx.logged(caplog.records)]:
        for form in spelled:
            assert form not in text
    assert json.dumps(audit)


def test_text_f_shows_no_manifest_or_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _version_7(tmp_path, monkeypatch, [R1])
    raw = sqlite3.connect(root / "app.db")
    try:
        manifest = raw.execute("SELECT manifest FROM labels").fetchone()[0]
    finally:
        raw.close()
    _damage(root, manifest, "corrupt")
    with fx.opened(root) as store, pytest.raises(StoreRefused) as raised:
        erase(store, "lib", "members", ["m-17"], ADA)
    text = fx.text_of(raised.value)
    for shown in (manifest, hex_of(manifest), "m-17"):
        assert shown not in text
        assert not any(shown in written for written in fx.chained(raised.value))


def test_links_never_reach_the_rows_the_terms_or_the_names(tmp_path: Path) -> None:
    """P6b: with a scoped coverage of loans' members, the rows, terms and ``Naming`` are the
    base's (the vendored module before D408)."""
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                scoped(
                    M2,
                    [(1, "m-1", "poetry"), (2, "m-17", "fiction")],
                    [("m-1", "poetry"), ("m-17", "fiction")],
                ),
                scoped(["m-1"], [(1, "m-1", "poetry")], [("m-1", "poetry")]),
            ],
        )
        live = erasure._live(store, "lib")  # pyright: ignore[reportPrivateUsage]
        person = erasure._person(store, "lib", "members", live, ["m-17"])  # pyright: ignore[reportPrivateUsage]
        before = base_erasure._Person("members", live, ["m-17"])  # pyright: ignore[reportPrivateUsage]
        assert person.covers
        assert person.rows == before.rows
        assert person.terms().dumps() == before.terms().dumps()
        terms = person.terms()
        assert sorted(terms.person) == ["m-17"]
        assert sorted(terms.below) == ["2"]
        assert terms.naming is not None
        assert terms.naming.names.get("loans.member_id") == {"members"}


# --- Where a key may be, and what erasure does there -------------------------------------------

BLOB_ROLES = {
    "tables": "rows withdraw a release (D223); coverage and scope tables refuse (D408)",
    "sources": "withdrawn with the release's rows; text before the header and names: M4.0e-1b",
    "descriptors": "withdrawn with the release; names and ids: M4.0e-2",
    "statistics": "withdrawn with the release: M4.0e-2",
    "tombstones": "withdrawn with the release: M4.0e-2",
    "report": "withdrawn with the release; notes, never cell values (D231)",
}
APP_DB = {
    **dict.fromkeys(
        ["audit", "catalog", "derivations", "issuances", "proposals", "result_cache"], "redacted"
    ),
    **dict.fromkeys(
        ["derivation_releases", "log_permits", "log_texts", "log_usage"],
        "redacted with the derivation log",
    ),
    **dict.fromkeys(["result_cache_contents", "result_cache_usage"], "emptied with the cache"),
    **dict.fromkeys(
        ["labels", "manifests", "sessions", "drafts", "session_decisions"],
        "the store's records: labels, hashes and operators",
    ),
    **dict.fromkeys(["pending_redactions", "pending_uploads", "vacuum_due"], "erasure's own work"),
    **dict.fromkeys(
        ["cover_links", "cover_scopes", "cover_graph", "cover_links_pending"], "ids, kept"
    ),
    "sqlite_sequence": "SQLite's",
}


def test_every_blob_role_and_app_db_table_is_classified(store: Store) -> None:
    """A new place a key may be is classified here before it lands: the registry's tables and
    its work list hold table and column names, kept until M4.0e-3 (R12)."""
    roles = set(Manifest.model_fields) - {"format", "dataset"}
    assert roles == set(BLOB_ROLES)
    tables = {
        row[0]
        for row in store.db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert tables == set(APP_DB)
    assert {t for t, c in APP_DB.items() if c == "redacted"} == set(redaction.REDACTORS)
    assert {t for t, c in APP_DB.items() if c == "ids, kept"} == {
        "cover_links",
        "cover_scopes",
        "cover_graph",
        "cover_links_pending",
    }


def test_redact_only_keeps_its_own_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RO-plain: ``redact_only`` redacts the key alone, with no ``Naming`` (never the person's
    terms, which a scan of coverage tables must not change)."""
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                enrol(["m-1", "z"], [(1, "m-1")], ["m-1", "z"]),
                enrol(["m-1"], [(1, "m-1")], ["m-1"]),
                erased("members", ["z"]),
            ],
        )
        requested: list[Any] = []
        request = store.request_redaction

        def spy(db: Any, dataset: str, terms: Any, manifests: Any) -> int:
            requested.append(terms)
            return request(db, dataset, terms, manifests)

        monkeypatch.setattr(store, "request_redaction", spy)
        assert attempt(store, "members", ["z"], redact_only=True) == "done () terms=1"
        [terms] = requested
        assert sorted(terms.person) == ["z"]
        assert terms.naming is None


# --- The cost of composition (PR #88's review, M2) ----------------------------------------------


def _wide_rows(keys: int, person: tuple[int, ...], rows: int, shift: int) -> list[tuple[int, ...]]:
    """``enrolled`` rows whose three scope columns for key column i hold the person's visit key
    at positions i + shift, i + shift + 1 and i + shift + 2: with ``shift`` 0 the person's key
    itself is among each position's values (K1); otherwise only a rotation of it can be formed
    (K2, a multiset)."""
    found = []
    for _ in range(rows):
        found.append(tuple(person[(i + shift + j) % keys] for j in range(3) for i in range(keys)))
    return found


@pytest.mark.parametrize(
    ("keys", "shift", "rotations", "expected"),
    [
        pytest.param(8, 0, 0, "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]", id="8x3 K1"),
        pytest.param(8, 1, 0, "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]", id="8x3 K2"),
        pytest.param(16, 1, 0, "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]", id="16x3 K2"),
        pytest.param(16, 1, 3, "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]", id="16x3 K2, taken"),
        pytest.param(16, 0, 0, "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]", id="16x3 K1"),
    ],
)
def test_composed_scope_columns_are_matched_without_their_product(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: int,
    shift: int,
    rotations: int,
    expected: str,
) -> None:
    """Three scope columns stand for each of the visits' key columns (compose_one): 3**16
    links if enumerated. With 1,000 rows listing the person's visit key, or only orders of it
    (some of them other visits' keys, ``rotations``), the erasure refuses within its budget."""
    person = tuple(range(1, keys + 1))
    others = [
        (tuple(person[(i + r) % keys] for i in range(keys)), "m-1") for r in range(1, rotations + 1)
    ]
    with fx.opened(tmp_path / "data") as store:
        fx.publish(store, fx.scoped_wide(keys, M2, [(person, "m-17"), *others], []))
        fx.publish(
            store,
            fx.scoped_wide(
                keys, ["m-1"], others, _wide_rows(keys, person, 1000, shift), declare=False
            ),
        )
        with _work(monkeypatch) as rows:
            assert attempt(store, "members", ["m-17"]) == expected
        # The rows are alike: one is matched (its reading kept for the rest), within a small
        # multiple of its values, the search included.
        assert [row.values for row in rows] == [3 * keys]
        assert rows[0].spent <= WORK * rows[0].values


@pytest.mark.parametrize("keys", [8, 16])
def test_scope_columns_unioned_across_releases_cost_rows_not_their_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: int
) -> None:
    """compose_store: the registry keeps three releases' scope maps of one coverage, each with
    its own scope columns; the erasure reads 1,000 rows in time linear in them."""
    with fx.opened(tmp_path / "data") as store:
        fx.publish(
            store, fx.scoped_wide(keys, M2, [((0,) * keys, "m-1")], [(0,) * (3 * keys)] * 1000)
        )
        fx.publish(
            store, fx.scoped_wide(keys, ["m-1"], [((0,) * keys, "m-1")], [(0,) * (3 * keys)] * 1000)
        )
        with _work(monkeypatch) as rows:
            assert attempt(store, "members", ["m-17"]) == "done (1,) terms=1"
        assert len(rows) <= 2  # one per release: their rows are alike
        assert all(row.spent <= WORK * row.values for row in rows)


WORK = 2
"""The work of a row of a cover of several columns, per value it holds, in these tests: the
keys and multisets its values anchor, each tested, and the matchings of ``_formable``, which
searches past orders of the person's key that are parent keys (16 x 3 values, three taken)."""
KEYS = [10, 2000]
"""The person's visits, so their keys (K1 and K2): the work does not grow with them."""
OTHER = [(10**6, 1, "m-1")]


def _person_visits(visits: int) -> list[tuple[int, int, str]]:
    return [(i, i + 1, "m-17") for i in range(visits)]


@pytest.mark.parametrize("visits", KEYS)
def test_a_listed_row_s_work_is_bounded_by_its_values_whatever_the_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visits: int
) -> None:
    """PR #88's review round 2, M2 (``listed_cost``): scope columns s0 and s1 stand for the
    visits' key (k0, k1); the latest types them as lists, 300 rows of two items each. A row
    tests only the keys and multisets its own values anchor."""
    rows = [(f"{10**7 + r};{10**7 + r + 1}", f"{10**7 + r};{r}") for r in range(300)]
    scope = {"s0": "k0", "s1": "k1"}
    with fx.opened(tmp_path / "data") as store:
        fx.publish(
            store,
            paired(M2, [*_person_visits(visits), *OTHER], [(10**6, 1)], ["s0", "s1"], scope=scope),
        )
        fx.publish(store, paired(["m-1"], OTHER, rows, ["s0", "s1"], sdt=LIST))
        with _work(monkeypatch) as work:
            assert attempt(store, "members", ["m-17"]).startswith("done (1,)")
    assert [row.values for row in work] == [4] * len(rows)
    assert all(row.spent <= WORK * row.values for row in work)


@pytest.mark.parametrize("visits", KEYS)
def test_alternative_scope_columns_cost_their_values_whatever_the_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visits: int
) -> None:
    """``alt_cost``: R1 maps s0 and s1 to the visits' key, R2 t0 and t1; the latest keeps all
    four with values that differ, so every row has two values at each position, no list."""
    columns = ["s0", "s1", "t0", "t1"]
    rows = [(10**7 + r, r, 10**7 + r + 1, r + 5) for r in range(300)]
    one = [(10**6, 1, 10**6, 1)]
    with fx.opened(tmp_path / "data") as store:
        fx.run(
            store,
            [
                paired(
                    M2,
                    [*_person_visits(visits), *OTHER],
                    one,
                    columns,
                    scope={"s0": "k0", "s1": "k1"},
                ),
                paired(["m-1"], OTHER, one, columns, scope={"t0": "k0", "t1": "k1"}),
                paired(["m-1"], OTHER, rows, columns),
            ],
        )
        with _work(monkeypatch) as work:
            assert attempt(store, "members", ["m-17"]).startswith("done (1,)")
    assert [row.values for row in work] == [4] * len(rows)
    assert all(row.spent <= WORK * row.values for row in work)


def test_a_cover_s_scan_stops_at_the_rows_text_e_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every row of the latest's coverage lists the person's visit (0, 1): three rows are
    matched, the three text E shows, not 300."""
    rows = [(f"0;{10**7 + r}", f"1;{r}") for r in range(300)]
    scope = {"s0": "k0", "s1": "k1"}
    with fx.opened(tmp_path / "data") as store:
        fx.publish(
            store, paired(M2, [*_person_visits(5), *OTHER], [(10**6, 1)], ["s0", "s1"], scope=scope)
        )
        fx.publish(store, paired(["m-1"], OTHER, rows, ["s0", "s1"], sdt=LIST))
        with _work(monkeypatch) as work:
            assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED E [2] [(1, (1, 2, 3))]"
    assert len(work) == 3


def _options(*positions: Sequence[str]) -> tuple[dict[str, frozenset[str]], ...]:
    """A row's options, each value read as itself."""
    return tuple({value: frozenset({value}) for value in values} for values in positions)


def test_a_row_tests_the_keys_its_values_anchor_or_the_product_of_its_values_where_smaller() -> (
    None
):
    """PR #88's review round 3, m2: each K1 key is indexed by its rarest (position, value) and
    each K2 multiset by its rarest value, and a row tests those its values anchor, or the
    product of its values where that is smaller, each examined once paid for. Over keys that
    share a first part (a column) the anchors are at the second position; over a grid every
    value is as common as any other, so a row of 2 x 2 values costs its product, 4."""
    keys, work = erasure._Keys, erasure._Work  # pyright: ignore[reportPrivateUsage]
    ordered, unordered = erasure._ordered, erasure._unordered  # pyright: ignore[reportPrivateUsage]

    def cost(
        shared: set[tuple[str, str]], options: tuple[dict[str, frozenset[str]], ...]
    ) -> tuple[list[tuple[str, ...]], int, list[tuple[str, ...]], int]:
        found = keys.of(shared, shared, None)
        first, second = work(20), work(20)
        return (
            ordered(options, found, first),
            first.spent,
            unordered(options, found, second),
            second.spent,
        )

    column = {("A", f"b{j}") for j in range(20)}
    row = _options(["A", *(f"z{i}" for i in range(9))], ["b0", "b1", *(f"q{i}" for i in range(8))])
    both = [("A", "b0"), ("A", "b1")]
    assert cost(column, row) == (both, 2, both, 2)  # not the 20 keys of 'A', nor the 100 orders
    grid = {(f"a{i}", f"b{j}") for i in range(8) for j in range(8)}
    square = {(a, b) for a in ("a0", "a1") for b in ("b0", "b1")}
    found = sorted(square)
    assert cost(grid, _options(["a0", "a1"], ["b0", "b1"])) == (found, 4, found, 4)  # not 16


def test_a_spent_search_hits_rather_than_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ROW_BUDGET`` is fail-closed: a row whose orders the search cannot finish within it
    refuses (a hit), never passes; and the search reads each order in every raw spelling of
    its values, a parent key taking only its own."""
    formable = erasure._formable  # pyright: ignore[reportPrivateUsage]
    work = erasure._Work  # pyright: ignore[reportPrivateUsage]
    spent = erasure._SpentError  # pyright: ignore[reportPrivateUsage]
    key = ("1", "2", "3", "4")
    four = _options(*[key] * 4)
    every: set[tuple[str, ...]] = set(itertools.permutations(key))
    assert formable(key, four, {key}, work(16))
    assert not formable(key, _options(*[("1",)] * 4), set(), work(16))
    assert not formable(key, four, every, work(16))
    spelled = ({"a": frozenset({"a", " a"})}, {"b": frozenset({"b"})})
    assert formable(("a", "b"), spelled, {("a", "b")}, work(3))
    assert not formable(("a", "b"), spelled, {("a", "b"), (" a", "b")}, work(3))
    with pytest.raises(spent):
        formable(key, four, every, work(0))
    keys = erasure._Keys.of(set(), {key}, None)  # pyright: ignore[reportPrivateUsage]
    parents = erasure._Parents(frozenset(every), {}, {key: frozenset(every)})  # pyright: ignore[reportPrivateUsage]
    row = (frozenset(key),) * 4
    names = erasure._names  # pyright: ignore[reportPrivateUsage]
    assert not names(row, keys, parents, own=False, rekeyed=False)
    assert names(row, keys, parents, own=False, rekeyed=True)
    monkeypatch.setattr(erasure, "ROW_BUDGET", 1)
    assert names(row, keys, parents, own=False, rekeyed=False)


def test_another_dataset_s_registry_is_not_this_one_s(tmp_path: Path) -> None:
    """The registry is read by dataset: a coverage another dataset declared does not make this
    dataset's undeclared table a cover."""
    with fx.opened(tmp_path / "data") as store:
        descriptors, sources, layouts = enrol(M2, [(1, "m-1")], M2)
        with store.pin() as pin:
            built = store.import_release(pin, "other", descriptors, sources, layouts)
            store.publish("other", built.manifest.hash, ADA)
        fx.run(
            store,
            [
                enrol(M2, [(1, "m-1"), (2, "m-17")], M2, declare=False),
                enrol(["m-1"], [(1, "m-1")], M2, declare=False),
            ],
        )
        assert store.db.cover_registry("lib")[0] == frozenset()
        assert attempt(store, "members", ["m-17"]) == "done (1,) terms=2"


def test_a_backfill_that_fails_leaves_its_manifest_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording a manifest's links and taking it off the work list are one transaction: a
    failure between them loses no link."""
    root = _version_7(tmp_path, monkeypatch, [R1])

    def failing(self: Store, db: Any, dataset: str, descriptors: Any) -> None:
        db.execute("SELECT 1")
        raise RuntimeError("the disk is full")

    with monkeypatch.context() as patched:
        patched.setattr(Store, "_record_links", failing)
        with pytest.raises(RuntimeError, match="disk is full"):
            Store(root, clock=fx.clock())
    raw = sqlite3.connect(root / "app.db")
    try:
        assert raw.execute("SELECT count(*) FROM cover_links_pending").fetchone()[0] == 1
        assert raw.execute("SELECT count(*) FROM cover_links").fetchone()[0] == 0
    finally:
        raw.close()
    with fx.opened(root) as store:
        assert store.db.links_pending() == []
        assert store.db.cover_registry("lib")[0]


def test_a_pending_release_that_is_not_the_latest_refuses_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _version_7(tmp_path, monkeypatch, [covlib(M2, [(1, "m-1"), (2, "m-17")], [1, 2]), R1])
    raw = sqlite3.connect(root / "app.db")
    try:
        first = raw.execute("SELECT manifest FROM labels WHERE label = 1").fetchone()[0]
    finally:
        raw.close()
    _damage(root, first, "corrupt")
    with fx.opened(root) as store:
        assert attempt(store, "members", ["m-17"]) == "ERASURE_BLOCKED F [1]"
