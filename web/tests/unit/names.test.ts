/**
 * The operator's name in the browser (D262, D423), held to the server's own functions over the
 * whole domain: `tests/fixtures/operator-names.json` is what `server/scripts/
 * export_operator_names.py` writes from `valid_name`, `encode_operator` and `attribution` (a core
 * test fails while it is stale).
 *
 * - Every one-character name, U+0000 to U+10FFFF: `validName` refuses exactly the code points the
 *   server refuses (its ranges), and `percentEncode` gives exactly the server's header for every
 *   one but the surrogates (the SHA-256 of all of them, one per line).
 * - The boundary names (the 200 limit in code points, never UTF-16 units; secret shapes and near
 *   misses through three percent-decodings; controls, bidi formatting, noncharacters, lone
 *   surrogates): the server's verdict, and its header for each name the server accepts.
 * - The headers: the client never makes one the server refuses (lower-case hexadecimal, an
 *   unreserved character encoded, a raw reserved character), and makes exactly the one the server
 *   reads back for each it accepts.
 * - The oracle written from the definitions (D262, RFC 3986's unreserved set, UTF-8), over every
 *   code point, so that a change in the fixture and the code together still fails.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { NAME_LIMIT, operatorHeader, percentEncode, unquote, validName } from "../../src/api/curator";

interface NameVector {
  readonly why: string;
  readonly points: readonly number[];
  readonly valid: boolean;
  readonly header: string | null;
  readonly attributed: boolean;
}

interface HeaderVector {
  readonly header: string;
  readonly points: readonly number[] | null;
}

interface Vectors {
  readonly invalid: readonly (readonly [number, number])[];
  readonly headers_sha256: string;
  readonly names: readonly NameVector[];
  readonly headers: readonly HeaderVector[];
}

const VECTORS = JSON.parse(readFileSync(path.join(import.meta.dirname, "../fixtures/operator-names.json"), "utf8")) as Vectors;

const LAST = 0x10ffff;

/** A string of code points, a lone surrogate as itself. */
const text = (points: readonly number[]): string => points.map((point) => (point >= 0xd800 && point <= 0xdfff ? String.fromCharCode(point) : String.fromCodePoint(point))).join("");

/** The definition (D262, §5.1, RFC 7493): a code point no name may hold. */
function forbiddenByDefinition(point: number): boolean {
  const bidi = [0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069];
  return (
    point < 0x20 ||
    (point >= 0x7f && point < 0xa0) ||
    point === 0x2028 ||
    point === 0x2029 ||
    bidi.includes(point) ||
    (point >= 0xd800 && point < 0xe000) ||
    (point >= 0xfdd0 && point < 0xfdf0) ||
    (point & 0xfffe) === 0xfffe
  );
}

/** The definition (RFC 3986, D262): a code point's header, its UTF-8 bytes each `%XX` in
 * upper-case hexadecimal unless it is unreserved. */
function headerByDefinition(point: number): string {
  if (/^[A-Za-z0-9._~-]$/u.test(String.fromCodePoint(point))) {
    return String.fromCodePoint(point);
  }
  return [...new TextEncoder().encode(String.fromCodePoint(point))].map((byte) => `%${byte.toString(16).toUpperCase().padStart(2, "0")}`).join("");
}

describe("every one-character name, U+0000 to U+10FFFF", () => {
  it("is refused exactly where the server refuses it, and where the definition says", () => {
    const invalid = new Set<number>();
    for (const [first, last] of VECTORS.invalid) {
      for (let point = first; point <= last; point += 1) {
        invalid.add(point);
      }
    }
    expect(invalid.size).toBe(32 + 1 + 32 + 2 + 12 + 2048 + 32 + 34);
    const disagree: number[] = [];
    for (let point = 0; point <= LAST; point += 1) {
      const name = text([point]);
      const client = validName(name);
      if (client === invalid.has(point) || client === forbiddenByDefinition(point)) {
        disagree.push(point);
      }
    }
    expect(disagree.length).toBe(0);
  });

  it("is encoded exactly as the server encodes it (the digest of all but the surrogates), and as the definition says", () => {
    const digest = createHash("sha256");
    let differs = 0;
    for (let point = 0; point <= LAST; point += 1) {
      if (point >= 0xd800 && point <= 0xdfff) {
        if (percentEncode(String.fromCharCode(point)) !== null) {
          differs += 1;
        }
        continue;
      }
      const header = percentEncode(String.fromCodePoint(point)) ?? "";
      if (header !== headerByDefinition(point)) {
        differs += 1;
      }
      digest.update(`${header}\n`);
    }
    expect(differs).toBe(0);
    expect(digest.digest("hex")).toBe(VECTORS.headers_sha256);
  }, 120_000); // 1.1 million names: seconds alone, more on a loaded machine (the default is 5 s)
});

describe("the boundary names", () => {
  it("are as many as the generator writes, the review's verdicts among them", () => {
    const verdict = (why: string): boolean | undefined => VECTORS.names.find((entry) => entry.why === why)?.valid;
    expect(VECTORS.names.length).toBeGreaterThan(130);
    expect(verdict("200 astral characters (400 UTF-16 units)")).toBe(true);
    expect(verdict("201 astral characters")).toBe(false);
    expect(verdict("U+FFFD")).toBe(true);
    expect(verdict("a tag character")).toBe(true);
    expect(verdict("spaces only")).toBe(true);
    expect(verdict("a lone high surrogate")).toBe(false);
  });

  it.each(VECTORS.names.map((entry) => [entry.why, entry] as const))("%s: the server's verdict and header", (_why, entry) => {
    const name = text(entry.points);
    expect(validName(name)).toBe(entry.valid);
    expect(percentEncode(name)).toBe(entry.header);
    expect(operatorHeader(name)).toBe(entry.valid ? entry.header : null);
    if (entry.valid) {
      expect(entry.attributed).toBe(true);
    }
  });

  it("count the limit in code points, not UTF-16 units", () => {
    const astral = "\u{1F600}";
    expect(NAME_LIMIT).toBe(200);
    expect(validName(astral.repeat(200))).toBe(true);
    expect(astral.repeat(200).length).toBe(400);
    expect(validName(astral.repeat(201))).toBe(false);
    expect(validName(`${"a".repeat(199)}${astral}`)).toBe(true);
  });
});

describe("the headers", () => {
  it.each(VECTORS.headers.map((entry) => [entry.header, entry] as const))("%s: the client makes it only if the server reads it back", (_header, entry) => {
    if (entry.points !== null) {
      expect(operatorHeader(text(entry.points))).toBe(entry.header);
      return;
    }
    let decoded: string | null;
    try {
      decoded = decodeURIComponent(entry.header);
    } catch {
      decoded = null;
    }
    if (decoded !== null) {
      expect(operatorHeader(decoded)).not.toBe(entry.header);
    }
  });

  it("never holds lower-case hexadecimal or a raw character outside the unreserved set", () => {
    for (const entry of VECTORS.names) {
      const header = operatorHeader(text(entry.points));
      if (header !== null) {
        expect(header).toMatch(/^(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+$/u);
      }
    }
  });
});

describe("unquote is Python's", () => {
  it.each([
    ["no escape", "abc", "abc"],
    ["an escape", "a%41b", "aAb"],
    ["lower-case hex", "%c3%a9", "é"],
    ["a lone percent", "50%", "50%"],
    ["a malformed escape", "%zz%4", "%zz%4"],
    ["malformed UTF-8", "%FF", "�"],
    ["a truncated sequence", "%E2%82", "�"],
    ["a byte order mark kept", "%EF%BB%BFa", "﻿a"],
    ["non-ASCII kept as it is", "é%41", "éA"],
    ["runs split by non-ASCII", "%C3é%A9", "�é�"],
    ["an encoded percent, once", "%2541", "%41"],
  ])("%s", (_case, given, expected) => {
    expect(unquote(given)).toBe(expected);
  });
});
