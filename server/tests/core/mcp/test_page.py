"""The read-only catalogue page (SPEC §14, §15 M1; D311–D314): it renders the M1 fixtures as
``search_catalog`` and ``describe_dataset`` describe them, every text from data as text, under a
content security policy that allows the server alone; every answer at a page path, a refusal of
request protection's or of routing's included, is a page; and a link from another site opens it,
while no other request from another site gets past request protection.

The fixtures are data here, named by nothing but their directories (P8): what a page must show
is read from the tools' answers for the same release. The page tests share the MCP tests'
whole application (``served``).
"""

import base64
import hashlib
import json
import re
import shutil
import threading
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aibi.core.api import page
from aibi.core.api.app import create_app
from aibi.core.api.chrome import root_link
from aibi.core.api.markup import Markup
from aibi.core.api.protection import Policy
from aibi.core.api.serve import check
from aibi.core.schema.catalog import CatalogHits, DatasetDescription, StatCount

Served = Any
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"
BLOCKS = frozenset({"p", "li", "td", "th", "tr", "h1", "h2", "h3", "h4", "br", "title", "div"})
"""Elements whose text a browser shows apart from the text before them."""
INJECTED = '<script>alert(1)</script><img src=x onerror="alert(2)"><a href="//elsewhere">x</a>'
NAVIGATION = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}
"""What a browser sends when a link on another site opens a page."""
BASE = "http://127.0.0.1:8000"
SCRIPTED = {name: value for name, value in NAVIGATION.items() if name != "Sec-Fetch-User"}
"""What a browser sends when a script, not the person, navigates a window to a page."""
MANIFEST = "sha256:" + "ab" * 32


class Parsed(HTMLParser):
    """A page's elements, attributes and text, as a browser's parser reads them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str, str]] = []
        self.texts: list[str] = []
        self.styles: list[str] = []
        self.escapes: list[str] = []
        self.nav: list[str] = []
        self.rows: list[list[str]] = []
        self._open: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        if tag in BLOCKS:
            self.texts.append(" ")
        given = {name: value or "" for name, value in attrs}
        self.attributes.extend((tag, name, value) for name, value in given.items())
        if tag == "a" and any(open_tag == "nav" for open_tag, _ in self._open):
            self.nav.append(given.get("href", ""))
        if tag == "tr":
            self.rows.append([])
        if tag in ("td", "th") and self.rows:
            self.rows[-1].append("")
        if tag not in ("meta", "br"):
            self._open.append((tag, given.get("class", "")))

    def handle_endtag(self, tag: str) -> None:
        if self._open and self._open[-1][0] == tag:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        where = self._open[-1] if self._open else ("", "")
        if where[0] == "style":
            self.styles.append(data)
            return
        if where == ("span", "escape"):
            self.escapes.append(data)
        if self.rows and self.rows[-1] and any(tag in ("td", "th") for tag, _ in self._open):
            self.rows[-1][-1] += data
        self.texts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self.texts).split())

    @property
    def links(self) -> list[str]:
        return [value for tag, name, value in self.attributes if tag == "a" and name == "href"]


def parsed(body: str | bytes | Markup) -> Parsed:
    found = Parsed()
    if isinstance(body, Markup):
        found.feed(body.html)
    else:
        found.feed(body.decode("utf-8") if isinstance(body, bytes) else body)
    found.close()
    return found


def is_page(answered: Any) -> None:
    """``answered`` is a page with the pages' headers (D313)."""
    assert answered.headers["content-type"] == "text/html; charset=utf-8", answered.text
    assert answered.headers["cache-control"] == "no-store"
    assert answered.headers["cross-origin-opener-policy"] == "same-origin"
    assert answered.headers["content-security-policy"].startswith("default-src 'none'; style-src")


def shown(
    served: Served, path: str, status: int = 200, headers: dict[str, str] | None = None
) -> Parsed:
    answered = served.client.get(path, headers=headers or {})
    assert answered.status_code == status, answered.text
    is_page(answered)
    return parsed(answered.text)


def described(served: Served, dataset: str) -> dict[str, Any]:
    answered = served.api("describe_dataset", {"dataset": dataset, "columns_limit": 10_000})
    assert answered.status_code == 200, answered.text
    found: dict[str, Any] = answered.json()
    return found


def import_fixtures(served: Served) -> list[str]:
    """The M1 fixtures, each directory a dataset of its name; the datasets imported."""
    datasets: list[str] = []
    for name in ("library", "biai"):
        target = served.imports / name
        shutil.copytree(FIXTURES / name, target, ignore=shutil.ignore_patterns("*.md"))
        served.operator(f"/operator/datasets/{name}/import", {"source": {"path": str(target)}})
        datasets.append(name)
    return datasets


def count_text(stat: dict[str, Any]) -> str:
    return "suppressed" if stat["count"] is None else f"{stat['count']} {stat['reference']}"


def numbers(text: str) -> set[int]:
    return {int(found) for found in re.findall(r"(?<![0-9A-Za-z.])[0-9]+(?![0-9A-Za-z])", text)}


# --- What the pages show ----------------------------------------------------------------------


def test_the_catalogue_lists_each_dataset_the_m1_fixtures_publish_with_its_tables(
    served: Served,
) -> None:
    datasets = import_fixtures(served)
    hits = served.api("search_catalog", {"limit": 50}).json()["hits"]
    found = shown(served, "/")
    assert f"{len(datasets)} datasets; 1 to {len(datasets)} listed." in found.text
    assert {f"/datasets/{dataset}" for dataset in datasets} <= set(found.links)
    for hit in hits:
        assert hit["label"]["data"] in found.text
        assert f"release @{hit['release']['label']} (published)" in found.text
        for table in hit["tables"]:
            assert table["id"] in found.text
            assert table["label"]["data"] in found.text
            assert count_text(table["rows"]) in found.text


def test_a_datasets_page_shows_its_descriptors_table_graph_and_columns(served: Served) -> None:
    edges = 0
    endpoints = 0
    datasets = import_fixtures(served)
    endpoint = {"kind": "endpoint", "id": "ep:borrowed", "label": "Borrowed", "fields": {}}
    served.curate("library", {"op": "put", "descriptor": endpoint})
    for dataset in datasets:
        tool = described(served, dataset)
        found = shown(served, f"/datasets/{dataset}")
        assert tool["descriptor"]["label"] in found.text
        assert tool["release"]["manifest"] in found.text
        for table in tool["tables"]:
            assert table["descriptor"]["id"] in found.text
            assert count_text(table["rows"]) in found.text
            for pointer, entry in table["descriptor"]["curation"].items():
                assert f"{pointer} " in found.text
                assert f" {entry['status']} {entry['by']}" in found.text
            for column in table["columns"]:
                assert column["id"] in found.text
                assert column["label"]["data"] in found.text
        relationships = {d["id"]: d["fields"] for d in tool["relationships"]}
        edges += len(tool["graph"]["edges"])
        if not tool["graph"]["edges"]:
            assert "No relationship starts from these tables." in found.text
        for edge in tool["graph"]["edges"]:
            fields = relationships[edge["relationship"]]
            child = ", ".join(fields["child_columns"])
            parent = ", ".join(fields["parent_columns"])
            row = (
                f"{edge['relationship']} {edge['child']} ({child}) {edge['parent']} ({parent}) "
                f"{edge['cardinality']}"
            )
            assert row in found.text
        for descriptor in (*tool["coverage"], *tool["endpoints"]):
            assert f"{descriptor['label']} {descriptor['id']}" in found.text
        endpoints += len(tool["endpoints"])
        if tool["endpoints"]:
            assert "Endpoints" in found.texts
        anchors = {value for tag, name, value in found.attributes if name == "id"}
        assert {f"table-{table}" for table in tool["graph"]["tables"]} <= anchors
    assert edges
    assert endpoints


def test_an_edge_shows_its_child_columns_beside_the_child_and_its_parent_columns_beside_the_parent(
    served: Served,
) -> None:
    served.import_(
        "grove",
        {
            "plots.csv": b"plot_id,area\np1,3\np2,5\n",
            "trees.csv": b"tree_id,planted_in\nt1,p1\nt2,p2\nt3,p1\n",
        },
    )
    edges = described(served, "grove")["graph"]["edges"]
    assert [(e["relationship"], e["child"], e["parent"]) for e in edges] == [
        ("rel:trees.planted_in", "trees", "plots")
    ]
    found = shown(served, "/datasets/grove")
    row = "rel:trees.planted_in trees (planted_in) plots (plot_id) many-to-one"
    assert row in found.text


def test_an_injected_markup_label_renders_as_text(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    places = (
        ("dataset", "/label"),
        ("dataset", "/fields/description"),
        ("trees", "/label"),
        ("trees", "/definition"),
        ("trees.variety", "/label"),
    )
    edits: list[dict[str, Any]] = [
        {"op": "set", "descriptor": descriptor, "pointer": pointer, "value": f"{INJECTED} {n}"}
        for n, (descriptor, pointer) in enumerate(places)
    ]
    served.curate("orchard", *edits)
    for path, expected in (("/", (0, 1, 2)), ("/datasets/orchard", (0, 1, 2, 3, 4))):
        answered = served.client.get(path)
        found = parsed(answered.text)
        assert not {"script", "img"} & set(found.tags)
        hrefs = [value for _, name, value in found.attributes if name in ("href", "src")]
        assert all(value.startswith(("/", "#")) and not value.startswith("//") for value in hrefs)
        assert not [name for _, name, _ in found.attributes if name.startswith("on")]
        for n in expected:
            assert f"{INJECTED} {n}" in found.texts
        assert "<script>" not in answered.text


def test_control_and_format_characters_from_data_are_shown_as_marked_escapes(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    label = (
        "left" + chr(0x202E) + "right\x1b[2Jend" + chr(0x2066) + " zero" + chr(0x200B) + "width"
        " literal \\x1b joined" + chr(0x200D) + "on"
    )
    served.curate(
        "orchard", {"op": "set", "descriptor": "trees", "pointer": "/label", "value": label}
    )
    answered = served.client.get("/datasets/orchard")
    for character in (chr(0x202E), "\x1b", chr(0x2066), chr(0x200B)):
        assert character not in answered.text
    found = parsed(answered.text)
    shown_label = (
        "left\\u202eright\\x1b[2Jend\\u2066 zero\\u200bwidth literal \\x1b joined"
        + chr(0x200D)
        + "on"
    )
    assert shown_label in found.text
    assert found.escapes.count("\\x1b") == found.escapes.count("\\u202e")
    assert {"\\u202e", "\\x1b", "\\u2066", "\\u200b"} <= set(found.escapes)
    assert any("literal \\x1b joined" in text for text in found.texts)


def test_the_pages_allow_their_stylesheet_images_and_connections_to_the_server_only(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    for path in (
        "/",
        "/datasets/orchard",
        "/datasets/unknown",
        "/datasets",
        "/datasets/",
        "/datasets/orchard/",
        "/favicon.ico",
    ):
        answered = served.client.get(path)
        is_page(answered)
        policy = dict(
            [*directive.split(" ", 1), ""][:2]
            for directive in answered.headers["content-security-policy"].split("; ")
        )
        found = parsed(answered.text)
        (style,) = found.styles
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        assert policy == {
            "default-src": "'none'",
            "style-src": f"'sha256-{digest}'",
            "img-src": "'self'",
            "connect-src": "'self'",
            "form-action": "'none'",
            "base-uri": "'none'",
            "frame-ancestors": "'none'",
        }
        assert found.tags.count("style") == 1
        assert not {"script", "form", "iframe", "object", "embed", "link", "base"} & set(found.tags)
        assert "url(" not in style
        assert "@import" not in style
        assert answered.headers["x-content-type-options"] == "nosniff"


def test_suppressed_counts_are_shown_suppressed_as_the_tools_show_them(
    make_served: Callable[..., Served], orchard: Callable[..., dict[str, bytes]]
) -> None:
    served = make_served(disclosure={"min_cell_count_floor": 100})
    files = orchard()
    served.import_("orchard", files)
    tool = described(served, "orchard")
    assert all(table["rows"]["count"] is None for table in tool["tables"])
    rows = {len(content.splitlines()) - 1 for content in files.values()}
    for path in ("/", "/datasets/orchard"):
        found = shown(served, path)
        references = [table["rows"]["reference"] for table in tool["tables"]]
        assert all(f"suppressed {reference}" in found.text for reference in references)
        assert "minimum cell count 100: counts below it, and counts that would reveal" in found.text
        assert "SUPPRESSED (info)" in found.text
        assert not rows & numbers(found.text)


def test_a_count_that_is_not_estimable_for_another_reason_says_so() -> None:
    reference = f"stat:{MANIFEST}/trees/n_rows"
    for reason, said in (("suppressed", "suppressed"), ("no_units", "not estimable (no_units)")):
        stat = StatCount.model_validate_json(
            json.dumps({"count": None, "reference": reference, "not_estimable": {"/count": reason}})
        )
        assert parsed(page.count(stat)).text == f"{said} {reference}"
    known = StatCount.model_validate_json(json.dumps({"count": 7, "reference": reference}))
    assert parsed(page.count(known)).text == f"7 {reference}"
    unexplained = StatCount.model_construct(count=None, reference=reference, not_estimable=None)
    assert parsed(page.count(unexplained)).text == f"suppressed {reference}"


def test_a_descriptor_lists_each_member_once_with_its_value_status_and_setter() -> None:
    long = "d" * (page.VALUE_CHARACTERS + 1)
    descriptor: dict[str, Any] = {
        "kind": "table",
        "id": "plots",
        "version": 1,
        "label": "Plots",
        "definition": long,
        "extensions": {"x.y": {"a": 1}},
        "fields": {"a/b": [1, 2], "c~d": "text", "role": "entity"},
        "curation": {
            "/label": {"status": "asserted", "by": "operator:Ada", "at": "2026-01-01T00:00:00Z"},
            "/fields/role": {
                "status": "proposed",
                "by": "importer:x",
                "at": "2026-01-01T00:00:00Z",
            },
            "/fields/grain": {
                "status": "undeclared",
                "by": "importer:x",
                "at": "2026-01-01T00:00:00Z",
            },
        },
    }
    block = page.descriptor_block(descriptor)
    found = parsed(block)
    assert found.rows[0] == ["Member", "Value", "Status", "By"]
    assert [row[0] for row in found.rows[1:]] == [
        "/label",
        "/definition",
        "/extensions/x.y",
        "/fields/a~1b",
        "/fields/c~0d",
        "/fields/role",
        "/fields/grain",
    ]
    assert "/label Plots asserted operator:Ada" in found.text
    assert '/extensions/x.y {"a": 1}' in found.text
    assert "/fields/a~1b [1, 2]" in found.text
    assert "/fields/c~0d text" in found.text
    assert "/fields/role entity proposed importer:x" in found.text
    assert "/fields/grain no value undeclared importer:x" in found.text
    assert "d" * page.VALUE_CHARACTERS + "…" in found.text
    assert "d" * (page.VALUE_CHARACTERS + 1) not in found.text
    assert ("span", "class", "cut") in found.attributes
    exact = {**descriptor, "definition": "e" * page.VALUE_CHARACTERS, "fields": {}, "curation": {}}
    whole = parsed(page.descriptor_block(exact))
    assert "e" * page.VALUE_CHARACTERS in whole.text
    assert ("span", "class", "cut") not in whole.attributes


def test_the_catalogue_shows_a_hits_description_terms_facets_caveats_and_tables_left_out() -> None:
    reference = f"stat:{MANIFEST}/plots/n_rows"
    hits = CatalogHits.model_validate_json(
        json.dumps(
            {
                "hits": [
                    {
                        "dataset": "grove",
                        "release": {"label": 2, "manifest": MANIFEST, "status": "published"},
                        "disclosure": {"min_cell_count": None},
                        "label": {"data": "Grove"},
                        "name": {"data": "The grove"},
                        "description": {"data": "Plots and\ntheir trees"},
                        "domain_tags": [{"data": "trees"}, {"data": "land"}],
                        "data_use": [
                            {
                                "system": {"data": "USE"},
                                "code": {"data": "U1"},
                                "label": {"data": "Any use"},
                            }
                        ],
                        "packs": ["garden"],
                        "concepts": ["core:person"],
                        "facets": {"garden.soil": [{"data": "loam"}]},
                        "tables": [
                            {
                                "id": "plots",
                                "label": {"data": "Plots"},
                                "grain": {"data": "One row per plot"},
                                "role": "entity",
                                "rows": {"count": 3, "reference": reference},
                            }
                        ],
                        "tables_left_out": 4,
                    }
                ],
                "total": 1,
                "caveats": [
                    {
                        "code": "UNCONFIRMED_SEMANTICS",
                        "severity": "warn",
                        "message": [{"text": "Not confirmed: "}, {"data": "plots"}],
                        "affects": ["/hits/0/tables/0"],
                    }
                ],
            }
        )
    )
    found = parsed(page.catalogue_page("", hits, 0))
    for expected in (
        "The grove",
        "Plots and their trees",
        "Domain tags: trees, land",
        "Data use: Any use (USE:U1)",
        "Packs: garden",
        "Concepts: core:person",
        "Facet garden.soil: loam",
        "and 4 more tables, which the dataset's page lists",
        "UNCONFIRMED_SEMANTICS (warn): Not confirmed: plots",
        "Affects: /hits/0/tables/0",
        f"plots Plots One row per plot entity 3 {reference}",
    ):
        assert expected in found.text, expected


def hits_of(description: str | None = None) -> CatalogHits:
    listed: list[dict[str, Any]] = []
    if description is not None:
        listed.append(
            {
                "dataset": "grove",
                "release": {"label": 1, "manifest": MANIFEST, "status": "published"},
                "disclosure": {"min_cell_count": None},
                "label": {"data": "Grove"},
                "description": {"data": description},
                "domain_tags": [],
                "data_use": [],
                "packs": [],
                "concepts": [],
                "facets": {},
                "tables": [],
                "tables_left_out": 0,
            }
        )
    body = {"hits": listed, "total": len(listed), "caveats": []}
    return CatalogHits.model_validate_json(json.dumps(body))


def test_the_catalogue_says_when_nothing_is_published_and_cuts_long_texts_and_lists() -> None:
    empty = parsed(page.catalogue_page("", hits_of(), 0))
    assert "No dataset has a published release yet." in empty.text
    long = parsed(page.catalogue_page("", hits_of("w" * 10_000), 0))
    assert "w" * page.VALUE_CHARACTERS + "…" in long.text
    assert "w" * (page.VALUE_CHARACTERS + 1) not in long.text
    for member in ("name", "label"):
        hits = hits_of("short").model_dump(mode="json")
        hits["hits"][0][member] = {"data": "n" * 4_096}
        written = parsed(page.catalogue_page("", CatalogHits.model_validate(hits), 0))
        assert "n" * page.VALUE_CHARACTERS + "…" in written.text, member
        assert "n" * (page.VALUE_CHARACTERS + 1) not in written.text, member
    hits = hits_of("short").model_dump(mode="json")
    hits["hits"][0]["domain_tags"] = [{"data": f"tag{n}"} for n in range(page.LISTED + 5)]
    listed = parsed(page.catalogue_page("", CatalogHits.model_validate(hits), 0))
    assert f"tag{page.LISTED - 1}, and 5 more" in listed.text
    assert f"tag{page.LISTED}," not in listed.text


def description_of(columns: list[dict[str, Any]], total: int) -> Any:
    reference = f"stat:{MANIFEST}/plots/n_rows"
    body = {
        "dataset": "grove",
        "release": {"label": 1, "manifest": MANIFEST, "status": "published"},
        "disclosure": {"min_cell_count": None},
        "descriptor": {"kind": "dataset", "id": "dataset", "label": "Grove", "fields": {}},
        "tables": [
            {
                "descriptor": {"kind": "table", "id": "plots", "label": "Plots", "fields": {}},
                "rows": {"count": 3, "reference": reference},
                "columns": columns,
            }
        ],
        "relationships": [],
        "coverage": [],
        "endpoints": [
            {
                "kind": "endpoint",
                "id": "ep:cleared",
                "label": "Cleared",
                "fields": {"table": "plots"},
            }
        ],
        "graph": {"tables": ["plots"], "edges": []},
        "applicable_analyses": [],
        "caveats": [],
        "columns_total": total,
    }
    return DatasetDescription.model_validate_json(json.dumps(body))


def test_a_datasets_page_shows_its_columns_datatype_and_identifier_and_its_endpoints() -> None:
    columns = [
        {
            "id": "plots.plot_id",
            "label": {"data": "Plot"},
            "datatype": "string",
            "identifier": True,
        },
        {"id": "plots.area", "label": {"data": "Area"}, "datatype": "number", "identifier": False},
    ]
    found = parsed(page.dataset_page("", description_of(columns, 2), 0))
    assert ["plots.plot_id", "Plot", "string", "yes"] in found.rows
    assert ["plots.area", "Area", "number", ""] in found.rows
    assert "Endpoints" in found.texts
    assert "Cleared ep:cleared" in found.text
    assert "No relationship starts from these tables." in found.text
    none = parsed(page.dataset_page("", description_of([], 0), 0))
    assert "No columns." in none.text


# --- Refusals ---------------------------------------------------------------------------------


def test_an_unknown_dataset_is_refused_on_a_page_listing_the_published_ones(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    found = shown(served, "/datasets/nowhere", 404)
    assert "UNKNOWN_DATASET: No release of the dataset was published" in found.text
    assert " at /dataset" not in found.text
    assert "Available: orchard" in found.text


def test_a_secret_shaped_name_in_a_page_refusal_is_blanked(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    secret = "ses_" + "q" * 43
    served.import_(secret, orchard())
    answered = served.client.get("/datasets/nowhere")
    assert answered.status_code == 404
    assert secret not in answered.text
    assert "Available: <secret>" in parsed(answered.text).text


def test_a_malformed_dataset_or_query_parameter_is_refused_without_being_echoed(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    given_once = "The query parameter offset is given once, as a number from 0 to 9007199254740991"
    for path, expected in (
        ("/?offset=Echoed<b>", given_once),
        ("/?offset=1&offset=2", given_once),
        ("/?offset=12345678901234567", given_once),
        ("/?offset=9007199254740992", given_once),
        ("/?offset=" + "0" * 17, given_once),
        ("/?Echoed=1", "This page takes one query parameter Available: offset"),
        ("/datasets/orchard?offset=1", "Available: columns_offset"),
        ("/datasets/Echoed%3Cb%3E", "INVALID_VALUE: The address names no dataset"),
    ):
        answered = served.client.get(path)
        assert answered.status_code == 422, (path, answered.text)
        is_page(answered)
        assert expected in parsed(answered.text).text
        assert "Echoed" not in answered.text


def test_routings_refusals_at_the_page_paths_are_pages(served: Served) -> None:
    for path in ("/datasets", "/datasets/", "/datasets/orchard/", "/favicon.ico"):
        assert "NOT_FOUND: Nothing is at this path" in shown(served, path, 404).text
    head = served.client.head("/")
    assert head.status_code == 405
    is_page(head)
    assert head.headers["allow"] == "GET"
    posted = served.client.post("/")
    assert posted.status_code == 405
    is_page(posted)
    found = parsed(posted.text)
    assert "METHOD_NOT_ALLOWED: This path does not take this method Available: GET" in found.text
    for path in ("/api/nowhere", "/datasetsX", "/datasets.json", "/nowhere"):
        elsewhere = served.client.get(path)
        assert elsewhere.status_code == 404
        assert elsewhere.headers["content-type"] == "application/json", path


def test_a_failure_or_a_body_too_large_at_a_page_path_is_a_page(
    make_served: Callable[..., Served], monkeypatch: pytest.MonkeyPatch
) -> None:
    served = make_served(server_config={"max_body_bytes": 1024})

    def failing(*given: Any, **named: Any) -> Any:
        raise RuntimeError("failed with ses_" + "q" * 43)

    monkeypatch.setattr(page, "call", failing)
    client = TestClient(
        served.app, base_url=str(served.client.base_url), raise_server_exceptions=False
    )
    failed = client.get("/")
    assert failed.status_code == 500
    is_page(failed)
    assert "INTERNAL_ERROR: The server failed" in parsed(failed.text).text
    assert "ses_" not in failed.text
    large = served.client.post("/", content=b"x" * 2048)
    assert large.status_code == 413
    is_page(large)
    assert "Limit: request_bytes, at most 1024" in parsed(large.text).text


def test_without_the_page_its_paths_answer_json_and_let_no_navigation_through(
    served: Served,
) -> None:
    app = create_app(Policy.of(served.config, csrf_key=b"k" * 32), served.services)
    with TestClient(app, base_url=str(served.client.base_url)) as client:
        missing = client.get("/")
        assert missing.status_code == 404
        assert missing.headers["content-type"] == "application/json"
        navigated = client.get("/", headers=NAVIGATION)
        assert navigated.status_code == 403
        assert navigated.headers["content-type"] == "application/json"
        limited = client.get("/datasets/x", headers={"Host": "elsewhere.example"})
        assert limited.headers["content-type"] == "application/json"


def test_links_start_from_the_servers_root_path(
    served: Served, orchard: Callable[..., dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("first", "second"):
        served.import_(name, orchard())
    monkeypatch.setattr(page, "HITS", 1)
    base = str(served.client.base_url)
    with TestClient(served.app, base_url=base, root_path="/aibi") as client:
        found = parsed(client.get("/aibi/").text)
        assert found.links[0] == "/aibi/"
        assert "/aibi/datasets/first" in found.links
        assert found.nav == ["/aibi/?offset=1"]
        missing = client.get("/aibi/datasets")
        assert missing.status_code == 404
        is_page(missing)
    with TestClient(served.app, base_url=base, root_path="/a b") as client:
        assert parsed(client.get("/a b/").text).links[0] == "/a%20b/"
    with TestClient(served.app, base_url=base, root_path="/a:b@c") as client:
        assert parsed(client.get("/a:b@c/").text).links[0] == "/a%3Ab%40c/"
    for root in ("//elsewhere", "/x/", "/x/../y", "x", "/a" + chr(0xD800)):
        with TestClient(served.app, base_url=base, root_path=root) as client:
            failed = client.get("/")
            assert failed.status_code == 500, root
            is_page(failed)
            assert parsed(failed.text).links == ["/"]
            assert "INTERNAL_ERROR: The server failed" in parsed(failed.text).text


def test_a_root_path_that_is_not_plain_segments_is_refused_by_name() -> None:
    for root in ("x/y", "/x/", "//x", "/.", "/a" + chr(0xD800)):
        with pytest.raises(ValueError, match="the server's root path is not a path of plain"):
            root_link(root)
    assert root_link("") == ""
    assert root_link("/a b/c") == "/a%20b/c"


def test_request_protections_refusals_at_the_page_paths_are_pages(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(server_config={"rates": {"page": {"per_minute": 1, "burst": 2}}})
    host = shown(served, "/", 400, {"Host": "elsewhere.example"})
    assert "HOST_NOT_ALLOWED" in host.text
    subresource = shown(
        served, "/datasets/x", 403, {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"}
    )
    assert "ORIGIN_NOT_ALLOWED" in subresource.text
    assert served.client.get("/").status_code == 200
    assert served.client.get("/datasets/nowhere").status_code == 404
    limited = served.client.get("/")
    assert limited.status_code == 429
    is_page(limited)
    assert limited.headers["retry-after"]
    assert "Limit: page_requests, at most 1" in parsed(limited.text).text
    api = served.client.get("/api/health")
    assert api.headers["content-type"] == "application/json"


def test_a_call_that_gets_no_place_is_a_page_with_retry_after_and_its_limit(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(seconds=0)
    answered = served.client.get("/")
    assert answered.status_code == 503
    is_page(answered)
    assert answered.headers["retry-after"] == "30"
    assert "Limit: client_tool_calls, at most 2" in parsed(answered.text).text


def test_the_pages_call_their_tools_as_the_client_that_asked(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[str] = []
    run = served.calls.run

    async def recorded(function: Any, *, client: str = "", **given: Any) -> Any:
        clients.append(client)
        return await run(function, client=client, **given)

    monkeypatch.setattr(served.calls, "run", recorded)
    served.client.get("/")
    served.client.get("/datasets/nowhere")
    assert clients == ["testclient", "testclient"]


def test_a_page_is_rendered_in_its_calls_worker_thread_never_on_the_event_loop(
    served: Served, orchard: Callable[..., dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    served.import_("orchard", orchard())
    threads: list[str] = []
    for name in ("catalogue_page", "dataset_page"):
        rendered = getattr(page, name)

        def recording(*given: Any, rendered: Any = rendered) -> bytes:
            threads.append(threading.current_thread().name)
            result: bytes = rendered(*given)
            return result

        monkeypatch.setattr(page, name, recording)
    assert served.client.get("/").status_code == 200
    assert served.client.get("/datasets/orchard").status_code == 200
    assert threads == ["AnyIO worker thread", "AnyIO worker thread"]


def test_a_large_datasets_page_shows_a_bounded_window_of_its_tables() -> None:
    reference = f"stat:{MANIFEST}/t0/n_rows"
    tables = [
        {
            "descriptor": {"kind": "table", "id": f"t{n}", "label": f"T{n}", "fields": {}},
            "rows": {"count": 1, "reference": reference},
            "columns": [
                {"id": f"t{n}.c{m}", "label": {"data": f"C{m}"}, "identifier": False}
                for m in range(2)
            ],
        }
        for n in range(1_000)
    ]
    relationships = [
        {
            "kind": "relationship",
            "id": f"rel:t{n}.c1",
            "label": f"R{n}",
            "fields": {
                "child_table": f"t{n}",
                "child_columns": ["c1"],
                "parent_table": "t0",
                "parent_columns": ["c0"],
                "cardinality": "many-to-one",
            },
        }
        for n in range(1, 1_000)
    ]
    body = {
        "dataset": "wide",
        "release": {"label": 1, "manifest": MANIFEST, "status": "published"},
        "disclosure": {"min_cell_count": None},
        "descriptor": {"kind": "dataset", "id": "dataset", "label": "Wide", "fields": {}},
        "tables": tables,
        "relationships": relationships,
        "coverage": [],
        "endpoints": [],
        "graph": {
            "tables": [f"t{n}" for n in range(1_000)],
            "edges": [
                {
                    "relationship": f"rel:t{n}.c1",
                    "child": f"t{n}",
                    "parent": "t0",
                    "cardinality": "many-to-one",
                }
                for n in range(1, 1_000)
            ],
        },
        "applicable_analyses": [],
        "caveats": [],
        "columns_total": 2_000,
    }
    wide = DatasetDescription.model_validate_json(json.dumps(body))
    written = page.dataset_page("", wide, 0)
    found = parsed(written)
    assert len(written) < 200_000
    sections = [value for tag, name, value in found.attributes if tag == "section"]
    assert len(sections) == page.TABLES
    assert "2000 columns; 1 to 100 listed. 50 of 1000 tables shown here." in found.text
    assert "; 950 more on other pages" in found.text
    assert found.nav == ["/datasets/wide?columns_offset=100"]
    assert sum(1 for row in found.rows if row and row[0].startswith("rel:")) == 49
    assert "rel:t60.c1" not in found.text
    second = parsed(page.dataset_page("", wide, 100))
    assert found.nav != second.nav
    assert second.nav == ["/datasets/wide", "/datasets/wide?columns_offset=200"]


def test_a_datasets_page_moves_on_by_the_columns_of_the_tables_it_shows(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_fixtures(served)
    monkeypatch.setattr(page, "TABLES", 2)
    tool = described(served, "library")
    first = [table["descriptor"]["id"] for table in tool["tables"][:2]]
    listed = sum(len(table["columns"]) for table in tool["tables"][:2])
    found = shown(served, "/datasets/library")
    anchors = [value for tag, name, value in found.attributes if tag == "section"]
    assert anchors == [f"table-{table}" for table in first]
    assert found.nav == [f"/datasets/library?columns_offset={listed}"]
    following = shown(served, f"/datasets/library?columns_offset={listed}")
    third = tool["tables"][2]["descriptor"]["id"]
    assert f"table-{third}" in [value for tag, name, value in following.attributes]


# --- Links from other sites (D314) ------------------------------------------------------------


def test_a_link_from_another_site_opens_a_page(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    for headers in (NAVIGATION, {**NAVIGATION, "Sec-Fetch-Site": "same-site"}):
        assert "Catalogue" in shown(served, "/", headers=headers).text
        found = shown(served, "/datasets/orchard?columns_offset=0", headers=headers)
        assert "orchard" in found.text


def test_any_other_request_from_another_site_is_refused(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    refused: list[tuple[str, str, dict[str, str]]] = [
        ("GET", "/", {**NAVIGATION, "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors"}),
        ("GET", "/", {**NAVIGATION, "Sec-Fetch-Dest": "iframe"}),
        ("GET", "/", {**NAVIGATION, "Sec-Fetch-Mode": "cors"}),
        ("GET", "/", {**NAVIGATION, "Sec-Fetch-User": "?0"}),
        ("GET", "/", SCRIPTED),
        ("GET", "/datasets/orchard", SCRIPTED),
        ("HEAD", "/", NAVIGATION),
        ("GET", "/", {**NAVIGATION, "Origin": "https://elsewhere.example"}),
        ("POST", "/", NAVIGATION),
        ("GET", "/datasets/orchard/x", NAVIGATION),
        ("GET", "/favicon.ico", NAVIGATION),
        ("GET", "/api/health", NAVIGATION),
        ("GET", "/operator/status", NAVIGATION),
    ]
    for method, path, headers in refused:
        answered = served.client.request(method, path, headers=headers)
        assert answered.status_code == 403, (method, path, headers, answered.text)
        assert method == "HEAD" or "ORIGIN_NOT_ALLOWED" in answered.text
    for name, value in (
        ("Sec-Fetch-Dest", "document"),
        ("Sec-Fetch-User", "?1"),
        ("Sec-Fetch-Mode", "navigate"),
        ("Sec-Fetch-Site", "same-origin"),
    ):
        doubled = served.client.get("/", headers=[*NAVIGATION.items(), (name, value)])
        assert doubled.status_code == 403, name
    for name in ("Sec-Fetch-Mode", "Sec-Fetch-Dest", "Sec-Fetch-Site"):
        upper = served.client.get("/", headers={**NAVIGATION, name: NAVIGATION[name].upper()})
        assert upper.status_code == 403, name


def test_a_websocket_dressed_as_a_navigation_is_closed_before_any_rate(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(server_config={"rates": {"page": {"per_minute": 1, "burst": 1}}})
    with pytest.raises(WebSocketDisconnect) as closed:
        served.client.websocket_connect("/", headers=NAVIGATION).__enter__()
    assert closed.value.code == 1008
    assert served.client.get("/").status_code == 200


def test_a_navigation_a_script_started_is_refused_before_any_rate_is_spent(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(server_config={"rates": {"api": {"per_minute": 3, "burst": 3}}})
    for n in range(10):
        refused = served.client.get(f"/datasets/nowhere?columns_offset={n}", headers=SCRIPTED)
        assert refused.status_code == 403
        assert "ORIGIN_NOT_ALLOWED" in parsed(refused.text).text
    for _ in range(3):
        agent = served.api("search_catalog", {})
        assert agent.status_code == 200, agent.text


def test_a_preflight_at_a_page_path_spends_the_page_rate_and_never_the_agents(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(
        server_config={
            "rates": {"api": {"per_minute": 1, "burst": 2}, "page": {"per_minute": 1, "burst": 2}}
        }
    )
    preflight = {"Origin": BASE, "Access-Control-Request-Method": "GET"}
    for _ in range(2):
        assert served.client.options("/", headers=preflight).status_code == 403
    limited = served.client.options("/", headers=preflight)
    assert limited.status_code == 429
    assert "Limit: page_requests, at most 1" in parsed(limited.text).text
    for _ in range(2):
        agent = served.api("search_catalog", {})
        assert agent.status_code == 200, agent.text


def test_without_the_page_its_paths_spend_the_api_rate(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(
        server_config={
            "rates": {"api": {"per_minute": 1, "burst": 1}, "page": {"per_minute": 1, "burst": 9}}
        }
    )
    app = create_app(Policy.of(served.config, csrf_key=b"k" * 32), served.services)
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/").status_code == 404
        limited = client.get("/")
        assert limited.status_code == 429
        assert limited.json()["refusals"][0]["limit"]["name"] == "api_requests"


def test_the_page_rate_is_part_of_the_checked_configuration(served: Served) -> None:
    lines = check(served.config)
    assert any(line.startswith("rate, page: ") for line in lines)


def test_page_views_spend_their_own_rate_and_never_the_agents(
    make_served: Callable[..., Served],
) -> None:
    served = make_served(
        server_config={
            "rates": {"api": {"per_minute": 1, "burst": 2}, "page": {"per_minute": 1, "burst": 3}}
        }
    )
    image = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}
    for _ in range(5):
        assert served.client.get("/", headers=image).status_code == 403
    assert served.client.get("/", headers=NAVIGATION).status_code == 200
    assert served.client.get("/").status_code == 200
    assert served.client.get("/favicon.ico").status_code == 404
    limited = served.client.get("/", headers=NAVIGATION)
    assert limited.status_code == 429
    assert "Limit: page_requests, at most 1" in parsed(limited.text).text
    for _ in range(2):
        agent = served.api("search_catalog", {})
        assert agent.status_code == 200, agent.text
    assert served.api("search_catalog", {}).json()["refusals"][0]["limit"]["name"] == (
        "api_requests"
    )


# --- Paging and releases ----------------------------------------------------------------------


def test_the_catalogue_and_a_datasets_columns_are_listed_a_page_at_a_time(
    served: Served, orchard: Callable[..., dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("first", "second", "third"):
        served.import_(name, orchard())
    monkeypatch.setattr(page, "HITS", 2)
    monkeypatch.setattr(page, "COLUMNS", 2)
    opened = shown(served, "/")
    assert "3 datasets; 1 to 2 listed." in opened.text
    assert "/datasets/third" not in opened.links
    assert opened.nav == ["/?offset=2"]
    following = shown(served, "/?offset=2")
    assert "3 datasets; 3 to 3 listed." in following.text
    assert "/datasets/third" in following.links
    assert following.nav == ["/"]
    assert "3 datasets; none from" in shown(served, "/?offset=9007199254740991").text
    assert "3 datasets; 1 to 2 listed." in shown(served, "/?offset=" + "0" * 16).text
    beyond = shown(served, "/?offset=9")
    assert "3 datasets; none from 10 on." in beyond.text
    assert beyond.nav == ["/?offset=1"]
    total = described(served, "first")["columns_total"]
    assert total == 6
    columns = shown(served, "/datasets/first")
    assert "6 columns; 1 to 2 listed." in columns.text
    assert columns.nav == ["/datasets/first?columns_offset=2"]
    middle = shown(served, "/datasets/first?columns_offset=2")
    assert "6 columns; 3 to 4 listed." in middle.text
    assert middle.nav == ["/datasets/first", "/datasets/first?columns_offset=4"]
    last = shown(served, "/datasets/first?columns_offset=5")
    assert "6 columns; 6 to 6 listed." in last.text
    assert last.nav == ["/datasets/first?columns_offset=3"]
    past = shown(served, "/datasets/first?columns_offset=99")
    assert "6 columns; none from 100 on." in past.text
    assert past.nav == ["/datasets/first?columns_offset=4"]


def test_a_draft_is_not_shown_until_it_is_published(
    served: Served, orchard: Callable[..., dict[str, bytes]]
) -> None:
    served.import_("orchard", orchard())
    opened = served.operator("/operator/datasets/orchard/session/open")
    edit = {"op": "set", "descriptor": "dataset", "pointer": "/label", "value": "Renamed"}
    body = {"handle": opened["handle"], "expected": opened["draft"], "edits": [edit]}
    served.operator("/operator/datasets/orchard/session/change", body)
    for path in ("/", "/datasets/orchard"):
        found = shown(served, path)
        assert "Renamed" not in found.text
        assert "release @1 (published)" in found.text


def test_a_mount_at_or_below_the_datasets_path_is_refused(served: Served) -> None:
    for path in ("/datasets", "/datasets/orchard"):
        with pytest.raises(ValueError, match="would lie under a path the application serves"):
            create_app(
                Policy.of(served.config, csrf_key=b"k" * 32),
                served.services,
                tools=served.calls,
                mounts={path: FastAPI()},
            )
