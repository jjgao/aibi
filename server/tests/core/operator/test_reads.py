"""The operator router has no side effects on GET (SPEC §11.2, D264), and its routes are the ones
listed, each behind the router's own check of the request's attribution."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import iter_route_contexts

from aibi.core.operator.router import require_operator
from aibi.core.schema.curation import ProposalInput
from aibi.core.store.proposals import propose_descriptor

Server = Any
ROUTES = [
    ("GET", "/operator/csrf"),
    ("GET", "/operator/datasets"),
    ("GET", "/operator/datasets/{dataset}"),
    ("GET", "/operator/datasets/{dataset}/descriptors/{descriptor}"),
    ("GET", "/operator/datasets/{dataset}/queue"),
    ("POST", "/operator/datasets/{dataset}/erase"),
    ("POST", "/operator/datasets/{dataset}/import"),
    ("POST", "/operator/datasets/{dataset}/proposals/reject"),
    ("POST", "/operator/datasets/{dataset}/proposals/{proposal}/reject"),
    ("POST", "/operator/datasets/{dataset}/proposers"),
    ("POST", "/operator/datasets/{dataset}/reimport"),
    ("POST", "/operator/datasets/{dataset}/session/change"),
    ("POST", "/operator/datasets/{dataset}/session/discard"),
    ("POST", "/operator/datasets/{dataset}/session/open"),
    ("POST", "/operator/datasets/{dataset}/session/publish"),
    ("POST", "/operator/datasets/{dataset}/session/take-over"),
    ("POST", "/operator/datasets/{dataset}/uploads"),
    ("POST", "/operator/datasets/{dataset}/withdraw"),
]


def routes(server: Server) -> list[Any]:
    return [route for route in iter_route_contexts(server.app.routes) if route.methods]


def test_the_operator_routes_are_the_ones_listed(server: Server) -> None:
    found = sorted(
        (method, route.path)
        for route in routes(server)
        if route.path.startswith("/operator")
        for method in route.methods
    )
    assert found == sorted(ROUTES)
    others = [
        (method, route.path)
        for route in routes(server)
        if not route.path.startswith("/operator")
        for method in route.methods
    ]
    assert others == [("GET", "/api/health")]


def test_every_operator_route_checks_the_request_s_attribution(server: Server) -> None:
    for route in routes(server):
        if route.path.startswith("/operator"):
            calls = [dependency.call for dependency in route.dependant.dependencies]
            assert require_operator in calls, route.path


@pytest.fixture
def curated(server: Server, sites: Path) -> dict[str, Any]:
    """A dataset with a published and a withdrawn release, an open session whose draft has
    changed, and an open and a rejected proposal."""
    server.import_("d", sites)
    server.curate("d", {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "S"})
    assert server.post("/operator/datasets/d/withdraw", {"release": 1}).status_code == 200
    for value in ("Field sites", "Places"):
        proposal = ProposalInput(descriptor="sites", pointer="/definition", value=value)
        propose_descriptor(server.store, "d", proposal, "agent:helper")
    rejected = server.store.db.proposals("d")[0].id
    assert server.post(f"/operator/datasets/d/proposals/{rejected}/reject").status_code == 200
    opened = server.open("d")
    edit = {"op": "set", "descriptor": "visits", "pointer": "/label", "value": "Visits"}
    changed = server.change("d", opened, edit)
    assert changed.status_code == 200
    return {"latest": server.store.resolve("d").manifest, "draft": changed.json()["draft"]}


def _reads(curated: dict[str, Any]) -> list[tuple[str, dict[str, str]]]:
    return [
        ("/operator/csrf", {}),
        ("/operator/datasets", {}),
        ("/operator/datasets/d", {}),
        ("/operator/datasets/e", {}),
        ("/operator/datasets/d/queue", {}),
        ("/operator/datasets/d/queue", {"release": "2"}),
        ("/operator/datasets/d/queue", {"release": "1"}),
        ("/operator/datasets/d/descriptors/sites", {}),
        ("/operator/datasets/d/descriptors/visits", {"release": "draft"}),
        ("/operator/datasets/d/descriptors/sites", {"release": "1"}),
        ("/operator/datasets/d/descriptors/sites", {"release": curated["latest"]}),
        ("/operator/datasets/d/descriptors/sites", {"release": curated["draft"]}),
        ("/operator/datasets/d/descriptors/nothing", {}),
        ("/operator/nothing", {}),
    ]


def _state(server: Server) -> tuple[str, set[str]]:
    dump = "\n".join(server.store.db.connection.iterdump())
    return dump, set(server.store.blobs.digests())


def test_every_get_leaves_the_app_db_and_the_blobs_unchanged(
    server: Server, curated: dict[str, Any]
) -> None:
    reads = _reads(curated)
    paths = {path for path, _ in reads}
    for route in routes(server):
        if "GET" in route.methods and route.path.startswith("/operator"):
            filled = route.path.replace("{dataset}", "d").replace("{descriptor}", "sites")
            assert filled in paths, route.path
    before = _state(server)
    answered = {server.get(path, params=params).status_code for path, params in reads}
    assert answered == {200, 404, 409}
    assert _state(server) == before


def test_head_is_not_a_read(server: Server) -> None:
    response = server.client.head("/operator/datasets", headers=server.headers())
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
