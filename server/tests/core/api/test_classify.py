"""One classification of a request, recorded by request protection (D414): the error handlers
and the pages read it, so that inside a mount, under a root path, unusable or not, and for a
percent-encoded path, a refusal is a page exactly where protection's own would be, with links
from the server's root; and a record an incoming scope holds is dropped. The paths are given as
the server's scope holds them (uvicorn decodes a path once: ``/%2564atasets/x`` arrives as
``/%64atasets/x``), straight to the application, since a test client decodes twice."""

import itertools
import json
from collections.abc import Callable
from typing import Any

from starlette.exceptions import HTTPException
from starlette.types import Receive, Scope, Send

from aibi.core.classify import CLASSIFIED_KEY, Classified
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store.store import StoreRefused

Bundled = Any


async def raising(scope: Scope, receive: Receive, send: Send) -> None:
    """A mount that raises what its path's last segment names."""
    kind = scope["path"].rsplit("/", 1)[-1]
    if kind == "store":
        raise StoreRefused(RefusalCode.CONFLICT, "conflict")
    if kind == "boom":
        raise RuntimeError("boom")
    raise HTTPException(405 if kind == "method" else 404)


PATHS: dict[str, tuple[bytes, bool]] = {
    "/": (b"/", True),
    "/datasets": (b"/datasets", True),
    "/datasets/x": (b"/datasets/x", True),
    "/datasets/x/y": (b"/datasets/x/y", True),
    "/favicon.ico": (b"/favicon.ico", True),
    "/curate/x": (b"/curate/x", True),
    "/%64atasets/x": (b"/%2564atasets/x", False),
    "/datasets%2Fx": (b"/datasets%252Fx", False),
    "/other": (b"/other", False),
    "/m/datasets/store": (b"/m/datasets/store", False),
    "/m/datasets/method": (b"/m/datasets/method", False),
    "/m/curate/boom": (b"/m/curate/boom", False),
    "/m/store": (b"/m/store", False),
    "/m/": (b"/m/", False),
    "/m": (b"/m", False),
}
"""Each path as the scope holds it, its raw path, and whether it is a page path, by the table."""
ROOTS = ("", "/aibi", "/x/")
SAME = (b"sec-fetch-site", b"same-origin")


def test_every_answer_is_a_page_where_protection_says_so_with_links_from_the_server_root(
    make_bundled: Callable[..., Bundled], drive: Callable[..., list[Any]], sent: Any
) -> None:
    bundled = make_bundled(None, mounts={"/m": raising})
    rows = list(itertools.product(ROOTS, PATHS))
    requests = []
    for root, path in rows:
        raw, _ = PATHS[path]
        prefix = "" if root == "/x/" else root
        requests.append(
            sent(
                path=prefix + path,
                raw_path=prefix.encode() + raw,
                root_path=root,
                headers=((b"host", b"127.0.0.1:8000"), SAME),
            )
        )
    failures = []
    for (root, path), got in zip(rows, drive(bundled.app, requests), strict=True):
        page = PATHS[path][1]
        kind = got.header(b"content-type") or b""
        if kind.startswith(b"text/html") != page:
            failures.append((root, path, "kind", got.status, kind))
            continue
        if not page:
            assert json.loads(got.body)["refusals"], (root, path)
            continue
        link = b'href="/"' if root in ("", "/x/") else b'href="/aibi/"'
        if link not in got.body:
            failures.append((root, path, "link", got.status))
        if root == "/x/" and got.status != 500:
            failures.append((root, path, "an unusable root served", got.status))
    assert not failures, failures


def test_a_record_an_incoming_scope_holds_is_dropped(
    make_bundled: Callable[..., Bundled], drive: Callable[..., list[Any]], sent: Any
) -> None:
    bundled = make_bundled(None, mounts={"/m": raising})

    async def injecting(scope: Scope, receive: Receive, send: Send) -> None:
        record = Classified(kind="page", root="//evil.example", browser=False)
        await bundled.app({**scope, CLASSIFIED_KEY: record}, receive, send)

    async def injecting_other(scope: Scope, receive: Receive, send: Send) -> None:
        record = Classified(kind="other", root=None, browser=False)
        await bundled.app({**scope, CLASSIFIED_KEY: record}, receive, send)

    headers = ((b"host", b"127.0.0.1:8000"), SAME)
    mounted, pageish = drive(
        injecting,
        [sent(path="/m/store", headers=headers), sent(path="/datasets/x/y", headers=headers)],
    )
    assert mounted.status == 409, mounted.body
    assert mounted.header(b"content-type") == b"application/json"
    assert pageish.status == 404, pageish.body
    assert pageish.header(b"content-type") == b"text/html; charset=utf-8"
    assert b'href="/"' in pageish.body
    assert b"evil" not in pageish.body
    (still,) = drive(injecting_other, [sent(path="/datasets/x/y", headers=headers)])
    assert (still.status, still.header(b"content-type")) == (404, b"text/html; charset=utf-8")
