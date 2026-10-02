"""The core's checks on what a pack's importer returns, and on what it raises (SPEC §10.1, §14,
D385).

The core's own importers enforce the import's limits as they read (``files``, ``databases``); a
pack's importer reads through the same reader, but builds its tables in its own code, so the core
takes what it returns apart before any validator or the store sees it, and hands on a copy of its
own (``checked``): every container a plain ``dict``, ``list`` or ``tuple``, every value of one of
the exact types it stands for (a ``str`` that is a ``str``, never a subclass whose methods could
say otherwise), read once, so that what is checked is what is built; the copy is immutable
(tuples and read-only mappings), so that no code of the pack's, a validator's included, changes it
between the checks and the store. The copy holds:

- raw snapshots by source name, each name an identifier, as the manifest keeps it: a text file's
  bytes, or a typed source's column names and rows of source values (§12.2), its integers within
  64 bits, as the core's readers give them, its datetimes with a fixed offset or none, and its
  error cells' text no longer than a text file's field;
- layouts on those sources, by table ids that are identifiers, each column an identifier and a
  source name, every source read by one: a source no table reads would be kept as a
  blob that no typed row, and so no erasure's hold check, sees (D223);
- descriptors, read back from their JSON as the store reads them;
- notes of the kinds an importer writes (``skipped_source``, ``renamed`` and ``not_proposed``; the
  others are the core's), each with a subject and segments of Unicode text within a descriptor's
  string and text limits, a count from 0 to ``MAX_SAFE_INTEGER`` and at most 5 row references,
  each from 1; at most ``MAX_QUEUE_ITEMS`` of them, their text at most ``MAX_QUEUE_BYTES`` in
  UTF-8, which bounds the import report.

What does not fit is ``PACK_FAILED`` naming the pack, with a message of the core's that quotes
nothing the pack gave (D303). The import's limits are then counted as the core's importers count
them: its tables (``import_tables``), each table's columns (``table_columns``), the cells of every
table, a typed source's header and rows and a text file's as its table's parse settings read it,
summed (``import_cells``), each text file's bytes (``import_bytes``, a limit per file) and the
UTF-8 bytes of the typed sources' strings, names and error cells' text included
(``decoded_bytes``), each ``LIMIT_EXCEEDED`` naming its limit; and a typed cell over
``FIELD_CHARACTERS`` characters is ``UNPARSEABLE_SOURCE``, as a text file's is.

``run_importer`` calls the importer with a reader that reads at most the operator's
``import_bytes`` and remembers what it refused within the operator's limits, and a copy of its
refusals: such a refusal (a path outside the import directory, a file over ``import_bytes``) refuses
the import as the reader made it, whoever lets it out and whatever was done to it since; a refusal
of a smaller limit the pack chose is the pack's. The checks' own refusals stand only as the
instances this call raised. Both are attributed by origin (``_raised_by_core``): an exception is
the reader's or the checks' only when every frame from where it was caught to where it was raised
is the core's own code, so one that the pack's code raised inside their frames (a ``str``
subclass's method, an interpreter hook it installed) is the pack's. The reader makes its own path
of an exact ``str`` first; types are compared by identity (``_one_of``); the class's methods and
serializers are used, never an instance's; and what a descriptor's serializer reaches of the
pack's runs inside the checks' guard as its failure; the reader keeps JSON copies of its
refusals. Within the reader's record and the checks the core reads a pack-made object's fields
only from a plain instance dictionary of exact ``str`` keys (``_fields``), reads a raised refusal
only once its origin is the core's, and reaches the pack's code only through a descriptor's or a
refusal's class serializer, every exception of which becomes the checks' own fixed failure, so a
native callable of the pack's (``functools.partial``) gains nothing there. A validator is given a
copy of its own (``for_validator``), which shares nothing with what is built but immutable
built-in values. Should building the refusal fail, the core's
constant ``PACK_FAILED`` stands. The limits are taken before the importer runs, which is given a
copy of its own. None of this is a sandbox (D285): a pack that reaches the core's internals by
introspection, whose process-wide hooks change what the core reads without raising, that compiles
code under a core file's name or that runs native code is outside what is checked. A
``Refused`` itself (not a subclass) refuses it with the pack's refusals, read back as refusals,
when there are from 1 to ``MAX_REFUSALS``, each has a
code of the pack's own (``<pack id>.<CODE>``, §8.6), names no limit, points to no path, holds no
counts, and has a message and alternatives within a note's bounds.
``MemoryError`` is ``LIMIT_EXCEEDED`` naming ``import_bytes`` (D225), and ``KeyboardInterrupt`` and
``SystemExit`` pass as new instances, as for a pack's analyses (``analyses.packs``); anything else
the pack raises, ``BaseException`` subclasses and an ``ImportRefused`` of its own making included,
is ``PACK_FAILED``, logged with the pack and a built-in exception's type alone (D285).
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import cast

from pydantic import BaseModel, JsonValue, TypeAdapter

from aibi.core.engine.resolve import pack_failed
from aibi.core.importers.errors import ImportRefused, long_cell_refused, out_of_memory, refused
from aibi.core.schema.descriptors import (
    AnalysisDescriptor,
    ColumnDescriptor,
    ConceptDescriptor,
    CoverageDescriptor,
    DatasetDescriptor,
    Descriptor,
    EndpointDescriptor,
    ModelCardDescriptor,
    ParseSettings,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.ids import MAX_SAFE_INTEGER, is_identifier, is_pack_code
from aibi.core.schema.jsonio import canonical, is_text
from aibi.core.schema.limits import (
    DECODED_BYTES,
    IMPORT_BYTES,
    IMPORT_CELLS,
    IMPORT_TABLES,
    MAX_IDENTIFIER,
    MAX_QUEUE_BYTES,
    MAX_QUEUE_ITEMS,
    MAX_REFUSALS,
    MAX_STRING,
    MAX_SUMMARY_SEGMENTS,
    MAX_TEXT,
    TABLE_COLUMNS,
    ImportLimits,
)
from aibi.core.schema.output import DATA_TOKEN_MAX, DataSegment, Segment, TextSegment, data
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DirectoryEntry,
    Importer,
    ImportNote,
    ImportOptions,
    ImportResult,
    NoteKind,
    Refused,
    SourceReader,
)
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store.build import Layout
from aibi.core.store.sources import (
    FIELD_CHARACTERS,
    ErrorCell,
    RawSource,
    SourceError,
    SourceValue,
    TextSource,
    TooManyCells,
    TypedSource,
    long_cell,
    parse_text,
)

IMPORTER_NOTES: frozenset[str] = frozenset({"skipped_source", "renamed", "not_proposed"})
"""The note kinds an importer writes; the others are the core's (D231, D385)."""
MAX_ROW_REFERENCES = 5
INTEGER_BOUND = 1 << 64
"""A typed integer is within 64 bits, signed or not, as the core's readers give them."""
_DESCRIPTOR_TYPES: tuple[type, ...] = (
    DatasetDescriptor,
    TableDescriptor,
    ColumnDescriptor,
    RelationshipDescriptor,
    CoverageDescriptor,
    EndpointDescriptor,
    ConceptDescriptor,
    ModelCardDescriptor,
    AnalysisDescriptor,
)
_DESCRIPTORS: TypeAdapter[list[Descriptor]] = TypeAdapter(list[Descriptor])
_REFUSAL: TypeAdapter[Refusal] = TypeAdapter(Refusal)
_PASSED: tuple[type[BaseException], ...] = (KeyboardInterrupt, SystemExit)
"""What the importer raises that is no failure of the pack's, compared by identity and raised
again as a new instance; a ``MemoryError`` is ``LIMIT_EXCEEDED`` (``analyses.packs._PASSED``)."""


_PATH: type[Path] = type(Path())
"""The concrete type of a path here (``PosixPath``): a confined path the reader takes is one,
exactly, so that the reader runs no code of the pack's (a subclass's ``__fspath__``)."""


_TRACEBACK = BaseException.__dict__["__traceback__"]
"""``BaseException``'s own ``__traceback__``, which a subclass's property cannot shadow."""


def _raised_by_core(error: BaseException) -> bool:
    """Whether every frame between where ``error`` was caught and where it was raised is the
    core's own code, read from its traceback, which runs no code: an exception attributed to the
    reader or the checks must be one they raised, not one that a pack's code running inside
    their frames raised (a hook of the interpreter's that it installed, a subclass's method).
    Code of the pack's that runs there without raising (a process-wide hook that changes an
    object) is outside what this decides, as introspection is (D285, D385)."""
    trace: object = _TRACEBACK.__get__(error, BaseException)
    while trace is not None:
        frame = cast(TracebackType, trace)
        name = cast(object, frame.tb_frame.f_code.co_filename)
        if type(name) is not str or not name.startswith(_CORE):
            return False
        trace = frame.tb_next
    return True


def _core_directory() -> str:
    named = os.path.dirname(os.path.dirname(_raised_by_core.__code__.co_filename)) + os.sep
    if named.endswith(os.sep + os.path.join("aibi", "core") + os.sep):
        return named
    return "\0"  # no file name holds a NUL, so every exception is then the pack's: fail closed


_CORE: str = _core_directory()
"""The directory of the core's own modules (``aibi/core/``), as the frames of its code name it:
taken from a code object of the core's, since ``__file__`` names a compiled file where only
bytecode is shipped while frames keep the name it was compiled under. A name that does not end
in ``aibi/core/`` (code compiled under a bare relative name) matches no frame, so attribution
fails closed rather than counting every frame as the core's."""


def _own_path(path: object) -> ConfinedPath:
    """A path of the core's own, made from ``path`` before the reader's record begins: ``path``
    must be of the exact path type, and what it is written as must be a ``str`` itself, since a
    path keeps the text it was made from and parses it when first read, which runs the methods of
    a ``str`` subclass (``__len__``, ``split``). Whatever that runs raises here, outside the
    record, so it is the pack's (D385)."""
    if type(path) is not _PATH:
        raise TypeError("the reader takes a confined path")
    written = os.fspath(path)
    if type(written) is not str:
        raise TypeError("the reader takes a confined path")
    return cast(ConfinedPath, _PATH(written))


class _Reader:
    """The reader a pack's importer is given: the import's own, which reads at most the
    operator's ``import_bytes`` whatever limit the pack asks, and remembers each refusal it
    raises within the operator's limits, with a copy of its refusals, in a closure of
    ``run_importer``'s, so that a refusal the pack lets out is known as the reader's and is
    refused as the reader made it. A refusal of a smaller limit the pack chose is the pack's; any
    other refusal of the reader's, such as a path outside the import directory, is the reader's
    whatever the limit. It takes a path of the exact path type alone, written as a ``str``
    itself, and makes its own from it before the record begins (``_own_path``), and a limit is
    an ``int`` or the operator's, and it records only an ``ImportRefused`` itself that the core's
    own code raised (``_raised_by_core``), so that a refusal the pack's code raised inside the
    reader's frames is the pack's. It is no sandbox (D285): a pack that reaches the closure by
    introspection, whose process-wide hooks change what it reads without raising, that compiles
    code under a core file's name or that runs native code is outside what is checked."""

    __slots__ = ("files", "location", "read")

    def __init__(
        self, inner: SourceReader, most: int, record: Callable[[ImportRefused], None]
    ) -> None:
        def call[T](run: Callable[[], T], own: Callable[[ImportRefused], bool]) -> T:
            try:
                return run()
            except ImportRefused as error:
                if type(error) is ImportRefused and _raised_by_core(error) and own(error):
                    record(error)
                raise

        def always(error: ImportRefused) -> bool:
            return True

        def read(path: ConfinedPath, limit: int) -> bytes:
            confined = _own_path(path)
            bound = most if type(limit) is not int else min(limit, most)

            def own(error: ImportRefused) -> bool:
                return bound == most or all(
                    r.code is not RefusalCode.LIMIT_EXCEEDED for r in error.refusals
                )

            return call(lambda: inner.read(confined, bound), own)

        def files(directory: ConfinedPath) -> Sequence[DirectoryEntry]:
            confined = _own_path(directory)
            return call(lambda: inner.files(confined), always)

        def location(path: ConfinedPath) -> str:
            confined = _own_path(path)
            return call(lambda: inner.location(confined), always)

        self.read: Callable[[ConfinedPath, int], bytes] = read
        self.files: Callable[[ConfinedPath], Sequence[DirectoryEntry]] = files
        self.location: Callable[[ConfinedPath], str] = location


class _Failed(Exception):  # noqa: N818 - the check's own word for a result it cannot take
    """What a pack gave does not fit what is checked; ``what`` says how, in the core's words."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


class _Checked(ImportRefused):
    """A refusal of the checks' own."""


def run_importer(
    importer: Importer, pack: str, source: ConfinedPath, options: ImportOptions
) -> ImportResult:
    """What ``pack``'s importer reads from ``source``, checked (``checked``). Raises
    ``ImportRefused``. Only the reader's refusals and the checks' own, as the instances this call
    made, are taken as theirs; the same types raised by the pack's code are its failure."""
    refused_by_reader: list[tuple[ImportRefused, tuple[Refusal, ...]]] = []
    made: list[BaseException] = []
    limits = replace(options.limits)
    fallback = _failed(pack, "failed")
    memory = out_of_memory(IMPORT_BYTES, limits.import_bytes)

    def record(error: ImportRefused) -> None:
        copies = (_REFUSAL.validate_json(r.model_dump_json()) for r in error.refusals)
        refused_by_reader.append((error, tuple(copies)))

    reader = _Reader(options.reader, limits.import_bytes, record)
    given = replace(options, limits=replace(limits), reader=reader)
    passed: type[BaseException] | None = None
    failure: ImportRefused | None = None
    try:
        result = importer.import_source(source, given)
        try:
            return checked(result, limits)
        except (_Checked, _Failed) as error:
            if _raised_by_core(error):
                made.append(error)
            raise
    except BaseException as error:  # every exception of the pack's is contained (module docstring)
        try:
            kind = type(error)
            ours = any(error is found for found in made)
            reader_s = next((copy for raised, copy in refused_by_reader if raised is error), None)
            if ours and kind is _Checked:
                failure = ImportRefused(cast(_Checked, error).refusals)
            elif reader_s is not None:
                failure = ImportRefused(reader_s)
            elif ours and kind is _Failed:
                failure = _failed(pack, cast(_Failed, error).what)
            elif kind is Refused:
                failure = _pack_refused(cast(Refused, error), pack)
            elif kind is MemoryError:
                failure = memory
            else:
                passed = next((one for one in _PASSED if kind is one), None)
                if passed is None:
                    pack_failed(pack, "importer", error)
                    failure = fallback
        except MemoryError:  # as while the checks run
            passed, failure = None, memory
        except BaseException:  # building the refusal is guarded too: the constant one stands
            passed, failure = None, fallback
    if passed is not None:
        raise passed()
    assert failure is not None
    raise failure


def _pack_refused(error: Refused, pack: str) -> ImportRefused:
    """The pack's refusals, read back as refusals; ``PACK_FAILED`` when they are none, not
    refusals, or not of the pack's own codes (``<pack id>.<CODE>``, §8.6): a pack does not refuse
    in the core's words, the reader's limits among them. ``error`` is a ``Refused`` itself, whose
    ``refusals`` is read as every field of a pack-made object is (``_fields``: a plain ``dict`` of
    exact ``str`` keys, so no key's comparison runs), and each refusal is dumped by the class's
    method, never the instance's; what the serializer reaches of the pack's (an object held in a
    field) is guarded and is the pack's failure. A refusal points to no path: an importer's points
    into no document, and a path is unbounded. A ``MemoryError`` is ``LIMIT_EXCEEDED``, as while
    the checks run."""
    wrong = _failed(pack, "refused with what are not refusals of its own")
    try:
        (refusals,) = _fields(error, "refusals")
        found = _sequence(refusals)
        if not found or not all(type(refusal) is Refusal for refusal in found):
            return wrong
        copied = [
            _REFUSAL.validate_json(
                Refusal.__pydantic_serializer__.to_json(refusal, warnings="error")
            )
            for refusal in found
        ]
    except MemoryError:
        raise
    except BaseException:
        return wrong
    if any(r.limit is not None or r.counts is not None or r.path is not None for r in copied):
        return wrong
    if len(copied) > MAX_REFUSALS or not all(
        _bounded(r.message) and _bounded(r.alternatives) for r in copied
    ):
        return _failed(
            pack,
            f"refused with more than {MAX_REFUSALS} refusals, or messages of more than "
            f"{MAX_SUMMARY_SEGMENTS} segments or {MAX_TEXT} characters",
        )
    if not all(is_pack_code(r.code) and r.code.partition(".")[0] == pack for r in copied):
        return wrong
    return ImportRefused(copied)


def _bounded(segments: Sequence[Segment]) -> bool:
    """Whether a refusal's message or alternatives are within a note's bounds."""
    total = sum(len(s.text if isinstance(s, TextSegment) else s.data) for s in segments)
    return len(segments) <= MAX_SUMMARY_SEGMENTS and total <= MAX_TEXT


def checked(result: object, limits: ImportLimits) -> ImportResult:
    """The core's copy of ``result``, if it is an import's result within ``limits`` (module
    docstring). Raises ``ImportRefused`` for a limit, and ``_Failed`` for a result that is no
    import's result, which ``run_importer`` makes ``PACK_FAILED``."""
    if type(result) is not ImportResult:
        raise _Failed("returned what is not an import's result")
    given_descriptors, given_sources, given_layouts, given_notes = _fields(
        result, "descriptors", "sources", "layouts", "notes"
    )
    descriptors = _descriptors(given_descriptors)
    sources = _sources(given_sources)
    layouts = _layouts(given_layouts, sources)
    notes = _notes(given_notes)
    copy = ImportResult(
        sources=MappingProxyType(sources),
        layouts=MappingProxyType(layouts),
        descriptors=tuple(descriptors),
        notes=tuple(notes),
    )
    _limits(copy, limits)
    return copy


def for_validator(result: ImportResult) -> ImportResult:
    """A copy of ``result`` for a validator, which is the pack's code: it shares with ``result``
    nothing but values of immutable built-in types (``str``, ``bytes``, numbers, dates, fixed
    offsets, ``None``) and tuples of them, every object of the core's (a source, an error cell, a
    layout, a descriptor, a note, a segment) made anew, so that nothing a validator does to its
    copy, through ``object.__setattr__`` or an object's ``__dict__`` included, changes what is
    built from ``result`` (D385). ``result`` is a copy of the core's: the checked copy of a
    pack's result, or what the core's own importers read."""
    return ImportResult(
        sources=MappingProxyType(
            {name: _source_copy(source) for name, source in result.sources.items()}
        ),
        layouts=MappingProxyType(
            {
                table: Layout(str(layout.source), tuple((c, n) for c, n in layout.columns))
                for table, layout in result.layouts.items()
            }
        ),
        descriptors=tuple(d.model_copy(deep=True) for d in result.descriptors),
        notes=tuple(
            ImportNote(
                n.kind,
                n.subject,
                tuple(s.model_copy(deep=True) for s in n.message),
                n.count,
                tuple(n.rows),
            )
            for n in result.notes
        ),
    )


def _source_copy(source: RawSource) -> RawSource:
    if isinstance(source, TextSource):
        return TextSource(source.data)
    return TypedSource(
        tuple(source.columns),
        tuple(
            row
            if not any(type(value) is ErrorCell for value in row)
            else tuple(
                ErrorCell(value.text) if isinstance(value, ErrorCell) else value for value in row
            )
            for row in source.rows
        ),
    )


# --- Taking the result apart ---------------------------------------------------------------------


def _one_of(value: object, types: tuple[type, ...]) -> bool:
    """Whether ``value``'s type is one of ``types``, by identity: a membership test would compare
    the types with ``==``, which a pack's metaclass may define, running its code within the
    checks (D385)."""
    kind = type(value)
    return any(kind is one for one in types)


def _fields(given: object, *names: str) -> tuple[object, ...]:
    """The named fields of an object of a core type that the pack built, read from its instance
    dictionary only when that is a plain ``dict`` whose keys are all exact ``str``: a lookup in any
    other would run the dictionary's or a key's code (a comparison) within the checks (D385).
    Iterating a plain ``dict``'s keys, and looking up an exact ``str`` among exact ``str`` keys,
    run none. ``_Failed`` otherwise."""
    held = cast(object, vars(given))
    if type(held) is not dict:
        raise _Failed("returned an object whose fields are not its own")
    fields = cast(dict[object, object], held)
    if not all(type(key) is str for key in fields):
        raise _Failed("returned an object whose fields are not its own")
    found: list[object] = []
    for name in names:
        if name not in fields:
            raise _Failed("returned an object whose fields are not its own")
        found.append(fields[name])
    return tuple(found)


def _sequence(value: object) -> list[object] | None:
    """``value``'s items, if it is a plain list or tuple."""
    if type(value) is list or type(value) is tuple:
        return list(cast(Sequence[object], value))
    return None


def _mapping(value: object) -> list[tuple[object, object]] | None:
    """``value``'s items, if it is a plain dict."""
    if type(value) is dict:
        return list(cast(dict[object, object], value).items())
    return None


def _string(value: object, most: int) -> bool:
    """Whether ``value`` is a ``str`` of Unicode text of at most ``most`` characters."""
    return type(value) is str and len(value) <= most and is_text(value)


def _identifier(value: object) -> bool:
    """Whether ``value`` is a ``str`` that is an identifier, as a table's or column's id is."""
    return _string(value, MAX_IDENTIFIER) and is_identifier(cast(str, value))


def _descriptors(given: object) -> list[Descriptor]:
    """The descriptors, read back from their JSON. Each is dumped by the class's method, never an
    instance's; what the serializer reaches of the pack's (an object held in a field by
    ``model_construct``) runs inside this guard, so whatever it raises is the pack's failure in the
    core's words, and a ``MemoryError`` is ``LIMIT_EXCEEDED`` as while the checks run."""
    found = _sequence(given)
    if found is None or not all(_one_of(item, _DESCRIPTOR_TYPES) for item in found):
        raise _Failed("returned descriptors that are not a list of descriptors")
    try:
        dumped: list[JsonValue] = [
            cast(
                JsonValue,
                type(cast(BaseModel, item)).__pydantic_serializer__.to_python(
                    item, mode="json", warnings="error"
                ),
            )
            for item in found
        ]
        return _DESCRIPTORS.validate_json(canonical(dumped))
    except MemoryError:
        raise
    except Exception:
        raise _Failed("returned descriptors that do not hold as descriptors") from None


def _sources(given: object) -> dict[str, RawSource]:
    items = _mapping(given)
    if items is None:
        raise _Failed("returned sources that are not a mapping")
    copied: dict[str, RawSource] = {}
    for name, source in items:
        if not _identifier(name):
            raise _Failed("returned a source whose name is not an identifier")
        copied[cast(str, name)] = _source(source)
    return copied


def _source(source: object) -> RawSource:
    if type(source) is TextSource:
        (content,) = _fields(source, "data")
        if type(content) is not bytes:
            raise _Failed("returned a text source that is not bytes")
        return TextSource(content)
    if type(source) is not TypedSource:
        raise _Failed("returned a source that is not a raw snapshot")
    given_columns, given_rows = _fields(source, "columns", "rows")
    columns = _sequence(given_columns)
    rows = _sequence(given_rows)
    if (
        columns is None
        or rows is None
        or not columns
        or not all(type(name) is str for name in columns)
    ):
        raise _Failed("returned a typed source that is not names and rows")
    width = len(columns)
    copied: list[tuple[SourceValue, ...]] = []
    for row in rows:
        values = _sequence(row)
        if values is None or len(values) != width:
            raise _Failed("returned a typed source whose rows are not as wide as its names")
        copied.append(tuple(_value(value) for value in values))
    try:
        return TypedSource(tuple(cast(list[str], columns)), tuple(copied))
    except SourceError:
        raise _Failed("returned a typed source whose values are not source values") from None


def _value(value: object) -> SourceValue:
    """A source value of an exact type, or ``_Failed``."""
    kind = type(value)
    if value is None or kind is bool or kind is float or kind is str:
        return cast(SourceValue, value)
    if kind is int:
        if not -INTEGER_BOUND < cast(int, value) < INTEGER_BOUND:
            raise _Failed("returned an integer beyond 64 bits")
        return cast(int, value)
    if kind is date:
        return cast(date, value)
    if kind is datetime:
        zone = cast(datetime, value).tzinfo
        if zone is not None and type(zone) is not timezone:
            raise _Failed("returned a datetime whose zone is not a fixed offset")
        return cast(datetime, value)
    if kind is ErrorCell:
        text_ = cast(object, cast(ErrorCell, value).text)
        if type(text_) is not str or len(text_) > FIELD_CHARACTERS:
            raise _Failed("returned an error cell whose text is not a cell's")
        return ErrorCell(text_)
    raise _Failed("returned a value that is not a source value")


def _layouts(given: object, sources: Mapping[str, RawSource]) -> dict[str, Layout]:
    items = _mapping(given)
    if items is None:
        raise _Failed("returned layouts that are not a mapping")
    copied: dict[str, Layout] = {}
    for table, layout in items:
        if not _identifier(table) or type(layout) is not Layout:
            raise _Failed("returned a layout that is not a table's, by its id")
        source, given_columns = _fields(layout, "source", "columns")
        columns = _sequence(given_columns)
        if type(source) is not str or columns is None:
            raise _Failed("returned a layout that is not a table's")
        pairs: list[tuple[str, str]] = []
        for column in columns:
            pair = _sequence(column)
            if (
                pair is None
                or len(pair) != 2
                or not _identifier(pair[0])
                or type(pair[1]) is not str
            ):
                raise _Failed("returned a layout whose columns are not pairs of an id and a name")
            pairs.append((cast(str, pair[0]), pair[1]))
        if source not in sources:
            raise _Failed("laid out a table on a source it did not return")
        copied[cast(str, table)] = Layout(source, tuple(pairs))
    if {layout.source for layout in copied.values()} != set(sources):
        raise _Failed("returned a source that no table reads")
    return copied


def _notes(given: object) -> list[ImportNote]:
    found = _sequence(given)
    if found is None:
        raise _Failed("returned notes that are not a list")
    if len(found) > MAX_QUEUE_ITEMS:
        raise _Failed(f"returned more than {MAX_QUEUE_ITEMS} notes")
    copied: list[ImportNote] = []
    size = 0
    for note in found:
        copy, more = _note(note)
        size += more
        if size > MAX_QUEUE_BYTES:
            raise _Failed(f"returned notes of more than {MAX_QUEUE_BYTES} bytes of text")
        copied.append(copy)
    return copied


def _note(note: object) -> tuple[ImportNote, int]:
    """The note, copied, and the UTF-8 bytes of its text."""
    if type(note) is not ImportNote:
        raise _Failed("returned a note that is not a note")
    kind, subject, given_message, count, given_rows = _fields(
        note, "kind", "subject", "message", "count", "rows"
    )
    if type(kind) is not str or kind not in IMPORTER_NOTES:
        raise _Failed(
            "returned a note of a kind an importer does not write (it writes "
            + ", ".join(sorted(IMPORTER_NOTES))
            + ")"
        )
    if subject is not None and not _string(subject, MAX_STRING):
        raise _Failed("returned a note whose subject is not a descriptor's string")
    message = _message(given_message)
    if count is not None and not _count(count, 0):
        raise _Failed("returned a note whose count is not a count")
    rows = _sequence(given_rows)
    if rows is None or len(rows) > MAX_ROW_REFERENCES or not all(_count(r, 1) for r in rows):
        raise _Failed(
            f"returned a note whose rows are not at most {MAX_ROW_REFERENCES} row numbers"
        )
    parts = [part.text if isinstance(part, TextSegment) else part.data for part in message]
    if subject is not None:
        parts.append(cast(str, subject))
    copy = ImportNote(
        cast(NoteKind, kind),
        cast(str | None, subject),
        tuple(message),
        cast(int | None, count),
        tuple(cast(list[int], rows)),
    )
    return copy, sum(len(part.encode()) for part in parts)


def _count(value: object, least: int) -> bool:
    return type(value) is int and least <= value <= MAX_SAFE_INTEGER


def _message(given: object) -> list[Segment]:
    segments = _sequence(given)
    wrong = (
        f"returned a note whose message is not at most {MAX_SUMMARY_SEGMENTS} segments of "
        f"Unicode text, of at most {MAX_TEXT} characters"
    )
    if segments is None or len(segments) > MAX_SUMMARY_SEGMENTS:
        raise _Failed(wrong)
    copied: list[Segment] = []
    total = 0
    for segment in segments:
        if type(segment) is TextSegment:
            (value,) = _fields(segment, "text")
            if not _string(value, MAX_TEXT):
                raise _Failed(wrong)
            copied.append(TextSegment(text=cast(str, value)))
        elif type(segment) is DataSegment:
            value, cut = _fields(segment, "data", "truncated")
            if not _string(value, DATA_TOKEN_MAX) or (cut is not None and cut is not True):
                raise _Failed(wrong)
            copied.append(
                DataSegment(data=cast(str, value), truncated=None if cut is None else True)
            )
        else:
            raise _Failed(wrong)
        total += len(cast(str, value))
    if total > MAX_TEXT:
        raise _Failed(wrong)
    return copied


# --- The import's limits -------------------------------------------------------------------------


def _limit(message: str, limit: tuple[str, int], *more: Segment | str) -> _Checked:
    found = refused(RefusalCode.LIMIT_EXCEEDED, message, *more, limit=limit)
    return _Checked(found.refusals)


def _limits(result: ImportResult, limits: ImportLimits) -> None:
    if len(result.layouts) > limits.import_tables:
        raise _limit(
            f"The source has more than {limits.import_tables} tables",
            (IMPORT_TABLES, limits.import_tables),
        )
    decoded = 0
    for name, source in sorted(result.sources.items()):
        if isinstance(source, TextSource):
            if len(source.data) > limits.import_bytes:
                raise _limit(
                    f"A text file of the source has more than {limits.import_bytes} bytes: ",
                    (IMPORT_BYTES, limits.import_bytes),
                    data(name),
                )
            continue
        decoded += _decoded(source)
        if decoded > limits.decoded_bytes:
            raise _limit(
                f"The source's tables decode to more than {limits.decoded_bytes} bytes",
                (DECODED_BYTES, limits.decoded_bytes),
            )
        if (found := long_cell(source.rows)) is not None:
            names = [column if _string(column, MAX_STRING) else "" for column in source.columns]
            refusal = long_cell_refused(found, names, "The source ", data(name))
            raise _Checked(refusal.refusals)
    settings = {
        descriptor.id: descriptor.fields.source.parse
        for descriptor in result.descriptors
        if isinstance(descriptor, TableDescriptor) and descriptor.fields.source is not None
    }
    cells = 0
    for table, layout in sorted(result.layouts.items()):
        if len(layout.columns) > limits.table_columns:
            raise _limit(
                f"A table has more than {limits.table_columns} columns: ",
                (TABLE_COLUMNS, limits.table_columns),
                data(table if _string(table, MAX_STRING) else ""),
            )
        cells += _cells(result.sources[layout.source], settings.get(table), limits, cells)
        if cells > limits.import_cells:
            raise _too_many_cells(limits)


def _cells(
    source: RawSource, settings: ParseSettings | None, limits: ImportLimits, counted: int
) -> int:
    """The cells a table reads of its source, as the core's importers count them: its header's
    columns times its rows, at least one. A text file read with no parse settings, or one that
    cannot be read with its own, is refused by the build, so counts none here."""
    if isinstance(source, TypedSource):
        return len(source.columns) * max(1, len(source.rows))
    if settings is None:
        return 0
    try:
        parsed = parse_text(source.data, settings, max_cells=limits.import_cells - counted)
    except TooManyCells:
        raise _too_many_cells(limits) from None
    except SourceError:
        return 0
    return len(parsed.names) * max(1, len(parsed.rows))


def _too_many_cells(limits: ImportLimits) -> _Checked:
    return _limit(
        f"The source has more than {limits.import_cells} cells",
        (IMPORT_CELLS, limits.import_cells),
    )


def _size(value: str) -> int:
    return len(value.encode("utf-8", "surrogatepass"))


def _decoded(source: TypedSource) -> int:
    """The UTF-8 bytes of a typed source's strings: its names, its string cells and its error
    cells' text."""
    total = sum(_size(name) for name in source.columns)
    for row in source.rows:
        for value in row:
            if isinstance(value, str):
                total += _size(value)
            elif isinstance(value, ErrorCell):
                total += _size(value.text)
    return total


def _failed(pack: str, what: str) -> ImportRefused:
    return refused(RefusalCode.PACK_FAILED, "The importer of the pack ", data(pack), f" {what}")


__all__ = ["IMPORTER_NOTES", "INTEGER_BOUND", "checked", "run_importer"]
