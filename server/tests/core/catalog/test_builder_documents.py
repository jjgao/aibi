"""The server's verdict on the documents the web's seam writes (D432: the table of rules,
``web/tests/fixtures/seam-rules.json``, each row of which names the cell that holds it).

``web/tests/fixtures/builder-documents.json`` is written by the web's property test
(``web/tests/unit/document-property.test.tsx``) from bodies its ``validateDraft`` sent: each entry
holds the typed values by position (cohort, leaf, column id; a long value as a run), the body's
SHA-256, its length, the server's measure of its document, and the body itself when it is small.
Here each body is rebuilt by an oracle of this test's own (the document's template and §7.1's
literal) and held to the web's digest, byte for byte, then POSTed as those bytes to
``/api/tools/validate_document`` of a real ``create_app`` over the orchard.

An entry's class is derived here from the server's own code, never from a hand classifier of the
limits: the server's models (``Document.model_json_schema()`` and ``DistributionParams``'s, walked
at every position the seam writes: ``_model_bounds``), the constants of ``aibi.core.schema.limits``,
and ``engine.resolved.measure`` of each cohort the server's own ``resolve`` gives (a leaf as §7.1's
caps count it: each down step of a path is one of its own). The web's ``classOf`` is held to that
class (``test_each_body_is_the_oracle_s_and_its_class_the_server_s``). Over the corpus's bodies (the
orchard's, far under ``MAX_VALUES`` JSON values; a body of more is not stated here), in the
server's order:

- **D'** a body over ``MAX_BODY_BYTES``: 413 ``LIMIT_EXCEEDED``, at every tool;
- **C** a value holding a noncharacter (U+FDD0-U+FDEF, or ``cp & 0xFFFE == 0xFFFE`` on any plane)
  or a surrogate: 422, every refusal ``INVALID_JSON`` at
  ``/document/cohorts/<name>/all/<i>/values/0`` of such a value;
- **D** a document over ``MAX_DOCUMENT_BYTES`` by the server's measure (its JSON as
  ``json.dumps`` writes it): validate 200, ``valid: false``, one ``LIMIT_EXCEEDED`` with no path;
  count and run 422, the same;
- the document's schema (every refusal at its own path, all of them together): **E** a list of
  more than the models' ``maxItems`` clauses (``MAX_CLAUSES``) at ``/cohorts/<name>/all``, and **B**
  a value of more than the models' ``maxLength`` code points (``MAX_STRING``, unescaped) at the
  value's ``/cohorts/<name>/all/<i>/values/0``; any of them precedes the resolution, and a list
  over its bound is refused as a whole, none of its values separately (those of another cohort
  are). The class is E when a list is over, else B;
- the resolution (after the schema, all together, validate 200 ``valid: false``): **E** a cohort
  of more than ``MAX_LEAVES`` leaves by ``measure`` at ``/cohorts/<name>/all``, and **F** a view
  of more than the models' ``maxItems`` columns (``MAX_VARIABLES``) at ``/views/0/params/columns``;
  count and run 422, with a first of those paths. The class is E when a cohort is over, else F;
- **A** otherwise: 200, ``valid: true``, and by position, for each cohort: the set of
  ``(column, values[0])`` of every value node of its canonical form equals the written pairs,
  unescaped; and in its readback each data token after a ``" is "`` text, paired with the first
  data token after the nearest ``"the "`` before it (the column's label), gives exactly the written
  ``(label, value)`` pairs, a value of more than 200 code points as a token marked ``truncated``
  whose text is exactly its first 200 code points.

Never a refusal at an id position, never ``UNKNOWN_PARAMETER``. A tenth of (A), and the
structural cases, also pass through ``count_cohort`` and ``run_analysis`` (200).

The table's bound rows are held against the models: every ``maxItems``, ``maxLength`` and
``maxProperties`` the models state at a position the seam writes is a row, and a row that the seam
reaches has an edge pair in the corpus (at the bound, and one past it, of the classes the row
names), or states why the seam never reaches it. The controls: each class's branch of the verdict
check fails an entry of another class, a wrong path, and a count or run answer of another status;
the corpus written without the ``$$`` escape fails on ``$x``; a ``$`` + 4,096 entry classed (A)
fails; a U+1FFFE entry classed (A) fails; a cohort of ``MAX_LEAVES / 2 + 1`` child-table leaves
classed (A) fails (33 distinct leaves are no verdict: the server counts 66); and a body whose two
leaves' values are swapped fails (A) by position.
"""

import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.core.api.served_builders import config_of
from tests.core.catalog.world import World, orchard_files

from aibi.core.api.app import create_app
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of, tools_of
from aibi.core.engine import resolve as resolve_module
from aibi.core.engine.data import Release
from aibi.core.engine.resolve import resolve
from aibi.core.engine.resolved import measure
from aibi.core.schema import limits
from aibi.core.schema.analyses import DistributionParams
from aibi.core.schema.document import Document
from aibi.core.schema.limits import (
    MAX_BODY_BYTES,
    MAX_CLAUSES,
    MAX_DOCUMENT_BYTES,
    MAX_LEAVES,
    MAX_STRING,
    MAX_VARIABLES,
)
from aibi.core.schema.loading import load_document
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

CORPUS = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "builder-documents.json"
)
RULES = CORPUS.with_name("seam-rules.json")
NAMES = ("first", "second")
CUT = 200
"""The readback's cut of a data token, in code points."""

Post = Callable[[str, bytes], httpx.Response]


def _entries() -> list[dict[str, Any]]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]


def _expand(spec: str | dict[str, Any]) -> str:
    if isinstance(spec, str):
        return spec
    return spec["prefix"] + spec["repeat"] * spec["times"] + spec["suffix"]


def _literal(value: str) -> str:
    return "$" + value if value.startswith("$") else value


def _cohorts(entry: dict[str, Any]) -> list[list[tuple[str, str]]]:
    return [[(column, _expand(spec)) for column, spec in leaves] for leaves in entry["cohorts"]]


def _document(entry: dict[str, Any], literal: Callable[[str], str] = _literal) -> dict[str, Any]:
    """The oracle's document of an entry: the seam's template, written apart from the web."""
    cohorts = _cohorts(entry)
    names = list(NAMES[: len(cohorts)])
    return {
        "aibi": "1",
        "dataset": "orchard",
        "unit": entry["unit"],
        "cohorts": {
            name: {
                "all": [
                    {"kind": "value", "column": column, "values": [literal(value)]}
                    for column, value in leaves
                ]
            }
            for name, leaves in zip(names, cohorts, strict=True)
        },
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": names,
                "params": {"columns": [{"column": column} for column in entry["view"]]},
            }
        ],
    }


def _js_bytes(value: Any) -> bytes:
    """``JSON.stringify``'s text of a value, in UTF-8: no spaces, a lone surrogate escaped."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "".join(
        f"\\u{ord(ch):04x}" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text
    ).encode("utf-8")


def _measure(document: Any) -> int:
    """The server's measure of a document as written (``catalog.cohorts``' ``load_document`` of
    ``json.dumps(written, ensure_ascii=False)``)."""
    text = json.dumps(document, ensure_ascii=False, allow_nan=False)
    return len(text.encode("utf-8", "surrogatepass"))


def _refused_point(point: int) -> bool:
    return 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFE) == 0xFFFE or 0xD800 <= point <= 0xDFFF


def _json_type(node: Any) -> str:
    if node is None:
        return "null"
    if isinstance(node, bool):
        return "boolean"
    if isinstance(node, int | float):
        return "number"
    return {str: "string", list: "array", dict: "object"}[type(node)]


def _deref(root: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = root
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        schema = node
    return schema


def _fits(root: dict[str, Any], schema: dict[str, Any], node: Any) -> bool:
    """Whether a node of the written document is one the schema's branch describes (its type, a
    tagged union's ``kind``, a member's pattern)."""
    schema = _deref(root, schema)
    kind = schema.get("type")
    if (
        kind is not None
        and kind != _json_type(node)
        and not (kind == "integer" and _json_type(node) == "number")
    ):
        return False
    tag = schema.get("properties", {}).get("kind", {}).get("const")
    if tag is not None and not (isinstance(node, dict) and node.get("kind") == tag):
        return False
    if isinstance(node, str) and "pattern" in schema and not re.search(schema["pattern"], node):
        return False
    if isinstance(node, dict):
        if any(key not in node for key in schema.get("required", [])):
            return False
        for key, member in schema.get("properties", {}).items():
            if key in node and isinstance(node[key], str) and not _fits(root, member, node[key]):
                return False
    return True


def _collect(
    root: dict[str, Any],
    schema: dict[str, Any],
    node: Any,
    at: str,
    out: dict[tuple[str, str], int],
) -> None:
    """The bounds (``maxItems``, ``maxLength``, ``maxProperties``) the schema states at each
    position of a written document, by pointer with ``*`` for a list index or a name of a map."""
    schema = _deref(root, schema)
    for union in ("anyOf", "oneOf"):
        for option in schema.get(union, []):
            if _fits(root, option, node):
                _collect(root, option, node, at, out)
    for keyword in ("maxItems", "maxLength", "maxProperties"):
        if keyword in schema and _fits(root, schema, node):
            out[(at, keyword)] = schema[keyword]
    if isinstance(node, dict):
        names = _deref(root, schema.get("propertyNames", {}))
        if "maxLength" in names:
            out[(f"{at}/*key", "maxLength")] = names["maxLength"]
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        extra = schema.get("additionalProperties")
        for key, member in node.items():
            if key in properties:
                _collect(root, properties[key], member, f"{at}/{key}", out)
                continue
            found = next(
                (sub for pattern, sub in patterns.items() if re.search(pattern, key)), None
            )
            if found is not None:
                _collect(root, found, member, f"{at}/*", out)
            elif isinstance(extra, dict):
                _collect(root, extra, member, f"{at}/*", out)  # pyright: ignore[reportUnknownArgumentType]
    elif isinstance(node, list) and "items" in schema:
        for member in node:
            _collect(root, schema["items"], member, f"{at}/*", out)


def _model_bounds() -> dict[tuple[str, str], int]:
    """Every bound the server's models state at a position the seam writes: the document's model
    over the document of two cohorts, and ``summary.distribution``'s parameters' over its params."""
    sample = {
        "unit": "trees",
        "cohorts": [[("trees.variety", "a")], []],
        "view": ["trees.variety"],
    }
    document = _document(sample)
    found: dict[tuple[str, str], int] = {}
    root = Document.model_json_schema()
    _collect(root, root, document, "", found)
    params = DistributionParams.model_json_schema()
    _collect(params, params, document["views"][0]["params"], "/views/*/params", found)
    return found


MODEL_BOUNDS = _model_bounds()
"""The server's own bounds, by (position, keyword)."""
CLAUSES = MODEL_BOUNDS[("/cohorts/*/all", "maxItems")]
STRING = MODEL_BOUNDS[("/cohorts/*/all/*/values/*", "maxLength")]
VARIABLES = MODEL_BOUNDS[("/views/*/params/columns", "maxItems")]


@dataclass(frozen=True)
class Facts:
    """What the server's own code says of an entry's document."""

    size: int
    """The body's bytes."""
    measure: int
    """The server's measure of the document (``MAX_DOCUMENT_BYTES``)."""
    written: list[int]
    """Each cohort's clauses as written."""
    leaves: dict[str, int]
    """Each cohort the server's ``resolve`` gives, by name, with its leaves by ``measure``."""
    columns: int
    """The view's columns as written."""


def _leaves_by_measure(text: str, release: Release) -> dict[str, int]:
    """Each cohort's leaves, as §7.1's caps count them: ``engine.resolved.measure`` of the
    clauses the server's ``resolve`` gives (the leaves cap lifted, so that a cohort over it is
    measured too). Empty where the document does not load (the schema refuses it first)."""
    loaded = load_document(text)
    if loaded.document is None:
        return {}
    with mock.patch.object(resolve_module, "MAX_LEAVES", sys.maxsize):
        resolution = resolve(loaded.document, {"orchard": release}, loaded.positions)
    return {name: measure(cohort.clauses)[2] for name, cohort in resolution.cohorts.items()}


def _has_refused_point(value: str) -> bool:
    return any(_refused_point(ord(ch)) for ch in value)


def _facts(entry: dict[str, Any], release: Release) -> Facts:
    document = _document(entry)
    body = _js_bytes({"document": document})
    values = [value for leaves in _cohorts(entry) for _, value in leaves]
    measured = _measure(document)
    leaves: dict[str, int] = {}
    loads = len(body) <= MAX_BODY_BYTES and measured <= MAX_DOCUMENT_BYTES
    if loads and not any(_has_refused_point(value) for value in values):
        leaves = _leaves_by_measure(
            json.dumps(document, ensure_ascii=False, allow_nan=False), release
        )
    return Facts(
        size=len(body),
        measure=measured,
        written=[len(leaves_) for leaves_ in _cohorts(entry)],
        leaves=leaves,
        columns=len(entry["view"]),
    )


@dataclass(frozen=True)
class Verdict:
    kind: str
    paths: frozenset[str] | None
    """The paths of the refusals validate_document gives all together (``None``: no list of paths:
    a body over the limit, or a document over its bytes)."""


def _verdict(entry: dict[str, Any], facts: Facts) -> Verdict:
    """The entry's class and the paths of its refusals, from the server's own code (the module's
    docstring)."""
    if facts.size > MAX_BODY_BYTES:
        return Verdict("D'", None)
    refused = _paths(entry, _has_refused_point, "/document")
    if refused:
        return Verdict("C", frozenset(refused))
    if facts.measure > MAX_DOCUMENT_BYTES:
        return Verdict("D", None)
    over = {at for at, count in enumerate(facts.written) if count > CLAUSES}
    lists = frozenset(f"/cohorts/{NAMES[at]}/all" for at in over)
    # A list over its bound is refused as a whole: none of its values separately (those of another
    # cohort are).
    strings = frozenset(
        f"/cohorts/{NAMES[at]}/all/{i}/values/0"
        for at, leaves in enumerate(_cohorts(entry))
        if at not in over
        for i, (_, value) in enumerate(leaves)
        if len(value) > STRING
    )
    if lists or strings:
        return Verdict("E" if lists else "B", lists | strings)
    cohorts = frozenset(
        f"/cohorts/{name}/all" for name, count in facts.leaves.items() if count > MAX_LEAVES
    )
    columns = frozenset({"/views/0/params/columns"} if facts.columns > VARIABLES else ())
    if cohorts or columns:
        return Verdict("E" if cohorts else "F", cohorts | columns)
    return Verdict("A", None)


def _value_leaves(node: Any, out: set[tuple[str, str]]) -> set[tuple[str, str]]:
    if isinstance(node, dict):
        if node.get("kind") == "value":
            out.add((node["column"], node["values"][0]))
        for member in node.values():
            _value_leaves(member, out)
    elif isinstance(node, list):
        for member in node:
            _value_leaves(member, out)
    return out


def _readback_pairs(readback: list[dict[str, Any]]) -> list[tuple[str | None, str, bool]]:
    pairs: list[tuple[str | None, str, bool]] = []
    for at, segment in enumerate(readback):
        following = readback[at + 1] if at + 1 < len(readback) else {}
        if segment.get("text") == " is " and "data" in following:
            back = at - 1
            while back >= 0 and readback[back].get("text") != "the ":
                back -= 1
            label = readback[back + 1].get("data") if back >= 0 else None
            pairs.append((label, following["data"], bool(following.get("truncated", False))))
    return pairs


def _a_problems(entry: dict[str, Any], answer: dict[str, Any], labels: dict[str, str]) -> list[str]:
    """Where an answer is not (A)'s for an entry (none: it is)."""
    if answer.get("valid") is not True:
        codes = [(r["code"], r["path"]) for r in answer.get("refusals", [])]
        return [f"not valid: {codes[:3]}"]
    problems: list[str] = []
    for at, leaves in enumerate(_cohorts(entry)):
        cohort = answer["cohorts"][at]
        if _value_leaves(cohort["form"], set()) != set(leaves):
            problems.append(f"cohort {at}: the form's value leaves are not the written pairs")
        pairs = _readback_pairs(cohort["readback"])
        whole = sorted((label, value) for label, value, cut in pairs if not cut)
        wanted = sorted({(labels[column], value) for column, value in leaves if len(value) <= CUT})
        if sorted(set(whole)) != wanted:
            problems.append(f"cohort {at}: the readback's pairs are not the written pairs")
        cuts = [(label, value) for label, value, cut in pairs if cut]
        for column, value in leaves:
            if len(value) > CUT and (labels[column], value[:CUT]) not in cuts:
                problems.append(f"cohort {at}: {column}'s value is not cut at its first {CUT}")
    return problems


def _paths(entry: dict[str, Any], keep: Callable[[str], bool], prefix: str = "") -> set[str]:
    return {
        f"{prefix}/cohorts/{NAMES[c]}/all/{i}/values/0"
        for c, leaves in enumerate(_cohorts(entry))
        for i, (_, value) in enumerate(leaves)
        if keep(value)
    }


def _refusals(answer: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(r["code"], r["path"]) for r in answer.get("refusals", [])]


@dataclass(frozen=True)
class Served:
    post: Post
    labels: dict[str, str]
    release: Release


@pytest.fixture(scope="module")
def served() -> Iterator[Served]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = config_of(root, [])
        store = Store(config.storage.data)
        World(root, store).publish("orchard", orchard_files(rows=40, harvests=60))
        services = services_of(config, store, PackRegistry((), core_version="0.0.1"))
        policy = Policy.of(config, csrf_key=b"k" * 32)
        app = create_app(policy, services, tools=tools_of(config, store, None))
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:

            def post(tool: str, body: bytes) -> httpx.Response:
                return client.post(
                    f"/api/tools/{tool}",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

            described = post("describe_dataset", b'{"dataset":"orchard"}').json()
            labels = {
                column["id"]: column["label"]["data"]
                for table in described["tables"]
                for column in table["columns"]
            }
            release = store.outline(store.resolve("orchard").manifest)
            yield Served(post, labels, release)
        store.close()


def _rules() -> list[dict[str, Any]]:
    return json.loads(RULES.read_text(encoding="utf-8"))["rows"]


def _named(name: str) -> dict[str, Any]:
    return next(entry for entry in _entries() if entry["name"] == name)


def _real(served: Served, name: str) -> tuple[dict[str, Any], Verdict]:
    entry = _named(name)
    return entry, _verdict(entry, _facts(entry, served.release))


def test_the_corpus_holds_every_class_and_the_limits_edges() -> None:
    entries = _entries()
    classes = {entry["class"] for entry in entries}
    assert classes == {"A", "B", "C", "D", "D'", "E", "F"}
    measures = {entry["measure"] for entry in entries}
    assert {MAX_DOCUMENT_BYTES, MAX_DOCUMENT_BYTES + 1} <= measures
    sizes = {entry["bytes"] for entry in entries}
    assert {MAX_BODY_BYTES, MAX_BODY_BYTES + 1} <= sizes
    lengths = {len(value) for entry in entries for leaves in _cohorts(entry) for _, value in leaves}
    assert {MAX_STRING - 1, MAX_STRING, MAX_STRING + 1} <= lengths
    written = {len(leaves) for entry in entries for leaves in _cohorts(entry)}
    assert {MAX_LEAVES, MAX_LEAVES + 1, MAX_CLAUSES, MAX_CLAUSES + 1} <= written
    assert {MAX_VARIABLES, MAX_VARIABLES + 1} <= {len(entry["view"]) for entry in entries}
    seeded = [entry for entry in entries if entry["name"].startswith("seeded ")]
    assert all(leaves for entry in seeded for leaves in entry["cohorts"])
    assert 2 * sum(entry["class"] == "A" for entry in seeded) >= len(seeded)
    by_name = {entry["name"]: entry["class"] for entry in entries}
    assert by_name["a cohort of MAX_LEAVES distinct leaves"] == "A"
    assert by_name["a cohort of MAX_LEAVES + 1 distinct leaves"] == "E"
    assert by_name["a column twice in one view"] == "A"


def test_each_body_is_the_oracle_s_and_its_class_the_server_s(served: Served) -> None:
    """The web's ``classOf`` is held to the class the server's own code gives (``_verdict``)."""
    for entry in _entries():
        document = _document(entry)
        body = _js_bytes({"document": document})
        assert hashlib.sha256(body).hexdigest() == entry["sha256"], entry["name"]
        assert len(body) == entry["bytes"], entry["name"]
        if entry["body"] is not None:
            assert body.decode("utf-8") == entry["body"], entry["name"]
        assert _measure(document) == entry["measure"], entry["name"]
        found = _verdict(entry, _facts(entry, served.release))
        assert found.kind == entry["class"], entry["name"]


def _failures(
    post: Post, labels: dict[str, str], entry: dict[str, Any], verdict: Verdict, through: bool
) -> list[str]:
    """Where the server's answers to an entry are not those of its class (none: they are)."""
    failures: list[str] = []
    body = _js_bytes({"document": _document(entry)})
    kind = verdict.kind
    name = entry["name"]
    response = post("validate_document", body)
    answer = response.json()
    refusals = _refusals(answer)
    paths = {path for _, path in refusals}
    codes = {code for code, _ in refusals}
    if "UNKNOWN_PARAMETER" in codes:
        failures.append(f"{name}: UNKNOWN_PARAMETER")
    if kind == "A":
        if response.status_code != 200:
            failures.append(f"{name}: A {response.status_code}")
            return failures
        failures += [f"{name}: A {p}" for p in _a_problems(entry, answer, labels)]
        if through:
            for tool in ("count_cohort", "run_analysis"):
                other = post(tool, body)
                if other.status_code != 200:
                    failures.append(f"{name}: A {tool} {other.status_code}")
    elif kind in ("B", "E", "F"):
        wanted = verdict.paths or frozenset()
        if response.status_code != 200 or answer.get("valid") is not False:
            failures.append(f"{name}: {kind} {response.status_code} {answer.get('valid')}")
        elif codes != {"LIMIT_EXCEEDED"} or paths != wanted:
            failures.append(f"{name}: {kind} refusals {refusals[:3]}")
        for tool in ("count_cohort", "run_analysis"):
            other = post(tool, body)
            found = {path for _, path in _refusals(other.json())}
            if (
                other.status_code != 422
                or {code for code, _ in _refusals(other.json())} != {"LIMIT_EXCEEDED"}
                or not found
                or not found <= wanted
            ):
                failures.append(f"{name}: {kind} {tool} {other.status_code}")
    elif kind == "C":
        wanted = verdict.paths or frozenset()
        if response.status_code != 422 or codes != {"INVALID_JSON"}:
            failures.append(f"{name}: C {response.status_code} {refusals[:3]}")
        elif not paths or not paths <= wanted:
            failures.append(f"{name}: C at {sorted(map(str, paths))[:3]}")
    elif kind == "D":
        if response.status_code != 200 or answer.get("valid") is not False:
            failures.append(f"{name}: D {response.status_code}")
        elif refusals != [("LIMIT_EXCEEDED", None)]:
            failures.append(f"{name}: D refusals {refusals}")
        for tool in ("count_cohort", "run_analysis"):
            other = post(tool, body)
            if other.status_code != 422 or _refusals(other.json()) != [("LIMIT_EXCEEDED", None)]:
                failures.append(f"{name}: D {tool} {other.status_code}")
    else:
        for tool in ("validate_document", "count_cohort", "run_analysis"):
            other = response if tool == "validate_document" else post(tool, body)
            if other.status_code != 413 or {code for code, _ in _refusals(other.json())} != {
                "LIMIT_EXCEEDED"
            }:
                failures.append(f"{name}: D' {tool} {other.status_code}")
    return failures


def test_the_server_s_verdict_on_every_entry(served: Served) -> None:
    failures: list[str] = []
    counted = 0
    for index, entry in enumerate(_entries()):
        name = entry["name"]
        verdict = _verdict(entry, _facts(entry, served.release))
        through = index % 10 == 0 or not name.startswith(("A value", "seeded"))
        counted += verdict.kind == "A" and through
        failures += _failures(served.post, served.labels, entry, verdict, through)
    assert failures == []
    assert counted >= 20


def _bound_problems(rows: list[dict[str, Any]]) -> list[str]:
    """Where the table's bound rows are not the bounds the server's models state (``MODEL_BOUNDS``):
    a bound with no row, a row of no bound, a row of another value."""
    found = {(row["position"], row["keyword"]): row for row in rows if row["kind"] == "bound"}
    problems = [f"no row: {key}" for key in MODEL_BOUNDS if key not in found]
    problems += [f"no bound: {key}" for key in found if key not in MODEL_BOUNDS]
    problems += [
        f"value: {key}"
        for key, bound in MODEL_BOUNDS.items()
        if found.get(key, {}).get("value") != bound and key in found
    ]
    return problems


def test_every_bound_of_the_server_s_models_is_a_row_with_an_edge_pair_or_a_reason(
    served: Served,
) -> None:
    """The bounds the server's models state at every position the seam writes (``MODEL_BOUNDS``)
    are exactly the table's bound rows; each row is an edge pair of the corpus (an entry at the
    bound and one past it, of the classes the row names) when the seam reaches it, or says why it
    does not (the table's ``reach``); the limits of the caps ``measure`` and the loader count are
    rows too."""
    rows = _rules()
    bounds = {(row["position"], row["keyword"]): row for row in rows if row["kind"] == "bound"}
    assert _bound_problems(rows) == []
    measured = [row for row in rows if row["kind"] == "limit"]
    for row in [*bounds.values(), *measured]:
        if "limit" in row:
            assert getattr(limits, row["limit"]) == row["value"], row["id"]
        assert row["reach"] in ("edge", "never", "unreached"), row["id"]
        assert row["rule"], row["id"]
        assert row["cells"], row["id"]
    assert {row["reach"] for row in [*bounds.values(), *measured]} == {"edge", "never", "unreached"}
    assert {row["limit"] for row in measured} >= {
        "MAX_BODY_BYTES",
        "MAX_DOCUMENT_BYTES",
        "MAX_LEAVES",
        "MAX_CLAUSE_DEPTH",
        "MAX_DEPTH",
        "MAX_VALUES",
    }
    verdicts = _verdicts(served)
    assert _edge_problems([*bounds.values(), *measured], verdicts) == []


def test_the_table_s_bound_rows_are_held_to_the_models(served: Served) -> None:
    """The check of the bound rows fails on a bound with no row, a row of no bound and a row of
    another value (a control over the table with each edit)."""
    rows = _rules()
    assert _bound_problems(rows) == []
    first = next(row for row in rows if row["kind"] == "bound")
    key = (first["position"], first["keyword"])
    assert _bound_problems([row for row in rows if row is not first]) == [f"no row: {key}"]
    stale = {**first, "id": "stale", "position": "/nowhere"}
    assert _bound_problems([*rows, stale]) == [f"no bound: {('/nowhere', first['keyword'])}"]
    changed = [{**row, "value": row["value"] + 1} if row is first else row for row in rows]
    assert _bound_problems(changed) == [f"value: {key}"]


Judged = list[tuple[dict[str, Any], Facts, Verdict]]


def _verdicts(served: Served) -> Judged:
    return [
        (entry, facts, _verdict(entry, facts))
        for entry in _entries()
        for facts in [_facts(entry, served.release)]
    ]


def _at(row: dict[str, Any], judged: Judged, value: int, kinds: list[str]) -> list[str]:
    """The entries whose quantity of the row's bound is ``value``, that hold what the row needs, of
    a class among ``kinds``."""
    quantity = QUANTITIES[row["quantity"]]
    needs = [NEEDS[name] for name in row.get("needs", [])]
    return [
        entry["name"]
        for entry, facts, verdict in judged
        if quantity(entry, facts) == value
        and all(need(entry, facts, row) for need in needs)
        and verdict.kind in kinds
    ]


def _edge_problems(rows: list[dict[str, Any]], judged: Judged) -> list[str]:
    """The rows the seam reaches that lack an entry at their bound, or one past it."""
    problems: list[str] = []
    for row in rows:
        if row["reach"] != "edge":
            if not row["why"]:
                problems.append(f"{row['id']}: no reason")
            continue
        if not _at(row, judged, row["value"], row["at"]):
            problems.append(f"{row['id']}: no entry at the bound")
        if not _at(row, judged, row["value"] + 1, row["past"]):
            problems.append(f"{row['id']}: no entry one past it")
    return problems


def test_a_bound_without_its_edge_pair_is_a_problem(served: Served) -> None:
    """The check of the edge pairs fails when a pair is missing (a control: each edge row, with the
    entries that serve as its bound, or as the entry one past it, taken out of the corpus)."""
    rows = [row for row in _rules() if row["kind"] in ("bound", "limit")]
    judged = _verdicts(served)
    edges = [row for row in rows if row["reach"] == "edge"]
    assert len(edges) == 7
    for row in edges:
        for side, value, kinds in (
            ("at the bound", row["value"], row["at"]),
            ("one past it", row["value"] + 1, row["past"]),
        ):
            serving = set(_at(row, judged, value, kinds))
            assert serving, (row["id"], side)
            rest = [item for item in judged if item[0]["name"] not in serving]
            problems = _edge_problems(rows, rest)
            assert f"{row['id']}: no entry {side}" in problems, (row["id"], side)
    # A row with no reason, that the seam does not reach, is a problem too.
    quiet = [{**row, "why": ""} if row["reach"] != "edge" else row for row in rows]
    assert any(problem.endswith("no reason") for problem in _edge_problems(quiet, judged))


Quantity = Callable[[dict[str, Any], Facts], int | None]
QUANTITIES: dict[str, Quantity] = {
    "body_bytes": lambda entry, facts: facts.size,
    "document_measure": lambda entry, facts: facts.measure,
    "longest_value": lambda entry, facts: max(
        (len(value) for leaves in _cohorts(entry) for _, value in leaves), default=None
    ),
    "written_clauses": lambda entry, facts: max(facts.written, default=None),
    "leaves": lambda entry, facts: max(facts.leaves.values(), default=None),
    "view_columns": lambda entry, facts: facts.columns,
}
"""What a row's bound counts, of an entry (``None``: nothing of it)."""
BOUNDS_OF: dict[str, int] = {
    "body_bytes": MAX_BODY_BYTES,
    "document_measure": MAX_DOCUMENT_BYTES,
    "longest_value": STRING,
    "written_clauses": CLAUSES,
    "leaves": MAX_LEAVES,
    "view_columns": VARIABLES,
}
"""The bound of each quantity."""
Needs = Callable[[dict[str, Any], Facts, dict[str, Any]], bool]


def _alone(entry: dict[str, Any], facts: Facts, row: dict[str, Any]) -> bool:
    """Whether no quantity but the row's own is over its bound in the entry (the entry tells this
    bound's pair, not another's)."""
    for name, quantity in QUANTITIES.items():
        found = quantity(entry, facts)
        if name != row["quantity"] and found is not None and found > BOUNDS_OF[name]:
            return False
    return True


NEEDS: dict[str, Needs] = {
    "a_value_over_the_string_bound": lambda entry, facts, row: any(
        len(value) > STRING for leaves in _cohorts(entry) for _, value in leaves
    ),
    "a_down_step_counted": lambda entry, facts, row: any(
        facts.leaves.get(NAMES[at], 0) > len(set(leaves))
        for at, leaves in enumerate(_cohorts(entry))
    ),
    "alone": _alone,
}
"""What an edge pair's entries must also hold, to tell the bound's pair from another's and the
mechanisms of a class apart."""


def test_a_b_document_is_refused_by_count_cohort_at_its_value(served: Served) -> None:
    entry = next(e for e in _entries() if e["class"] == "B")
    answer = served.post("count_cohort", _js_bytes({"document": _document(entry)}))
    assert answer.status_code == 422
    found = _refusals(answer.json())
    assert {code for code, _ in found} == {"LIMIT_EXCEEDED"}
    assert {path for _, path in found} <= _paths(entry, lambda value: len(value) > MAX_STRING)


def _value_entry(value: str) -> dict[str, Any]:
    return next(
        entry
        for entry in _entries()
        if entry["name"].split(" value ")[0] in ("A", "B", "C")
        and [[(c, _expand(s)) for c, s in leaves] for leaves in entry["cohorts"]]
        == [[("trees.variety", value)]]
    )


def _answering(post: Post, **replaced: httpx.Response) -> Post:
    """``post``, but a tool in ``replaced`` answers as given (a control's stub)."""
    return lambda tool, body: replaced[tool] if tool in replaced else post(tool, body)


TOOLS = ("validate_document", "count_cohort", "run_analysis")
CHECKED = {
    "A": TOOLS,
    "B": TOOLS,
    "C": ("validate_document",),
    "D": TOOLS,
    "D'": TOOLS,
    "E": TOOLS,
    "F": TOOLS,
}
"""The tools each class's branch of ``_failures`` reads the answer of."""


def _changed(post: Post, tool: str, body: bytes, how: str) -> Post:
    """``post``, but ``tool`` answers what it answered with one thing changed: ``status`` (the body
    as it was), ``code`` (every refusal's) or ``path`` (every refusal's)."""
    real = post(tool, body)
    data = real.json()
    status = real.status_code
    if how == "status":
        status = 422 if status == 200 else 200
    refusals = data.get("refusals", [])
    if how == "code":
        refusals = [{**refusal, "code": "NOT_SUPPORTED"} for refusal in refusals]
    if how == "path":
        refusals = [{**refusal, "path": "/x"} for refusal in refusals]
    changed = {**data, "refusals": refusals} if "refusals" in data else data
    return _answering(post, **{tool: httpx.Response(status, json=changed)})


READ = {
    "status": CHECKED,
    "code": {kind: tools for kind, tools in CHECKED.items() if kind != "A"},
    "path": {"B": TOOLS, "C": ("validate_document",), "D": TOOLS, "E": TOOLS, "F": TOOLS},
}
"""What each class's branch reads of each tool's answer: its status and refusals."""


def test_each_class_s_branch_fails_what_it_does_not_hold(served: Served) -> None:
    """A branch of ``_failures`` that cannot fail is no check (m5 of round 2): an entry of class A
    classed as each other class fails that branch; an entry of each class classed A, or at the wrong
    paths, or answered by any tool with another status, code or path (the rest of the answer as it
    was), fails too."""
    post, labels = served.post, served.labels
    plain, plain_verdict = _real(served, "two cohorts")
    assert plain_verdict == Verdict("A", None)
    classes = {
        "B": "a B value among MAX_LEAVES leaves",
        "C": "C value 0",
        "D": "a document of MAX_DOCUMENT_BYTES + 1",
        "D'": "a body of MAX_BODY_BYTES + 1",
        "E": "a cohort of MAX_LEAVES + 1 distinct leaves",
        "F": "MAX_VARIABLES + 1 view columns",
    }
    entries = {"A": (plain, plain_verdict)}
    for kind, name in classes.items():
        entry, verdict = _real(served, name)
        assert verdict.kind == kind, name
        entries[kind] = (entry, verdict)
    for kind, (entry, verdict) in entries.items():
        assert _failures(post, labels, entry, verdict, True) == [], kind
        if kind != "A":
            # An entry of class A, classed as this class.
            wrongly = _failures(post, labels, plain, Verdict(kind, verdict.paths), True)
            assert wrongly != [], kind
            assert all(failure.startswith(f"{plain['name']}: {kind}") for failure in wrongly), kind
            # An entry of this class, classed A.
            assert _failures(post, labels, entry, Verdict("A", None), True) != [], kind
        # An entry of this class, at paths of another class.
        if verdict.paths is not None:
            there = _failures(post, labels, entry, Verdict(kind, frozenset({"/nowhere"})), True)
            assert there != [], kind
            assert any(" refusals " in f or " at " in f for f in there), (kind, there)
        # Each tool whose answer the branch reads, answered with another status, code or path.
        body = _js_bytes({"document": _document(entry)})
        for how, reads in READ.items():
            for tool in reads.get(kind, ()):
                skewed = _failures(_changed(post, tool, body, how), labels, entry, verdict, True)
                assert skewed != [], (kind, tool, how)
                if tool != "validate_document" and kind != "D'":
                    assert [f for f in skewed if tool in f] != [], (kind, tool, how)
    # A valid answer to another document (two leaves' values swapped): not the written pairs.
    swapped = json.loads(json.dumps(plain))
    first = swapped["cohorts"][0]
    first[0][1], first[1][1] = first[1][1], first[0][1]
    other = post("validate_document", _js_bytes({"document": _document(swapped)})).json()
    stubbed = httpx.Response(200, json=other)
    wrong = _failures(
        _answering(post, validate_document=stubbed), labels, plain, plain_verdict, True
    )
    assert [failure for failure in wrong if failure.startswith(f"{plain['name']}: A cohort")] != []
    # A valid answer that still names a parameter nobody gave (a `$x` taken for a reference).
    real = post("validate_document", _js_bytes({"document": _document(plain)})).json()
    reference = {"code": "UNKNOWN_PARAMETER", "path": "/cohorts/first/all/0/values/0"}
    stubbed = httpx.Response(200, json={**real, "refusals": [reference]})
    named = _failures(
        _answering(post, validate_document=stubbed), labels, plain, plain_verdict, True
    )
    assert f"{plain['name']}: UNKNOWN_PARAMETER" in named
    # A count refused at a path of no cohort (an E answered elsewhere).
    entry, verdict = entries["E"]
    nowhere = httpx.Response(422, json={"refusals": [{"code": "LIMIT_EXCEEDED", "path": "/x"}]})
    skewed = _failures(_answering(post, count_cohort=nowhere), labels, entry, verdict, True)
    assert [f for f in skewed if "count_cohort" in f] != []


def test_the_needs_of_an_edge_pair_tell_what_they_name() -> None:
    """The conditions an edge pair's entries must hold (``NEEDS``) are no vacuous ones, over made
    entries and facts: the same child leaf three times is two leaves of the server's count, not
    three and not one."""
    child = {"cohorts": [[["harvests.grade", "A"]] * 3], "view": ["trees.variety"]}
    own = {"cohorts": [[["trees.variety", "a"], ["trees.variety", "b"]]], "view": ["trees.variety"]}
    long = {"cohorts": [[["trees.variety", "a" * (STRING + 1)]]], "view": ["trees.variety"]}
    facts = Facts(size=0, measure=0, written=[3], leaves={"first": 2}, columns=1)
    row = {"quantity": "view_columns"}
    down = NEEDS["a_down_step_counted"]
    assert down(child, facts, row)
    assert not down(own, Facts(0, 0, [2], {"first": 2}, 1), row)
    assert not down(child, Facts(0, 0, [3], {"first": 1}, 1), row)
    assert NEEDS["a_value_over_the_string_bound"](long, facts, row)
    assert not NEEDS["a_value_over_the_string_bound"](own, facts, row)
    alone = NEEDS["alone"]
    assert alone(own, Facts(10, 10, [2], {"first": 2}, MAX_VARIABLES + 1), row)
    assert not alone(own, Facts(10, 10, [2], {"first": MAX_LEAVES + 1}, 1), row)
    assert not alone(own, Facts(MAX_BODY_BYTES + 1, 10, [2], {"first": 2}, 1), row)
    assert not alone(own, Facts(10, MAX_DOCUMENT_BYTES + 1, [2], {"first": 2}, 1), row)
    assert not alone(own, Facts(10, 10, [CLAUSES + 1], {"first": 2}, 1), row)
    assert not alone(long, Facts(10, 10, [1], {"first": 1}, 1), row)
    assert not alone(
        own, Facts(10, 10, [2], {"first": 2}, MAX_VARIABLES + 1), {"quantity": "leaves"}
    )
    # An edge pair's entries are those that hold every need of the row, and only those.
    held = {**child, "name": "child"}
    plain = {**own, "name": "own"}
    judged: Judged = [
        (held, Facts(0, 0, [3], {"first": 2}, 1), Verdict("A", None)),
        (plain, Facts(0, 0, [2], {"first": 2}, 1), Verdict("A", None)),
    ]
    needing = {"quantity": "leaves", "needs": ["a_down_step_counted"]}
    assert _at(needing, judged, 2, ["A"]) == ["child"]
    assert _at({**needing, "needs": []}, judged, 2, ["A"]) == ["child", "own"]
    assert _at(needing, judged, 2, ["E"]) == []
    assert _at(needing, judged, 3, ["A"]) == []


def _first_value_node(node: Any) -> dict[str, Any] | None:
    """The first value leaf of a canonical form."""
    if isinstance(node, dict):
        if node.get("kind") == "value":
            return node
        for member in node.values():
            found = _first_value_node(member)
            if found is not None:
                return found
    if isinstance(node, list):
        for member in node:
            found = _first_value_node(member)
            if found is not None:
                return found
    return None


def test_each_check_of_class_a_fires_alone(served: Served) -> None:
    """The three checks of ``_a_problems`` each fire on an answer wrong in that alone (m2 of round
    3): the form's value leaves, the readback's pairs and the cut of a long value, each over a real
    answer with one thing changed."""
    post, labels = served.post, served.labels
    short = _named("two cohorts")
    long = _value_entry("a" * (CUT + 1))
    answers = {
        name: post("validate_document", _js_bytes({"document": _document(entry)})).json()
        for name, entry in (("short", short), ("long", long))
    }
    assert _a_problems(short, answers["short"], labels) == []
    assert _a_problems(long, answers["long"], labels) == []
    # The form's value leaf wrong, the readback right.
    form = json.loads(json.dumps(answers["short"]))
    node = _first_value_node(form["cohorts"][0]["form"])
    assert node is not None
    node["values"][0] = "not what was written"
    assert _a_problems(short, form, labels) == [
        "cohort 0: the form's value leaves are not the written pairs"
    ]
    # The readback's pair wrong, the form right.
    readback = json.loads(json.dumps(answers["short"]))
    segments = readback["cohorts"][0]["readback"]
    at = next(
        i
        for i, segment in enumerate(segments)
        if segment.get("text") == " is " and "data" in segments[i + 1]
    )
    segments[at + 1]["data"] = "not what was written"
    assert _a_problems(short, readback, labels) == [
        "cohort 0: the readback's pairs are not the written pairs"
    ]
    # A long value cut at 199 code points, the form and the other pairs right.
    cut = json.loads(json.dumps(answers["long"]))
    for segment in cut["cohorts"][0]["readback"]:
        if segment.get("truncated"):
            segment["data"] = segment["data"][: CUT - 1]
    assert _a_problems(long, cut, labels) == [
        f"cohort 0: trees.variety's value is not cut at its first {CUT}"
    ]


def test_the_controls_fail_the_oracles(served: Served) -> None:
    post, labels = served.post, served.labels
    # The corpus written without the $$ escape: $x is a parameter reference.
    dollar = _value_entry("$x")
    unescaped = post(
        "validate_document", _js_bytes({"document": _document(dollar, literal=lambda v: v)})
    ).json()
    assert _a_problems(dollar, unescaped, labels) != []
    assert ("UNKNOWN_PARAMETER", "/cohorts/first/all/0/values/0") in _refusals(unescaped)
    # A $ + 4,096 entry, and a U+1FFFE entry, classed (A).
    for value in ("$" + "a" * MAX_STRING, "a\U0001fffe"):
        entry = _value_entry(value)
        response = post("validate_document", _js_bytes({"document": _document(entry)}))
        answer = response.json() if response.status_code == 200 else {}
        assert _a_problems(entry, answer, labels) != [], value[:3]
    # A cohort of more than MAX_LEAVES distinct leaves, classed (A): the server refuses it.
    entry, verdict = _real(served, "a cohort of MAX_LEAVES + 1 distinct leaves")
    answer = post("validate_document", _js_bytes({"document": _document(entry)})).json()
    assert answer["valid"] is False
    assert _a_problems(entry, answer, labels) != []
    assert verdict.kind == "E"
    # A cohort of MAX_LEAVES / 2 + 1 child-table leaves is no 33 of 64: a down step is a leaf of its
    # own, so the server counts 66 (the count of distinct leaves is no verdict).
    entry, verdict = _real(served, "MAX_LEAVES / 2 + 1 distinct leaves of a child table")
    facts = _facts(entry, served.release)
    assert len(set(_cohorts(entry)[0])) == MAX_LEAVES // 2 + 1 <= MAX_LEAVES
    assert facts.leaves == {"first": 2 * (MAX_LEAVES // 2 + 1)}
    assert verdict.kind == "E"
    answer = post("validate_document", _js_bytes({"document": _document(entry)})).json()
    assert answer["valid"] is False
    assert _a_problems(entry, answer, labels) != []
    # ... and a parent's leaf counts once: 64 of them are the bound, not past it.
    entry, verdict = _real(served, "MAX_LEAVES distinct leaves of a parent table")
    assert _facts(entry, served.release).leaves == {"first": MAX_LEAVES}
    assert verdict.kind == "A"
    # Two leaves' values swapped: the same values, each at another position.
    entry = _named("two cohorts")
    swapped = json.loads(json.dumps(entry))
    first = swapped["cohorts"][0]
    first[0][1], first[1][1] = first[1][1], first[0][1]
    answer = post("validate_document", _js_bytes({"document": _document(swapped)})).json()
    assert answer["valid"] is True
    assert _a_problems(entry, answer, labels) != []
    assert _a_problems(swapped, answer, labels) == []
