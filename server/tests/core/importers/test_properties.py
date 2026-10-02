"""Properties of every file import (SPEC §13.1–§13.2, D227–D230): whatever the files hold, the
importer proposes nothing the gate refuses (a failing proposal is dropped), asserts nothing, and
builds a release the reference evaluator loads."""

import csv
import io
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Imported, import_dataset
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions
from aibi.core.store.store import Pin, Store

TOKENS = st.sampled_from(
    ["", "NA", "?", "x", "y", "1", "2", "10", "0", "1.5", "yes", "no", "2024-01-02", "a;b", "b"]
)
KEYS = st.sampled_from(["k1", "k2", "k3", "k4"])
NAMES = st.sampled_from(["id", "k", "Id", "value", "", "k1", "when", "note"])


@st.composite
def tables(draw: st.DrawFn) -> dict[str, list[list[str]]]:
    found: dict[str, list[list[str]]] = {}
    for index in range(draw(st.integers(1, 3))):
        header = draw(st.lists(NAMES, min_size=1, max_size=4))
        rows = draw(
            st.lists(
                st.lists(st.one_of(TOKENS, KEYS), min_size=len(header), max_size=len(header)),
                max_size=8,
            )
        )
        found[f"t{index}"] = [header, *rows]
    return found


@settings(max_examples=60, deadline=None)
@given(tables())
def test_an_import_of_any_files_passes_its_own_gate(
    given_tables: dict[str, list[list[str]]],
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "in").mkdir()
        for name, rows in given_tables.items():
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            (root / "in" / f"{name}.csv").write_text(buffer.getvalue())
        confinement = Confinement.of(root / "in")
        options = ImportOptions("d", confinement, ImportLimits(), "2026-01-01T00:00:00Z")
        store = Store(root / "store")
        try:
            with store.pin() as pin:
                imported = _import(store, pin, confinement, root / "in", options)
                if isinstance(imported, set):
                    assert imported <= {"UNPARSEABLE_SOURCE"}
                    return
                store.load(imported.built.manifest.hash)
                assert imported.built.gate is not None
                assert imported.built.gate.refusals == ()
                for descriptor in imported.built.descriptors:
                    for entry in descriptor.curation.values():
                        assert entry.status != "asserted"
        finally:
            store.close()


def _import(
    store: Store, pin: Pin, confinement: Confinement, path: Path, options: ImportOptions
) -> Imported | set[str]:
    """The import, or the codes of its refusals."""
    try:
        return import_dataset(store, pin, confinement.confine(path), options)
    except ImportRefused as refused:
        return {str(r.code) for r in refused.refusals}
