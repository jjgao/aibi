"""The dataset answer names its tables (D440, refining D275): each `TableOut`'s `id` and `label`
are the served descriptor's `/id` and `/label` for the release the call pins, under any *k*.

The property is held to the labels this test wrote, not only to the answer's own `descriptor`
(which shares the builder's code), with the fixture conditions under which a wrong source shows:
labels that differ from the ids, labels that differ between the pinned, latest and draft releases,
a second dataset whose tables have the same ids under the same release labels, and one label long
enough (4,096 code points, a bidi control last) that any cut shows. A label is also served byte for
byte over the whole domain of `Label`: every code point `is_text` accepts (1,111,998), written as
labels of 4,096 code points, with the sampled shapes (`WILD`: white space at either end and alone,
a control, a sequence NFC and a character NFKC changes, an astral character, the ends of each
code-point class D312 names, derived from `aibi.core.schema.visible`, and the long label), at the
latest release, a label, a manifest hash and the draft, under no k and a floor. The samples are
checked against the classes; only the brute force holds the domain.
"""

import unicodedata
from collections.abc import Callable
from typing import Any

from tests.core.catalog.world import ADA

from aibi.core.schema import visible
from aibi.core.schema.catalog import DatasetDescription, DescribeDataset
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_CHANGE_EDITS, MAX_STRING
from aibi.core.store import sessions

World = Any
Orchard = Callable[..., dict[str, bytes]]

LONG = "L" * (MAX_STRING - 1) + "‮"

#: The labels written, by dataset and release; release 1 is the importer's (each file's name).
WRITTEN: dict[str, dict[int | str, dict[str, str]]] = {
    "orchard": {
        1: {"harvests": "harvests", "trees": "trees"},
        2: {"harvests": "Harvests, release 2", "trees": "Trees, release 2"},
        3: {"harvests": LONG, "trees": "Trees, release 3"},
        "draft": {"harvests": LONG, "trees": "Trees, draft"},
    },
    "grove": {
        1: {"harvests": "harvests", "trees": "trees"},
        2: {"harvests": "Grove's harvests, release 2", "trees": "Grove's trees, release 2"},
        3: {"harvests": "Grove's harvests, release 3", "trees": "Grove's trees, release 3"},
    },
}


def _labels(written: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"op": "set", "descriptor": table, "pointer": "/label", "value": label}
        for table, label in written.items()
    ]


def _world(world: World, orchard: Orchard) -> None:
    for dataset in ("orchard", "grove"):
        world.publish(dataset, orchard())
        for release in (2, 3):
            edits = _labels(WRITTEN[dataset][release])
            if dataset == "orchard" and release == 2:
                # the dataset's own k from release 2 on
                edits.append(
                    {
                        "op": "set",
                        "descriptor": "dataset",
                        "pointer": "/fields/disclosure",
                        "value": {"min_cell_count": 9, "allow_row_ids": True},
                    }
                )
            assert world.curate(dataset, *edits) == release
    opened = world.open("orchard")
    world.change("orchard", opened, opened.draft, *_labels({"trees": "Trees, draft"}))


def _describe(catalog: Any, **given: Any) -> DatasetDescription:
    return catalog.describe_dataset(DescribeDataset.model_validate(given))


def _named(found: DatasetDescription) -> dict[str, str]:
    return {table.id: table.label.data for table in found.tables}


def test_each_table_is_named_by_the_id_and_label_written_for_the_release_under_any_k(
    world: World, orchard: Orchard
) -> None:
    _world(world, orchard)
    for floor in (None, 50):
        catalog = world.catalog(floor=floor)
        # the other dataset first, under every release label the orchard has too
        for release in (1, 2, 3):
            found = _describe(catalog, dataset="grove", release=release)
            assert _named(found) == WRITTEN["grove"][release], (floor, release)
        # a manifest hash pins release 2, not the latest (the labels differ)
        manifests = {
            label: _describe(catalog, dataset="orchard", release=label).release.manifest
            for label in (2, 3)
        }
        assert manifests[2] != manifests[3]
        pins: list[tuple[int | str | None, int | str]] = [
            (1, 1),
            (2, 2),
            (3, 3),
            ("draft", "draft"),
            (None, 3),
            (manifests[2], 2),
        ]
        for release, pinned in pins:
            given: dict[str, Any] = {"dataset": "orchard"}
            if release is not None:
                given["release"] = release
            found = _describe(catalog, **given)
            wanted = WRITTEN["orchard"][pinned]
            assert [table.id for table in found.tables] == ["harvests", "trees"]
            assert _named(found) == wanted, (floor, release)
            for table in found.tables:
                # the typed members are the served descriptor's own
                assert table.id == table.descriptor["id"]
                assert table.label.data == table.descriptor["label"]
                assert table.rows.reference.split("/")[-2] == table.id
            # no k, the floor, or the dataset's own k (from release 2 on, and the draft's)
            own = None if pinned == 1 else 9
            assert found.disclosure.min_cell_count == (floor or own), (floor, release)


def test_a_label_is_served_whole_and_as_written(world: World, orchard: Orchard) -> None:
    _world(world, orchard)
    for floor in (None, 50):
        found = _describe(world.catalog(floor=floor), dataset="orchard")
        harvests = next(table for table in found.tables if table.id == "harvests")
        assert harvests.label.data == LONG
        assert len(harvests.label.data) == MAX_STRING
        if floor:
            # the count is suppressed under the floor; the name is declared text, served whole
            assert harvests.rows.count is None


# --- The domain of a label: the classes of code points and shapes, each served as written -------


def _class(point: int) -> str:
    """The D312 class of an unsafe code point, by the names of `visible`'s own rules."""
    if point < 0x20:
        return "c0"
    if 0x7F <= point <= 0x9F:
        return "c1_and_del"
    if point in visible.SEPARATORS:
        return "line_and_paragraph_separators"
    return unicodedata.category(chr(point)).lower()  # "cf" or "cs"


def _writable(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The ranges' code points that a descriptor's text may hold (no lone surrogate or
    noncharacter: `is_text`), as the maximal runs among them."""
    found: list[tuple[int, int]] = []
    for first, last in ranges:
        for point in range(first, last + 1):
            if not is_text(chr(point)):
                continue
            if found and found[-1][1] == point - 1:
                found[-1] = (found[-1][0], point)
            else:
                found.append((point, point))
    return found


def _ends(ranges: list[tuple[int, int]]) -> str:
    """The first and last writable code point of every range, apart by an ASCII hyphen."""
    return "-".join(
        chr(point) for first, last in _writable(ranges) for point in sorted({first, last})
    )


def _unsafe_by_class(*, writable: bool = True) -> dict[str, list[tuple[int, int]]]:
    """Each unsafe class's maximal runs of code points, as inclusive ranges; by default those a
    descriptor's text may hold (D312's lone surrogates, `Cs`, are refused at write)."""
    found: dict[str, list[tuple[int, int]]] = {}
    for first, last in visible.unsafe_ranges():
        for point in range(first, last + 1):
            if writable and not is_text(chr(point)):
                continue
            runs = found.setdefault(_class(point), [])
            if runs and runs[-1][1] == point - 1:
                runs[-1] = (runs[-1][0], point)
            else:
                runs.append((point, point))
    return found


def _wild() -> dict[str, str]:
    """The labels, by table id: the shapes by hand (each a mutant's target: strip, collapse,
    normalise, drop), then every D312 class's range ends (the unsafe classes, the ones D312 keeps
    raw, the ones it leaves raw though a renderer draws them as nothing), then the long label."""
    written = {
        "lead_space": "  a",
        "lead_tab": "\ta",
        "lead_cr": "\ra",
        "lead_lf": "\na",
        "trail_space": "a  ",
        "trail_tab": "a\t",
        "trail_cr": "a\r",
        "trail_lf": "a\n",
        "blank_space": "   ",
        "blank_tab": "\t",
        "blank_cr": "\r",
        "blank_lf": "\n",
        "blank_mixed": " \t\r\n ",
        "inner_runs": "a \t\r\n  b",
        "crlf": "a\r\nb",
        "control_inline": "a\x01b\x1fc",
        "nfd_pair": "e\u0301 \u1100\u1161",
        "nfc_changes_sign": "\u212b \u2126",
        "nfkc_changes": "\uff21 \ufb01 \u2460 x\u00b2",
        "astral": "\U0001f600 \U00010000a\U0010fffd",
        "mixed_case": "MiXed CaSe",
    }
    for name, runs in _unsafe_by_class().items():
        written[f"unsafe_{name}"] = _ends(runs)
    written["kept_by_d312"] = "-".join(chr(point) for point in sorted(visible.KEPT))
    written["raw_ignorable_assigned"] = _ends(visible.raw_ignorable(assigned=True))
    written["raw_ignorable_unassigned"] = _ends(visible.raw_ignorable(assigned=False))
    written["raw_spaces"] = _ends(visible.raw_spaces())
    written["braille_blank"] = chr(visible.BRAILLE_BLANK)
    written["long"] = LONG
    return written


WILD = _wild()


def _bytes(named: dict[str, str]) -> dict[str, bytes]:
    return {table: label.encode("utf-8", "surrogatepass") for table, label in named.items()}


def test_the_hand_and_class_labels_hold_the_ends_of_every_class_d312_names() -> None:
    """The sampled labels (`WILD`) follow `visible`'s classes: each class is one the module
    names, and every range's ends (and each kept code point) is in a label. This is a check of
    the samples, derived through the helpers that make them; the guard of the domain is the
    brute force below."""
    unsafe = _unsafe_by_class()
    # all D312's classes but the lone surrogates, which a descriptor's text cannot hold
    assert len(_unsafe_by_class(writable=False)) == len(visible.document()["classes"])  # type: ignore[arg-type]
    assert set(unsafe) == set(_unsafe_by_class(writable=False)) - {"cs"}
    written = "".join(WILD.values())
    ends = [point for runs in unsafe.values() for first, last in runs for point in (first, last)]
    ends += visible.KEPT
    ends.append(visible.BRAILLE_BLANK)
    for ranges in (
        visible.raw_ignorable(assigned=True),
        visible.raw_ignorable(assigned=False),
        visible.raw_spaces(),
    ):
        ends += [point for first, last in _writable(ranges) for point in (first, last)]
    assert ends
    assert [hex(point) for point in ends if chr(point) not in written] == []
    for label in (
        "  a",
        "a  ",
        "   ",
        "a\t",
        "\ta",
        "a\r",
        "\ra",
        "a\n",
        "\na",
        "\x01",
        "\U0001f600",
    ):
        assert any(label in kept for kept in WILD.values()), label
    assert any(unicodedata.normalize("NFC", kept) != kept for kept in WILD.values())
    assert any(unicodedata.normalize("NFKC", kept) != kept for kept in WILD.values())
    assert any(len(kept) == MAX_STRING for kept in WILD.values())


def _every_code_point() -> list[str]:
    """Every code point a `Label` may hold, in order, as labels of `MAX_STRING` code points (the
    last is shorter)."""
    points = [chr(point) for point in range(0x110000) if is_text(chr(point))]
    return ["".join(points[at : at + MAX_STRING]) for at in range(0, len(points), MAX_STRING)]


#: The code points `is_text` accepts, counted from `Label`'s rules, not from the generator: the
#: code points (U+0000 to U+10FFFF), but the 2,048 surrogates (U+D800 to U+DFFF: a lone one cannot
#: be UTF-8) and the 66 noncharacters (RFC 7493 §2.1: U+FDD0 to U+FDEF, 32, and U+xxFFFE and
#: U+xxFFFF of each of the 17 planes, 34).
DOMAIN = 0x110000 - (0xE000 - 0xD800) - (0xFDEF - 0xFDD0 + 1) - 17 * 2


def test_the_labels_made_hold_each_code_point_a_label_may_hold_exactly_once() -> None:
    assert DOMAIN == 0x110000 - 2048 - 66 == 1_111_998
    labels = _every_code_point()
    joined = "".join(labels)
    assert len(joined) == DOMAIN
    assert len(set(joined)) == DOMAIN
    assert [len(label) for label in labels] == [MAX_STRING] * (DOMAIN // MAX_STRING) + [
        DOMAIN % MAX_STRING
    ]
    assert len(labels) == 272
    # the generator's own boundaries, by hand: both ends of the domain, and what it leaves out
    assert joined[0] == "\x00"
    assert joined[-1] == "\U0010fffd"
    held = set(joined)
    for point in (0xD7FF, 0xE000, 0xFDCF, 0xFDF0, 0xFFFD, 0x10FFFD):
        assert chr(point) in held, hex(point)
    for point in (0xD800, 0xDFFF, 0xFDD0, 0xFDEF, 0xFFFE, 0xFFFF, 0x1FFFE, 0x10FFFF):
        assert chr(point) not in held, hex(point)


def test_every_code_point_a_label_may_hold_is_served_byte_for_byte_under_any_k_and_release_form(
    world: World, orchard: Orchard
) -> None:
    """Every code point `Label` accepts, written as labels of `MAX_STRING` code points on one-row
    tables (with the sampled labels, `WILD`), is asserted byte for byte, under no k and a floor,
    at the latest release, a label, a manifest hash and the draft. The domain is the test's
    input, not samples of it, so a server that alters any code point shows."""
    names = {f"t{index:03d}": label for index, label in enumerate(_every_code_point())}
    assert not names.keys() & WILD.keys()
    files = orchard()
    files.update({f"{table}.csv": b"id\n1\n" for table in WILD | names})
    world.publish("wild", files)
    edits = _labels(WILD | names)
    # a change takes at most MAX_CHANGE_EDITS edits; release 2 has no k of the dataset's own
    opened = world.open("wild")
    draft = opened.draft
    for at in range(0, len(edits), MAX_CHANGE_EDITS):
        draft = world.change("wild", opened, draft, *edits[at : at + MAX_CHANGE_EDITS])
    assert len(edits) > MAX_CHANGE_EDITS
    assert sessions.publish(world.store, "wild", opened.handle, draft, ADA).label == 2
    # release 3 has its own k; the draft relabels one table and keeps the rest
    own_k = {"min_cell_count": 9, "allow_row_ids": True}
    k = {"op": "set", "descriptor": "dataset", "pointer": "/fields/disclosure", "value": own_k}
    assert world.curate("wild", k) == 3
    opened = world.open("wild")
    world.change("wild", opened, opened.draft, *_labels({"trees": "Trees, draft"}))
    # the orchard's two tables keep the importer's labels (their file names)
    base = {"harvests": "harvests", "trees": "trees"} | WILD | names
    assert len(base) == len(names) + len(WILD) + 2
    manifest = _describe(world.catalog(), dataset="wild", release=2).release.manifest
    forms: list[tuple[int | str | None, int | None, dict[str, str]]] = [
        (None, 9, base),
        (2, None, base),
        (manifest, None, base),
        ("draft", 9, base | {"trees": "Trees, draft"}),
    ]
    for release, own, written in forms:
        wanted = _bytes(written)
        for floor in (None, 50):
            given: dict[str, Any] = {"dataset": "wild"}
            if release is not None:
                given["release"] = release
            found = _describe(world.catalog(floor=floor), **given)
            assert found.disclosure.min_cell_count == (floor or own), (release, floor)
            served = _bytes(_named(found))
            assert served.keys() == wanted.keys(), (release, floor)
            assert [table for table in wanted if served[table] != wanted[table]] == [], (
                release,
                floor,
            )
            for table in found.tables:
                assert table.label.data == table.descriptor["label"], (release, floor)
