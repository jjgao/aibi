"""Operator names as the server reads them, for the web client's name rules (D262, D423).

The browser's operator client refuses a name the server would refuse and encodes the
``Aibi-Operator`` header as the server's ``encode_operator`` does (``web/src/api/curator.ts``:
``validName``, ``percentEncode``, ``operatorHeader``), so that it never sends a name the server
refuses, and never a token pasted where the name goes. This script writes what the server's own
functions say to ``web/tests/fixtures/operator-names.json``, which the web package's tests hold the
client to:

- ``invalid``: every code point ``c`` from U+0000 to U+10FFFF for which ``valid_name(chr(c))`` is
  false, as ranges (the whole domain of one-character names);
- ``headers_sha256``: the SHA-256 of ``encode_operator(chr(c))`` for every code point but the
  surrogates (which no header can carry), one per line: the whole domain of the encoding;
- ``names``: names of more than one character at the boundaries (the 200-character limit in code
  points and in UTF-16 units, every secret shape the server refuses and the near misses it
  accepts, percent-decoding up to three times, controls, bidi formatting, noncharacters, lone
  surrogates), each with ``valid_name``'s verdict and ``encode_operator``'s header (``null`` for a
  name holding a surrogate, which UTF-8 cannot encode);
- ``headers``: header values with what ``attribution`` makes of them (the name, or ``null``):
  lower-case hexadecimal, an unreserved character encoded, a raw reserved character, malformed
  UTF-8.

A name is written as its code points, never as text: a JSON string cannot tell a lone surrogate
from half of a pair the way both languages read it. A name holding a high surrogate followed by a
low one is left out, since JavaScript reads that pair as one character and Python as two.

The file is checked in; ``tests/core/operator/test_operator_names.py`` fails while it differs
from what the server's functions give now, so a change to the server's name rules reaches the
client's tests in the same change. Run it from ``server/`` (``--check`` writes nothing and exits 1
while the file is stale):

    uv run python scripts/export_operator_names.py
    uv run python scripts/export_operator_names.py --check
"""

import hashlib
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from aibi.core.operator.auth import attribution, encode_operator, valid_name

SERVER = Path(__file__).resolve().parents[1]
TARGET = SERVER.parent / "web" / "tests" / "fixtures" / "operator-names.json"
LAST = 0x10FFFF
TAIL = "AbC-_9" * 7 + "z"
"""43 base64url characters: what follows ``aibi_`` or ``ses_`` in a token or a handle."""
ASTRAL = "\U0001f600"
BIDI = (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))


def _surrogate(point: int) -> bool:
    return 0xD800 <= point <= 0xDFFF


def invalid_ranges() -> list[list[int]]:
    """The code points ``valid_name`` refuses as a one-character name, as ``[first, last]``."""
    ranges: list[list[int]] = []
    for point in range(LAST + 1):
        if valid_name(chr(point)):
            continue
        if ranges and ranges[-1][1] == point - 1:
            ranges[-1][1] = point
        else:
            ranges.append([point, point])
    return ranges


def headers_sha256() -> str:
    """The SHA-256 of every non-surrogate code point's header, one per line, in order."""
    digest = hashlib.sha256()
    for point in range(LAST + 1):
        if not _surrogate(point):
            digest.update(encode_operator(chr(point)).encode("ascii") + b"\n")
    return digest.hexdigest()


def _names() -> list[tuple[str, str]]:
    token = "aibi_" + TAIL
    handle = "ses_" + TAIL
    found: list[tuple[str, str]] = [
        ("a name with a space", "Ada Lovelace"),
        ("one character", "a"),
        ("empty", ""),
        ("199 ASCII characters", "a" * 199),
        ("200 ASCII characters", "a" * 200),
        ("201 ASCII characters", "a" * 201),
        ("199 astral characters", ASTRAL * 199),
        ("200 astral characters (400 UTF-16 units)", ASTRAL * 200),
        ("201 astral characters", ASTRAL * 201),
        ("199 ASCII and one astral: 200 code points, 201 UTF-16 units", "a" * 199 + ASTRAL),
        ("200 ASCII and one astral", "a" * 200 + ASTRAL),
        ("200 two-byte characters", (chr(0x00E9)) * 200),
        ("201 two-byte characters", (chr(0x00E9)) * 201),
        ("200 three-byte characters", (chr(0x4E2D)) * 200),
        ("the unreserved characters", "AZaz09-._~"),
        ("the characters encodeURIComponent leaves", "!'()*"),
        ("an exclamation mark", "Ada!"),
        ("an apostrophe", "O'Neil"),
        ("parentheses", "Ada (curator)"),
        ("an asterisk", "a*b"),
        ("reserved characters", "a/b?c#d&e=f+g;h,i:j@k$l"),
        ("spaces only", "   "),
        ("a tab inside", "a\tb"),
        ("a percent sign", "50%"),
        ("a percent-encoded letter", "%41"),
        ("lower-case percent-encoding", "%c3%a9"),
        ("a percent sign alone", "%"),
        ("a malformed escape", "%zz"),
        ("U+FFFD", (chr(0xFFFD))),
        ("a zero-width joiner", ("a" + chr(0x200D) + "b")),
        ("a tag character", "\U000e0041"),
        ("a private use character", (chr(0xE000))),
        ("a no-break space", (chr(0x00A0))),
        ("a byte order mark", (chr(0xFEFF) + "a")),
        ("a lone high surrogate", "\ud800"),
        ("a lone low surrogate", "\udc00"),
        ("a lone surrogate inside", "a\ud800b"),
        ("a reversed pair", "\udc00\ud800"),
        ("a lone high surrogate at the end", "ab\udbff"),
        ("a token alone", token),
        ("a handle alone", handle),
        ("a token inside a longer word, before", "x" + token),
        ("a token inside a longer word, after", token + "A"),
        ("a token one character short", token[:-1]),
        ("a token between spaces", "x " + token + " y"),
        ("a token before a dot", token + "."),
        ("a token after a non-ASCII letter", (chr(0x00E9)) + token),
        ("a handle after a hyphen", "-" + handle),
        ("a handle after a dot", "." + handle),
        ("a token percent-encoded once", "aibi%5F" + TAIL),
        ("a token percent-encoded twice", "aibi%255F" + TAIL),
        ("a token percent-encoded three times", "aibi%25255F" + TAIL),
        ("a token percent-encoded four times", "aibi%2525255F" + TAIL),
        ("a handle percent-encoded once", "ses%5F" + TAIL),
        ("a token's letter percent-encoded", "%61ibi_" + TAIL),
        ("a token's tail percent-encoded", "aibi_%41" + TAIL[1:]),
        ("a token after an encoded letter", "%41" + token),
        ("a token after an encoded hyphen", "%2D" + token),
        ("a token after an encoded dot", "%2E" + token),
        ("a token after an encoded byte order mark", "x%EF%BB%BF" + token),
        ("a token after malformed UTF-8", "%FF" + token),
        ("a token after an encoded space, lower-case", "%20" + token),
        ("a token with an encoded non-ASCII letter before it", "%C3%A9" + token),
        ("a token split by a non-ASCII letter", ("aibi%5F" + chr(0x00E9)) + TAIL),
        ("U+2028 inside", ("a" + chr(0x2028) + "b")),
        ("U+2029 inside", ("a" + chr(0x2029) + "b")),
        ("DEL inside", "a\x7fb"),
        ("U+00A0 after C1", ("a" + chr(0x00A0))),
        ("noncharacter U+FDD0", ("a" + chr(0xFDD0))),
        ("noncharacter U+FDEF", ("a" + chr(0xFDEF))),
        ("U+FDCF, no noncharacter", ("a" + chr(0xFDCF))),
        ("U+FDF0, no noncharacter", ("a" + chr(0xFDF0))),
        ("noncharacter U+FFFE", ("a" + chr(0xFFFE))),
        ("noncharacter U+FFFF", ("a" + chr(0xFFFF))),
        ("noncharacter U+1FFFE", "a\U0001fffe"),
        ("noncharacter U+10FFFF", "a\U0010ffff"),
        ("U+10FFFD, no noncharacter", "a\U0010fffd"),
    ]
    found += [(f"C0 control U+{point:04X} inside", f"a{chr(point)}b") for point in range(0x20)]
    found += [
        (f"C1 control U+{point:04X} inside", f"a{chr(point)}b") for point in range(0x80, 0xA0)
    ]
    found += [(f"bidi formatting U+{point:04X} inside", f"a{chr(point)}b") for point in BIDI]
    return found


def _pair_inside(name: str) -> bool:
    return any(
        0xD800 <= ord(first) <= 0xDBFF and 0xDC00 <= ord(second) <= 0xDFFF
        for first, second in itertools.pairwise(name)
    )


def names() -> list[dict[str, Any]]:
    """Each boundary name: its code points, ``valid_name``'s verdict, ``encode_operator``'s header
    (``None`` for a name UTF-8 cannot encode) and whether ``attribution`` reads that header back
    as the name."""
    found: list[dict[str, Any]] = []
    for why, name in _names():
        if _pair_inside(name):
            raise ValueError(f"{why}: a surrogate pair reads differently in the two languages")
        try:
            header: str | None = encode_operator(name)
        except UnicodeEncodeError:
            header = None
        read = None if header is None else attribution(header)
        found.append(
            {
                "why": why,
                "points": [ord(character) for character in name],
                "valid": valid_name(name),
                "header": header,
                "attributed": read == f"operator:{name}",
            }
        )
    return found


HEADERS = [
    "Ada",
    "%41da",
    "%c3%a9",
    "%C3%A9",
    "Ada!",
    "%21",
    "a%20b",
    "a+b",
    "a%2Bb",
    "%E9",
    "%ED%A0%80",
    "%00",
    "%7F",
    "%C2%85",
    "%E2%80%AE",
    "%EF%BF%BD",
    "%F0%9F%98%80",
    "%7e",
    "%7E",
    "~",
    "a%2",
    "%",
]


def headers() -> list[dict[str, Any]]:
    """Each header value, with the name ``attribution`` reads from it, or ``None``."""
    found: list[dict[str, Any]] = []
    for header in HEADERS:
        read = attribution(header)
        name = None if read is None else read.removeprefix("operator:")
        found.append({"header": header, "points": None if name is None else [ord(c) for c in name]})
    return found


def render() -> str:
    """The file's text: compact, ASCII, one entry a line."""
    lines = [
        "{",
        f'"invalid": {json.dumps(invalid_ranges(), separators=(",", ":"))},',
        f'"headers_sha256": {json.dumps(headers_sha256())},',
        '"names": [',
        ",\n".join(json.dumps(entry, separators=(",", ":")) for entry in names()),
        "],",
        '"headers": [',
        ",\n".join(json.dumps(entry, separators=(",", ":")) for entry in headers()),
        "]",
        "}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("usage: export_operator_names.py [--check]", file=sys.stderr)
        return 2
    text = render()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
    if argv == ["--check"]:
        if current != text:
            print(f"{TARGET} is stale: run scripts/export_operator_names.py", file=sys.stderr)
            return 1
        return 0
    if current != text:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(text, encoding="utf-8")
        print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
