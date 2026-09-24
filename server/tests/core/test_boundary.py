"""The core must not load any pack, even indirectly (SPEC P8)."""

import subprocess
import sys
import textwrap


def test_importing_every_core_module_loads_no_pack() -> None:
    script = textwrap.dedent(
        """
        import importlib
        import pkgutil
        import sys

        import aibi.core

        for module in pkgutil.walk_packages(aibi.core.__path__, prefix="aibi.core."):
            importlib.import_module(module.name)

        leaked = sorted(m for m in sys.modules if m == "aibi.packs" or m.startswith("aibi.packs."))
        if leaked:
            raise SystemExit("core imported packs: " + ", ".join(leaked))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
