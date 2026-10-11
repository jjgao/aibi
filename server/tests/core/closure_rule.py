"""The static rule of D399, over ``src/aibi`` (the core and every pack), from the syntax alone.

Threat model: the adversary is data; the server's own code is trusted but fallible. This rule
bans, by a short syntactic list, the routes a mistake takes; it is not a defence against hostile
code in the tree (SPEC D399 lists spellings it does not stop: a ``match`` pattern on ``__dict__``
beyond the identifier, a reused ``deepcopy`` memo, ``inspect.getmembers``, ``copyreg._reduce_ex``,
``__builtins__["vars"]``, and the schema of a ``TypeAdapter`` taken apart).

Server text is closed at its type at run time: ``TextSegment``'s validator admits an exact
instance, or a mapping only in a call whose validation context is ``text()``'s or a listed
boundary's capability (``schema/output.py``), and ``Output`` raises for Pydantic 1's API, which
builds or copies without validating. What the run time cannot see is a route that skips the
validators, so this rule bans them by *name*, whatever the receiver:

(a) the reflective routes: every attribute name of ``BaseModel``, ``Output`` and ``TextSegment``
    is classified, by ``CLASSIFIED``, as harmless or banned, and a test holds the table to a
    run-time brute force (``closure_brute.py``): a name is banned exactly when trying it makes
    server text. A banned name (``__setstate__``, ``model_construct``, ``model_config``,
    ``__pydantic_*__``, ``__dict__``, ``__getattribute__``...) is never read, called or written but
    at a read site of ``READS``, each with its reason; nor is a string that is one (``getattr(x,
    "__dict__")`` is ``x.__dict__``), nor the name of the output module or of the capability. No
    classified name is ever assigned or deleted as an attribute, nor named by ``setattr`` or
    ``delattr``, whose name must be a constant; an attribute of the output module, or of a name
    it exports, is never assigned. ``getattr``, ``getattr_static``, ``attrgetter``,
    ``methodcaller``, ``__getattribute__`` and ``__getattr__`` are called with a constant name
    only (but at a site of ``NAMED``), and ``getattr``, ``setattr``, ``delattr``, ``vars`` and
    ``attrgetter`` are never aliased. ``BaseModel.<attribute>`` (which skips an override) and
    two-argument ``super`` are never written; ``vars(x)`` is ``__dict__``; ``object.__setattr__``
    only sets a declared member of a frozen dataclass in its ``__post_init__``;
    ``copy(update=...)`` (Pydantic 1's copy, which validates nothing) is never called; ``ctypes``
    and ``gc`` are imported only where ``MEMORY`` says; ``exec``, ``eval``, ``compile`` and
    ``__import__`` are never called, nor ``import_module`` but at a site of ``IMPORTS``. A
    ``TextSegment`` is never built by a call (``text()`` makes one), and a call that validates or
    copies is given no ``*`` or ``**``.
    Every identifier the syntax names but a definition's own name (a function's or a class's) and
    the module of a ``from`` import (a keyword argument's, a pattern's ``kwd_attrs``, an alias, a
    parameter, a ``global``'s), is held to the banned and capability names too (``identifiers``),
    so ``case object(__dict__=d)`` is flagged.
(b) the capability: ``admitted_at``, ``unpickled`` and ``Boundary`` appear outside
    ``output.py`` only in a boundary site, and its private names (``_Admitted``...) nowhere else.
    A boundary site is one statement, ``x = C``, ``return C`` or ``C``, outside any
    ``async def``, whose one call ``C`` is ``<name or attribute chain>.{model_validate,
    model_validate_json, validate_python, validate_json}(<names, attributes, subscripts or
    constants>, context=admitted_at(Boundary.MEMBER))`` or ``unpickled(Boundary.MEMBER,
    <name>)``; every other ``context=`` of ``src`` is a dict literal, and the validation context
    is read (``.context``) only where ``CONTEXT_READS`` says, so no validator forwards or keeps a
    capability. ``SITES`` holds each member's sites; the rule's sites must be exactly those.
    ``dumped_in_validation``, which dumps an output without validating its mutable parts again, is
    a capability too: it is called, by its name, only in a function of ``DUMPS_IN_VALIDATION``, each
    with its reason, and is never aliased, imported under another name or spelled as a string.

(c) routes return a ``Response``: a function decorated as a route is annotated to return one,
    and an endpoint given to ``add_api_route`` is made by a function annotated to return one (a
    returned model would be dumped and validated again by FastAPI, which the guard refuses: this
    protects availability, not the invariant).

It is a module of its own: A1's census (``scripts/census_text_sites.py``) imports nothing of aibi
and is retired by M4.0f-A2b; B's escape ban and the census of mappings written as segments share
its helpers (``_ast.py``), and this rule reads Pydantic's names by reflection.
"""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from tests.core import _ast

import aibi
from aibi.core.schema.output import Boundary, Output, TextSegment

SOURCE = Path(aibi.__file__).resolve().parent
"""``src/aibi``: the core and the packs."""
OUTPUT = "core/schema/output.py"

METHODS = frozenset({"model_validate", "model_validate_json", "validate_python", "validate_json"})
"""The calls a boundary site may make: one validation of what it was handed."""
PUBLIC_CAPABILITY = frozenset({"admitted_at", "unpickled", "Boundary"})
"""Names of ``output`` that appear elsewhere only in a boundary site (or an import)."""
DUMP_HELPER = "dumped_in_validation"
"""The name of the helper that dumps an output without validating it again: a capability."""
PRIVATE_CAPABILITY = frozenset({"_Admitted", "_ADMITTED", "_BY_TEXT", "_UNPICKLING", "_Loads"})
"""Names of ``output`` that appear nowhere else."""
OUTPUT_EXPORTS = frozenset({"TextSegment", "Output", "DataSegment"})
"""Classes of ``output`` none of whose attributes is ever assigned, under the names they have."""
OUTPUT_MODULE = "aibi.core.schema.output"
"""The module's name, which a string (``importlib``) would reach it by."""
TEXT_TYPE = "TextSegment"
GUARDED_CALLS = METHODS | {
    "model_validate_strings",
    "model_copy",
    "copy",
    "partial",
    "partialmethod",
    "model_construct",
    "__replace__",
}
"""Calls that validate or copy: given no ``*`` or ``**``, which could carry a ``context`` or an
``update`` the rule does not see."""
NAMING = frozenset(
    {"getattr", "getattr_static", "attrgetter", "methodcaller", "__getattribute__", "__getattr__"}
)
"""Calls that read an attribute by a name they are given."""
DYNAMIC = frozenset({"exec", "eval", "compile", "__import__"})
"""Built-ins that run or import code from a string, which no rule over the syntax could read; and
``import_module`` (``importlib``). Neither is used in ``src``."""
ALIASED = frozenset(
    {"setattr", "delattr", "getattr", "vars", "attrgetter", "getattr_static", "methodcaller"}
)
"""Functions that are only ever called by their name: bound to another, they would escape the
checks of the call."""

HARMLESS = "harmless"
BANNED = "banned"

CLASSIFIED: dict[str, tuple[str, str]] = {
    # Validating entries: the guard sees what each is given.
    "__init__": (HARMLESS, "validates its arguments; on an instance too"),
    "model_validate": (HARMLESS, "validates"),
    "model_validate_json": (HARMLESS, "validates"),
    "model_validate_strings": (HARMLESS, "validates"),
    "model_copy": (HARMLESS, "Output's validates again with update; without, copies as is"),
    "__replace__": (HARMLESS, "calls model_copy with update, which validates"),
    "__copy__": (HARMLESS, "a copy of what was validated"),
    "__deepcopy__": (HARMLESS, "a copy of what was validated"),
    "copy": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "__new__": (HARMLESS, "an instance without members; filling them needs a banned name"),
    "__init_subclass__": (HARMLESS, "TextSegment's raises"),
    "model_rebuild": (HARMLESS, "rebuilds the schema from the class, guard included"),
    "update_forward_refs": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    # Reads and dumps.
    "model_dump": (HARMLESS, "a dump of what was validated"),
    "model_dump_json": (HARMLESS, "a dump of what was validated"),
    "dict": (HARMLESS, "Pydantic 1's dump"),
    "json": (HARMLESS, "Pydantic 1's dump"),
    "schema": (HARMLESS, "Pydantic 1's JSON Schema"),
    "schema_json": (HARMLESS, "Pydantic 1's JSON Schema"),
    "model_json_schema": (HARMLESS, "the JSON Schema"),
    "model_fields": (HARMLESS, "read only; an assignment is banned"),
    "model_computed_fields": (HARMLESS, "read only"),
    "model_fields_set": (HARMLESS, "read only"),
    "model_extra": (HARMLESS, "read only"),
    "model_parametrized_name": (HARMLESS, "a name"),
    "model_post_init": (HARMLESS, "a hook that sets nothing of an output's"),
    "__class__": (HARMLESS, "read only; assigned, it is banned like every classified name"),
    "__module__": (HARMLESS, "a name"),
    "__final__": (HARMLESS, "typing.final's mark"),
    "__doc__": (HARMLESS, "text of the class"),
    "__annotations__": (HARMLESS, "read only"),
    "__abstractmethods__": (HARMLESS, "read only"),
    "_abc_impl": (HARMLESS, "abc's cache"),
    "__slots__": (HARMLESS, "read only"),
    "__weakref__": (HARMLESS, "read only"),
    "__signature__": (HARMLESS, "read only"),
    "__class_getitem__": (HARMLESS, "generics"),
    "__subclasshook__": (HARMLESS, "abc"),
    "__dir__": (HARMLESS, "names"),
    "__format__": (HARMLESS, "formats"),
    "__sizeof__": (HARMLESS, "a size"),
    "__hash__": (HARMLESS, "a hash"),
    "__eq__": (HARMLESS, "compares"),
    "__ne__": (HARMLESS, "compares"),
    "__lt__": (HARMLESS, "compares"),
    "__le__": (HARMLESS, "compares"),
    "__gt__": (HARMLESS, "compares"),
    "__ge__": (HARMLESS, "compares"),
    "__iter__": (HARMLESS, "the members"),
    "__getattr__": (BANNED, "a read by name: __getattr__(instance, '__dict__') is __dict__"),
    "__getattribute__": (
        BANNED,
        "a read by name: __getattribute__(instance, '__dict__') is __dict__",
    ),
    "__repr__": (HARMLESS, "a representation"),
    "__str__": (HARMLESS, "a representation"),
    "__repr_args__": (HARMLESS, "a representation"),
    "__repr_name__": (HARMLESS, "a representation"),
    "__repr_recursion__": (HARMLESS, "a representation"),
    "__repr_str__": (HARMLESS, "a representation"),
    "__rich_repr__": (HARMLESS, "a representation"),
    "__pretty__": (HARMLESS, "a representation"),
    # Unchecked construction and mutation.
    "model_construct": (
        BANNED,
        "builds without validating: a carrier given a mapping for a segment dumps it as text",
    ),
    "construct": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "_copy_and_set_values": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "__setstate__": (
        BANNED,
        "restores members without validating (pickle): Output's, unbound, restores a mapping",
    ),
    "__getstate__": (BANNED, "returns the members themselves, which a write changes"),
    "__reduce__": (BANNED, "returns the state, the members themselves"),
    "__reduce_ex__": (BANNED, "returns the state, the members themselves"),
    "__setattr__": (BANNED, "object.__setattr__ skips frozen; but a frozen dataclass's own"),
    "__delattr__": (HARMLESS, "deletes a member: no text is made"),
    "__dict__": (BANNED, "the members themselves; but at a read site of READS"),
    "model_config": (BANNED, "a write unfreezes or unstricts every output"),
    "_setattr_handler": (
        BANNED,
        "Pydantic's assignment internals: sets a member of a frozen output",
    ),
    "_iter": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "_calculate_keys": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "_get_value": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "__fields_set__": (HARMLESS, "Pydantic 1's read of the names of the members set"),
    "__get_pydantic_core_schema__": (
        HARMLESS,
        "returns a schema that is not used to rebuild; assigned, it is banned",
    ),
    "__get_pydantic_json_schema__": (HARMLESS, "returns a JSON Schema"),
    "__class_vars__": (HARMLESS, "read only"),
    "__private_attributes__": (HARMLESS, "read only"),
    # Pydantic 1's API: Output raises for each, so none is a way in.
    "validate": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "parse_obj": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "parse_raw": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "parse_file": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    "from_orm": (HARMLESS, "closed at the type: Output's raises (Pydantic 1's API)"),
    # Output's and TextSegment's own validators and serialiser, called by Pydantic alone.
    "_null_is_absent": (HARMLESS, "Output's validator: it returns what it is given"),
    "_text": (HARMLESS, "Output's validator: it returns what it is given"),
    "_omit_absent": (HARMLESS, "Output's serialiser: it dumps"),
    "_made_here": (
        HARMLESS,
        "TextSegment's guard: called by hand it returns what its handler returns",
    ),
}
"""Every attribute name of ``BaseModel``, ``Output`` and ``TextSegment`` but ``__pydantic_*``
(all banned), each harmless or banned, with why. A name is banned exactly when the run-time brute
force (``closure_brute.py``) makes server text with it, and harmless when it does not: a test holds
the table to that outcome, so a name the type closes (Output raises for Pydantic 1's API) is
harmless, and one a classification gets wrong fails."""

READS: dict[tuple[str, str, str], str] = {
    ("core/catalog/index.py", "_asserted_concept", "__dict__"): (
        "reads a descriptor's fields member, whatever its kind"
    ),
    ("core/importers/worker.py", "_decoded", "__dict__"): (
        "counts the bytes of the strings a segment holds, in the child"
    ),
    ("core/schema/descriptors.py", "Envelope.curated_pointers", "__dict__"): (
        "reads the fields member"
    ),
    ("core/schema/output.py", "_members", "__dict__"): "check_values walks an output's members",
    ("core/schema/output.py", "_members", "__pydantic_extra__"): "the same, for extra members",
    ("core/schema/output.py", "TextSegment.__setstate__", "__setstate__"): (
        "the guard's own, after its check"
    ),
    ("core/store/proposals.py", "_undeclared", "__dict__"): "reads the fields member",
    ("core/schema/document.py", "PackLeaf._check_pack", "__pydantic_extra__"): (
        "reads a pack leaf's extra members, to check them"
    ),
    ("core/importers/checks.py", "<module>", "__dict__"): (
        "reads the class dictionary of BaseException, for its own __traceback__ descriptor"
    ),
    ("core/importers/checks.py", "_descriptors", "__pydantic_serializer__"): (
        "dumps a descriptor by its class's serializer, so that nothing the instance holds is "
        "called; a descriptor holds no server text and is no Output, and what it dumps is read "
        "back into descriptors only, whose strings are data"
    ),
    ("core/importers/checks.py", "_fields", "vars"): (
        "reads the instance dictionary of an object a pack built, only when it is a plain dict of "
        "exact str keys, so that no code of the pack's runs; what is read is copied through "
        "text(), data() or a constructor, never handed on"
    ),
    ("core/schema/copiers.py", "_fields", "vars"): (
        "reads the instance dictionary of an object a pack built, only when it is a plain dict of "
        "exact str keys, so that no code of the pack's runs; what is read is counted and then "
        "validated again as the exact instance it is (D403), never handed on"
    ),
    ("core/schema/copiers.py", "_fields", "__pydantic_fields_set__"): (
        "reads the fields-set of a model a pack built, only to check that it is an exact set of "
        "exact str, so that the instance's validation reads built-in types only; nothing is made "
        "from it"
    ),
    ("core/schema/copiers.py", "_fields", "__pydantic_extra__"): (
        "reads that a model a pack built holds no extra members, before it is validated again; "
        "nothing is made from it"
    ),
    ("core/schema/copiers.py", "_fields", "__pydantic_private__"): (
        "reads that a model a pack built holds no private members, before it is validated again; "
        "nothing is made from it"
    ),
    ("core/schema/guards.py", "<module>", "vars"): (
        "reads the built-in exceptions' names, from the module builtins: no output, no object of "
        "a pack's"
    ),
    ("core/schema/guards.py", "Hook.__init__", "__setattr__"): (
        "sets the three slots of the handle it is making, before anyone holds it, to a pack, a "
        "stage and the hook object, which is stored and never read; no model, no server text"
    ),
}
"""The reads of a banned name, by file, enclosing function and name, each with why."""

SITES: dict[Boundary, tuple[tuple[str, str], ...]] = {
    Boundary.REPORT_NOTES: (("core/store/proposals.py", "curation_queue"),),
    Boundary.RESULT_CACHE: (
        ("core/analyses/results.py", "Digested.read"),
        ("core/analyses/results.py", "Digested.read"),
    ),
    Boundary.READER_ANSWER: (
        ("core/importers/worker.py", "Reader.run"),
        ("core/importers/worker.py", "Reader.run"),
    ),
    Boundary.CLI_CLIENT: (
        ("core/operator/client.py", "OperatorClient._read"),
        ("core/operator/client.py", "OperatorClient._read"),
    ),
}
"""Each boundary's sites, by file and enclosing function, once for each: the rule's sites must be
exactly these, so a new site, or one moved, is a change to this table (D399)."""

DUMPS_IN_VALIDATION: dict[tuple[str, str], str] = {
    ("core/schema/results.py", "ResultEnvelope._check"): (
        "an after-validator: dumps the envelope Pydantic just validated, to find what raises a "
        "caveat in its digested parts; nothing it dumps is served"
    ),
    ("core/schema/results.py", "_check_digest"): (
        "called by the after-validators of ResultEnvelope and CohortCount: dumps the output just "
        "validated, to digest its digested members; nothing it dumps is served"
    ),
    ("core/schema/results.py", "CohortCount._check"): (
        "an after-validator: dumps the count Pydantic just validated, to check its population; "
        "nothing it dumps is served"
    ),
}
"""The functions that call ``dumped_in_validation``, by file and enclosing function, each with why:
there the output was validated a moment before, by the validator itself, and the dump is read by
that validator and served nowhere (D399)."""

NAMED: dict[tuple[str, str], str] = {
    ("core/analyses/registry.py", "_given"): "the members of a model, by its own field names",
    ("core/engine/readback.py", "_Reader.predicate"): "a range's bounds, by a constant tuple",
    ("core/engine/resolve.py", "_Resolver._predicate"): "a range's bounds, by a constant tuple",
    ("core/engine/resolved.py", "Deduplicated._constants"): "a range's bounds, by a constant tuple",
    ("core/engine/resolved.py", "document"): "a range's bounds, by a constant tuple",
    ("core/engine/suppression.py", "_Counts.of"): "a count of a dataclass, by its own field name",
    ("core/importers/worker.py", "_decoded"): "a dataclass's fields, by dataclasses.fields",
    ("core/schema/checks.py", "_cross_dataset"): "a leaf's member, by a constant name",
    ("core/schema/descriptors.py", "ConceptFields._check"): "members, by a constant tuple",
    ("core/schema/descriptors.py", "_chosen"): "a member of a model, by a name its caller gives",
    ("core/schema/document.py", "ValueLeaf._check_predicate"): "members, by a constant tuple",
    ("core/schema/limits.py", "LogLimits.__post_init__"): "limits, by constant tuples of names",
    ("core/schema/limits.py", "QueryLimits.__post_init__"): "limits, by constant tuples of names",
    ("core/schema/numbers.py", "computed_nulls"): "a model's fields, by its own field names",
    ("core/schema/operator.py", "_stored"): "a request's members, by a constant tuple",
    ("core/schema/output.py", "Output.model_copy"): "the members of an output that are set",
    ("core/schema/release.py", "_Checker.coverage_tables"): "a constant pair of members",
}
"""The functions that read an attribute by a name that is not a constant (``getattr(x, name)``),
by file and enclosing function, each with why."""

IMPORTS: dict[tuple[str, str], str] = {}
"""The functions that import a module by a name they are given (``importlib.import_module``), by
file and enclosing function, each with why: a name from data would reach the output module."""

CONTEXT_READS: dict[tuple[str, str], str] = {
    ("core/api/config.py", "_path"): "the configuration's directory, if the context is a dict",
    ("core/schema/document.py", "_parsed"): "whether the document was parsed, if it is a dict",
}
"""The functions that read a validation context (``info.context``), by file and enclosing
function, each with why: what a validator reads it for must not be the capability."""

POST_INIT_ALLOWED = "__post_init__"
MEMORY: dict[tuple[str, str], str] = {
    ("core/importers/worker.py", "_die_with"): "the reader child asks to die with its parent",
    ("core/engine/duck.py", "_die_with"): "the query child asks to die with its parent",
    ("core/schema/loading.py", "<module>"): "pauses the collector while a document is parsed",
}
"""Where ``ctypes`` or ``gc``, which reach objects past their types, are imported, and why."""


def reflected() -> frozenset[str]:
    """The attribute names the rule classifies."""
    return frozenset(dir(BaseModel)) | frozenset(dir(Output)) | frozenset(dir(TextSegment))


def banned(name: str) -> bool:
    if name.startswith("__pydantic_"):
        return True
    found = CLASSIFIED.get(name)
    return found is not None and found[0] == BANNED


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str


@dataclass(frozen=True)
class Site:
    member: str
    file: str
    function: str
    line: int


def _chain(node: ast.expr) -> bool:
    """A name, or attributes of a name: nothing evaluated but lookups."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _plain(node: ast.expr) -> bool:
    """A name, attribute, subscript or constant, all the way down."""
    if isinstance(node, ast.Name | ast.Constant):
        return True
    if isinstance(node, ast.Attribute):
        return _plain(node.value)
    if isinstance(node, ast.Subscript):
        return _plain(node.value) and _plain(node.slice)
    return False


def _member(node: ast.expr) -> tuple[str, ast.Name] | None:
    """``MEMBER`` of ``Boundary.MEMBER``, with the name ``Boundary``."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Boundary"
        and node.attr in Boundary.__members__
    ):
        return node.attr, node.value
    return None


def _capability_call(node: ast.expr, callee: str, arity: int) -> tuple[str, list[ast.AST]] | None:
    """``MEMBER`` of ``callee(Boundary.MEMBER, ...)``, with the names of the capability."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == callee
        and len(node.args) == arity
        and not node.keywords
    ):
        return None
    member = _member(node.args[0])
    return None if member is None else (member[0], [node.func, member[1]])


def boundary_call(statement: ast.stmt) -> tuple[str, list[ast.AST]] | None:
    """The member of a statement of a boundary site's shape, with the nodes the shape allows to
    name the capability; ``None`` for any other statement."""
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return None
        call = statement.value
    elif isinstance(statement, ast.Return | ast.Expr):
        call = statement.value
    else:
        return None
    if not isinstance(call, ast.Call):
        return None
    if isinstance(call.func, ast.Name) and call.func.id == "unpickled":
        if len(call.args) != 2 or not isinstance(call.args[1], ast.Name):
            return None
        return _capability_call(call, "unpickled", 2)
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in METHODS
        and _chain(call.func.value)
        and all(_plain(argument) for argument in call.args)
        and len(call.keywords) == 1
        and call.keywords[0].arg == "context"
    ):
        return None
    return _capability_call(call.keywords[0].value, "admitted_at", 1)


class _Scan(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.findings: list[Finding] = []
        self.sites: list[Site] = []
        self.scopes: list[_ast.Scope] = []
        self.allowed: set[int] = set()
        """Nodes a boundary site may hold that name the capability."""
        self.callees: set[int] = set()
        """Nodes that are the function of a call."""
        self.output_names: set[str] = set(OUTPUT_EXPORTS) if file == OUTPUT else set()
        """Names bound to the output module or to a class it exports."""
        self.text_names: set[str] = {TEXT_TYPE}
        """Names bound to ``TextSegment``."""

    # --- where ------------------------------------------------------------------------------

    def function(self) -> str:
        return _ast.qualified(self.scopes)

    def flag(self, node: ast.AST, rule: str) -> None:
        self.findings.append(Finding(self.file, getattr(node, "lineno", 0), rule))

    def read_allowed(self, name: str) -> bool:
        return (self.file, self.function(), name) in READS

    def identifiers(self, node: ast.AST) -> None:
        """Every identifier the tree names that no node of its own carries as a ``Name`` or an
        ``Attribute`` (a keyword's, a pattern's, an import's, a parameter's, a ``global``'s)
        is held to the banned and capability names, whatever the spelling: ``case object(
        __dict__=d)`` names ``__dict__`` as a bare identifier, which no ``Attribute`` or
        ``Constant`` holds. A definition's own name is not a use of it."""
        if isinstance(node, IDENTIFIED_ELSEWHERE):
            return
        for name in _identifiers(node):
            if banned(name) and not self.read_allowed(name):
                self.flag(node, f"the banned name {name} as an identifier")
            elif isinstance(node, ast.alias):
                continue  # an import of a public capability name is checked where it is used
            elif name in PRIVATE_CAPABILITY | PUBLIC_CAPABILITY and self.file != OUTPUT:
                self.flag(node, f"the capability's name {name} as an identifier")
        if isinstance(node, ast.MatchClass) and "context" in node.kwd_attrs:
            self.flag(node, "a validation context read by a pattern")

    def generic_visit(self, node: ast.AST) -> None:
        self.identifiers(node)
        scoped = isinstance(node, _ast.Scope)
        if isinstance(node, _ast.Scope):
            self.scopes.append(node)
        if isinstance(node, ast.stmt):
            self._statement(node)
        super().generic_visit(node)
        if scoped:
            self.scopes.pop()

    # --- (b) the capability -------------------------------------------------------------------

    def _statement(self, node: ast.stmt) -> None:
        if self.file == OUTPUT:
            return
        found = boundary_call(node)
        if found is None:
            return
        member, names = found
        if any(isinstance(scope, ast.AsyncFunctionDef) for scope in self.scopes):
            self.flag(node, "a boundary site in an async def")
            return
        self.allowed.update(id(name) for name in names)
        self.sites.append(Site(member, self.file, self.function(), node.lineno))

    def _capability(self, node: ast.AST, name: str) -> None:
        if self.file == OUTPUT:
            return
        if name in PRIVATE_CAPABILITY:
            self.flag(node, f"the capability's private name {name} outside output.py")
        elif name in PUBLIC_CAPABILITY and id(node) not in self.allowed:
            self.flag(node, f"{name} outside a boundary site of the shape")
        elif name == DUMP_HELPER:
            if (self.file, self.function()) not in DUMPS_IN_VALIDATION:
                self.flag(node, f"{name} outside DUMPS_IN_VALIDATION")
            elif id(node) not in self.callees:
                self.flag(node, f"{name} used as a value, which is an alias of it")

    def visit_Name(self, node: ast.Name) -> None:
        self._capability(node, node.id)
        if node.id in ALIASED and isinstance(node.ctx, ast.Load) and id(node) not in self.callees:
            self.flag(node, f"{node.id} used as a value, which is an alias of it")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """A string that is a banned or capability name reaches it by ``getattr`` or the like."""
        value = _ast.string(node)
        if value is None or self.file == OUTPUT or self.read_allowed(value):
            return
        if value in PRIVATE_CAPABILITY | PUBLIC_CAPABILITY | {DUMP_HELPER} or value in (
            OUTPUT_MODULE,
            "context",
        ):
            self.flag(node, f"the string {value!r} spells a name of the capability")
        elif banned(value):
            self.flag(node, f"the string {value!r} spells a banned name")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in PRIVATE_CAPABILITY:
                self.flag(node, f"an import of the capability's private name {alias.name}")
            if node.module == "aibi.core.schema.output":
                if alias.name in OUTPUT_EXPORTS:
                    self.output_names.add(alias.asname or alias.name)
                if alias.name == TEXT_TYPE:
                    self.text_names.add(alias.asname or alias.name)
                if alias.asname and alias.name in PUBLIC_CAPABILITY:
                    self.flag(node, f"{alias.name} imported under another name")
            if alias.asname and alias.name == DUMP_HELPER:
                self.flag(node, f"{alias.name} imported under another name")
            if node.module == "aibi.core.schema" and alias.name == "output":
                self.output_names.add(alias.asname or alias.name)
            if alias.name in ("ctypes", "gc") or (node.module or "").split(".")[0] in (
                "ctypes",
                "gc",
            ):
                self._memory(node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == OUTPUT_MODULE and alias.asname:
                self.output_names.add(alias.asname)
            if alias.name.split(".")[0] in ("ctypes", "gc"):
                self._memory(node)
        self.generic_visit(node)

    def _memory(self, node: ast.AST) -> None:
        if (self.file, self.function()) not in MEMORY:
            self.flag(node, "ctypes or gc imported")

    # --- (a) names ----------------------------------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._capability(node, node.attr)
        receiver = node.value
        if (isinstance(receiver, ast.Name) and receiver.id == "BaseModel") or (
            isinstance(receiver, ast.Attribute) and receiver.attr == "BaseModel"
        ):
            self.flag(node, f"BaseModel.{node.attr} skips an output's override")
        if banned(node.attr) and not self.read_allowed(node.attr) and not self._own_post_init(node):
            self.flag(node, f"the banned name {node.attr}")
        if node.attr in ALIASED and isinstance(node.ctx, ast.Load) and id(node) not in self.callees:
            self.flag(node, f"{node.attr} used as a value, which is an alias of it")
        if (
            node.attr == "context"
            and isinstance(node.ctx, ast.Load)
            and self.file != OUTPUT
            and (self.file, self.function()) not in CONTEXT_READS
        ):
            self.flag(node, "a validation context read outside CONTEXT_READS")
        if isinstance(node.ctx, ast.Store | ast.Del):
            if node.attr in CLASSIFIED or node.attr.startswith("__pydantic_"):
                self.flag(node, f"{node.attr} assigned or deleted")
            if isinstance(receiver, ast.Name) and receiver.id in self.output_names:
                self.flag(node, f"an attribute of {receiver.id} assigned or deleted")
        self.generic_visit(node)

    def _own_post_init(self, node: ast.Attribute) -> bool:
        """``object.__setattr__`` as a frozen dataclass's ``__post_init__`` sets one of its own
        declared members: checked at the call (``visit_Call``)."""
        return (
            node.attr == "__setattr__"
            and isinstance(node.value, ast.Name)
            and node.value.id == "object"
            and id(node) in self.allowed
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Store | ast.Del):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr in ("__dict__", "model_config"):
                self.flag(node, f"a write through {value.attr}")
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "vars"
            ):
                self.flag(node, "a write through vars()")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        self.callees.add(id(function))
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "__setattr__"
            and isinstance(function.value, ast.Name)
            and function.value.id == "object"
            and self._declared(node)
        ):
            self.allowed.add(id(function))
        callee = (
            function.id
            if isinstance(function, ast.Name)
            else function.attr
            if isinstance(function, ast.Attribute)
            else None
        )
        if callee is not None:
            self._call(node, callee, isinstance(function, ast.Name))
        for keyword in node.keywords:
            if keyword.arg == "context" and self.file != OUTPUT:
                value = keyword.value
                if not isinstance(value, ast.Dict) and id(_func(value)) not in self.allowed:
                    self.flag(node, "a context that is not a dict literal outside a boundary site")
        self.generic_visit(node)

    def _call(self, node: ast.Call, callee: str, plain: bool) -> None:
        """The checks of a call by the name it is made with: ``plain`` for a bare name."""
        if callee in ("setattr", "delattr"):
            given = node.args[1] if len(node.args) > 1 else None
            name = _ast.string(given)
            if name is None:
                self.flag(node, f"{callee} with a name that is not a constant")
            elif name in CLASSIFIED or name.startswith("__pydantic_"):
                self.flag(node, f"{callee} of {name}")
            receiver = node.args[0] if node.args else None
            if isinstance(receiver, ast.Name) and receiver.id in self.output_names:
                self.flag(node, f"{callee} on {receiver.id}")
        if (plain and callee in DYNAMIC) or (
            callee == "import_module" and (self.file, self.function()) not in IMPORTS
        ):
            self.flag(node, f"{callee}, which runs or imports code the rule cannot read")
        if callee in NAMING:
            self._naming(node, callee)
        if plain and callee == "super" and len(node.args) == 2:
            self.flag(node, "two-argument super, which skips an override")
        if callee == "vars" and node.args and not self.read_allowed("vars"):
            self.flag(node, "vars(), which is __dict__")
        if callee in GUARDED_CALLS:
            if any(keyword.arg is None for keyword in node.keywords) or any(
                isinstance(argument, ast.Starred) for argument in node.args
            ):
                self.flag(node, f"* or ** in a call of {callee}, which could carry a capability")
            if callee in METHODS and len(node.args) > 1:
                self.flag(node, f"{callee} given its options by position")
        if callee == "copy" and any(keyword.arg == "update" for keyword in node.keywords):
            self.flag(node, "copy(update=...), which validates nothing")
        if self.file != OUTPUT and (
            (plain and callee in self.text_names)
            or (isinstance(node.func, ast.Attribute) and _tail(node.func.value) in self.text_names)
        ):
            self.flag(node, "a TextSegment built by a call: text() makes one")

    def _naming(self, node: ast.Call, callee: str) -> None:
        """A read by name, whose name is a constant or the function is at a site of ``NAMED``."""
        if callee in ("getattr", "getattr_static"):
            given = node.args[1:2]
        elif callee in ("__getattribute__", "__getattr__"):
            given = node.args[-1:]
        elif callee == "methodcaller":
            given = node.args[:1]
        else:
            given = node.args
        if (self.file, self.function()) in NAMED:
            return
        if not given or any(_ast.string(name) is None for name in given):
            self.flag(node, f"{callee} with a name that is not a constant")

    def _declared(self, node: ast.Call) -> bool:
        """``object.__setattr__(self, "member", ...)`` in the ``__post_init__`` of a frozen
        dataclass that declares ``member``."""
        if len(self.scopes) < 2:
            return False
        method, owner = self.scopes[-1], self.scopes[-2]
        if not (
            isinstance(method, ast.FunctionDef)
            and method.name == POST_INIT_ALLOWED
            and isinstance(owner, ast.ClassDef)
            and _frozen_dataclass(owner)
        ):
            return False
        if len(node.args) != 3:
            return False
        target, name = node.args[0], node.args[1]
        if not (isinstance(target, ast.Name) and target.id == "self"):
            return False
        if _ast.string(name) is None:
            return False
        declared = {
            statement.target.id
            for statement in owner.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        return _ast.string(name) in declared


IDENTIFIED_ELSEWHERE = (
    ast.Name,
    ast.Attribute,
    ast.Constant,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ImportFrom,
)
"""Nodes whose identifiers have checks of their own (a name, an attribute, a string, an
import's names) or are a definition's name, which is no use of it."""


def _identifiers(node: ast.AST) -> Iterator[str]:
    """Every ``str`` and every ``str`` of a list that a node's fields hold: the identifiers a
    node spells itself (``ast.iter_fields``), whatever its kind."""
    for _, value in ast.iter_fields(node):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))  # pyright: ignore[reportUnknownVariableType]


def _tail(node: ast.expr) -> str | None:
    """The last name of a name or of attributes of a name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _func(node: ast.expr) -> ast.AST:
    return node.func if isinstance(node, ast.Call) else node


def _frozen_dataclass(owner: ast.ClassDef) -> bool:
    for decorator in owner.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
        ):
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "frozen"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    return True
    return False


ROUTES = frozenset({"get", "post", "put", "patch", "delete", "api_route"})


def _response(node: ast.expr) -> bool:
    """A class named ``...Response``, and nothing else (not a union that holds one)."""
    name = _tail(node)
    return name is not None and name.endswith("Response")


def _responds(annotation: ast.expr | None) -> bool:
    """An annotation that is a ``Response``, or ``Callable[[...], Awaitable[Response]]``."""
    if annotation is None:
        return False
    if _response(annotation):
        return True
    if (
        isinstance(annotation, ast.Subscript)
        and _tail(annotation.value) == "Callable"
        and isinstance(annotation.slice, ast.Tuple)
        and len(annotation.slice.elts) == 2
    ):
        returned = annotation.slice.elts[1]
        return (
            isinstance(returned, ast.Subscript)
            and _tail(returned.value) == "Awaitable"
            and _response(returned.slice)
        )
    return False


def routes(tree: ast.AST, file: str) -> list[Finding]:
    """(c): every route answers with a ``Response``."""
    found: list[Finding] = []
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, _ast.Function)}
    for node in ast.walk(tree):
        if isinstance(node, _ast.Function):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in ROUTES
                    and not _responds(node.returns)
                ):
                    found.append(Finding(file, node.lineno, "a route that is not a Response"))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_api_route"
        ):
            given = [*node.args[1:2], *(k.value for k in node.keywords if k.arg == "endpoint")]
            maker = given[0] if given else None
            made = (
                functions.get(maker.func.id)
                if isinstance(maker, ast.Call) and isinstance(maker.func, ast.Name)
                else None
            )
            if made is None or not _responds(made.returns):
                found.append(Finding(file, node.lineno, "an endpoint that is not a Response"))
    return found


def scan(source: str, file: str) -> tuple[list[Finding], list[Site]]:
    """The findings and the boundary sites of one module, ``file`` its path below ``src/aibi``."""
    found = _Scan(file)
    tree = ast.parse(source)
    found.visit(tree)
    return found.findings + routes(tree, file), found.sites


def modules(root: Path = SOURCE) -> Iterator[Path]:
    """Every module of ``src/aibi``: the core and the packs."""
    yield from sorted(root.rglob("*.py"))


def scan_source(root: Path = SOURCE) -> tuple[list[Finding], list[Site]]:
    findings: list[Finding] = []
    sites: list[Site] = []
    for path in modules(root):
        got = scan(path.read_text(), path.relative_to(root).as_posix())
        findings += got[0]
        sites += got[1]
    return findings, sites


def table_of(sites: list[Site]) -> dict[Boundary, tuple[tuple[str, str], ...]]:
    """The sites as ``SITES`` lists them."""
    found: dict[Boundary, list[tuple[str, str]]] = {member: [] for member in Boundary}
    for site in sites:
        found[Boundary[site.member]].append((site.file, site.function))
    return {member: tuple(sorted(places)) for member, places in found.items()}
