"""Operation slots (SPEC §12.3, D236): imports, re-imports, withdrawals, erasures and sessions of
one dataset exclude each other, at once and never queued; other datasets are unaffected."""

import threading
from typing import Any

import pytest

from aibi.core.schema.curation import ChangeRequest
from aibi.core.store.erasure import erase
from aibi.core.store.sessions import change, open_session
from aibi.core.store.store import Store, StoreRefused

ADA = "operator:ada"


def code(call: Any, *args: Any) -> str:
    with pytest.raises(StoreRefused) as refused:
        call(*args)
    return str(refused.value.refusal.code)


def test_a_withdrawal_is_refused_while_a_session_is_open(store: Store, imported: str) -> None:
    open_session(store, "lib", ADA)
    assert code(store.withdraw, "lib", 1, ADA) == "DATASET_BUSY"
    assert store.resolve("lib", 1).status == "published"


def test_an_erasure_is_refused_while_another_operation_runs(store: Store, imported: str) -> None:
    with store.exclusive("lib", "import"):
        assert code(erase, store, "lib", "members", ["m-1"], ADA) == "DATASET_BUSY"


def test_session_operations_are_refused_while_an_import_holds_the_slot(
    store: Store, imported: str
) -> None:
    with store.exclusive("lib", "reimport"):
        assert code(open_session, store, "lib", ADA) == "DATASET_BUSY"
    opened = open_session(store, "lib", ADA)
    relabel = ChangeRequest.model_validate(
        {"edits": [{"op": "set", "descriptor": "books", "pointer": "/label", "value": "Books"}]}
    )
    with store.exclusive("lib", "erase"):
        assert code(change, store, "lib", opened.handle, opened.draft, relabel, ADA) == (
            "DATASET_BUSY"
        )


def test_two_operations_on_one_dataset_exclude_each_other_and_another_dataset_proceeds(
    store: Store, imported: str
) -> None:
    found: dict[str, str] = {}

    def run(dataset: str) -> None:
        try:
            with store.exclusive(dataset, "withdraw"):
                found[dataset] = "ran"
        except StoreRefused as refused:
            found[dataset] = str(refused.refusal.code)

    with store.exclusive("lib", "import"):
        threads = [threading.Thread(target=run, args=(name,)) for name in ("lib", "other")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert found == {"lib": "DATASET_BUSY", "other": "ran"}


def test_a_failure_releases_the_slot(store: Store, imported: str) -> None:
    with pytest.raises(RuntimeError), store.exclusive("lib", "import"):
        raise RuntimeError("the import failed")
    assert store.withdraw("lib", 1, ADA) == [1]
