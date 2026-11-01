"""D438's mutation table for the oncology pack's retention guards (issue #136), as data.

Each ``Mutant`` is a change to the pack, or to the guards themselves (``S`` and ``G``), that a
careless hand could make, as exact text: each ``Edit``, in turn, replaces its ``anchor``, found
exactly once in its file (a path from ``server/``) as the edits before it left it, by its
``replacement``. Its outcome is recorded beside it:

- ``scan``: what the marker scan (``retention_scan.py``) reports with the mutant applied:
  ``clean``, ``hits after <calls>`` (the walks, after which of the nine calls, that found a marker),
  or how its report fails otherwise (``outcomes``, ``walks``, ``canary``, ``crash``);
- ``banned``: whether the source guard (``test_retention.banned``) refuses the mutated pack;
- ``tests``: the tests of ``test_retention.py`` that fail with it (the guards' own pins);
- ``survives``: for a mutant nothing here kills, why, and who is to hold it.

A mutant is killed when its scan is not ``clean``, the source guard refuses it, or a test fails;
otherwise it is a named survivor. ``test_retention_mutants.py`` checks, in the default suite, that
every anchor is found exactly once (a refactor of the pack or of the guards moves anchors: the
failure names the mutant and the file, and the anchor is re-cut from the new text), and replays
every mutant, against a copy of the tree, when run with ``-m mutation`` (CI's ``mutation`` job).
Hits are after the calls of ``retention_scan.CALLS``: 1 and 2 import, 3 and 4 read, 5 is an import
refused at its last patient row, 6 a read refused at its end, 7 the validator, 8 the facet, 9 the
proposer. ``tests`` are tests of ``test_retention.py`` by name (and of
``test_retention_mutants.py`` as ``test_retention_mutants.py::<name>``): a parametrised one is
killed when every case of it fails.
"""

from typing import NamedTuple

CLINICAL = "src/aibi/packs/onco/clinical.py"
IMPORTER = "src/aibi/packs/onco/importer.py"
FACETS = "src/aibi/packs/onco/facets.py"
ENDPOINTS = "src/aibi/packs/onco/endpoints.py"
VALIDATORS = "src/aibi/packs/onco/validators.py"
BUILD = "src/aibi/packs/onco/build.py"
SCAN = "tests/packs/onco/retention_scan.py"
TEST = "tests/packs/onco/test_retention.py"
PINS = "tests/packs/onco/retention_pins.py"
REPORT = "../.github/scripts/mutation_report.py"

ROW = "        identifier = fields[self.patient_id]\n"
COUNTED = '        identifier = fields[self.patient_id]\n        if self.role == "patient":'
HEAD = 'that no field is ever quoted, as cBioPortal reads none."""\n'
READ_START = "    reading = _Reading(name, role, limits, cells_before, patient)\n"
READ_END = "    return reading.finished(data)\n\n\n__all__"
IMPORT_START = "        reader = options.reader\n        study = discover("
BY = 'BY = f"importer:{PACK_ID}@{VERSION}"\n'


class Edit(NamedTuple):
    path: str
    anchor: str
    replacement: str


class Mutant(NamedTuple):
    name: str
    route: str
    edits: tuple[Edit, ...]
    scan: str
    banned: bool = False
    tests: tuple[str, ...] = ()
    survives: str = ""


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "NB01",
        "the 8-character prefixes of the last 128 ids, in a module-level SimpleNamespace",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _STATE.last = (_STATE.last + (identifier[:8],))[-128:]\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix shorter than the marker: no marker, in the pack's own"
            " state; PR B of #136 (an allow-list of the pack's state and a"
            " snapshot of it after every call) is to hold it"
        ),
    ),
    Mutant(
        "NB02",
        "the sha256 of each of the last 128 ids, in a module-level SimpleNamespace",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _STATE.last = (_STATE.last +"
                " (hashlib.sha256(identifier.encode()).digest(),))[-128:]\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="clean",
        survives=(
            "a digest: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "NB03",
        "an io.StringIO at module level, holding the last row",
        (
            Edit(
                CLINICAL,
                "        fields = line.split(TAB)\n        if len(line) > FIELD_CHARACTERS:",
                "        fields = line.split(TAB)\n"
                "        _ROW.seek(0)\n"
                "        _ROW.truncate()\n"
                "        _ROW.write(line)\n"
                "        if len(line) > FIELD_CHARACTERS:",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport io as _io\n_ROW = _io.StringIO()\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB04",
        "the last read's first 128 ids, reset at the start of every read",
        (
            Edit(
                CLINICAL,
                READ_START,
                READ_START + "    _STATE.ids = ()\n",
            ),
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                "    _STATE.ids = tuple(sorted(reading.keys))[:128]\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="clean",
        survives=(
            "equivalent: each call's last read is a sample read, which keeps"
            " no id, so nothing is kept past a call"
        ),
    ),
    Mutant(
        "NB04b",
        "the last good import's first 128 patient ids, reset at the start of each import",
        (
            Edit(
                IMPORTER,
                IMPORT_START,
                "        _LAST.ids = ()\n" + IMPORT_START,
            ),
            Edit(
                IMPORTER,
                "        return ImportResult(\n",
                "        if patient is not None:\n"
                "            _LAST.ids = tuple(sorted(patient.keys))[:128]\n"
                "        return ImportResult(\n",
            ),
            Edit(
                IMPORTER,
                BY,
                BY + "from types import SimpleNamespace as _NS\n_LAST = _NS(ids=())\n",
            ),
        ),
        scan="hits after 1-4, 7-9",
    ),
    Mutant(
        "NB04c",
        "the last good patient read's first 128 ids, reset at the start of each patient read",
        (
            Edit(
                CLINICAL,
                READ_START,
                READ_START + '    if role == "patient":\n        _STATE.ids = ()\n',
            ),
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                "        _STATE.ids = tuple(reading.keys)[:128]\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-4, 7-9",
    ),
    Mutant(
        "NB04d",
        "the same, joined in one str",
        (
            Edit(
                CLINICAL,
                READ_START,
                READ_START + '    if role == "patient":\n        _STATE.ids = ""\n',
            ),
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                '        _STATE.ids = ",".join(list(reading.keys)[:128])\n'
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-4, 7-9",
    ),
    Mutant(
        "NB05",
        "the last read's names, title-cased",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                "    _STATE.names = tuple(n.title() for n in reading.names)\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB06",
        "the last id in an array('u')",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _STATE.last = _array('u', identifier)\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n"
                "from array import array as _array\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB07",
        "the last id on read's __dict__",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        read.__dict__['last'] = identifier\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB08",
        "the last id on a class attribute set at call time",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        Clinical.last = identifier  # type: ignore[attr-defined]\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB09",
        "the last id in a threading.local",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + '        _LOCAL.last = identifier\n        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport threading as _threading\n_LOCAL = _threading.local()\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB10",
        "the last id in a namespace put in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _sys.modules['_onco_last'] = _NS(v=identifier)  # type:"
                " ignore[assignment]\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n"
                "import sys as _sys\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB11",
        "the last refusal of a read's end, kept",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    try:\n"
                "        return reading.finished(data)\n"
                "    except Refused as error:\n"
                "        _STATE.error = error\n"
                "        raise\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 5-9",
    ),
    Mutant(
        "NB11b",
        "the last refusal's traceback, kept",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    try:\n"
                "        return reading.finished(data)\n"
                "    except Refused as error:\n"
                "        _STATE.tb = error.__traceback__\n"
                "        raise\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 5-9",
    ),
    Mutant(
        "NB12",
        "a functools.partial over the last id, made through sys.modules",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _STATE.p = _sys.modules['functools'].partial(str, identifier)\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n"
                "import sys as _sys\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB13",
        "a memoryview of the last line",
        (
            Edit(
                CLINICAL,
                '            line = str(view[start:end], "utf-8")',
                '            line = str(view[start:end], "utf-8")\n'
                "            _STATE.mv = view[start:end]",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB14",
        "the last dataset id",
        (
            Edit(
                IMPORTER,
                IMPORT_START,
                "        _LAST.ds = options.dataset\n" + IMPORT_START,
            ),
            Edit(
                IMPORTER,
                BY,
                BY + "from types import SimpleNamespace as _NS\n_LAST = _NS(ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB15",
        "the last id as a str subclass",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + '        _STATE.last = _S(identifier)\n        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n"
                "class _S(str):\n"
                "    pass\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB16",
        "the last id in a slice",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        _STATE.last = slice(identifier, None)\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "NB17",
        "the sha256 of each of the last 128 ids, on a class attribute set at call time",
        (
            Edit(
                CLINICAL,
                ROW + '        if self.role == "patient":',
                ROW + "        Clinical._h = (getattr(Clinical, '_h', ()) +"
                " (hashlib.sha256(identifier.encode()).digest(),))[-128:]  #"
                " type: ignore[attr-defined]\n"
                '        if self.role == "patient":',
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=(), ids=())\n",
            ),
        ),
        scan="clean",
        survives=(
            "a digest: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C01",
        "the 8-character prefixes of the last 128 ids, on a class attribute",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        Clinical._p = (Clinical.__dict__.get('_p', ()) +"
                " (identifier[:8],))[-128:]  # type: ignore\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C02",
        "the sha256 of the last good patient read's 128 ids on a class"
        " attribute, deleted at each patient read",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                "        Clinical._h ="
                " hashlib.sha256(','.join(sorted(reading.keys)[:128]).encode()).d"
                "igest()  # type: ignore\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                READ_START,
                READ_START + '    if role == "patient":\n'
                "        if '_h' in Clinical.__dict__:\n"
                "            del Clinical._h  # type: ignore\n",
            ),
        ),
        scan="clean",
        survives=(
            "a digest: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C02p",
        "their 8-character prefixes, so kept and deleted",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                "        Clinical._h = tuple(k[:8] for k in"
                " sorted(reading.keys)[:128])  # type: ignore\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                READ_START,
                READ_START + '    if role == "patient":\n'
                "        if '_h' in Clinical.__dict__:\n"
                "            del Clinical._h  # type: ignore\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C03",
        "the ids themselves, so kept and deleted",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                "        Clinical._h = ','.join(sorted(reading.keys)[:128])  #"
                " type: ignore\n"
                "    return done\n"
                "\n"
                "\n"
                "__all__",
            ),
            Edit(
                CLINICAL,
                READ_START,
                READ_START + '    if role == "patient":\n'
                "        if '_h' in Clinical.__dict__:\n"
                "            del Clinical._h  # type: ignore\n",
            ),
        ),
        scan="hits after 1-4, 7-9",
    ),
    Mutant(
        "C04",
        "the last dataset id's 8-character prefix on the importer's instance",
        (
            Edit(
                IMPORTER,
                IMPORT_START,
                "        self._last = options.dataset[:8]\n" + IMPORT_START,
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C05",
        "a hashlib object made at import on the importer, updated with every dataset id",
        (
            Edit(
                IMPORTER,
                "import os\n",
                "import os\nimport hashlib\n",
            ),
            Edit(
                IMPORTER,
                '    docstring)."""\n\n    def import_source(',
                '    docstring)."""\n'
                "\n"
                "    def __init__(self) -> None:\n"
                "        self._h = hashlib.sha256()\n"
                "\n"
                "    def import_source(",
            ),
            Edit(
                IMPORTER,
                IMPORT_START,
                "        self._h.update(options.dataset.encode())\n" + IMPORT_START,
            ),
        ),
        scan="clean",
        survives=(
            "an opaque C object's state (a hash of the ids), which no walk"
            " of the heap reads; PR B of #136 (an allow-list of the pack's"
            " state and a snapshot of it after every call) is to name or hold it"
        ),
    ),
    Mutant(
        "C06",
        "an id's prefix in the inner dict of a schema's MappingProxyType",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _DS['properties']['_p'] = identifier[:8]  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nfrom aibi.packs.onco.schemas import DATASET as _DS\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C07",
        "an id's prefix in a frozen pydantic model's __dict__",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _C[0].__dict__['_p'] = identifier[:8]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nfrom aibi.packs.onco.concepts import CONCEPTS as _C\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C07b",
        "an id's prefix in a frozen pydantic model's __pydantic_extra__",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        object.__setattr__(_C[1], '__pydantic_extra__', {'_p':"
                " identifier[:8]})\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nfrom aibi.packs.onco.concepts import CONCEPTS as _C\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C08",
        "a count derived from a call, on the TypeAdapter's __dict__",
        (
            Edit(
                BUILD,
                "        return _ADAPTER.validate_python(written)",
                "        _ADAPTER.__dict__['_n'] = len(str(written))\n"
                "        return _ADAPTER.validate_python(written)",
            ),
        ),
        scan="clean",
        survives=(
            "a count: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C09",
        "an id's prefix on read's __dict__",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        read.__dict__['_p'] = identifier[:8]\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C10",
        "an id's prefix in read's __kwdefaults__",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        read.__kwdefaults__['_p'] = identifier[:8]  # type: ignore\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C11",
        "the id's code points in an array('H'), in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _array('H', map(ord,"
                " identifier))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C11i",
        "the id's code points in an array('i'), in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _array('i', map(ord,"
                " identifier))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C12",
        "the id in an array('u'), in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _array('u', identifier)  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C13",
        "the id in UTF-16, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " identifier.encode('utf-16-le')  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C14",
        "the id's 8-character prefix, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = identifier[:8]  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix shorter than the marker: no marker, outside the pack's"
            " state; no scan holds it"
        ),
    ),
    Mutant(
        "C15",
        "the id in a slice, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = slice(identifier, None)  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C16",
        "the id title-cased, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = identifier.title()  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C17",
        "the id in an io.StringIO, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _io.StringIO(identifier)  #"
                " type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C18",
        "the id reversed, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = identifier[::-1]  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C19",
        "a warning naming the id's prefix (the module's __warningregistry__)",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _warnings.warn('row ' + identifier[:8])\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport warnings as _warnings\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix shorter than the marker: no marker, outside the pack's"
            " state; no scan holds it"
        ),
    ),
    Mutant(
        "C20",
        "the id's prefix in os.environ",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _os.environ['ONCO_P'] = identifier[:8]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport os as _os\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix shorter than the marker, in the process environment:"
            " no marker, outside the pack's state; no scan holds it"
        ),
    ),
    Mutant(
        "C20m",
        "the id in os.environ",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _os.environ['ONCO_P'] = identifier\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport os as _os\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C21",
        "the id written to a file left open",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        open(_tempfile.gettempdir() + '/onco136_last',"
                " 'w').write(identifier)\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport tempfile as _tempfile\n",
            ),
        ),
        scan="clean",
        survives=("a file: outside the heap; no scan holds it"),
    ),
    Mutant(
        "C21b",
        "the id written to a file and closed",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        with open(_tempfile.gettempdir() + '/onco136_last',"
                " 'w') as _f:\n"
                "            _f.write(identifier)\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport tempfile as _tempfile\n",
            ),
        ),
        scan="clean",
        survives=("a file: outside the heap; no scan holds it"),
    ),
    Mutant(
        "C22",
        "an atexit callback holding the id",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _atexit.register(str, identifier)\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport atexit as _atexit\n",
            ),
        ),
        scan="clean",
        survives=(
            "the interpreter's registry of atexit callbacks, which no"
            " collector's root reaches; no scan holds it"
        ),
    ),
    Mutant(
        "C23",
        "the id compiled as a pattern (re's cache)",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _re.compile(_re.escape(identifier))\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport re as _re\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C23p",
        "the id's prefix compiled as a pattern (re's cache)",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _re.compile(_re.escape(identifier[:8]))\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport re as _re\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix shorter than the marker: no marker, outside the pack's"
            " state; no scan holds it"
        ),
    ),
    Mutant(
        "C24",
        "a functools.partial over an id's prefix on a class attribute",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        Clinical._f = _sys.modules['functools'].partial(str,"
                " identifier[:8])  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C25",
        "a class made at call time, holding an id's prefix, on a class attribute",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        Clinical._k = type('K', (), {'p': identifier[:8]})  #"
                " type: ignore\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C26",
        "an id's prefix in a module-level threading.local",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _L.p = identifier[:8]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport threading as _threading\n_L = _threading.local()\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C27",
        "an id's prefix in a module-level ContextVar",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _V.set(identifier[:8])\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport contextvars as _cv\n_V = _cv.ContextVar('v')\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C28",
        "a module-level itertools.count of the rows ever read",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        next(_N)\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport itertools as _it\n_N = _it.count()\n",
            ),
        ),
        scan="clean",
        survives=(
            "a count: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C29",
        "a module-level generator fed ids' prefixes",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _G.send(identifier[:8])\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "def _keep():\n"
                "    kept = None\n"
                "    while True:\n"
                "        kept = yield kept\n"
                "_G = _keep()\n"
                "next(_G)\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C30",
        "an lru_cache, made through sys.modules at import, over ids' prefixes",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _memo(identifier[:8])\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys2\n"
                "_memo = _sys2.modules['functools'].lru_cache(maxsize=128)(str)\n",
            ),
        ),
        scan="clean",
        banned=True,
    ),
    Mutant(
        "C31",
        "an id's prefix in a dict held by a module-level frozen dataclass",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _F.d['_p'] = identifier[:8]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n@dataclass(frozen=True)\nclass _Frozen:\n    d: dict\n_F = _Frozen({})\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C32",
        "an id's prefix in a list held by a module-level NamedTuple",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _T.items[:] = [identifier[:8]]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "from typing import NamedTuple as _NT\n"
                "class _Tup(_NT):\n"
                "    items: list\n"
                "_T = _Tup([])\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C33",
        "a name's prefix in a dict held by a frozen dataclass default of read",
        (
            Edit(
                CLINICAL,
                "    patient: Clinical | None = None,\n) -> Clinical:",
                "    patient: Clinical | None = None,\n"
                "    _box: object = _Box({}),\n"
                ") -> Clinical:",
            ),
            Edit(
                CLINICAL,
                READ_START,
                READ_START + "    _box.d['_p'] = name[:8]  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n@dataclass(frozen=True)\nclass _Box:\n    d: dict\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C34",
        "an id's prefix in a list held by an object whose __class__ says tuple",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _S.kept[:] = [identifier[:8]]\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "class _Spoof:\n"
                "    __class__ = property(lambda self: tuple)  # type: ignore\n"
                "    def __init__(self):\n"
                "        self.kept = []\n"
                "    def __iter__(self):\n"
                "        return iter(())\n"
                "_S = _Spoof()\n",
            ),
        ),
        scan="clean",
        survives=(
            "a prefix: no marker, in the pack's own state; PR B of #136 (an"
            " allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C35",
        "a signal handler holding the id in a default",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _signal.signal(_signal.SIGUSR1, _ft.partial(print,"
                " identifier) if False else (lambda *a, _k=identifier: None))\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport signal as _signal\nimport functools as _ft\n",
            ),
        ),
        scan="hits after 1-9",
        banned=True,
    ),
    Mutant(
        "C36",
        "an audit hook holding the read's file name in a default",
        (
            Edit(
                CLINICAL,
                READ_START,
                READ_START + "    if role == 'patient':\n"
                "        _sys3.addaudithook(lambda e, a, _k=name: None)\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport sys as _sys3\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C37",
        "facet keeps its last result on a function attribute",
        (
            Edit(
                FACETS,
                "    found: dict[str, list[str]] = {}\n",
                "    found: dict[str, list[str]] = {}\n    facet.__dict__['last'] = found\n",
            ),
        ),
        scan="hits after 8-9",
    ),
    Mutant(
        "C38",
        "propose keeps the release's descriptors on a function attribute",
        (
            Edit(
                ENDPOINTS,
                "    descriptors = release.descriptors\n",
                "    descriptors = release.descriptors\n"
                "    propose.__dict__['last'] = dict(descriptors)\n",
            ),
        ),
        scan="hits after 9",
    ),
    Mutant(
        "C40",
        "the id's code points in a list, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = [ord(c) for c in identifier] "
                " # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C41",
        "the id's code points in a deque, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _col.deque(map(ord,"
                " identifier))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C42",
        "the id in UTF-32, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = identifier.encode('utf-32') "
                " # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C43",
        "the id's code points in an array('d'), in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _array('d', map(ord,"
                " identifier))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C44",
        "list(identifier), one str a character, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = list(identifier)  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C45",
        "the id in two chunks in a tuple, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = (identifier[:9],"
                " identifier[9:])  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C46",
        "int.from_bytes of the id, big-endian, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " int.from_bytes(identifier.encode(), 'big')  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C46b",
        "int.from_bytes of the id, little-endian, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " int.from_bytes(identifier.encode(), 'little')  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C47",
        "the hex of the id's UTF-8, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = identifier.encode().hex()  #"
                " type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C48",
        "the base64 of the id's UTF-8, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " _b64.b64encode(identifier.encode())  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C49",
        "the id in an io.BytesIO, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " _io.BytesIO(identifier.encode())  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C50",
        "a memoryview of the id's UTF-8, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " memoryview(identifier.encode())  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C52",
        "propose keeps its last proposals",
        (
            Edit(
                ENDPOINTS,
                "    return tuple(found)\n",
                "    propose.__dict__['last'] = tuple(found)\n    return tuple(found)\n",
            ),
        ),
        scan="clean",
        survives=(
            "a hook's memo of what it derives from vocabulary alone (an"
            " endpoint's id, a table and columns of the pack's own names):"
            " it can carry no marker; PR B of #136 (an allow-list of the"
            " pack's state and a snapshot of it after every call) is to hold it"
        ),
    ),
    Mutant(
        "C53",
        "the validator keeps the last type_of_cancer it read",
        (
            Edit(
                VALIDATORS,
                "        value = members[TYPE_OF_CANCER]\n",
                "        value = members[TYPE_OF_CANCER]\n"
                "        OncoValidator.last = value  # type: ignore\n",
            ),
        ),
        scan="hits after 7-9",
    ),
    Mutant(
        "C54",
        "facet keeps the last reference genome and profile set",
        (
            Edit(
                FACETS,
                "    if profiles:\n",
                "    facet.__dict__['last'] = (found.get('reference_genome'),"
                " tuple(profiles))\n"
                "    if profiles:\n",
            ),
        ),
        scan="clean",
        survives=(
            "a hook's memo of what it derives from closed vocabularies (the"
            " genomes, the profiles): it can carry no marker; PR B of #136"
            " (an allow-list of the pack's state and a snapshot of it after"
            " every call) is to hold it"
        ),
    ),
    Mutant(
        "C55",
        "the validator keeps a value only when it refuses it",
        (
            Edit(
                VALIDATORS,
                "        return [\n            Refusal(",
                "        OncoValidator.refused = value  # type: ignore\n"
                "        return [\n"
                "            Refusal(",
            ),
        ),
        scan="hits after 7-9",
    ),
    Mutant(
        "C56",
        "the base64 of the id's UTF-16, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " _b64.b64encode(identifier.encode('utf-16-le'))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="clean",
        survives=("unread by design: base64 and hex are read of UTF-8 alone (D438)"),
    ),
    Mutant(
        "C57",
        "frozenset(enumerate(identifier)), in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] ="
                " frozenset(enumerate(identifier))  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="clean",
        survives=("unread by design: a set keeps no order (D438)"),
    ),
    Mutant(
        "C58",
        "the base64 of a byte and the id's UTF-8, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _b64.b64encode(b'z' +"
                " identifier.encode())  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "C59",
        "the base64 of two bytes and the id's UTF-8, in sys.modules",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules['_onco_h'] = _b64.b64encode(b'zz' +"
                " identifier.encode())  # type: ignore\n",
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n"
                "import sys as _sys\n"
                "from array import array as _array\n"
                "import io as _io\n"
                "import collections as _col\n"
                "import base64 as _b64\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "S1",
        "the walk reads no dict's keys",
        (
            Edit(
                SCAN,
                "    modules = sys.modules\n    if type(modules) is not dict:",
                "    keys = False\n    modules = sys.modules\n    if type(modules) is not dict:",
            ),
        ),
        scan="canary",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "S2",
        "the scan run in the suite's own process",
        (
            Edit(
                TEST,
                "    ran = subprocess.run(\n"
                '        [sys.executable, "-I", "-c", RUN, str(SOURCE), str(SCAN)],\n'
                "        capture_output=True,\n"
                "        text=True,\n"
                "        timeout=300,\n"
                "        check=False,\n"
                "    )",
                "    import contextlib, io, runpy, types as _t\n"
                "    out = io.StringIO()\n"
                "    with contextlib.redirect_stdout(out):\n"
                '        runpy.run_path(str(SCAN), run_name="__main__")\n'
                "    ran = _t.SimpleNamespace(returncode=0,"
                ' stdout=out.getvalue(), stderr="")',
            ),
        ),
        scan="clean",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "S2b",
        "the scan run without -I",
        (
            Edit(
                TEST,
                '[sys.executable, "-I", "-c", RUN, str(SOURCE), str(SCAN)]',
                '[sys.executable, "-c", RUN, str(SOURCE), str(SCAN)]',
            ),
        ),
        scan="clean",
        survives=("equivalent: the child puts the tree's src first on its path either way"),
    ),
    Mutant(
        "S4",
        "the walk reads no bytes",
        (
            Edit(
                SCAN,
                '        return "bytes", find.in_bytes(_BYTES_COPY(item, _ALL))',
                '        return "bytes", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S5",
        "the marks form not looked for",
        (
            Edit(
                SCAN,
                "        return self.lower, self.upper, self.marks",
                "        return self.lower, self.upper, self.upper",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "S6",
        "the upper-case form not looked for",
        (
            Edit(
                SCAN,
                "        return self.lower, self.upper, self.marks",
                "        return self.lower, self.lower, self.marks",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S7",
        "the walk reads no array",
        (
            Edit(
                SCAN,
                '        return f"array {_ARRAY_TYPECODE(item)}",'
                " find.in_bytes(_ARRAY_TOBYTES(item))",
                '        return f"array {_ARRAY_TYPECODE(item)}", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S8",
        "fresh() returns its argument",
        (
            Edit(
                SCAN,
                '    return (text + "x")[:-1]',
                "    return text",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "S9",
        "the canary not looked for",
        (
            Edit(
                SCAN,
                "        seen = reachable([*markers[:done], canary], ignore=(hits, walks))",
                "        seen = reachable(markers[:done], ignore=(hits, walks))",
            ),
        ),
        scan="canary",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "S10",
        "no refused import",
        (
            Edit(
                SCAN,
                '    Call("import", late="Mar-1"),\n',
                '    Call("import"),\n',
            ),
        ),
        scan="outcomes",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_every_value_of_a_call_carries_its_marker",
        ),
    ),
    Mutant(
        "S11",
        "sys.modules not a root of the walk",
        (
            Edit(
                SCAN,
                "    stack: list[object] = [*gc.get_objects(), *dict.values(modules)]",
                "    stack: list[object] = [*gc.get_objects()]",
            ),
        ),
        scan="clean",
        survives=(
            "equivalent: every module is tracked by the collector, so gc.get_objects() holds it"
        ),
    ),
    Mutant(
        "S12",
        "the study meta's name written without its marker",
        (
            Edit(
                SCAN,
                'name: Name {up}\\n"',
                'name: Name\\n"',
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "S13",
        "only the first four calls",
        (
            Edit(
                SCAN,
                "    for done, call in enumerate(calls, 1):",
                "    for done, call in enumerate(calls[:4], 1):",
            ),
        ),
        scan="outcomes",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "S14",
        "one walk, after the last call, not one after each",
        (
            Edit(
                SCAN,
                "        gc.collect()\n        after(done)\n    return outcomes",
                "        gc.collect()\n    after(len(outcomes))\n    return outcomes",
            ),
        ),
        scan="walks",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_a_walk_after_each_call_finds_what_a_later_call_drops",
        ),
    ),
    Mutant(
        "S15",
        "a text read without casefolding",
        (
            Edit(
                SCAN,
                "        return self.text(_STR_CASEFOLD(text)) | self.exact(text)",
                "        return self.text(text) | self.exact(text)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S16",
        "no read of a sequence's elements in order",
        (
            Edit(
                SCAN,
                "    kinds = list(map(type, elements))\n",
                "    return set()\n    kinds = list(map(type, elements))\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S17",
        "no read of an io.StringIO",
        (
            Edit(
                SCAN,
                '            return "StringIO", find.in_text(_STRINGIO_GETVALUE(item))',
                '            return "StringIO", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S18",
        "no read of an io.BytesIO",
        (
            Edit(
                SCAN,
                '            return "BytesIO", find.in_bytes(_BYTESIO_GETVALUE(item))',
                '            return "BytesIO", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S19",
        "no read of a memoryview",
        (
            Edit(
                SCAN,
                '            return "memoryview", find.in_bytes(_MEMORYVIEW_TOBYTES(item))',
                '            return "memoryview", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S20",
        "no read of an int's bytes",
        (
            Edit(
                SCAN,
                '        return "int", find.in_bytes(number.to_bytes(size, "big"))',
                '        return "int", set()',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S21",
        "no reversed form",
        (
            Edit(
                SCAN,
                "                text |= {(index, folded), (index,"
                " folded[::-1]), (index, utf8.hex())}",
                "                text |= {(index, folded), (index, utf8.hex())}",
            ),
            Edit(
                SCAN,
                "                    blobs |= {(index, blob), (index, blob[::-1])}",
                "                    blobs |= {(index, blob)}",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S22",
        "no hex",
        (
            Edit(
                SCAN,
                "                text |= {(index, folded), (index,"
                " folded[::-1]), (index, utf8.hex())}",
                "                text |= {(index, folded), (index, folded[::-1])}",
            ),
            Edit(
                SCAN,
                "                blobs.add((index, utf8.hex().encode()))\n",
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S23",
        "no base64",
        (
            Edit(
                SCAN,
                "                exact |= {(index, b.decode()) for b in _base64(utf8)}\n",
                "",
            ),
            Edit(
                SCAN,
                "                raw |= {(index, b) for b in _base64(utf8)}\n",
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S24",
        "UTF-8 alone, no UTF-16 or UTF-32",
        (
            Edit(
                SCAN,
                'ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")',
                'ENCODINGS = ("utf-8",)',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S25",
        "code points looked for in arrays of type codes b and B alone",
        (
            Edit(
                SCAN,
                "                for code in typecodes:",
                '                for code in "bB":',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S26",
        "no read of a deque",
        (
            Edit(
                SCAN,
                '        return [("deque items", list(_DEQUE_ITER(item)))]',
                "        return []",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S27",
        "a slice a leaf again",
        (
            Edit(
                SCAN,
                "(float, bool, type(None), complex, type(...))",
                "(float, bool, type(None), complex, type(...), slice)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S28",
        "a walked bytes searched without a copy (re runs a subclass's __buffer__)",
        (
            Edit(
                SCAN,
                '        return "bytes", find.in_bytes(_BYTES_COPY(item, _ALL))',
                '        return "bytes", find.in_bytes(item)',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "S29",
        "the walk's own patterns not skipped (re caches them)",
        (
            Edit(
                SCAN,
                "    own += [FINDERS, *(part for finder in FINDERS for part in finder.own())]\n",
                "",
            ),
        ),
        scan="hits after 1-9",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "S30",
        "the validator, the facet and the proposer not called",
        (
            Edit(
                SCAN,
                '    Call("validate", named=True),\n'
                '    Call("facet", named=True),\n'
                '    Call("propose", named=True),\n',
                "",
            ),
        ),
        scan="outcomes",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_every_value_of_a_call_carries_its_marker",
        ),
    ),
    Mutant(
        "S31",
        "the study's type_of_cancer unmarked",
        (
            Edit(
                SCAN,
                'f"type_of_cancer: t{lo}\\ncancer',
                'f"type_of_cancer: mixed\\ncancer',
            ),
        ),
        scan="crash",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_every_value_of_a_call_carries_its_marker",
        ),
    ),
    Mutant(
        "S32",
        "the validator's refusal branch not driven",
        (
            Edit(
                SCAN,
                '        dumped["extensions"]["onco"]["type_of_cancer"] = edited_of(marker)\n',
                "",
            ),
        ),
        scan="crash",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "S33",
        "an io.StringIO read through its own getvalue",
        (
            Edit(
                SCAN,
                '            return "StringIO", find.in_text(_STRINGIO_GETVALUE(item))',
                '            return "StringIO", find.in_text(item.getvalue())',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "S34",
        "an int's leading zero bytes not restored",
        (
            Edit(
                SCAN,
                "        size = (number.bit_length() + 7) // 8 + 3",
                "        size = (number.bit_length() + 7) // 8",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "S35",
        "a deque read through its own __iter__",
        (
            Edit(
                SCAN,
                '        return [("deque items", list(_DEQUE_ITER(item)))]',
                '        return [("deque items", list(item))]',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "S36",
        "a sequence's elements that are the walk's own read as text",
        (
            Edit(
                SCAN,
                "    return find.in_text(_units(elements, skip))",
                "    return find.in_text(_units(elements))",
            ),
        ),
        scan="hits after 1-9",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "G1",
        "a str subclass searched without its copy (its __contains__ runs)",
        (
            Edit(
                SCAN,
                '        return "str", find.in_text(_STR_COPY(item, _ALL))',
                '        return "str", find.in_text(item)',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "G5",
        "an int element read without int.__index__ (its comparisons run)",
        (
            Edit(
                SCAN,
                "            point = _INT_INDEX(element)\n",
                "            point = element\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "G14",
        "base64 needles at alignments 1 and 2 keeping the group of the padding's zero bytes",
        (
            Edit(
                SCAN,
                "        start = 1 if before else 0",
                "        start = 0",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_reads_a_form_as_a_class_of_holders_and_encodings",),
    ),
    Mutant(
        "G43",
        "the scan's imports handed a fixed dataset id, not the call's",
        (
            Edit(
                SCAN,
                "            dataset=fresh(marker.lower),\n            reader=reader,",
                '            dataset="m",\n            reader=reader,',
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "R1",
        "builtin methods a leaf of the walk",
        (
            Edit(
                SCAN,
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...)))",
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...), BuiltinFunctionType))",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_walk_s_leaves_are_the_atoms_alone",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "R2",
        "bound methods a leaf of the walk",
        (
            Edit(
                SCAN,
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...)))",
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...), MethodType))",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_walk_s_leaves_are_the_atoms_alone",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "R5",
        "frozensets a leaf of the walk",
        (
            Edit(
                SCAN,
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...)))",
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...), frozenset))",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_walk_s_leaves_are_the_atoms_alone",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "R9",
        "mappingproxies a leaf of the walk",
        (
            Edit(
                SCAN,
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...)))",
                "LEAVES = frozenset(id(kind) for kind in (float, bool,"
                " type(None), complex, type(...), MappingProxyType))",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_walk_s_leaves_are_the_atoms_alone",
            "test_the_walk_finds_each_route_and_nothing_once_it_is_released",
        ),
    ),
    Mutant(
        "F1",
        "the walk follows nothing of a type",
        (
            Edit(
                SCAN,
                "            if kind is type:\n"
                "                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]",
                "            if kind is type:\n                pass",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F2",
        "the walk follows nothing of a FunctionType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is FunctionType else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F3",
        "the walk follows nothing of a CodeType",
        (
            Edit(
                SCAN,
                "                for part in _CODE_PARTS:",
                "                for part in ():",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F4",
        "the walk follows nothing of a CellType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is CellType else gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F5",
        "the walk follows nothing of a MethodType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is MethodType else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F6",
        "the walk follows nothing of a BuiltinFunctionType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is BuiltinFunctionType else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F7",
        "the walk follows nothing of a MethodWrapperType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is MethodWrapperType else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F8",
        "the walk follows nothing of a MappingProxyType",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is MappingProxyType else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F9",
        "the walk follows nothing of a property",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is property else gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F10",
        "the walk follows nothing of a staticmethod",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is staticmethod else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F11",
        "the walk follows nothing of a classmethod",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is classmethod else"
                " gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F12",
        "the walk follows nothing of a set",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is set else gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F13",
        "the walk follows nothing of a frozenset",
        (
            Edit(
                SCAN,
                "        if id(kind) in PLAIN:\n            held = gc.get_referents(item)\n",
                "        if id(kind) in PLAIN:\n"
                "            held = [] if kind is frozenset else gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F14",
        "the walk follows nothing of a tuple",
        (
            Edit(
                SCAN,
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = gc.get_referents(item)",
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = [] if kind is tuple else gc.get_referents(item)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F15",
        "the walk follows nothing of a list",
        (
            Edit(
                SCAN,
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = gc.get_referents(item)",
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = [] if kind is list else gc.get_referents(item)",
            ),
        ),
        scan="canary",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "F16",
        "the walk follows nothing of a dict",
        (
            Edit(
                SCAN,
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = gc.get_referents(item)",
                "        elif kind is tuple or kind is list or kind is dict:\n"
                "            held = [] if kind is dict else gc.get_referents(item)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "H1",
        "the sample read handed a fixed file name",
        (
            Edit(
                SCAN,
                '            sample, sample_name, "sample", limits,'
                " cells_before=found.cells, patient=found",
                '            sample, "sample.txt", "sample", limits,'
                " cells_before=found.cells, patient=found",
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "H2",
        "the importer handed other options than the call built",
        (
            Edit(
                SCAN,
                "        result = pack.importer.import_source(source, options)",
                "        options = ImportOptions(dataset='m', reader=reader,"
                " limits=limits, at=AT, name=options.name)\n"
                "        result = pack.importer.import_source(source, options)",
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "H5",
        "the options carry an unmarked original_name",
        (
            Edit(
                SCAN,
                "            name=name_of(marker, call),\n        )",
                "            name=name_of(marker, call),\n"
                "            original_name='upload.txt',\n"
                "        )",
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "H6",
        "the release handed the validator, facet and proposer named by a fixed id",
        (
            Edit(
                SCAN,
                "        return Release({d.id: d for d in found}, fresh(marker.lower))",
                "        return Release({d.id: d for d in found}, 'm')",
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "W7",
        "a bytes subclass read through bytes()",
        (
            Edit(
                SCAN,
                'return "bytes", find.in_bytes(_BYTES_COPY(item, _ALL))',
                'return "bytes", find.in_bytes(bytes(item))',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "W17",
        "a chain's module named through a module subclass's own lookup",
        (
            Edit(
                SCAN,
                "    elif kind is ModuleType:\n        name = _module_name(item)",
                "    elif issubclass(kind, ModuleType):\n        name = _module_name(item)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "W34",
        "the size of every walked object asked",
        (
            Edit(
                SCAN,
                "        seen.add(id(item))\n        kind = type(item)",
                "        seen.add(id(item))\n"
                "        sys.getsizeof(item)\n"
                "        kind = type(item)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T1",
        "no twin of a str",
        (
            Edit(
                PINS,
                "    for base in (str, bytes, bytearray, int):\n        if isinstance(held, base):",
                "    for base in (bytes, bytearray, int):\n        if isinstance(held, base):",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T2",
        "a twin's data descriptors not overridden",
        (
            Edit(
                PINS,
                "        if isinstance(attribute, types.GetSetDescriptorType |"
                " types.MemberDescriptorType):",
                "        if False:",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T3",
        "the twins' walk unarmed",
        (
            Edit(
                PINS,
                "    ARMED.append(True)\n    try:\n        armed",
                "    try:\n        armed",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T4",
        "a twin's slots record and do not raise",
        (
            Edit(
                PINS,
                '            raise RuntimeError(f"the walk ran {owner}.{name}")\n',
                "            pass\n",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T5",
        "a twin's slots raise and record nothing",
        (
            Edit(
                PINS,
                '            RAN.append(f"{owner}.{name}")\n',
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T6",
        "a sequence's elements not twinned",
        (
            Edit(
                PINS,
                "            return TWINS[base](_hostile(element) for element in held)",
                "            return TWINS[base](held)",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T7",
        "a dict's keys not twinned",
        (
            Edit(
                PINS,
                "        return TWINS[dict]({_hostile(k): _hostile(v) for k, v in held.items()})",
                "        return TWINS[dict]({k: _hostile(v) for k, v in held.items()})",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T8",
        "the twins' metaclass overriding nothing",
        (
            Edit(
                PINS,
                '_Recorded: type = type("_Recorded", (type,), _slots(type, META_SPARED))',
                '_Recorded: type = type("_Recorded", (type,), {})',
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T9",
        "the twins' cells left as they are made",
        (
            Edit(
                PINS,
                "        parted = _parted(held)",
                "        parted = held",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "T10",
        "a twin's dunder slots spared",
        (
            Edit(
                PINS,
                "        if name in spared:\n            continue",
                "        if name in spared or name.startswith('__'):\n            continue",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_runs_no_code_of_an_object_it_walks",),
    ),
    Mutant(
        "X1",
        "a class a metaclass made: its names not read",
        (
            Edit(
                SCAN,
                "            if issubclass(kind, type):  # a class's names,"
                " which its traverse does not give\n"
                "                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]\n",
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "X2",
        "a code object's constants and str parts read, not its names",
        (
            Edit(
                SCAN,
                "_CODE_PARTS = tuple(CodeType.__dict__[name].__get__ for name in CODE_READ)",
                "_CODE_PARTS = tuple(CodeType.__dict__[name].__get__ for name in"
                " CODE_READ[:1] + CODE_READ[5:])",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "X3",
        "a code object's file and names not read",
        (
            Edit(
                SCAN,
                "_CODE_PARTS = tuple(CodeType.__dict__[name].__get__ for name in CODE_READ)",
                "_CODE_PARTS = tuple(CodeType.__dict__[name].__get__ for name in CODE_READ[:5])",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "X4",
        "a code object's parts that are not tuples dropped",
        (
            Edit(
                SCAN,
                "held += found_part if type(found_part) is tuple else [found_part]",
                "held += found_part if type(found_part) is tuple else []",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "X5",
        "a class's name read, not its qualified name",
        (
            Edit(
                SCAN,
                "            if kind is type:\n"
                "                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]",
                "            if kind is type:\n                held += [_TYPE_NAME(item)]",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "X6",
        "a class's qualified name read, not its name",
        (
            Edit(
                SCAN,
                "            if kind is type:\n"
                "                held += [_TYPE_NAME(item), _TYPE_QUALNAME(item)]",
                "            if kind is type:\n                held += [_TYPE_QUALNAME(item)]",
            ),
        ),
        scan="clean",
        tests=("test_the_walk_finds_each_route_and_nothing_once_it_is_released",),
    ),
    Mutant(
        "Y4",
        "the validator handed a dataset descriptor renamed to a fixed name",
        (
            Edit(
                SCAN,
                '        dumped["extensions"]["onco"]["type_of_cancer"] = edited_of(marker)\n',
                '        dumped["extensions"]["onco"]["type_of_cancer"] ='
                " edited_of(marker)\n"
                '        dumped["fields"]["name"] = "Fixed"\n',
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "Y5",
        "the hooks handed a dataset descriptor renamed to a fixed name",
        (
            Edit(
                SCAN,
                "        return Release({d.id: d for d in found}, fresh(marker.lower))",
                "        return Release({d.id: (d.model_copy(update={'fields':"
                " d.fields.model_copy(update={'name': 'Fixed'})}) if d.id =="
                " 'dataset' else d) for d in found}, fresh(marker.lower))",
            ),
        ),
        scan="clean",
        tests=("test_every_value_of_a_call_carries_its_marker",),
    ),
    Mutant(
        "V1",
        "validate_source keeps the import's result in a module-level SimpleNamespace",
        (
            Edit(
                VALIDATORS,
                "    def validate_source(self, source: ImportSource, result:"
                " ImportResult) -> Refusals:\n"
                "        return []",
                "    def validate_source(self, source: ImportSource, result:"
                " ImportResult) -> Refusals:\n"
                "        _STATE.last = result\n"
                "        return []",
            ),
            Edit(
                VALIDATORS,
                'TYPE_OF_CANCER = "type_of_cancer"\n',
                'TYPE_OF_CANCER = "type_of_cancer"\n'
                "from types import SimpleNamespace as _NS\n"
                "_STATE = _NS(last=None)\n",
            ),
        ),
        scan="hits after 1-9",
    ),
    Mutant(
        "MR2",
        "the report check accepts a skipped case",
        (
            Edit(
                REPORT,
                'for kind in ("failure", "error", "skipped")',
                'for kind in ("failure", "error")',
            ),
        ),
        scan="clean",
        tests=(
            "test_retention_mutants.py::test_the_job_s_report_check_refuses_an_incomplete_replay",
        ),
    ),
    Mutant(
        "MR2b",
        "the report check accepts an errored case",
        (
            Edit(
                REPORT,
                'for kind in ("failure", "error", "skipped")',
                'for kind in ("failure", "skipped")',
            ),
        ),
        scan="clean",
        tests=(
            "test_retention_mutants.py::test_the_job_s_report_check_refuses_an_incomplete_replay",
        ),
    ),
    Mutant(
        "MR6",
        "the report check accepts a table without mutants",
        (
            Edit(
                REPORT,
                '    if not want:\n        found.append(f"no mutant is read from {table}")\n',
                "",
            ),
        ),
        scan="clean",
        tests=(
            "test_retention_mutants.py::test_the_job_s_report_check_refuses_an_incomplete_replay",
        ),
    ),
    Mutant(
        "MR8",
        "the report check ignores a required case's failure",
        (
            Edit(
                REPORT,
                "        if any(case.findall(kind) for kind in",
                "        if not (case.get('name') or"
                " '').startswith('test_the_replay') and any(case.findall(kind)"
                " for kind in",
            ),
        ),
        scan="clean",
        tests=(
            "test_retention_mutants.py::test_the_job_s_report_check_refuses_an_incomplete_replay",
        ),
    ),
)
