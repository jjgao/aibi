"""The marker scan's child (M4.2a-1b, D438): what of a file is reachable once the calls return.

``test_retention`` runs this file in a fresh interpreter (``python -I retention_scan.py``), so that
a memo of the first call, which an earlier test in the suite's process would have filled, is the
scan's own to fill. It makes six calls through the pack, each with values of its own: two imports
through ``PACK.importer``, two reads through ``clinical.read``, an import refused at its last
patient row (``EXCEL_DATE``) and a read refused at its end (``MISSING_FORM``). Every value of every
call, an id, a cell, a name, a label, a definition, a missing spelling, a meta file's value, a data
file's name, the dataset's id and name, carries that call's marker: random letters, in lower and
upper case (a name is upper case, a dataset id lower), and, for a missing spelling, which may hold
no letter that is not the spelling's own, a random run of ``[``, ``]`` and ``|`` (a spelling
folds them away). After each call its result or refusal is dropped and ``gc.collect()`` runs;
**once, after the sixth call**, the heap is walked from ``gc.get_objects()`` and ``sys.modules``:
each referent (but the ``_asyncio`` module's own, on every interpreter, its dict walked in their
place: see ``_referents``), and **each dict's keys** (a dict with only str keys has its
values visited alone), looking at every ``str``, ``bytes``, ``bytearray`` and ``array('b')`` or
``array('B')`` for a marker. The match is a case-exact substring of one of the marker's forms, and
``slice`` is a leaf (its parts are not visited). The walk's own reads run no code of an object it
walks: a type is told by its identity and by C-level subtype checks, an object is read through its
base type's own methods, and a name is used only when it is an exact ``str``, so a class that
raises on a hash, a comparison, a length, a format or an attribute (the suite keeps some) is
walked. What does run code is not the walk's: the ``gc.collect()`` before it may run a finalizer
or a weakref callback, and the interpreter's C-level traverse of an object (``gc.get_objects``,
``gc.get_referents``) is the interpreter's own (issue #140). A hit's chain names only classes,
functions and modules (a ``__qualname__`` or a module's ``__name__``, when it is an exact
``str``); any other holder is named by its type. The report, one JSON line, says what was
reachable and by which chain.

What it holds: retention that carries a marker, whole or as a substring in one of those types, and
is visible to that single walk after the sixth call. What it does not hold: a value that cannot
carry a marker (a prefix, a digest, a count); state outside the Python heap (a cache of a C
extension; a buffer owned by a library); an object reachable only through the frames of a thread
that is still running; a value a later call releases or resets (the walk is after the sixth, not
after every call); a marker held in an ``array`` of another type code (``'u'``), in a ``slice``, in
an ``io.StringIO`` or in a re-cased form; a call whose values do not all carry a marker. The
mutants that live are listed in D438; issue #136 plans the hardening (a walk after every call,
type-aware reads, an allow-list for module state, a before-and-after snapshot, a check that every
value carries its marker).
"""

import gc
import json
import os
import secrets
import sys
import tempfile
from array import array
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any, NamedTuple

LETTERS = "abcdefghjkmnpqrstuvwxyz"
MARKS = "[]|"
ROWS = 300
"""More rows than any bounded memo (a 128-entry memo is the usual one) holds."""
CALLS = 6
CHAIN = 12
CANARY: list[object] = []
"""Where ``main`` holds its canary (a heap object: a frame's locals are not)."""
LEAVES = frozenset(
    id(kind) for kind in (int, float, bool, type(None), complex, type(...), range, slice)
)
"""The ids of the leaf types: never the types, which a set would hash and compare, and a type's
metaclass may run code for either."""
_TYPE_NAME = type.__dict__["__name__"].__get__
_TYPE_QUALNAME = type.__dict__["__qualname__"].__get__
_FUNCTION_QUALNAME = FunctionType.__dict__["__qualname__"].__get__
_STR_CONTAINS: Callable[[Any, str], bool] = str.__contains__
_BYTES_CONTAINS: Callable[[Any, bytes], bool] = bytes.__contains__
_BYTEARRAY_CONTAINS: Callable[[Any, bytes], bool] = bytearray.__contains__
_ARRAY_TYPECODE: Callable[[Any], str] = array.__dict__["typecode"].__get__
_ARRAY_TOBYTES: Callable[[Any], bytes] = array.tobytes
_DICT_KEYS: Callable[[Any], Iterable[object]] = dict.keys
_DICT_LEN: Callable[[Any], int] = dict.__len__


class Marker(NamedTuple):
    """One call's marker: ``lower`` (a dataset id, a file name, a value), ``upper`` (the same
    letters, as an attribute's name needs), ``marks`` (a missing spelling's)."""

    lower: str
    upper: str
    marks: str

    @staticmethod
    def new() -> "Marker":
        lower = "".join(secrets.choice(LETTERS) for _ in range(16))
        return Marker(lower, lower.upper(), "".join(secrets.choice(MARKS) for _ in range(20)))

    def forms(self) -> tuple[str, str, str]:
        return self.lower, self.upper, self.marks


def fresh(text: str) -> str:
    """A copy of ``text`` that is an object of its own, so that what a call keeps of what it was
    given is never the scan's own string."""
    return (text + "x")[:-1]


class Hit(NamedTuple):
    """A marker found: the call (its index), what holds it, and the chain from a root."""

    call: int
    kind: str
    chain: tuple[str, ...]


def _module_name(module: object) -> object:
    """A module's ``__name__``, found by iterating its dict and comparing the keys that are exactly
    ``str``: a lookup would hash and compare a colliding key of another type."""
    for key, value in dict.items(vars(module)):
        if type(key) is str and str.__eq__(key, "__name__"):
            return value
    return None


def _type_name(kind: type) -> str:
    """A type's name through ``type``'s own descriptor, ``"?"`` where it is not an exact ``str``
    (a name may be set to a ``str`` subclass, whose format or comparison runs code)."""
    name = _TYPE_NAME(kind)
    return name if type(name) is str else "?"


def _describe(item: object) -> str:
    """A name for ``item`` in a chain, read without running its code: its type's name through
    ``type``'s own descriptor, and the name of a class, of a function or of a module through
    theirs, each used when it is an exact ``str``, never through ``getattr`` or ``len`` of the
    object, which a class of the pack may override. Other holders are named by their type."""
    kind = type(item)
    name: object = None
    if issubclass(kind, type):
        name = _TYPE_QUALNAME(item)
    elif kind is FunctionType:
        name = _FUNCTION_QUALNAME(item)
    elif kind is ModuleType:
        name = _module_name(item)
    if type(name) is str:
        return f"{_type_name(kind)} {name}"
    if issubclass(kind, dict):
        return f"dict of {_DICT_LEN(item)}"
    return _type_name(kind)


def _referents(item: object) -> list[object]:
    """What ``item`` refers to, for the walk, but for the ``_asyncio`` module, which is not asked,
    on every interpreter. CPython before 3.12.6 (3.12.3 to 3.12.5 checked; 3.12.6 is fixed) has
    that module's traverse visit its free list of ``FutureIter`` objects: dead ones, with a count
    of 0 and untracked. ``gc.get_referents`` hands them out, and the count falling back to 0 when
    the walk lets go of one frees it a second time, which links the free list into a cycle: the
    next traverse of the module never ends (gdb: ``PyList_Append`` called from the module's
    traverse), or the process faults. Any process that has awaited a future is exposed, as are the
    suite's ``asyncio`` tests (issue #140). On later interpreters asking is harmless but not
    needed, and the walk is the same everywhere. The module's own dict, which holds what it
    exports (``_current_tasks``, ``_scheduled_tasks``), is walked in its place, and that is how
    the current-tasks dict is seen. What the walk skips is the module's own C-level referents
    beyond its dict: its types, its functions, a constant tuple and the free list. None of them
    is state a call of a pack writes a value into, so none can hold a pack's marker."""
    name = _module_name(item) if type(item) is ModuleType else None
    if type(name) is str and str.__eq__(name, "_asyncio"):
        return [vars(item)]
    return gc.get_referents(item)


def reachable(
    markers: Sequence[Marker], ignore: Iterable[object] = (), *, keys: bool = True
) -> list[Hit]:
    """Every ``str``, ``bytes``, ``bytearray`` or ``array`` in the heap that holds a form of one of
    ``markers`` (the index of the marker is the call's), but for the markers' own objects and
    ``ignore`` (the scan's other objects, by identity). ``keys=False`` is the walk that skips a
    dict's keys: a mutant of the scan's own, which its unit test must refuse."""
    modules = sys.modules
    if type(modules) is not dict:
        raise RuntimeError(
            f"sys.modules is a {_type_name(type(modules))}, not a dict: the walk cannot read it "
            "without running its code"
        )
    gc.collect()
    found = [(index, form) for index, marker in enumerate(markers) for form in marker.forms()]
    blobs = [(index, form.encode()) for index, form in found]
    skip = {id(obj) for obj in ignore}
    skip |= {id(obj) for obj in (found, blobs, markers, *markers)}
    skip |= {id(form) for _, form in found} | {id(blob) for _, blob in blobs}
    stack: list[object] = [*gc.get_objects(), *dict.values(modules)]
    parents: dict[int, object] = {}
    seen = set(skip)
    hits: list[Hit] = []
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        kind = type(item)
        if id(kind) in LEAVES:
            continue
        if issubclass(kind, str):
            matched = [i for i, form in found if _STR_CONTAINS(item, form)]
            kind_name = "str"
        elif issubclass(kind, bytes | bytearray):
            contains = _BYTEARRAY_CONTAINS if issubclass(kind, bytearray) else _BYTES_CONTAINS
            matched = [i for i, blob in blobs if contains(item, blob)]
            kind_name = _type_name(kind)
        elif issubclass(kind, array) and _ARRAY_TYPECODE(item) in "bB":
            data = _ARRAY_TOBYTES(item)
            matched = [i for i, blob in blobs if blob in data]
            kind_name = "array"
        else:
            referents = _referents(item)
            for child in referents:
                parents.setdefault(id(child), item)
            stack.extend(referents)
            if keys and issubclass(kind, dict):
                held = list(_DICT_KEYS(item))
                for child in held:
                    parents.setdefault(id(child), item)
                stack.extend(held)
            continue
        for index in matched:
            chain: list[str] = [kind_name]
            link: object | None = item
            while link is not None and len(chain) < CHAIN:
                link = parents.get(id(link))
                if link is not None:
                    chain.append(_describe(link))
            hits.append(Hit(index, kind_name, tuple(chain)))
    return hits


# --- The studies ---------------------------------------------------------------------------------


def _header(names: list[str], types: list[str], marker: Marker) -> str:
    labels = "\t".join(f"Label {marker.upper} {n}" for n in names)
    definitions = "\t".join(f"Definition {marker.lower} of {n}" for n in names)
    rows = [labels, definitions, "\t".join(types), "\t".join("1" for _ in names)]
    return "".join(f"#{row}\n" for row in rows) + "\t".join(names) + "\n"


def _base3(number: int, digits: int) -> str:
    out = []
    for _ in range(digits):
        number, rest = divmod(number, 3)
        out.append(MARKS[rest])
    return "".join(out)


def patient_file(marker: Marker, *, late: str | None = None, overflow: bool = False) -> bytes:
    """A patient file of ``ROWS`` rows, a value of the marker in every column: the key, a STRING,
    a NUMBER (one cell in two is not a number), the OS, DFS and PFS statuses (the last with its
    label), a BOOLEAN and a STRING with missing spellings, and three columns whose names carry
    the marker. ``late`` is the last row's STRING cell; with ``overflow`` every row's missing
    spelling is its own, which a column may not have so many of."""
    lo, up, marks = marker
    extra = [f"C_{up}_{k}" for k in range(3)]
    names = ["PATIENT_ID", "NOTE", "SCORE", "OS_STATUS", "DFS_STATUS", "PFS_STATUS", "PFS_MONTHS"]
    names += ["SMOKER", "MISS", *extra]
    types = ["STRING", "STRING", "NUMBER", "STRING", "STRING", "STRING", "NUMBER", "BOOLEAN"]
    types += ["STRING", "STRING", "STRING", "STRING"]
    out = [_header(names, types, marker)]
    for i in range(ROWS):
        note = late if late is not None and i == ROWS - 1 else f"n{i}{lo}"
        spelling = f"N{marks}{_base3(i, 6)}A" if overflow else f"[N{marks}A]"
        row = [f"P{i}{up}", note, f"{i}.5" if i % 2 else f"Q{up}"]
        row += [("0:LIVING", "1:DECEASED")[i % 2], ("0:DiseaseFree", "1:Recurred")[i % 2]]
        row += [f"{i % 2}:L{up}{i}", f"{i}.75", ("TRUE", "FALSE", f"N{marks}A")[i % 3]]
        row += [spelling if overflow or i % 5 == 0 else f"m{i}{lo}"]
        row += [f"w{k}{i}{lo}" for k in range(3)]
        out.append("\t".join(row) + "\n")
    return "".join(out).encode()


def sample_file(marker: Marker) -> bytes:
    lo, up, _ = marker
    names = ["PATIENT_ID", "SAMPLE_ID", "TYPE", "TMB", f"S_{up}"]
    out = [_header(names, ["STRING", "STRING", "STRING", "NUMBER", "STRING"], marker)]
    out += (f"P{i}{up}\tS{i}{up}\tt{i % 3}{lo}\t{i}.5\ts{i}{lo}\n" for i in range(ROWS))
    return "".join(out).encode()


def study(root: Path, marker: Marker, patient: bytes) -> Path:
    """A study directory whose every meta value, file name and directory name carries the marker."""
    lo, up, _ = marker
    directory = root / f"s{lo}"
    directory.mkdir()
    meta = {
        "meta_study.txt": (
            f"type_of_cancer: mixed\ncancer_study_identifier: m_{lo}\nname: Name {up}\n"
            f"description: Description {lo}\ncitation: Citation {up}\ngroups: Group{up}\n"
            f"short_name: S {lo}\npmid: 12345\nreference_genome: hg19\n"
        ),
        "meta_clinical_patient.txt": (
            f"cancer_study_identifier: m_{lo}\ngenetic_alteration_type: CLINICAL\n"
            f"datatype: PATIENT_ATTRIBUTES\ndata_filename: patient_{lo}.txt\n"
        ),
        "meta_clinical_sample.txt": (
            f"cancer_study_identifier: m_{lo}\ngenetic_alteration_type: CLINICAL\n"
            f"datatype: SAMPLE_ATTRIBUTES\ndata_filename: sample_{lo}.txt\n"
        ),
    }
    for name, text in meta.items():
        (directory / name).write_text(text, encoding="utf-8")
    (directory / f"patient_{lo}.txt").write_bytes(patient)
    (directory / f"sample_{lo}.txt").write_bytes(sample_file(marker))
    return directory


# --- The calls -----------------------------------------------------------------------------------


def run(root: Path, markers: Sequence[Marker]) -> list[str]:
    """The six calls, each an outcome (``ok``, or the refusal's code), and nothing held by this
    function's frame once it returns."""
    import importlib

    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.limits import ImportLimits
    from aibi.core.schema.pack_api import ImportOptions, Refused
    from aibi.packs.onco import clinical

    importer: Any = importlib.import_module("aibi.packs.onco").PACK.importer
    limits = ImportLimits()
    outcomes: list[str] = []

    def importing(marker: Marker, patient: bytes, name: str | None) -> None:
        directory = study(root, marker, patient)
        reader = Confinement.of(directory.parent)
        options = ImportOptions(
            dataset=fresh(marker.lower),
            reader=reader,
            limits=limits,
            at="2026-01-01T00:00:00Z",
            name=name,
        )
        result = importer.import_source(reader.confine(directory), options)
        assert len(result.descriptors) > 10
        outcomes.append("ok")

    def reading(marker: Marker, patient: bytes) -> None:
        found = clinical.read(patient, f"patient_{marker.lower}.txt", "patient", limits)
        clinical.read(
            sample_file(marker),
            f"sample_{marker.lower}.txt",
            "sample",
            limits,
            cells_before=found.cells,
            patient=found,
        )
        outcomes.append("ok")

    calls = [
        lambda m: importing(m, patient_file(m), f"Name {m.upper}"),
        lambda m: importing(m, patient_file(m), None),
        lambda m: reading(m, patient_file(m)),
        lambda m: reading(m, patient_file(m)),
        lambda m: importing(m, patient_file(m, late="Mar-1"), None),
        lambda m: reading(m, patient_file(m, overflow=True)),
    ]
    assert len(calls) == CALLS == len(markers)
    for call, marker in zip(calls, markers, strict=True):
        try:
            call(marker)
        except Refused as error:
            outcomes.append(str(error.refusals[0].code).removeprefix("onco."))
            error.with_traceback(None)
        gc.collect()
    return outcomes


def main() -> None:
    fresh_start = "aibi.packs.onco" not in sys.modules
    markers = [Marker.new() for _ in range(CALLS)]
    canary = Marker.new()
    with tempfile.TemporaryDirectory() as tmp:
        outcomes = run(Path(tmp), markers)
    CANARY.append({fresh(canary.lower) + "k": 1})  # a str-keyed dict holding the canary as a key
    seen = reachable([*markers, canary])
    CANARY.clear()
    report = {
        "pid": os.getpid(),
        "fresh_start": fresh_start,
        "outcomes": outcomes,
        "canary_seen": any(hit.call == CALLS for hit in seen),
        "hits": [hit._asdict() for hit in seen if hit.call != CALLS],
    }
    print(json.dumps(report))


def report_of(output: str) -> Mapping[str, Any]:
    """The report in the child's output (its last line)."""
    found = json.loads(output.strip().splitlines()[-1])
    assert isinstance(found, dict)
    return found


if __name__ == "__main__":
    main()
