/**
 * The injection harness (D428): a corpus of hostile texts, boxed through the tests' door (`boxText`,
 * as the walk boxes a response's strings), rendered in a position (a function from a box to an
 * element, the screen PR's own), and each rendering asserted to show the text as text: no element
 * the text spelled (an `<img>`, a `<b>`, a `</bdi>` closing the isolate), only the elements the
 * visible form makes (`bdi`, the escapes' and the cut's `span`s, and the position's own), no raw
 * unsafe code point in any text node, and the code points D312 leaves raw ("raw by D312": the
 * default-ignorable code points that are neither a control nor `Cf`, assigned and unassigned, the
 * space separators but U+0020, U+2800) rendered raw, so that a change to D312 shows up as a change
 * here. Each screen PR runs `injectionProblems` over its positions, as part of its D427 rows.
 */
import { type ReactElement } from "react";

import { boxText, type ServerText } from "../../src/api/box";
import { ENGINE } from "../../src/api/visible";
import { KEPT, RAW_BY_D312, UNSAFE } from "../../src/api/visible-ranges";
import { render } from "./render";

/** A row of the corpus: its name, its text, and whether its code points must render raw. */
export interface Row {
  readonly name: string;
  readonly text: string;
  readonly raw: boolean;
}

/** The code points of inclusive ranges. */
function pointsOf(ranges: readonly (readonly [number, number])[]): number[] {
  return ranges.flatMap(([first, last]) => Array.from({ length: last - first + 1 }, (_, index) => first + index));
}

/** The code points as texts of at most `size` code points each, a space between them (so that no
 * row reaches the cut and each code point stands apart). */
function chunks(name: string, points: readonly number[], size = 400): Row[] {
  const rows: Row[] = [];
  for (let start = 0; start < points.length; start += size) {
    rows.push({ name: `${name} ${String(start)}`, text: points.slice(start, start + size).map((point) => String.fromCodePoint(point)).join(" "), raw: true });
  }
  return rows;
}

/** The hostile texts (escaped where D312 escapes; never an element). */
export const HOSTILE: readonly Row[] = [
  { name: "an img with onerror", text: '<img src=x onerror="alert(1)">', raw: false },
  { name: "a script", text: "<script>alert(1)</script>", raw: false },
  { name: "a right-to-left override", text: "\u202etxt.exe", raw: false },
  { name: "every bidi control", text: "a\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061cb", raw: false },
  { name: "a lone high surrogate", text: "a\ud800b", raw: false },
  { name: "a lone low surrogate", text: "a\udc00b", raw: false },
  { name: "javascript:", text: "javascript:alert(1)", raw: false },
  { name: "the isolate's end tag", text: "</bdi><b>bold</b><bdi>", raw: false },
  { name: "a data token spelled like the server's words", text: "The page could not reach the server; check the connection and try again.", raw: false },
  { name: "5,000 characters", text: "x".repeat(5000), raw: false },
  { name: "5,000 overrides", text: "\u202e".repeat(5000), raw: false },
  { name: "controls, separators and a CRLF", text: "a\u0000\u0007\u001b[31m\u007f\u0085\u2028\u2029b\r\nc\rd", raw: false },
  { name: "invisible format characters", text: "\u00ad\u200b\u2060\ufeff\u{e0001}\u{e0041}\u{1d173}", raw: false },
  { name: "a subdivision flag (tag characters, escaped by D312)", text: "\u{1f3f4}\u{e0067}\u{e0062}\u{e0065}\u{e006e}\u{e0067}\u{e007f}", raw: false },
  { name: "what D312 keeps: tab, line feed, ZWNJ, ZWJ", text: "a\tb\nc\u200cd\u200de", raw: false },
];

/** "Raw by D312" (D428's O-2 record): every code point the export names, which must render raw. */
export const RAW: readonly Row[] = [
  ...chunks("default-ignorable, assigned", pointsOf(RAW_BY_D312.default_ignorable_assigned)),
  ...chunks("default-ignorable, unassigned", pointsOf(RAW_BY_D312.default_ignorable_unassigned)),
  ...chunks("space separators", pointsOf(RAW_BY_D312.space_separators)),
  { name: "the braille blank", text: String.fromCodePoint(RAW_BY_D312.braille_blank), raw: true },
];

/** The whole corpus. */
export const CORPUS: readonly Row[] = [...HOSTILE, ...RAW];

const KEPT_POINTS = new Set(KEPT);

/** Whether a code point is unsafe (the visible form's rule, by the exported list or the engine). */
export function isUnsafe(point: number): boolean {
  if (KEPT_POINTS.has(point)) {
    return false;
  }
  if (UNSAFE.some(([first, last]) => point >= first && point <= last)) {
    return true;
  }
  return ENGINE.test(String.fromCodePoint(point));
}

/** The elements the visible form makes, by tag, and the classes of its spans. */
const MADE = new Set(["BDI", "SPAN"]);
const SPANS = new Set(["escape", "cut"]);

/** What is wrong with a rendering of a row: an element the visible form does not make, a raw unsafe
 * code point, a raw row's code point not shown raw. `own` names the position's own tags. */
export function problemsOf(container: Element, row: Row, own: readonly string[] = []): string[] {
  const found: string[] = [];
  for (const element of container.querySelectorAll("*")) {
    if (!MADE.has(element.tagName) && !own.includes(element.tagName)) {
      found.push(`${row.name}: a <${element.tagName.toLowerCase()}> element`);
    }
    if (element.tagName === "SPAN" && !SPANS.has(element.className)) {
      found.push(`${row.name}: a span of class ${element.className}`);
    }
  }
  const text = container.textContent;
  for (const character of text) {
    const point = character.codePointAt(0) ?? 0;
    if (isUnsafe(point)) {
      found.push(`${row.name}: U+${point.toString(16)} raw`);
      break;
    }
  }
  if (row.raw) {
    for (const character of row.text) {
      if (character !== " " && !text.includes(character)) {
        found.push(`${row.name}: U+${(character.codePointAt(0) ?? 0).toString(16)} not raw`);
        break;
      }
    }
    if (container.querySelector(".escape") !== null) {
      found.push(`${row.name}: an escape`);
    }
  }
  return found;
}

/** Render every row of `rows` in `position` (boxed by the tests' door) and give every problem; an
 * empty list is a pass. `own` names the tags the position makes itself. */
export async function injectionProblems(position: (box: ServerText) => ReactElement, own: readonly string[] = [], rows: readonly Row[] = CORPUS): Promise<string[]> {
  const found: string[] = [];
  for (const row of rows) {
    const shown = await render(position(boxText(row.text)));
    found.push(...problemsOf(shown.container, row, own));
    shown.unmount();
  }
  return found;
}
