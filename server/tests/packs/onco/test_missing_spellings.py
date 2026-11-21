"""The MAF's spellings of no value and their look-alikes (M4.2b-2b-ii, slice A; D444).

What this module holds, each part by class, never from the pack's code read back:

- **the family**: every ASCII case of ``unknown``, ``na``, ``n/a`` and ``null`` (152, by
  ``itertools.product`` here) is a missing code of ``Variant_Classification`` and
  ``Mutation_Status`` in every MAF study, ``imported``; a cell of it is UNKNOWN by reason, never a
  value, and D228's count leaves it out;
- **the normal form N** and its streaming bound: the pack's ``normal_form`` against the test's
  model of N, unbounded (``symbol_oracle.normal``), over every code point and runs of every
  removable and edge code point, seeded random and adversarial strings, and its bound white-box;
- **the look-alike's refusal**, ``MISSING_SPELLING``, through the reader's row check: every code
  point whose N is empty in each column on each kind of row, the substitutions that make a word,
  and the order the checks keep;
- **the engine**: the family answers UNKNOWN on the ``every``, ``some`` and ``negate`` shapes,
  the evaluator and the SQL compiler alike, and a look-alike cannot import;
- **the carry** (D241): the slice's own cases, through the real session and the real re-import of
  a study read as pack 0.8.0 read it, each outcome a literal.

The pack is imported inside the test bodies alone (``conftest.py``).
"""

import collections
import importlib
import importlib.util
import itertools
import json
import random
import re
import shutil
import time
import unicodedata
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import PackRegistry

HERE = Path(__file__).resolve().parent
UNICODE_15 = pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0",
    reason="the symbol model's literals are Unicode 15.0's, the pinned Python's",
)

WORDS = ("unknown", "na", "n/a", "null")
SYMBOL_WORDS = ("unknown", "na")
FAMILY = frozenset(
    "".join(spelled)
    for word in WORDS
    for spelled in itertools.product(*({c.lower(), c.upper()} for c in word))
)
"""The 152 spellings, the test's own: every ASCII case of each word, by ``itertools.product``."""
SYMBOL_EXACT = frozenset({"NA", "Unknown"})
REASON = (
    "the value looks blank, or reads as a spelling of no value once case, width, spaces and "
    "invisible code points are set aside, but is not one of the column's own spellings of no "
    "value, which the published column would keep as a value"
)
"""The look-alike's reason, written here: a fixed text, with no cell in it."""
EDGED = (
    "the gene symbol starts or ends with whitespace, a control or format character, or a code "
    "point drawn as nothing, which cBioPortal accepts and the published column would keep as "
    "written"
)
BLANK = "the required value is blank"


def mutation() -> Any:
    return importlib.import_module("aibi.packs.onco.mutation")


def corpus() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_corpus", HERE / "mutation_corpus.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def loaded() -> ModuleType:
    """The corpus, loaded once for the generators that build many MAFs from its rows."""
    return corpus()


def write(world: Any, name: str, files: Mapping[str, Any]) -> Path:
    directory = world.root / "imports" / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return directory


# --- The family -----------------------------------------------------------------------------------


def test_the_family_is_every_case_of_the_four_words() -> None:
    """The pack's 152 spellings are the test's (``itertools.product`` over case, against the pack's
    bit counter), 128 + 4 + 4 + 16, each ASCII; the two columns that declare them, and the
    symbol's words, are D444's."""
    found = mutation()
    assert found.MISSING_FAMILY == FAMILY
    assert len(FAMILY) == 152
    by_word = collections.Counter(spelled.lower() for spelled in FAMILY)
    assert by_word == {"unknown": 128, "na": 4, "n/a": 4, "null": 16}
    assert all(spelled.isascii() for spelled in FAMILY)
    assert found.MISSING_WORDS == WORDS
    assert found.SYMBOL_WORDS == SYMBOL_WORDS
    assert dict(found.MISSING_SPELLINGS) == {
        "Variant_Classification": FAMILY,
        "Mutation_Status": FAMILY,
    }


def test_the_family_is_declared_whatever_the_rows_hold(tmp_path: Path) -> None:
    """In a MAF whose rows hold no spelling of the family, the class and the status still declare
    all 152 as missing codes, ``imported`` (not ``imported_default``, which would raise
    ``UNCONFIRMED_SEMANTICS`` on every document reading the column), each ``UNKNOWN``, with the
    evidence that the pack reads them as no value; the symbol keeps ``NA`` and ``Unknown``."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.pack_api import ImportOptions

    onco = importlib.import_module("aibi.packs.onco")
    directory = tmp_path / "imports" / "study"
    for path, content in corpus().study().items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="study", reader=confinement, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    result = onco.PACK.importer.import_source(confinement.confine(directory), options)
    found = {d.id: d for d in result.descriptors}
    for column in ("variant_classification", "mutation_status"):
        descriptor = found[f"mutations.{column}"]
        assert descriptor.fields.missing_codes == dict.fromkeys(sorted(FAMILY), "UNKNOWN")
        entry = descriptor.curation["/fields/missing_codes"]
        assert entry.status == "imported"
        assert entry.evidence is not None
        assert "reads as no value" in entry.evidence
    assert found["mutations.hugo_symbol"].fields.missing_codes == dict.fromkeys(
        sorted(SYMBOL_EXACT), "UNKNOWN"
    )


def present_counts(cells: tuple[str, ...]) -> bool:
    """D228's rule as SPEC states it, written here: a category when it has at least one present
    value, at most 50 distinct ones, and at least twice as many present cells as distinct values,
    a present cell neither empty nor a missing code (the 152)."""
    present = [cell for cell in cells if cell != "" and cell not in FAMILY]
    distinct = len(set(present))
    return 0 < distinct <= 50 and 2 * distinct <= len(present)


TOKENS = ("Somatic", "Germline", "", "NA", "uNkNoWn", "n/A", "NULL", "None")
"""Status cells: values, the empty cell and spellings of the family (no look-alike, which the
reader refuses)."""


ROW_PATTERNS = ("validated", "skipped", "alternating")
"""How a column's rows are read: every row validated, every row one the reference skips (the
class ``Silent``, or, for the class column, whose own cell it is, ``Unknown`` with Entrez ``0``),
and every second row skipped."""
SKIPPED_ROW = {
    "Mutation_Status": {"Variant_Classification": "Silent"},
    "Variant_Type": {"Variant_Classification": "Silent"},
    "Variant_Classification": {"Hugo_Symbol": "Unknown", "Entrez_Gene_Id": "0"},
}


def d228_flag(column: str, cells: tuple[str, ...], skipped: tuple[bool, ...]) -> bool | None:
    """The reader's category flag for a column of ``cells``, row ``i`` skipped where
    ``skipped[i]``; ``None`` where a validated row's class is blank (refused)."""
    c = loaded()
    if column == "Variant_Classification" and any(
        cell == "" and not skip for cell, skip in zip(cells, skipped, strict=True)
    ):
        return None  # a blank class is refused on a validated row
    rows = [
        {
            **c.R1,
            **(SKIPPED_ROW[column] if skip else {}),
            "Start_Position": str(i),
            "End_Position": str(i),
            column: cell,
        }
        for i, (cell, skip) in enumerate(zip(cells, skipped, strict=True), start=1)
    ]
    read = mutation().read(
        c.maf(rows),
        "m.txt",
        frozenset({"S1", "S2", "S3"}),
        importlib.import_module("aibi.packs.onco.meta").MutationFields(None, None, (), "GRCh37"),
        ImportLimits(),
    )
    return {found.name: found.category for found in read.columns}[column]


def test_d228_leaves_the_family_out() -> None:
    """D228's count, by class: for every column of two to four cells drawn from ``TOKENS`` (two to
    three where some rows are skipped), in the class and in the status, on validated rows, on rows
    the reference skips and on both alternately (review r2 minor 1: the rule is every cell's, not
    the validated rows'), the reader's category flag is the test's rule, which counts a spelling of
    the family as neither present nor distinct; ``Variant_Type``, which declares no missing code,
    counts them as values."""
    flips = 0
    for pattern in ROW_PATTERNS:
        for size in range(2, 5 if pattern == "validated" else 4):
            skipped = tuple(
                pattern == "skipped" or (pattern == "alternating" and i % 2 == 1)
                for i in range(size)
            )
            for cells in itertools.product(TOKENS, repeat=size):
                for column in ("Mutation_Status", "Variant_Classification", "Variant_Type"):
                    flag = d228_flag(column, cells, skipped)
                    if flag is None:
                        continue
                    if column == "Variant_Type":
                        present = [cell for cell in cells if cell != ""]
                        distinct = len(set(present))
                        wanted = 0 < distinct <= 50 and 2 * distinct <= len(present)
                    else:
                        wanted = present_counts(cells)
                        plain = [cell for cell in cells if cell != ""]
                        counted = len(set(plain)) > 0 and 2 * len(set(plain)) <= len(plain)
                        flips += pattern == "validated" and wanted != counted
                    assert flag == wanted, (pattern, column, cells)
    assert flips == 988


def test_d228_leaves_the_family_out_on_skipped_rows() -> None:
    """Review r2 minor 1's case, a literal: a status of ``Somatic``, ``Somatic``, ``Germline``,
    ``Germline`` on validated rows and ``NA``, ``Unknown``, ``null``, ``N/A`` on rows the
    reference skips (``Silent``) is a category (two values of four present); counting the family
    on the skipped rows would make it six of eight, a string."""
    cells = ("Somatic", "Somatic", "Germline", "Germline", "NA", "Unknown", "null", "N/A")
    skipped = (False,) * 4 + (True,) * 4
    assert d228_flag("Mutation_Status", cells, skipped) is True
    assert d228_flag("Variant_Type", cells, skipped) is False


SHAPES: Mapping[str, tuple[str, ...]] = {
    "string-to-category": ("Somatic",) * 2 + ("Germline",) * 2 + ("NA", "Unknown", "null", "N/A"),
    "category-to-string": ("Somatic", "Germline", "LOH") + ("NA",) * 5,
    "category": ("Somatic",) * 3 + ("Germline",) * 3 + ("NA",) * 2,
}
"""Status columns whose D228 flag the family flips each way, or not: counted as values, six
distinct of eight (a string) and four of eight (a category); with the family left out, two of four
(a category) and three of three (a string)."""


def shaped(name: str, column: str = "Mutation_Status") -> bytes:
    c = corpus()
    values = SHAPES[name]
    if column == "Variant_Classification":
        mapped = {
            "Somatic": "Missense_Mutation",
            "Germline": "Nonsense_Mutation",
            "LOH": "Splice_Site",
        }
        values = tuple(mapped.get(value, value) for value in values)
    rows = [
        {
            **(c.R1 if i % 2 == 0 else c.R2),
            "Start_Position": str(1000 + i),
            "End_Position": str(1000 + i),
            column: value,
        }
        for i, value in enumerate(values)
    ]
    return c.maf(rows)


def test_the_shapes_flip_as_stated() -> None:
    """The three shapes' flags under the slice, literals: the first a category, the second a
    string, the third a category, in both columns."""
    meta = importlib.import_module("aibi.packs.onco.meta")
    fields = meta.MutationFields(None, None, (), "GRCh37")
    found = {}
    for name in SHAPES:
        for column in ("Mutation_Status", "Variant_Classification"):
            read = mutation().read(
                shaped(name, column), "m.txt", frozenset({"S1", "S2"}), fields, ImportLimits()
            )
            found[(name, column)] = {c.name: c.category for c in read.columns}[column]
    assert found == {
        ("string-to-category", "Mutation_Status"): True,
        ("string-to-category", "Variant_Classification"): True,
        ("category-to-string", "Mutation_Status"): False,
        ("category-to-string", "Variant_Classification"): False,
        ("category", "Mutation_Status"): True,
        ("category", "Variant_Classification"): True,
    }


def test_every_spelling_of_the_family_is_unknown_never_a_value(
    world: Any, registry: PackRegistry
) -> None:
    """Each of the 152 spellings, in the class and the status of a validated row and of a skipped
    one (``Unknown`` with Entrez ``0``), imports, and the published cell is UNKNOWN with no value;
    a value beside them is PRESENT as written."""
    from aibi.core.schema.semantics import ObservationState

    c = corpus()
    rows = []
    for index, spelled in enumerate(sorted(FAMILY)):
        skipped = {"Hugo_Symbol": "Unknown", "Entrez_Gene_Id": "0"} if index % 2 else {}
        rows.append(
            {
                **c.R1,
                **skipped,
                "Start_Position": str(100 + index),
                "End_Position": str(100 + index),
                "Variant_Classification": spelled,
                "Mutation_Status": spelled,
            }
        )
    rows.append({**c.R2, "Mutation_Status": "None", "Variant_Classification": "N A"})
    files = c.study(data_mutations_DOT_txt=c.maf(rows))
    published = world.publish("f", write(world, "f", files), registry, pack="onco")
    table = world.store.load(published.manifest).rows("mutations")
    states = collections.Counter()
    for row in range(len(table) - 1):
        for column in ("variant_classification", "mutation_status"):
            cell = table.cell(row, column)
            assert cell.value is None
            states[cell.state] += 1
    assert states == {ObservationState.UNKNOWN: 304}
    last = len(table) - 1
    assert table.cell(last, "mutation_status").value == "None"
    assert table.cell(last, "variant_classification").value == "N A"


# --- N, against the test's model ------------------------------------------------------------------

LAST = 0x10FFFF
RUNS = (1, 16, 17, 20)
SHAPE_WORDS = ("NA", "Unknown", "N/A", "null", "")


def kinds(oracle: ModuleType) -> tuple[frozenset[int], frozenset[int]]:
    """The model's removable and edge-space code points (an edge space that is removable is
    removable)."""
    removed = frozenset(p for p in range(LAST + 1) if oracle.removable(p))
    edges = frozenset(p for p in range(LAST + 1) if p not in removed and oracle.edge_space(p))
    return removed, edges


KINDS: dict[str, tuple[frozenset[int], frozenset[int]]] = {}


def model_kinds(oracle: ModuleType) -> tuple[frozenset[int], frozenset[int]]:
    if "kinds" not in KINDS:
        KINDS["kinds"] = kinds(oracle)
    return KINDS["kinds"]


def shapes(run: str) -> list[str]:
    """``run`` alone, at every place of each word of ``SHAPE_WORDS`` (before, inside, after), and
    at both ends of it."""
    found = [run]
    for word in SHAPE_WORDS:
        found += [word[:at] + run + word[at:] for at in range(len(word) + 1)]
        found.append(run + word + run)
    return found


def disagreements(oracle: ModuleType, cells: Any, removed: frozenset[int]) -> list[str]:
    lookalike = mutation().lookalike
    wrong = []
    for cell in cells:
        if lookalike(cell, frozenset(), WORDS) != oracle.lookalike(
            cell, frozenset(), WORDS, removed
        ):
            wrong.append(cell.encode("unicode_escape").decode()[:60])
    return wrong


@UNICODE_15
def test_n_agrees_with_the_model_over_every_code_point(symbol_model: ModuleType) -> None:
    """Every code point alone, before ``NA`` with U+2800 after it and after U+200B ``NA`` (so a
    printable ASCII code point at the edge of a cell that cannot take the ASCII shortcut, review
    r3 minor 4), inside ``NA``, and at both ends of ``Unknown``: the pack's verdict is the model's
    unbounded one (the scan over every code point at
    every run and place, 120,324,096 strings, found no disagreement when the slice was written;
    this keeps the cheap shapes for every code point and the long ones below)."""
    removed, _ = model_kinds(symbol_model)
    cells = (
        cell
        for point in range(LAST + 1)
        for character in (chr(point),)
        for cell in (
            character,
            character + "NA\u2800",
            "\u200bNA" + character,
            "N" + character + "A",
            character + "Unknown" + character,
        )
    )
    assert disagreements(symbol_model, cells, removed) == []


@UNICODE_15
def test_n_agrees_over_runs_of_every_removable_and_edge_point(symbol_model: ModuleType) -> None:
    """Every removable and edge-space code point (6,339), in runs of 1, 16, 17 and 20, alone, at
    every place of each word and the empty cell, and at both ends: the streaming verdict is the
    model's (a run past the bound is never counted toward it; an edge run inside a word is
    inner, not dropped; a removable point is deleted wherever it is)."""
    removed, edges = model_kinds(symbol_model)
    assert len(removed) + len(edges) == 6339
    cells = (
        cell
        for point in sorted(removed | edges)
        for run in RUNS
        for cell in shapes(chr(point) * run)
    )
    assert disagreements(symbol_model, cells, removed) == []


LONG = 131_072
LONG_POINTS = (0x20, 0x2800, 0x200B, 0x0085)
"""An edge space of each kind (ASCII, U+2800) and removable points (U+200B, and U+0085, which is
whitespace too)."""


@UNICODE_15
def test_n_agrees_over_runs_of_131072_points(symbol_model: ModuleType) -> None:
    """Runs of 131,072 of each of ``LONG_POINTS`` at every place of each word: the streaming verdict
    is the model's."""
    removed, _ = model_kinds(symbol_model)
    cells = (cell for point in LONG_POINTS for cell in shapes(chr(point) * LONG))
    assert disagreements(symbol_model, cells, removed) == []


def random_cells(seed: int, count: int) -> list[str]:
    """Round 3's generator: strings of 0 to 40 code points from a pool of edge, removable and
    confusable code points and the words' letters."""
    rng = random.Random(seed)
    pool = [
        chr(p)
        for p in (
            *(0x20, 0xA0, 0x2800, 0x200B, 0x200D, 0xAD, 0x3000, 0x3381, 0xFF2E, 0xFF21),
            *(0x212A, 0x1D2C, 0x1D2D, 0xFDFA, 0x0345, 0x0301, 0x0130, 0x017F),
        )
    ] + list("NAnaUuKkOoWwLl/ ")
    return ["".join(rng.choice(pool) for _ in range(rng.randint(0, 40))) for _ in range(count)]


def adversarial_cells(seed: int, count: int, edges: list[str]) -> list[str]:
    """A spelling of the family with runs of edge and removable code points of 0 to 40 at its ends
    and of 1 to 3 inside it, and now and then a kept code point that composes or folds: the
    generator that tells the wrong readings apart (round 3's random one alone does not)."""
    rng = random.Random(seed)
    kept = ["\u0301", "\u0345", "\u0338", "\u3099", "\u1161", "\u0307", "\u212a", "\u017f"]
    kept += ["\u0130", "\uff2e", "\u3381", "\u1d2c"]
    family = ["NA", "Unknown", "N/A", "null", "UNKNOWN", "na", "Na", "n/A"]

    def run(low: int, high: int) -> str:
        return "".join(rng.choice(edges) for _ in range(rng.randint(low, high)))

    found = []
    for _ in range(count):
        parts = [run(0, 40)]
        for character in rng.choice(family):
            parts.append(character)
            if rng.random() < 0.15:
                parts.append(run(1, 3))
            if rng.random() < 0.05:
                parts.append(rng.choice(kept))
        parts.append(run(0, 40))
        if rng.random() < 0.1:
            parts.append(rng.choice(kept))
        found.append("".join(parts))
    return found


@UNICODE_15
def test_n_agrees_over_seeded_random_and_adversarial_cells(symbol_model: ModuleType) -> None:
    """20,000 strings of each generator, seeded: the streaming verdict is the model's; the
    adversarial ones hold look-alikes and values both."""
    removed, edges = model_kinds(symbol_model)
    points = [*sorted(edges), 0x0B, 0x0D, 0x85, 0x2028, 0x200B, 0xAD, 0xFEFF, 0x1D173, 0xE0020]
    adversarial = adversarial_cells(11, 20_000, [chr(p) for p in points])
    assert disagreements(symbol_model, random_cells(7, 20_000), removed) == []
    assert disagreements(symbol_model, adversarial, removed) == []
    verdicts = collections.Counter(
        symbol_model.lookalike(cell, frozenset(), WORDS, removed) for cell in adversarial
    )
    assert verdicts[True] > 5_000
    assert verdicts[False] > 5_000


def test_the_bound_is_sixteen_kept_points_white_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """White-box (the verdict cannot show it: any bound of 7 or more gives the same verdicts): the
    scan reads at most 16 kept code points and answers ``None`` at the 17th, without reading
    further; a trailing run past the bound is dropped, not counted; an edge run before a kept
    point is inner."""
    found = mutation()
    assert found.NORMAL_KEPT == 16
    assert found.normal_form("a" * 16) == "a" * 16
    assert found.normal_form("a" * 17) is None
    assert found.normal_form("NA" + " " * 40) == "na"
    assert found.normal_form(" " * 40 + "NA" + "\u200b \u2800" * 40) == "na"
    assert found.normal_form("a" * 16 + "\u3000" * 40) == "a" * 16
    assert found.normal_form("N A") is None
    assert found.normal_form("N\u200bA") == "na"
    assert found.normal_form("") == ""
    calls = []
    classify = found.point_class

    def counted(character: str) -> int:
        calls.append(character)
        return classify(character)

    monkeypatch.setattr(found, "point_class", counted)
    assert found.normal_form("x" * 17 + "y" * LONG) is None
    assert len(calls) == 17
    calls.clear()
    assert found.normal_form("\ufdfa" * LONG) is None
    assert len(calls) == 17
    calls.clear()
    assert found.normal_form("x" * 16 + " " * 20) == "x" * 16
    assert len(calls) == 36
    calls.clear()
    assert found.normal_form("N" + " " * 5 + "A" + "z" * LONG) is None
    assert len(calls) == 7


def prepass_disagreements(cells: Any) -> list[str]:
    """The cells whose normal form by the pre-pass (``trimmed``, then ``normal_form`` on what it
    leaves) is not the streaming rule's (``normal_form`` on the whole cell): the value, not only
    the verdict."""
    found = mutation()
    wrong = []
    for cell in cells:
        core = found.trimmed(cell)
        by_prepass = None if core is None else found.normal_form(core)
        if by_prepass != found.normal_form(cell):
            wrong.append(cell.encode("unicode_escape").decode()[:60])
    return wrong


@UNICODE_15
def test_the_prepass_is_the_streaming_rule(symbol_model: ModuleType) -> None:
    """Review r2 minor 3, the pre-pass's equality: over runs of 1, 16, 17 and 20 of every
    removable and edge-space code point at every place of each word; runs of 131,072 of
    ``LONG_POINTS``; and the seeded random and adversarial strings, the normal form the pre-pass
    leads to is the streaming rule's, value for value (the verdict tests above hold ``lookalike``,
    which takes the pre-pass, to the model, over every code point too)."""
    removed, edges = model_kinds(symbol_model)
    runs = (
        cell
        for point in sorted(removed | edges)
        for run in RUNS
        for cell in shapes(chr(point) * run)
    )
    assert prepass_disagreements(runs) == []
    long = (cell for point in LONG_POINTS for cell in shapes(chr(point) * LONG))
    assert prepass_disagreements(long) == []
    points = [*sorted(edges), 0x0B, 0x0D, 0x85, 0x2028, 0x200B, 0xAD, 0xFEFF, 0x1D173, 0xE0020]
    adversarial = adversarial_cells(13, 20_000, [chr(p) for p in points])
    assert prepass_disagreements(random_cells(17, 20_000)) == []
    assert prepass_disagreements(adversarial) == []


def test_the_prepass_classes_are_every_code_point_s() -> None:
    """The pre-pass's classes, built at import from ``SCANNED_BLOCKS``, are those of all
    1,114,112 code points under the interpreter's Unicode: no code point outside the blocks is of
    D312's unsafe class or ``str.isspace()``; ``REMOVABLE_RUNS`` matches a code point exactly
    where ``point_class`` says removable; ``EDGE_SPACES`` is every ``str.isspace()`` code point
    and U+2800."""
    from aibi.core.schema import visible

    found = mutation()
    inside = {p for first, last in found.SCANNED_BLOCKS for p in range(first, last + 1)}
    outside = [
        hex(p)
        for p in range(LAST + 1)
        if p not in inside and (visible.unsafe_point(p) or chr(p).isspace())
    ]
    assert outside == []
    wrong = [
        hex(p)
        for p in range(LAST + 1)
        if (found.REMOVABLE_RUNS.fullmatch(chr(p)) is not None)
        != (found.point_class(chr(p)) == found.REMOVABLE)
    ]
    assert wrong == []
    spaces = {chr(p) for p in range(LAST + 1) if chr(p).isspace()}
    assert set(found.EDGE_SPACES) == spaces | {"\u2800"}
    assert len(found.EDGE_SPACES) == len(set(found.EDGE_SPACES))


def test_the_prepass_white_box() -> None:
    """The pre-pass by number: it deletes removable code points anywhere, then strips edge spaces
    (whitespace and U+2800) at both ends, and answers ``None`` past ``NORMAL_KEPT`` code points
    (16 kept: kept; 17: ``None``); an inner edge space stays for ``normal_form`` to refuse."""
    found = mutation()
    assert found.trimmed("a" * 16) == "a" * 16
    assert found.trimmed("a" * 17) is None
    assert found.trimmed("N" + "\u200b" * 40 + "A") == "NA"
    assert found.trimmed(" \u2800\u3000NA\u2800 \u200b") == "NA"
    assert found.trimmed("\u2800 \u200b \u2800") == ""
    assert found.trimmed("N A") == "N A"
    assert found.trimmed("N\u0085A") == "NA"
    assert found.trimmed("NA" + "\u2800" * 40 + "\u200b" + " " * 40) == "NA"
    assert found.trimmed("Somatic" + " " * LONG) == "Somatic"
    assert found.trimmed(" " + "a" * 16 + "\u200b" * 40 + " ") == "a" * 16


LONG_CELLS = (
    "Somatic" + " " * 131_062,
    "Somatic" + "\u2800" * 131_062,
    " " * 131_050 + "Somatic, Germline",
)
"""Long cells that are no look-alike: a short value padded by spaces or U+2800 (the pre-pass
leaves the value), and spaces before a value longer than ``NORMAL_KEPT`` (the pre-pass answers
``None`` itself)."""
LONG_CALLS = (7, 7, 0)
"""The code points ``normal_form`` reads of each of ``LONG_CELLS`` after the pre-pass: the value's,
or none."""


def test_a_long_padded_cell_costs_a_pass_of_the_c_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 minor 3 and r3 minors 1-2, the cost, white-box first: each of ``LONG_CELLS`` is
    decided by the pre-pass, ``normal_form`` reading only what it leaves (``LONG_CALLS``), never
    the cell; and, by the thread's CPU time (``time.thread_time``, so that other processes on the
    runner do not count), it costs at most 20 times a one-character regular expression's ``sub``
    over the same cell, the best of five runs each (about two to five times, unloaded or with the
    CPU shared; the per-code-point scan was twenty to fifty times, and over a hundred for the
    last cell)."""
    found = mutation()
    baseline = re.compile("[\x00]+")
    classify = found.point_class
    for cell, wanted in zip(LONG_CELLS, LONG_CALLS, strict=True):
        calls: list[str] = []

        def counted(character: str, calls: list[str] = calls) -> int:
            calls.append(character)
            return classify(character)

        monkeypatch.setattr(found, "point_class", counted)
        assert found.lookalike(cell, found.MISSING_FAMILY, found.MISSING_WORDS) is False
        assert len(calls) == wanted
        monkeypatch.setattr(found, "point_class", classify)
        cost = best_cpu(
            lambda cell=cell: found.lookalike(cell, found.MISSING_FAMILY, found.MISSING_WORDS)
        )
        assert cost <= 20 * best_cpu(lambda cell=cell: baseline.sub("", cell)), cell[:9]


def best_cpu(run: Any) -> float:
    """The least of five runs' CPU time in this thread."""
    times = []
    for _ in range(5):
        start = time.thread_time()
        run()
        times.append(time.thread_time() - start)
    return max(min(times), 1e-6)


def test_a_removable_space_is_removed_before_it_is_a_space() -> None:
    """A code point that is both of D312's unsafe class and whitespace is removable (deleted
    inside a word, so ``N`` U+0085 ``A`` is a look-alike), and a plain edge space inside a word
    is inner (``N`` U+3000 ``A`` is a value): the classes are asked removable first. The points
    of both classes, written out."""
    found = mutation()
    both = {p for p in range(LAST + 1) if chr(p).isspace() and found._removable(p)}
    assert both == {0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x85, 0x2028, 0x2029}
    for point in both:
        assert found.point_class(chr(point)) == found.REMOVABLE
        assert found.lookalike("N" + chr(point) + "A", frozenset(), WORDS)
    for point in (0x20, 0xA0, 0x3000, 0x2800, 0x09, 0x0A):
        assert found.point_class(chr(point)) == found.EDGE_SPACE
        assert not found.lookalike("N" + chr(point) + "A", frozenset(), WORDS)


def test_a_long_cell_costs_one_bounded_pass() -> None:
    """Timed, generously (the bound itself is pinned white-box above): a cell of 131,072 U+FDFA
    (each NFKC's longest expansion) and one of ``N``, 131,072 zero-width spaces and ``A`` (a
    look-alike, read whole)."""
    found = mutation()
    start = time.perf_counter()
    assert found.lookalike("\ufdfa" * LONG, frozenset(), WORDS) is False
    assert time.perf_counter() - start < 0.5
    start = time.perf_counter()
    assert found.lookalike("N" + "\u200b" * LONG + "A", frozenset(), WORDS) is True
    assert time.perf_counter() - start < 2.0


@UNICODE_15
def test_no_kept_code_point_normalises_to_nothing(symbol_model: ModuleType) -> None:
    """The bound's premise, over every code point: no code point the scan keeps has an empty
    NFKC, casefold or second NFKC, so a cell's N has at least one code point a kept one; and no
    ASCII character is a canonical composite (composition never makes one)."""
    removed, edges = model_kinds(symbol_model)
    empty = []
    for point in range(LAST + 1):
        if point in removed or point in edges:
            continue
        character = chr(point)
        first = unicodedata.normalize("NFKC", character)
        if first == "" or first.casefold() == "" or symbol_model.normal(character) == "":
            empty.append(point)
    assert empty == []
    ascii_points = [chr(p) for p in range(0x80)]
    assert all(unicodedata.normalize("NFD", a) == a for a in ascii_points)


# --- The refusal, through the reader's row check --------------------------------------------------

HEADER = "\t".join(
    (
        "Hugo_Symbol",
        "Entrez_Gene_Id",
        "Variant_Classification",
        "Tumor_Sample_Barcode",
        "HGVSp_Short",
        "Mutation_Status",
    )
)
COLUMN_AT = {"symbol": 0, "class": 2, "status": 5}
VALID = ("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic")
SKIPPED_BY = {
    "symbol": {2: "Silent"},
    "class": {0: "Unknown", 1: "0"},
    "status": {2: "Silent"},
}
"""What makes a row one the reference skips when ``column`` holds the cell: ``Silent``, or, for
the class, whose own cell it is, ``Unknown`` with Entrez ``0``."""


def reading() -> Any:
    meta = importlib.import_module("aibi.packs.onco.meta")
    fields = meta.MutationFields(None, None, (), "GRCh37")
    found = mutation()._Reading("m.txt", frozenset({"S1"}), fields, ImportLimits(), 0)
    assert found.header(1, HEADER, 0) is None
    return found


def row(column: str, cell: str, skipped: bool) -> list[str]:
    fields: list[str] = list(VALID)
    if skipped:
        for at, value in SKIPPED_BY[column].items():
            fields[at] = value
    fields[COLUMN_AT[column]] = cell
    return fields


def code_of(found: Any) -> str:
    if found is None:
        return "ok"
    code, _, reason = found
    names = {REASON: "MISSING_SPELLING", EDGED: "EDGED", BLANK: "REQUIRED_VALUE"}
    return str(names.get(reason, code))


EMPTY_N = {
    ("class", False): {"whitespace": "REQUIRED_VALUE"},
    ("symbol", False): {"whitespace": "REQUIRED_VALUE"},
}
"""A blank-looking cell's outcome where it is not ``MISSING_SPELLING``'s (the class and the
status) or the edged symbol's (the symbol), by the model's class of its one code point: blank by
``str.strip()`` on a validated row is ``REQUIRED_VALUE`` first, in the class and the symbol."""
EMPTY_N_COUNTS = {
    ("class", False, "REQUIRED_VALUE"): 29,
    ("class", False, "MISSING_SPELLING"): 6310,
    ("class", True, "MISSING_SPELLING"): 6339,
    ("status", False, "MISSING_SPELLING"): 6339,
    ("status", True, "MISSING_SPELLING"): 6339,
    ("symbol", False, "REQUIRED_VALUE"): 29,
    ("symbol", False, "EDGED"): 6310,
    ("symbol", True, "EDGED"): 6339,
}
"""How many of the code points whose N is empty get each outcome, per column and row (skipped
or not): a class narrowed or the order changed, in the code and the model alike, moves a count."""


@UNICODE_15
def test_every_point_whose_n_is_empty_is_refused_by_its_class(symbol_model: ModuleType) -> None:
    """Review mi-2, by class: each code point whose N alone is empty (the model's removable and
    edge-space points, 6,339), as the whole cell of each column, on a validated and on a skipped
    row, through the reader's row check: ``REQUIRED_VALUE`` where it is blank by ``str.strip()``
    on a validated row in the class or the symbol, the edged symbol in the symbol otherwise, and
    ``MISSING_SPELLING`` otherwise, its reason the fixed text; the exactly empty cell is no
    look-alike (``REQUIRED_VALUE`` for a validated class or symbol, else accepted)."""
    removed, edges = model_kinds(symbol_model)
    check = reading()
    counts: collections.Counter[tuple[str, bool, str]] = collections.Counter()
    wrong = []
    for point in sorted(removed | edges):
        character = chr(point)
        assert symbol_model.normal(character, removed) == ""
        kind = symbol_model.class_of(point)
        for column in COLUMN_AT:
            for skipped in (False, True):
                found = check.checked(row(column, character, skipped))
                code = code_of(found)
                default = "EDGED" if column == "symbol" else "MISSING_SPELLING"
                wanted = EMPTY_N.get((column, skipped), {}).get(kind, default)
                if code != wanted or found[1] != COLUMN_AT[column]:
                    wrong.append((hex(point), column, skipped, found))
                counts[(column, skipped, code)] += 1
    assert wrong == []
    assert counts == EMPTY_N_COUNTS
    empty = {
        (c, s): code_of(check.checked(row(c, "", s))) for c in COLUMN_AT for s in (False, True)
    }
    assert empty == {
        ("symbol", False): "REQUIRED_VALUE",
        ("symbol", True): "ok",
        ("class", False): "REQUIRED_VALUE",
        ("class", True): "ok",
        ("status", False): "ok",
        ("status", True): "ok",
    }


def substitutions(oracle: ModuleType, removed: frozenset[int]) -> dict[str, list[str]]:
    """For each letter of the words, the non-ASCII code points whose N alone is that letter (or the
    letter's word, for U+3381), found over every code point by the model."""
    letters = set("".join(WORDS))
    found: dict[str, list[str]] = collections.defaultdict(list)
    for point in range(0x80, LAST + 1):
        if point in removed:
            continue
        normal = oracle.normal(chr(point), removed)
        if normal in letters or normal in WORDS:
            found[normal].append(chr(point))
    return found


LOOKALIKE_COUNTS = {
    ("class", "MISSING_SPELLING"): 1041,
    ("status", "MISSING_SPELLING"): 1041,
    ("symbol", "MISSING_SPELLING"): 617,
    ("symbol", "ok"): 424,
}
"""How the 1,041 substitution cells fall in each column: every one a look-alike in the class and
the status; in the symbol, those of ``unknown`` and ``na`` (617), the rest (``n/a`` and ``null``)
values."""


@UNICODE_15
def test_every_substitution_that_spells_a_word_is_refused(symbol_model: ModuleType) -> None:
    """By class: each word of the family, in its lower and upper case, with one of its letters
    replaced by each code point whose N is that letter (a fullwidth, a modifier, a Kelvin sign, a
    circled or mathematical letter among them), and each code point whose N is a word alone
    (U+3381), as the cell of each column on a validated row: refused ``MISSING_SPELLING`` exactly
    where the model's look-alike holds (the symbol's words are ``unknown`` and ``na`` alone), and
    accepted otherwise; the counts are literals."""
    removed, _ = model_kinds(symbol_model)
    replacing = substitutions(symbol_model, removed)
    cells = list(replacing.get("na", []))
    for word in WORDS:
        for written in (word, word.upper()):
            for at, letter in enumerate(written):
                for point in replacing.get(letter.lower(), []):
                    cells.append(written[:at] + point + written[at + 1 :])
    check = reading()
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    wrong = []
    for cell in cells:
        for column in COLUMN_AT:
            exact, words = (SYMBOL_EXACT, SYMBOL_WORDS) if column == "symbol" else (FAMILY, WORDS)
            expected = symbol_model.lookalike(cell, exact, words, removed)
            code = code_of(check.checked(row(column, cell, False)))
            if code != ("MISSING_SPELLING" if expected else "ok"):
                wrong.append((cell.encode("unicode_escape").decode(), column, code))
            counts[(column, code)] += 1
    assert wrong == []
    assert counts == LOOKALIKE_COUNTS


R = "MISSING_SPELLING"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        # the symbol's checks keep their order: the null spelling's, NO_GENE, the edge, then this
        ((" NA", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), ("PADDED_VALUE", 0)),
        ((" NA", "", "Missense_Mutation", "S1", "p.R1H", "Somatic"), ("NO_GENE", 0)),
        (("\u2800NA", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), ("EDGED", 0)),
        (("\u2800NA", "7157", "Silent", "S1", "p.R1H", "Somatic"), ("EDGED", 0)),
        (("na", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), (R, 0)),
        (("na", "7157", "Silent", "S1", "p.R1H", "Somatic"), (R, 0)),
        (("NA", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), None),
        (("Unknown", "0", "Missense_Mutation", "S1", "p.R1H", "Somatic"), None),
        (("UNKNOWN", "0", "Missense_Mutation", "S1", "p.R1H", "Somatic"), (R, 0)),
        (("N/A", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), None),
        (("null", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), None),
        (("None", "7157", "Missense_Mutation", "S1", "p.R1H", "Somatic"), None),
        # the class: a lone space is blank first on a validated row, U+200B is a look-alike
        (("TP53", "7157", " ", "S1", "p.R1H", "Somatic"), ("REQUIRED_VALUE", 2)),
        (("TP53", "7157", "\u200b", "S1", "p.R1H", "Somatic"), (R, 2)),
        (("Unknown", "0", " ", "S1", "p.R1H", "Somatic"), (R, 2)),
        (("Unknown", "0", "\u200b", "S1", "p.R1H", "Somatic"), (R, 2)),
        (("TP53", "7157", "Unknown", "S1", "p.R1H", "Somatic"), None),
        (("TP53", "7157", "UNKNOWN ", "S1", "p.R1H", "Somatic"), (R, 2)),
        # the status: every look-alike, on every row; a value written otherwise is a value
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", " "), (R, 5)),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "\uff2e\uff21"), (R, 5)),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "nA"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", ""), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", " Somatic"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "somatic"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "[Not Available]"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "#N/A"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "NaN"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "-"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "?"), None),
        (("TP53", "7157", "Missense_Mutation", "S1", "p.R1H", "none"), None),
        # by column, in header order: the class's look-alike before the status's, the symbol's
        # before the class's, and an earlier column's other failure first
        (("TP53", "7157", "\u3381", "S1", "p.R1H", "\u3381"), (R, 2)),
        (("\u3381", "7157", "\u3381", "S1", "p.R1H", "\u3381"), (R, 0)),
        (("TP53", "7157", "\u3381", "S9", "p.R1H", "Somatic"), (R, 2)),
        (("TP53", "7157", "Missense_Mutation", "S9", "p.R1H", "\u3381"), ("UNKNOWN_SAMPLE", 3)),
    ],
)
def test_the_checks_keep_their_order(
    fields: tuple[str, ...], expected: tuple[str, int] | None
) -> None:
    """Review mi-2 and D444's order: per row the columns in header order, per column the existing
    checks, then the edged symbol, then the look-alike (a tie keeps the order: `` NA`` keeps the
    null spelling's ``PADDED_VALUE`` or ``NO_GENE``, U+2800 ``NA`` the edged symbol); the reason is
    the fixed text."""
    found = reading().checked(list(fields))
    if expected is None:
        assert found is None
        return
    code, column = expected
    assert (code_of(found), found[1]) == (code, column)
    if code == R:
        assert found == ("MISSING_SPELLING", column, REASON)


def test_the_reason_holds_no_cell(world: Any, registry: PackRegistry) -> None:
    """A refused look-alike's message names the file, its line and its column, and the fixed
    reason, never the cell (D400)."""
    c = corpus()
    sentinel = "N\u200bA"
    rows = [c.R2, {**c.R1, "Mutation_Status": sentinel}]
    files = c.study(data_mutations_DOT_txt=c.maf(rows))
    with pytest.raises(ImportRefused) as raised:
        world.publish("r", write(world, "r", files), registry, pack="onco")
    (refusal,) = raised.value.refusals
    assert str(refusal.code) == "onco.MISSING_SPELLING"
    said = "".join(
        getattr(segment, "text", getattr(segment, "data", "")) for segment in refusal.message
    )
    assert said == f"data_mutations.txt line 4, column 16: {REASON}"
    assert sentinel not in json.dumps(refusal.model_dump(mode="json"), ensure_ascii=False)


# --- The engine -----------------------------------------------------------------------------------

ENGINE_COLUMNS = {
    "status": ("Mutation_Status", "mutations.mutation_status", "Somatic"),
    "class": ("Variant_Classification", "mutations.variant_classification", "Missense_Mutation"),
}
UNKNOWN_ANSWERS = {
    "every": ("UNKNOWN", "UNKNOWN", "UNKNOWN"),
    "some": ("TRUE", "UNKNOWN", "UNKNOWN"),
    "negate": ("UNKNOWN", "UNKNOWN", "UNKNOWN"),
}
"""S1 (a row of the value and a row of the cell), S2 (a row of the cell), S3 (no row): with the
cell UNKNOWN, no shape closes on it (no coverage is declared, so S3 is UNKNOWN too)."""
VALUE_ANSWERS = {
    "every": ("FALSE", "FALSE", "UNKNOWN"),
    "some": ("TRUE", "UNKNOWN", "UNKNOWN"),
    "negate": ("TRUE", "TRUE", "UNKNOWN"),
}
"""With the cell a value other than the one asked: ``every`` closes FALSE and the negation TRUE
at one row, the closed answers a look-alike read as a value would give."""
ENGINE_CELLS = {
    "NA": UNKNOWN_ANSWERS,
    "nA": UNKNOWN_ANSWERS,
    "uNKNOWN": UNKNOWN_ANSWERS,
    "n/A": UNKNOWN_ANSWERS,
    "nULL": UNKNOWN_ANSWERS,
    "": UNKNOWN_ANSWERS,
    "None": VALUE_ANSWERS,
    "N A": VALUE_ANSWERS,
}
ENGINE_REFUSED = ("\uff2e\uff21", "NA ", "\u200b", "\u2800", "Unk\u200bnown", "N\u00adA", "\u3381")


def engine_rows(column: str, cell: str) -> bytes:
    c = corpus()
    source, _, good = ENGINE_COLUMNS[column]
    rows = [
        {**c.R1, source: good},
        {**c.R1, "Hugo_Symbol": "EGFR", "Entrez_Gene_Id": "1956", source: cell},
        {**c.R2, "Hugo_Symbol": "EGFR", "Entrez_Gene_Id": "1956", source: cell},
    ]
    return c.maf(rows)


@pytest.mark.parametrize("column", list(ENGINE_COLUMNS))
@pytest.mark.parametrize("cell", list(ENGINE_CELLS))
def test_the_engine_reads_the_family_as_unknown(
    column: str, cell: str, world: Any, registry: PackRegistry
) -> None:
    """Review mi-8, at the engine: ``every``, ``some`` and ``some`` with a negated value over
    ``mutations``, each sample's answer by the reference evaluator and the counts by the SQL
    compiler (``count_cohort``), which agree: a spelling of the family (and the empty cell) is
    UNKNOWN, so no shape closes on it; a value is a value. ``missing_codes`` is no unconfirmed
    field the count's caveat names."""
    from aibi.core.catalog.service import Catalog
    from aibi.core.catalog.tools import BY_NAME, call
    from aibi.core.engine.evaluate import evaluate
    from aibi.core.engine.resolve import resolve
    from aibi.core.engine.worker import Workers
    from aibi.core.schema.limits import QueryLimits
    from aibi.core.schema.loading import load_document

    if column == "class" and cell == "":
        pytest.skip("a blank class is refused on a validated row (REQUIRED_VALUE)")
    _, id, good = ENGINE_COLUMNS[column]
    files = corpus().study(data_mutations_DOT_txt=engine_rows(column, cell))
    published = world.publish("e", write(world, "e", files), registry, pack="onco")
    release = world.store.load(published.manifest)
    catalog = Catalog(world.store, registry=registry, workers=Workers(QueryLimits()))

    def value(negate: bool = False) -> dict[str, Any]:
        found: dict[str, Any] = {"kind": "value", "column": id, "values": [good]}
        return {**found, "negate": True} if negate else found

    clauses = {
        "every": {
            "kind": "exists",
            "table": "mutations",
            "quantifier": "every",
            "where": [value()],
        },
        "some": {"kind": "exists", "table": "mutations", "where": [value()]},
        "negate": {"kind": "exists", "table": "mutations", "where": [value(True)]},
    }
    answers = {}
    for shape, clause in clauses.items():
        document = {
            "aibi": "1",
            "dataset": "e",
            "unit": "samples",
            "cohorts": {"c": {"all": [clause]}},
        }
        loaded = load_document(json.dumps(document))
        assert loaded.document is not None
        resolved = resolve(loaded.document, {"e": release}, loaded.positions, registry=registry)
        assert resolved.refusals == []
        evaluated = evaluate(resolved.cohorts["c"])
        units = release.rows("samples")
        per = {
            str(units.cell(r, "sample_id").value): v.value.value
            for r, v in enumerate(evaluated.values)
        }
        answers[shape] = (per["S1"], per["S2"], per["S3"])
        counted = call(
            catalog, BY_NAME["count_cohort"], json.dumps({"document": document}).encode()
        )
        assert not isinstance(counted, list), counted
        dumped = json.dumps(counted.model_dump(mode="json"))
        sql = {k: int(n) for k, n in re.findall(r'"n_(true|false|unknown)": (\d+)', dumped)[:3]}
        tallies = collections.Counter(per.values())
        assert sql == {
            "true": tallies["TRUE"],
            "false": tallies["FALSE"],
            "unknown": tallies["UNKNOWN"],
        }, shape
        assert f"{id}/fields/missing_codes" not in dumped
    assert answers == ENGINE_CELLS[cell]


@pytest.mark.parametrize("column", list(ENGINE_COLUMNS))
@pytest.mark.parametrize("cell", ENGINE_REFUSED)
def test_a_look_alike_cannot_import(
    column: str, cell: str, world: Any, registry: PackRegistry
) -> None:
    """A look-alike (a fullwidth, padded, invisible, joined or squared spelling, a blank-looking
    cell) in the rows the engine test reads is refused at import: no closed answer can arise from
    one."""
    files = corpus().study(data_mutations_DOT_txt=engine_rows(column, cell))
    with pytest.raises(ImportRefused) as raised:
        world.publish("x", write(world, "x", files), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.MISSING_SPELLING"]


# --- The carry (D241): the slice's own cases ------------------------------------------------------


def as_pack_080(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pack as 0.8.0 read the class and the status: no spelling of the family declared, every
    cell counted by D228, no look-alike refused."""
    build = importlib.import_module("aibi.packs.onco.build")
    found = mutation()
    monkeypatch.setattr(found, "MISSING_SPELLINGS", MappingProxyType({}))
    monkeypatch.setattr(build, "MAF_SPELLED_MISSING_CODES", MappingProxyType({}))
    monkeypatch.setattr(found, "lookalike", lambda cell, exact, words: False)


CARRY_COLUMNS = {"status": "mutations.mutation_status", "class": "mutations.variant_classification"}


def carry(
    world: Any,
    registry: PackRegistry,
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    shape: str,
    *edits: Mapping[str, Any],
) -> Any:
    """A study of ``shape`` published and curated with ``edits`` as pack 0.8.0 read it, then
    re-imported by the slice: the re-import's result, or its refusal."""
    source = "Mutation_Status" if column == "status" else "Variant_Classification"
    files = corpus().study(data_mutations_DOT_txt=shaped(shape, source))
    directory = write(world, "c", files)
    with monkeypatch.context() as patched:
        as_pack_080(patched)
        world.publish("c", directory, registry, pack="onco")
        world.curate("c", registry, *edits)
    latest = world.store.latest("c")
    assert latest is not None
    BEFORE["release"] = world.store.load(latest.manifest)
    BEFORE["descriptors"] = {d.id: d for d in world.store.descriptors(latest.manifest)}
    try:
        return world.reimport("c", directory, registry, pack="onco")
    except ImportRefused as refused:
        return refused


BEFORE: dict[str, Any] = {}
"""The curated release as pack 0.8.0 read it, which ``carry`` last published."""


def set_field(column: str, field: str, value: Any) -> dict[str, Any]:
    return {
        "op": "set",
        "descriptor": CARRY_COLUMNS[column],
        "pointer": f"/fields/{field}",
        "value": value,
    }


def refusals(found: Any) -> list[tuple[str, str | None]]:
    assert isinstance(found, ImportRefused), found
    return [(str(r.code), r.path) for r in found.refusals]


VALUES = {"status": ["Somatic", "Germline"], "class": ["Missense_Mutation", "Nonsense_Mutation"]}


@pytest.mark.parametrize("column", list(CARRY_COLUMNS))
@pytest.mark.parametrize("spelled", ["Unknown", "NA", "null"])
def test_a_curated_permissible_value_of_the_family_refuses_the_re_import(
    column: str, spelled: str, world: Any, registry: PackRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D241's rule, the slice's case: an operator's ``permissible_values`` listing ``Unknown``,
    ``NA`` or ``null`` (valid under pack 0.8.0) refuses the re-import, the value now a missing
    code: ``CONFLICTING_MEMBERS`` at the value. The remedy: drop it and re-import."""
    values = [*VALUES[column], spelled]
    found = carry(
        world,
        registry,
        monkeypatch,
        column,
        "category",
        set_field(column, "datatype", "category"),
        set_field(column, "permissible_values", {"values": [{"value": v} for v in values]}),
    )
    path = f"/descriptors/{CARRY_COLUMNS[column]}/fields/permissible_values/values/2/value"
    assert refusals(found) == [("CONFLICTING_MEMBERS", path)]


def outcome(world: Any, found: Any, column: str) -> dict[str, Any]:
    """What a re-import made of the column: its datatype and the datatype's status, its missing
    codes (their number), the curation status of each curated field, the changes it reported,
    and, for the derived column of a case, that column's cells; or the refusal."""
    if isinstance(found, ImportRefused):
        return {"refused": refusals(found)}
    descriptors = {d.id: d for d in world.store.descriptors(found.manifest)}
    descriptor = descriptors[CARRY_COLUMNS[column]]
    fields = descriptor.fields
    codes = fields.missing_codes
    table = world.store.load(found.manifest).rows("mutations")
    name = CARRY_COLUMNS[column].split(".")[1]
    result: dict[str, Any] = {
        "datatype": (fields.datatype, descriptor.curation["/fields/datatype"].status),
        "codes": None if codes is None else len(codes),
        "changes": sorted(f"{c.descriptor} {c.pointer} {c.happened}" for c in found.changes),
        "cells": [table.cell(r, name).value for r in range(len(table))],
    }
    if codes is not None and len(codes) < 152:
        result["codes"] = dict(codes)
        result["codes status"] = descriptor.curation["/fields/missing_codes"].status
    if fields.maps_to is not None:
        entry = descriptor.curation["/fields/maps_to"]
        result["maps_to"] = (
            fields.maps_to.model_dump(mode="json", exclude_none=True),
            entry.status,
        )
    if "mutations.flag" in descriptors:
        result["flag"] = [table.cell(r, "flag").value for r in range(len(table))]
        before = BEFORE["release"].rows("mutations")
        result["flag before"] = [before.cell(r, "flag").value for r in range(len(before))]
    before_fields = BEFORE["descriptors"][CARRY_COLUMNS[column]]
    result["datatype before"] = (
        before_fields.fields.datatype,
        before_fields.curation["/fields/datatype"].status,
    )
    return result


def derived(column: str) -> dict[str, Any]:
    first = VALUES[column][0]
    return {
        "op": "put",
        "descriptor": {
            "kind": "column",
            "id": "mutations.flag",
            "label": "flag",
            "fields": {
                "datatype": "string",
                "derived": {
                    "op": "value_map",
                    "input": CARRY_COLUMNS[column].split(".")[1],
                    "map": {"NA": "imputed", first: "given"},
                },
            },
        },
    }


MAPS_TO_VALUE_MAP = {
    "NA": "Missense_Mutation",
    "Missense_Mutation": "Missense_Mutation",
    "Nonsense_Mutation": "Nonsense_Mutation",
}
"""An operator's ``maps_to`` value map of the class onto ``onco:mutation_class``, keyed on ``NA``
(a value of the column under pack 0.8.0), on the pack's own concept."""
CARRY_CASES: Mapping[str, tuple[str, str, tuple[Any, ...]]] = {
    "narrower codes": (
        "status",
        "string-to-category",
        (set_field("status", "missing_codes", {"NA": "UNKNOWN"}),),
    ),
    "disjoint codes": (
        "status",
        "category",
        (set_field("status", "missing_codes", {"None": "UNKNOWN"}),),
    ),
    "dead key": ("status", "category", (derived("status"),)),
    "maps_to dead key": (
        "class",
        "category",
        (
            set_field(
                "class",
                "maps_to",
                {"concept": "onco:mutation_class", "transform": {"value_map": MAPS_TO_VALUE_MAP}},
            ),
        ),
    ),
    "asserted string, flip to category": (
        "status",
        "string-to-category",
        (set_field("status", "datatype", "string"),),
    ),
    "asserted category, flip to string": (
        "class",
        "category-to-string",
        (set_field("class", "datatype", "category"),),
    ),
    "confirm-all, flip to string": (
        "status",
        "category-to-string",
        ({"op": "confirm", "descriptor": CARRY_COLUMNS["status"]},),
    ),
    "asserted category, no flip": (
        "class",
        "category",
        (set_field("class", "datatype", "category"),),
    ),
}
"""The slice's carry cases beyond the permissible values: an operator's narrower missing codes, a
derived value map and a ``maps_to`` value map keyed on ``NA``, and an operator's datatype where
D228's count flips the inference each way, or not."""


@pytest.mark.parametrize("case", list(CARRY_CASES))
def test_the_slice_s_carry_cases(
    case: str, world: Any, registry: PackRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D241's table, the slice's cases (``CARRY_OUTCOMES``, literals), through the real session
    and the real re-import of a study curated as pack 0.8.0 read it."""
    column, shape, edits = CARRY_CASES[case]
    found = carry(world, registry, monkeypatch, column, shape, *edits)
    assert outcome(world, found, column) == CARRY_OUTCOMES[case]


STATUS_CHANGE = "mutations.mutation_status /fields/missing_codes proposed"
CLASS_CHANGE = "mutations.variant_classification /fields/missing_codes proposed"
CARRY_OUTCOMES: Mapping[str, Any] = {
    "narrower codes": {
        "datatype before": ("string", "imported"),
        "datatype": ("category", "proposed"),
        "codes": {"NA": "UNKNOWN"},
        "codes status": "asserted",
        "changes": ["mutations.mutation_status /fields/datatype proposed", CLASS_CHANGE],
        "cells": ["Somatic", "Somatic", "Germline", "Germline", None, "Unknown", "null", "N/A"],
    },
    "disjoint codes": {
        "datatype before": ("category", "proposed"),
        "datatype": ("category", "proposed"),
        "codes": {"None": "UNKNOWN"},
        "codes status": "asserted",
        "changes": [CLASS_CHANGE],
        "cells": ["Somatic"] * 3 + ["Germline"] * 3 + ["NA", "NA"],
    },
    "dead key": {
        "datatype before": ("category", "proposed"),
        "datatype": ("category", "proposed"),
        "codes": 152,
        "changes": [STATUS_CHANGE, CLASS_CHANGE],
        "cells": ["Somatic"] * 3 + ["Germline"] * 3 + [None, None],
        "flag before": ["given"] * 3 + [None] * 3 + ["imputed", "imputed"],
        "flag": ["given"] * 3 + [None] * 5,
    },
    "maps_to dead key": {
        "datatype before": ("category", "proposed"),
        "datatype": ("category", "proposed"),
        "codes": 152,
        "changes": [STATUS_CHANGE, CLASS_CHANGE],
        "cells": ["Missense_Mutation"] * 3 + ["Nonsense_Mutation"] * 3 + [None, None],
        "maps_to": (
            {
                "concept": "onco:mutation_class",
                "transform": {
                    "value_map": {
                        "Missense_Mutation": "Missense_Mutation",
                        "NA": "Missense_Mutation",
                        "Nonsense_Mutation": "Nonsense_Mutation",
                    }
                },
            },
            "asserted",
        ),
    },
    "asserted string, flip to category": {
        "datatype before": ("string", "asserted"),
        "datatype": ("category", "proposed"),
        "codes": 152,
        "changes": [
            "mutations.mutation_status /fields/datatype proposed",
            STATUS_CHANGE,
            CLASS_CHANGE,
        ],
        "cells": ["Somatic", "Somatic", "Germline", "Germline", None, None, None, None],
    },
    "asserted category, flip to string": {
        "datatype before": ("category", "asserted"),
        "datatype": ("string", "imported"),
        "codes": 152,
        "changes": [
            STATUS_CHANGE,
            "mutations.variant_classification /fields/datatype proposed",
            CLASS_CHANGE,
        ],
        "cells": ["Missense_Mutation", "Nonsense_Mutation", "Splice_Site"] + [None] * 5,
    },
    "confirm-all, flip to string": {
        "datatype before": ("category", "asserted"),
        "datatype": ("string", "imported"),
        "codes": 152,
        "changes": [
            "mutations.mutation_status /fields/datatype proposed",
            STATUS_CHANGE,
            CLASS_CHANGE,
        ],
        "cells": ["Somatic", "Germline", "LOH"] + [None] * 5,
    },
    "asserted category, no flip": {
        "datatype before": ("category", "asserted"),
        "datatype": ("category", "asserted"),
        "codes": 152,
        "changes": [STATUS_CHANGE, CLASS_CHANGE],
        "cells": ["Missense_Mutation"] * 3 + ["Nonsense_Mutation"] * 3 + [None, None],
    },
}
"""Each case's outcome, written here: an operator's codes are carried, asserted, and win,
whatever they hold (D241 (c)): codes disjoint from the family (``None`` alone) leave all 152
spellings values (the status's ``NA`` cells are present) with nothing reported of the status; the
narrower codes likewise (the
published status reads ``Unknown``, ``null`` and ``N/A`` as values; the datatype the reader's
count gave, a category, is the one a count by the carried codes would not give, five distinct
values of seven: D444's stated disagreement), with no change reported of them; the value map's
``NA`` key now meets UNKNOWN cells, so its rows' derived value is UNKNOWN where 0.8.0 gave
``imputed`` (a dead key, nothing reported of it), and so does a ``maps_to`` value map's, carried
as written with nothing reported of it while its ``NA`` rows are UNKNOWN cells; an operator's
datatype over a flipped inference takes the importer's new one, replaced or demoted
(``category`` at ``proposed``, ``string`` at the pack's ``imported``), reported ``proposed``; over
an unchanged one it is carried."""


STATUS_HEADERS = ("mutation_status", "MUTATION_STATUS", "Mutation_status", "Mutation Status")


@pytest.mark.parametrize("header", STATUS_HEADERS)
@pytest.mark.parametrize("before", [False, True])
def test_a_status_header_written_otherwise_is_a_plain_column(
    header: str, before: bool, world: Any, registry: PackRegistry
) -> None:
    """D444's stated residual (it predates the slice: aliases are out of scope, a follow-up): the
    reader knows the status by its exact name ``Mutation_Status``. A header written otherwise
    (case, a space) with no exact column still publishes as ``mutations.mutation_status`` with no
    missing codes and no look-alike check, so ``NA`` and a fullwidth ``NA`` are present values;
    placed before an exact ``Mutation_Status``, it takes that id and the real column becomes
    ``mutations.mutation_status_2``, which carries the 152."""
    from aibi.core.schema.semantics import ObservationState

    c = corpus()
    if before:
        columns = tuple(
            name for x in c.COLUMNS for name in ((header, x) if x == "Mutation_Status" else (x,))
        )
    else:
        columns = tuple(header if x == "Mutation_Status" else x for x in c.COLUMNS)
    rows = [
        {**c.R1, header: "Somatic"},
        {**c.R2, header: "NA", "Hugo_Symbol": "EGFR", "Entrez_Gene_Id": "1956"},
        {**c.R1, "Start_Position": "9", "End_Position": "9", header: "\uff2e\uff21"},
    ]
    files = c.study(data_mutations_DOT_txt=c.maf(rows, columns))
    published = world.publish("h", write(world, "h", files), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    table = world.store.load(published.manifest).rows("mutations")
    plain = found["mutations.mutation_status"]
    assert plain.fields.source is not None
    assert plain.fields.source.original_name == header
    assert plain.fields.missing_codes is None
    cells = [table.cell(r, "mutation_status") for r in range(len(table))]
    assert [(cell.state, cell.value) for cell in cells] == [
        (ObservationState.PRESENT, "Somatic"),
        (ObservationState.PRESENT, "NA"),
        (ObservationState.PRESENT, "\uff2e\uff21"),
    ]
    if before:
        real = found["mutations.mutation_status_2"]
        assert real.fields.source is not None
        assert real.fields.source.original_name == "Mutation_Status"
        assert real.fields.missing_codes == dict.fromkeys(sorted(FAMILY), "UNKNOWN")
    else:
        assert "mutations.mutation_status_2" not in found
