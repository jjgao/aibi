"""Identifiers, hashes and other string forms (SPEC §5.1, §7.6, §8.1).

Patterns are written without look-around, and without ``.``, so that the same expressions work in
Pydantic (Rust regex), in Python's ``re`` and in JSON Schema validators (ECMA-262). Python code
matches them with ``fullmatch``: with ``match``, ``$`` would also accept a trailing newline.

An identifier is at most 64 characters of ``[a-z0-9_]``, starting with a letter, and never holds
``__``, which is reserved for the system (§12.2). The pattern bounds each identifier in a compound
form such as ``<table>.<column>``; ``__`` is refused by a separate check (``NO_DOUBLE_UNDERSCORE``)
and in JSON Schema by ``not: {pattern: "__"}``, because a pattern alone cannot say both without
look-around. An identifier in a compound form that is too long is refused before the pattern is
tried, naming the identifier limit (``IDENTIFIER_PARTS``); the form as a whole is bounded by the
reference limit.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints
from pydantic_core import PydanticCustomError

from aibi.core.schema.limits import (
    IDENTIFIER_CHARACTERS,
    MAX_COLUMNS,
    MAX_IDENTIFIER,
    REFERENCE_CHARACTERS,
    LimitName,
)

IDENT = rf"[a-z][a-z0-9_]{{0,{MAX_IDENTIFIER - 1}}}"
"""One identifier: the pattern bounds its length; ``__`` is refused separately."""
NAME = rf"[A-Za-z_][A-Za-z0-9_]{{0,{MAX_IDENTIFIER - 1}}}"
HEX64 = r"[0-9a-f]{64}"
CODE = rf"[A-Z][A-Z0-9_]{{0,{MAX_IDENTIFIER - 1}}}"
"""A code, in upper snake case, bounded like an identifier; ``PackCode`` refuses ``__`` in it."""
_COLUMNS = rf"{IDENT}(?:\+{IDENT})*"

IDENTIFIER_RE = re.compile(rf"^{IDENT}$")
COLUMN_REF_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
CONCEPT_ID_RE = re.compile(rf"^{IDENT}:{IDENT}(?:\.{IDENT})*$")
RELATIONSHIP_ID_RE = re.compile(rf"^rel:{IDENT}\.{_COLUMNS}$")
COVERAGE_ID_RE = re.compile(rf"^cov:{IDENT}\.{_COLUMNS}$")
ENDPOINT_ID_RE = re.compile(rf"^ep:{IDENT}$")
ANALYSIS_ID_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
MODEL_CARD_ID_RE = re.compile(rf"^model:{IDENT}$")
PACK_LEAF_KIND_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
DATASET_REF_RE = re.compile(rf"^{IDENT}(?:@(?:[1-9][0-9]*|sha256:{HEX64}|draft))?$")
NAME_RE = re.compile(rf"^{NAME}$")
JSON_POINTER_RE = re.compile(r"^(?:/(?:[^~/]|~[01])*)*$")
SHA256_RE = re.compile(rf"^sha256:{HEX64}$")
"""A manifest hash or a digest: ``sha256:`` and lowercase hexadecimal."""
DERIVATION_ID_RE = re.compile(rf"^drv:{HEX64}$")
LEAF_KEY_RE = re.compile(rf"^leaf:{HEX64}$")
ISSUANCE_ID_RE = re.compile(r"^iss:[0-7][0-9A-HJKMNP-TV-Z]{25}$")
"""``iss:`` and a ULID in Crockford's base 32; never hashed."""
DECIMAL_INTEGER_RE = re.compile(r"^(?:0|-?[1-9][0-9]*)$")
PACK_CODE_RE = re.compile(rf"^{IDENT}\.{CODE}$")
"""A pack's refusal or caveat code: ``<pack id>.<CODE>``."""
ANY_CODE_RE = re.compile(rf"^(?:{IDENT}\.)?{CODE}$")
"""A core code (unprefixed) or a pack's."""

MAX_SAFE_INTEGER = 2**53 - 1
"""Integers beyond ±(2^53 - 1) are carried as decimal strings (SPEC §5.1)."""

CORE_ANALYSIS_FAMILIES = frozenset({"summary", "compare", "survival"})
CORE_LEAF_KINDS = frozenset({"value", "exists", "covered", "ids", "cohort"})
ID_PREFIXES = frozenset(
    {"rel", "cov", "ep", "model", "dataset", "sha256", "drv", "leaf", "iss", "stat"}
)
"""The words that begin the core's id forms (SPEC §5.1). A pack taking one as its id would make
its concepts, analyses or codes look like those ids, such as a concept ``rel:t.c``."""
RESERVED_PACK_IDS = CORE_ANALYSIS_FAMILIES | CORE_LEAF_KINDS | ID_PREFIXES | {"core"}
"""Pack ids equal no core analysis family, no core leaf kind, no prefix of the core's id forms,
and not the ``core`` namespace."""

DATASET_DESCRIPTOR_ID = "dataset"
"""The dataset descriptor's id, which no table may take (SPEC §5.1)."""

_LIMIT = LimitName(IDENTIFIER_CHARACTERS)
_REFERENCE_LIMIT = LimitName(REFERENCE_CHARACTERS)
_SEPARATORS = re.compile(r"[.:+]")


def no_double_underscore(value: str) -> str:
    if "__" in value:
        raise PydanticCustomError(
            "reserved_name", "Names containing __ are reserved for the system"
        )
    return value


NO_DOUBLE_UNDERSCORE = AfterValidator(no_double_underscore)
"""Refuses ``__`` in a string made of identifiers."""
NO_DOUBLE_UNDERSCORE_SCHEMA = Field(json_schema_extra={"not": {"pattern": "__"}})

_NOT_CONCEPT_NAMESPACES = RESERVED_PACK_IDS - {"core"}
"""A concept's namespace is ``core`` or a pack id (SPEC §5.1)."""
_NOT_ANALYSIS_FAMILIES = RESERVED_PACK_IDS - CORE_ANALYSIS_FAMILIES
"""An analysis's family is a core family or a pack id (SPEC §5.1)."""


def _namespace(value: str, separator: str) -> str:
    return value.partition(separator)[0]


def concept_namespace(value: str) -> str:
    """A concept's namespace is ``core`` or a pack id; a value without ``:`` names no concept."""
    namespace, colon, _ = value.partition(":")
    if colon and namespace in _NOT_CONCEPT_NAMESPACES:
        raise PydanticCustomError(
            "reserved_namespace", "A concept's namespace is core or a pack id"
        )
    return value


def analysis_family(value: str) -> str:
    if _namespace(value, ".") in _NOT_ANALYSIS_FAMILIES:
        raise PydanticCustomError(
            "reserved_namespace",
            "An analysis's family is {families} or a pack id",
            {"families": ", ".join(sorted(CORE_ANALYSIS_FAMILIES))},
        )
    return value


def pack_namespace(value: str) -> str:
    if _namespace(value, ".") in RESERVED_PACK_IDS:
        raise PydanticCustomError("reserved_namespace", "The namespace is a pack id")
    return value


def code_namespace(value: str) -> str:
    """A code with a namespace is a pack's: the namespace is a pack id."""
    return pack_namespace(value) if "." in value else value


def _refused(*patterns: str) -> Any:
    """JSON Schema: refuse ``__``, and strings matching ``patterns``."""
    return Field(
        json_schema_extra={
            "not": {"anyOf": [{"pattern": "__"}, *({"pattern": p} for p in patterns)]}
        }
    )


def _prefixes(names: frozenset[str], separator: str) -> str:
    return "^(?:" + "|".join(sorted(names)) + ")" + re.escape(separator)


CONCEPT_NAMESPACE_SCHEMA = _prefixes(_NOT_CONCEPT_NAMESPACES, ":")
"""A pattern matching concept ids in a namespace that is neither ``core`` nor a pack id."""


def identifier_parts(value: object) -> object:
    """Refuses a compound form holding an identifier or code longer than one may be.

    The form's pattern would refuse it too, without naming the limit (SPEC §8.6). Only the part
    before an ``@`` is made of identifiers: a pin after it is left to the pattern.
    """
    if isinstance(value, str):
        for part in _SEPARATORS.split(value.partition("@")[0]):
            if len(part) > MAX_IDENTIFIER:
                raise PydanticCustomError(
                    "string_too_long",
                    "An identifier has at most {max_length} characters",
                    {"max_length": MAX_IDENTIFIER, "limit": IDENTIFIER_CHARACTERS},
                )
    return value


IDENTIFIER_PARTS = BeforeValidator(identifier_parts)


def _form(regex: re.Pattern[str], max_length: int) -> StringConstraints:
    return StringConstraints(pattern=regex.pattern, max_length=max_length)


_COMPOUND = 2 * MAX_IDENTIFIER + 1
"""``<identifier>.<identifier>``."""
_KEYED = 4 + MAX_IDENTIFIER + 1 + MAX_COLUMNS * MAX_IDENTIFIER + MAX_COLUMNS - 1
"""``rel:<table>.<column>[+<column>…]`` with ``MAX_COLUMNS`` columns; ``cov:`` is as long."""
_CONCEPT = 4 * MAX_IDENTIFIER
"""A concept id; its dotted parts are not counted, so its length is."""

Identifier = Annotated[
    str,
    _form(IDENTIFIER_RE, MAX_IDENTIFIER),
    _LIMIT,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
DatasetId = Identifier
TableId = Identifier
ColumnId = Identifier
PackId = Identifier
ColumnRef = Annotated[
    str,
    _form(COLUMN_REF_RE, _COMPOUND),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
"""``<table>.<column>``: a column's descriptor id."""
ConceptId = Annotated[
    str,
    _form(CONCEPT_ID_RE, _CONCEPT),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    AfterValidator(concept_namespace),
    _refused(CONCEPT_NAMESPACE_SCHEMA),
]
RelationshipId = Annotated[
    str,
    _form(RELATIONSHIP_ID_RE, _KEYED),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
CoverageId = Annotated[
    str,
    _form(COVERAGE_ID_RE, _KEYED),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
EndpointId = Annotated[
    str,
    _form(ENDPOINT_ID_RE, MAX_IDENTIFIER + 3),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
AnalysisId = Annotated[
    str,
    _form(ANALYSIS_ID_RE, _COMPOUND),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    AfterValidator(analysis_family),
    _refused(_prefixes(_NOT_ANALYSIS_FAMILIES, ".")),
]
ModelCardId = Annotated[
    str,
    _form(MODEL_CARD_ID_RE, MAX_IDENTIFIER + 6),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
DatasetRef = Annotated[
    str,
    _form(DATASET_REF_RE, MAX_IDENTIFIER + 72),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    NO_DOUBLE_UNDERSCORE_SCHEMA,
]
"""A dataset id, optionally pinned: ``x``, ``x@3``, ``x@sha256:<hex>`` or ``x@draft``."""
PackCode = Annotated[
    str,
    _form(PACK_CODE_RE, _COMPOUND),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    AfterValidator(pack_namespace),
    _refused(_prefixes(RESERVED_PACK_IDS, ".")),
]
"""A pack's refusal or caveat code: ``<pack id>.<CODE>``."""
AnyCode = Annotated[
    str,
    _form(ANY_CODE_RE, _COMPOUND),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    AfterValidator(code_namespace),
    _refused(_prefixes(RESERVED_PACK_IDS, ".")),
]
"""A core code, unprefixed, or a pack's."""
PackName = Annotated[
    str,
    _form(PACK_LEAF_KIND_RE, _COMPOUND),
    _REFERENCE_LIMIT,
    IDENTIFIER_PARTS,
    NO_DOUBLE_UNDERSCORE,
    AfterValidator(pack_namespace),
    _refused(_prefixes(RESERVED_PACK_IDS, ".")),
]
"""Something a pack names, ``<pack id>.<name>``: a leaf kind or a requirement predicate."""
Name = Annotated[str, _form(NAME_RE, MAX_IDENTIFIER), _LIMIT]
"""A cohort or parameter name."""
JsonPointer = Annotated[str, StringConstraints(pattern=JSON_POINTER_RE.pattern)]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_RE.pattern)]
DerivationId = Annotated[str, StringConstraints(pattern=DERIVATION_ID_RE.pattern)]
LeafKey = Annotated[str, StringConstraints(pattern=LEAF_KEY_RE.pattern)]
IssuanceId = Annotated[str, StringConstraints(pattern=ISSUANCE_ID_RE.pattern)]


def is_identifier(value: str) -> bool:
    return IDENTIFIER_RE.fullmatch(value) is not None and "__" not in value


def is_pack_id(value: str) -> bool:
    return is_identifier(value) and value not in RESERVED_PACK_IDS


def is_pack_code(value: str) -> bool:
    """Whether a code is a pack's, ``<pack id>.<CODE>``, in a namespace that is a pack id."""
    return (
        PACK_CODE_RE.fullmatch(value) is not None
        and "__" not in value
        and _namespace(value, ".") not in RESERVED_PACK_IDS
    )


def integer_value(value: int) -> int | str:
    """An integer as documents, canonical forms and results carry it (SPEC §5.1)."""
    return value if abs(value) <= MAX_SAFE_INTEGER else str(value)


def normalise(name: str) -> str:
    """Normalise one source name, before prefixes and collisions (SPEC §5.1)."""
    lowered = unicodedata.normalize("NFKC", name).lower()
    return _cut(re.sub(r"[^a-z0-9]+", "_", lowered).strip("_"), MAX_IDENTIFIER)


def _cut(value: str, length: int) -> str:
    """At most ``length`` characters, without a trailing ``_``."""
    return value[:length].rstrip("_")


def normalise_names(
    names: Iterable[str],
    kind: Literal["table", "column"],
    previous: Sequence[tuple[str, str]] | None = None,
) -> list[str]:
    """Derive unique ids from source names, in source order (SPEC §5.1).

    Empty results become ``t_<position>`` or ``c_<position>`` (1-based); results starting with a
    digit get the ``t_`` or ``c_`` prefix. Ids have at most ``MAX_IDENTIFIER`` characters: longer
    results are cut, and a collision suffix replaces final characters when it has to. An id
    already assigned, or the table id ``dataset``, is a collision, resolved by the smallest
    suffix ``_<n>``, n ≥ 2, that gives an unassigned id.

    On re-import, ``previous`` holds the previous release's (original name, id) pairs in source
    order. The k-th occurrence of a name keeps the id of its k-th occurrence there, so duplicate
    and empty names keep their ids too; the other names are assigned around the kept ids.
    """
    names = list(names)
    earlier: dict[str, list[str]] = {}
    for name, assigned in previous or ():
        earlier.setdefault(name, []).append(assigned)
    occurrence: dict[str, int] = {}
    kept: list[str | None] = []
    for name in names:
        index = occurrence.get(name, 0)
        occurrence[name] = index + 1
        ids_of_name = earlier.get(name, [])
        kept.append(ids_of_name[index] if index < len(ids_of_name) else None)

    prefix = "t_" if kind == "table" else "c_"
    taken: set[str] = {DATASET_DESCRIPTOR_ID} if kind == "table" else set()
    taken.update(assigned for assigned in kept if assigned is not None)
    ids: list[str] = []
    for position, (name, assigned) in enumerate(zip(names, kept, strict=True), start=1):
        if assigned is not None:
            ids.append(assigned)
            continue
        base = normalise(name)
        if not base:
            base = f"{prefix}{position}"
        elif base[0].isdigit():
            base = _cut(prefix + base, MAX_IDENTIFIER)
        candidate = base
        n = 2
        while candidate in taken:
            suffix = f"_{n}"
            candidate = _cut(base, MAX_IDENTIFIER - len(suffix)) + suffix
            n += 1
        taken.add(candidate)
        ids.append(candidate)
    return ids
