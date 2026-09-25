"""The operator router (SPEC §11.2, §12.3, D264–D267, D269): imports and uploads, sessions,
proposals, withdrawal, erasure and the proposers over HTTP, each answered with the service's
result or its refusals, and bodies read by the core's loader."""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from starlette.types import Message

from aibi.core.operator import router as router_module

Server = Any
MakeServer = Any


def codes(response: Any) -> list[tuple[str, str | None]]:
    return [(refusal["code"], refusal["path"]) for refusal in response.json()["refusals"]]


def import_path(server: Server, dataset: str, path: str) -> Any:
    return server.post(f"/operator/datasets/{dataset}/import", {"source": {"path": path}})


# --- Imports and uploads (D266) -----------------------------------------------------------------


def test_an_import_of_a_path_in_an_import_directory_publishes_label_1(
    server: Server, sites: Path
) -> None:
    published = server.import_("d", sites)
    assert (published["dataset"], published["label"]) == ("d", 1)
    assert server.store.resolve("d").manifest == published["manifest"]
    assert published["changes"] == []
    assert {note["kind"] for note in published["notes"]} <= {"not_proposed", "renamed", "gap"}


@pytest.mark.parametrize("given", ["outside", "relative", "url", "glob", "missing"])
def test_a_path_outside_the_roots_is_refused(server: Server, sites: Path, given: str) -> None:
    outside = server.root / "elsewhere.csv"
    outside.write_bytes(b"a\n1\n")
    path = {
        "outside": str(outside),
        "relative": "imports/sites",
        "url": "https://example.org/sites.csv",
        "glob": str(sites / "*.csv"),
        "missing": str(server.imports / "nothing.csv"),
    }[given]
    response = import_path(server, "d", path)
    assert response.status_code == 422
    assert codes(response) == [("PATH_NOT_CONFINED", None)]


def test_a_second_import_of_a_dataset_is_refused(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    response = import_path(server, "d", str(sites))
    assert response.status_code == 409
    assert codes(response) == [("DATASET_EXISTS", None)]


def _upload(server: Server, dataset: str, content: bytes, extension: str = "csv") -> Any:
    return server.client.post(
        f"/operator/datasets/{dataset}/uploads",
        params={"extension": extension},
        content=content,
        headers={**server.headers(), "Content-Type": "application/octet-stream"},
    )


def test_an_upload_imported_by_reference_names_its_tables_by_its_original_name(
    server: Server,
) -> None:
    content = b"plot_id,area\np1,3\np2,5\n"
    uploaded = _upload(server, "d", content)
    assert uploaded.status_code == 200, uploaded.text
    found = uploaded.json()
    assert found["bytes"] == len(content)
    assert found["upload"].endswith(".csv")
    assert len(found["upload"]) == 64 + 4
    body = {"source": {"upload": found["upload"]}, "original_name": "Field Plots.csv"}
    published = server.post("/operator/datasets/d/import", body)
    assert published.status_code == 200, published.text
    tables = {d.id for d in server.store.descriptors(published.json()["manifest"])}
    assert "field_plots" in tables
    dataset = next(
        d for d in server.store.descriptors(published.json()["manifest"]) if d.id == "dataset"
    )
    assert dataset.fields.name == "Field Plots"


def test_an_upload_of_another_dataset_is_not_found(server: Server) -> None:
    found = _upload(server, "d", b"a\n1\n").json()["upload"]
    response = server.post("/operator/datasets/e/import", {"source": {"upload": found}})
    assert codes(response) == [("PATH_NOT_CONFINED", None)]


def test_an_upload_of_a_kind_no_importer_reads_is_refused(server: Server) -> None:
    response = _upload(server, "d", b"MZ", extension="exe")
    assert response.status_code == 422
    assert codes(response) == [("UNSUPPORTED_FORMAT", None)]
    assert not list((server.root / "data" / "uploads.tmp").iterdir())


def test_a_re_import_returns_its_changes_and_an_unchanged_one_is_refused(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    unchanged = server.post("/operator/datasets/d/reimport", {"source": {"path": str(sites)}})
    assert unchanged.status_code == 409
    assert codes(unchanged) == [("NO_CHANGE", None)]
    (sites / "sites.csv").write_bytes(b"site_id,name,region\ns1,North,n\ns2,South,s\ns3,East,e\n")
    changed = server.post("/operator/datasets/d/reimport", {"source": {"path": str(sites)}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["label"] == 2
    assert {"descriptor": "sites.region", "pointer": "", "happened": "added"} in changed.json()[
        "changes"
    ]


# --- Sessions (D244–D246, D267) ------------------------------------------------------------------

RELABEL = {"op": "set", "descriptor": "sites", "pointer": "/label", "value": "Field sites"}


def test_a_session_opens_changes_and_publishes_as_the_next_label(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    assert opened["handle"].startswith("ses_")
    assert opened["base"] == opened["draft"]
    changed = server.change("d", opened, RELABEL)
    assert changed.status_code == 200, changed.text
    draft = changed.json()["draft"]
    assert draft != opened["draft"]
    published = server.end("d", opened, draft, "publish")
    assert published.status_code == 200, published.text
    assert published.json()["label"] == 2
    sites_descriptor = next(d for d in server.store.descriptors(draft) if d.id == "sites")
    assert sites_descriptor.curation["/label"].by == "operator:Ada Lovelace"


def test_a_stale_expected_draft_is_a_conflict(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    assert server.change("d", opened, RELABEL).status_code == 200
    again = server.change("d", opened, {**RELABEL, "value": "Sites"})
    assert again.status_code == 409
    assert codes(again) == [("CONFLICT", None)]


def test_a_take_over_by_another_operator_invalidates_the_old_handle(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    first = server.open("d")
    taken = server.post("/operator/datasets/d/session/take-over", operator="Grace Hopper")
    assert taken.status_code == 200
    second = taken.json()
    assert second["handle"] != first["handle"]
    assert second["draft"] == first["draft"]
    stale = server.change("d", first, RELABEL)
    assert codes(stale) == [("CONFLICT", None)]
    assert server.change("d", second, RELABEL).status_code == 200
    actors = server.store.db.connection.execute(
        "SELECT action, actor FROM audit WHERE action = 'take_over'"
    ).fetchall()
    assert actors == [("take_over", "operator:Grace Hopper")]


def test_an_unchanged_publish_is_refused_and_a_discard_ends_the_session(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    unchanged = server.end("d", opened, opened["draft"], "publish")
    assert codes(unchanged) == [("NO_CHANGE", None)]
    discarded = server.end("d", opened, opened["draft"], "discard")
    assert discarded.json() == {"dataset": "d", "outcome": "discarded"}
    assert server.store.db.open_session_of("d") is None
    assert codes(server.end("d", opened, opened["draft"], "discard")) == [("NO_SESSION", None)]


def test_a_second_session_is_busy_and_one_on_a_dataset_without_a_release_is_unknown(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    server.open("d")
    again = server.post("/operator/datasets/d/session/open")
    assert (again.status_code, codes(again)) == (409, [("DATASET_BUSY", None)])
    none = server.post("/operator/datasets/e/session/open")
    assert (none.status_code, codes(none)) == (404, [("UNKNOWN_DATASET", None)])


def test_a_refused_change_points_at_its_edit_or_its_draft_field(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    edit = server.change(
        "d", opened, RELABEL, {"op": "remove", "descriptor": "sites", "pointer": "/label"}
    )
    assert edit.status_code == 422
    assert [path for _, path in codes(edit)] == ["/edits/1/pointer"]
    field = server.change(
        "d", opened, {"op": "set", "descriptor": "sites", "pointer": "/fields/role", "value": "x"}
    )
    assert field.status_code == 422
    assert [path for _, path in codes(field)] == ["/draft/sites/fields/role"]


# --- Proposals, proposers and the queue (D248–D250, D269) ----------------------------------------


def test_the_proposers_run_after_an_import_and_on_request(
    make_server: MakeServer, surveys: Any
) -> None:
    server = make_server(registry=surveys)
    (server.imports / "sites.csv").write_bytes(b"site_id,name\ns1,North\n")
    published = server.post(
        "/operator/datasets/d/import",
        {"source": {"path": str(server.imports / "sites.csv")}, "pack": "surveys"},
    )
    assert published.status_code == 200, published.text
    proposals = published.json()["proposers"]["proposals"]
    assert len(proposals) == 1
    again = server.post("/operator/datasets/d/proposers")
    assert again.status_code == 200
    assert again.json() == {"proposals": proposals, "skipped": []}
    queue = server.get("/operator/datasets/d/queue").json()
    assert [item["id"] for item in queue["proposals"]] == proposals


def test_an_accepted_proposal_is_accepted_when_its_session_publishes(
    make_server: MakeServer, surveys: Any
) -> None:
    server = make_server(registry=surveys)
    (server.imports / "sites.csv").write_bytes(b"site_id,name\ns1,North\n")
    body = {"source": {"path": str(server.imports / "sites.csv")}, "pack": "surveys"}
    [proposal] = server.post("/operator/datasets/d/import", body).json()["proposers"]["proposals"]
    opened = server.open("d")
    changed = server.change("d", opened, {"op": "accept", "proposal": proposal})
    assert changed.status_code == 200, changed.text
    assert server.end("d", opened, changed.json()["draft"], "publish").status_code == 200
    [accepted] = server.store.db.proposals("d", status="accepted")
    assert accepted.id == proposal


def test_a_proposal_is_rejected_and_an_unknown_one_is_not_found(
    make_server: MakeServer, surveys: Any
) -> None:
    server = make_server(registry=surveys)
    (server.imports / "sites.csv").write_bytes(b"site_id,name\ns1,North\n")
    body = {"source": {"path": str(server.imports / "sites.csv")}, "pack": "surveys"}
    [proposal] = server.post("/operator/datasets/d/import", body).json()["proposers"]["proposals"]
    rejected = server.post(f"/operator/datasets/d/proposals/{proposal}/reject")
    assert rejected.json() == {"dataset": "d", "proposal": proposal}
    unknown = server.post(f"/operator/datasets/d/proposals/{proposal}/reject")
    assert (unknown.status_code, codes(unknown)) == (404, [("UNKNOWN_PROPOSAL", None)])


def test_the_proposers_and_queue_of_an_unknown_dataset_are_refused(
    server: Server, sites: Path
) -> None:
    for response in (
        server.post("/operator/datasets/d/proposers"),
        server.get("/operator/datasets/d/queue"),
    ):
        assert (response.status_code, codes(response)) == (404, [("UNKNOWN_DATASET", None)])
    server.import_("d", sites)
    assert server.post("/operator/datasets/d/withdraw", {"release": 1}).status_code == 200
    withdrawn = server.post("/operator/datasets/d/proposers")
    assert (withdrawn.status_code, codes(withdrawn)) == (404, [("UNKNOWN_RELEASE", None)])


HANDLE_BODY = {"handle": "ses_" + "h" * 43, "expected": "sha256:" + "0" * 64}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "", None),
        ("GET", "/queue", None),
        ("GET", "/descriptors/dataset", None),
        ("POST", "/reimport", {"source": {"path": "/nowhere.csv"}}),
        ("POST", "/withdraw", {"release": 1}),
        ("POST", "/erase", {"table": "sites", "key": ["s1"]}),
        ("POST", "/proposers", {}),
        ("POST", "/proposals/1/reject", {}),
        ("POST", "/session/open", {}),
        ("POST", "/session/change", {**HANDLE_BODY, "edits": [{"op": "accept", "proposal": 1}]}),
        ("POST", "/session/publish", HANDLE_BODY),
        ("POST", "/session/discard", HANDLE_BODY),
        ("POST", "/session/take-over", {}),
    ],
)
def test_every_route_on_a_dataset_without_a_release_answers_unknown_dataset(
    server: Server, method: str, path: str, body: dict[str, Any] | None
) -> None:
    where = f"/operator/datasets/d{path}"
    response = server.get(where) if method == "GET" else server.post(where, body)
    assert (response.status_code, codes(response)) == (404, [("UNKNOWN_DATASET", None)])


# --- Withdrawal and erasure (D223, D269) --------------------------------------------------------


def test_a_withdrawal_returns_its_labels_and_is_refused_while_a_session_is_open(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    server.curate("d", RELABEL)
    unknown = server.post("/operator/datasets/d/withdraw", {"release": 7})
    assert (unknown.status_code, codes(unknown)) == (404, [("UNKNOWN_RELEASE", None)])
    server.open("d")
    busy = server.post("/operator/datasets/d/withdraw", {"release": 1})
    assert (busy.status_code, codes(busy)) == (409, [("DATASET_BUSY", None)])


def test_a_withdrawal_by_manifest_hash_withdraws_every_label_of_it(
    server: Server, sites: Path
) -> None:
    manifest = server.import_("d", sites)["manifest"]
    withdrawn = server.post("/operator/datasets/d/withdraw", {"release": manifest})
    assert withdrawn.json() == {"dataset": "d", "labels": [1]}
    again = server.post("/operator/datasets/d/withdraw", {"release": 1})
    assert codes(again) == [("RELEASE_WITHDRAWN", None)]
    state = server.get("/operator/datasets/d").json()
    assert state["labels"] == [{"label": 1, "manifest": manifest, "status": "withdrawn"}]
    assert state["latest"] is None


def test_an_erasure_follows_a_re_import_without_the_person(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    blocked = server.post("/operator/datasets/d/erase", {"table": "sites", "key": ["s3"]})
    assert (blocked.status_code, codes(blocked)) == (409, [("ERASURE_BLOCKED", None)])
    (sites / "sites.csv").write_bytes(b"site_id,name\ns1,North\ns2,South\n")
    (sites / "visits.csv").write_bytes(b"visit_id,site_id,count\nv1,s1,3\nv2,s2,5\nv3,s1,1\n")
    assert (
        server.post("/operator/datasets/d/reimport", {"source": {"path": str(sites)}}).json()[
            "label"
        ]
        == 2
    )
    erased = server.post("/operator/datasets/d/erase", {"table": "sites", "key": ["s3"]})
    assert erased.status_code == 200, erased.text
    assert erased.json() == {
        "dataset": "d",
        "withdrawn": [1],
        "terms": erased.json()["terms"],
        "redacted": True,
        "uploads_pending": False,
    }
    assert "s3" not in erased.text
    detail = server.store.db.connection.execute(
        "SELECT detail FROM audit WHERE action = 'erase'"
    ).fetchone()[0]
    assert "s3" not in detail


def test_an_erasure_key_is_bounded(server: Server, sites: Path) -> None:
    response = server.post("/operator/datasets/d/erase", {"table": "sites", "key": ["k"] * 17})
    assert codes(response) == [("LIMIT_EXCEEDED", "/key")]
    assert response.json()["refusals"][0]["limit"] == {"name": "key_columns", "max": 16}


# --- Slots and concurrent imports (D236, D266) ----------------------------------------------------


def test_an_operation_while_the_dataset_s_slot_is_held_is_busy(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    with server.store.exclusive("d", "session"):
        response = server.post("/operator/datasets/d/withdraw", {"release": 1})
    assert (response.status_code, codes(response)) == (409, [("DATASET_BUSY", None)])


def test_an_import_beyond_the_concurrent_imports_is_refused_at_once(
    make_server: MakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = make_server(imports={"concurrent": 1})
    for name in ("a", "b"):
        (server.imports / f"{name}.csv").write_bytes(b"k,v\nx,1\n")
    entered, release = threading.Event(), threading.Event()
    real = router_module.import_dataset

    def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(router_module, "import_dataset", held)
    first: list[Any] = []
    thread = threading.Thread(
        target=lambda: first.append(import_path(server, "a", str(server.imports / "a.csv")))
    )
    thread.start()
    try:
        assert entered.wait(10)
        second = import_path(server, "b", str(server.imports / "b.csv"))
    finally:
        release.set()
        thread.join(10)
    assert second.status_code == 503
    assert codes(second) == [("LIMIT_EXCEEDED", None)]
    assert second.json()["refusals"][0]["limit"] == {"name": "concurrent_imports", "max": 1}
    assert int(second.headers["retry-after"]) >= 1
    assert first[0].status_code == 200, first[0].text


def test_an_erasure_or_an_upload_beyond_the_concurrent_imports_is_refused_at_once(
    make_server: MakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = make_server(imports={"concurrent": 1})
    (server.imports / "a.csv").write_bytes(b"k,v\nx,1\n")
    entered, release = threading.Event(), threading.Event()
    real = router_module.import_dataset

    def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(router_module, "import_dataset", held)
    first: list[Any] = []
    thread = threading.Thread(
        target=lambda: first.append(import_path(server, "a", str(server.imports / "a.csv")))
    )
    thread.start()
    try:
        assert entered.wait(10)
        erased = server.post("/operator/datasets/b/erase", {"table": "t", "key": ["x"]})
        uploaded = _upload(server, "b", b"k\n1\n")
    finally:
        release.set()
        thread.join(10)
    for refused in (erased, uploaded):
        assert refused.status_code == 503, refused.text
        assert refused.json()["refusals"][0]["limit"] == {"name": "concurrent_imports", "max": 1}
    assert first[0].status_code == 200, first[0].text
    assert _upload(server, "b", b"k\n1\n").status_code == 200


def _paced_upload(
    server: Server,
    pieces: list[bytes],
    declared: int | None,
    *,
    gap: float = 0,
    ends: bool = False,
    path: str = "/operator/datasets/b/uploads",
    extra: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """A body that declares ``declared`` bytes (or no length, if ``None``) and sends ``pieces``
    ``gap`` seconds apart, straight to the application, then ends if ``ends`` or else sends
    nothing more: its status and body. An upload unless ``path`` names another route, whose body
    is JSON."""
    upload = path.endswith("/uploads")
    headers = {
        **server.headers(),
        "Content-Type": "application/octet-stream" if upload else "application/json",
        **({} if declared is None else {"Content-Length": str(declared)}),
        "Host": "127.0.0.1:8000",
        **(extra or {}),
    }
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"extension=csv" if upload else b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    pending = list(pieces)
    answer: dict[str, Any] = {"status": None, "body": b""}

    async def receive() -> Message:
        if pending:
            if len(pending) < len(pieces):
                await anyio.sleep(gap)
            piece = pending.pop(0)
            return {"type": "http.request", "body": piece, "more_body": bool(pending) or not ends}
        await anyio.sleep_forever()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            answer["status"] = message["status"]
        elif message["type"] == "http.response.body":
            answer["body"] += message.get("body", b"")

    async def main() -> None:
        with anyio.fail_after(20):
            await server.app(scope, receive, send)

    anyio.run(main)
    return answer["status"], answer["body"]


def _stalled_upload(server: Server, sent: bytes, declared: int) -> tuple[int, bytes]:
    return _paced_upload(server, [sent], declared)


def test_a_stalled_upload_is_refused_and_frees_its_place(make_server: MakeServer) -> None:
    server = make_server(imports={"concurrent": 1, "upload_idle_seconds": 1})
    status, body = _stalled_upload(server, b"k\n1", 1000)
    assert status == 408
    [refusal] = json.loads(body)["refusals"]
    assert (refusal["code"], refusal["limit"]) == (
        "LIMIT_EXCEEDED",
        {"name": "upload_idle_seconds", "max": 1},
    )
    erased = server.post("/operator/datasets/b/erase", {"table": "t", "key": ["x"]})
    assert codes(erased) == [("UNKNOWN_DATASET", None)]
    uploaded = _upload(server, "b", b"k\n1\n")
    assert uploaded.status_code == 200, uploaded.text
    data = server.config.storage.data
    assert [path.name for path in (data / "uploads" / "b").iterdir()] == [uploaded.json()["upload"]]
    assert list((data / "uploads.tmp").iterdir()) == []


def test_a_trickle_is_refused_at_its_deadline_and_frees_its_place(
    make_server: MakeServer,
) -> None:
    settings = {"concurrent": 1, "upload_idle_seconds": 1, "upload_min_bytes_per_second": 25}
    server = make_server(imports=settings)
    started = time.monotonic()
    status, body = _paced_upload(server, [b"k"] * 50, 50, gap=0.4)
    assert 2.5 < time.monotonic() - started < 6
    assert status == 408
    [refusal] = json.loads(body)["refusals"]
    assert (refusal["code"], refusal["limit"]) == (
        "LIMIT_EXCEEDED",
        {"name": "upload_seconds", "max": 3},
    )
    erased = server.post("/operator/datasets/b/erase", {"table": "t", "key": ["x"]})
    assert codes(erased) == [("UNKNOWN_DATASET", None)]
    assert _upload(server, "b", b"k\n1\n").status_code == 200
    assert list((server.config.storage.data / "uploads.tmp").iterdir()) == []


def test_a_slow_upload_that_keeps_sending_is_accepted(make_server: MakeServer) -> None:
    settings = {"concurrent": 1, "upload_idle_seconds": 1, "upload_min_bytes_per_second": 25}
    server = make_server(imports=settings)
    started = time.monotonic()
    status, body = _paced_upload(server, [b"k\n1\n2"] * 10, 50, gap=0.15, ends=True)
    assert time.monotonic() - started > 1
    assert status == 200, body
    assert json.loads(body)["bytes"] == 50


def test_a_stalled_upload_is_refused_at_its_deadline_when_that_comes_first(
    make_server: MakeServer,
) -> None:
    settings = {"concurrent": 1, "upload_idle_seconds": 2, "upload_min_bytes_per_second": 25}
    server = make_server(imports=settings)
    started = time.monotonic()
    status, body = _paced_upload(server, [b"k"] * 4, 5, gap=0.5)
    assert 2.0 < time.monotonic() - started < 3.0
    assert status == 408
    [refusal] = json.loads(body)["refusals"]
    assert (refusal["code"], refusal["limit"]) == (
        "LIMIT_EXCEEDED",
        {"name": "upload_seconds", "max": 3},
    )


def test_an_upload_without_one_content_length_or_framed_by_transfer_encoding_is_refused(
    server: Server,
) -> None:
    for extra in ({"Transfer-Encoding": "chunked"}, {}):
        status, body = _paced_upload(server, [b"k\n1\n"], None, ends=True, extra=extra)
        assert status == 411
        assert [r["code"] for r in json.loads(body)["refusals"]] == ["LENGTH_REQUIRED"]
    status, body = _paced_upload(
        server, [b"k\n1\n"], 4, ends=True, extra={"Transfer-Encoding": "chunked"}
    )
    assert (status, [r["code"] for r in json.loads(body)["refusals"]]) == (411, ["LENGTH_REQUIRED"])
    assert not (server.config.storage.data / "uploads" / "b").exists()
    assert _upload(server, "b", b"k\n1\n").status_code == 200


def test_a_json_body_is_timed_as_an_upload_is(make_server: MakeServer) -> None:
    settings = {"upload_idle_seconds": 1, "upload_min_bytes_per_second": 25}
    server = make_server(imports=settings)
    withdraw = "/operator/datasets/d/withdraw"
    started = time.monotonic()
    status, body = _paced_upload(server, [b'{"release"'], 14, path=withdraw)
    assert time.monotonic() - started < 3
    assert status == 408
    assert json.loads(body)["refusals"][0]["limit"] == {"name": "upload_idle_seconds", "max": 1}
    status, body = _paced_upload(server, [b" "] * 40, 50, gap=0.4, path=withdraw)
    assert status == 408
    assert json.loads(body)["refusals"][0]["limit"] == {"name": "upload_seconds", "max": 3}
    status, body = _paced_upload(server, [b'{"release": 1}'], None, ends=True, path=withdraw)
    assert status == 404
    assert [r["code"] for r in json.loads(body)["refusals"]] == ["UNKNOWN_DATASET"]


# --- Bodies (D260, D264) ----------------------------------------------------------------------


def _raw(server: Server, path: str, content: bytes) -> Any:
    return server.client.post(
        path, content=content, headers={**server.headers(), "Content-Type": "application/json"}
    )


def test_bodies_are_read_by_the_core_s_loader(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    path = "/operator/datasets/d/withdraw"
    assert codes(_raw(server, path, b"{")) == [("INVALID_JSON", None)]
    assert codes(_raw(server, path, b'{"release": 1, "release": 1}')) == [
        ("DUPLICATE_KEY", "/release")
    ]
    assert codes(_raw(server, path, b'{"release": 1, "why": "x"}')) == [("UNKNOWN_MEMBER", "/why")]
    assert codes(_raw(server, path, b"[]")) == [("WRONG_TYPE", "")]
    deep = b'{"release": 1, "x": ' + b"[" * 65 + b"]" * 65 + b"}"
    nested = _raw(server, path, deep)
    assert nested.status_code == 422
    assert nested.json()["refusals"][0]["limit"] == {"name": "nesting_depth", "max": 64}


@pytest.mark.parametrize("encoded", [False, True])
def test_text_the_server_keeps_is_refused_when_it_holds_a_secret_s_shape(
    server: Server, sites: Path, encoded: bool
) -> None:
    token = server.token.replace("_", "%5F") if encoded else server.token
    handle = "ses_" + "h" * 43
    imported = server.post(
        "/operator/datasets/d/import",
        {"source": {"path": str(sites)}, "name": f"x {token}", "original_name": handle},
    )
    assert imported.status_code == 422
    assert codes(imported) == [("INVALID_VALUE", "/name"), ("INVALID_VALUE", "/original_name")]
    assert not server.store.labels("d")
    server.import_("d", sites)
    opened = server.open("d")
    changed = server.change(
        "d",
        opened,
        {**RELABEL, "value": token},
        {**RELABEL, "evidence": f"see {handle}"},
        {"op": "put", "descriptor": {"kind": "table", "id": "sites", "extensions": {token: 1}}},
        {**RELABEL, "value": {"nested": [handle]}},
    )
    assert changed.status_code == 422
    assert codes(changed) == [
        ("INVALID_VALUE", "/edits/0/value"),
        ("INVALID_VALUE", "/edits/1/evidence"),
        ("INVALID_VALUE", "/edits/2/descriptor/extensions/<secret>"),
        ("INVALID_VALUE", "/edits/3/value/nested/0"),
    ]
    for response in (imported, changed):
        assert token not in response.text
        assert handle not in response.text
        assert server.token[5:] not in response.text
    state = server.get("/operator/datasets/d").json()
    assert state["session"]["draft"] == opened["draft"]


def test_stored_text_holding_the_curator_token_inside_a_word_is_refused(
    server: Server, sites: Path
) -> None:
    other = "x_aibi_" + "t" * 43
    imported = server.post(
        "/operator/datasets/d/import",
        {"source": {"path": str(sites)}, "name": f"token_{server.token}", "original_name": other},
    )
    assert codes(imported) == [("INVALID_VALUE", "/name")]
    assert server.token[5:] not in imported.text
    server.import_("d", sites, name=f"token_{other}")
    opened = server.open("d")
    changed = server.change("d", opened, {**RELABEL, "value": f"{server.token}-x"})
    assert codes(changed) == [("INVALID_VALUE", "/edits/0/value")]
    assert server.token[5:] not in changed.text


LONG_NAME = "courses_completed_before_enrollment_in_the_program_2024"
"""An ordinary column name that holds a handle's shape inside it: ``ses_`` and 47 more."""


def test_a_long_name_holding_a_secret_s_shape_inside_a_word_is_stored_and_put_back(
    server: Server,
) -> None:
    (server.imports / "records.csv").write_bytes(f"record_id,{LONG_NAME}\nr1,3\nr2,5\n".encode())
    server.import_("things", server.imports / "records.csv", original_name=f"{LONG_NAME}.csv")
    column = f"{LONG_NAME}.{LONG_NAME}"
    shown = server.get(f"/operator/datasets/things/descriptors/{column}")
    assert shown.status_code == 200, shown.text
    written = shown.json()["descriptor"]
    assert written["fields"]["source"]["original_name"] == LONG_NAME
    opened = server.open("things")
    put = {k: v for k, v in written.items() if k not in ("version", "curation")}
    relabel = {"op": "set", "descriptor": column, "pointer": "/label", "value": f"x {LONG_NAME}"}
    unchanged = server.change("things", opened, {"op": "put", "descriptor": put})
    assert unchanged.status_code == 200, unchanged.text
    changed = server.change("things", opened, relabel, expected=unchanged.json()["draft"])
    assert changed.status_code == 200, changed.text
    draft = server.get(
        f"/operator/datasets/things/descriptors/{column}", params={"release": "draft"}
    ).json()["descriptor"]
    assert draft["label"] == f"x {LONG_NAME}"


def test_a_body_is_parsed_off_the_event_loop(
    server: Server, sites: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.import_("d", sites)
    seen: list[bool] = []
    loading = router_module.load_request

    def recording(source: bytes, model: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen.append(False)
        else:
            seen.append(True)
        return loading(source, model)

    monkeypatch.setattr(router_module, "load_request", recording)
    assert server.post("/operator/datasets/d/withdraw", {"release": 1}).status_code == 200
    assert seen == [False]


def test_an_unknown_edit_is_refused_with_the_six_ops(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    response = server.change("d", opened, RELABEL, {"op": "rename", "descriptor": "sites"})
    assert response.status_code == 422
    [refusal] = response.json()["refusals"]
    assert (refusal["code"], refusal["path"]) == ("UNKNOWN_KIND", "/edits/1")
    assert [a["text"] for a in refusal["alternatives"]] == [
        "set",
        "remove",
        "confirm",
        "put",
        "remove_descriptor",
        "accept",
    ]


def test_a_member_name_of_a_secret_s_shape_is_not_written_back(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    for secret in ("ses_" + "h" * 43, server.token):
        response = server.post("/operator/datasets/d/session/open", {secret: 1})
        assert codes(response) == [("UNKNOWN_MEMBER", "/<secret>")]
        assert secret not in response.text


@pytest.mark.parametrize("encoded", [False, True], ids=["written", "percent-encoded"])
@pytest.mark.parametrize("kind", ["token", "handle"])
def test_a_service_s_refusal_blanks_a_secret_s_shape_it_took_from_the_request(
    server: Server, sites: Path, kind: str, encoded: bool
) -> None:
    secret = server.token if kind == "token" else "ses_" + "h" * 43
    if encoded:
        secret = secret.replace("_", "%5F", 1)
    server.import_("d", sites)
    opened = server.open("d")
    for edit in (
        {"op": "set", "descriptor": "sites", "pointer": f"/fields/{secret}", "value": "x"},
        {"op": "remove", "descriptor": "sites", "pointer": f"/{secret}"},
    ):
        response = server.change("d", opened, edit)
        assert response.status_code == 422, response.text
        assert secret not in response.text
        assert secret.replace("%5F", "_", 1)[5:] not in response.text
        assert "<secret>" in response.text


def test_an_empty_body_is_an_empty_object(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    response = server.post("/operator/datasets/d/session/open", {"extra": True})
    assert codes(response) == [("UNKNOWN_MEMBER", "/extra")]


# --- Reads ----------------------------------------------------------------------------------------


def test_the_status_of_a_dataset_shows_its_session_without_its_handle(
    server: Server, sites: Path
) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    for path in ("/operator/datasets/d", "/operator/datasets"):
        response = server.get(path)
        assert response.status_code == 200
        assert opened["handle"] not in response.text
    state = server.get("/operator/datasets/d").json()
    assert state["session"] == {
        "session": opened["session"],
        "base": opened["base"],
        "draft": opened["draft"],
        "opened_by": "operator:Ada Lovelace",
    }
    assert state["labels"] == [{"label": 1, "manifest": opened["base"], "status": "published"}]


def test_a_dataset_without_a_release_is_unknown(server: Server) -> None:
    response = server.get("/operator/datasets/d")
    assert (response.status_code, codes(response)) == (404, [("UNKNOWN_DATASET", None)])


def test_a_descriptor_is_shown_from_a_release_or_the_draft(server: Server, sites: Path) -> None:
    server.import_("d", sites)
    opened = server.open("d")
    draft = server.change("d", opened, RELABEL).json()["draft"]
    latest = server.get("/operator/datasets/d/descriptors/sites").json()
    assert (latest["label"], latest["descriptor"]["label"]) == (1, "sites")
    shown = server.get("/operator/datasets/d/descriptors/sites", params={"release": "draft"})
    assert (shown.json()["label"], shown.json()["release"]) == ("draft", draft)
    assert shown.json()["descriptor"]["label"] == "Field sites"
    column = server.get("/operator/datasets/d/descriptors/rel:visits.site_id")
    assert column.json()["descriptor"]["kind"] == "relationship"
    missing = server.get("/operator/datasets/d/descriptors/nothing")
    assert (missing.status_code, codes(missing)) == (404, [("NOT_FOUND", None)])
    bad = server.get("/operator/datasets/d/descriptors/sites", params={"release": "latest"})
    assert bad.status_code == 422
    assert "latest" not in bad.text


def test_openapi_is_generated_but_neither_it_nor_the_docs_are_served(server: Server) -> None:
    schema = server.app.openapi()
    assert "/operator/datasets/{dataset}/session/change" in schema["paths"]
    assert "/api/health" in schema["paths"]
    json.dumps(schema)
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        response = server.client.get(path)
        assert (response.status_code, codes(response)) == (404, [("NOT_FOUND", None)])
