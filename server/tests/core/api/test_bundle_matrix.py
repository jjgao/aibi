"""The web bundle's routes under every request a browser, another site or another client can
send them (SPEC §14; D410–D414): a generated brute force over the product of the routes,
``Sec-Fetch-Site``, ``Sec-Fetch-Mode`` and ``Dest``, ``Sec-Fetch-User``, ``Origin`` and the
methods, and over root paths and a mount, against an oracle written as a table, independent of
the code: what each route is, and what each check refuses, in the order request protection runs
them. Each row asserts the status,
the content type, the exact policies, ``Cache-Control``, ``X-Frame-Options``, the opener policy
and the rate class charged, observed as the bucket that holds the row's own client afterwards
(each row comes from a client of its own); ``test_the_rate_classes_drain_apart`` checks that
observation against buckets drained for real.
"""

import itertools
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount

from aibi.core.api import chrome
from aibi.core.api.chrome import CONTENT_SECURITY_POLICY as PAGE_POLICY
from aibi.core.api.protection import RequestProtection
from aibi.core.api.rates import client_key

Bundled = Any
OWN = "http://127.0.0.1:8000"
PUBLIC = "https://lab.example"
CORS = "https://cors.example"
FOREIGN = "https://evil.example"
DOCUMENT = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)
"""The documents' policy, written out again rather than imported (D411)."""
DEFAULT = "default-src 'none'; frame-ancestors 'none'"
HASHED = "index-C4-4bqdv.js"
UNHASHED = "react-router-dom.js"
"""A file of ``assets/`` that the manifest does not name, hyphenated as a hash is."""
MISSING = "gone-AAAAAAAA.js"


@dataclass(frozen=True)
class Route:
    """What one path is, by the table (not the code): whether it is a page path, a navigable
    one, the operator entry or below it, its rate class, and what ``GET`` answers at the origin
    root ("catalogue", "operator", "immutable", "no-cache" or 404); ``route`` when a ``GET`` route
    matches it, so that another method is 405 there and 404 elsewhere."""

    page: bool
    navigable: bool
    curate: bool
    rate: str
    get: str | int
    route: bool


TABLE: dict[str, Route] = {
    "/": Route(True, True, False, "page", "catalogue", True),
    "/datasets/x": Route(True, True, False, "page", "catalogue", True),
    "/datasets/x/y": Route(True, False, False, "page", 404, False),
    "/datasets": Route(True, False, False, "page", 404, False),
    "/curate": Route(True, False, True, "page", "operator", True),
    "/curate/": Route(True, False, True, "page", 404, False),
    "/curate/x": Route(True, False, True, "page", 404, False),
    "/operator.html": Route(False, False, False, "api", 404, False),
    "/assets": Route(False, False, False, "assets", 404, False),
    "/assets/": Route(False, False, False, "assets", 404, False),
    f"/assets/{HASHED}": Route(False, False, False, "assets", "immutable", True),
    f"/assets/{UNHASHED}": Route(False, False, False, "assets", "no-cache", True),
    f"/assets/{MISSING}": Route(False, False, False, "assets", 404, True),
    "/index.html": Route(False, False, False, "api", 404, False),
    "/favicon.ico": Route(True, False, False, "page", 404, False),
}
SITES: tuple[str | None, ...] = (
    None,
    "none",
    "same-origin",
    "same-site",
    "cross-site",
    "cross-site, none",
)
MODES = (
    ("navigate", "document"),
    ("no-cors", "script"),
    ("cors", "script"),
    ("no-cors", "style"),
    ("cors", "style"),
    ("no-cors", "image"),
    ("cors", "empty"),
)
USERS: tuple[str | None, ...] = (None, "?1")
ORIGINS: tuple[str | None, ...] = (None, "own", "public", "cors", "foreign", "null", "repeated")
METHODS = ("GET", "HEAD", "POST", "OPTIONS")
TYPES = {".js": "text/javascript; charset=utf-8"}
HTML = "text/html; charset=utf-8"
JSON = "application/json"


@dataclass(frozen=True)
class Row:
    path: str
    site: str | None
    mode: tuple[str, str]
    user: str | None
    origin: str | None
    method: str
    root: str
    """The root path as the server sees it: ``""``, ``/aibi``, ``/x/`` (unusable) or ``/m``
    (an embedder's mount)."""


@dataclass(frozen=True)
class Expected:
    status: int
    content_type: str
    policy: str
    cache: str | None
    frame: str | None
    opener: str | None
    charged: str | None


def expected(row: Row) -> Expected:
    """The oracle: the table above and the order of request protection's checks (D255–D257,
    D314, D412), then routing and the bundle's routes (D410, D411)."""
    route = TABLE[row.path]
    origin_given = row.origin is not None
    preflight = row.method == "OPTIONS" and origin_given

    def refused(status: int, charged: str | None = None) -> Expected:
        if route.page:
            return Expected(status, HTML, PAGE_POLICY, "no-store", None, "same-origin", charged)
        cache = "no-store" if route.rate == "assets" else None
        return Expected(status, JSON, DEFAULT, cache, None, None, charged)

    if row.origin in ("cors", "foreign", "null", "repeated"):
        return refused(403)
    if route.curate and row.site not in ("none", "same-origin"):
        return refused(403)
    navigation = (
        row.method == "GET"
        and route.navigable
        and row.site in ("same-site", "cross-site")
        and row.mode == ("navigate", "document")
        and row.user == "?1"
    )
    if not origin_given and row.site in ("same-site", "cross-site") and not navigation:
        return refused(403)
    if preflight:
        return refused(403, route.rate)
    charged = route.rate
    unusable = row.root == "/x/"
    if row.method != "GET" or not route.route:
        status = 405 if route.route else 404
        return refused(500 if unusable and route.page else status, charged)
    if row.root != "":
        return refused(500, charged)
    if route.get == "catalogue" or route.get == "operator":
        return Expected(200, HTML, DOCUMENT, "no-store", "DENY", "same-origin", charged)
    if route.get == "immutable":
        cache = "public, max-age=31536000, immutable"
        return Expected(200, TYPES[".js"], DEFAULT, cache, None, None, charged)
    if route.get == "no-cache":
        return Expected(200, TYPES[".js"], DEFAULT, "no-cache", None, None, charged)
    return refused(404, charged)


def headers_of(row: Row) -> tuple[tuple[bytes, bytes], ...]:
    found: list[tuple[bytes, bytes]] = [(b"host", b"127.0.0.1:8000")]
    if row.site is not None:
        found.append((b"sec-fetch-site", row.site.encode()))
    found += [(b"sec-fetch-mode", row.mode[0].encode()), (b"sec-fetch-dest", row.mode[1].encode())]
    if row.user is not None:
        found.append((b"sec-fetch-user", row.user.encode()))
    origins = {"own": [OWN], "public": [PUBLIC], "cors": [CORS], "foreign": [FOREIGN]}
    origins |= {"null": ["null"], "repeated": [OWN, OWN], None: []}
    found += [(b"origin", value.encode()) for value in origins[row.origin]]
    if row.method == "OPTIONS":
        found.append((b"access-control-request-method", b"GET"))
    return tuple(found)


def rows() -> Iterator[Row]:
    for path, site, mode, user, origin, method in itertools.product(
        TABLE, SITES, MODES, USERS, ORIGINS, METHODS
    ):
        yield Row(path, site, mode, user, origin, method, "")
    for root in ("/aibi", "/x/", "/m"):
        for path, site, mode, user, origin, method in itertools.product(
            TABLE,
            ("none", "same-origin", "cross-site", "cross-site, none", None),
            (("navigate", "document"), ("cors", "script"), ("no-cors", "image")),
            USERS,
            (None, "own", "foreign"),
            METHODS,
        ):
            yield Row(path, site, mode, user, origin, method, root)


def protection_of(app: Any) -> RequestProtection:
    found = app.middleware_stack
    while not isinstance(found, RequestProtection):
        found = found.app
    return found


def charged(protection: RequestProtection, client: str) -> set[str]:
    key = client_key(client)
    return {name for name, buckets in protection._buckets.items() if key in buckets._levels}  # pyright: ignore[reportPrivateUsage]


def address(index: int) -> str:
    return f"10.{(index >> 16) & 255}.{(index >> 8) & 255}.{index & 255}"


def served(make_bundled: Callable[..., Bundled], bundle_dir: Path) -> Bundled:
    (bundle_dir / "assets" / UNHASHED).write_bytes(b"export {};\n")
    return make_bundled(
        bundle_dir,
        server={
            "hostnames": ["lab.example"],
            "public_origins": [PUBLIC],
            "cors_origins": [CORS],
        },
    )


def test_every_row_answers_as_the_table_says(
    make_bundled: Callable[..., Bundled],
    bundle_dir: Path,
    drive: Callable[..., list[Any]],
    sent: Any,
) -> None:
    bundled = served(make_bundled, bundle_dir)
    mounted = Starlette(routes=[Mount("/m", app=bundled.app)])
    every = list(rows())
    requests: list[Any] = []
    for index, row in enumerate(every):
        root = "" if row.root == "/m" else row.root
        prefix = row.root
        requests.append(
            sent(
                path=prefix + row.path,
                method=row.method,
                headers=headers_of(row),
                root_path=root,
                client=(address(index), 40000),
            )
        )
    plain = [i for i, row in enumerate(every) if row.root != "/m"]
    inside = [i for i, row in enumerate(every) if row.root == "/m"]
    answers: dict[int, Any] = {}
    seen: dict[int, set[str]] = {}
    drive(bundled.app, [sent(path="/api/health")])
    protection = protection_of(bundled.app)
    for group, app in ((plain, bundled.app), (inside, mounted)):
        for start in range(0, len(group), 2000):
            chunk = group[start : start + 2000]
            got = drive(app, [requests[i] for i in chunk])
            for i, answer in zip(chunk, got, strict=True):
                answers[i] = answer
                seen[i] = charged(protection, address(i))
    failures: list[tuple[Row, str, object, object]] = []
    for index, row in enumerate(every):
        want = expected(row)
        got = answers[index]
        found = {
            "status": got.status,
            "content_type": (got.header(b"content-type") or b"").decode(),
            "policy": (got.header(b"content-security-policy") or b"").decode(),
            "cache": _text(got.header(b"cache-control")),
            "frame": _text(got.header(b"x-frame-options")),
            "opener": _text(got.header(b"cross-origin-opener-policy")),
            "charged": next(iter(seen[index]), None) if len(seen[index]) <= 1 else seen[index],
        }
        for name, value in found.items():
            if getattr(want, name) != value:
                failures.append((row, name, getattr(want, name), value))
    assert not failures, (len(failures), failures[:10])
    assert len(every) == 15 * 6 * 7 * 2 * 7 * 4 + 3 * 15 * 5 * 3 * 2 * 3 * 4


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def test_no_websocket_is_accepted_at_the_bundles_paths(
    make_bundled: Callable[..., Bundled],
    bundle_dir: Path,
    drive: Callable[..., list[Any]],
    sent: Any,
) -> None:
    bundled = served(make_bundled, bundle_dir)
    requests = [
        sent(
            path=path,
            websocket=True,
            headers=(
                (b"host", b"127.0.0.1:8000"),
                *(() if site is None else ((b"sec-fetch-site", site.encode()),)),
                *(() if origin is None else ((b"origin", origin.encode()),)),
            ),
        )
        for path, site, origin in itertools.product(
            ["/", "/datasets/x", "/curate", f"/assets/{HASHED}", "/nope"],
            SITES,
            (None, OWN, FOREIGN),
        )
    ]
    for request, answer in zip(requests, drive(bundled.app, requests), strict=True):
        assert not answer.accepted, request


def test_the_rate_classes_drain_apart(
    make_bundled: Callable[..., Bundled], bundle_dir: Path, sent: Any, drive: Any
) -> None:
    """Each class's bucket drained for real (a burst of 1): the second request of a row is
    refused naming the class's limit, and a request of another class still passes."""
    one = {"per_minute": 1, "burst": 1}
    bundled = make_bundled(
        bundle_dir,
        server={"rates": {"page": one, "assets": one, "api": one}},
    )
    same = ((b"host", b"127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin"))
    preflight = ((b"host", b"127.0.0.1:8000"), (b"origin", OWN.encode()))
    preflight += ((b"access-control-request-method", b"GET"),)
    cases: Sequence[tuple[str, str, tuple[tuple[bytes, bytes], ...], str]] = (
        ("/", "GET", same, "page_requests"),
        ("/curate", "GET", same, "page_requests"),
        (f"/assets/{HASHED}", "GET", same, "asset_requests"),
        ("/assets/", "GET", same, "asset_requests"),
        ("/index.html", "GET", same, "api_requests"),
        ("/", "OPTIONS", preflight, "page_requests"),
        (f"/assets/{HASHED}", "OPTIONS", preflight, "asset_requests"),
        ("/operator/datasets", "OPTIONS", preflight, "api_requests"),
    )
    for index, (path, method, headers, limit) in enumerate(cases):
        client = (address(100 + index), 1)
        first, second = drive(
            bundled.app, [sent(path=path, method=method, headers=headers, client=client)] * 2
        )
        assert first.status != 429, (path, method)
        assert second.status == 429, (path, method)
        assert limit.encode() in second.body, (path, method, second.body)
        others = [p for p in ("/", f"/assets/{HASHED}", "/index.html") if p != path]
        for other in others:
            if other == "/" and limit == "page_requests":
                continue
            if other.startswith("/assets") and limit == "asset_requests":
                continue
            if other == "/index.html" and limit == "api_requests":
                continue
            (passed,) = drive(bundled.app, [sent(path=other, headers=same, client=client)])
            assert passed.status != 429, (path, method, other)


def test_the_tables_page_paths_and_navigable_paths_are_the_servers() -> None:
    """The admission at ``/curate`` would hide a ``/curate`` made navigable from every request
    above, so the table is held to the server's two predicates directly too (D314, D412)."""
    for path, route in TABLE.items():
        assert chrome.page_path(path) == route.page, path
        assert chrome.navigable(path) == route.navigable, path
    for path in ("/curate", "/curate/", "/curate/x", "/curate/x/y"):
        assert not chrome.navigable(path), path
