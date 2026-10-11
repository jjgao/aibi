"""Discovery is total over hostile study directories (M4.2a-1a, D409; M4.0f-B, D398).

A study's directory holds entries whose names are not Unicode text (bytes that are not UTF-8, an
overlong or truncated sequence, a lone surrogate, a noncharacter), entries nobody can read (mode
000) and files that are swapped after they are listed. Over a generated set of such directories,
each run both directly (``discover(reader, source, limits)``) and through the core's importer
checks (``run_importer``) at three ``import_bytes`` (1 KiB, 64 KiB + 1, and a generous one):

- no run is ``PACK_FAILED``, and nothing but the core's own refusals escapes;
- the direct run returns a study, or raises the pack's ``Refused`` with one of its own codes, or
  lets out the reader's own ``ImportRefused``, the very instance the reader raised;
- the direct and the core's runs agree on whether the study is accepted and, when it is refused,
  on the refusal codes;
- no entry name or value appears in a refusal, a note, or an exception's text but a name as a
  ``data`` token of at most 200 characters of Unicode text.

Before the core's reader was total for such names (D398) the direct run raised ``ValidationError``
for one and the core's checks made that ``PACK_FAILED``.
"""

import contextlib
import importlib
import os
import signal
import threading
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.importers.checks import run_importer
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, Segment, TextSegment
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DirectoryEntry,
    ImportNote,
    ImportOptions,
    ImportResult,
    Refused,
)
from aibi.core.schema.refusals import RefusalCode

AT = "2026-01-01T00:00:00Z"
REFUSALS = "aibi.packs.onco.refusals"
NAME_SENTINEL = "zQ7sEnTiNeL"
"""Held by some names: it may be shown, as ``data`` only."""
VALUE_SENTINEL = "vAlUeSeNtInEl"
"""Held by values (a meta file's, a data file's): it is shown nowhere."""
IMPORT_BYTES = (1_024, 65_537, 1 << 30)
"""1 KiB, one over a meta file's bound, and a generous limit."""
SHOWN_MOST = 200

HOSTILE: dict[str, bytes] = {
    "ff": b"\xff",
    "overlong": b"a\xc0\xafb",
    "truncated": b"\xe2\x82",
    "utf8_surrogate": b"\xed\xa0\x80",
    "above_max": b"\xf4\x90\x80\x80",
    "stray_continuation": b"a\x80b",
    "fffe": "￾".encode(),
    "fdd0": "﷐".encode(),
    "plane1_ffff": "x\U0001ffff".encode(),
    "plane16_fffe": "\U0010fffe".encode(),
    "sentinel_ff": NAME_SENTINEL.encode() + b"\xff",
    "sentinel_fffe": NAME_SENTINEL.encode() + "￾".encode(),
    "long_ff": b"\xff" * 240,
    "text_with_controls": b"a\x01\x7f\x1fb",
}
"""Names as the bytes the directory holds: all but the last are not Unicode text once read."""
NAMES = list(HOSTILE)
MAF = (
    b"cancer_study_identifier: demo_study\ngenetic_alteration_type: MUTATION_EXTENDED\n"
    b"datatype: MAF\nstable_id: mutations\nshow_profile_in_analysis_tab: true\n"
    b"profile_name: M\nprofile_description: M.\ndata_filename: data_mutations.txt\n"
)
VALUED = b"description: " + VALUE_SENTINEL.encode() + b"\n"

Tree = Mapping[str | bytes, object]
Outcome = tuple[str, ...]
"""``("accepted",)``, or ``("refused", *codes)``."""
Swap = Literal["directory", "symlink", "fifo", "unwatched_fifo", "removed", "replaced"]
Plan = tuple[bytes, Swap]
"""A listed entry's name, and what it becomes after the listing. A ``fifo`` has a writer that
opens it as the reader does; an ``unwatched_fifo`` has none, and a reader that opens it for
reading waits for one."""
SWAPS: tuple[Swap, ...] = ("directory", "symlink", "fifo", "removed", "replaced")


@dataclass(frozen=True)
class Case:
    """A hostile directory: a tree builder (called once per directory built, since a tree holds
    objects a build uses), the top-level directories to make unreadable once built, and the swap
    made after the listing."""

    label: str
    tree: Callable[[], Tree]
    locked: tuple[bytes, ...] = field(default=())
    plan: Plan | None = None


def meta_named(name: bytes, template: str = "meta_{}.txt") -> bytes:
    before, _, after = template.partition("{}")
    return before.encode() + name + after.encode()


def families(base: dict[str, bytes], kinds: Any, name: bytes) -> dict[str, Case]:
    """Every family of hostile directory for one hostile ``name``."""
    study = base["meta_study.txt"]
    sample = base["meta_clinical_sample.txt"]
    named = meta_named(name)

    def with_entries(*entries: tuple[str | bytes, Callable[[], object]]) -> Callable[[], Tree]:
        def built() -> Tree:
            return {**base, **{key: make() for key, make in entries}}

        return built

    def padded(size: int) -> bytes:
        return MAF + b"padding: " + b"x" * size + b"\n"

    cases: dict[str, Case] = {}

    def add(label: str, builder: Callable[[], Tree], **more: Any) -> None:
        cases[label] = Case(label, builder, **more)

    # (a) the name at the top level, in a subdirectory, as the clinical file's, as a meta file's
    add("top_file", with_entries((name, lambda: b"x")))
    add("top_file_valued", with_entries((name, lambda: VALUED)))
    add("top_dir", with_entries((name, lambda: {"x": b"y", name: b"z"})))
    add("top_symlink", with_entries((name, lambda: kinds.symlink("meta_study.txt"))))
    add("top_fifo", with_entries((name, kinds.fifo)))
    add(
        "subdirectory",
        with_entries(
            ("case_lists", lambda: {name: b"x", named: MAF}),
            (b"d_" + name, lambda: {name: b"x", named: study}),
        ),
    )

    def clinical(data_filename: bytes, present: bytes | None) -> Callable[[], Tree]:
        def built() -> Tree:
            files: dict[str | bytes, object] = {**base}
            files["meta_clinical_sample.txt"] = sample + b"data_filename: " + data_filename + b"\n"
            if present is not None:
                del files["data_clinical_sample.txt"]
                files[present] = base["data_clinical_sample.txt"]
            return files

        return built

    add("clinical_named_and_present", clinical(name, name))
    add("clinical_named_only", clinical(name, None))
    add("clinical_present_only", clinical(b"data_clinical_sample.txt", name))
    add("clinical_both", with_entries((name, lambda: base["data_clinical_sample.txt"])))
    for label, content in {
        "valid": MAF,
        "valued": MAF + VALUED,
        "no_colon": b"no colon " + VALUE_SENTINEL.encode() + b"\n",
        "second_study": study,
        "second_sample": sample + VALUED,
        "over_1k": padded(2_000),
        "over_64k": padded(70_000),
        "not_utf8": b"stable_id: " + name + b"\n",
    }.items():
        add(f"meta_{label}", with_entries((named, lambda content=content: content)))
    for template in ("{}_meta", "META_{}", "meta{}", ".meta_{}", "meta_{}.txt~", "x{}meta"):
        entry = meta_named(name, template)
        add(f"meta_template_{template}", with_entries((entry, lambda: MAF)))
    add("meta_dir", with_entries((named, lambda: {"x": b"y"})))
    add("meta_symlink", with_entries((named, lambda: kinds.symlink("meta_study.txt"))))
    add("meta_fifo", with_entries((named, kinds.fifo)))

    # (b) entries of mode 000
    add("mode_meta", with_entries(("meta_study.txt", lambda: kinds.mode(study, 0))))
    add(
        "mode_clinical_meta",
        with_entries(("meta_clinical_sample.txt", lambda: kinds.mode(sample, 0))),
    )
    add("mode_data", with_entries(("data_clinical_sample.txt", lambda: kinds.mode(b"x", 0))))
    add("mode_hostile_meta", with_entries((named, lambda: kinds.mode(MAF, 0))))
    add("mode_hostile_file", with_entries((name, lambda: kinds.mode(b"x", 0))))

    def unreadable_clinical() -> Tree:
        files = dict(clinical(name, name)())
        files[name] = kinds.mode(base["data_clinical_sample.txt"], 0)
        return files

    add("mode_hostile_clinical", unreadable_clinical)
    add("mode_dir", with_entries(("case_lists", lambda: {"x": b"y"})), locked=(b"case_lists",))
    add("mode_dir_hostile", with_entries((name, lambda: {name: b"y"})), locked=(name,))
    add("mode_dir_meta", with_entries((named, lambda: {"x": b"y"})), locked=(named,))

    # (c) a file swapped after the listing
    targets = {
        "meta_study": b"meta_study.txt",
        "meta_sample": b"meta_clinical_sample.txt",
        "data": b"data_clinical_sample.txt",
        "hostile_meta": named,
        "hostile_file": name,
    }
    for target, swapped in targets.items():
        for swap in SWAPS:
            add(
                f"swap_{target}_{swap}",
                with_entries((named, lambda: MAF), (name, lambda: b"x")),
                plan=(swapped, swap),
            )
    return cases


WRITERS: list[tuple[threading.Thread, bytes]] = []
"""The threads that hold a swapped-in FIFO open for writing, and its path."""


def _watch(path: bytes) -> None:
    """Opens the FIFO at ``path`` for writing, in a thread, as another process would: the open
    returns when the reader opens it, so that what the reader does with a FIFO is its own."""

    def hold() -> None:
        with contextlib.suppress(OSError):
            os.close(os.open(path, os.O_WRONLY))

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    WRITERS.append((thread, path))


def swap_after_listing(reader: Confinement, plan: Plan | None) -> None:
    """Stands a listing in for ``reader``'s that, once the real one is made, changes the entry the
    plan names: a file that became a directory, a symlink or a FIFO, or is gone, or is a new
    file. The reader stays the core's own object (its reads and refusals are the core's, as the
    checks attribute a refusal to the reader by the frames it was raised in)."""
    if plan is None:
        return
    listed = reader.files
    name, kind = plan

    def files(directory: ConfinedPath) -> list[DirectoryEntry]:
        entries = listed(directory)
        path = os.path.join(os.fsencode(directory), name)
        if os.path.lexists(path):
            if kind == "replaced":
                # Made under another name and moved over, while the old file still holds its
                # inode: the new file's differs (made after a remove, it would take the freed
                # one, and the reader's identity check would pass).
                made = os.path.join(os.fsencode(directory), b".swapped")
                with open(made, "wb") as file:
                    file.write(b"swapped\n")
                os.replace(made, path)
                return entries
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)
            else:
                os.remove(path)
            if kind == "directory":
                os.mkdir(path)
            elif kind == "symlink":
                os.symlink(b"meta_study.txt", path)
            elif kind in ("fifo", "unwatched_fifo"):
                os.mkfifo(path)
                if kind == "fifo":
                    _watch(path)
        return entries

    object.__setattr__(reader, "files", files)  # a frozen dataclass


class Hung(BaseException):
    """A run that did not end. Not an ``Exception``, and not an ``OSError``: the reader's own
    ``except OSError`` would take it for a file that cannot be opened."""


@pytest.fixture(autouse=True)
def _a_read_that_hangs_fails() -> Iterator[None]:
    """A FIFO swapped in must be refused, not waited for: a test that takes a minute fails."""

    def hung(signum: int, frame: Any) -> None:
        raise Hung("a run did not end")

    before = signal.signal(signal.SIGALRM, hung)
    signal.alarm(120)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)
        for thread, path in WRITERS:  # one the reader never opened: opened here, so that it ends
            if thread.is_alive():
                try:
                    os.close(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
                except OSError:
                    continue
            thread.join(timeout=5)
        WRITERS.clear()


@pytest.fixture
def restore() -> Iterator[list[bytes]]:
    """The directories a test made unreadable, restored after it so that they can be removed."""
    paths: list[bytes] = []
    yield paths
    for path in paths:
        if os.path.lexists(path):
            os.chmod(path, 0o755)


def _shown_ok(message: Sequence[Segment]) -> None:
    """A message shows a name as ``data`` of at most 200 characters of Unicode text, and its text
    is the pack's own: no sentinel, no name."""
    for segment in message:
        if isinstance(segment, DataSegment):
            assert len(segment.data) <= SHOWN_MOST
            assert is_text(segment.data)
        else:
            assert type(segment) is TextSegment
            assert NAME_SENTINEL.lower() not in segment.text.lower()
            assert VALUE_SENTINEL.lower() not in segment.text.lower()
            for name in HOSTILE.values():
                assert os.fsdecode(name) not in segment.text


def snapshot(note: ImportNote) -> tuple[Any, ...]:
    """A note as values, to compare: a list of segments or a tuple of them are the same message."""
    message = [segment.model_dump(mode="json") for segment in note.message]
    return (note.kind, note.subject, message, note.count, tuple(note.rows))


def _said_nothing_of(error: BaseException) -> None:
    """An exception's text, ``repr`` and chain, as formatted, hold no name and no value."""
    chain = "".join(traceback.format_exception(error))
    for found in (str(error), repr(error), repr(error.args), chain):
        assert NAME_SENTINEL.lower() not in found.lower()
        assert VALUE_SENTINEL.lower() not in found.lower()


class Totality:
    """Builds a case, runs it both ways and checks what must hold of the two."""

    def __init__(self, discovery: Any, make_study: Callable[..., Path], restore: list[bytes]):
        self.discovery = discovery
        self.make_study = make_study
        self.restore = restore
        self.noted: dict[str, list[tuple[Any, ...]]] = {}
        """The notes of each way's last accepted run."""
        self.codes = {f"onco.{code}" for code in importlib.import_module(REFUSALS).CODES}

    def build(self, case: Case) -> Path:
        directory = self.make_study(case.tree())
        for name in case.locked:
            path = os.path.join(os.fsencode(directory), name)
            self.restore.append(path)
            os.chmod(path, 0)
        return directory

    def direct(self, directory: Path, import_bytes: int, plan: Plan | None) -> Outcome:
        reader = self.discovery.reader(directory, most=import_bytes)
        swap_after_listing(reader.inner, plan)
        source = reader.inner.confine(directory)
        limits = ImportLimits(import_bytes=import_bytes)
        failed: Refused | ImportRefused | None = None
        found: Any = None
        try:
            found = self.discovery.study.discover(reader, source, limits)
        except (Refused, ImportRefused) as error:
            failed = error
        if isinstance(failed, Refused):
            assert type(failed) is Refused
            (refusal,) = failed.refusals
            assert str(refusal.code) in self.codes
            assert (refusal.path, refusal.limit, refusal.counts) == (None, None, None)
            _shown_ok(refusal.message)
            _said_nothing_of(failed)
            return ("refused", str(refusal.code))
        if failed is not None:
            assert type(failed) is ImportRefused
            assert any(failed is raised for raised in reader.raised), "not the reader's instance"
            for refusal in failed.refusals:
                _shown_ok(refusal.message)
            _said_nothing_of(failed)
            return ("refused", *(str(refusal.code) for refusal in failed.refusals))
        note: ImportNote
        for note in found.notes:
            _shown_ok(note.message)
        self.noted["direct"] = [snapshot(note) for note in found.notes]
        return ("accepted",)

    def core(self, directory: Path, import_bytes: int, plan: Plan | None) -> Outcome:
        confinement = Confinement.of(directory.parent)
        swap_after_listing(confinement, plan)
        study = self.discovery.study

        class Importer:
            def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
                found = study.discover(options.reader, source, options.limits)
                return ImportResult({}, {}, [], notes=found.notes)

        limits = ImportLimits(import_bytes=import_bytes)
        options = ImportOptions(dataset="study", reader=confinement, limits=limits, at=AT)
        try:
            result = run_importer(Importer(), "onco", confinement.confine(directory), options)
            for note in result.notes:
                _shown_ok(note.message)
            self.noted["core"] = [snapshot(note) for note in result.notes]
        except ImportRefused as error:
            codes = [str(refusal.code) for refusal in error.refusals]
            assert str(RefusalCode.PACK_FAILED) not in codes, codes
            for refusal in error.refusals:
                _shown_ok(refusal.message)
            _said_nothing_of(error)
            return ("refused", *codes)
        return ("accepted",)

    def agree(self, case: Case, import_bytes: int) -> Outcome:
        """The case built twice (a swap changes its directory), run once each way, and the
        outcomes equal."""
        self.noted.clear()
        direct = self.direct(self.build(case), import_bytes, case.plan)
        core = self.core(self.build(case), import_bytes, case.plan)
        assert direct == core, (case.label, import_bytes)
        assert self.noted.get("direct") == self.noted.get("core"), (case.label, import_bytes)
        return direct


@pytest.fixture
def totality(discovery: Any, make_study: Callable[..., Path], restore: list[bytes]) -> Totality:
    return Totality(discovery, make_study, restore)


LABELS = list(
    families(
        {"meta_study.txt": b"", "meta_clinical_sample.txt": b"", "data_clinical_sample.txt": b""},
        SimpleNamespace(fifo=None, symlink=None, mode=None),
        b"x",
    )
)
"""The families, by label."""
READ_TARGETS = ("meta_study", "meta_sample", "hostile_meta")
"""The swap targets ``discover`` reads: a meta file is read, a data file or another entry never."""
GAP = [f"swap_{target}_directory" for target in READ_TARGETS]
"""The cases the core's reader is not total for: a file that became a directory after the listing
is opened (``os.open`` succeeds on a directory) and then ``os.fdopen`` raises ``IsADirectoryError``,
out of ``Confinement.read`` and as the pack's failure through the core's checks; its descriptor is
not closed either (issue #94). Each is ``xfail(strict)``, so that it fails the day the
reader refuses it: remove the marks then."""
CASES = [(name, label) for name in NAMES for label in LABELS]
GAPPED = pytest.mark.xfail(
    strict=True,
    raises=IsADirectoryError,
    reason="Confinement.read on a file that became a directory (issue #94)",
)
ROOT = os.geteuid() == 0
UNREAD = pytest.mark.skipif(ROOT, reason="mode 000 does not bite for root")
"""A mode-000 family: the owner's mode does not stop root from reading, so the case would pass
without holding anything."""


def marks(label: str) -> list[pytest.MarkDecorator]:
    return [
        *([GAPPED] if label in GAP else []),
        *([UNREAD] if label.startswith("mode_") else []),
    ]


PARAMS = [
    pytest.param(name, label, id=f"{name}-{label}", marks=marks(label)) for name, label in CASES
]


@pytest.mark.parametrize(("name", "label"), PARAMS)
def test_discover_is_total_over_hostile_directories(
    name: str, label: str, totality: Totality, base: dict[str, bytes], kinds: Any
) -> None:
    case = families(base, kinds, HOSTILE[name])[label]
    outcomes = {totality.agree(case, import_bytes) for import_bytes in IMPORT_BYTES}
    assert outcomes  # each run agreed with its twin; a limit may change the outcome


def test_the_set_is_the_size_it_is_said_to_be() -> None:
    """The generated set: 14 names in 61 families, each at 3 limits, each run twice."""
    assert (len(NAMES), len(LABELS), len(CASES)) == (14, 61, 14 * 61)
    assert len(GAP) == 3


@pytest.mark.parametrize("target", READ_TARGETS)
def test_a_file_replaced_after_the_listing_is_not_the_one_listed(
    target: str, totality: Totality, base: Any, kinds: Any
) -> None:
    """A meta file that is another file after the listing is refused by the reader's identity
    check, at every limit and both ways (the new file has its own inode: it is made under another
    name and moved over the old one, as a remove then a create would hand the freed inode back)."""
    case = families(base, kinds, HOSTILE["ff"])[f"swap_{target}_replaced"]
    for import_bytes in IMPORT_BYTES:
        assert totality.agree(case, import_bytes) == ("refused", str(RefusalCode.PATH_NOT_CONFINED))


def test_an_outcome_is_not_always_one_thing(totality: Totality, base: Any, kinds: Any) -> None:
    """The set is not vacuous: its runs end in acceptance and in the reader's refusals, in each
    of the pack's refusals of the entry kinds, and the swaps are refused by the reader."""
    seen: dict[str, set[Outcome]] = {}
    for name in ("ff", "fffe", "sentinel_ff"):
        for label, case in families(base, kinds, HOSTILE[name]).items():
            if label in GAP or (ROOT and label.startswith("mode_")):
                continue
            for import_bytes in IMPORT_BYTES:
                seen.setdefault(label, set()).add(totality.agree(case, import_bytes))
    everything = {outcome for outcomes in seen.values() for outcome in outcomes}
    assert ("accepted",) in everything
    assert ("refused", "onco.META_FILE") in everything
    assert ("refused", "onco.DATA_FILE") in everything
    assert ("refused", str(RefusalCode.PATH_NOT_CONFINED)) in everything
    assert ("refused", str(RefusalCode.LIMIT_EXCEEDED)) in everything
    assert ("accepted",) in seen["top_file"]
    assert ("accepted",) not in seen["meta_no_colon"]
    assert ("refused", str(RefusalCode.LIMIT_EXCEEDED)) in seen["meta_over_1k"]


# A generated directory: any bytes that make a name, in any family, with any swap and limit.

file_names = st.binary(min_size=1, max_size=60).filter(
    lambda raw: b"/" not in raw and b"\0" not in raw and raw not in (b".", b"..")
)


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    name=file_names,
    label=st.sampled_from(
        [label for label in LABELS if label not in GAP and not (ROOT and label.startswith("mode_"))]
    ),
    import_bytes=st.sampled_from(IMPORT_BYTES),
)
def test_discover_is_total_over_generated_names(
    name: bytes,
    label: str,
    import_bytes: int,
    totality: Totality,
    base: dict[str, bytes],
    kinds: Any,
) -> None:
    case = families(base, kinds, name)[label]
    totality.agree(case, import_bytes)


@pytest.mark.xfail(
    strict=True,
    raises=Hung,
    reason="Confinement.read opens a FIFO that replaced a file, and waits for a writer (issue #94)",
)
def test_a_file_swapped_for_a_fifo_with_no_writer_is_refused_not_waited_for(
    totality: Totality, base: Any, kinds: Any
) -> None:
    """The core's reader opens without ``O_NONBLOCK``: a listed file that becomes a FIFO blocks
    the import until something opens it for writing. Known, and the core's (not the pack's):
    the case waits 3 seconds and ends as ``Hung``. With a writer, as the ``fifo`` swaps have,
    the reader refuses the file."""
    signal.alarm(3)
    case = families(base, kinds, HOSTILE["ff"])["swap_meta_study_fifo"]
    swapped = Case(case.label, case.tree, plan=(b"meta_study.txt", "unwatched_fifo"))
    totality.direct(totality.build(swapped), IMPORT_BYTES[0], swapped.plan)
