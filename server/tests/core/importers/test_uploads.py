"""The upload area (SPEC §12.2, §14, D234): paths of hashes and identifiers only, written whole,
and deleted by an erasure."""

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.uploads import TEMPORARY, UploadArea
from aibi.core.store.erasure import erase
from aibi.core.store.store import Store

Importing = Any


def test_an_upload_is_kept_under_its_hash_and_confined(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    kept = area.put("lib", [b"id,", b"name\n1,a\n"], ".CSV", limit=100)
    digest = hashlib.sha256(b"id,name\n1,a\n").hexdigest()
    assert kept == area.path("lib", digest, "csv").resolve()
    assert re.fullmatch(r"[0-9a-f]{64}\.csv", kept.name)
    assert kept.parent.name == "lib"
    confinement = Confinement.of(area.root)
    assert confinement.read(confinement.confine(kept), 100) == b"id,name\n1,a\n"
    assert area.put("lib", [b"id,name\n1,a\n"], "csv", limit=100) == kept


def test_a_digest_is_64_lowercase_hex_digits(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    for digest in ("g" * 64, "A" * 64, "a" * 63, "a" * 65, "../" + "a" * 61):
        with pytest.raises(ImportRefused) as refused:
            area.path("lib", digest, "csv")
        assert refused.value.refusals[0].code == "INVALID_VALUE"
    assert area.path("lib", "a" * 64, "csv").name == "a" * 64 + ".csv"


def test_a_dataset_that_is_no_identifier_is_refused(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    for dataset in ("../x", "Lib", "a__b", ""):
        with pytest.raises(ImportRefused) as refused:
            area.put(dataset, [b"x"], "csv", limit=100)
        assert refused.value.refusals[0].code == "INVALID_VALUE"


def test_only_the_extensions_read_are_kept(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    with pytest.raises(ImportRefused) as xls:
        area.put("lib", [b"x"], "xls", limit=100)
    assert xls.value.refusals[0].code == "UNSUPPORTED_FORMAT"
    with pytest.raises(ImportRefused) as refused:
        area.put("lib", [b"x"], "exe", limit=100)
    [refusal] = refused.value.refusals
    assert refusal.code == "UNSUPPORTED_FORMAT"
    assert {"data": "exe"} in [segment.model_dump() for segment in refusal.message]
    assert [segment.model_dump()["text"] for segment in refusal.alternatives][:2] == [
        ".csv",
        ".tsv",
    ]


def test_an_upload_over_the_cap_leaves_nothing(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    assert area.put("lib", [b"x" * 100], "csv", limit=100).stat().st_size == 100
    area.delete_dataset("lib")
    with pytest.raises(ImportRefused) as refused:
        area.put("lib", [b"x" * 60, b"x" * 60], "csv", limit=100)
    [refusal] = refused.value.refusals
    assert refusal.limit is not None
    assert refusal.limit.name == "import_bytes"
    assert not (area.root / "lib").exists()
    assert list((tmp_path / TEMPORARY).iterdir()) == []


def test_a_file_being_written_is_inside_no_import_root(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    seen: list[list[Path]] = []

    def chunks() -> Iterator[bytes]:
        yield b"id\n"
        seen.append(list((tmp_path / TEMPORARY).iterdir()))
        yield b"1\n"

    area.put("lib", chunks(), "csv", limit=100)
    [[writing]] = seen
    assert not writing.resolve().is_relative_to(area.root.resolve())
    assert list(area.root.iterdir()) == [area.root / "lib"]


def test_deleting_a_dataset_deletes_its_uploads_only(tmp_path: Path) -> None:
    area = UploadArea(tmp_path)
    area.put("lib", [b"a\n"], "csv", limit=100)
    other = area.put("zoo", [b"b\n"], "csv", limit=100)
    area.delete_dataset("lib")
    assert not (area.root / "lib").exists()
    assert other.exists()
    area.delete_dataset("lib")


def test_an_erasure_deletes_the_dataset_uploads(
    store: Store, importing: Importing, roots: Any
) -> None:
    area = UploadArea(roots.stores)
    members = b"member_id,name\nm1,Ada\nm2,Grace\n"
    kept = area.put("d", [members], "csv", limit=1000)
    roots.confinement = Confinement.of(roots.inside, area.root)
    importing(kept, original_name="members.csv")
    without = roots.write("members.csv", b"member_id,name\nm1,Ada\n")
    importing(without)
    erased = erase(store, "d", "members", ["m2"], "operator:ada", uploads=area.delete_dataset)
    assert erased.withdrawn == (1,)
    assert not kept.exists()
