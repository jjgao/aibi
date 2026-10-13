"""The web client's operator answers (D425): ``web/tests/fixtures/operator-answers.json`` holds
the server's own answers, as the text it wrote, for the reads the browser's four flows take an id
back from (a dataset's state, its queue, its queue as of a release) and their look-alikes (the
datasets list, the public ``curation_queue`` tool, a refusal): the web package's tests decode each
one and hold that every node of every answer is sent back only from its feed (``classes.json``'s
``from``). The state is made here, on a server whose clock ticks from a fixed start, so that the
answers are the same on every run; a change to what the server answers fails here until the file
is written again, so that the client's tests see it in the same change (from ``server/``:
``AIBI_WRITE_WEB_FIXTURES=1 uv run pytest tests/core/operator/test_web_answers.py``).

The state: a dataset ``d`` of sites and visits with two releases, label 1 withdrawn and label 2
published; three proposals, the first rejected, the third accepted in the open session's draft;
the session open, its draft changed (so that its ``base`` and ``draft`` differ)."""

import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.core.operator.harness import BASE_URL

from aibi.core.api.app import create_app
from aibi.core.api.protection import Policy
from aibi.core.api.serve import tools_of
from aibi.core.schema.curation import ProposalInput
from aibi.core.store.proposals import propose_descriptor

Server = Any
TARGET = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "operator-answers.json"
)


def _answers(server: Server, sites: Path) -> list[dict[str, Any]]:
    server.import_("d", sites)
    server.curate("d", {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "S"})
    assert server.post("/operator/datasets/d/withdraw", {"release": 1}).status_code == 200
    for value in ("Field sites", "Places", "Locations"):
        proposal = ProposalInput(descriptor="sites", pointer="/definition", value=value)
        propose_descriptor(server.store, "d", proposal, "agent:helper")
    ids = [found.id for found in server.store.db.proposals("d")]
    assert server.post(f"/operator/datasets/d/proposals/{ids[0]}/reject").status_code == 200
    opened = server.open("d")
    edit = {"op": "set", "descriptor": "visits", "pointer": "/label", "value": "Visits"}
    assert server.change("d", opened, edit).status_code == 200
    state = server.get("/operator/datasets/d").json()
    accept = {"op": "accept", "proposal": ids[2]}
    accepted = server.post(
        "/operator/datasets/d/session/change",
        {"handle": opened["handle"], "expected": state["session"]["draft"], "edits": [accept]},
    )
    assert accepted.status_code == 200, accepted.text
    reads = [
        ("dataset_state", "GET", "/operator/datasets/d", None),
        ("queue", "GET", "/operator/datasets/d/queue", None),
        ("queue", "GET", "/operator/datasets/d/queue?release=2", None),
        ("datasets", "GET", "/operator/datasets", None),
        ("queue", "GET", "/operator/datasets/d/queue?release=1", None),
        ("curation_queue", "POST", "/api/tools/curation_queue", {"dataset": "d"}),
    ]
    # The public tools are served beside the operator router on a server with tool calls: the
    # same store, an application with them.
    policy = Policy.of(server.config, csrf_key=b"k" * 32)
    tools = create_app(policy, server.services, tools=tools_of(server.config, server.store, None))
    answers: list[dict[str, Any]] = []
    with TestClient(tools, base_url=BASE_URL) as public:
        for operation, method, path, body in reads:
            response = server.get(path) if method == "GET" else public.post(path, json=body)
            answers.append(_answer(operation, method, path, response))
    return answers


def _answer(operation: str, method: str, path: str, response: Any) -> dict[str, Any]:
    """One answer as the fixture holds it: the body as the text the server wrote."""
    assert response.headers["content-type"].startswith("application/json"), path
    assert "ses_" not in response.text, path
    return {
        "operation": operation,
        "method": method,
        "path": path,
        "status": response.status_code,
        "body": response.text,
    }


def _render(answers: list[dict[str, Any]]) -> str:
    return json.dumps({"answers": answers}, indent=2, ensure_ascii=False) + "\n"


def test_the_web_client_s_operator_answers_are_the_server_s(server: Server, sites: Path) -> None:
    rendered = _render(_answers(server, sites))
    if os.environ.get("AIBI_WRITE_WEB_FIXTURES") == "1":
        TARGET.write_text(rendered, encoding="utf-8")
    assert TARGET.read_text(encoding="utf-8") == rendered


def test_the_answers_hold_what_the_client_s_tests_need() -> None:
    """Each feed's node is there (a session whose draft is not its base, two labels, open
    proposals), with its look-alikes: the datasets list's ``session``, the public tool's
    ``queue.proposals``, and a refusal (409, the withdrawn release)."""
    answers = json.loads(TARGET.read_text(encoding="utf-8"))["answers"]
    by = [(found["operation"], found["status"]) for found in answers]
    assert by == [
        ("dataset_state", 200),
        ("queue", 200),
        ("queue", 200),
        ("datasets", 200),
        ("queue", 409),
        ("curation_queue", 200),
    ]
    state = json.loads(answers[0]["body"])
    assert state["session"]["base"] != state["session"]["draft"]
    assert [label["status"] for label in state["labels"]] == ["withdrawn", "published"]
    queue = json.loads(answers[1]["body"])
    assert len(queue["proposals"]) >= 2
    assert any(found["accepted_in_draft"] for found in queue["proposals"])
    listed = json.loads(answers[3]["body"])
    assert any(found.get("session") is not None for found in listed["datasets"])
    tool = json.loads(answers[5]["body"])
    assert len(tool["queue"]["proposals"]) >= 1
