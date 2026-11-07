"""D438's mutation table for the oncology pack's retention guards (issue #136), as data.

Each ``Mutant`` is a change to the pack, or to the guards themselves (``S`` and ``G``), that a
careless hand could make, as exact text: each ``Edit``, in turn, replaces its ``anchor``, found
exactly once in its file (a path from ``server/``) as the edits before it left it, by its
``replacement``. Its outcome is recorded beside it:

- ``scan``: what the marker scan's child (``retention_scan.py``) reports with the mutant applied:
  ``clean``, or which of its guards found the mutant and when, in turn (``; ``): ``allow at
  import`` (the allow-list before the first call), ``allow after <calls>`` (a reject it did not
  have before them), ``snapshot after <calls>`` (a snapshot that differs from the first) and
  ``hits after <calls>`` (a walk that found a marker), each after which of the nine calls; or how
  its report fails otherwise (``outcomes``, ``walks``, ``canary``, ``stores``, ``crash``);
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
STATE = "tests/packs/onco/retention_state.py"
STATE_PINS = "tests/packs/onco/retention_state_pins.py"
REPORT = "../.github/scripts/mutation_report.py"

ROW = "        identifier = fields[self.patient_id]\n"
COUNTED = '        identifier = fields[self.patient_id]\n        if self.role == "patient":'
HEAD = 'that no field is ever quoted, as cBioPortal reads none."""\n'
READ_START = "    reading = _Reading(name, role, limits, cells_before, patient)\n"
READ_END = "    return reading.finished(data)\n\n\n__all__"
IMPORT_START = "        reader = options.reader\n        study = discover("
BY = 'BY = f"importer:{PACK_ID}@{VERSION}"\n'
SYS = HEAD + "\nimport sys as _sys\n"
NEXT = '        if self.role == "patient":'
ACC = (
    "\nclass _Acc:\n    def __init__(self):\n        self.h = hashlib.sha256()\n"
    "    def add(self, x):\n        self.h.update(x)\n        return x\n"
)
STATE_TESTS = (
    "test_nothing_of_a_file_is_reachable_after_any_call",
    "test_the_allow_list_admits_the_clean_pack_by_its_rules",
    "test_the_allow_list_refuses_each_way_a_module_holds_state",
    "test_the_snapshot_reads_content_not_representation",
    "test_the_state_guards_after_every_call_compare_with_the_first",
    "test_the_snapshot_sees_a_change_through_each_holder",
    "test_the_state_guards_hold_every_container_by_every_mutator_in_every_holder",
    "test_the_state_guards_run_no_code_of_what_they_judge",
    "test_every_rule_looks_into_what_it_admits_or_is_a_named_leaf",
    "test_each_rule_refuses_what_lies_just_outside_it",
    "test_the_stop_set_is_looked_through_for_the_pack_s_objects",
    "test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",
    "test_each_kind_of_module_is_refused_or_entered",
    "test_no_reader_of_a_walked_object_runs_its_code",
    "test_a_reject_names_no_value",
    "test_a_reject_s_path_is_stable",
)
"""Every test of the state guards: what a guard's mutant is replayed against to find its kills."""


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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import",
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
        scan="allow at import; snapshot after 1-4, 7-9; hits after 1-4, 7-9",
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
        scan="allow at import; snapshot after 1-4, 7-11; hits after 1-4, 7-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-4, 7-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; hits after 1-11",
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
        scan="allow at import; snapshot after 5-11; hits after 5-11",
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
        scan="allow at import; snapshot after 5-11; hits after 5-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-4, 7-11",
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
        scan="snapshot after 1-4, 7-11",
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
        scan="snapshot after 1-4, 7-11; hits after 1-4, 7-11",
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
        scan="snapshot after 1-11",
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
        scan="allow at import",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow after 1-11; snapshot after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow after 1-11; snapshot after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow after 1-11; snapshot after 1-11",
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
        scan="snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import",
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
        scan="allow at import",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="allow at import; snapshot after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow after 8-11; snapshot after 8-11; hits after 8-11",
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
        scan="allow after 9-11; snapshot after 9-11; hits after 9-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow after 9-11; snapshot after 9-11",
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
        scan="snapshot after 7-11; hits after 7-11",
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
        scan="allow after 8-11; snapshot after 8-11",
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
        scan="snapshot after 7-11; hits after 7-11",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
                "        seen = reachable([*markers[:done], canary], ignore=ignore)",
                "        seen = reachable(markers[:done], ignore=ignore)",
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
        scan="hits after 1-11",
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
        scan="hits after 1-11",
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
        scan="allow at import; snapshot after 1-11; hits after 1-11",
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
    Mutant(
        "N01",
        "a module-level [].append, fed ids' prefixes",
        (
            Edit(CLINICAL, ROW, ROW + "        _KEEP(identifier[:8])\n"),
            Edit(CLINICAL, HEAD, HEAD + "\n_KEEP = [].append\n"),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "N02",
        "a module-level deque(maxlen=128).append, fed ids' prefixes",
        (
            Edit(CLINICAL, ROW, ROW + "        _KEEP(identifier[:8])\n"),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport collections as _c\n_KEEP = _c.deque(maxlen=128).append\n",
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "N03",
        "a module-level dict subclass instance, keyed by ids' prefixes",
        (
            Edit(CLINICAL, ROW, ROW + "        _M[identifier[:8]] = 1\n"),
            Edit(CLINICAL, HEAD, HEAD + "\nclass _D(dict):\n    pass\n_M = _D()\n"),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "N04",
        "an id's prefix in the pack manifest's __pydantic_private__",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        object.__setattr__(_sys.modules['aibi.packs.onco'].PACK.manifest,"
                " '__pydantic_private__', {'_p': identifier[:8]})\n",
            ),
            Edit(CLINICAL, HEAD, SYS),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N05",
        "an id's prefix on a core class the pack imports (ParseSettings)",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        type.__setattr__(ParseSettings, '_p', identifier[:8])\n",
            ),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N06",
        "a module-level logging.Logger with a BufferingHandler, logging ids' prefixes",
        (
            Edit(CLINICAL, ROW, ROW + "        _LOG.debug('row %s', identifier[:8])\n"),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport logging as _logging, logging.handlers as _lh\n"
                "_LOG = _logging.getLogger(__name__)\n_LOG.setLevel(_logging.DEBUG)\n"
                "_LOG.addHandler(_lh.BufferingHandler(10**6))\n",
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "N07",
        "the whole id logged at WARNING through logging, no handler of the pack's",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _logging.getLogger(__name__).warning('row %s', identifier)\n",
            ),
            Edit(CLINICAL, HEAD, HEAD + "\nimport logging as _logging\n"),
        ),
        scan="clean",
        survives=(
            "a log record: the child keeps none (no handler of the pack's or the core's keeps"
            " one), so nothing in the heap holds it; another process's log is outside the"
            " heap; no scan holds it"
        ),
    ),
    Mutant(
        "N08",
        "the descriptors read back through validate_json (pydantic-core's string cache)",
        (
            Edit(
                BUILD,
                "        return _ADAPTER.validate_python(written)",
                "        return _ADAPTER.validate_json("
                "_ADAPTER.dump_json(_ADAPTER.validate_python(written)))",
            ),
        ),
        scan="clean",
        survives=(
            "pydantic-core's C-level cache of the strings of 64 characters or less a"
            " validate_json reads, which no collector's root reaches; the core's own reads"
            " fill it in production: jjgao/aibi#155's decision; no scan holds it"
        ),
    ),
    Mutant(
        "N09",
        "a pack closure holding a name's prefix, registered as an audit hook at each read",
        (
            Edit(
                CLINICAL,
                READ_START,
                READ_START + "    _sys.addaudithook(lambda e, a, _k=name[:8]: None)\n",
            ),
            Edit(CLINICAL, HEAD, SYS),
        ),
        scan="clean",
        survives=(
            "the interpreter's registry of audit hooks, which no collector's root reaches (as"
            " atexit's, C22, and signal handlers, the codec search and sys's hooks); no scan"
            " holds it"
        ),
    ),
    Mutant(
        "N10",
        "an id's prefix as a non-field attribute of a module-level frozen dataclass instance",
        (
            Edit(CLINICAL, ROW, ROW + "        object.__setattr__(_PS, '_p', identifier[:8])\n"),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\n@dataclass(frozen=True)\nclass _PSC:\n    a: int = 1\n_PS = _PSC()\n",
            ),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N11",
        "an id's prefix in an Annotated alias's metadata list",
        (
            Edit(CLINICAL, ROW, ROW + "        _AL.__metadata__[0][:] = [identifier[:8]]\n"),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nfrom typing import Annotated as _An\n_AL = _An[str, list()]\n",
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "N12",
        "a random.Random made at import, seeded with every id",
        (
            Edit(CLINICAL, ROW, ROW + "        _R.seed(identifier)\n"),
            Edit(CLINICAL, HEAD, HEAD + "\nimport random as _random\n_R = _random.Random(0)\n"),
        ),
        scan="allow at import",
    ),
    Mutant(
        "N13",
        "the module random's own generator seeded with every id",
        (
            Edit(CLINICAL, ROW, ROW + "        _random.seed(identifier)\n"),
            Edit(CLINICAL, HEAD, HEAD + "\nimport random as _random\n"),
        ),
        scan="clean",
        survives=(
            "another library's C state (the random module's generator, seeded by a value):"
            " outside the pack's state; no scan holds it"
        ),
    ),
    Mutant(
        "N14",
        "a zlib compressor made on the importer at first call, fed every dataset id",
        (
            Edit(
                IMPORTER,
                IMPORT_START,
                "        OncoImporter._z = OncoImporter.__dict__.get('_z')"
                " or _zlib.compressobj()\n"
                "        OncoImporter._z.compress(options.dataset.encode())\n" + IMPORT_START,
            ),
            Edit(IMPORTER, BY, BY + "import zlib as _zlib\n"),
        ),
        scan="allow after 1-11; snapshot after 1-11",
    ),
    Mutant(
        "N15",
        "a class-level counter: rows ever read, an int on a class attribute",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        Clinical._n = Clinical.__dict__.get('_n', 0) + 1  # type: ignore\n",
            ),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N16",
        "an id's prefix in the module's __doc__",
        (
            Edit(CLINICAL, ROW, ROW + "        _sys.modules[__name__].__doc__ = identifier[:8]\n"),
            Edit(CLINICAL, HEAD, SYS),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N17",
        "an id's prefix in a module dunder of the pack's own (__cache__)",
        (
            Edit(
                CLINICAL,
                ROW,
                ROW + "        _sys.modules[__name__].__dict__['__cache__'] = identifier[:8]\n",
            ),
            Edit(CLINICAL, HEAD, SYS),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "N18",
        "an id's prefix on a core function the pack imports (is_text.__dict__)",
        (Edit(CLINICAL, ROW, ROW + "        is_text.__dict__['_p'] = identifier[:8]\n"),),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "P1",
        "a module-level bound method of a pack instance holding a hashlib object, fed every id",
        (
            Edit(CLINICAL, ROW, ROW + "        _ADD(identifier.encode())\n"),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nclass _Acc:\n    def __init__(self):\n        self.h = hashlib.sha256()\n"
                "    def add(self, x):\n        self.h.update(x)\n_ADD = _Acc().add\n",
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "P2",
        "C02's hash kept from each patient read and deleted by propose (held after calls 1-8)",
        (
            Edit(
                CLINICAL,
                READ_END,
                "    done = reading.finished(data)\n"
                '    if role == "patient":\n'
                "        Clinical._h = hashlib.sha256("
                "','.join(sorted(reading.keys)[:128]).encode()).digest()  # type: ignore\n"
                "    return done\n\n\n__all__",
            ),
            Edit(
                ENDPOINTS,
                "    return tuple(found)\n",
                "    import aibi.packs.onco.clinical as _cl\n"
                "    if '_h' in _cl.Clinical.__dict__:\n"
                "        del _cl.Clinical._h  # type: ignore\n"
                "    return tuple(found)\n",
            ),
        ),
        scan="snapshot after 1-8, 10-11",
    ),
    Mutant(
        "RV1",
        "P1 through an Annotated alias's metadata (a bound method, of the stop set, inside it)",
        (
            Edit(
                CLINICAL,
                ROW + NEXT,
                ROW + "        _AL.__metadata__[0](identifier.encode())\n" + NEXT,
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + ACC + "from typing import Annotated as _An\n_AL = _An[str, _Acc().add]\n",
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RV2",
        "P1 through a PlainValidator in an Annotated alias",
        (
            Edit(
                CLINICAL,
                ROW + NEXT,
                ROW + "        _AL.__metadata__[0].func(identifier.encode())\n" + NEXT,
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + ACC + "from typing import Annotated as _An\n"
                "from pydantic.functional_validators import PlainValidator as _PV\n"
                "_AL = _An[str, _PV(_Acc().add)]\n",
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RV3",
        "NB01 with a module made by types.ModuleType for the SimpleNamespace",
        (
            Edit(
                CLINICAL,
                ROW + NEXT,
                ROW
                + "        _STATE.last = (getattr(_STATE, 'last', ()) + (identifier[:8],))[-128:]\n"
                + NEXT,
            ),
            Edit(
                CLINICAL, HEAD, HEAD + "\nimport types as _ty\n_STATE = _ty.ModuleType('state')\n"
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "RV4",
        "a count of ids in a NamedTuple field's default (itertools.count)",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        next(_Row().n)\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\nimport itertools as _it\nfrom typing import NamedTuple as _NT\n"
                "class _Row(_NT):\n    n: object = _it.count()\n",
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RV5",
        "a hash of every id in a plain class's attribute named _fields",
        (
            Edit(
                CLINICAL,
                ROW + NEXT,
                ROW + "        _Seen._fields.update(identifier.encode())\n" + NEXT,
            ),
            Edit(CLINICAL, HEAD, HEAD + "\nclass _Seen:\n    _fields = hashlib.sha256()\n"),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RV6",
        "prefixes in a closure's dict, its function saying another module (json)",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _KEEP(identifier[:8])\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + "\ndef _mk():\n    seen = {}\n    def keep(x):\n        seen[x] = 1\n"
                "    return keep\n_KEEP = _mk()\n_KEEP.__module__ = 'json'\n",
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "P3",
        "P1's bound method met first in an alias's metadata, then bound at module level",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _ADD(identifier.encode())\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD + ACC + "from typing import Annotated as _An\n_AL = _An[str, _Acc().add]\n"
                "_ADD = _AL.__metadata__[0]\n",
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "A1",
        "the allow-list's rules widened to admit a SimpleNamespace",
        (
            Edit(
                STATE,
                "        elif facts.frozen_model:",
                '        elif facts.name == "types.SimpleNamespace":\n'
                '            self.admit("exempt")\n            return\n'
                "        elif facts.frozen_model:",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
            "test_the_state_guards_after_every_call_compare_with_the_first",
        ),
    ),
    Mutant(
        "A2",
        "a tuple told by isinstance, not by identity (a spoofed __class__ is read)",
        (
            Edit(
                STATE,
                "        if kind is tuple or kind is frozenset:\n            self.admit(",
                "        if isinstance(item, tuple | frozenset):\n            self.admit(",
            ),
        ),
        scan="clean",
        tests=(
            "test_the_allow_list_admits_the_clean_pack_by_its_rules",
            "test_the_state_guards_run_no_code_of_what_they_judge",
        ),
    ),
    Mutant(
        "A3",
        "an exempt object admitted and not looked into",
        (
            Edit(
                STATE,
                "        self.admit(rule)\n",
                '        self.admit(rule)\n        if rule == "exempt":\n            return\n',
            ),
        ),
        scan="clean",
        tests=(
            "test_the_allow_list_admits_the_clean_pack_by_its_rules",
            "test_the_state_guards_hold_every_container_by_every_mutator_in_every_holder",
            "test_the_stop_set_is_looked_through_for_the_pack_s_objects",
        ),
    ),
    Mutant(
        "A4",
        "the stop set applied by type everywhere, not inside a foreign holder alone",
        (
            Edit(
                STATE,
                "        elif inside and id(kind) in self.stop:\n"
                "            self.stopped_by(kind)\n            self.look_through(item, path)\n"
                "        elif kind is MethodType:",
                "        elif id(kind) in self.stop:\n            self.stopped_by(kind)\n"
                "            self.look_through(item, path)\n"
                "        elif kind is MethodType:",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_state_guards_run_no_code_of_what_they_judge",
        ),
    ),
    Mutant(
        "A5",
        "a callable admitted without its __self__ judged",
        (
            Edit(
                STATE,
                "        kind = type(bound)\n        facts = None if",
                '        self.admit("bound callable")\n        return\n'
                "        kind = type(bound)\n        facts = None if",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
            "test_the_state_guards_hold_every_container_by_every_mutator_in_every_holder",
            "test_the_state_guards_run_no_code_of_what_they_judge",
        ),
    ),
    Mutant(
        "A6",
        "the context of a foreign holder carried into a pack-owned object",
        (
            Edit(
                STATE,
                "        inside = False if facts.own else inside or model\n",
                "        inside = inside or model\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "A7",
        "a module's dunders admitted by a pattern, not by a closed list of names",
        (
            Edit(
                STATE,
                "            if type(key) is str and key in MODULE_MACHINERY:\n"
                "                self.module_machinery(key, value, path)",
                '            if type(key) is str and key.startswith("__") and key.endswith("__"):\n'
                '                self.admit("module machinery")',
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_admits_the_clean_pack_by_its_rules",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "A8",
        "any __slotnames__ admitted",
        (
            Edit(
                STATE,
                "                if _slotnames_kept(kind, value):",
                "                if True:",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "A9",
        "nonlocal not refused by the source guard",
        (
            Edit(
                TEST,
                '        elif isinstance(node, ast.Nonlocal):\n            found.append(f"line'
                " {node.lineno}: nonlocal {', '.join(node.names)}\")\n",
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_source_guard_refuses_a_nonlocal_binding",),
    ),
    Mutant(
        "A10",
        "the allow-list run before the first call alone, not after every call",
        (
            Edit(
                STATE,
                "    def after(self, done: int) -> None:\n"
                "        judged = allowed(self.modules, self.exempt, self.names)\n",
                "    def after(self, done: int) -> None:\n"
                "        judged = allowed([], self.exempt, self.names)\n",
            ),
        ),
        scan="clean",
        tests=("test_the_state_guards_after_every_call_compare_with_the_first",),
    ),
    Mutant(
        "D1",
        "the snapshot compared with the first after the last call alone",
        (
            Edit(
                STATE,
                "        found = differences(self.first, now)\n",
                "        found = differences(self.first, now) if done in (3, 9) else []\n",
            ),
        ),
        scan="clean",
        tests=("test_the_state_guards_after_every_call_compare_with_the_first",),
    ),
    Mutant(
        "D2",
        "each snapshot compared with the one before it, not with the first",
        (
            Edit(
                STATE,
                "        found = differences(self.first, now)\n",
                "        found = differences(self.first, now)\n        self.first = now\n",
            ),
        ),
        scan="clean",
        tests=("test_the_state_guards_after_every_call_compare_with_the_first",),
    ),
    Mutant(
        "D3",
        "the snapshot enters the pack's functions, not the core's",
        (
            Edit(
                STATE,
                "        if not self.registry.of_core(function):",
                "        if not self.registry.own(function):",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_a_change_through_each_holder",),
    ),
    Mutant(
        "D4",
        "the snapshot enters the pack's classes, not the core's",
        (
            Edit(
                STATE,
                "            if facts.core or not self.registry.binds(item):",
                "            if facts.own or not self.registry.binds(item):",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_a_change_through_each_holder",),
    ),
    Mutant(
        "D5",
        "the snapshot enters no instance",
        (
            Edit(
                STATE,
                '            self.put(path, f"object:{name}:{len(held)}:{own is not None}")\n'
                "            if own is not None:\n"
                '                self.push(f"{path}.__dict__", own)\n'
                "            for index, value in reversed(list(enumerate(held))):\n"
                '                self.push(f"{path}<{index}>", value)\n',
                '            self.put(path, f"object:{name}:{len(held)}:{own is not None}")\n',
            ),
        ),
        scan="clean",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_the_snapshot_sees_a_change_through_each_holder",
            "test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",
            "test_the_state_guards_hold_every_container_by_every_mutator_in_every_holder",
        ),
    ),
    Mutant(
        "D6",
        "the snapshot reads the roots' values alone: no class's dict, no function entered",
        (
            Edit(
                STATE,
                "            if facts.core or not self.registry.binds(item):",
                "            if False:",
            ),
            Edit(
                STATE,
                "        if not self.registry.of_core(function):",
                "        if True:",
            ),
        ),
        scan="clean",
        tests=(
            "test_nothing_of_a_file_is_reachable_after_any_call",
            "test_the_snapshot_sees_a_change_through_each_holder",
            "test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",
            "test_the_state_guards_hold_every_container_by_every_mutator_in_every_holder",
        ),
    ),
    Mutant(
        "D7",
        "the snapshot holds content, not digests",
        (
            Edit(
                STATE,
                "        self.found[path] = hash(text)  # the process's keyed hash",
                "        self.found[path] = text  # the process's keyed hash",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_a_change_through_each_holder",),
    ),
    Mutant(
        "D8",
        "a path labels a dict key by its text",
        (
            Edit(
                STATE,
                '    return key if key in names or key in _CODE_NAMES else f"#{_digest(key)}"',
                "    return key",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_a_change_through_each_holder",),
    ),
    Mutant(
        "D9",
        "an instance's dict read through vars()",
        (
            Edit(
                STATE,
                "            found = _GETSET_GET(getset, item, kind)",
                "            found = vars(item)",
            ),
        ),
        scan="clean",
        tests=("test_the_state_guards_run_no_code_of_what_they_judge",),
    ),
    Mutant(
        "D10",
        "the snapshot's seen set keeps nothing alive (an id it saw given to another object)",
        (
            Edit(
                STATE,
                "        self.seen[id(item)] = (path, item)",
                "        self.seen[id(item)] = (path, None)",
            ),
        ),
        scan="clean",
        survives=(
            "equivalent: the pass enters no temporary of its own (a class's dict is read behind"
            " its mappingproxy since review round 1), so keeping what it entered alive changes"
            " nothing today; the rule stays for a pass that comes to make one"
        ),
    ),
    Mutant(
        "D11",
        "a function's annotations and dict read as they are, not materialised",
        (
            Edit(
                STATE,
                "        _FUNC_ANNOTATIONS(function)  # content, not form: a tuple made a dict,"
                " as any read does\n        _FUNC_DICT(function)\n",
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_reads_content_not_representation",),
    ),
    Mutant(
        "D12",
        "a class's __slotnames__ that copyreg wrote read as content",
        (
            Edit(
                STATE,
                ' and key == "__slotnames__" and _slotnames_kept(owner, value))',
                ' and key == "__slotnames__" and False)',
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_reads_content_not_representation",),
    ),
    Mutant(
        "D13",
        "the walk reads the state guards' stores",
        (
            Edit(
                SCAN,
                "        ignore = (hits, walks, ignored, *stores)\n",
                "        ignore = (hits, walks, ignored)\n",
            ),
        ),
        scan="stores",
        tests=("test_nothing_of_a_file_is_reachable_after_any_call",),
    ),
    Mutant(
        "RG1",
        "a mappingproxy's keys not judged",
        (
            Edit(
                STATE,
                (
                    '            self.judge(key, f"{path}[key {index}]", inside)\n'
                    '            at = f"{path}[{self.key(key, index)}]"\n'
                    "            if exempt"
                ),
                ('            at = f"{path}[{self.key(key, index)}]"\n            if exempt'),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG2",
        "a set's atoms left out of its digest (its length alone)",
        (
            Edit(
                STATE,
                "{len(members)}:{','.join(atoms)}",
                "{len(members)}",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "RG3",
        "a dict's keys that are not str not entered by the snapshot",
        (
            Edit(
                STATE,
                (
                    "            if type(key) is not str:\n"
                    '                self.push(f"{path}[key {index}]", key)\n'
                ),
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "RG4",
        "a frozen model told by any model_config with a frozen key",
        (
            Edit(
                STATE,
                'type(config) is dict and _lookup(config, "frozen") is True',
                'type(config) is dict and _lookup(config, "frozen") is not _MISSING',
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG5",
        "the fields-set rule admits any set",
        (
            Edit(
                STATE,
                (
                    "            if type(value) is set and all(type(n) is str for n in"
                    " gc.get_referents(value)):"
                ),
                "            if type(value) is set:",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG6",
        "a model's extra and private dict admitted, not judged",
        (
            Edit(
                STATE,
                (
                    "        elif type(value) is dict:\n"
                    '            self.items(value, at, inside, json=name == "__pydantic_extra__")\n'
                ),
                ('        elif type(value) is dict:\n            self.admit("JSON container")\n'),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_stop_set_is_looked_through_for_the_pack_s_objects",
        ),
    ),
    Mutant(
        "RG7",
        "a module's __all__ admitted whatever it is",
        (
            Edit(
                STATE,
                (
                    "                if type(value) is list and all(type(i) is str for i in"
                    " _LIST_COPY(value, _ALL)):\n"
                    '                    self.admit("__all__")'
                ),
                ('                if True:\n                    self.admit("__all__")'),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG8",
        "a JSON list's items all admitted",
        (
            Edit(
                STATE,
                (
                    "            if type(value) is dict or type(value) is list:\n"
                    "                self.json(value, at, inside)  # pyright:"
                    " ignore[reportArgumentType]\n"
                    "            else:\n"
                    "                self.judge(value, at, inside)\n"
                ),
                (
                    "            if type(value) is dict or type(value) is list:\n"
                    "                self.json(value, at, inside)  # pyright:"
                    " ignore[reportArgumentType]\n"
                ),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG9",
        "any set in any closure admitted as dataclasses' repr guard",
        (
            Edit(
                STATE,
                "            if guard and type(value) is set and not gc.get_referents(value):",
                "            if type(value) is set:",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG10",
        "a class's slot descriptors told by any descriptor type (a property's too)",
        (
            Edit(
                STATE,
                "        descriptors |= _ids((WrapperDescriptorType,))\n",
                (
                    "        descriptors |= _ids((WrapperDescriptorType, property, staticmethod,"
                    " classmethod))\n"
                ),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_admits_the_clean_pack_by_its_rules",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "RG11",
        "a static or class method's or a property's functions not judged",
        (
            Edit(
                STATE,
                '            self.admit("descriptor")\n',
                ('            self.admit("descriptor")\n            return\n'),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "RG12",
        "the snapshot never reads the own dict of a str, list or dict subclass's instance",
        (
            Edit(
                STATE,
                '        if self.kinds.of(kind).module != "builtins":',
                "        if False:",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "RG13",
        "the snapshot drops every __slotnames__ of a class dict, not copyreg's alone",
        (
            Edit(
                STATE,
                (
                    'if not (type(key) is str and key == "__slotnames__" and'
                    " _slotnames_kept(owner, value))"
                ),
                'if not (type(key) is str and key == "__slotnames__")',
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "RG17",
        "any empty set in any closure admitted as dataclasses' repr guard",
        (
            Edit(
                STATE,
                "        guard = type(filename) is str and str.__eq__("
                "filename, dataclasses.__file__)\n",
                "        guard = True\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG18",
        "bytes digested by their length",
        (
            Edit(
                STATE,
                '        return "bytes:" + bytes.hex(item)  # pyright: ignore[reportArgumentType]',
                '        return f"bytes:{len(item)}"  # pyright: ignore[reportArgumentType]',
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "RG21",
        "a class's _field_defaults and __annotations__ admitted by shape, their values not judged",
        (
            Edit(
                STATE,
                (
                    "            if ok:\n"
                    '                self.items(value, path, key == "__annotations__")\n'
                ),
                "",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG22",
        "a dataclass field's default, factory and metadata not judged",
        (
            Edit(
                STATE,
                '                        self.judge(held, f"{at}<{place}>")\n',
                "                        continue\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG23",
        "a class's _fields admitted whatever it is",
        (
            Edit(
                STATE,
                (
                    "            ok = _sub(kind, tuple) and type(value) is tuple\n"
                    "            ok = ok and all(type(i) is str for i in"
                    " _TUPLE_COPY(value, _ALL))\n"
                ),
                "            ok = True\n",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "RG24",
        "any module admitted by its type, a module the pack made among them",
        (
            Edit(
                STATE,
                "            found = self.registry.module_kind(item, self.roots)  # pyright:",
                "            found = self.registry.module_kind(item, self.roots)"
                ' or "library module"'
                "  # pyright:",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
        ),
    ),
    Mutant(
        "RG25",
        "a stop-set object a leaf: admitted and not looked through",
        (
            Edit(
                STATE,
                (
                    "            self.stopped_by(kind)\n"
                    "            self.look_through(item, path)\n"
                    "        elif kind is MethodType:"
                ),
                ("            self.stopped_by(kind)\n        elif kind is MethodType:"),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_the_allow_list_admits_the_clean_pack_by_its_rules",
            "test_the_allow_list_refuses_each_way_a_module_holds_state",
            "test_the_stop_set_is_looked_through_for_the_pack_s_objects",
        ),
    ),
    Mutant(
        "RG26",
        "an object judged once whatever the context (seen keyed by id alone)",
        (
            Edit(
                STATE,
                (
                    "        if (id(item), inside) in self.seen:\n"
                    "            return\n"
                    "        self.seen[(id(item), inside)] = item\n"
                    "        if kind is tuple"
                ),
                (
                    "        if (id(item), False) in self.seen:\n"
                    "            return\n"
                    "        self.seen[(id(item), False)] = item\n"
                    "        if kind is tuple"
                ),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "RG27",
        "a function told the pack's by the __module__ it says, not by its __globals__",
        (
            Edit(
                STATE,
                (
                    "        return id(_FUNC_GLOBALS(function)) not in self.foreign or"
                    " _own(_FUNC_MODULE(function))"
                ),
                "        return _own(_FUNC_MODULE(function))",
            ),
        ),
        scan="clean",
        tests=("test_the_allow_list_refuses_each_way_a_module_holds_state",),
    ),
    Mutant(
        "RG34",
        "a function judged only where its __globals__ is a pack module's dict",
        (
            Edit(
                STATE,
                "        return id(_FUNC_GLOBALS(function)) not in self.foreign or",
                "        return id(_FUNC_GLOBALS(function)) in self.pack or",
            ),
        ),
        scan="clean",
        tests=("test_the_allow_list_refuses_each_way_a_module_holds_state",),
    ),
    Mutant(
        "RG28",
        "a class told the pack's by the __module__ it says, not by where it is bound",
        (
            Edit(
                STATE,
                "        return self.kinds.of(kind).own or not self.registry.binds(kind)",
                "        return self.kinds.of(kind).own",
            ),
        ),
        scan="clean",
        tests=("test_the_allow_list_refuses_each_way_a_module_holds_state",),
    ),
    Mutant(
        "RG29",
        "a module dict's keys not judged",
        (
            Edit(
                STATE,
                (
                    "            if id(type(key)) not in ATOMS:\n"
                    '                self.judge(key, f"{name}:[key {index}]")\n'
                ),
                "",
            ),
        ),
        scan="clean",
        tests=("test_the_allow_list_refuses_each_way_a_module_holds_state",),
    ),
    Mutant(
        "RG30",
        "a reject names a class by its qualname whatever made it",
        (
            Edit(
                STATE,
                "        if qualname in names or self.binds(kind):",
                "        if True:",
            ),
        ),
        scan="clean",
        tests=("test_a_reject_names_no_value",),
    ),
    Mutant(
        "RG31",
        "a key's label an unkeyed digest",
        (
            Edit(
                STATE,
                "    return hashlib.blake2b(encoded, digest_size=8, key=_KEY).hexdigest()",
                "    return hashlib.blake2b(encoded, digest_size=8).hexdigest()",
            ),
        ),
        scan="clean",
        tests=("test_a_reject_names_no_value",),
    ),
    Mutant(
        "RG32",
        "abc's cache read as content by the snapshot",
        (
            Edit(
                STATE,
                "        elif id(kind) in self.abc:  # abc's cache",
                "        elif False:  # abc's cache",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_reads_content_not_representation",),
    ),
    Mutant(
        "RG33",
        "the stop set looked through for the pack's objects alone",
        (
            Edit(
                STATE,
                ("            else:\n                self.judge(value, at, True)\n"),
                (
                    "            elif self.kinds.of(kind).own:\n"
                    "                self.judge(value, at)\n"
                ),
            ),
        ),
        scan="clean",
        tests=("test_the_allow_list_refuses_each_way_a_module_holds_state",),
    ),
    Mutant(
        "RX1",
        "prefixes in a module the pack registers under a foreign name",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _S.last = identifier[:8]\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD
                + (
                    "\n"
                    "import sys as _sy, types as _ty\n"
                    "_S = _sy.modules.setdefault('onco_state', _ty.ModuleType('onco_state'))\n"
                    "_S.last = ''\n"
                ),
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "RX2",
        "prefixes in a module the pack registers under its own prefix",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _S.seen.append(identifier[:8])\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD
                + (
                    "\n"
                    "import sys as _sy, types as _ty\n"
                    "_S = _sy.modules.setdefault('aibi.packs.onco._state',"
                    " _ty.ModuleType('aibi.packs.onco._state'))\n"
                    "_S.seen = []\n"
                ),
            ),
        ),
        scan="allow at import; snapshot after 1-11",
    ),
    Mutant(
        "RX3",
        "a prefix set on a core module the pack imports",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _inf._onco_last = identifier[:8]\n" + NEXT),
            Edit(CLINICAL, HEAD, HEAD + ("\nimport aibi.core.importers.infer as _inf\n")),
        ),
        scan="snapshot after 1-11",
    ),
    Mutant(
        "RX4",
        "a hash of every id in a keyword default held by a dict subclass",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _k(identifier.encode())\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD
                + (
                    "\n"
                    "import hashlib as _hl\n"
                    "class _D(dict):\n"
                    "    pass\n"
                    "def _k(x, *, h=None):\n"
                    "    h.update(x)\n"
                    "_k.__kwdefaults__ = _D(h=_hl.sha256())\n"
                ),
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RX5",
        "a hash of every id in a default held by a tuple subclass whose __len__ is 0",
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _k(identifier.encode())\n" + NEXT),
            Edit(
                CLINICAL,
                HEAD,
                HEAD
                + (
                    "\n"
                    "import hashlib as _hl\n"
                    "class _T(tuple):\n"
                    "    def __len__(self):\n"
                    "        return 0\n"
                    "def _k(x, h=None):\n"
                    "    h.update(x)\n"
                    "_k.__defaults__ = _T((_hl.sha256(),))\n"
                ),
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "RX6",
        (
            "a type alias of the pack's whose value is read at call time (refused: its cached"
            " value is held by no guard)"
        ),
        (
            Edit(CLINICAL, ROW + NEXT, ROW + "        _X.__value__\n" + NEXT),
            Edit(CLINICAL, HEAD, HEAD + ("\ntype _X = int\n")),
        ),
        scan="allow at import",
    ),
    Mutant(
        "GX4",
        "a module's __annotations__ not judged",
        (
            Edit(
                STATE,
                (
                    '                self.admit("module machinery")\n'
                    "                self.items(value, path, True)\n"
                ),
                '                self.admit("module machinery")\n',
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "GX6",
        "the snapshot enters no module the pack made",
        (
            Edit(
                STATE,
                '            if found == "library module":  # another library\'s: by its name',
                (
                    '            if found is None or found == "library module":  # another'
                    " library's: by its name"
                ),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_kind_of_module_is_refused_or_entered",
            "test_the_snapshot_sees_a_change_through_each_holder",
        ),
    ),
    Mutant(
        "GX7",
        "a set's members that are not atoms not entered by the snapshot",
        (
            Edit(
                STATE,
                "            others = [m for m in members if id(type(m)) not in ATOMS]\n",
                "            others = []\n",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_sees_each_leaf_replaced_by_one_of_its_size",),
    ),
    Mutant(
        "GX10",
        "a class's _abc_impl admitted whatever it is",
        (
            Edit(
                STATE,
                (
                    "            ok = any(type(value) is known for known in self.machinery[key])\n"
                    '        elif key == "__dataclass_params__":'
                ),
                ('            ok = True\n        elif key == "__dataclass_params__":'),
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_no_reader_of_a_walked_object_runs_its_code",
        ),
    ),
    Mutant(
        "GX16",
        "a static or class method's own dict not judged",
        (
            Edit(
                STATE,
                (
                    "                            self.judge(held,"
                    ' f"{path}.__dict__[{self.key(key, at)}]", inside)\n'
                ),
                "                            pass\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "GX17",
        "a stop-set object reached as a callable's __self__ not looked through",
        (
            Edit(
                STATE,
                (
                    "        elif inside and id(kind) in self.stop:\n"
                    "            self.stopped_by(kind)\n"
                    "            self.look_through(bound, path)\n"
                ),
                (
                    "        elif inside and id(kind) in self.stop:\n"
                    "            self.stopped_by(kind)\n"
                ),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "GX19",
        "a builtin bound to any module admitted as unbound",
        (
            Edit(
                STATE,
                (
                    "                type(bound) is ModuleType\n"
                    "                and self.registry.module_kind(bound, self.roots) is not"
                    " None  # pyright: ignore[reportArgumentType]\n"
                    "            ):"
                ),
                ("                type(bound) is ModuleType\n            ):"),
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "GX20",
        "a static or class method's __annotations__ not judged",
        (
            Edit(
                STATE,
                ('                            self.items(held, f"{path}.__annotations__", True)\n'),
                "                            pass\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "GX21",
        "a function's containers told by isinstance, not by their exact type",
        (
            Edit(
                STATE,
                "        if value is None or type(value) is base:\n",
                "        if value is None or isinstance(value, base):\n",
            ),
        ),
        scan="clean",
        tests=("test_no_reader_of_a_walked_object_runs_its_code",),
    ),
    Mutant(
        "GX22",
        "a function's names, module, doc and annotations not judged",
        (
            Edit(
                STATE,
                "        for index, value in enumerate(gc.get_referents(function)):\n",
                "        for index, value in enumerate(()):\n",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_no_reader_of_a_walked_object_runs_its_code",
        ),
    ),
    Mutant(
        "GX23",
        "a pack class's name and qualified name not judged",
        (
            Edit(
                STATE,
                (
                    '        self.judge(_TYPE_NAME(kind), f"{path}.__name__")\n'
                    '        self.judge(_TYPE_QUALNAME(kind), f"{path}.__qualname__")\n'
                ),
                "",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_rule_refuses_what_lies_just_outside_it",
            "test_no_reader_of_a_walked_object_runs_its_code",
        ),
    ),
    Mutant(
        "GX24",
        "a planted model slot's TypeError let out",
        (
            Edit(
                STATE,
                (
                    "            except TypeError:  # a member descriptor of another class,"
                    " planted under the name\n"
                    '                self.reject(f"{path}.{name}", "a model slot that is not'
                    " its class's\")\n"
                    "                continue\n"
                ),
                "",
            ),
        ),
        scan="clean",
        tests=("test_no_reader_of_a_walked_object_runs_its_code",),
    ),
    Mutant(
        "GX25",
        "an object's referents read before its own dict is materialised",
        (
            Edit(
                STATE,
                (
                    "        self.kinds.own_dict(item, kind)\n"
                    "        return gc.get_referents(item)\n"
                ),
                "        return gc.get_referents(item)\n",
            ),
        ),
        scan="clean",
        tests=("test_a_reject_s_path_is_stable",),
    ),
    Mutant(
        "GX26",
        "a type alias's cached value read by the snapshot",
        (
            Edit(
                STATE,
                "        elif kind is _ALIAS:",
                "        elif False:",
            ),
        ),
        scan="clean",
        tests=("test_the_snapshot_reads_content_not_representation",),
    ),
    Mutant(
        "GX27",
        "a function's code file name compared by ==",
        (
            Edit(
                STATE,
                (
                    "        guard = type(filename) is str and str.__eq__(filename,"
                    " dataclasses.__file__)\n"
                ),
                "        guard = filename == dataclasses.__file__\n",
            ),
        ),
        scan="clean",
        tests=("test_no_reader_of_a_walked_object_runs_its_code",),
    ),
    Mutant(
        "ZP3",
        "prefixes in a list behind a type alias's cached value",
        (
            Edit(
                CLINICAL,
                ROW + NEXT,
                ROW
                + ("        _X.__value__.__metadata__[0].json_schema['k'].append(identifier[:8])\n")
                + NEXT,
            ),
            Edit(
                CLINICAL,
                HEAD,
                HEAD
                + (
                    "\n"
                    "import typing as _ty\n"
                    "from pydantic.json_schema import WithJsonSchema as _W\n"
                    "type _X = _ty.Annotated[str, _W({'k': []})]\n"
                ),
            ),
        ),
        scan="allow at import",
    ),
    Mutant(
        "Z1",
        "a module's spec not compared by its name",
        (
            Edit(
                STATE,
                (
                    "        if not (type(spec_name) is str and str.__eq__(spec_name,"
                    " name)):  # pyright: ignore[reportArgumentType]\n"
                ),
                "        if False:\n",
            ),
        ),
        scan="clean",
        tests=(
            "test_each_kind_of_module_is_refused_or_entered",
            "test_no_reader_of_a_walked_object_runs_its_code",
        ),
    ),
    Mutant(
        "Z2",
        "a module's loader not checked",
        (
            Edit(
                STATE,
                ("        if not any(loader is known for known in _LOADER_CLASSES) and not any(\n"),
                (
                    "        if False and not any(loader is known for known in"
                    " _LOADER_CLASSES) and not any(\n"
                ),
            ),
        ),
        scan="clean",
        tests=("test_each_kind_of_module_is_refused_or_entered",),
    ),
    Mutant(
        "Z17",
        "a class's __dataclass_params__'s referents not judged",
        (
            Edit(
                STATE,
                (
                    "                for index, held in enumerate(gc.get_referents(value)):\n"
                    '                    self.judge(held, f"{path}<{index}>")\n'
                ),
                "                pass\n",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
    Mutant(
        "Z21",
        "a pack module that is not a root admitted and not judged",
        (
            Edit(
                STATE,
                (
                    '                if found == "pack module" and id(item) not in'
                    " self.roots:  # judged as a root\n"
                    "                    self.module(item, _module_label(item))  # pyright:"
                    " ignore[reportArgumentType]\n"
                ),
                "",
            ),
        ),
        scan="clean",
        tests=("test_each_kind_of_module_is_refused_or_entered",),
    ),
    Mutant(
        "Z22",
        "a type alias of the pack's admitted as a typing object",
        (
            Edit(
                STATE,
                "        elif kind is _ALIAS and not inside:",
                "        elif False:",
            ),
        ),
        scan="clean",
        tests=("test_each_rule_refuses_what_lies_just_outside_it",),
    ),
)
