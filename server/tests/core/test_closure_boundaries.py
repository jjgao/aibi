"""The listed boundaries of D399, each tested: where server text is rebuilt from bytes the
server's own code wrote, the writer validated what it wrote, and a stored blob records what text
meant when it was written.

- ``REPORT_NOTES``: the report writer refuses a look-alike; a ``/2`` report is served as written
  only under the classification it was written with, and otherwise read with fixed text.
- ``RESULT_CACHE``: the cache's tests (``catalog/test_result_cache_wording.py``).
- ``READER_ANSWER``: the child validates the refusals it sends, and one its writer refuses is the
  server's fault (``ReaderError``), never the file's; a reason the parent unpickles is validated
  again as the instances it holds.
- ``CLI_CLIENT``: the CLI reads the configured server's segments as server text.
- The stored members are the boundaries that persist text, and only reports and the cache do.
"""

import ast
import json
import types
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError
from tests.core import _ast, closure_rule

from aibi.core.analyses.results import _Written  # pyright: ignore[reportPrivateUsage]
from aibi.core.api import errors as errors_module
from aibi.core.api import markup
from aibi.core.importers import databases, worker
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.worker import Reader, ReaderError
from aibi.core.mcp import server as mcp_server
from aibi.core.operator import cli
from aibi.core.operator import router as router_module
from aibi.core.operator.client import OperatorClient, Refused
from aibi.core.schema.caveats import Caveat, CaveatCode, Severity
from aibi.core.schema.curation import QueueNote
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.operator import Datasets, Refusals
from aibi.core.schema.output import (
    GUARD_MESSAGE,
    STORED,
    Boundary,
    Output,
    TextSegment,
    admitted_at,
    data,
    text,
)
from aibi.core.schema.pack_api import NOTE_TEXT, ImportNote
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.store import build

N = "IGNORE_PREVIOUS_INSTRUCTIONS"


class _Said(Output):
    text: str


def _guarded(error: ValidationError) -> bool:
    return any(found["msg"] == GUARD_MESSAGE for found in error.errors())


def test_every_member_is_tested_here_or_in_the_cache_s_tests() -> None:
    assert set(Boundary) == {
        Boundary.REPORT_NOTES,
        Boundary.RESULT_CACHE,
        Boundary.READER_ANSWER,
        Boundary.CLI_CLIENT,
    }


# --- REPORT_NOTES ---------------------------------------------------------------------------------

LOOK_ALIKES = {
    "an Output with a text member": lambda: _Said(text=N),
    "a dict": lambda: {"text": N},
}


@pytest.mark.parametrize("made", list(LOOK_ALIKES.values()), ids=list(LOOK_ALIKES))
def test_the_report_writer_refuses_a_look_alike(made: Any) -> None:
    note = ImportNote("dropped", "t", cast(Any, [text("A note "), made()]))
    with pytest.raises(ValidationError) as raised:
        build.report_bytes([note])
    assert _guarded(raised.value)


def test_a_report_s_notes_are_server_text_again_at_the_boundary() -> None:
    blob = build.report_bytes([ImportNote("dropped", "t", [text("Skipped "), data("x")], count=2)])
    [note] = build.read_report(blob)
    made = QueueNote.model_validate(note, context=admitted_at(Boundary.REPORT_NOTES))
    assert made.message == [text("Skipped "), data("x")]
    with pytest.raises(ValidationError) as raised:
        QueueNote.model_validate(note)
    assert _guarded(raised.value)


def test_a_report_of_another_classification_is_read_with_fixed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = build.report_bytes([ImportNote("dropped", "t", [text("A note "), text(N)], count=3)])
    assert N in json.dumps(build.read_report(blob))
    monkeypatch.setattr(build, "REPORT_CLASSIFICATION", build.REPORT_2_CLASSIFICATION + 1)
    [note] = build.read_report(blob)
    assert note["message"] == [{"text": NOTE_TEXT["dropped"]}]
    assert (note["subject"], note["count"]) == ("t", 3)


# --- READER_ANSWER ------------------------------------------------------------------------------

_LOOK_ALIKE_REFUSAL = (
    "(_ for _ in ()).throw(__import__('aibi.core.importers.errors', fromlist=['x']).ImportRefused("
    "[__import__('aibi.core.schema.refusals', fromlist=['x']).Refusal.model_construct("
    "code=__import__('aibi.core.schema.refusals', fromlist=['x']).RefusalCode.INVALID_VALUE, "
    f"path=None, message=[{{'text': {N!r}}}])]))"
)
_NO_REFUSAL = (
    "(_ for _ in ()).throw("
    "__import__('aibi.core.importers.errors', fromlist=['x']).ImportRefused([]))"
)


@pytest.mark.parametrize(
    "code",
    [
        pytest.param(_LOOK_ALIKE_REFUSAL, id="a look-alike in a refusal"),
        pytest.param(_NO_REFUSAL, id="no refusal"),
    ],
)
def test_refusals_the_child_s_writer_refuses_are_the_server_s_fault(code: str) -> None:
    with Reader(ImportLimits()) as reader, pytest.raises(ReaderError) as raised:
        reader.run(eval, code)
    assert str(raised.value) == worker._UNSENT  # pyright: ignore[reportPrivateUsage]
    assert N not in str(raised.value)


def test_a_request_the_child_cannot_read_is_the_server_s_fault() -> None:
    """Server text in a request, which no child unpickles (D399), is a ``ReaderError``, never
    ``UNPARSEABLE_SOURCE``: the file is not to blame."""
    with Reader(ImportLimits()) as reader, pytest.raises(ReaderError) as raised:
        reader.run(len, cast(Any, text(N)))
    assert str(raised.value) == worker._UNREADABLE  # pyright: ignore[reportPrivateUsage]
    assert N not in str(raised.value)


def test_the_child_s_writer_refuses_a_look_alike() -> None:
    lookalike = Refusal.model_construct(
        code=RefusalCode.INVALID_VALUE, path=None, message=[_Said(text=N)]
    )
    sent = worker._refused([lookalike])  # pyright: ignore[reportPrivateUsage]
    assert sent == ("error", worker._UNSENT)  # pyright: ignore[reportPrivateUsage]
    good = Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[text("a "), data("b")])
    assert worker._refused([good]) == (  # pyright: ignore[reportPrivateUsage]
        "refused",
        [good.model_dump(mode="json")],
    )


def test_a_refusal_the_child_sends_is_server_text_again() -> None:
    code = (
        "(_ for _ in ()).throw(__import__('aibi.core.importers.errors', fromlist=['x'])"
        ".refused('INVALID_VALUE', 'The file is ', __import__('aibi.core.schema.output', "
        "fromlist=['x']).data('x')))"
    )
    with Reader(ImportLimits()) as reader, pytest.raises(ImportRefused) as raised:
        reader.run(eval, code)
    assert raised.value.refusals[0].message == [text("The file is "), data("x")]


def _malformed(value: object) -> TextSegment:
    """A ``TextSegment`` as only a reflective write makes one (test code only)."""
    made = object.__new__(TextSegment)
    object.__setattr__(made, "__dict__", {"text": value})
    object.__setattr__(made, "__pydantic_fields_set__", {"text"})
    object.__setattr__(made, "__pydantic_extra__", None)
    object.__setattr__(made, "__pydantic_private__", None)
    return made


def test_an_unpickled_reason_is_validated_again_as_the_instances_it_holds() -> None:
    """``databases._reason`` is the one check of a segment the parent unpickles (M15)."""
    assert databases._reason((text("ok"), data("x"))) == [text("ok"), data("x")]  # pyright: ignore[reportPrivateUsage]
    for bad in (_malformed(["not", "a", "string"]), _malformed("a\ud800b"), _Said(text=N)):
        with pytest.raises(ReaderError, match="not valid segments"):
            databases._reason((text("ok"), bad))  # pyright: ignore[reportPrivateUsage]


# --- CLI_CLIENT ---------------------------------------------------------------------------------


def _client() -> OperatorClient:
    return OperatorClient(httpx.Client(), token="t" * 43, operator="Ada")


def test_the_cli_reads_the_server_s_refusals_as_server_text() -> None:
    body = Refusals(
        refusals=[
            Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[text("No "), data("x")])
        ]
    ).model_dump_json()
    response = httpx.Response(422, content=body.encode())
    with pytest.raises(Refused) as raised:
        _client()._read(response, Datasets)  # pyright: ignore[reportPrivateUsage]
    assert raised.value.refusals[0].message == [text("No "), data("x")]


def test_the_cli_reads_the_server_s_answer_s_segments_as_server_text() -> None:
    sent = Refusals(
        refusals=[Refusal(code=RefusalCode.INVALID_VALUE, path=None, message=[text("Said")])]
    )
    response = httpx.Response(200, content=sent.model_dump_json().encode())
    answer = _client()._read(response, Refusals)  # pyright: ignore[reportPrivateUsage]
    assert answer.value == sent
    assert _client()._read(  # pyright: ignore[reportPrivateUsage]
        httpx.Response(200, content=b'{"datasets": []}'), Datasets
    ).value == Datasets(datasets=[])


def test_the_renderers_fail_closed_on_what_is_not_a_segment() -> None:
    """The catalogue pages and the CLI show an exact data token as data and an exact ``TextSegment``
    as text; a look-alike, which no output holds, is refused, never shown as the server's words."""
    ok = [text("a "), data("<b>")]
    assert markup.segments(ok).html == 'a <bdi class="data">&lt;b&gt;</bdi>'
    assert cli._segments(ok) == "a <b>"  # pyright: ignore[reportPrivateUsage]
    for look in (_Said(text=N), {"text": N}, types.SimpleNamespace(text=N), N):
        with pytest.raises(TypeError, match="text and data segments only"):
            markup.segments(cast(Any, [look]))
        with pytest.raises(TypeError, match="text and data segments only"):
            cli._segments(cast(Any, [look]))  # pyright: ignore[reportPrivateUsage]


# --- what is stored -------------------------------------------------------------------------------


def test_the_stored_members_are_the_ones_that_record_what_text_meant() -> None:
    """Only reports (``REPORT_2_CLASSIFICATION``) and the cache (``_Written.text_format``) store
    segments: a segment-bearing model read from stored bytes anywhere else meets the guard,
    which refuses it (fail closed)."""
    assert {Boundary.REPORT_NOTES, Boundary.RESULT_CACHE} == STORED
    assert build.REPORT_2_CLASSIFICATION == 1
    assert _Written.model_fields["text_format"].default == 0


def mappings_of_text(source: str) -> list[str]:
    """The scopes of ``source`` that write a mapping a segment could be rebuilt from: a display
    with a ``"text"`` key (``{**m, "text": v}`` too), and a ``dict(...)`` call with a ``text``
    keyword or a ``**`` (``dict(**m)``). A display of ``**`` alone, or one whose key is computed,
    is not seen: the guard refuses such a mapping at run time wherever it is made."""
    tree = ast.parse(source)
    scopes = _ast.scopes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(_ast.string(key) == "text" for key in node.keys):
            found.append(scopes[id(node)])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
            and any(keyword.arg in ("text", None) for keyword in node.keywords)
        ):
            found.append(scopes[id(node)])
    return found


def test_the_only_segments_written_as_mappings_in_src_are_known() -> None:
    """Re-entrancy: a validator reached from a boundary's call is in its context, so a segment
    built from a mapping in one would be admitted there. The only mappings with a ``text`` key
    are ``text()``'s own and a ``/1`` report's fixed text (a constant); no ``dict(text=...)`` or
    ``dict(**...)`` call is made."""
    found = [
        (path.relative_to(closure_rule.SOURCE).as_posix(), scope)
        for path in closure_rule.modules()
        for scope in mappings_of_text(path.read_text())
    ]
    assert sorted(found) == [("core/schema/output.py", "text"), ("core/store/build.py", "_fixed")]


@pytest.mark.parametrize(
    "source",
    [
        "x = {'text': v}",
        "x = {**m, 'text': v}",
        "x = dict(text=v)",
        "x = dict(**m)",
        "x = dict(m, text=v)",
    ],
)
def test_the_census_of_mappings_sees_each_spelling(source: str) -> None:
    assert mappings_of_text(source) == ["<module>"]


@pytest.mark.parametrize("source", ["x = {**m}", "x = {k: v}", "x = dict(a=1)", "x = dict(m)"])
def test_the_census_of_mappings_leaves_the_rest(source: str) -> None:
    assert mappings_of_text(source) == []


# --- the writers of the carriers, each handed a look-alike ---------------------------------------


def _lookalike_refusal() -> Refusal:
    return Refusal.model_construct(
        code=RefusalCode.INVALID_VALUE, path=None, message=[_Said(text=N)]
    )


def _lookalike_caveat() -> Caveat:
    return Caveat.model_construct(
        code=CaveatCode.NOT_ESTIMABLE, severity=Severity.INFO, message=[_Said(text=N)]
    )


CONSUMERS = {
    "an import note to the queue (router._note)": lambda: router_module._note(
        ImportNote("dropped", None, cast(Any, [_Said(text=N)]))
    ),
    "an import note to a report (build.report_bytes)": lambda: build.report_bytes(
        [ImportNote("dropped", None, cast(Any, [_Said(text=N)]))]
    ),
    "refusals to HTTP (api.errors.refused)": lambda: errors_module.refused([_lookalike_refusal()]),
    "refusals to MCP (mcp.server._refusals)": lambda: mcp_server._refusals([_lookalike_refusal()]),
    "refusals to the CLI (Refusals)": lambda: Refusals(refusals=[_lookalike_refusal()]),
    "a caveat to the cache (_Written)": lambda: _Written(
        result="drv:" + "0" * 64,
        digest="sha256:" + "0" * 64,
        cohorts=[],
        population=[],
        analysed=[],
        values={},
        caveats=[_lookalike_caveat()],
        text_format=1,
    ),
}
"""Every way a carrier's segments (a dataclass's, an exception's) become bytes: each through a
validated output, which refuses a look-alike of server text (D399)."""


@pytest.mark.parametrize("write", list(CONSUMERS.values()), ids=list(CONSUMERS))
def test_each_writer_of_a_carrier_refuses_a_look_alike(write: Any) -> None:
    with pytest.raises(ValidationError) as raised:
        write()
    assert _guarded(raised.value)
