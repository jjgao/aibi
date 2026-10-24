"""The clinical files' corpus (M4.2a-1b, D409's clinical files): every variant the 1b plan's four
review rounds ran on the pinned reference, as named cells, with what aibi does with each.

This is not a test: pytest does not collect it, and it imports nothing of the pack, so the tests
load it by its path inside a fixture (``corpus``). The variants were run on cbioportal-core 47890bb
outside the repository (its validator, in module form, under an audit hook, in a scratch
environment); what is checked in of those runs is each cell's outcome, ``clinical_reference.json``
(no message of the reference: an error is named by an identity in the pack's words). The cells:

- **rv1**: 123 whole studies, each a patient and a sample file (``rv1``), named ``NN_what``: the
  plan's first round's 105, 15 the code round ran for the stricter items no earlier cell
  witnessed (``85`` to ``99``), two more the code review's first round ran
  (``86_repeated_header_prefix_field`` and ``86_tab_hash_nonkey_second``) and one read under a
  lower ``import_cells``;
- **hdr2**: header rows 1 to 4, a key column first or second, eleven line shapes and twelve field
  shapes (280 cells);
- **hdr3**: header rows 1 to 4 by five column kinds (key, ``STRING``, ``NUMBER``, ``BOOLEAN`` and a
  status) by field shapes: padding by every whitespace code point and two more, leading, trailing
  and alone, the validator's null words in four cases and padded, lengths at the caps, priority
  forms, control characters and noncharacters (2,940 cells);
- **hdr4**: rows 1 and 2 by the five kinds, by every noncharacter and other code-point classes,
  bytes that are not UTF-8, and code-point lengths at the caps with a four-byte character last;
  **hdr4b**: their null test's neighbours (1,610 cells together);
- **cells**: data cells of a ``BOOLEAN`` column, ``OS_STATUS``, ``PFS_STATUS`` with
  ``PFS_MONTHS``, ``DFS_STATUS``, the keys, and Excel dates in a ``STRING`` and a ``NUMBER``
  column, each bare and padded by each whitespace code point and two more (1,938 cells; the
  reference ran three to a study, since it collapses repeated errors after three lines, and each
  cell's outcome is its own line's);
- **excel**: the Excel date class by brute force (``excel``): every sequence of up to four of a
  month, a digit, ``-``, ``,``, ``.``, ``:`` and ``;`` with one ``-``, and a month and a date beside
  ten neighbours of the marks, each cell alone in a study (the reference's own line, run one
  study each), so that no one of the four marks can be dropped or added unseen.

Each header and cell variant is a file of its own here, so that aibi's first refusal is that
variant's. ``expected_header`` and ``expected_cell`` are the plan's predicates, written here again
(never read from the pack): the code aibi gives each cell, or ``None``.
"""

import itertools
import re
from collections.abc import Callable, Mapping
from typing import NamedTuple

# --- The header rows (hdr2, hdr3, hdr4, hdr4b) ------------------------------------------------

NAMES = ["PATIENT_ID", "SITE", "SCORE", "FLAG", "OS_STATUS"]
KINDS = ["key", "STRING", "NUMBER", "BOOLEAN", "status"]
BASE = [["L"] * 5, ["D"] * 5, ["STRING", "STRING", "NUMBER", "BOOLEAN", "STRING"], ["1"] * 5]
DATA = [["P1", "x", "1", "TRUE", "0:LIVING"], ["P2", "y", "2", "FALSE", "1:DECEASED"]]
NULL_WORDS = [
    "[not applicable]",
    "[not available]",
    "[pending]",
    "[discrepancy]",
    "[completed]",
    "[null]",
    "",
    "na",
]
"""The validator's null words (V:2442)."""
PADS = [c for c in map(chr, range(0x110000)) if c.isspace() and c not in "\t\n\r"] + [
    "\ufeff",
    "\u200b",
]
"""Every whitespace code point but tab, line feed and carriage return (Python's ``str.isspace``),
then a byte order mark and a zero-width space."""
CONTROLS = {chr(c) for c in (*range(0x20), 0x7F)} - set("\t\n\r")
MAX_STRING = 4_096
MAX_TEXT = 10_000
GOOD_S = (
    b"#Patient\tSample\tTMB\n#The patient's id.\tSample\tTMB\n#STRING\tSTRING\tNUMBER\n#1\t1\t1\n"
    b"PATIENT_ID\tSAMPLE_ID\tTMB\nP1\tS1\t1\nP2\tS2\t2\n"
)
"""The sample file beside every header cell's patient file (the rv1 builders' ``GOOD_S``)."""


def header_file(row: int, col: int, raw: bytes) -> bytes:
    """The patient file whose header row ``row`` (from 0) holds ``raw`` at column ``col``."""
    rows = [[x.encode() for x in r] for r in BASE]
    rows[row][col] = raw
    lines = [b"#" + b"\t".join(r) for r in rows] + ["\t".join(NAMES).encode()]
    lines += ["\t".join(d).encode() for d in DATA]
    return b"\n".join(lines) + b"\n"


def _hdr3_shapes(row: int, base: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for c in PADS:
        h = f"U{ord(c):04X}"
        out += [(f"lead_{h}", c + base), (f"trail_{h}", base + c), (f"alone_{h}", c)]
    words = ["[not applicable]", "[not available]", "[pending]", "[discrepancy]", "[completed]"]
    words += ["[null]", "na"]
    cases: list[tuple[str, Callable[[str], str]]] = [
        ("lo", str.lower),
        ("up", str.upper),
        ("ti", str.title),
        ("mx", lambda s: "".join(ch.upper() if i % 2 else ch for i, ch in enumerate(s))),
    ]
    for w in words:
        for tag, f in cases:
            out.append((f"null_{w}_{tag}", f(w)))
        out.append((f"null_{w}_ideo", "\u3000" + w.upper() + "\u3000"))
        out.append((f"null_{w}_nbsp", "\xa0" + w + "\u0085"))
    out += [("blank", "")]
    if row in (0, 1):
        for n in (4095, 4096, 4097, 9999, 10000, 10001, 131072, 131073):
            out.append((f"len{n}", "L" * n))
        out.append(("len4096_trailsp", "L" * 4096 + " "))
        out.append(("len10000_trailsp", "L" * 10000 + " "))
    if row == 2:
        out += [("lower", base.lower()), ("title", base.title()), ("bom", "\ufeff" + base)]
    if row == 3:
        forms = ["01", "001", "0" * 16 + "1", "0" * 17 + "1", "-01", "-0", "+1", "1_0", "\uff11"]
        forms += ["\u0663", "-1", "-2", str(2**53 - 1), str(2**53), "9" * 17, "1" * 18]
        forms += ["0" * 4299 + "1", "0" * 4300 + "1", "1.0", "1e0", " -1 "]
        for v in forms:
            out.append((f"prio_{v[:20]}_{len(v)}", v))
    for c, t in (("\x01", "lead"), ("\x7f", "trail"), ("\x0b", "trail"), ("\x1f", "lead")):
        out.append((f"ctl_{ord(c):02X}_{t}", c + base if t == "lead" else base + c))
    out.append(("ctl_00_mid", base[:1] + "\x00" + base[1:]))
    for c in ("\ufffe", "\ufdd0", "\U0010ffff", "\U0001fffe"):
        out.append((f"nonchar_{ord(c):X}", base + c))
    return out


def hdr3() -> dict[str, tuple[int, int, bytes]]:
    cells: dict[str, tuple[int, int, bytes]] = {}
    for row in range(4):
        for col in range(5):
            for tag, value in _hdr3_shapes(row, BASE[row][col]):
                raw = value.encode("utf-8", "surrogatepass")
                cells[f"r{row + 1}_{KINDS[col]}_{tag}"] = (row, col, raw)
    return cells


NONCHARACTERS = [plane * 0x10000 + 0xFFFE for plane in range(17)]
NONCHARACTERS += [plane * 0x10000 + 0xFFFF for plane in range(17)] + list(range(0xFDD0, 0xFDF0))
OTHERS = {
    "pua_E000": "\ue000",
    "pua_F8FF": "\uf8ff",
    "pua_F0000": "\U000f0000",
    "pua_10FFFD": "\U0010fffd",
    "unas_0378": "\u0378",
    "unas_2FFFD": "\U0002fffd",
    "unas_E0080": "\U000e0080",
    "unas_FFFD_near_FDCF": "\ufdcf",
    "FDF0": "\ufdf0",
    "LS_2028": "\u2028",
    "PS_2029": "\u2029",
    "NEL_0085": "\u0085",
    "C1_0080": "\u0080",
    "C1_009F": "\u009f",
    "RLO_202E": "\u202e",
    "LRI_2066": "\u2066",
    "PDI_2069": "\u2069",
    "LRM_200E": "\u200e",
    "ALM_061C": "\u061c",
    "ZWNJ_200C": "\u200c",
    "ZWJ_200D": "\u200d",
    "WJ_2060": "\u2060",
    "BOM_FEFF": "\ufeff",
    "TAG_E0001": "\U000e0001",
    "REPL_FFFD": "\ufffd",
    "FFFC": "\ufffc",
    "comb_0301": "\u0301",
    "emoji_1F600": "\U0001f600",
    "FFFD_last_BMP_FFFD": "\uffef",
}
RAW = {
    "surr_EDA080": b"\xed\xa0\x80",
    "surr_EDBFBF": b"\xed\xbf\xbf",
    "cesu_pair": b"\xed\xa0\xbd\xed\xb8\x80",
    "byte_FF": b"\xff",
    "overlong_C080": b"\xc0\x80",
    "trunc_E2": b"\xe2\x82",
}


def _hdr4_shapes(base: str) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for cp in NONCHARACTERS:
        out.append((f"nc_{cp:X}_trail", (base + chr(cp)).encode()))
    for cp in (0xFFFE, 0xFDD0, 0xFDEF, 0x10FFFF, 0x1FFFF):
        out.append((f"nc_{cp:X}_lead", (chr(cp) + base).encode()))
        out.append((f"nc_{cp:X}_alone", chr(cp).encode()))
    for tag, c in OTHERS.items():
        out.append((f"o_{tag}_mid", (base + c + base).encode()))
        out.append((f"o_{tag}_alone", c.encode()))
    for tag, raw in RAW.items():
        out.append((f"raw_{tag}_trail", base.encode() + raw))
    for n in (4095, 4096, 9999, 10000):
        out.append((f"len{n}_cp_emoji_last", ("L" * (n - 1) + "\U0001f600").encode()))
    return out


def hdr4() -> dict[str, tuple[int, int, bytes]]:
    cells: dict[str, tuple[int, int, bytes]] = {}
    for row in (0, 1):
        for col in range(5):
            for tag, raw in _hdr4_shapes(BASE[row][col]):
                cells[f"r{row + 1}_{KINDS[col]}_{tag}"] = (row, col, raw)
    return cells


WORDS = {
    "diLongS": "[di\u017fcrepancy]",
    "pendDotless": "[pend\u0131ng]",
    "nullUpperI_dot": "[NULL]",
    "n_slash_a": "n/a",
    "null": "null",
    "none": "none",
    "missing": "[missing]",
    "N_pipe_A": "N|A",
    "N_space_A": "N A",
    "nan": "nan",
    "fullwidthNA": "\uff2e\uff21",
    "unknown": "[unknown]",
    "NA_zwsp": "NA\u200b",
    "NA_bom": "\ufeffNA",
    "kelvin_na": "nA",
    "pending_nbsp_inner": "[pend\xa0ing]",
    "completed_cap_dotI": "[COMPLETED]",
}


def hdr4b() -> dict[str, tuple[int, int, bytes]]:
    cells: dict[str, tuple[int, int, bytes]] = {}
    for row in (0, 1):
        for col in range(5):
            for tag, word in WORDS.items():
                cells[f"r{row + 1}_{KINDS[col]}_nb_{tag}"] = (row, col, word.encode())
    return cells


def hdr2() -> dict[str, bytes]:
    """Each cell's patient file: a key column first (``KA``) or second (``AK``)."""
    base = [["L", "L"], ["D", "D"], ["STRING", "STRING"], ["1", "1"]]

    def build(
        layout: str,
        rows: list[list[str]],
        line: Callable[[str], str] | None = None,
        at: int | None = None,
    ) -> bytes:
        names = ["PATIENT_ID", "SEX"] if layout == "KA" else ["SEX", "PATIENT_ID"]
        data = [["P1", "F"], ["P2", "M"]] if layout == "KA" else [["F", "P1"], ["M", "P2"]]
        lines: list[str] = []
        for i, r in enumerate(rows):
            s = "#" + "\t".join(r)
            if at == i and line is not None:
                s = line(s)
            lines.append(s)
        lines += ["\t".join(names)] + ["\t".join(d) for d in data]
        return ("\n".join(lines) + "\n").encode()

    line_shapes: list[tuple[str, Callable[[str], str]]] = [
        ("nohash", lambda s: s[1:]),
        ("sp", lambda s: " " + s),
        ("tab", lambda s: "\t" + s),
        ("nbsp", lambda s: "\xa0" + s),
        ("bom", lambda s: "\ufeff" + s),
        ("h2", lambda s: "#" + s),
        ("h3", lambda s: "##" + s),
        ("hsp", lambda s: "# " + s[1:]),
        ("htab", lambda s: "#\t" + s[1:]),
        ("trail_tab", lambda s: s + "\t"),
        ("empty", lambda s: "#"),
    ]
    field_shapes: list[tuple[str, Callable[[str], str]]] = [
        ("pre_sp", lambda v: " " + v),
        ("pre_nbsp", lambda v: "\xa0" + v),
        ("pre_bom", lambda v: "\ufeff" + v),
        ("pre_h", lambda v: "#" + v),
        ("pre_h2", lambda v: "##" + v),
        ("pre_ideo", lambda v: "\u3000" + v),
        ("post_sp", lambda v: v + " "),
        ("post_nbsp", lambda v: v + "\xa0"),
        ("blank", lambda v: ""),
        ("only_h", lambda v: "#"),
        ("only_sp", lambda v: " "),
        ("pre_nl", lambda v: "\u0085" + v),
    ]
    cells: dict[str, bytes] = {}
    for layout in ("KA", "AK"):
        for row in range(4):
            for tag, f in line_shapes:
                cells[f"{layout}_r{row + 1}_line_{tag}"] = build(layout, base, f, row)
            for col in range(2):
                for tag, g in field_shapes:
                    rows = [list(r) for r in base]
                    rows[row][col] = g(rows[row][col])
                    cells[f"{layout}_r{row + 1}_c{col + 1}_{tag}"] = build(layout, rows)
    return cells


def expected_header(row: int, col: int, raw: bytes) -> str | None:
    """The plan's code for a header cell of ``hdr3``, ``hdr4`` or ``hdr4b`` (``None``: accepted):
    strict UTF-8, then the 30 control characters file-wide, then the cell's row: rows 1 and 2 a
    null word by ``strip().lower()``, a cap in code points as written (a label's, a text's), or
    not Unicode text (a noncharacter); row 3 exactly ``STRING``, ``NUMBER`` or ``BOOLEAN``, and
    ``STRING`` for the status column, which cBioPortal predefines (``OS_STATUS``); row 4 a priority
    of 1 to 17 ASCII digits with an optional ``-``, from -1 to 2^53 - 1."""
    try:
        v = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "NOT_UTF8"
    if any(c in CONTROLS for c in v):
        return "CONTROL_CHARACTER"
    if row in (0, 1):
        if v.strip().lower() in NULL_WORDS:
            return "TEXT_VALUE"
        if len(v) > (MAX_STRING, MAX_TEXT)[row]:
            return "TEXT_VALUE"
        if any(_noncharacter(c) for c in v):
            return "TEXT_VALUE"
        return None
    if row == 2:
        if v not in ("STRING", "NUMBER", "BOOLEAN"):
            return "DATATYPE_ROW"
        if col == 4 and v != "STRING":
            return "PREDEFINED_ATTRIBUTE"
        return None
    if not (re.fullmatch(r"-?[0-9]{1,17}", v) and -1 <= int(v) <= 2**53 - 1):
        return "PRIORITY_ROW"
    return None


def _noncharacter(c: str) -> bool:
    """Whether ``c`` is one of Unicode's 66 noncharacters."""
    code = ord(c)
    return 0xFDD0 <= code <= 0xFDEF or (code & 0xFFFE) == 0xFFFE


def expected_hdr2(text: str) -> str | None:
    """The plan's code for an ``hdr2`` cell (``None``: accepted): lines 1 to 4 start with ``#``
    (line 1 past one byte order mark), line 5 does not and is not empty; each header row past one
    ``#`` has line 5's width; then each field by row as ``expected_header`` reads it."""
    lines = re.split(r"\r\n|\n|\r", text)
    if lines and lines[-1] == "":
        lines.pop()
    width = len(lines[4].split("\t"))
    for i in range(4):
        s = lines[i][1:] if i == 0 and lines[i].startswith("\ufeff") else lines[i]
        if not s.startswith("#"):
            return "HEADER_ROWS"
    if lines[4].startswith("#") or lines[4] == "":
        return "HEADER_ROWS"
    rows: list[list[str]] = []
    for i in range(4):
        s = lines[i][1:] if i == 0 and lines[i].startswith("\ufeff") else lines[i]
        rows.append(s[1:].split("\t"))
        if len(rows[-1]) != width:
            return "HEADER_ROW_WIDTH"
    for i, fields in enumerate(rows):
        for v in fields:
            found = expected_header(i, 0, v.encode())
            if found is not None:
                return found
    return None


# --- The data cells (cells) -------------------------------------------------------------------

WHITESPACE = [chr(c) for c in range(0x110000) if chr(c).isspace() and c not in (9, 10, 13)]
CELL_PADS = [*WHITESPACE, "\ufeff", "\u200b"]
JAVA_NAMES = {
    "NOT_APPLICABLE",
    "NOT_AVAILABLE",
    "PENDING",
    "DISCREPANCY",
    "COMPLETED",
    "NULL",
    "MISSING",
    "NA",
}
ID_INVALID = r"[^A-Za-z0-9._()\[\]',+\-:;]"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
OS = ("LIVING", "DECEASED", "0:LIVING", "1:DECEASED")
DFS = (
    "0:DiseaseFree",
    "1:Recurred/Progressed",
    "1:Recurred",
    "1:Progressed",
    "DiseaseFree",
    "Recurred/Progressed",
    "Recurred",
    "Progressed",
)
CELL_DOMAINS: Mapping[str, tuple[str, str | None, list[str], str]] = {
    "cb_bool": (
        "bool",
        "FLAG",
        ["TRUE", "FALSE", "NA", "", "[Not Available]", "Pending", "true", "[null]"],
        "BOOLEAN",
    ),
    "cb_os": (
        "os",
        "OS_STATUS",
        ["0:LIVING", "1:DECEASED", "LIVING", "NA", "", "Pending", "Dead"],
        "STRING",
    ),
    "cb_pfs": ("pfs", "PFS_STATUS", ["1:x", "0:y", "x", "NA", "", "1:"], "STRING"),
    "cb_dfs": ("dfs", "DFS_STATUS", ["Recurred", "1:Recurred"], "STRING"),
    "cb_pid": ("pid", None, ["P", "NA", "", "Mar-P"], "STRING"),
    "cb_sid": ("sid", None, ["S", "NA", "", "Mar-S"], "STRING"),
    "cb_excel": ("str", "SITE", ["Mar-1", "x"], "STRING"),
    "cb_excel_num": ("str", "AGE", ["Mar-1"], "NUMBER"),
}  # fmt: skip
"""Each cell domain: its kind, its column, its bare values and its datatype row."""


def _header(n: int, types: list[str]) -> list[str]:
    return [
        "#" + "\t".join(["L"] * n),
        "#" + "\t".join(["D"] * n),
        "#" + "\t".join(types),
        "#" + "\t".join(["1"] * n),
    ]


def _file(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


CELL_P = _file([*_header(2, ["STRING", "STRING"]), "PATIENT_ID\tSEX", "P1\tFemale", "P2\tMale"])
CELL_S = _file(
    [
        *_header(3, ["STRING", "STRING", "NUMBER"]),
        "PATIENT_ID\tSAMPLE_ID\tTMB",
        "P1\tS1\t1",
        "P2\tS2\t2",
    ]
)


def cells() -> dict[str, tuple[str, str, bytes, bytes]]:
    """Each cell: its domain, its value, and its patient and sample files, in the order the run
    made them (``<domain>:<index>``)."""
    found: dict[str, tuple[str, str, bytes, bytes]] = {}
    for tag, (kind, column, bases, datatype) in CELL_DOMAINS.items():
        values: list[str] = []
        for base in bases:
            values.append(base)
            for pad in CELL_PADS:
                values += [pad + base, base + pad]
        for i, v in enumerate(values):
            if kind == "pid":
                v = v.replace("P", f"P{i}x") if "P" in v else v
                lines = [*_header(2, ["STRING", "STRING"]), "PATIENT_ID\tSEX", f"{v}\tF"]
                found[f"{tag}:{i}"] = (tag, v, ("\n".join(lines) + "\n").encode(), CELL_S)
            elif kind == "sid":
                v = v.replace("S", f"S{i}x") if "S" in v else v
                lines = [*_header(2, ["STRING", "STRING"]), "PATIENT_ID\tSAMPLE_ID", f"P1\t{v}"]
                found[f"{tag}:{i}"] = (tag, v, CELL_P, ("\n".join(lines) + "\n").encode())
            else:
                assert column is not None
                extra = ["PFS_MONTHS"] if kind == "pfs" else []
                types = ["STRING", datatype] + ["NUMBER"] * len(extra)
                lines = _header(2 + len(extra), types)
                lines += ["\t".join(["PATIENT_ID", column, *extra])]
                lines += ["\t".join(["P0", v] + ["1"] * len(extra))]
                found[f"{tag}:{i}"] = (tag, v, ("\n".join(lines) + "\n").encode(), CELL_S)
    return found


EXCEL_TOKENS = ("Mar", "1", "-", ",", ".", ":", ";")
"""The tokens of ``excel``'s brute force: a month, a digit, the joiner and the four marks."""
EXCEL_NEIGHBOURS = ("!", "/", "_", "+", "'", " ", "\u00a0", "#", "(", ")")
"""Characters next to the four marks that the rule must not treat as marks."""


def excel() -> dict[str, tuple[str, str, bytes, bytes]]:
    """The Excel date class by brute force, each value in a ``STRING`` column of a file of its own
    (``cb_excel``'s column): every sequence of up to four of ``EXCEL_TOKENS`` with exactly one
    ``-`` (the only cells the rule can read as a date), and a month and a date beside each of
    ``EXCEL_NEIGHBOURS``, so that dropping or adding one of ``, . : ;`` changes a cell's outcome.
    The id is the value as ``ascii`` writes it."""
    values: list[str] = []
    for length in range(1, 5):
        for sequence in itertools.product(EXCEL_TOKENS, repeat=length):
            if sequence.count("-") == 1:
                values.append("".join(sequence))
    for n in EXCEL_NEIGHBOURS:
        values += [f"Mar-1{n}", f"Mar{n}-1", f"{n}Mar-1", f"Mar-{n}1", f"1-Mar{n}", f"{n}1-Mar"]
    found: dict[str, tuple[str, str, bytes, bytes]] = {}
    for v in dict.fromkeys(values):
        lines = [*_header(2, ["STRING", "STRING"]), "PATIENT_ID\tSITE", f"P0\t{v}"]
        found[ascii(v)] = ("cb_excel", v, _file(lines), CELL_S)
    return found


def java_has(c: str) -> bool:
    """Java's missing test after its trim, on a cell with no control character."""
    t = c.strip(" ")
    if c != "" and t == "":
        return True
    return t.replace("[", "").replace("]", "").replace("|", "").replace(" ", "_").upper() in (
        JAVA_NAMES
    )


def expected_cell(tag: str, c: str) -> str | None:
    """The plan's code for a data cell (``None``: accepted): the control characters, then an
    Excel date (Python's ``strip()``, a column without ``date``), then the column's own rule: a
    ``BOOLEAN``'s ``TRUE``/``FALSE`` after Java's trim, a status's values (``OS_STATUS``,
    ``DFS_STATUS``, a ``<X>_STATUS`` beside ``<X>_MONTHS`` prefixed ``0:`` or ``1:``), each
    passing a cell Java calls missing or an empty one; a key's null word, padding, characters and,
    for a sample id, length."""
    kind = CELL_DOMAINS[tag][0]
    column = CELL_DOMAINS[tag][1] or ("PATIENT_ID" if kind == "pid" else "SAMPLE_ID")
    if any(x in CONTROLS for x in c):
        return "CONTROL_CHARACTER"
    v = c.strip()
    if (
        "date" not in column.lower()
        and len(v.split("-")) == 2
        and not any(i in v for i in ",.:;")
        and any(x.lower() in MONTHS for x in v.split("-"))
    ):
        return "EXCEL_DATE"
    t = c.strip(" ")
    missing = c == "" or java_has(c)
    if kind == "bool":
        return None if missing or t in ("TRUE", "FALSE") else "BOOLEAN_VALUE"
    if kind == "os":
        return None if missing or t in OS else "SURVIVAL_STATUS"
    if kind == "dfs":
        return None if missing or t in DFS else "SURVIVAL_STATUS"
    if kind == "pfs":
        parts = t.lower().split(":")
        good = len(parts) >= 2 and parts[0] in ("0", "1")
        return None if missing or good else "SURVIVAL_STATUS"
    if kind in ("pid", "sid"):
        if c.strip().lower() in NULL_WORDS:
            return "KEY_MISSING"
        if c != c.strip(" "):
            return "PADDED_KEY"
        if re.search(ID_INVALID, c):
            return "KEY_CHARACTERS"
        if kind == "sid" and len(c) > 63:
            return "SAMPLE_ID_LENGTH"
    return None


# --- Whole studies (rv1) ----------------------------------------------------------------------


def clinical(
    names: list[str],
    rows: list[list[str]],
    label: list[str] | None = None,
    defs: list[str] | None = None,
    types: list[str] | None = None,
    prio: list[str] | None = None,
    eol: str = "\n",
) -> bytes:
    n = len(names)
    lines = [
        "#" + "\t".join(label or ["L"] * n),
        "#" + "\t".join(defs or ["D"] * n),
        "#" + "\t".join(types or ["STRING"] * n),
        "#" + "\t".join(prio or ["1"] * n),
        "\t".join(names),
    ] + ["\t".join(r) for r in rows]
    return (eol.join(lines) + eol).encode()


Rows = list[list[str]]


def patient(
    extra: list[str] | None = None,
    rows: Rows | None = None,
    *,
    label: list[str] | None = None,
    defs: list[str] | None = None,
    types: list[str] | None = None,
    prio: list[str] | None = None,
) -> bytes:
    extra = extra or []
    rows = rows or [["P1", *["x"] * len(extra)], ["P2", *["x"] * len(extra)]]
    return clinical(["PATIENT_ID", *extra], rows, label, defs, types, prio)


def sample(
    extra: list[str] | None = None, rows: Rows | None = None, *, types: list[str] | None = None
) -> bytes:
    extra = extra or []
    rows = rows or [["P1", "S1", *["x"] * len(extra)], ["P2", "S2", *["x"] * len(extra)]]
    return clinical(["PATIENT_ID", "SAMPLE_ID", *extra], rows, types=types)


GOOD_P = patient(["SEX"], rows=[["P1", "Female"], ["P2", "Male"]])
NUMBER_S = ["STRING", "STRING", "NUMBER"]
RV1_S = sample(["TMB"], rows=[["P1", "S1", "1"], ["P2", "S2", "2"]], types=NUMBER_S)


class Whole(NamedTuple):
    """A whole study of the rv1 kind: its patient and sample files, the sample file's name (which
    the sample meta names) and the import's limits where they are not the defaults."""

    patient: bytes
    sample: bytes
    sample_name: str = "data_clinical_sample.txt"
    limits: Mapping[str, int] | None = None


SAMPLE_NAME = "data_clinical_sample.txt"


def rv1() -> dict[str, Whole]:
    """Each whole study: the plan's round-1 studies (``00`` to ``83``), then the code round's
    (``85`` to ``99``), and one study read under a lower limit (``00_control@cells``, whose
    reference outcome is ``00_control``'s: the limit is the operator's, not the study's)."""
    found: dict[str, Whole] = {}

    def case(
        name: str,
        p: bytes = GOOD_P,
        s: bytes = RV1_S,
        sample_name: str = SAMPLE_NAME,
        limits: Mapping[str, int] | None = None,
    ) -> None:
        found[name] = Whole(p, s, sample_name, limits)

    case("00_control")
    case(
        "01_dup_attr_patient", p=patient(["SEX", "SEX"], rows=[["P1", "F", "F"], ["P2", "M", "M"]])
    )
    case(
        "02_dup_attr_sample",
        s=sample(["TMB", "TMB"], rows=[["P1", "S1", "1", "1"], ["P2", "S2", "2", "2"]]),
    )
    case(
        "03_dup_key_col_sample",
        s=clinical(
            ["PATIENT_ID", "SAMPLE_ID", "SAMPLE_ID"], [["P1", "S1", "S1"], ["P2", "S2", "S2"]]
        ),
    )
    case("04_datatype_double_hash", p=GOOD_P.replace(b"#STRING\tSTRING\n", b"##STRING\tSTRING\n"))
    case("05_priority_double_hash", p=GOOD_P.replace(b"\n#1\t1\n", b"\n##1\t1\n"))
    case("06_label_double_hash", p=GOOD_P.replace(b"#L\tL\n", b"##L\tL\n", 1))
    case(
        "07_label_only_hashes",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], label=["#", "L"]),
    )
    case("08_repeated_header_sample", s=RV1_S + b"PATIENT_ID\tSAMPLE_ID\tTMB\n")
    case("09_bom_sample", s=b"\xef\xbb\xbf" + RV1_S)
    case("10_mixed_eol_patient", p=GOOD_P.replace(b"\n", b"\r\n", 2))
    case("11_cr_only_patient", p=GOOD_P.replace(b"\n", b"\r"))
    case("12_trailing_blank_line", p=GOOD_P + b"\n")
    case("13_nbsp_only_line", p=GOOD_P + "\xa0\n".encode())
    case("14_tab_hash_line", p=GOOD_P + b"\t#x\n")
    case("15_space_hash_line", p=GOOD_P + b" #x\tF\n")
    case("16_label_NA", p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], label=["L", "NA"]))
    case(
        "17_def_ideographic_space",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], defs=["D", "\u3000"]),
    )
    case(
        "18_label_Pending",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], label=["L", "Pending"]),
    )
    booleans = [
        ("TRUE_sp", "TRUE "),
        ("true", "true"),
        ("Yes", "Yes"),
        ("NA", "NA"),
        ("Pending", "Pending"),
        ("TRUE_nbsp", "TRUE\xa0"),
        ("NA_nbsp", "NA\xa0"),
        ("ideo", "\u3000"),
    ]
    for tag, cell in booleans:
        case(
            f"19_bool_{tag}",
            p=patient(["FLAG"], rows=[["P1", cell], ["P2", "FALSE"]], types=["STRING", "BOOLEAN"]),
        )
    for tag, cell in [
        ("Pending", "Pending"),
        ("Dead", "Dead"),
        ("NA_nbsp", "NA\xa0"),
        ("pad", "1:DECEASED "),
        ("NotAvail", "Not Available"),
    ]:
        case(f"20_os_{tag}", p=patient(["OS_STATUS"], rows=[["P1", cell], ["P2", "0:LIVING"]]))
    case(
        "21_pfs_x_patient",
        p=patient(
            ["PFS_STATUS", "PFS_MONTHS"],
            rows=[["P1", "x", "1"], ["P2", "1:Yes", "2"]],
            types=NUMBER_S,
        ),
    )
    case(
        "22_pfs_x_sample",
        s=sample(
            ["PFS_STATUS", "PFS_MONTHS"],
            rows=[["P1", "S1", "x", "1"], ["P2", "S2", "1:Y", "2"]],
            types=["STRING", "STRING", "STRING", "NUMBER"],
        ),
    )
    case(
        "23_pfs_1colon_patient",
        p=patient(
            ["PFS_STATUS", "PFS_MONTHS"],
            rows=[["P1", "1:", "1"], ["P2", "0:a:b", "2"]],
            types=NUMBER_S,
        ),
    )
    for tag, cell in [
        ("NA", "NA"),
        ("Pending", "Pending"),
        ("null", "null"),
        ("hash", "P#1"),
        ("pad", "P1 "),
        ("nbsp", "P1\xa0"),
        ("excel", "Mar-1"),
    ]:
        case(
            f"24_pkey_{tag}",
            p=patient(["SEX"], rows=[[cell, "F"], ["P2", "M"]]),
            s=sample(["TMB"], rows=[[cell, "S1", "1"], ["P2", "S2", "2"]], types=NUMBER_S),
        )
    case(
        "25_sid_64",
        s=sample(["TMB"], rows=[["P1", "S" * 64, "1"], ["P2", "S2", "2"]], types=NUMBER_S),
    )
    case(
        "26_sid_63",
        s=sample(["TMB"], rows=[["P1", "S" * 63, "1"], ["P2", "S2", "2"]], types=NUMBER_S),
    )
    case("27_excel_string", p=patient(["SITE"], rows=[["P1", "Mar-1"], ["P2", "x"]]))
    case("28_excel_date_col", p=patient(["DATE_X"], rows=[["P1", "Mar-1"], ["P2", "x"]]))
    case(
        "29_excel_number",
        p=patient(["AGE"], rows=[["P1", "Mar-1"], ["P2", "3"]], types=["STRING", "NUMBER"]),
    )
    case("30_excel_padded", p=patient(["SITE"], rows=[["P1", " Mar-1"], ["P2", "x"]]))
    for tag, v in [
        ("space1", " 1"),
        ("plus1", "+1"),
        ("under", "1_0"),
        ("m2", "-2"),
        ("big", "9007199254740993"),
        ("arabic", "\u0663"),
    ]:
        case(f"31_prio_{tag}", p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], prio=["1", v]))
    case(
        "32_type_lower",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], types=["STRING", "string"]),
    )
    case(
        "33_key_type_number",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], types=["NUMBER", "STRING"]),
    )
    case("34_os_months_string", p=patient(["OS_MONTHS"], rows=[["P1", "1"], ["P2", "2"]]))
    case(
        "35_sample_type_in_patient",
        p=patient(["SAMPLE_TYPE"], rows=[["P1", "Primary"], ["P2", "Primary"]]),
    )
    case(
        "36_mutation_count",
        s=sample(["MUTATION_COUNT"], rows=[["P1", "S1", "1"], ["P2", "S2", "2"]], types=NUMBER_S),
    )
    case("37_attr_both", s=sample(["SEX"], rows=[["P1", "S1", "F"], ["P2", "S2", "M"]]))
    case("38_name_lower", p=patient(["Sex"], rows=[["P1", "F"], ["P2", "M"]]))
    case("39_name_digits", p=patient(["123"], rows=[["P1", "F"], ["P2", "M"]]))
    case("40_name_trailing_space", p=patient(["SEX "], rows=[["P1", "F"], ["P2", "M"]]))
    case("41_field_131072", p=patient(["NOTE"], rows=[["P1", "a" * 131072], ["P2", "b"]]))
    case("42_field_131073", p=patient(["NOTE"], rows=[["P1", "a" * 131073], ["P2", "b"]]))
    for tag, v in [
        ("gt", ">5"),
        ("under", "1_0"),
        ("inf", "inf"),
        ("nan", "nan"),
        ("arabic", "\u0663"),
        ("NotAvail", "Not Available"),
        ("hex", "0x10"),
    ]:
        case(
            f"43_num_{tag}",
            s=sample(["TMB"], rows=[["P1", "S1", v], ["P2", "S2", "2"]], types=NUMBER_S),
        )
    case("44_row_width_extra_tab", p=GOOD_P + b"P3\tF\t\n")
    case(
        "45_dangling_sample",
        s=sample(["TMB"], rows=[["P1", "S1", "1"], ["P9", "S2", "2"]], types=NUMBER_S),
    )
    case(
        "46_tcga_dup",
        s=sample(["TMB"], rows=[["P1", "TCGA-1", "1"], ["P2", "TCGA-1", "2"]], types=NUMBER_S),
    )
    case("47_no_data_rows_patient", p=clinical(["PATIENT_ID", "SEX"], []))
    case("48_quote_cells", p=patient(["SEX"], rows=[["P1", '"F"'], ["P2", '"M"']]))
    case("49_u2028_cell", p=patient(["SEX"], rows=[["P1", "F\u2028x"], ["P2", "M"]]))
    case("50_u0085_cell", p=patient(["SEX"], rows=[["P1", "F\u0085x"], ["P2", "M"]]))
    case("51_five_hash_rows", p=b"#x\t#x\n" + GOOD_P)
    case("52_formfeed_cell", p=patient(["SEX"], rows=[["P1", "F\x0cx"], ["P2", "M"]]))
    case("53_crcrlf", p=GOOD_P.replace(b"\nP2", b"\r\r\nP2"))
    case("54_empty_name", p=patient([""], rows=[["P1", "F"], ["P2", "M"]]))
    case("55_repeated_header_patient_padded", p=GOOD_P + b"PATIENT_ID\tSEX\textra\n")
    case(
        "60_sid_notavail",
        s=sample(["TMB"], rows=[["P1", "[Not Available]", "1"], ["P2", "S2", "2"]], types=NUMBER_S),
    )
    case("61_no_sample_id_col", s=clinical(["PATIENT_ID", "TMB"], [["P1", "1"], ["P2", "2"]]))
    case(
        "62_sample_id_in_patient",
        p=clinical(["PATIENT_ID", "SAMPLE_ID"], [["P1", "S1"], ["P2", "S2"]]),
    )
    case("63_sample_zero_rows", s=clinical(["PATIENT_ID", "SAMPLE_ID"], []))
    case(
        "64_os_LIVING_with_months",
        p=patient(
            ["OS_STATUS", "OS_MONTHS"],
            rows=[["P1", "LIVING", "1"], ["P2", "DECEASED", "2"]],
            types=NUMBER_S,
        ),
    )
    case(
        "65_dfs_padded",
        p=patient(["DFS_STATUS"], rows=[["P1", "Recurred "], ["P2", "DiseaseFree"]]),
    )
    case("66_row3_short", p=GOOD_P.replace(b"#STRING\tSTRING\n", b"#STRING\n"))
    case("67_nonascii_upper_name", p=patient(["\xc4GE"], rows=[["P1", "1"], ["P2", "2"]]))
    case(
        "68_tabs_short_line",
        p=clinical(["PATIENT_ID", "SEX", "AGE"], [["P1", "F", "1"], ["", ""], ["P2", "M", "2"]]),
    )
    case("69_key_name_padded", p=clinical(["PATIENT_ID ", "SEX"], [["P1", "F"], ["P2", "M"]]))
    case("70_hash_inside_name", p=patient(["#SEX"], rows=[["P1", "F"], ["P2", "M"]]))
    case("71_second_bom_in_cell", p=patient(["SEX"], rows=[["P1", "\ufeffF"], ["P2", "M"]]))
    case(
        "72_type_key_bad",
        p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], types=["string", "STRING"]),
    )
    case("73_prio_key_bad", p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], prio=["x", "1"]))
    case("74_label_key_NA", p=patient(["SEX"], rows=[["P1", "F"], ["P2", "M"]], label=["NA", "L"]))
    case(
        "75_num_padded_nbsp",
        s=sample(["TMB"], rows=[["P1", "S1", "1\xa0"], ["P2", "S2", "2"]], types=NUMBER_S),
    )
    case(
        "76_pid_sample_excel_notdate",
        s=sample(["TMB"], rows=[["P1", "S1", "1"], ["P2", "jan-feb", "2"]], types=NUMBER_S),
    )
    case("80_not_utf8_cell", p=GOOD_P.replace(b"Female", b"F\xe9male"))
    case("81_comment_line", p=GOOD_P + b"#P3\tF\n")
    case("82_not_utf8_label", p=GOOD_P.replace(b"#L\tL", b"#L\t\xff", 1))
    case("83_surrogate_bytes", p=GOOD_P.replace(b"Female", b"F\xed\xa0\x80"))
    case("85_single_column_patient_na", p=clinical(["PATIENT_ID"], [["P1"], ["NA"]]))
    case(
        "86_space_hash_nonkey_first",
        p=clinical(["SEX", "PATIENT_ID"], [["F", "P1"], [" #x", "P2"]]),
    )
    case(
        "86_repeated_header_prefix_field",
        p=clinical(["PATIENT_ID", "SEX"], [["P1", "F"], ["PATIENT_ID", "SEXY"]]),
    )
    case(
        "86_tab_hash_nonkey_second",
        p=clinical(
            ["SEX", "SITE", "PATIENT_ID"], [["F", "a", "P1"], ["M", "b", "P2"], ["", "#x", "P3"]]
        ),
    )
    case("87_name_nonchar", p=patient(["SEX\ufdd0"], rows=[["P1", "F"], ["P2", "M"]]))
    case("88_name_4097", p=patient(["A" * 4097], rows=[["P1", "F"], ["P2", "M"]]))
    case("89_noncharacter_data_file", sample_name="data_clinical_sample\ufffe.txt")
    pipes = ["N" + "|" * k + "A" for k in range(1, 66)]
    case(
        "90_missing_forms_65", p=patient(["NOTE"], rows=[[f"P{i}", v] for i, v in enumerate(pipes)])
    )
    case(
        "91_missing_forms_64",
        p=patient(["NOTE"], rows=[[f"P{i}", v] for i, v in enumerate(pipes[:64])]),
    )
    case(
        "92_missing_form_65_chars",
        p=patient(["NOTE"], rows=[["P1", "[" * 31 + "NA" + "]" * 32], ["P2", "x"]]),
    )
    case(
        "93_missing_form_64_chars",
        p=patient(["NOTE"], rows=[["P1", "[" * 31 + "NA" + "]" * 31], ["P2", "x"]]),
    )
    case(
        "94_attr_both_plain",
        p=patient(["FOO"], rows=[["P1", "a"], ["P2", "b"]]),
        s=sample(["FOO"], rows=[["P1", "S1", "a"], ["P2", "S2", "b"]]),
    )
    case("95_single_column_patient_dup", p=clinical(["PATIENT_ID"], [["P1"], ["P1"], ["P2"]]))
    case("96_patient_dup", p=patient(["SEX"], rows=[["P1", "F"], ["P1", "M"], ["P2", "M"]]))
    case(
        "97_sample_dup",
        s=sample(["TMB"], rows=[["P1", "S1", "1"], ["P2", "S1", "2"]], types=NUMBER_S),
    )
    case(
        "98_dfs_generic_shape",
        p=patient(
            ["DFS_STATUS", "DFS_MONTHS"],
            rows=[["P1", "DiseaseFree", "1"], ["P2", "Recurred", "2"]],
            types=NUMBER_S,
        ),
    )
    case(
        "99_many_columns",
        p=clinical(
            ["PATIENT_ID"] + [f"C{i}" for i in range(4096)],
            [["P1"] + ["x"] * 4096, ["P2"] + ["y"] * 4096],
        ),
    )
    case("00_control@cells", limits={"import_cells": 9})
    return found  # fmt: skip


__all__ = [
    "GOOD_S",
    "cells",
    "expected_cell",
    "expected_hdr2",
    "expected_header",
    "hdr2",
    "hdr3",
    "hdr4",
    "hdr4b",
    "header_file",
    "java_has",
    "rv1",
]
