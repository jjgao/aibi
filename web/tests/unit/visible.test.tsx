/**
 * The visible form (D312, D428): `visibleForm`'s rule against the server's list over every code
 * point, the engine's arm and the list's each proven by injection, the CR/LF rule, the runs, the cut
 * and its order, the escapes' spelling; and the same through `showText`'s rendered DOM (a
 * `showText` that bypassed the visible form fails here, not only `visibleForm`'s own tests).
 *
 * **Unicode versions.** The list is the server's (`UNIDATA_VERSION`, the Python that wrote
 * `schemas/visible-form.json`); the engine's arm is this engine's (`process.versions["unicode"]`). The
 * relation held: every code point the form escapes is the list's, or the engine's `Cc`/`Cf`/`Cs`
 * when the engine's Unicode is newer than the list's (a newer browser is safer, never laxer); every
 * code point of the list is escaped. Today (Python 3.12's 15.0, Node's 16.0) they are equal.
 */
import { describe, expect, it } from "vitest";

import { boxText } from "../../src/api/box";
import { showText } from "../../src/api/text";
import { ENGINE, visibleForm } from "../../src/api/visible";
import { CUT, KEPT, RAW_BY_D312, UNIDATA_VERSION, UNSAFE } from "../../src/api/visible-ranges";
import { FORM } from "../../src/layout/form";
import { render } from "./render";

const LAST = 0x10ffff;

/** A flag per code point of inclusive ranges. */
function flags(ranges: readonly (readonly [number, number])[]): Uint8Array {
  const found = new Uint8Array(LAST + 1);
  for (const [first, last] of ranges) {
    found.fill(1, first, last + 1);
  }
  return found;
}

const LISTED = flags(UNSAFE);
const KEPT_POINTS = new Set(KEPT);

/** One code point's escape, as the server writes it. */
function escape(point: number): string {
  const hex = point.toString(16);
  return point < 0x100 ? `\\x${hex.padStart(2, "0")}` : point < 0x10000 ? `\\u${hex.padStart(4, "0")}` : `\\U${hex.padStart(8, "0")}`;
}

/** Whether this engine's Unicode is the list's (major and minor: `16.0` is not `15.0.0`): the
 * comparisons that measure the list against the engine hold exactly only then; on a newer engine
 * they hold as D428 states the relation, which a newer `Cc`/`Cf`/`Cs` may widen (a newer engine
 * escapes more, never less) and no regeneration can fix, since the Python that writes the list is
 * pinned (round 1 of #117). */
function sameUnicode(engine: string = process.versions["unicode"] ?? "", list: string = UNIDATA_VERSION): boolean {
  const [a = "", b = ""] = engine.split(".");
  const [x = "", y = ""] = list.split(".");
  return a === x && b === y;
}

/** Whether this engine's Unicode is newer than the list's. */
function engineIsNewer(engine: string = process.versions["unicode"] ?? "0.0", list: string = UNIDATA_VERSION): boolean {
  const [a = 0, b = 0, c = 0] = engine.split(".").map((part) => Number(part));
  const [x = 0, y = 0, z = 0] = list.split(".").map((part) => Number(part));
  return a !== x ? a > x : b !== y ? b > y : c > z;
}

/** Over every code point, what the form escapes that the list does not hold (`extra`) and what the
 * list holds that the form does not escape (`missing`), for a list and an engine's arm. */
function relation(list: readonly (readonly [number, number])[], engine: RegExp): { readonly extra: number[]; readonly missing: number[] } {
  const listed = flags(list);
  const extra: number[] = [];
  const missing: number[] = [];
  for (let point = 0; point <= LAST; point += 1) {
    const { runs } = visibleForm(String.fromCodePoint(point), list, engine);
    const escaped = runs.length === 1 && runs[0]?.escaped === true;
    if (escaped && listed[point] !== 1) {
      extra.push(point);
    }
    if (!escaped && listed[point] === 1) {
      missing.push(point);
    }
  }
  return { extra, missing };
}

/** The relation D428 states between an engine's Unicode and a list's, as the problems found over
 * every code point (none when it holds), given what `relation` saw for the engine's arm: the list is escaped whole; what the form adds is the
 * engine's own `Cc`/`Cf`/`Cs` and no kept member; where major.minor are equal the form IS the list;
 * and only a newer engine adds (an older one cannot take away). */
function problems(engineVersion: string, listVersion: string, engine: RegExp, { extra, missing }: { readonly extra: number[]; readonly missing: number[] }): string[] {
  const found: string[] = [];
  if (missing.length > 0) {
    found.push("the list is not escaped whole");
  }
  if (extra.some((point) => !engine.test(String.fromCodePoint(point)) || KEPT_POINTS.has(point))) {
    found.push("the form adds what is neither the list's nor the engine's");
  }
  if (extra.length > 0 && sameUnicode(engineVersion, listVersion)) {
    found.push("equal versions: the form is not the list");
  }
  if (extra.length > 0 && !sameUnicode(engineVersion, listVersion) && !engineIsNewer(engineVersion, listVersion)) {
    found.push("an older engine adds");
  }
  return found;
}

describe(`the visible form over every code point (list: Unicode ${UNIDATA_VERSION}; engine: Unicode ${process.versions["unicode"] ?? "?"})`, () => {
  it("escapes exactly the server's list, but for the engine's newer Cc/Cf/Cs (none today), and keeps tab, LF, ZWNJ and ZWJ", () => {
    const extra: string[] = [];
    const missing: string[] = [];
    for (let point = 0; point <= LAST; point += 1) {
      const { runs } = visibleForm(String.fromCodePoint(point));
      const escaped = runs.length === 1 && runs[0]?.escaped === true;
      if (escaped && LISTED[point] !== 1) {
        extra.push(point.toString(16));
      }
      if (!escaped && LISTED[point] === 1) {
        missing.push(point.toString(16));
      }
      if (escaped) {
        expect(runs[0]?.text).toBe(escape(point));
      } else if (runs[0]?.text !== String.fromCodePoint(point)) {
        missing.push(`${point.toString(16)} changed`);
      }
    }
    expect(missing).toEqual([]);
    expect(extra.every((hex) => ENGINE.test(String.fromCodePoint(parseInt(hex, 16))))).toBe(true);
    expect(extra.length === 0 || engineIsNewer()).toBe(true);
    for (const point of KEPT) {
      expect(visibleForm(String.fromCodePoint(point)).runs).toEqual([{ escaped: false, text: String.fromCodePoint(point) }]);
    }
  });

  it("the engine's arm alone differs from the list at U+2028 and U+2029 and the kept members only (each arm proven by injection)", () => {
    const differ: number[] = [];
    for (let point = 0; point <= LAST; point += 1) {
      const engine = !KEPT_POINTS.has(point) && ENGINE.test(String.fromCodePoint(point));
      if (engine !== (LISTED[point] === 1)) {
        differ.push(point);
      }
    }
    // The list's U+2028 and U+2029 (`Zl`, `Zp`) are the engine's neither; anything else the engine
    // escapes and the list does not is a newer engine's `Cc`/`Cf`/`Cs`, never on the list's own.
    expect(differ.filter((point) => LISTED[point] === 1)).toEqual([0x2028, 0x2029]);
    const newer = differ.filter((point) => LISTED[point] !== 1);
    expect(newer.length === 0 || engineIsNewer()).toBe(true);
    if (sameUnicode()) {
      expect(newer).toEqual([]);
    }
    // A reduced list: the engine's arm escapes what the list no longer holds.
    expect(visibleForm("a\u202eb", []).runs).toEqual([
      { escaped: false, text: "a" },
      { escaped: true, text: "\\u202e" },
      { escaped: false, text: "b" },
    ]);
    expect(visibleForm("\u2028", []).runs).toEqual([{ escaped: false, text: "\u2028" }]);
    // A stub engine that matches nothing: the list's arm escapes on its own.
    const nothing = /(?!)/u;
    expect(visibleForm("\u2028\u202e\ud800", UNSAFE, nothing).runs).toEqual([{ escaped: true, text: "\\u2028\\u202e\\ud800" }]);
    expect(visibleForm("\u202e", [], nothing).runs).toEqual([{ escaped: false, text: "\u202e" }]);
    // Neither arm overrides a kept member.
    expect(visibleForm("\t\n\u200c\u200d", [[0, LAST]], /^[\s\S]$/u).runs).toEqual([{ escaped: false, text: "\t\n\u200c\u200d" }]);
  });

  it("states the relation between the engine's Unicode and the list's, and holds the real pair to it (m5 of round 2 of #117)", () => {
    const engine = process.versions["unicode"] ?? "";
    const seen = relation(UNSAFE, ENGINE);
    expect(problems(engine, UNIDATA_VERSION, ENGINE, seen)).toEqual([]);
    // The pair measured when D428 was written (Node 22 and 24: Unicode 16.0 against the list's 15.0.0): equal.
    const list: string = UNIDATA_VERSION;
    if (engine === "16.0" && list === "15.0.0") {
      expect(seen.extra).toEqual([]);
    }
  }, 60_000);

  it("the relation's own cells, whatever this engine's Unicode: the equal-version comparison is run on an engine of the list's version, and each way it can fail is seen", () => {
    // An engine whose classes are the list's own (written as a class of its ranges), of the list's version: equal, so the form IS the list.
    const own = new RegExp(`^[${UNSAFE.map(([first, last]) => `\\u{${first.toString(16)}}-\\u{${last.toString(16)}}`).join("")}]$`, "u");
    expect(problems("15.0", UNIDATA_VERSION, own, relation(UNSAFE, own))).toEqual([]);
    const wider = /^[\p{Cc}\p{Cf}\p{Cs}\u0378]$/u;
    const seen = relation(UNSAFE, wider);
    expect(seen.extra).toEqual([0x378]);
    expect(problems("15.0", UNIDATA_VERSION, wider, seen)).toEqual(["equal versions: the form is not the list"]);
    expect(problems("14.0", UNIDATA_VERSION, wider, seen)).toEqual(["an older engine adds"]);
    expect(problems("16.0", UNIDATA_VERSION, wider, seen)).toEqual([]);
    // The minor counts: a 15.1 engine is newer than the 15.0.0 list, not equal to it.
    expect(problems("15.1", UNIDATA_VERSION, wider, seen)).toEqual([]);
    expect(relation(UNSAFE, /(?!)/u).extra).toEqual([]);
  }, 60_000);

  it("through showText's rendered DOM: every code point, a bar between them, escaped exactly where the list says", async () => {
    const step = 0x10000;
    for (let start = 0; start <= LAST; start += step) {
      const points = Array.from({ length: step }, (_, index) => start + index);
      const text = points.map((point) => String.fromCodePoint(point)).join("|");
      const expected = points.map((point) => (LISTED[point] === 1 ? escape(point) : String.fromCodePoint(point))).join("|");
      const shown = await render(<FORM value="whole">{showText(boxText(text))}</FORM>);
      expect(shown.container.textContent === expected, `plane ${String(start / step)}`).toBe(true);
      const spans = [...shown.container.querySelectorAll("span.escape")].map((span) => span.textContent);
      expect(spans).toEqual(points.filter((point) => LISTED[point] === 1).map(escape));
      expect(shown.container.querySelectorAll("*").length).toBe(spans.length);
      shown.unmount();
    }
  });

  it("holds the O-2 record against the engine: the default-ignorable code points it leaves raw are the export's", () => {
    const di = /^\p{Default_Ignorable_Code_Point}$/u;
    const zs = /^\p{Zs}$/u;
    const assigned = /^\P{Cn}$/u;
    const raw: number[] = [];
    const spaces: number[] = [];
    for (let point = 0; point <= LAST; point += 1) {
      const character = String.fromCodePoint(point);
      const escaped = visibleForm(character).runs[0]?.escaped === true;
      if (di.test(character) && !escaped && !KEPT_POINTS.has(point)) {
        raw.push(point);
      }
      if (zs.test(character) && point !== 0x20) {
        spaces.push(point);
      }
    }
    const listed = (ranges: readonly (readonly [number, number])[]): number[] => ranges.flatMap(([first, last]) => Array.from({ length: last - first + 1 }, (_, index) => first + index));
    const exported = [...listed(RAW_BY_D312.default_ignorable_assigned), ...listed(RAW_BY_D312.default_ignorable_unassigned)].sort((a, b) => a - b);
    expect(exported.length).toBe(267 + 3769);
    expect(listed(RAW_BY_D312.space_separators).length).toBe(16);
    if (sameUnicode()) {
      // The export's own engine's Unicode: the record is exactly what this engine leaves raw.
      expect(raw).toEqual(exported);
      expect(raw.filter((point) => assigned.test(String.fromCodePoint(point))).length).toBe(267);
      expect(spaces).toEqual(listed(RAW_BY_D312.space_separators));
      expect(spaces.length).toBe(16);
    } else {
      // Another engine's Unicode (a newer one): each point of the record is still raw but where the engine
      // has made it a `Cf` (a newer engine escapes more, never less).
      expect(engineIsNewer()).toBe(true);
      const escapedNow = exported.filter((point) => visibleForm(String.fromCodePoint(point)).runs[0]?.escaped === true);
      expect(escapedNow.every((point) => ENGINE.test(String.fromCodePoint(point)))).toBe(true);
    }
    expect(visibleForm("\u2800").runs).toEqual([{ escaped: false, text: "\u2800" }]);
  });
});

describe("the rules", () => {
  it("drops a carriage return before a line feed, and only there", () => {
    expect(visibleForm("a\r\nb").runs).toEqual([{ escaped: false, text: "a\nb" }]);
    expect(visibleForm("a\rb").runs).toEqual([
      { escaped: false, text: "a" },
      { escaped: true, text: "\\x0d" },
      { escaped: false, text: "b" },
    ]);
    expect(visibleForm("\r\r\n").runs).toEqual([
      { escaped: true, text: "\\x0d" },
      { escaped: false, text: "\n" },
    ]);
    expect(visibleForm("a\n\rb").runs).toEqual([
      { escaped: false, text: "a\n" },
      { escaped: true, text: "\\x0d" },
      { escaped: false, text: "b" },
    ]);
  });

  it("writes one run per run of unsafe code points, never one per code point", () => {
    expect(visibleForm("a\u202e\u202d\u0007b\u2066c").runs).toEqual([
      { escaped: false, text: "a" },
      { escaped: true, text: "\\u202e\\u202d\\x07" },
      { escaped: false, text: "b" },
      { escaped: true, text: "\\u2066" },
      { escaped: false, text: "c" },
    ]);
    expect(visibleForm("\u202e".repeat(10)).runs).toHaveLength(1);
  });

  it("spells each escape in lower-case hex, an astral one as \\U and eight digits, a lone surrogate as itself", () => {
    expect(visibleForm("\u0000\u001f\u007f\u009f\u00ad\u061c\u202e\ufeff\udfff\ud800\u{e0001}\u{1d173}").runs).toEqual([
      { escaped: true, text: "\\x00\\x1f\\x7f\\x9f\\xad\\u061c\\u202e\\ufeff\\udfff\\ud800\\U000e0001\\U0001d173" },
    ]);
  });

  it("cuts at 2,000 code points, before folding and escaping, never splitting a pair, and says so (the vectors of the server page's `test_page.py`, parallel examples: D428)", () => {
    expect(CUT).toBe(2000);
    expect(visibleForm("x".repeat(2000)).truncated).toBe(false);
    const cut = visibleForm("x".repeat(2001));
    expect(cut.truncated).toBe(true);
    expect(cut.runs).toEqual([{ escaped: false, text: "x".repeat(2000) }]);
    // By code point: 1,999 letters and an emoji are 2,000; the pair is kept whole.
    expect(visibleForm(`${"x".repeat(1999)}\u{1f600}z`).runs).toEqual([{ escaped: false, text: `${"x".repeat(1999)}\u{1f600}` }]);
    expect(visibleForm("\u{1f600}".repeat(2001)).runs).toEqual([{ escaped: false, text: "\u{1f600}".repeat(2000) }]);
    // Before folding: a CRLF whose LF is the 2,001st code point leaves the CR, escaped.
    const split = visibleForm(`${"x".repeat(1999)}\r\n`);
    expect(split.truncated).toBe(true);
    expect(split.runs.at(-1)).toEqual({ escaped: true, text: "\\x0d" });
    // A CRLF inside the cut folds; the fold does not let a 2,001st code point in.
    const folded = visibleForm(`${"x".repeat(1998)}\r\ny`);
    expect(folded.truncated).toBe(true);
    expect(folded.runs).toEqual([{ escaped: false, text: `${"x".repeat(1998)}\n` }]);
    // Before escaping: 2,000 overrides are kept, each escaped (12,000 characters), the rest cut.
    const overrides = visibleForm("\u202e".repeat(2500));
    expect(overrides.truncated).toBe(true);
    expect(overrides.runs).toEqual([{ escaped: true, text: "\\u202e".repeat(2000) }]);
  });

  it("does not cut a position that is not cut (cut null)", () => {
    const whole = visibleForm("x".repeat(5000), UNSAFE, ENGINE, null);
    expect(whole.truncated).toBe(false);
    expect(whole.runs).toEqual([{ escaped: false, text: "x".repeat(5000) }]);
  });

  it("is frozen", () => {
    const found = visibleForm("a\u202e");
    expect(Object.isFrozen(found)).toBe(true);
    expect(Object.isFrozen(found.runs)).toBe(true);
    expect(found.runs.every((run) => Object.isFrozen(run))).toBe(true);
  });
});
