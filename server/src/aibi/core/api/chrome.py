"""What every catalogue page shares, its refusals included (SPEC §14; D311, D313, D314).

- ``STYLE``, the pages' one stylesheet, and ``CONTENT_SECURITY_POLICY``, which admits it by its
  hash and allows images and connections to the server only (D313); ``PAGE_HEADERS``, that
  policy with ``Cache-Control: no-store`` and ``Cross-Origin-Opener-Policy: same-origin`` (which
  cuts the handle of a page that opened the window, D314), on every response of a page path.
- ``page_path``: the paths whose every answer, a refusal of request protection's or of routing
  included, is a page (D311): ``/``, ``/datasets`` and below, and ``/favicon.ico``, which a
  browser asks for beside each page. ``navigable``: the pages a link from another site may open,
  ``/`` and ``/datasets/<id>`` (D314).
- ``route_path``: the path the routes match; ``root_link`` and ``root_of``: the server's root
  path, which links start from. A root path that is not plain path segments (a letter, digit,
  ``.``, ``_``, ``~`` or ``-``, percent-encoded otherwise; neither ``.`` nor ``..``; no empty
  segment and no trailing ``/``) raises ``ValueError``, so that a page is never served with links
  outside the application (D312): the request fails, and request protection answers it with an
  ``INTERNAL_ERROR`` page whose links start from ``/``, logged.
- ``header`` and ``refusal_document``: the pages' header, and the page that lists a refusal's
  code, path, message, alternatives and limit (D265's refusals, as a page).
"""

import base64
import hashlib
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import cast
from urllib.parse import quote

from starlette.types import Scope

from aibi.core.api.markup import Child, Markup, data, document, element, joined, segments
from aibi.core.schema.refusals import Refusal, blank_secrets

CATALOGUE_PATH = "/"
DATASETS_PREFIX = "/datasets"
FAVICON_PATH = "/favicon.ico"
STYLE = """
:root { color-scheme: light dark; --line: #8884; --soft: #8881; --warn: #b36b00; --block: #b00020; }
body { font: 15px/1.45 system-ui, sans-serif; margin: 0 auto; max-width: 72rem; padding: 1rem; }
header { margin-bottom: 1rem; }
h1 { font-size: 1.6rem; margin: 0.2rem 0; }
h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.05rem; margin-top: 1.5rem; }
article, .descriptor { border: 1px solid var(--line); border-radius: 6px; padding: 0.6rem 0.9rem;
  margin: 0.8rem 0; }
table { border-collapse: collapse; margin: 0.4rem 0; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 0.2rem 0.5rem; text-align: left;
  vertical-align: top; }
th { background: var(--soft); font-weight: 600; }
code, .value { font-family: ui-monospace, monospace; font-size: 0.9em; overflow-wrap: anywhere; }
.value, .text { white-space: pre-wrap; }
.meta, .ref, .absent, .cut { color: GrayText; }
.ref { font-size: 0.8em; }
.suppressed { font-style: italic; }
.escape { border: 1px dotted currentColor; border-radius: 3px; padding: 0 0.15em;
  font-size: 0.85em; }
.caveat.warn { border-left: 4px solid var(--warn); padding-left: 0.5rem; }
.caveat.block { border-left: 4px solid var(--block); padding-left: 0.5rem; }
.caveat.info { border-left: 4px solid var(--line); padding-left: 0.5rem; }
nav.pages a { margin-right: 1rem; }
""".lstrip()
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    f"style-src 'sha256-{base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()}'; "
    "img-src 'self'; connect-src 'self'; form-action 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)
"""The pages' policy (D313): no script, their stylesheet by hash, images and connections to the
server only."""
PAGE_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "Content-Security-Policy": CONTENT_SECURITY_POLICY,
        "Cache-Control": "no-store",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
)
_NAVIGABLE = re.compile(r"^/datasets/[^/]+$")
_SEGMENT = re.compile(r"^(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+$")


def page_path(path: str) -> bool:
    """Whether every answer at ``path`` is a page (module docstring)."""
    return path in (CATALOGUE_PATH, FAVICON_PATH, DATASETS_PREFIX) or path.startswith(
        DATASETS_PREFIX + "/"
    )


def navigable(path: str) -> bool:
    """Whether a link from another site may open ``path`` (D314)."""
    return path == CATALOGUE_PATH or _NAVIGABLE.fullmatch(path) is not None


def route_path(scope: Scope) -> str:
    """The path the routes match, as Starlette finds it: without the server's root path."""
    path = cast(str, scope.get("path", ""))
    root = cast(str, scope.get("root_path", ""))
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root) :] if path[len(root)] == "/" else path


def root_link(root_path: str) -> str:
    """The root path links start from: ``root_path``, percent-encoded; a root path that is not
    plain path segments raises ``ValueError`` (module docstring)."""
    if root_path == "":
        return ""
    try:
        encoded = quote(root_path, safe="/")
    except UnicodeEncodeError:
        raise ValueError("the server's root path is not a path of plain segments") from None
    segments = encoded.split("/")
    if (
        segments[0] != ""
        or len(segments) < 2
        or any(_SEGMENT.fullmatch(part) is None or part in (".", "..") for part in segments[1:])
    ):
        raise ValueError("the server's root path is not a path of plain segments")
    return encoded


def root_of(scope: Scope) -> str:
    """``root_link`` of the request's root path."""
    return root_link(cast(str, scope.get("root_path", "")))


def header(root: str) -> Markup:
    """The pages' header: a link to the catalogue, from ``root``."""
    return element(
        "header",
        element("a", "Catalogue", attributes={"href": f"{root}/"}),
        element("span", " · aibi, read-only", attributes={"class": "meta"}),
    )


def _refusal(found: Refusal) -> Markup:
    parts: list[Child] = [element("strong", str(found.code))]
    if found.path is not None:
        parts += [" at ", element("code", data(found.path))]
    parts += [": ", segments(found.message)]
    if found.alternatives:
        listed = joined((segments([alternative]) for alternative in found.alternatives), ", ")
        parts += [element("br"), "Available: ", listed]
    if found.limit is not None:
        parts += [element("br"), f"Limit: {found.limit.name}, at most {found.limit.max}"]
    return element("li", *parts)


def refusal_document(root: str, refusals: Sequence[Refusal]) -> bytes:
    """The page of refusals, each with anything of a token's or a handle's shape blanked
    (``blank_secrets``, D265)."""
    refusals = [blank_secrets(found) for found in refusals]
    return document(
        "Refused · aibi",
        STYLE,
        header(root),
        element("h1", "The request was refused"),
        element("ul", *(_refusal(found) for found in refusals)),
    )


__all__ = [
    "CATALOGUE_PATH",
    "CONTENT_SECURITY_POLICY",
    "DATASETS_PREFIX",
    "FAVICON_PATH",
    "PAGE_HEADERS",
    "STYLE",
    "header",
    "navigable",
    "page_path",
    "refusal_document",
    "root_link",
    "root_of",
    "route_path",
]
