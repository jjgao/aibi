"""Registries at and around the bounds on what the server serves alike to every call (D421), for
the bound's tests (``test_served_bounds.py``) and for measuring them by hand
(``measure_served.py``); M5.3-server-b2's measurement builds on them too.

A test pack is described by a ``Spec`` and built by ``pack_of``: tiny analyses (bare roles, or
full requirements and every optional member: model-dense), their methods, assumptions and caveats
(the pack declares 64 codes), leaf kinds whose schema is ``{"type": "object"}``, and the first
analysis's ``params`` or the first kind's schema given a ``description`` (``pad``, text of one
of the string classes) and an ``enum`` of zeros (values-dense). An extension schema of zeros is
data the registry copies but never serves. No domain: packs, kinds and analyses are letters and
numbers.

``write_modules`` writes one module per spec, each building its pack from this module, for the
server's loader (``api.packs.load``); ``transports`` serves a registry through the whole
application and answers a tool's HTTP body and MCP response (``Wire``), the oracle the bounds
are compared with: its bytes are the real transports', never the server's own measure.
"""

import importlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient
from pydantic import JsonValue

from aibi.core.api.app import create_app
from aibi.core.api.config import BASE, ServerConfig
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of, tools_of
from aibi.core.operator.auth import hash_token, new_token
from aibi.core.schema.caveats import Severity
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.pack_api import AnalysisInputs, Pack, PackManifest, PackRegistry
from aibi.core.store.store import Store

TEST_PACKS = "aibi_test_pack_"
"""The prefix of the modules ``write_modules`` writes, which ``api/conftest._process_state``
unloads after each test."""

CLASSES: dict[str, str] = {
    "ascii": "a",
    "control": "\x01",
    "quote": '"',
    "backslash": "\\",
    "bmp2": "é",
    "bmp3": "中",
    "astral": "\U0001f600",
}
"""One character of each string class: printable ASCII; a control character, escaped as
``\\u0001`` in the body and again in the MCP text; a quote and a backslash, escaped as two
characters and then four; non-ASCII characters of two and three bytes in UTF-8; and an astral
one, four bytes (twelve in an ASCII-escaped JSON string)."""

CODES = tuple(f"C{chr(65 + n // 26)}{chr(65 + n % 26)}" for n in range(64))
"""The caveat codes every test pack declares, after its id."""


@dataclass(frozen=True)
class Spec:
    """A test pack: ``analyses`` analyses ``<id>.a0000``…, each with ``requires`` requirements
    (the first ``extra`` one more), ``methods``, ``assumptions`` and ``caveats`` members, and,
    with ``full``, full requirements and every optional member; ``tail`` more assumptions,
    spread over its last analyses (at most 64 each); ``kinds`` leaf kinds ``<id>.k0000``…; the
    ``pad`` and ``zeros`` in the first analysis's ``params`` (``at`` ``"analysis"``) or the first
    kind's schema (``"kind"``), with ``nests`` (containers: per ``(shape, depth, count)``,
    ``count`` objects ``{"a": …}`` or arrays ``[…]`` nested ``depth`` deep, or objects of
    ``depth`` keys holding zeros, ``"keys"``, of ``depth`` astral keys, ``"astral keys"``, or of
    ``depth`` keys holding an astral character, ``"astral values"``; the ``zeros`` each
    ``filler``) after the zeros in its ``enum``, and ``examples``
    (JSON text) as its ``examples``; ``extension`` zeros in an extension schema."""

    id: str
    version: str = "1.0.0"
    analyses: int = 0
    requires: int = 1
    extra: int = 0
    methods: int = 0
    assumptions: int = 0
    caveats: int = 0
    full: bool = False
    tail: int = 0
    kinds: int = 0
    pad: str = ""
    zeros: int = 0
    at: Literal["analysis", "kind"] = "analysis"
    extension: int = 0
    nests: tuple[tuple[str, int, int], ...] = ()
    examples: str = ""
    filler: str = "0"
    """The JSON of each of the ``zeros``: a zero unless another scalar is given."""


class _Analysis:
    def __init__(self, entry: AnalysisDescriptor) -> None:
        self._entry = entry

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> dict[str, JsonValue]:
        return {}


class _Kind:
    def __init__(self, schema: dict[str, JsonValue]) -> None:
        self._schema = schema

    @property
    def schema(self) -> dict[str, JsonValue]:
        return self._schema

    def compile(self, leaf: Any, release: Any, pack_version: str) -> list[Any]:
        return []

    def summary(self, leaf: Any) -> list[Any]:
        return []


def nested(shape: str, depth: int) -> JsonValue:
    """One container of ``shape``: an object or an array nested ``depth`` deep (``depth``
    containers), objects so nested each under an astral key (``"astral chain"``), or an object of
    ``depth`` keys holding zeros (one container)."""
    if shape == "keys":
        return {f"k{n}": 0 for n in range(depth)}
    if shape == "astral keys":
        return {chr(0x1F600 + n): 0 for n in range(depth)}
    if shape == "astral values":
        return {f"k{n}": "\U0001f600" for n in range(depth)}
    found: JsonValue = [] if shape == "array" else {}
    key = "\U0001f600" if shape == "astral chain" else "a"
    for _ in range(depth - 1):
        found = [found] if shape == "array" else {key: found}
    return found


def _padded(spec: Spec, schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if spec.pad:
        schema["description"] = spec.pad
    if spec.zeros or spec.nests:
        enum: list[JsonValue] = [json.loads(spec.filler)] * spec.zeros
        for shape, depth, count in spec.nests:
            enum += [nested(shape, depth) for _ in range(count)]
        schema["enum"] = enum
    if spec.examples:
        schema["examples"] = json.loads(spec.examples)
    return schema


def _requirement(role: str, full: bool) -> dict[str, JsonValue]:
    if not full:
        return {"role": role}
    return {"role": role, "kind": "column", "on": "unit", "datatype": "integer", "min": 1, "max": 9}


def _entry(spec: Spec, n: int, assumptions: int) -> AnalysisDescriptor:
    count = spec.requires + (1 if n < spec.extra else 0)
    fields: dict[str, Any] = {
        "requires": [_requirement(f"r{j}", spec.full) for j in range(count)],
        "params": _padded(spec, {"type": "object"}) if n == 0 and spec.at == "analysis" else {},
        "returns": {},
        "methods": {f"m{j}": "x" for j in range(spec.methods)},
        "assumptions": ["x"] * assumptions,
        "uses_reference": False,
        "assumes_independent_groups": True,
        "cross_dataset": None,
        "caveats": [f"{spec.id}.{code}" for code in CODES[: spec.caveats]],
    }
    if spec.full:
        fields |= {
            "library": {"name": "x", "version": "1"},
            "randomness": {"seeded": True, "replicates": 1},
            "min_group_n": 1,
            "min_events": 1,
        }
    given: dict[str, Any] = {
        "kind": "analysis",
        "id": f"{spec.id}.a{n:04d}",
        "version": "1.0.0",
        "label": "A",
        "fields": fields,
    }
    if spec.full:
        given["definition"] = "x"
    return AnalysisDescriptor.model_validate(given)


def _assumptions(spec: Spec) -> list[int]:
    found = [spec.assumptions] * spec.analyses
    left = spec.tail
    for n in reversed(range(spec.analyses)):
        more = min(left, 64 - found[n])
        found[n] += more
        left -= more
    if left:
        raise ValueError("more tail than the analyses hold")
    return found


def pack_of(spec: Spec) -> Pack:
    """The test pack ``spec`` describes."""
    manifest = PackManifest.model_validate(
        {"id": spec.id, "version": spec.version, "results_version": 1, "requires_core": ">=0"}
    )
    kinds = {
        f"{spec.id}.k{n:04d}": _Kind(
            _padded(spec, {"type": "object"})
            if n == 0 and spec.at == "kind"
            else {"type": "object"}
        )
        for n in range(spec.kinds)
    }
    analyses = [_Analysis(_entry(spec, n, count)) for n, count in enumerate(_assumptions(spec))]
    return Pack(
        manifest=manifest,
        analyses=analyses,
        leaf_kinds=kinds,
        caveat_codes={f"{spec.id}.{code}": Severity.INFO for code in CODES},
        extension_schemas=(
            {"dataset": {"type": "object", "enum": list[JsonValue]([0] * spec.extension)}}
            if spec.extension
            else {}
        ),
    )


def spread(total: int, packs: int = 16, **given: Any) -> list[Spec]:
    """``total`` analyses (or, with ``kinds=True``, leaf kinds) over ``packs`` packs
    ``p00``…, as evenly as they go, the first packs one more, each pack of its own version."""
    as_kinds = given.pop("kinds", False)
    specs: list[Spec] = []
    for p in range(packs):
        count = total // packs + (1 if p < total % packs else 0)
        member = {"kinds": count} if as_kinds else {"analyses": count}
        specs.append(Spec(id=f"p{p:02d}", version=f"1.{p}.0", **member, **given))
    return specs


def write_modules(directory: Path, specs: list[Spec]) -> list[str]:
    """One module per spec in ``directory`` (on ``sys.path``), each defining ``PACK`` as
    ``pack_of`` builds it; their names, in order."""
    names: list[str] = []
    for spec in specs:
        name = f"{TEST_PACKS}{uuid.uuid4().hex}"
        source = (
            "from tests.core.api.served_builders import Spec, pack_of\n"
            f"PACK = pack_of(Spec(**{asdict(spec)!r}))\n"
        )
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
        names.append(name)
    importlib.invalidate_caches()
    return names


def config_of(root: Path, modules: list[str]) -> ServerConfig:
    """A configuration over a store in ``root`` with generous rates and ``modules``."""
    (root / "imports").mkdir(parents=True, exist_ok=True)
    generous = {"per_minute": 1_000_000, "burst": 1_000_000}
    written: dict[str, Any] = {
        "server": {"rates": dict.fromkeys(("operator", "api", "token_failures"), generous)},
        "curator": {"token_hash": hash_token(new_token())},
        "storage": {"data": "data", "imports": ["imports"]},
        "packs": {"modules": modules},
    }
    return ServerConfig.model_validate(written, context={BASE: root})


@dataclass(frozen=True)
class Wire:
    """A tool's answer as each transport sent it: the HTTP body, and the MCP response to a
    ``tools/call`` of ``id`` 1."""

    http: bytes
    mcp: bytes


ACCEPT = {"Accept": "application/json, text/event-stream"}


@contextmanager
def transports(
    root: Path, registry: PackRegistry | None
) -> Iterator[Callable[[str, dict[str, JsonValue]], Wire]]:
    """The whole application over a new store in ``root`` with ``registry``'s packs; a call of a
    tool with its arguments over both transports."""
    config = config_of(root, [])
    store = Store(config.storage.data)
    try:
        services = services_of(config, store, registry or PackRegistry((), core_version="0.0.1"))
        app = create_app(
            Policy.of(config, csrf_key=b"k" * 32), services, tools=tools_of(config, store, registry)
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:

            def call(tool: str, arguments: dict[str, JsonValue]) -> Wire:
                http = client.post(f"/api/tools/{tool}", json=arguments)
                assert http.status_code == 200, http.text[:500]
                message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                }
                answered = client.post("/mcp", json=message, headers=ACCEPT)
                assert answered.status_code == 200, answered.text[:500]
                return Wire(http.content, answered.content)

            yield call
    finally:
        store.close()


def values_of(value: object) -> int:
    """JSON values, from the definition (each object, array, string, number, boolean and null;
    keys are not values), written apart from the server's counter."""
    if isinstance(value, dict):
        return 1 + sum(values_of(member) for member in value.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return 1 + sum(values_of(item) for item in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return 1


__all__ = [
    "ACCEPT",
    "CLASSES",
    "CODES",
    "TEST_PACKS",
    "Spec",
    "Wire",
    "config_of",
    "nested",
    "pack_of",
    "spread",
    "transports",
    "values_of",
    "write_modules",
]
