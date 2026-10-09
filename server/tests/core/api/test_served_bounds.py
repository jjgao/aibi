"""The bounds on what the server serves alike to every call (SPEC §11.1, §12.4, D421).

``list_analyses``' entries and ``list_leaf_kinds``, each on its own, serve at most
``MAX_SERVED_VALUES`` JSON values, of them at most ``MAX_SERVED_CONTAINERS`` objects and arrays,
and ``MAX_SERVED_BYTES`` bytes of their form over MCP; the loader refuses a registry over any,
in ``aibi-server check`` and ``serve`` alike, naming the limit, the total and every share (the
core's, each pack's, the listing's own), whatever the order of ``[packs] modules``.

The oracle is the real application: each listing is served over HTTP (its body) and over MCP
(a ``tools/call`` of ``id`` 1, whose answer is the body, the body again as one JSON string, and
an envelope of 108 bytes, checked here against ``json.dumps(..., ensure_ascii=False)``, a JSON
string written apart from the server's). Values and containers are counted from the definition
over the body parsed, and the shares by splitting the body by owner; the server's measure is
held equal to it over every escape class, number and container shape there is
(``test_the_measure_is_what_both_transports_serve``). Every registry at a cap is built from the
oracle's own measures (the bytes each character of a class adds, the values and containers a
registry serves), never from the server's: the byte cap for each string class (ASCII, control,
quote, backslash, BMP of two and three bytes, astral), checked by the real wire for ASCII and
astral and by the measure the wire is held to for the others; the value cap model-dense (tiny
members, the core's included) and values-dense (an ``enum`` of zeros); and the container cap for
each shape (empty objects and arrays, objects of keys, of astral keys and holding astral
strings, arrays 30 and objects 60 deep). One
over is one byte (six ASCII characters made one control character), one value or one container
more.
"""

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest
from tests.core.api.served_builders import (
    CLASSES,
    Spec,
    Wire,
    config_of,
    pack_of,
    spread,
    transports,
    values_of,
    write_modules,
)

from aibi.core.analyses.registry import Analyses
from aibi.core.api import packs, serve
from aibi.core.catalog.served import (
    CORE_OWNER,
    FRAMING,
    Measured,
    form_bytes,
    json_counts,
    measured,
    served_problems,
)
from aibi.core.engine import build
from aibi.core.schema.catalog import AnalysisListing
from aibi.core.schema.limits import (
    MAX_SERVED_BYTES,
    MAX_SERVED_CONTAINERS,
    MAX_SERVED_VALUES,
)
from aibi.core.schema.pack_api import PackRegistry

ENVELOPE = 108
"""The bytes of a ``tools/call`` answer at ``id`` 1 around the body and its JSON string."""
CORE_VERSION = "0.0.1"
TOOLS = {"list_analyses": "analyses", "list_leaf_kinds": "kinds"}
"""Each listing, and the member that lists its items."""


@dataclass
class Harness:
    """Loads specs through the server's loader (each pack a module of its own) and serves
    registries through the whole application, counting what it serves."""

    root: Path
    modules: Path
    served: int = 0
    counter: ClassVar[int] = 0

    def directory(self) -> Path:
        Harness.counter += 1
        found = self.root / f"app{Harness.counter}"
        found.mkdir()
        return found

    def wire(self, registry: PackRegistry | None, tool: str) -> Wire:
        self.served += 1
        with transports(self.directory(), registry) as call:
            return call(tool, {})

    def both(self, registry: PackRegistry | None) -> dict[str, Wire]:
        """Both listings' answers over ``registry``, from one application."""
        self.served += 2
        with transports(self.directory(), registry) as call:
            return {tool: call(tool, {}) for tool in TOOLS}

    def wires(self, specs: list[Spec], tool: str) -> Wire:
        """``tool``'s answers over a registry of ``specs`` built directly (the loader's check
        bypassed: the oracle serves what the loader refuses)."""
        return self.wire(registry_of(specs), tool)

    def load(self, specs: list[Spec], order: int = 1) -> tuple[packs.Loaded, dict[str, str]]:
        names = write_modules(self.modules, specs)
        labels = {
            spec.id: f"the module {name} (pack {spec.id})"
            for spec, name in zip(specs, names, strict=True)
        }
        loaded = packs.load(config_of(self.directory(), names[::order]), io.StringIO())
        return loaded, labels


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    modules = tmp_path / "modules"
    modules.mkdir()
    monkeypatch.syspath_prepend(str(modules))
    return Harness(tmp_path, modules)


def registry_of(specs: list[Spec]) -> PackRegistry:
    return PackRegistry([pack_of(spec) for spec in specs], core_version=CORE_VERSION)


# --- The oracle ----------------------------------------------------------------------------------


def form_of(wire: Wire) -> int:
    """The bytes of the MCP form, from the real MCP answer."""
    return len(wire.mcp) - ENVELOPE


def string_bytes(text: str) -> int:
    """A JSON string's bytes in UTF-8, quotes included, written by ``json.dumps``."""
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8"))


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def containers_of(value: object) -> int:
    """JSON objects and arrays, at every depth, from the definition."""
    if isinstance(value, dict):
        return 1 + sum(containers_of(member) for member in value.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return 1 + sum(containers_of(item) for item in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return 0


Counts = tuple[int, int, int]
"""Values, containers and bytes."""


@dataclass(frozen=True)
class Oracle:
    """A listing as the transports served it: its totals, and each owner's share and the
    framing, split from the body."""

    values: int
    containers: int
    bytes: int
    shares: dict[str, Counts]
    framing: Counts


def oracle(wire: Wire, tool: str, owner: Callable[[dict[str, Any]], str]) -> Oracle:
    body = wire.http.decode("utf-8")
    listing = json.loads(body)
    assert compact(listing) == body, "the body is the compact JSON of what it holds"
    answer = json.loads(wire.mcp)
    assert answer["result"]["content"] == [{"type": "text", "text": body}]
    assert answer["result"]["structuredContent"] == listing
    assert len(wire.mcp) == len(wire.http) + string_bytes(body) + ENVELOPE
    items = listing[TOOLS[tool]]
    shares: dict[str, Counts] = {}
    for item in items:
        text = compact(item)
        values, containers, size = shares.get(owner(item), (0, 0, 0))
        shares[owner(item)] = (
            values + values_of(item),
            containers + containers_of(item),
            size + len(text.encode("utf-8")) + string_bytes(text) - 2,
        )
    empty = {**listing, TOOLS[tool]: []}
    bare = compact(empty)
    framing = (
        values_of(empty),
        containers_of(empty),
        len(bare.encode("utf-8")) + string_bytes(bare) + 2 * max(len(items) - 1, 0),
    )
    total = (values_of(listing), containers_of(listing), form_of(wire))
    for index in range(3):
        assert sum(share[index] for share in shares.values()) + framing[index] == total[index]
    return Oracle(*total, shares, framing)


CORE_IDS = {entry.id for entry in Analyses(None).entries()}


def owner_of(tool: str, labels: dict[str, str] | None = None) -> Callable[[dict[str, Any]], str]:
    def owner(item: dict[str, Any]) -> str:
        if tool == "list_analyses" and item["id"] in CORE_IDS:
            return CORE_OWNER
        pack = item["id"].partition(".")[0] if tool == "list_analyses" else item["pack"]
        return pack if labels is None else labels[pack]

    return owner


UNITS = {
    "values": (0, "JSON values", MAX_SERVED_VALUES, "MAX_SERVED_VALUES"),
    "containers": (1, "JSON objects and arrays", MAX_SERVED_CONTAINERS, "MAX_SERVED_CONTAINERS"),
    "bytes": (2, "bytes of its form over MCP", MAX_SERVED_BYTES, "MAX_SERVED_BYTES"),
}


def problem(tool: str, found: Oracle, packs_: list[str], labels: dict[str, str], of: str) -> str:
    """The loader's problem for ``found``, from the oracle's shares: every pack named, in pack
    id order, a pack with nothing listed at 0."""
    index, words, most, name = UNITS[of]
    shares = [(CORE_OWNER, found.shares.get(CORE_OWNER, (0, 0, 0))[index])]
    shares += [(labels[pack], found.shares.get(pack, (0, 0, 0))[index]) for pack in sorted(packs_)]
    shares.append((FRAMING, found.framing[index]))
    named = ", ".join(f"{who} has {count}" for who, count in shares)
    total = (found.values, found.containers, found.bytes)[index]
    return f"{tool} would serve {total} {words}, more than the {most} allowed ({name}): {named}"


def mine(found: Measured, labels: dict[str, str], of: str) -> str:
    """The same problem from the server's measure, its owners renamed by ``labels`` (the
    measure is held to the oracle by the transport tests)."""
    index, words, most, name = UNITS[of]
    counts = [(share.values, share.containers, share.bytes)[index] for share in found.shares]
    owners = [labels.get(share.owner, share.owner) for share in found.shares]
    named = ", ".join(f"{who} has {count}" for who, count in zip(owners, counts, strict=True))
    total = (found.values, found.containers, found.bytes)[index]
    return (
        f"{found.tool} would serve {total} {words}, more than the {most} allowed ({name}): {named}"
    )


def test_the_oracle_s_definitions() -> None:
    assert values_of({"a": [1, "b", None, {"c": True}]}) == 7
    assert containers_of({"a": [1, [], {"c": {}}]}) == 5
    assert string_bytes('a"\\\x01é\U0001f600') == 2 + 1 + 2 + 2 + 6 + 2 + 4
    assert compact({"a": [1, 1.5]}) == '{"a":[1,1.5]}'


# --- The measure is the transports' --------------------------------------------------------------


def _examples() -> list[tuple[str, list[Spec]]]:
    found: list[tuple[str, list[Spec]]] = [("core", [])]
    for name, character in CLASSES.items():
        found.append(
            (
                name,
                [
                    Spec(id="p00", version="1.0.0", analyses=2, pad=character * 50, methods=3),
                    Spec(id="p01", version="2.0.1", kinds=3, at="kind", pad=character * 70),
                    Spec(id="p02", version="0.3", analyses=1, kinds=1, full=True, caveats=3),
                ],
            )
        )
    found.append(("values", [Spec(id="p00", analyses=1, zeros=500), Spec(id="p01", kinds=1)]))
    found.append(("kind zeros", [Spec(id="p00", kinds=2, at="kind", zeros=300)]))
    escapes = (
        "".join(chr(code) for code in range(0x20)) + "\x7f\x80\x9f\u2028\u2029\ufeff\U000f0000"
    )
    found.append(
        (
            "escapes",
            [
                Spec(id="p00", analyses=1, pad=escapes * 9),
                Spec(id="p01", kinds=2, at="kind", pad=escapes * 7),
            ],
        )
    )
    numbers = json.dumps(
        [-0.0, 5e-324, 1 / 3, 2**53 - 1, -(2**53 - 1), 0.1, 1e15, None, True, False,
         {"\U0001f600": 1, "\u00e9\n": [{}], "": []}]
    )  # fmt: skip
    found.append(
        (
            "numbers",
            [
                Spec(id="p00", analyses=1, examples=numbers),
                Spec(id="p01", kinds=1, at="kind", examples=numbers),
            ],
        )
    )
    shapes = (("object", 1, 3), ("array", 1, 3), ("array", 2, 2), ("keys", 3, 2), ("object", 8, 2))
    found.append(
        (
            "containers",
            [
                Spec(id="p00", analyses=1, nests=shapes),
                Spec(id="p01", kinds=1, at="kind", nests=(("object", 60, 1), ("array", 30, 1))),
            ],
        )
    )
    return found


EXAMPLES = _examples()


@pytest.mark.parametrize(("name", "specs"), EXAMPLES, ids=[name for name, _ in EXAMPLES])
def test_the_measure_is_what_both_transports_serve(
    harness: Harness, name: str, specs: list[Spec]
) -> None:
    """For each listing the server's measure equals the real transports': its body is the HTTP
    body; its bytes are the MCP answer's less the envelope; its values are the body's; and its
    shares, each pack's and the core's and the framing's, are the body's split by owner."""
    registry = registry_of(specs)
    found = dict(zip(TOOLS, measured(registry), strict=True))
    wires = harness.both(registry)
    for tool in TOOLS:
        wire = wires[tool]
        truth = oracle(wire, tool, owner_of(tool))
        ours = found[tool]
        assert (ours.tool, ours.values, ours.containers, ours.bytes) == (
            tool,
            truth.values,
            truth.containers,
            truth.bytes,
        )
        assert form_bytes(wire.http.decode()) == truth.bytes
        assert json_counts(json.loads(wire.http)) == (truth.values, truth.containers)
        assert [share.owner for share in ours.shares] == [CORE_OWNER, *registry.ids, FRAMING]
        for share in ours.shares:
            counts = (share.values, share.containers, share.bytes)
            if share.owner == FRAMING:
                assert counts == truth.framing
            else:
                assert counts == truth.shares.get(share.owner, (0, 0, 0))


def test_the_listings_are_what_the_service_gives(tmp_path: Path) -> None:
    """The check measures what the service functions build: ``Analyses.entries`` and
    ``leaf_kind_listing``, with no dataset."""
    registry = registry_of(EXAMPLES[3][1])
    entries = Analyses(registry).entries()
    with transports(tmp_path, registry) as call:
        assert (
            call("list_analyses", {}).http
            == AnalysisListing(analyses=entries).model_dump_json().encode()
        )


# --- The byte cap, per string class --------------------------------------------------------------


@dataclass(frozen=True)
class Costs:
    """What each character adds to a listing's form, measured by the oracle."""

    base: int
    each: dict[str, int]


def costs(
    harness: Harness, tool: str, at: Literal["analysis", "kind"], names: tuple[str, ...]
) -> Costs:
    """The form of a listing of one item with an empty ``description``, and what each character
    of each class adds to it, from one answer of the transports: packs of ids of one length
    (``p00``…), each one item padded with ten or twenty characters of a class, the shares split
    from the body by the oracle."""
    chosen = sorted({*names, "ascii", "control"})
    padded = [(name, count) for name in chosen for count in (10, 20)]
    specs = [
        Spec(id=f"p{n:02d}", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at,
             pad=CLASSES[name] * count)
        for n, (name, count) in enumerate(padded)
    ]  # fmt: skip
    truth = oracle(harness.wires(specs, tool), tool, owner_of(tool))
    core = len(CORE_IDS) if tool == "list_analyses" else 0
    bare = truth.framing[2] - 2 * (core + len(specs) - 1)
    alone = truth.shares.get(CORE_OWNER, (0, 0, 0))[2] + bare + 2 * core
    shares = {padded[n]: truth.shares[spec.id][2] for n, spec in enumerate(specs)}
    each = {name: (shares[name, 20] - shares[name, 10]) // 10 for name in chosen}
    assert all(shares[name, 20] - shares[name, 10] == 10 * each[name] for name in chosen)
    bases = {alone + shares[name, 10] - 10 * each[name] for name in chosen}
    [base] = bases
    assert (each["ascii"], each["control"]) == (2, 13)
    return Costs(base, each)


def at_cap(given: Costs, name: str, cap: int) -> str:
    """A pad of ``name``'s character, ASCII and control characters that makes the form exactly
    ``cap`` bytes, at least six of them ASCII, by the oracle's costs."""
    unit, ascii_, control = given.each[name], given.each["ascii"], given.each["control"]
    character = CLASSES[name]
    room = cap - given.base
    for drop in range(0, 64):
        count = room // unit - drop
        for controls in range(0, 8):
            rest = room - count * unit - controls * control
            if rest >= 6 * ascii_ and rest % ascii_ == 0:
                return character * count + "a" * (rest // ascii_) + "\x01" * controls
    raise AssertionError("no pad")


def over(pad: str) -> str:
    """The pad with six ASCII characters made one control character: one byte more."""
    assert pad.count("a") >= 6
    head, _, _ = pad.rpartition("a" * 6)
    return head + "\x01" + pad[len(head) + 6 :]


WIRED = ("ascii", "astral")
"""The classes whose at-cap and one-over registries are also served whole, 4 MiB over MCP."""


@pytest.mark.parametrize("tool", list(TOOLS))
def test_each_string_class_at_the_byte_cap_loads_and_one_byte_more_is_refused(
    harness: Harness, tool: str
) -> None:
    """Each class's pad, built from the oracle's per-character costs, makes the form exactly
    the cap and the check passes it, and one byte more is refused naming every share. For ASCII
    and astral text the whole 4 MiB wires are served and the registries go through the loader;
    for the others (``list_leaf_kinds``', whose pack text is escaped as ``list_analyses``') the
    measure, held to the wire for every class by the transport tests, is checked by the
    loader's own check (``served_problems``)."""
    at: Literal["analysis", "kind"] = "analysis" if tool == "list_analyses" else "kind"
    names = WIRED if tool == "list_analyses" else tuple(CLASSES)
    given = costs(harness, tool, at, names)
    for name in names:
        pad = at_cap(given, name, MAX_SERVED_BYTES)
        assert pad.count(CLASSES[name]) * given.each[name] > MAX_SERVED_BYTES // 2
        specs = [Spec(id="p00", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at,
                      pad=pad)]  # fmt: skip
        beyond = [replace(specs[0], pad=over(pad))]
        index = list(TOOLS).index(tool)
        if name not in WIRED:
            assert measured(registry_of(specs))[index].bytes == MAX_SERVED_BYTES, name
            assert served_problems(registry_of(specs)) == [], name
            found = measured(registry := registry_of(beyond))[index]
            assert found.bytes == MAX_SERVED_BYTES + 1, name
            assert served_problems(registry) == [mine(found, {}, "bytes")], name
            continue
        assert form_of(harness.wires(specs, tool)) == MAX_SERVED_BYTES, name
        loaded, labels = harness.load(specs)
        assert loaded.problems == (), name
        assert loaded.registry is not None, name
        wire = harness.wires(beyond, tool)
        assert form_of(wire) == MAX_SERVED_BYTES + 1, name
        loaded, labels = harness.load(beyond)
        assert loaded.registry is None
        truth = oracle(wire, tool, owner_of(tool))
        assert loaded.problems == (problem(tool, truth, ["p00"], labels, "bytes"),), name


# --- The value cap -------------------------------------------------------------------------------


def _tail(specs: list[Spec], tail: int) -> list[Spec]:
    """``tail`` more assumptions over the packs, as evenly as they go."""
    return [
        replace(spec, tail=tail // len(specs) + (1 if n < tail % len(specs) else 0))
        for n, spec in enumerate(specs)
    ]


MODEL_DENSE = spread(1000, requires=4, extra=6, methods=64, assumptions=40, caveats=48)
"""1,000 analyses in 16 packs, 4,096 requirements, 64 methods, 40 assumptions and 48 caveats
each: tiny members, near the value cap and within the byte cap."""


@pytest.fixture(scope="module")
def model_dense(tmp_path_factory: pytest.TempPathFactory) -> Oracle:
    """``MODEL_DENSE``'s ``list_analyses`` as the transports serve it, once for the module."""
    with transports(tmp_path_factory.mktemp("dense"), registry_of(MODEL_DENSE)) as call:
        return oracle(call("list_analyses", {}), "list_analyses", owner_of("list_analyses"))


def test_model_dense_entries_at_the_value_cap_load_and_one_value_more_is_refused(
    harness: Harness, model_dense: Oracle
) -> None:
    """The cap counts every member of every model, the core's included: an at-cap registry of
    tiny members loads, and one value more is refused naming the limit and every share, the
    same in either order of the modules."""
    base = model_dense
    assert 150_000 < base.values < MAX_SERVED_VALUES
    assert base.shares[CORE_OWNER][0] > 9_000
    assert base.containers < MAX_SERVED_CONTAINERS
    loaded, _ = harness.load(_tail(MODEL_DENSE, MAX_SERVED_VALUES - base.values), -1)
    assert loaded.problems == ()
    assert loaded.registry is not None
    found = measured(loaded.registry)[0]
    assert (found.values, found.containers) == (MAX_SERVED_VALUES, base.containers)
    assert found.bytes <= MAX_SERVED_BYTES
    beyond = _tail(MODEL_DENSE, MAX_SERVED_VALUES - base.values + 1)
    seen: set[str] = set()
    for order in (1, -1):
        loaded, _ = harness.load(beyond, order)
        assert loaded.registry is None
        [refused] = loaded.problems
        seen.add(re.sub(r"the module \S+ \(pack (p\d\d)\)", r"\1", refused))
        assert refused.startswith(
            f"list_analyses would serve {MAX_SERVED_VALUES + 1} JSON values, more than the "
            f"{MAX_SERVED_VALUES} allowed (MAX_SERVED_VALUES): the core has "
            f"{base.shares[CORE_OWNER][0]}, the module "
        )
        shares = refused.split(": ", 1)[1].split(", ")
        assert [share.split(" (pack ")[-1].split(")")[0] for share in shares[1:-1]] == [
            f"p{p:02d}" for p in range(16)
        ]
        assert sum(int(share.rsplit(" ", 1)[1]) for share in shares) == MAX_SERVED_VALUES + 1
    assert len(seen) == 1


@pytest.mark.parametrize("tool", list(TOOLS))
def test_values_dense_listings_at_the_value_cap_load_and_one_value_more_is_refused(
    harness: Harness, tool: str
) -> None:
    at: Literal["analysis", "kind"] = "analysis" if tool == "list_analyses" else "kind"
    zero = Spec(id="p00", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at)
    base = oracle(harness.wires([zero], tool), tool, owner_of(tool))
    for extra in (0, 1):
        specs = [replace(zero, zeros=MAX_SERVED_VALUES - base.values - 1 + extra)]
        found = measured(registry_of(specs))[list(TOOLS).index(tool)]
        assert found.values == MAX_SERVED_VALUES + extra
        assert found.bytes < MAX_SERVED_BYTES // 2
        loaded, labels = harness.load(specs)
        if not extra:
            assert loaded.problems == ()
            assert loaded.registry is not None
        else:
            assert loaded.problems == (mine(found, labels, "values"),)


def test_each_listing_is_bounded_on_its_own_never_summed(harness: Harness) -> None:
    """Both listings at their value caps, both near their byte caps, and both at their
    container caps, load: the caps are each listing's, not the two's together."""
    analyses = oracle(
        harness.wires([Spec(id="p00", analyses=1)], "list_analyses"),
        "list_analyses",
        owner_of("list_analyses"),
    )
    kinds = oracle(
        harness.wires([Spec(id="p01", kinds=1, at="kind")], "list_leaf_kinds"),
        "list_leaf_kinds",
        owner_of("list_leaf_kinds"),
    )

    def room(found: Oracle) -> int:  # the enum's list is one container
        return MAX_SERVED_CONTAINERS - found.containers - 1

    specs = [
        Spec(id="p00", analyses=1, zeros=MAX_SERVED_VALUES - analyses.values - 1),
        Spec(id="p01", kinds=1, at="kind", zeros=MAX_SERVED_VALUES - kinds.values - 1),
    ]
    loaded, _ = harness.load(specs)
    assert loaded.problems == ()
    assert loaded.registry is not None
    found = measured(loaded.registry)
    assert [one.values for one in found] == [MAX_SERVED_VALUES, MAX_SERVED_VALUES]
    near = MAX_SERVED_BYTES - 64
    specs = [
        Spec(id="p00", analyses=1, pad="a" * ((near - analyses.bytes) // 2)),
        Spec(id="p01", kinds=1, at="kind", pad="a" * ((near - kinds.bytes) // 2)),
    ]
    loaded, _ = harness.load(specs)
    assert loaded.problems == ()
    assert loaded.registry is not None
    assert all(near - 2 <= one.bytes <= MAX_SERVED_BYTES for one in measured(loaded.registry))
    specs = [
        Spec(id="p00", analyses=1, nests=(("object", 1, room(analyses)),)),
        Spec(id="p01", kinds=1, at="kind", nests=(("object", 1, room(kinds)),)),
    ]
    loaded, _ = harness.load(specs)
    assert loaded.problems == ()
    assert loaded.registry is not None
    assert [one.containers for one in measured(loaded.registry)] == [MAX_SERVED_CONTAINERS] * 2


# --- The container cap ---------------------------------------------------------------------------

SHAPES = {
    "empty objects": ("object", 1),
    "empty arrays": ("array", 1),
    "objects of keys": ("keys", 4),
    "objects of astral keys": ("astral keys", 9),
    "objects of keys holding astral strings": ("astral values", 6),
    "arrays 30 deep": ("array", 30),
    "objects 60 deep": ("object", 60),
    "objects 60 deep under astral keys": ("astral chain", 60),
}
"""Each container shape, and how deep (or how many keys) one item of it is."""


def containers_at(
    base: int, shape: str, depth: int, extra: int
) -> tuple[tuple[str, int, int], ...]:
    """Items of ``shape`` and empty objects that bring a listing of ``base`` containers to the
    cap and ``extra`` more, by the definition (an item of depth d is d containers; of keys, 1)."""
    each = depth if shape in ("object", "array", "astral chain") else 1
    room = MAX_SERVED_CONTAINERS - base + extra
    return ((shape, depth, room // each), ("object", 1, room % each))


LOADED = {"list_analyses": ("empty arrays",), "list_leaf_kinds": ("objects 60 deep",)}
"""The shapes whose at-cap and one-over registries are served whole and loaded; ``list_analyses``
takes no other (its pack data is counted by the same function as ``list_leaf_kinds``')."""


@pytest.mark.parametrize("tool", list(TOOLS))
def test_each_container_shape_at_the_container_cap_loads_and_one_more_is_refused(
    harness: Harness, tool: str
) -> None:
    """For every shape, built by the definition, a listing at the container cap passes the
    check and one container more is refused naming every share, the values and bytes within
    their caps throughout; for one shape of each listing the oracle counts the real wire and the
    registries go through the loader, and for the others (``list_leaf_kinds``') the measure,
    held to the wire for every shape by the transport tests, is checked by the loader's own
    check."""
    at: Literal["analysis", "kind"] = "analysis" if tool == "list_analyses" else "kind"
    zero = Spec(id="p00", analyses=int(at == "analysis"), kinds=int(at == "kind"), at=at,
                nests=(("object", 1, 1),))  # fmt: skip
    base = oracle(harness.wires([zero], tool), tool, owner_of(tool)).containers - 1
    index = list(TOOLS).index(tool)
    found: Measured | None = None
    for name, (shape, depth) in SHAPES.items():
        if tool == "list_analyses" and name not in LOADED[tool]:
            continue
        for extra in (0, 1):
            specs = [replace(zero, nests=containers_at(base, shape, depth, extra))]
            if name not in LOADED[tool] and not extra:
                # At the cap, measured; one over, the check (one measure each).
                found = measured(registry_of(specs))[index]
                assert found.containers == MAX_SERVED_CONTAINERS, name
                assert found.values <= MAX_SERVED_VALUES, name
                assert found.bytes <= MAX_SERVED_BYTES, name
                continue
            if name not in LOADED[tool]:
                assert found is not None
                shares = tuple(
                    replace(share, containers=share.containers + (share.owner == "p00"))
                    for share in found.shares
                )
                beyond = replace(found, containers=found.containers + 1, shares=shares)
                assert served_problems(registry_of(specs)) == [mine(beyond, {}, "containers")]
                continue
            truth = oracle(harness.wires(specs, tool), tool, owner_of(tool))
            assert truth.containers == MAX_SERVED_CONTAINERS + extra, name
            assert truth.values <= MAX_SERVED_VALUES, name
            loaded, labels = harness.load(specs)
            if extra:
                assert loaded.problems == (problem(tool, truth, ["p00"], labels, "containers"),)
            else:
                assert loaded.problems == (), name
                assert loaded.registry is not None, name


def test_both_listings_over_their_caps_name_both_problems(harness: Harness) -> None:
    """A registry whose two listings are each over a cap is refused for both, ``list_analyses``
    first."""
    analyses = Spec(id="p00", analyses=1, nests=(("object", 1, MAX_SERVED_CONTAINERS),))
    kinds = Spec(id="p01", kinds=1, at="kind", nests=(("array", 1, MAX_SERVED_CONTAINERS),))
    loaded, labels = harness.load([analyses, kinds])
    expected = [
        mine(found, labels, "containers") for found in measured(registry_of([analyses, kinds]))
    ]
    assert loaded.problems == tuple(expected)
    assert expected[0].startswith("list_analyses would serve ")
    assert expected[1].startswith("list_leaf_kinds would serve ")


# --- check and serve, and the bypass ------------------------------------------------------------


class _Stopped:
    """uvicorn's server, stopped as soon as it runs."""

    def __init__(self, config: Any) -> None:
        self.config = config

    def run(self) -> None:
        return None


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = serve.main(list(argv), environ={}, stdin=io.StringIO(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _config_file(harness: Harness, modules: list[str]) -> Path:
    directory = harness.directory()
    (directory / "imports").mkdir()
    listed = ", ".join(f'"{name}"' for name in modules)
    path = directory / "aibi.toml"
    path.write_text(
        f'[curator]\ntoken_hash = "sha256:{"0" * 64}"\n'
        '[storage]\ndata = "data"\nimports = ["imports"]\n'
        f"[packs]\nmodules = [{listed}]\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_check_and_serve_refuse_the_same_registries(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one check is the loader's: ``check`` and ``serve`` refuse a registry over a served
    cap with the same problem, and both start with it at the cap."""
    monkeypatch.setattr(serve.uvicorn, "Server", _Stopped)
    zero = Spec(id="p00", analyses=1, nests=(("object", 1, 1),))
    tool = "list_analyses"
    base = oracle(harness.wires([zero], tool), tool, owner_of(tool)).containers - 1
    for extra in (1, 0):
        spec = replace(zero, nests=(("object", 1, MAX_SERVED_CONTAINERS - base + extra),))
        names = write_modules(harness.modules, [spec, Spec(id="p01", kinds=2, at="kind")])
        path = _config_file(harness, names)
        checked = _run("check", "--config", str(path))
        served = _run("serve", "--config", str(path))
        if extra:
            assert checked[0] == served[0] == 2
            assert checked[1] == ""
            [line] = [line for line in checked[2].splitlines() if "MAX_SERVED_CONTAINERS" in line]
            assert line in served[2].splitlines()
            assert line.startswith(
                f"  list_analyses would serve {MAX_SERVED_CONTAINERS + 1} JSON objects and arrays"
            )
        else:
            assert (checked[0], served[0]) == (0, 0)


def test_a_registry_built_directly_bypasses_the_check(harness: Harness) -> None:
    """The loader is the one check: a ``PackRegistry`` built directly over the cap registers,
    and a catalogue built from it serves it (tests and embeddings; D421), while the loader
    refuses the same packs."""
    specs = [Spec(id="p00", analyses=1, nests=(("object", 1, MAX_SERVED_CONTAINERS),))]
    registry = registry_of(specs)
    wire = harness.wire(registry, "list_analyses")
    assert containers_of(json.loads(wire.http)) > MAX_SERVED_CONTAINERS
    assert served_problems(registry) != []
    loaded, _ = harness.load(specs)
    assert loaded.registry is None
    assert "MAX_SERVED_CONTAINERS" in loaded.problems[0]


# --- What applicability adds ---------------------------------------------------------------------


PER_ANALYSIS = len(
    compact(
        {
            "analysis": "",
            "version": "",
            "status": "available_with_caveats",
            "missing": ["allow_row_ids", "columns", "min_cell_count", "unit"],
            "unconfirmed": [],
        }
    )
)
"""The characters of an applicability item without its id, version and roles, at its largest."""


def test_applicability_adds_at_most_the_ids_and_roles_again() -> None:
    """With a dataset, ``list_analyses`` and ``describe_dataset`` add each analysis's
    applicability: its id, version and the roles it misses or has unconfirmed, each a role its
    entry names, so at most the entries' form again plus a fixed frame per analysis
    (``PER_ANALYSIS``, its MCP bytes at most three times its characters). Measured at the model-
    dense registry within the byte cap, every requirement a column the release lacks."""
    specs = spread(1000, requires=4, extra=6, methods=40, assumptions=40, caveats=40, full=True)
    registry = registry_of(specs)
    entries, _ = measured(registry)
    assert MAX_SERVED_BYTES // 2 < entries.bytes <= MAX_SERVED_BYTES
    release = [build.table(f"t{n}", ["c0"]) for n in range(3)]
    applicable = Analyses(registry).applicable(release, dataset="d", manifest="m")
    count = len(applicable)
    assert count == len(Analyses(registry).entries())
    assert count > 1_000
    assert sum(len(item.missing) for item in applicable) >= 4_096
    text = compact([item.model_dump(mode="json") for item in applicable])
    added = len(text.encode()) + string_bytes(text)
    assert added <= entries.bytes + 3 * PER_ANALYSIS * count
    assert added + entries.bytes <= 2 * MAX_SERVED_BYTES + 3 * PER_ANALYSIS * count


# --- The builders' shapes ------------------------------------------------------------------------


def test_the_builders_make_the_shapes_the_tests_rely_on() -> None:
    """The registries the bounds are taken to: 1,000 analyses in 16 packs of 16 versions with
    4,096 requirements, a tail spread to the last analyses, kinds spread alike, every string
    class's character distinct and of the bytes its name says."""
    assert sum(spec.analyses for spec in MODEL_DENSE) == 1_000
    assert len({spec.version for spec in MODEL_DENSE}) == 16
    assert sorted(spec.id for spec in MODEL_DENSE) == [f"p{p:02d}" for p in range(16)]
    registry = registry_of(_tail(MODEL_DENSE, 1_234))
    analyses = registry.analyses()
    assert sum(len(a.entry.fields.requires) for a in analyses) == 4_096
    assert sum(len(a.entry.fields.assumptions) for a in analyses) == 40 * 1_000 + 1_234
    assert max(len(a.entry.fields.assumptions) for a in analyses) <= 64
    kinds = spread(1_001, kinds=True)
    assert sum(spec.kinds for spec in kinds) == 1_001
    assert [spec.kinds for spec in kinds] == [63] * 9 + [62] * 7
    assert len(set(CLASSES.values())) == len(CLASSES)
    widths = {name: len(character.encode()) for name, character in CLASSES.items()}
    assert widths == {
        "ascii": 1, "control": 1, "quote": 1, "backslash": 1, "bmp2": 2, "bmp3": 3, "astral": 4,
    }  # fmt: skip


def test_the_caps_are_the_measured_ones_and_the_core_s_listing_is_well_within() -> None:
    """The caps are the figures D421 measured and states, pinned; the core's own listing is a
    fraction of each, so that packs have most of every cap."""
    from aibi.core.schema.limits import (
        MAX_LEAF_KINDS,
        MAX_PACK_ANALYSES,
        MAX_PACK_CHARACTERS,
        MAX_PACK_CONCEPTS,
        MAX_PACK_REQUIREMENTS,
        MAX_PACK_VALUES,
    )

    assert (
        MAX_SERVED_VALUES,
        MAX_SERVED_CONTAINERS,
        MAX_SERVED_BYTES,
        MAX_PACK_VALUES,
        MAX_PACK_CHARACTERS,
        MAX_LEAF_KINDS,
        MAX_PACK_ANALYSES,
        MAX_PACK_REQUIREMENTS,
        MAX_PACK_CONCEPTS,
    ) == (200_000, 20_000, 4 * 2**20, 500_000, 8 * 2**20, 1_000, 1_000, 4_096, 2_000)
    core, kinds = measured(None)
    assert (core.values, core.containers) == (9_451, 4_509)
    assert 4 * core.values < MAX_SERVED_VALUES
    assert 4 * core.containers < MAX_SERVED_CONTAINERS
    assert 4 * core.bytes < MAX_SERVED_BYTES
    assert (kinds.values, kinds.containers) == (2, 2)
