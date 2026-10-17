"""The server's verdict on the documents the web's seam writes (D432; A5.3 of the seam's plan,
minors 2 and 9 of its round 3).

``web/tests/fixtures/builder-documents.json`` is written by the web's property test
(``web/tests/unit/document-property.test.tsx``) from bodies its ``validateDraft`` sent: each entry
holds the typed values by position (cohort, leaf, column id; a long value as a run), the body's
SHA-256, its length, the server's measure of its document, and the body itself when it is small.
Here each body is rebuilt by an oracle of this test's own (the document's template and §7.1's
literal) and held to the web's digest, byte for byte, then POSTed as those bytes to
``/api/tools/validate_document`` of a real ``create_app`` over the orchard. Its class comes from its
typed values and its size, by the server's limits (``aibi.core.schema.limits``):

- **D'** a body over ``MAX_BODY_BYTES``: 413 ``LIMIT_EXCEEDED``, at every tool;
- **C** a value holding a noncharacter (U+FDD0–U+FDEF, or ``cp & 0xFFFE == 0xFFFE`` on any plane)
  or a surrogate: 422, every refusal ``INVALID_JSON`` at
  ``/document/cohorts/<name>/all/<i>/values/0`` of such a value;
- **D** a document over ``MAX_DOCUMENT_BYTES`` by the server's measure (its JSON as
  ``json.dumps`` writes it): validate 200, ``valid: false``, one ``LIMIT_EXCEEDED`` with no path;
  count and run 422, the same;
- **B** a value of more than ``MAX_STRING`` code points (unescaped), in a document under the
  limit: 200, ``valid: false``, every refusal ``LIMIT_EXCEEDED`` at the value's
  ``/cohorts/<name>/all/<i>/values/0``, one at each such value;
- **A** otherwise: 200, ``valid: true``, and by position, for each cohort: the set of
  ``(column, values[0])`` of every value node of its canonical form equals the written pairs,
  unescaped; and in its readback each data token after a ``" is "`` text, paired with the first
  data token after the nearest ``"the "`` before it (the column's label), gives exactly the written
  ``(label, value)`` pairs, a value of more than 200 code points as a token marked ``truncated``
  whose text is exactly its first 200 code points.

Never a refusal at an id position, never ``UNKNOWN_PARAMETER``. A tenth of (A), and the
structural cases, also pass through ``count_cohort`` and ``run_analysis`` (200), and a (B) through
``count_cohort`` (422 ``LIMIT_EXCEEDED`` at the value). The controls: the corpus written without
the ``$$`` escape fails on ``$x``; a ``$`` + 4,096 entry classed (A) fails; a U+1FFFE entry
classed (A) fails; and a body whose two leaves' values are swapped fails (A) by position.
"""

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.core.api.served_builders import config_of
from tests.core.catalog.world import World, orchard_files

from aibi.core.api.app import create_app
from aibi.core.api.protection import Policy
from aibi.core.api.serve import services_of, tools_of
from aibi.core.schema.limits import MAX_BODY_BYTES, MAX_DOCUMENT_BYTES, MAX_STRING
from aibi.core.schema.pack_api import PackRegistry
from aibi.core.store.store import Store

CORPUS = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "builder-documents.json"
)
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


def _class(values: list[str], measure: int, size: int) -> str:
    if size > MAX_BODY_BYTES:
        return "D'"
    if any(_refused_point(ord(ch)) for value in values for ch in value):
        return "C"
    if measure > MAX_DOCUMENT_BYTES:
        return "D"
    return "B" if any(len(value) > MAX_STRING for value in values) else "A"


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


@pytest.fixture(scope="module")
def served() -> Iterator[tuple[Post, dict[str, str]]]:
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
            yield post, labels
        store.close()


def test_the_corpus_holds_every_class_and_the_limits_edges() -> None:
    entries = _entries()
    classes = {entry["class"] for entry in entries}
    assert classes == {"A", "B", "C", "D", "D'"}
    measures = {entry["measure"] for entry in entries}
    assert {MAX_DOCUMENT_BYTES, MAX_DOCUMENT_BYTES + 1} <= measures
    sizes = {entry["bytes"] for entry in entries}
    assert {MAX_BODY_BYTES, MAX_BODY_BYTES + 1} <= sizes
    lengths = {len(value) for entry in entries for leaves in _cohorts(entry) for _, value in leaves}
    assert {MAX_STRING - 1, MAX_STRING, MAX_STRING + 1} <= lengths


def test_each_body_is_the_oracle_s_and_its_class_the_server_s() -> None:
    for entry in _entries():
        document = _document(entry)
        body = _js_bytes({"document": document})
        assert hashlib.sha256(body).hexdigest() == entry["sha256"], entry["name"]
        assert len(body) == entry["bytes"], entry["name"]
        if entry["body"] is not None:
            assert body.decode("utf-8") == entry["body"], entry["name"]
        assert _measure(document) == entry["measure"], entry["name"]
        values = [value for leaves in _cohorts(entry) for _, value in leaves]
        assert _class(values, _measure(document), len(body)) == entry["class"], entry["name"]


def test_the_server_s_verdict_on_every_entry(served: tuple[Post, dict[str, str]]) -> None:
    post, labels = served
    failures: list[str] = []
    counted = 0
    for index, entry in enumerate(_entries()):
        body = _js_bytes({"document": _document(entry)})
        kind = entry["class"]
        name = entry["name"]
        response = post("validate_document", body)
        answer = response.json()
        refusals = _refusals(answer)
        if any(code == "UNKNOWN_PARAMETER" for code, _ in refusals):
            failures.append(f"{name}: UNKNOWN_PARAMETER")
        if kind == "A":
            if response.status_code != 200:
                failures.append(f"{name}: {response.status_code}")
                continue
            failures += [f"{name}: {p}" for p in _a_problems(entry, answer, labels)]
            if index % 10 == 0 or not name.startswith(("A value", "seeded")):
                counted += 1
                for tool in ("count_cohort", "run_analysis"):
                    other = post(tool, body)
                    if other.status_code != 200:
                        failures.append(f"{name}: {tool} {other.status_code}")
        elif kind == "B":
            wanted = _paths(entry, lambda value: len(value) > MAX_STRING)
            if response.status_code != 200 or answer.get("valid") is not False:
                failures.append(f"{name}: B {response.status_code} {answer.get('valid')}")
            elif {code for code, _ in refusals} != {"LIMIT_EXCEEDED"} or {
                path for _, path in refusals
            } != wanted:
                failures.append(f"{name}: B refusals {refusals[:3]}")
        elif kind == "C":
            wanted = _paths(
                entry, lambda value: any(_refused_point(ord(ch)) for ch in value), "/document"
            )
            found = {path for _, path in refusals}
            if response.status_code != 422 or {code for code, _ in refusals} != {"INVALID_JSON"}:
                failures.append(f"{name}: C {response.status_code} {refusals[:3]}")
            elif not found or not found <= wanted:
                failures.append(f"{name}: C at {sorted(map(str, found))[:3]}")
        elif kind == "D":
            if response.status_code != 200 or answer.get("valid") is not False:
                failures.append(f"{name}: D {response.status_code}")
            elif refusals != [("LIMIT_EXCEEDED", None)]:
                failures.append(f"{name}: D refusals {refusals}")
            for tool in ("count_cohort", "run_analysis"):
                other = post(tool, body)
                if other.status_code != 422 or _refusals(other.json()) != [
                    ("LIMIT_EXCEEDED", None)
                ]:
                    failures.append(f"{name}: D {tool} {other.status_code}")
        else:
            for tool in ("validate_document", "count_cohort", "run_analysis"):
                other = response if tool == "validate_document" else post(tool, body)
                codes = {code for code, _ in _refusals(other.json())}
                if other.status_code != 413 or codes != {"LIMIT_EXCEEDED"}:
                    failures.append(f"{name}: D' {tool} {other.status_code}")
    assert failures == []
    assert counted >= 20


def test_a_b_document_is_refused_by_count_cohort_at_its_value(
    served: tuple[Post, dict[str, str]],
) -> None:
    post, _ = served
    entry = next(e for e in _entries() if e["class"] == "B")
    answer = post("count_cohort", _js_bytes({"document": _document(entry)}))
    assert answer.status_code == 422
    found = _refusals(answer.json())
    assert {code for code, _ in found} == {"LIMIT_EXCEEDED"}
    assert {path for _, path in found} <= _paths(entry, lambda value: len(value) > MAX_STRING)


def _named(name: str) -> dict[str, Any]:
    return next(entry for entry in _entries() if entry["name"] == name)


def _value_entry(value: str) -> dict[str, Any]:
    return next(
        entry
        for entry in _entries()
        if entry["name"].split(" value ")[0] in ("A", "B", "C")
        and [[(c, _expand(s)) for c, s in leaves] for leaves in entry["cohorts"]]
        == [[("trees.variety", value)]]
    )


def test_the_controls_fail_the_oracles(served: tuple[Post, dict[str, str]]) -> None:
    post, labels = served
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
    # Two leaves' values swapped: the same values, each at another position.
    entry = _named("two cohorts")
    swapped = json.loads(json.dumps(entry))
    first = swapped["cohorts"][0]
    first[0][1], first[1][1] = first[1][1], first[0][1]
    answer = post("validate_document", _js_bytes({"document": _document(swapped)})).json()
    assert answer["valid"] is True
    assert _a_problems(entry, answer, labels) != []
    assert _a_problems(swapped, answer, labels) == []
