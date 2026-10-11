"""The pack API (SPEC §10.1).

A pack is a Python package with a manifest that registers implementations of the extension
points below. The core knows packs only through this API: a ``PackRegistry`` built from ``Pack``
objects, which consults only the packs a dataset or document lists. The core never imports a
pack; the server hands the registry the packs it loads.

The extension points are typed here. The core calls each from the milestone that delivers its
feature (M1–M3); an analysis's inputs (``AnalysisInputs``) are what M3.2d materialises (D342).
Raw snapshots are the store's (``aibi.core.store.sources``); the core rebuilds every typed table
from them (D401).
"""

import hashlib
import math
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal, NewType, Protocol, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    JsonValue,
    StrictInt,
    TypeAdapter,
    ValidationError,
)

from aibi.core.schema.caveats import CORE_SEVERITIES, CaveatCode, Severity
from aibi.core.schema.descriptors import (
    RELEASE_KINDS,
    AnalysisDescriptor,
    ConceptDescriptor,
    Descriptor,
)
from aibi.core.schema.document import Clause, PackKey, PackLeaf
from aibi.core.schema.errors import problem
from aibi.core.schema.guards import PASSED, passed
from aibi.core.schema.ids import (
    CORE_ANALYSIS_FAMILIES,
    MAX_SAFE_INTEGER,
    is_identifier,
    is_pack_code,
)
from aibi.core.schema.jsonio import is_text
from aibi.core.schema.jsonschemas import Checker
from aibi.core.schema.jsonschemas import problems as schema_problems
from aibi.core.schema.limits import (
    MAX_DEPTH,
    RESULT_CHARACTERS,
    RESULT_VALUES,
    TEXT_CHARACTERS,
    ImportLimits,
)
from aibi.core.schema.output import Output, Segment
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import Pep440

if TYPE_CHECKING:
    from aibi.core.store.build import Layout
    from aibi.core.store.sources import RawSource

# --- The manifest ----------------------------------------------------------------------------


_CLAUSE = r"(?:~=|===|==|!=|<=|>=|<|>)[0-9A-Za-z.*+!_-]{1,64}"
SPECIFIER_RE = re.compile(rf"^{_CLAUSE}(?:,{_CLAUSE}){{0,15}}$")
"""A PEP 440 specifier set without spaces or empty clauses, such as ``>=0.1,<0.2``."""
_LOWER_BOUNDS = ("~=", "===", "==", ">=", ">")


def _specifier(value: str) -> str:
    try:
        specifiers = SpecifierSet(value)
    except InvalidSpecifier:
        raise problem(
            "specifier", "Not a PEP 440 version specifier, such as '>=0.1,<0.2'"
        ) from None
    if not any(specifier.operator in _LOWER_BOUNDS for specifier in specifiers):
        raise problem(
            "specifier",
            "requires_core bounds the core versions from below, such as '>=0.1,<0.2'",
        )
    return value


class PackManifest(Output):
    """``results_version`` is bumped whenever a change to the pack could change outputs; it is
    hashed into ids, so an upgrade that changes nothing changes no id (SPEC §7.6, §10.1)."""

    id: PackKey
    version: Pep440
    results_version: Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
    requires_core: Annotated[
        str, Field(max_length=1_200, pattern=SPECIFIER_RE.pattern), AfterValidator(_specifier)
    ]
    """The core versions the pack works with, as a PEP 440 specifier with a lower bound."""


# --- What extension points exchange ----------------------------------------------------------

ConfinedPath = NewType("ConfinedPath", Path)
"""A path the core has confined to the upload area or an import directory (SPEC §14)."""

JsonSchema = Mapping[str, JsonValue]


class ReleaseView(Protocol):
    """What a pack may read of a release: its descriptors, never its data (SPEC §10.1)."""

    @property
    def dataset(self) -> str: ...

    @property
    def manifest(self) -> str:
        """``sha256:<hex>``."""
        ...

    @property
    def label(self) -> int | Literal["draft"]: ...

    @property
    def packs(self) -> Sequence[str]: ...

    @property
    def descriptors(self) -> Mapping[str, Descriptor]:
        """The release's descriptors, by descriptor id."""
        ...


EntryKind = Literal["file", "directory", "symlink", "other", "unconfined"]


@dataclass(frozen=True)
class DirectoryEntry:
    """An entry directly inside a directory, by its own name, before any symlink is followed."""

    name: str
    kind: EntryKind
    """A regular ``file``, confined; a ``directory``; a ``symlink``, never followed; an ``other``
    kind (a FIFO, a socket, a device); or a regular file that could not be confined
    (``unconfined``), which is moved or swapped while it is listed."""
    path: ConfinedPath | None = None
    """For a ``file``, its confined path."""


class SourceReader(Protocol):
    """The only way any importer, the core's or a pack's, reads a file (SPEC §14, D232).

    Every path it takes or returns is confined to the upload area or an import directory, and
    ``read`` reads the file it confined, whatever replaced it since, or refuses."""

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        """The file's bytes, at most ``limit`` of them, or a ``LIMIT_EXCEEDED`` refusal."""
        ...

    def files(self, directory: ConfinedPath) -> Sequence[DirectoryEntry]:
        """What is directly inside a directory, by name; each regular file confined in its
        turn, and no symlink followed."""
        ...

    def location(self, path: ConfinedPath) -> str:
        """The path relative to the root that holds it, as a dataset's ``source`` records it."""
        ...


@dataclass(frozen=True)
class DatabaseSource:
    """A database snapshot's source, as a validator sees it (SPEC §13.1, §14, D305): the named
    connection, its kind, and where it points, as the dataset's ``source`` records it (host,
    database and schema, or a file's location); never a credential."""

    connection: str
    kind: Literal["postgres", "mysql", "sqlite", "duckdb"]
    location: str


ImportSource = ConfinedPath | DatabaseSource
"""What an import reads: a confined file or directory, or a named connection (D305)."""


@dataclass(frozen=True)
class Previous:
    """The names of the release a re-import starts from, so that the *k*-th occurrence of a name
    keeps its id (SPEC §5.1, §12.3, D238). Every importer, the core's or a pack's, passes them to
    ``normalise_names``."""

    tables: tuple[tuple[str, str], ...]
    """(original name, table id) for each table whose ``/label`` has an inference, the name
    being that inference; ordered by name and, within one name, in the order its ids were
    assigned: the id without a collision suffix first, then by suffix ``_<n>`` as a number."""
    columns: Mapping[str, tuple[tuple[str, str], ...]]
    """(source name, column id) of each table's source columns, by table id, in source order."""


@dataclass(frozen=True)
class ImportOptions:
    """What an operator gives an import (SPEC §11.2, §13.1)."""

    dataset: str
    """The dataset's id, which the operator names."""
    reader: SourceReader
    limits: ImportLimits
    at: str
    """RFC 3339: the ``at`` of every curation entry the import writes."""
    name: str | None = None
    """The dataset's name; the source's file name without its extension when not given."""
    original_name: str | None = None
    """An upload's file name, which its stored path does not keep (D234)."""
    previous: Previous | None = None
    """On a re-import, the previous release's names (D238); ``None`` on a first import."""


NoteKind = Literal[
    "skipped_source",
    "renamed",
    "not_proposed",
    "dropped",
    "unparsed",
    "gap",
    "reimported",
    "reshaped",
]

NOTE_TEXT: Mapping[NoteKind, str] = MappingProxyType(
    {
        "skipped_source": "A source was skipped at import",
        "renamed": "A name was changed to make an id",
        "not_proposed": "The importer proposed nothing here",
        "dropped": "A proposal was dropped at import: it did not hold against the data",
        "unparsed": "Cells whose value does not parse as the column's datatype, which are UNKNOWN",
        "gap": "Rows that the coverage or the endpoint's coding does not account for",
        "reimported": "Changed by a re-import",
        "reshaped": "Source cells a pack's importer reshaped away",
    }
)
"""An import report note's fixed text, by its kind: the message of every note in the public queue
under a disclosure setting, whose own words may quote counts (D277), and of every note of a report
written before D397 (``aibi.import-report/1``), whose words may hold an id as server text."""


@dataclass(frozen=True)
class ImportNote:
    """A line of the import report, for the curation queue (D231); it holds no cell values."""

    kind: NoteKind
    subject: str | None
    """The descriptor id or the source name it is about."""
    message: Sequence[Segment]
    count: int | None = None
    rows: Sequence[int] = ()
    """References to rows (counted from 1), at most 5, where a count has them."""


@dataclass(frozen=True)
class Reshaped:
    """What a pack's importer dropped when it unpivoted a matrix into a long table (SPEC §10.1,
    D401): a cell whose value is in ``absent`` (assessed, nothing there) or that is empty (not
    assessed) gives no row. ``column`` is the value column; ``dropped`` counts the absent cells
    and ``digest`` is ``cell_digest`` of their keys (the table's primary-key values, in the
    key's order); ``empty`` counts the empty cells, which the coverage does not list."""

    column: str
    absent: Sequence[str]
    dropped: int
    digest: str
    empty: int = 0


@dataclass(frozen=True)
class ImportResult:
    """What an importer returns: the raw snapshots by source name, each table's layout on
    them, the descriptors with their proposals, notes for the curation queue, and what a pack's
    importer dropped from each table it unpivoted, by table id.

    A validator is given the core's checked copy of it (``importers.checks``), whose mappings
    are read-only (``MappingProxyType``), which cannot be deep-copied or pickled."""

    sources: Mapping[str, "RawSource"]
    layouts: Mapping[str, "Layout"]
    descriptors: Sequence[Descriptor]
    notes: Sequence[ImportNote] = ()
    reshaped: Mapping[str, Reshaped] = field(default_factory=dict[str, Reshaped])


def cell_digest(cells: Iterable[Sequence[str]]) -> str:
    """The digest of a set of cells, each given by its key's values as text, in the key's order
    (D401), as 64 lower-case hexadecimal digits: each cell is encoded as each of its values'
    UTF-8 length (8 bytes, big-endian) followed by its UTF-8; the encodings, each once, are
    sorted as bytes; and the digest is SHA-256 of each encoding's length (8 bytes, big-endian)
    followed by the encoding, in that order. A commitment to the set, which chosen cells cannot
    match without a SHA-256 collision (a sum of per-cell hashes could be matched by cells found
    in seconds, round 1 of #77's review). The values are a ``category`` or ``string`` key's,
    which the core compares as its key parts do."""
    encoded = sorted(
        {
            b"".join(len(part).to_bytes(8, "big") + part for part in (v.encode() for v in cell))
            for cell in cells
        }
    )
    hashed = hashlib.sha256()
    for item in encoded:
        hashed.update(len(item).to_bytes(8, "big"))
        hashed.update(item)
    return hashed.hexdigest()


@dataclass(frozen=True)
class Proposal:
    """A proposed descriptor change with its rationale, as a curation proposer returns it (SPEC
    §10.1, §12.3, D249); an importer's own proposals are ``proposed`` fields of its descriptors.

    ``pointer`` names a whole curated field (``/label``, ``/definition``, ``/fields/<name>``,
    ``/extensions/<pack>/<name>``), or is ``""`` for a whole descriptor, whose ``value`` is then
    the descriptor without ``version`` and ``curation``. ``remove`` proposes removing the field or
    the descriptor instead, and then ``value`` is ``None``. It enters the queue by
    ``importer:<pack id>@<pack version>``."""

    descriptor: str
    pointer: str
    value: JsonValue = None
    remove: bool = False
    evidence: str | None = None


Cell = bool | int | float | str | None
"""A unit's value of one input column: a boolean, an integer, a double or text (a category's
value, an ordered category's under ``max`` and ``min``), or ``None`` where it is excluded."""


@dataclass(frozen=True)
class InputColumn:
    """One variable of a pack analysis's inputs, a column its view bound to a role (D341, D342)."""

    role: str
    """The ``column`` requirement of the entry it meets."""
    column: str
    """Its column's descriptor id."""
    kind: Literal["column", "aggregate", "question"]
    """A column of the unit or of a row it looks up, an aggregate of rows below the unit, or
    ``some`` or ``every`` of them (§9.2)."""
    function: Literal["count", "max", "min", "mean", "some", "every"] | None
    datatype: str | None
    """What its values are: its column's datatype, ``integer`` for ``count``, ``number`` for
    ``mean`` and ``boolean`` for ``some`` and ``every``."""
    form: JsonValue
    """Its canonical form (§7.6, D325), a copy of its own."""


@dataclass(frozen=True)
class InputEndpoint:
    """One endpoint of a pack analysis's inputs, the endpoint its view bound to an ``endpoint``
    requirement's role (D352)."""

    role: str
    endpoint: str
    """Its descriptor's id."""
    units: str | None
    """The units of its times, its time column's (§5.8)."""
    entry: bool
    """Whether its units enter at their entry column's time; else every unit enters at the
    origin, just before time 0."""


EndpointRow = tuple[float | None, float, bool]
"""A unit's endpoint row (§5.8, D352): its entry (``None`` at the origin), its time and whether
its follow-up ended in an event."""


@dataclass(frozen=True)
class InputPosition:
    """The units of one cohort position (D342): its members, in the order of SPEC §9.3 (their
    unit keys' RFC 8785 serialisation compared as UTF-16 code units), their keys not given."""

    units: int
    values: tuple[tuple[Cell, ...], ...]
    """Per input column, each unit's value, ``None`` where it is excluded."""
    excluded: tuple[tuple[tuple[str, ...], ...], ...]
    """Per input column, each unit's reasons for being excluded (``ExclusionReason`` names,
    sorted), ``()`` where it has a value."""
    endpoints: tuple[tuple[EndpointRow | None, ...], ...] = ()
    """Per input endpoint, each unit's row, ``None`` where it is excluded (D352)."""
    endpoint_excluded: tuple[tuple[tuple[str, ...], ...], ...] = ()
    """Per input endpoint, each unit's reasons for being excluded, ``INVALID_VALUE`` for a row
    §5.8 calls invalid, ``()`` where it has a row."""


@dataclass(frozen=True)
class AnalysisInputs:
    """What the core hands a pack analysis's ``run`` (SPEC §10.1; D342, D352): per cohort
    position in view order, its units with the columns its view bound to the entry's ``column``
    requirements and the rows of the endpoints it bound to its ``endpoint`` requirements,
    materialised by the core; the reference position (0 unless the entry ``uses_reference``);
    whether the view's cohorts share units, in which case no between-cohort value may be
    computed (§7.4; an analysis that assumes independent groups gets them only under
    ``overlap: "allow"``); the view's ``options``, a copy of
    its own; and a seed, which resampling uses and nothing else (§9.3)."""

    analysis: str
    version: str
    columns: tuple[InputColumn, ...]
    positions: tuple[InputPosition, ...]
    reference: int
    overlapping: bool
    options: Mapping[str, JsonValue]
    seed: int
    endpoints: tuple[InputEndpoint, ...] = ()
    """The endpoints its view bound to the entry's ``endpoint`` requirements, whose rows each
    position holds (D352)."""


class Refused(Exception):  # noqa: N818 - "refused" is the spec's word for a hook's refusal
    """Raised by a hook that refuses its input, such as a leaf compiler (SPEC §7.3)."""

    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(refusal.code for refusal in refusals))
        self.refusals = tuple(refusals)


@dataclass(frozen=True)
class TranslationNote:
    """A place where a translation changed meaning or could not be exact (SPEC §7.1)."""

    pointer: str
    """A JSON Pointer into the document being translated."""
    message: Sequence[Segment]


# --- The extension points (SPEC §10.1) --------------------------------------------------------


class Importer(Protocol):
    """Imports a source the operator names (SPEC §13.1). ``import`` is a Python keyword."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult: ...


class Validator(Protocol):
    """A pack's validator (SPEC §10.1). The paths of ``validate_descriptors``'s refusals point
    into the release's descriptors by id, ``/<descriptor id><field pointer>``, and the core
    points them at ``/descriptors/…`` on an import and ``/draft/…`` on a draft change (D246).
    ``validate_source`` is given a database snapshot's source as a ``DatabaseSource`` (D305)."""

    def validate_source(self, source: ImportSource, result: ImportResult) -> Sequence[Refusal]: ...

    def validate_descriptors(self, release: ReleaseView) -> Sequence[Refusal]: ...


class LeafKind(Protocol):
    """A pack leaf kind (SPEC §7.3). ``compile`` is pure and deterministic, reads no data, and
    returns core clauses with no pack, ``ids`` or ``cohort`` leaves, or raises ``Refused``. It
    and ``summary`` get a copy of the leaf, and ``compile`` a view whose ``label`` it may not
    read, since ids do not hash it; anything else they raise refuses the leaf (D285)."""

    @property
    def schema(self) -> JsonSchema:
        """The JSON Schema of the leaf's members."""
        ...

    def compile(
        self, leaf: PackLeaf, release: ReleaseView, pack_version: str
    ) -> Sequence[Clause]: ...

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        """A sentence about the leaf as written, shown as the pack's and kept out of digests."""
        ...


class Translator(Protocol):
    """Translates a document in another format into an aibi document (SPEC §7.1)."""

    def translate(
        self, document: JsonValue
    ) -> tuple[Mapping[str, JsonValue], Sequence[TranslationNote]]: ...


class Analysis(Protocol):
    """A registry entry and its implementation, deterministic as SPEC §9.3 requires. ``run``
    gives the result's ``values``, ``{"positions": [one object per position], "view": {…}}``,
    which the entry's ``returns`` schema checks (D343); the entry's ``params`` schema checks a
    view's ``options`` (D341). Both are checked as extension schemas are when the pack is
    registered."""

    @property
    def entry(self) -> AnalysisDescriptor: ...

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]: ...


OntologyValidator = Callable[[str], bool]
"""Whether a code belongs to the ontology system (SPEC §5.4)."""
Proposer = Callable[[ReleaseView], Sequence[Proposal]]
RequirementPredicate = Callable[[ReleaseView], bool]
"""Cited in an analysis's ``requires`` as ``"<pack id>.<name>"`` (SPEC §9.1)."""
Facet = Callable[[ReleaseView], Mapping[str, Sequence[str]]]
"""Catalogue facets of a release: facet name to values (SPEC §11.1)."""
CaveatRule = Callable[[ReleaseView, Mapping[str, JsonValue]], Sequence[str]]
"""Caveat codes for a canonical cohort or view; static, with no access to data. It reads a copy
of the form and a view whose ``label`` it may not read (D287)."""


@dataclass(frozen=True)
class Pack:
    """A pack's manifest and its implementations of the extension points; each is optional.

    The members that hold the pack's hook objects take no part in the generated ``repr``,
    equality or hash, so that none of them runs a hook's code (D402)."""

    manifest: PackManifest
    concepts: Sequence[ConceptDescriptor] = ()
    """Registered for every deployment the pack is installed in; ids are ``<pack id>:…``."""
    ontology_systems: Mapping[str, OntologyValidator] = field(
        default_factory=dict[str, OntologyValidator], repr=False, compare=False
    )
    extension_schemas: Mapping[str, JsonSchema] = field(default_factory=dict[str, JsonSchema])
    """A JSON Schema for the pack's extension object, by descriptor kind."""
    importer: Importer | None = field(default=None, repr=False, compare=False)
    validator: Validator | None = field(default=None, repr=False, compare=False)
    proposer: Proposer | None = field(default=None, repr=False, compare=False)
    leaf_kinds: Mapping[str, LeafKind] = field(
        default_factory=dict[str, LeafKind], repr=False, compare=False
    )
    """By kind, ``<pack id>.<name>``."""
    translators: Mapping[str, Translator] = field(
        default_factory=dict[str, Translator], repr=False, compare=False
    )
    """By format, ``<pack id>.<name>``."""
    analyses: Sequence[Analysis] = field(default=(), repr=False, compare=False)
    requirement_predicates: Mapping[str, RequirementPredicate] = field(
        default_factory=dict[str, RequirementPredicate], repr=False, compare=False
    )
    """By name; cited as ``<pack id>.<name>``."""
    facet: Facet | None = field(default=None, repr=False, compare=False)
    caveat_codes: Mapping[str, Severity] = field(default_factory=dict[str, Severity])
    """Every code the pack raises, ``<pack id>.<CODE>``, with its severity (SPEC §8.3)."""
    caveat_rule: CaveatRule | None = field(default=None, repr=False, compare=False)
    wording: Mapping[CaveatCode, str] = field(default_factory=dict[CaveatCode, str])
    """A message template per core code, in the pack's words (SPEC §10.1)."""

    @property
    def id(self) -> str:
        return self.manifest.id


class PackError(ValueError):
    """A pack that cannot be registered, with every problem found."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


class UnknownPack(LookupError):  # noqa: N818 - raised like KeyError, for a pack id
    """A pack id that no registered pack has."""


def _namespaced(pack: str, name: str) -> bool:
    prefix, dot, rest = name.partition(".")
    return prefix == pack and dot == "." and is_identifier(rest)


SHOWN_CHARACTERS = 200
"""How much of a pack's name or a schema pointer a registration problem quotes (D402)."""


def _shown(text: str, most: int = SHOWN_CHARACTERS) -> str:
    """``text``, exactly a ``str``, as a registration problem quotes it: a backslash and every
    character outside printable ASCII escaped (``\\uXXXX``, ``\\UXXXXXXXX``), so that no name
    puts a control character or an escape sequence on a terminal, cut to ``most`` characters
    with ``…`` (D402)."""
    written: list[str] = []
    for character in text[:most]:
        code = ord(character)
        if character == "\\":
            written.append("\\\\")
        elif 0x20 <= code < 0x7F:
            written.append(character)
        elif code <= 0xFFFF:
            written.append(f"\\u{code:04x}")
        else:
            written.append(f"\\U{code:08x}")
    return "".join(written) + ("…" if len(text) > most else "")


def _named(value: object) -> str:
    """How a problem names a key a pack gave: quoted (``_shown``) when it is exactly a ``str`` of
    Unicode text, else in the core's words, never formatting the pack's object."""
    if type(value) is str and is_text(value):
        return _shown(value)
    return "(a key that is not text)"


_ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(position: int) -> str:
    suffix = "th" if 10 <= position % 100 <= 20 else _ORDINALS.get(position % 10, "th")
    return f"{position}{suffix}"


class _NotJsonError(ValueError):
    """A schema that is not a JSON value."""


class JsonTooLarge(ValueError):  # noqa: N818 - raised like a limit's refusal
    """What a pack gave that holds more than ``plain_json`` allows: ``name`` the limit, ``most``
    its value (D343)."""

    def __init__(self, name: str, most: int) -> None:
        super().__init__(f"more than {most} ({name})")
        self.name = name
        self.most = most


class _RaisedLimitError(RuntimeError):
    """A ``JsonTooLarge`` that the code being copied raised, which is its failure, not a limit
    the copy passed (D343)."""


@dataclass
class _Allowance:
    """What a copy may still hold: JSON values, and characters of text (keys included), each
    string at most ``text`` of them; counted before each is copied. It records what the copy
    itself raised, so that only that is ever quoted or passed on as the copy's: an exception of
    the same type that is not it was raised by the code being copied (D343, D402)."""

    most_values: int
    most_characters: int
    text: int
    names: tuple[str, str, str]
    values: int = 0
    characters: int = 0
    tripped: JsonTooLarge | None = None
    """The limit this copy passed, raised as it is."""
    refused: _NotJsonError | None = None
    """What this copy found JSON text cannot carry unchanged, raised as it is."""
    reason: str = ""
    """``refused``'s reason, in the core's words."""

    @classmethod
    def unbounded(cls) -> "_Allowance":
        """An allowance of no limit but the recursion's, for a copy that only records."""
        most = sys.maxsize
        return cls(most, most, most, (RESULT_VALUES, RESULT_CHARACTERS, TEXT_CHARACTERS))

    def refuse(self, reason: str) -> _NotJsonError:
        """The exception that refuses what is being copied, for ``reason``, recorded."""
        self.reason = reason
        self.refused = _NotJsonError(reason)
        return self.refused

    def value(self) -> None:
        self.values += 1
        if self.values > self.most_values:
            self.trip(self.names[0], self.most_values)

    def string(self, length: int) -> None:
        if length > self.text:
            self.trip(self.names[2], self.text)
        self.characters += length
        if self.characters > self.most_characters:
            self.trip(self.names[1], self.most_characters)

    def trip(self, name: str, most: int) -> None:
        self.tripped = JsonTooLarge(name, most)
        raise self.tripped


class _Passing(BaseException):
    """A ``PASSED`` type a pack's code raised during registration, which stops it."""

    def __init__(self, kind: type[BaseException]) -> None:
        super().__init__()
        self.kind = kind


class _Reads:
    """The reads of one pack's members at registration, each inside a guard of its own (D402):
    what a read raises is that member's problem, in the core's words, and the pack's other
    members are still read; a ``PASSED`` type stops registration (``_Passing``, the instance
    recorded as ``raised``, so that no other stops it)."""

    def __init__(self, label: str, problems: list[str]) -> None:
        self.label = label
        self.problems = problems
        self.raised: _Passing | None = None

    def __call__[T](
        self,
        member: str,
        read: Callable[[], T],
        copy: _Allowance | None = None,
        where: str | None = None,
    ) -> tuple[bool, T | None]:
        """``read()``, guarded. With ``copy``, the allowance of a JSON copy that ``read`` makes,
        the copy's own refusal (``copy.refused``, by identity) is the problem that ``where`` (by
        default the member) is not a JSON value, quoting the core's reason; any other exception,
        of whatever type, is that the member could not be read."""
        passing: type[BaseException] | None = None
        try:
            return True, read()
        except BaseException as error:  # every exception of the pack's is contained (D402)
            passing = passed(error)
            if passing is None:
                if copy is not None and error is copy.refused:
                    shown = f"its {member}" if where is None else where
                    self.problems.append(
                        f"{self.label}: {shown} is not a JSON value: it holds {copy.reason}"
                    )
                else:
                    self.problems.append(f"{self.label}: its {member} could not be read")
        if passing is not None:
            self.raised = _Passing(passing)
            raise self.raised
        return False, None


def _member[E: Enum](kind: type[E], given: object) -> E | None:
    """The member of ``kind`` that ``given`` is, by identity, or ``None``: an object of the enum's
    exact type that is no member (``str.__new__(kind, ...)``) is none, and nothing of it is read
    (D402)."""
    return next((member for member in kind if member is given), None)


def _sequence(value: object) -> list[object]:
    """What a pack gave as a sequence, read once, by one iteration."""
    return [item for item in cast(Iterable[object], value)]  # noqa: C416 - one __iter__, no __len__


def _items(value: object) -> list[tuple[object, object]]:
    """What a pack gave as a mapping, read once, by one ``items()``."""
    return [(key, member) for key, member in cast(Mapping[object, object], value).items()]


_CONCEPT: TypeAdapter[ConceptDescriptor] = TypeAdapter(ConceptDescriptor)
_ENTRY: TypeAdapter[AnalysisDescriptor] = TypeAdapter(AnalysisDescriptor)


# The built-in types' own methods, which read a container without calling any of its members'.
_DICT_ITEMS = cast(
    Callable[[dict[object, object]], Iterable[tuple[object, object]]],
    dict.items,  # pyright: ignore[reportUnknownMemberType]
)
_LIST_ITEMS = cast(
    Callable[[list[object]], Iterable[object]],
    list.__iter__,  # pyright: ignore[reportUnknownMemberType]
)
_TUPLE_ITEMS = cast(
    Callable[[tuple[object, ...]], Iterable[object]],
    tuple.__iter__,  # pyright: ignore[reportUnknownMemberType]
)


def _lossless(value: object, allowance: _Allowance) -> None:
    """Refuses (``allowance.refuse``) what a model's dump holds that its JSON text would not
    carry unchanged: a number that is not finite, which JSON writes as ``null``, and two keys of
    an object written as the same text, of which JSON keeps the last. It reads the dump's
    containers by the built-in types' own methods, never a key's or a value's."""
    if isinstance(value, dict):
        seen: set[str] = set()
        for key, member in _DICT_ITEMS(cast(dict[object, object], value)):
            if isinstance(key, str):
                text = str.__str__(key)
                if text in seen:
                    raise allowance.refuse("two keys written as the same text")
                seen.add(text)
            _lossless(member, allowance)
    elif isinstance(value, list):
        for item in _LIST_ITEMS(cast(list[object], value)):
            _lossless(item, allowance)
    elif isinstance(value, tuple):
        for item in _TUPLE_ITEMS(cast(tuple[object, ...], value)):
            _lossless(item, allowance)
    elif isinstance(value, float) and not math.isfinite(float.__float__(value)):
        raise allowance.refuse("a number that is not finite")


def _read_back[M: BaseModel](
    adapter: TypeAdapter[M], given: Callable[[], object], allowance: _Allowance
) -> M:
    """A model a pack gave, read by ``given``, as the core keeps it: dumped once by its class's
    serializer, refused where its JSON text would not carry it unchanged (``_lossless``), and
    read back from that text as an exact model of plain values (D402)."""
    dumped = adapter.dump_python(cast(M, given()), mode="python", warnings="error")
    _lossless(dumped, allowance)
    kept = adapter.validate_python(dumped)
    return adapter.validate_json(adapter.dump_json(kept, warnings="error"))


_CORE_CODES: frozenset[str] = frozenset(code.value for code in CaveatCode)


def _manifest_fields(manifest: PackManifest) -> tuple[str, str, int, str]:
    """The manifest's fields, the manifest read once."""
    return manifest.id, manifest.version, manifest.results_version, manifest.requires_core


def _manifest(given: Pack, reads: _Reads) -> PackManifest | None:
    """The pack's manifest rebuilt as an exact ``PackManifest`` from its fields, each read once
    and exactly a ``str`` or an ``int``; a problem names the field alone (D402)."""
    ok, fields = reads("manifest", lambda: _manifest_fields(given.manifest))
    if not ok or fields is None:
        return None
    names = ("id", "version", "results_version", "requires_core")
    kinds: tuple[type, ...] = (str, str, int, str)
    wrong = [n for n, v, k in zip(names, fields, kinds, strict=True) if type(v) is not k]
    if wrong:
        reads.problems.append(
            f"{reads.label}: its manifest's {', '.join(wrong)} is not of its type"
        )
        return None
    try:
        return PackManifest(
            id=fields[0],
            version=fields[1],
            results_version=fields[2],
            requires_core=fields[3],
        )
    except ValidationError as error:
        found = sorted({str(e["loc"][0]) for e in error.errors(include_input=False) if e["loc"]})
        reads.problems.append(f"{reads.label}: its manifest's {', '.join(found)} is not valid")
        return None


def _keys(
    given: list[tuple[object, object]], what: str, reads: _Reads, rule: str = "a name"
) -> list[tuple[str, object]]:
    """The entries of a mapping a pack gave whose keys are exactly ``str`` of Unicode text,
    each key once; a problem for any other (D402)."""
    kept: list[tuple[str, object]] = []
    seen: set[str] = set()
    for key, member in given:
        if type(key) is not str or not is_text(key):
            reads.problems.append(f"{reads.label}: {what} {_named(key)} is not {rule}")
            continue
        if key in seen:
            reads.problems.append(f"{reads.label}: {what} {_shown(key)} is given twice")
            continue
        seen.add(key)
        kept.append((key, member))
    return kept


def _object(given: Callable[[], object], allowance: _Allowance) -> dict[str, JsonValue] | None:
    """A JSON object a pack gave, read by ``given`` and copied as plain JSON (``_plain``), or
    ``None`` when it is no mapping."""
    value = given()
    if not isinstance(value, Mapping):
        return None
    return cast(dict[str, JsonValue], _plain(cast(object, value), allowance))


def _schema(
    given: Callable[[], object], member: str, where: str, reads: _Reads
) -> dict[str, JsonValue] | None:
    """A schema a pack gave, read by ``given`` and copied as plain JSON inside the member's
    guard, which alone runs the pack's code, then checked as an extension schema is (D247,
    D285) by the core's code outside it, so that the core's own failure is never the pack's; or
    ``None`` with its problems."""
    allowance = _Allowance.unbounded()
    unread = f"{reads.label}: {where} is not a JSON object"
    ok, copied = reads(member, lambda: _object(given, allowance), allowance, where)
    if not ok:
        return None
    if copied is None:
        reads.problems.append(unread)
        return None
    return _checked_schema(copied, where, reads)


def _kept_schema(kept: JsonValue, where: str, reads: _Reads) -> dict[str, JsonValue] | None:
    """A schema the core already holds (an entry's ``params`` or ``returns``, read back from
    JSON), copied and checked by the core's code alone, outside any guard: the copy's own
    refusal is a problem, and any other failure is the core's, raised as it is (D402)."""
    allowance = _Allowance.unbounded()
    try:
        copied = _plain(kept, allowance)
    except _NotJsonError as error:
        if error is not allowance.refused:
            raise
        reads.problems.append(
            f"{reads.label}: {where} is not a JSON value: it holds {allowance.reason}"
        )
        return None
    if not isinstance(copied, dict):
        reads.problems.append(f"{reads.label}: {where} is not a JSON object")
        return None
    return _checked_schema(copied, where, reads)


def _checked_schema(
    copied: dict[str, JsonValue], where: str, reads: _Reads
) -> dict[str, JsonValue] | None:
    """A copied schema checked as an extension schema is (D247, D285), by the core's code
    alone; each problem, its pointers included, quoted escaped and cut (``_shown``)."""
    found = schema_problems(copied)
    reads.problems.extend(
        f"{reads.label}: {where} is refused: {_shown(problem, 4 * SHOWN_CHARACTERS)}"
        for problem in found
    )
    return None if found else copied


def _plain(
    value: object,
    allowance: _Allowance,
    depth: int = 0,
    inside: frozenset[int] = frozenset(),
) -> JsonValue:
    """A copy of a JSON value given as mappings and sequences, read-only ones included, made of
    plain dicts and lists. Anything JSON text cannot carry unchanged raises ``_NotJsonError``: a
    key that is not Unicode text, two keys written as the same text, a set, a non-finite number,
    a number beyond ±(2^53 − 1), a value that holds itself, or nesting deeper than JSON text may
    have, each recorded by ``allowance`` (``refuse``); more than it allows raises
    ``JsonTooLarge`` before the value that passes it is copied."""
    allowance.value()
    if isinstance(value, Mapping | list | tuple):
        identity = id(cast(object, value))
        if identity in inside:
            raise allowance.refuse("a value that holds itself")
        if depth >= MAX_DEPTH:
            raise allowance.refuse(f"nesting deeper than {MAX_DEPTH}")
        within = inside | {identity}
        if isinstance(value, Mapping):
            members = cast(Mapping[object, object], value)
            copied: dict[str, JsonValue] = {}
            for key, member in members.items():
                if not isinstance(key, str):
                    raise allowance.refuse("a key that is not Unicode text")
                allowance.string(str.__len__(key))
                text = str.__str__(key)
                if not is_text(text):
                    raise allowance.refuse("a key that is not Unicode text")
                if text in copied:
                    raise allowance.refuse("two keys written as the same text")
                copied[text] = _plain(member, allowance, depth + 1, within)
            return copied
        items = cast(Sequence[object], value)
        return [_plain(item, allowance, depth + 1, within) for item in items]
    # Scalars are read, checked and kept as Python's own types, so that a subclass cannot pass
    # the checks as one value and be written as another.
    if value is None:
        return None
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        allowance.string(str.__len__(value))
        text = str.__str__(value)
        if not is_text(text):
            raise allowance.refuse("text that is not Unicode")
        return text
    number: int | float
    if isinstance(value, int):
        if int.bit_length(value) > MAX_SAFE_INTEGER.bit_length():
            raise allowance.refuse("a number beyond ±(2^53 - 1)")
        number = int.__index__(value)
    elif isinstance(value, float):
        number = float.__float__(value)
        if not math.isfinite(number):
            raise allowance.refuse("a number that is not finite")
    else:
        raise allowance.refuse("a value that is not JSON")
    if abs(number) > MAX_SAFE_INTEGER:
        raise allowance.refuse("a number beyond ±(2^53 - 1)")
    return number


def plain_json(value: object, *, values: int, characters: int, text: int) -> JsonValue:
    """A copy of what a pack's code gave, made of plain dicts and lists, as ``_plain`` makes it,
    at most ``values`` JSON values and ``characters`` characters of text, keys included, each
    string at most ``text``: raises ``JsonTooLarge`` before it copies what passes them, and
    ``ValueError`` for anything JSON text cannot carry unchanged (D343). Reading what the pack
    gave runs the pack's code (a mapping's ``items``, a sequence's ``__iter__``), which the
    caller guards."""
    names = (RESULT_VALUES, RESULT_CHARACTERS, TEXT_CHARACTERS)
    allowance = _Allowance(values, characters, text, names)
    try:
        return _plain(value, allowance)
    except JsonTooLarge as large:
        if large is allowance.tripped:
            raise
        raise _RaisedLimitError("what a pack gave raised a limit of its own") from None


def _copied(value: JsonValue) -> JsonValue:
    """A copy of a JSON value that ``_plain`` made when its pack was registered."""
    if isinstance(value, dict):
        return {key: _copied(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_copied(item) for item in value]
    return value


def _schemas(schemas: Mapping[str, JsonSchema]) -> Mapping[str, JsonSchema]:
    return MappingProxyType(
        {
            kind: cast(dict[str, JsonValue], _copied(cast(JsonValue, schema)))
            for kind, schema in schemas.items()
        }
    )


@dataclass(frozen=True)
class RegisteredAnalysis:
    """An analysis as registered: its entry, read once and read back as an exact model, and the
    pack's implementation, kept by identity and never read by the registry (D402); a caller
    reads and calls ``implementation.run`` inside a guard (``analyses.packs``)."""

    _entry: AnalysisDescriptor
    implementation: Analysis = field(repr=False, compare=False)

    @property
    def entry(self) -> AnalysisDescriptor:
        """A copy of the entry as registered, which nothing can change."""
        return self._entry.model_copy(deep=True)


@dataclass(frozen=True, eq=False)
class _Kept:
    """What the registry keeps of one pack: the pack, the copies of its leaf kinds' schemas, and
    its analyses as registered."""

    pack: Pack | None
    leaf_schemas: dict[str, JsonSchema]
    analyses: list[RegisteredAnalysis]


def _analysis_copy(analysis: RegisteredAnalysis) -> RegisteredAnalysis:
    """An analysis as the registry hands it out: a copy of its entry, so that changing what is
    handed out changes nothing registered, and the same implementation."""
    return RegisteredAnalysis(analysis.entry, analysis.implementation)


def _registered(given: Pack, core: Version, reads: _Reads) -> _Kept:
    """``given`` as the registry keeps it (D402): a ``Pack`` built by keyword from copies of
    the core's (its manifest, concepts, entries, schemas, names and wordings) and the pack's
    hook objects, kept by identity; each member read once inside its own guard. No pack when
    its manifest cannot be read, its other members' problems still reported. Also the copies
    of its leaf kinds' schemas and its analyses as registered, whose implementations are the
    pack's ``analyses``."""
    manifest = _manifest(given, reads)
    if manifest is not None:
        reads.label = _shown(manifest.id)
    name = None if manifest is None else manifest.id
    problems = reads.problems
    label = reads.label
    if manifest is not None and not SpecifierSet(manifest.requires_core, prereleases=True).contains(
        core
    ):
        problems.append(f"{label} requires core {_shown(manifest.requires_core)}, not {core}")

    concepts: list[ConceptDescriptor] = []
    ok, found = reads("concepts", lambda: _sequence(given.concepts))
    for position, concept in enumerate(found or [], 1):
        allowance = _Allowance.unbounded()
        ok, copy = reads(
            f"{_ordinal(position)} concept",
            lambda c=concept, a=allowance: _read_back(_CONCEPT, lambda: c, a),
            allowance,
        )
        if ok and copy is not None:
            concepts.append(copy)
    if name is not None:
        problems.extend(
            f"{label}: concept {_shown(c.id)} is not in its namespace {label}:"
            for c in concepts
            if not c.id.startswith(f"{name}:")
        )
    ids = [c.id for c in concepts]
    problems.extend(
        f"{label}: concept {_shown(c)} is registered twice"
        for c in sorted({c for c in ids if ids.count(c) > 1})
    )

    systems: dict[str, OntologyValidator] = {}
    ok, items = reads("ontology systems", lambda: _items(given.ontology_systems))
    for system, validator in items or []:
        if type(system) is not str or not 0 < len(system) <= 200 or not is_text(system):
            problems.append(
                f"{label}: an ontology system name is empty, too long or not Unicode text"
            )
            continue
        if system in systems:
            problems.append(f"{label}: ontology system {_shown(system)} is given twice")
            continue
        systems[system] = cast(OntologyValidator, validator)

    schemas: dict[str, JsonSchema] = {}
    ok, items = reads("extension schemas", lambda: _items(given.extension_schemas))
    for kind, schema in _keys(items or [], "extension schema for", reads):
        if kind not in RELEASE_KINDS:
            problems.append(
                f"{label}: extension schema for {_shown(kind)}, which is not a release "
                "descriptor kind"
            )
        copied = _schema(
            lambda s=schema: s,
            f"extension schema for {_shown(kind)}",
            f"the extension schema for {_shown(kind)}",
            reads,
        )
        if copied is not None:
            schemas[kind] = copied

    leaf_kinds: dict[str, LeafKind] = {}
    leaf_schemas: dict[str, JsonSchema] = {}
    ok, items = reads("leaf kinds", lambda: _items(given.leaf_kinds))
    rule = "<pack id>.<name>" if name is None else f"{label}.<name>"
    for kind, leaf in _keys(items or [], "leaf kind", reads, rule):
        if name is not None and not _namespaced(name, kind):
            problems.append(f"{label}: leaf kind {_shown(kind)} is not {label}.<name>")
        leaf_kinds[kind] = cast(LeafKind, leaf)
        copied = _schema(
            lambda o=leaf: cast(LeafKind, o).schema,
            f"leaf kind {_shown(kind)}'s schema",
            f"the schema of leaf kind {_shown(kind)}",
            reads,
        )
        if copied is not None:
            leaf_schemas[kind] = copied

    translators: dict[str, Translator] = {}
    ok, items = reads("translators", lambda: _items(given.translators))
    for format, translator in _keys(items or [], "translator format", reads, rule):
        if name is not None and not _namespaced(name, format):
            problems.append(f"{label}: translator format {_shown(format)} is not {label}.<name>")
        translators[format] = cast(Translator, translator)

    analyses: list[RegisteredAnalysis] = []
    ok, found = reads("analyses", lambda: _sequence(given.analyses))
    for position, analysis in enumerate(found or [], 1):
        allowance = _Allowance.unbounded()
        ok, entry = reads(
            f"{_ordinal(position)} analysis's entry",
            lambda a=analysis, w=allowance: _read_back(_ENTRY, lambda: cast(Analysis, a).entry, w),
            allowance,
        )
        if not ok or entry is None:
            continue
        if name is not None and not _namespaced(name, entry.id):
            problems.append(f"{label}: analysis {_shown(entry.id)} is not {label}.<name>")
        for member, kept_schema in (
            ("params", entry.fields.params),
            ("returns", entry.fields.returns),
        ):
            _kept_schema(
                cast(JsonValue, kept_schema),
                f"the {member} schema of analysis {_shown(entry.id)}",
                reads,
            )
        analyses.append(RegisteredAnalysis(entry, cast(Analysis, analysis)))
    analysis_ids = [a.entry.id for a in analyses]
    if len(set(analysis_ids)) != len(analysis_ids):
        problems.append(f"{label}: an analysis id appears twice")

    predicates: dict[str, RequirementPredicate] = {}
    ok, items = reads("requirement predicates", lambda: _items(given.requirement_predicates))
    for predicate, function in items or []:
        if type(predicate) is not str or not is_identifier(predicate):
            problems.append(
                f"{label}: requirement predicate {_named(predicate)} is not an identifier"
            )
            continue
        if predicate in predicates:
            problems.append(f"{label}: requirement predicate {_shown(predicate)} is given twice")
            continue
        predicates[predicate] = cast(RequirementPredicate, function)

    codes: dict[str, Severity] = {}
    code_rule = "<pack id>.<CODE>" if name is None else f"{label}.<CODE>"
    ok, items = reads("caveat codes", lambda: _items(given.caveat_codes))
    for code, severity in items or []:
        if type(code) is not str or not is_text(code):
            problems.append(f"{label}: caveat code {_named(code)} is not {code_rule}")
            continue
        if name is not None and (code.partition(".")[0] != name or not is_pack_code(code)):
            problems.append(f"{label}: caveat code {_shown(code)} is not {label}.<CODE>")
        member = _member(Severity, severity)
        if member is None:
            problems.append(f"{label}: caveat code {_shown(code)} has no severity")
            continue
        if code in codes:
            problems.append(f"{label}: caveat code {_shown(code)} is given twice")
            continue
        codes[code] = member
    for analysis in analyses:
        for cited in analysis.entry.fields.caveats:
            owner, dot, _ = cited.partition(".")
            if (dot and owner == name and cited not in codes) or (
                not dot and cited not in _CORE_CODES
            ):
                problems.append(
                    f"{label}: analysis {_shown(analysis.entry.id)} cites {_shown(cited)}, "
                    "which is not declared"
                )

    wording: dict[CaveatCode, str] = {}
    ok, items = reads("wording", lambda: _items(given.wording))
    for code, template in items or []:
        core_code: CaveatCode | None = None
        if type(code) is CaveatCode:
            core_code = _member(CaveatCode, code)
        elif type(code) is str and code in _CORE_CODES:
            core_code = CaveatCode(code)
        if core_code is None:
            problems.append(f"{label}: wording for {_named(code)}, which is not a core caveat code")
            shown = _named(code)
        else:
            shown = core_code.value
        if type(template) is not str or not template or not is_text(template):
            problems.append(f"{label}: the wording for {shown} is not Unicode text")
            continue
        if core_code is None:
            continue
        if core_code in wording:
            problems.append(f"{label}: the wording for {shown} is given twice")
            continue
        wording[core_code] = template

    ok, importer = reads("importer", lambda: given.importer)
    ok, validator = reads("validator", lambda: given.validator)
    ok, proposer = reads("proposer", lambda: given.proposer)
    ok, facet = reads("facet", lambda: given.facet)
    ok, caveat_rule = reads("caveat rule", lambda: given.caveat_rule)

    if manifest is None:
        return _Kept(None, leaf_schemas, analyses)
    kept = Pack(
        manifest=manifest,
        concepts=tuple(concepts),
        ontology_systems=MappingProxyType(systems),
        extension_schemas=MappingProxyType(schemas),
        importer=importer,
        validator=validator,
        proposer=proposer,
        leaf_kinds=MappingProxyType(leaf_kinds),
        translators=MappingProxyType(translators),
        analyses=tuple(analysis.implementation for analysis in analyses),
        requirement_predicates=MappingProxyType(predicates),
        facet=facet,
        caveat_codes=MappingProxyType(codes),
        caveat_rule=caveat_rule,
        wording=MappingProxyType(wording),
    )
    return _Kept(kept, leaf_schemas, analyses)


def _handed_out(pack: Pack) -> Pack:
    """A registered pack as the registry hands it out, built by keyword: copies of its
    concepts and schemas, so that changing them changes nothing registered (analyses copy their
    entries themselves), and the same hook objects."""
    return Pack(
        manifest=pack.manifest.model_copy(),
        concepts=tuple(concept.model_copy(deep=True) for concept in pack.concepts),
        ontology_systems=pack.ontology_systems,
        extension_schemas=_schemas(pack.extension_schemas),
        importer=pack.importer,
        validator=pack.validator,
        proposer=pack.proposer,
        leaf_kinds=pack.leaf_kinds,
        translators=pack.translators,
        analyses=pack.analyses,
        requirement_predicates=pack.requirement_predicates,
        facet=pack.facet,
        caveat_codes=pack.caveat_codes,
        caveat_rule=pack.caveat_rule,
        wording=pack.wording,
    )


class PackRegistry:
    """The registered packs, and the extension points the core may consult (SPEC §10.1).

    Lookups that take ``packs`` consult only those packs: a dataset's ``packs``, or those a
    document names. The registry is built once, from every pack at once, and does not change.
    """

    def __init__(self, packs: Iterable[Pack], *, core_version: str) -> None:
        try:
            core = Version(core_version)
        except InvalidVersion:
            raise PackError([f"core version {core_version!r} is not a PEP 440 version"]) from None
        problems: list[str] = []
        kept: list[Pack] = []
        leaf_schemas: dict[str, JsonSchema] = {}
        registered: dict[str, list[RegisteredAnalysis]] = {}
        readers: list[_Reads] = []
        passing: type[BaseException] | None = None
        try:
            for position, given in enumerate(list(packs), 1):
                label = f"the {_ordinal(position)} pack given"
                if type(given) is not Pack:
                    problems.append(f"{label} is not a Pack")
                    continue
                readers.append(_Reads(label, problems))
                found = _registered(given, core, readers[-1])
                leaf_schemas.update(found.leaf_schemas)
                if found.pack is not None:
                    kept.append(found.pack)
                    registered.setdefault(found.pack.id, []).extend(found.analyses)
        except _Passing as stop:
            # Only a stop a guard of this registry's raised passes, its type taken by identity.
            ours = any(stop is reads.raised for reads in readers)
            passing = next((kind for kind in PASSED if ours and kind is stop.kind), None)
            if passing is None:
                problems.append("registration was stopped by what is not a guard's")
        if passing is not None:
            raise SystemExit(1) if passing is SystemExit else passing()
        by_id: dict[str, Pack] = {}
        owners: dict[tuple[str, str], str] = {}
        declared = {code for pack in kept for code in pack.caveat_codes}
        for pack in kept:
            label = _shown(pack.id)
            problems.extend(
                f"{label}: analysis {_shown(analysis.entry.id)} cites {_shown(code)}, which no "
                "registered pack declares"
                for analysis in registered.get(pack.id, [])
                for code in analysis.entry.fields.caveats
                if "." in code and code.partition(".")[0] != pack.id and code not in declared
            )
            if pack.id in by_id:
                problems.append(f"pack {label} is registered twice")
            by_id[pack.id] = pack
            claims = [("ontology system", system) for system in pack.ontology_systems]
            claims += [("concept", concept.id) for concept in pack.concepts]
            for claim in claims:
                if claim in owners and owners[claim] != pack.id:
                    problems.append(
                        f"{claim[0]} {_shown(claim[1])} is registered by "
                        f"{_shown(owners[claim])} and {label}"
                    )
                owners.setdefault(claim, pack.id)
        if problems:
            raise PackError(problems)
        packs = kept
        self._packs = MappingProxyType(dict(sorted(by_id.items())))
        self._systems = {system: pack for pack in packs for system in pack.ontology_systems}
        self._analyses = {
            analysis.entry.id: analysis for pack in packs for analysis in registered[pack.id]
        }
        self._leaf_checkers = {kind: Checker(schema) for kind, schema in leaf_schemas.items()}
        """The checker of each leaf kind's schema as registered."""
        self._analysis_checkers = {
            analysis.entry.id: (
                Checker(analysis.entry.fields.params),
                Checker(analysis.entry.fields.returns),
            )
            for analysis in self._analyses.values()
        }
        """The checkers of each analysis's ``params`` and ``returns`` schemas as registered."""

    # --- Packs ---

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def pack(self, pack_id: str) -> Pack:
        """The pack as registered, with copies of its concepts and schemas."""
        return _handed_out(self._registered(pack_id))

    def _registered(self, pack_id: str) -> Pack:
        try:
            return self._packs[pack_id]
        except KeyError:
            raise UnknownPack(pack_id) from None

    def listed(self, packs: Iterable[str]) -> list[Pack]:
        """The listed packs, in pack id order, as ``pack`` hands them out; an unregistered one
        raises ``UnknownPack``."""
        return [_handed_out(pack) for pack in self._listed(packs)]

    def _listed(self, packs: Iterable[str]) -> list[Pack]:
        return [self._registered(pack_id) for pack_id in sorted(set(packs))]

    # --- Extension points consulted for every registered pack ---

    def concepts(self) -> list[ConceptDescriptor]:
        """Copies of every pack's concepts, in id order."""
        return sorted(
            (
                concept.model_copy(deep=True)
                for pack in self._packs.values()
                for concept in pack.concepts
            ),
            key=lambda concept: concept.id,
        )

    def ontology_validator(self, system: str) -> OntologyValidator | None:
        pack = self._systems.get(system)
        return None if pack is None else pack.ontology_systems[system]

    # --- Extension points consulted for the packs listed ---

    def extension_schemas(self, kind: str, packs: Iterable[str]) -> dict[str, JsonSchema]:
        """The schema each listed pack has for descriptors of ``kind``, by pack id."""
        return {
            pack.id: cast(
                dict[str, JsonValue], _copied(cast(JsonValue, pack.extension_schemas[kind]))
            )
            for pack in self._listed(packs)
            if kind in pack.extension_schemas
        }

    def validators(self, packs: Iterable[str]) -> list[Validator]:
        return [pack.validator for pack in self._listed(packs) if pack.validator is not None]

    def proposers(self, packs: Iterable[str]) -> list[Proposer]:
        return [pack.proposer for pack in self._listed(packs) if pack.proposer is not None]

    def facets(self, packs: Iterable[str]) -> list[Facet]:
        return [pack.facet for pack in self._listed(packs) if pack.facet is not None]

    def caveat_rules(self, packs: Iterable[str]) -> list[CaveatRule]:
        return [pack.caveat_rule for pack in self._listed(packs) if pack.caveat_rule is not None]

    def wordings(self, code: CaveatCode, packs: Iterable[str]) -> list[tuple[str, str]]:
        """The listed packs' wordings of a core code, in pack id order (SPEC §10.1)."""
        return [
            (pack.id, pack.wording[code]) for pack in self._listed(packs) if code in pack.wording
        ]

    # --- Extension points of one pack ---

    def importer(self, pack_id: str) -> Importer | None:
        return self._registered(pack_id).importer

    def leaf_kind(self, kind: str) -> LeafKind | None:
        """The leaf kind ``<pack id>.<name>``, from the pack its namespace names: ``None`` if
        that pack has no such kind, and ``UnknownPack`` if no such pack is registered (A3)."""
        return self._registered(kind.partition(".")[0]).leaf_kinds.get(kind)

    def leaf_checker(self, kind: str) -> Checker:
        """The checker of a registered leaf kind's schema, as the kind had it when its pack was
        registered (D285)."""
        return self._leaf_checkers[kind]

    def leaf_kinds(self) -> list[str]:
        """Every registered leaf kind, sorted."""
        return sorted(self._leaf_checkers)

    def translator(self, format: str) -> Translator | None:
        """As ``leaf_kind``, for a document format ``<pack id>.<name>``."""
        return self._registered(format.partition(".")[0]).translators.get(format)

    def analysis(self, analysis_id: str) -> RegisteredAnalysis | None:
        """A pack's analysis, as ``leaf_kind`` finds a kind; a core family's are not packs'
        (M3)."""
        family = analysis_id.partition(".")[0]
        if family not in CORE_ANALYSIS_FAMILIES:
            self._registered(family)
        found = self._analyses.get(analysis_id)
        return None if found is None else _analysis_copy(found)

    def analyses(self) -> list[RegisteredAnalysis]:
        return [
            _analysis_copy(self._analyses[analysis_id]) for analysis_id in sorted(self._analyses)
        ]

    def analysis_checkers(self, analysis_id: str) -> tuple[Checker, Checker]:
        """The checkers of a registered pack analysis's ``params`` and ``returns`` schemas, as
        its entry had them when its pack was registered (D341, D343)."""
        return self._analysis_checkers[analysis_id]

    def requirement_predicate(self, reference: str) -> RequirementPredicate | None:
        """A predicate cited as ``<pack id>.<name>``; as ``leaf_kind`` for an unknown pack."""
        pack_id, _, name = reference.partition(".")
        return self._registered(pack_id).requirement_predicates.get(name)

    def severity(self, code: str) -> Severity | None:
        """The declared severity of a core or pack caveat code: ``None`` for a code no one
        declares, and ``UnknownPack`` for a namespace no registered pack has."""
        if code in CaveatCode:
            return CORE_SEVERITIES[CaveatCode(code)]
        if "." not in code:
            return None
        return self._registered(code.partition(".")[0]).caveat_codes.get(code)


__all__ = [
    "NOTE_TEXT",
    "Analysis",
    "AnalysisInputs",
    "CaveatRule",
    "Cell",
    "ConfinedPath",
    "DatabaseSource",
    "DirectoryEntry",
    "EndpointRow",
    "EntryKind",
    "Facet",
    "ImportNote",
    "ImportOptions",
    "ImportResult",
    "ImportSource",
    "Importer",
    "InputColumn",
    "InputEndpoint",
    "InputPosition",
    "JsonSchema",
    "JsonTooLarge",
    "LeafKind",
    "NoteKind",
    "OntologyValidator",
    "Pack",
    "PackError",
    "PackManifest",
    "PackRegistry",
    "Previous",
    "Proposal",
    "Proposer",
    "Refused",
    "RegisteredAnalysis",
    "ReleaseView",
    "RequirementPredicate",
    "Reshaped",
    "SourceReader",
    "TranslationNote",
    "Translator",
    "UnknownPack",
    "Validator",
    "cell_digest",
    "plain_json",
]
