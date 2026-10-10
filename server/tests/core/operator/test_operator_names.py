"""The web client's operator-name vectors (D262, D423): ``web/tests/fixtures/operator-names.json``,
which the web package's tests hold the browser's name rules to, is what
``scripts/export_operator_names.py`` writes from the server's own ``valid_name``,
``encode_operator`` and ``attribution`` now. A change to the server's name rules fails here until
the file is written again, so that the client's tests see it in the same change."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from aibi.core.operator.auth import attribution, encode_operator, valid_name

EXPORT = Path(__file__).resolve().parents[3] / "scripts" / "export_operator_names.py"


def _export() -> ModuleType:
    """``scripts/export_operator_names.py`` as a module, by its path (it is no package)."""
    spec = importlib.util.spec_from_file_location("export_operator_names", EXPORT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def test_the_web_client_s_name_vectors_are_the_server_s() -> None:
    script = _export()
    assert script.TARGET.read_text(encoding="utf-8") == script.render()


def test_the_vectors_carry_the_verdicts_the_review_named() -> None:
    """The boundaries, as the server decides them: 200 astral characters (400 UTF-16 units) are a
    name and 201 are not; U+FFFD, a tag character and spaces alone are names; a lone surrogate is
    not; lower-case hexadecimal and a raw ``!`` are no header."""
    script = _export()
    verdicts = {entry["why"]: entry["valid"] for entry in script.names()}
    assert verdicts["200 astral characters (400 UTF-16 units)"] is True
    assert verdicts["201 astral characters"] is False
    assert verdicts["199 ASCII and one astral: 200 code points, 201 UTF-16 units"] is True
    assert verdicts["U+FFFD"] is True
    assert verdicts["a tag character"] is True
    assert verdicts["spaces only"] is True
    assert verdicts["a lone high surrogate"] is False
    assert verdicts["a token alone"] is False
    assert verdicts["a token percent-encoded three times"] is False
    assert verdicts["a token percent-encoded four times"] is True
    read = {entry["header"]: entry["points"] for entry in script.headers()}
    assert read["%c3%a9"] is None
    assert read["%C3%A9"] == [0xE9]
    assert read["Ada!"] is None
    assert read["%21"] == [0x21]


def test_the_whole_domain_is_what_the_functions_say() -> None:
    """The file's ranges and digest are the functions' own, read back (the generator's control)."""
    data = json.loads(_export().TARGET.read_text(encoding="utf-8"))
    invalid = {point for first, last in data["invalid"] for point in range(first, last + 1)}
    for point in (0x00, 0x1F, 0x20, 0x7E, 0x7F, 0x9F, 0xA0, 0x061C, 0x2028, 0xD800, 0xDFFF):
        assert (point in invalid) is (not valid_name(chr(point)))
    assert len(invalid) == 32 + 1 + 32 + 2 + 12 + 2048 + 32 + 34
    assert encode_operator("!'()*") == "%21%27%28%29%2A"
    assert attribution("%21%27%28%29%2A") == "operator:!'()*"
