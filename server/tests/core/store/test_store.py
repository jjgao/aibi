"""The store (SPEC §12.2, §12.3, D221): labels and their statuses, resolution, withdrawal, pins
and the sweep that deletes what no live release references."""

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from aibi.core.store.blobs import CorruptBlobError, MissingBlobError
from aibi.core.store.manifest import hex_of
from aibi.core.store.sources import SourceValue
from aibi.core.store.store import Pin, Store, StoreLockedError, StoreRefused

Library = Any


def _import(store: Store, library: Library, loans: Any = None, pin: Pin | None = None) -> str:
    """A release of the library, with other loans if given; pinned by ``pin`` if given."""
    sources = library.sources() if loans is None else library.sources(loans=loans)
    held = pin or store.pin()
    try:
        built = store.import_release(held, "lib", library.descriptors(), sources, library.layouts)
    finally:
        if pin is None:
            held.release()
    return built.manifest.hash


def _loans(count: int) -> tuple[tuple[SourceValue, ...], ...]:
    return tuple((index, "m-1", "b-1", None, float(index)) for index in range(1, count + 1))


def test_publishing_gives_the_next_label_and_resolution_finds_it(
    store: Store, library: Library, imported: str
) -> None:
    with store.pin() as pin:
        second = _import(store, library, _loans(2), pin=pin)
        assert store.publish("lib", second, "operator:ada") == 2
    assert store.resolve("lib").manifest == second
    assert store.resolve("lib", 1).manifest == imported
    assert store.resolve("lib", imported).label == 1
    for missing in (3, "sha256:" + "0" * 64, "draft", "latest", True):
        with pytest.raises(StoreRefused):
            store.resolve("lib", missing)


def test_withdrawal_applies_to_every_label_of_a_manifest(
    store: Store, library: Library, imported: str
) -> None:
    with store.pin() as pin:
        pin.manifest(imported)
        assert store.publish("lib", imported, "operator:ada") == 2  # the same release again
    purged: list[str] = []
    store.on_withdraw(purged.append)
    assert store.withdraw("lib", 1, "operator:ada") == [1, 2]
    assert purged == [imported]
    assert [label.withdrawn for label in store.labels("lib")] == [True, True]
    assert store.resolve("lib", 2).status == "withdrawn"
    with pytest.raises(StoreRefused) as refused:
        store.resolve("lib")
    assert refused.value.refusal.code == "UNKNOWN_RELEASE"
    with pytest.raises(StoreRefused) as again:
        store.publish("lib", imported, "operator:ada")
    assert again.value.refusal.code == "RELEASE_WITHDRAWN"


def test_the_sweep_keeps_live_releases_and_withdrawn_manifests_alone(
    store: Store, library: Library, imported: str
) -> None:
    manifest = store.manifest(imported)
    unpublished = _import(store, library, _loans(1))
    assert not store.blobs.exists(hex_of(unpublished))  # swept when its pin was released
    assert all(store.blobs.exists(blob) for blob in manifest.blobs())
    store.withdraw("lib", 1, "operator:ada")
    assert store.blobs.digests() == [hex_of(imported)]
    assert store.resolve("lib", 1).status == "withdrawn"  # its id still resolves
    with pytest.raises(StoreRefused) as refused:  # its blobs are gone, and it is withdrawn
        store.publish("lib", imported, "operator:ada")
    assert refused.value.refusal.code == "RELEASE_WITHDRAWN"


def test_a_pinned_release_survives_withdrawal_until_its_pin_is_released(
    store: Store, imported: str
) -> None:
    manifest = store.manifest(imported)
    pin = store.pin()
    pin.manifest(imported)
    store.withdraw("lib", 1, "operator:ada")
    assert all(store.blobs.exists(blob) for blob in manifest.blobs())
    assert store.load(imported).dataset == "lib"  # a query that pinned it still reads it
    pin.release()
    assert store.blobs.digests() == [hex_of(imported)]
    pin.release()  # releasing twice changes nothing


def test_blobs_written_by_an_operation_are_kept_until_it_ends(
    store: Store, library: Library
) -> None:
    with store.pin() as pin:
        built = _import(store, library, pin=pin)
        store.sweep()
        assert store.blobs.exists(hex_of(built))
    assert not store.blobs.exists(hex_of(built))


def test_a_release_with_a_missing_blob_is_not_published(store: Store, library: Library) -> None:
    with store.pin() as pin:
        built = _import(store, library, pin=pin)
        table = store.manifest(built).tables[0].hash
        store.blobs.delete(table)
        with pytest.raises(StoreRefused) as refused:
            store.publish("lib", built, "operator:ada")
    assert refused.value.refusal.code == "UNKNOWN_RELEASE"


def test_a_table_blob_that_does_not_hold_its_bytes_is_not_loaded(
    store: Store, imported: str
) -> None:
    books = store.manifest(imported).table("books")
    assert books is not None
    path = store.blobs.path(books.hash)
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"SICP", b"XICP"))
    with pytest.raises(CorruptBlobError):
        store.load(imported)


def test_releasing_a_pin_sweeps_only_when_it_freed_what_nothing_live_references(
    store: Store, library: Library, imported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweeps: list[list[str]] = []
    sweep = store.sweep

    def counted() -> list[str]:
        sweeps.append(sweep())
        return sweeps[-1]

    monkeypatch.setattr(store, "sweep", counted)
    with store.pin() as query:
        query.manifest(imported)
    assert sweeps == []  # a query on a live release ends without a scan of the blobs
    unpublished = _import(store, library, _loans(1))
    assert len(sweeps) == 1
    assert hex_of(unpublished) in sweeps[0]


def test_a_second_process_cannot_open_the_store(tmp_path: Path) -> None:
    """Opening sweeps, so a second writer would delete what the first has pinned (D221)."""
    first = Store(tmp_path / "data")
    try:
        with first.pin() as pin:
            written = first.blobs.put(b"being written", pin)
            with pytest.raises(StoreLockedError):
                Store(tmp_path / "data")
            assert first.blobs.exists(written)
    finally:
        first.close()
    Store(tmp_path / "data").close()


def test_a_store_that_fails_to_open_releases_its_lock(tmp_path: Path, library: Library) -> None:
    root = tmp_path / "data"
    store = Store(root)
    with store.pin() as pin:
        imported = _import(store, library, pin=pin)
        store.publish("lib", imported, "operator:ada")
    store.close()
    path = root / "blobs" / hex_of(imported)
    path.chmod(0o644)
    path.unlink()  # a live manifest is missing, so opening cannot sweep
    for _ in range(2):  # not StoreLockedError the second time
        with pytest.raises(MissingBlobError):
            Store(root)


def test_pinning_a_release_with_a_missing_blob_fails(store: Store, imported: str) -> None:
    books = store.manifest(imported).table("books")
    assert books is not None
    store.blobs.delete(books.hash)
    with store.pin() as pin, pytest.raises(MissingBlobError):
        pin.manifest(imported)


def test_a_release_is_published_only_into_its_own_dataset(store: Store, imported: str) -> None:
    with pytest.raises(StoreRefused) as refused:
        store.publish("other", imported, "operator:ada")
    assert refused.value.refusal.code == "INVALID_VALUE"
    assert store.labels("other") == []


def test_a_publish_is_recorded_only_as_an_action_that_names_releases(
    store: Store, imported: str
) -> None:
    with pytest.raises(ValueError, match="names releases"):
        store.publish("lib", imported, "operator:ada", action="note")


class _Store(RuleBasedStateMachine):
    """Random publishes, withdrawals, pins and sweeps: every live release stays whole, every
    withdrawn manifest stays, and a sweep leaves nothing else unpinned."""

    library: Library = None

    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "data")
        self.releases: list[str] = []
        self.pins: list[Pin] = []

    def teardown(self) -> None:
        for pin in self.pins:
            pin.release()
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    @rule(loans=st.integers(0, 3))
    def build(self, loans: int) -> None:
        """A release built by an operation that holds it until an unpin ends it."""
        pin = self.store.pin()
        self.pins.append(pin)
        given = _loans(loans) if loans else None
        self.releases.append(_import(self.store, self.library, given, pin=pin))

    @precondition(lambda self: self.releases)
    @rule(data=st.data())
    def publish(self, data: st.DataObject) -> None:
        manifest = data.draw(st.sampled_from(self.releases))
        with contextlib.suppress(StoreRefused):  # withdrawn, or swept before it was published
            self.store.publish("lib", manifest, "operator:ada")

    @precondition(lambda self: self.store.labels("lib"))
    @rule(data=st.data())
    def withdraw(self, data: st.DataObject) -> None:
        label = data.draw(st.sampled_from(self.store.labels("lib")))
        try:
            self.store.withdraw("lib", label.label, "operator:ada")
        except StoreRefused:
            assert label.withdrawn

    @precondition(lambda self: self.store.labels("lib"))
    @rule(data=st.data())
    def pin(self, data: st.DataObject) -> None:
        label = data.draw(st.sampled_from(self.store.labels("lib")))
        pin = self.store.pin()
        try:
            pin.manifest(label.manifest)
        except Exception:
            pin.release()
            return
        self.pins.append(pin)

    @precondition(lambda self: self.pins)
    @rule(data=st.data())
    def unpin(self, data: st.DataObject) -> None:
        pin = data.draw(st.sampled_from(self.pins))
        self.pins.remove(pin)
        pin.release()

    @rule()
    def sweep(self) -> None:
        self.store.sweep()

    @invariant()
    def live_releases_are_whole(self) -> None:
        for manifest in self.store.db.live_manifests():
            assert self.store.blobs.exists(hex_of(manifest))
            assert all(self.store.blobs.exists(b) for b in self.store.manifest(manifest).blobs())
        for manifest in self.store.db.withdrawn_manifests():
            assert self.store.blobs.exists(hex_of(manifest))

    @invariant()
    def pinned_blobs_exist(self) -> None:
        assert all(self.store.blobs.exists(digest) for digest in self.store.pinned)

    @invariant()
    def a_sweep_leaves_only_what_is_kept(self) -> None:
        self.store.sweep()
        kept = self.store.referenced() | set(self.store.pinned)
        assert set(self.store.blobs.digests()) <= kept


def test_the_store_keeps_its_invariants_under_any_sequence(library: Library) -> None:
    _Store.library = library
    runner = _Store.TestCase
    runner.settings = settings(
        max_examples=25,
        stateful_step_count=15,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    runner().runTest()
