"""The golden result envelopes, for the web client's type gate (D416, D419).

``tests/core/analyses/golden/results.json`` holds documents over the shop; the reference evaluator
runs each into its result envelope (``tests/core/analyses/conftest.py``'s ``analyse_document`` over
``shop_release``, what the golden tests run). This script writes those envelopes, by name, to
``web/tests/fixtures/envelopes.json``, where the web package's gate compiles each as a
``ResultEnvelope`` literal against the generated types (every JSON number a ``ServerNumber``), and
holds the largest within the client's response cap. The file is checked in;
``tests/core/analyses/test_openapi_results.py`` fails while it differs from what the envelopes are
now, so a change to an envelope's shape reaches the client's types in the same change.

Run it from ``server/`` (``--check`` writes nothing and exits 1 while the file is stale):

    uv run python scripts/export_envelopes.py
    uv run python scripts/export_envelopes.py --check
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SERVER = Path(__file__).resolve().parents[1]
ANALYSES = SERVER / "tests" / "core" / "analyses"
GOLDEN = ANALYSES / "golden" / "results.json"
TARGET = SERVER.parent / "web" / "tests" / "fixtures" / "envelopes.json"


def _helpers() -> ModuleType:
    """The analysis tests' helpers, by their path (the tests are no package)."""
    spec = importlib.util.spec_from_file_location("aibi_analyses_helpers", ANALYSES / "conftest.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


def envelopes() -> dict[str, Any]:
    """Each golden case's envelope, as JSON, by the case's name."""
    helpers = _helpers()
    found: dict[str, Any] = {}
    for name, case in json.loads(GOLDEN.read_text(encoding="utf-8")).items():
        release = helpers.shop_release(**case.get("shop", {}))
        [analysed] = helpers.analyse_document(case["document"], release, floor=case.get("floor"))
        found[name] = json.loads(analysed.result.model_dump_json())
    return found


def render(found: dict[str, Any]) -> str:
    """The file's text: the envelopes in the golden file's order, one compact line each, ASCII."""
    lines = [
        f"{json.dumps(name)}: {json.dumps(envelope, separators=(',', ':'), ensure_ascii=True)}"
        for name, envelope in found.items()
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("usage: export_envelopes.py [--check]", file=sys.stderr)
        return 2
    text = render(envelopes())
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
    if argv == ["--check"]:
        if current != text:
            print(f"{TARGET} is stale: run scripts/export_envelopes.py", file=sys.stderr)
            return 1
        return 0
    if current != text:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(text, encoding="utf-8")
        print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
