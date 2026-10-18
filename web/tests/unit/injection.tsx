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
 *
 * `leafSweep` covers a component's positions by construction (m8 of #121 round 1): it walks the data
 * a component is given, finds every `ServerText` in it, and replaces each in turn by every row of
 * `HOSTILE`, so that no position is left out because a hand did not list it. It walks arrays, objects'
 * own data properties (string and symbol keys, enumerable or not), `BoxedMap`s (keys and values),
 * native `Map`s (keys and values) and `Set`s; it does not walk an accessor's result, a prototype, a
 * class's private fields or a typed array, and it replaces leaves of the value it is given alone
 * (pass every prop of the component in it). Its leaf count is held to an independent traversal
 * (`countTexts`), it throws unless it rendered every leaf under every row, and `injection.test.tsx`
 * holds, at every kind of position, that a leaf a component renders raw is flagged at the rows whose
 * text React does not escape (eight of the fifteen), at that leaf's path, and that a sentinel control,
 * shown only when the leaf is a hostile row's text, is flagged at every row.
 */
import { type ReactElement } from "react";

import { BoxedMap, boxMap, boxText, entriesOf, readText, ServerText } from "../../src/api/box";
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
 * code point, a raw row's code point not shown raw. `own` names the position's own tags, and
 * `classes` its own spans' classes (a component's own words: `Num`'s `number`, a list's `more`;
 * D430), each exactly as written; no other span is the position's. */
export function problemsOf(container: Element, row: Row, own: readonly string[] = [], classes: readonly string[] = []): string[] {
  const found: string[] = [];
  for (const element of container.querySelectorAll("*")) {
    if (!MADE.has(element.tagName) && !own.includes(element.tagName)) {
      found.push(`${row.name}: a <${element.tagName.toLowerCase()}> element`);
    }
    if (element.tagName === "SPAN" && !SPANS.has(element.className) && !classes.includes(element.className)) {
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
 * empty list is a pass. `own` names the tags the position makes itself, and `classes` its spans'
 * classes. */
export async function injectionProblems(position: (box: ServerText) => ReactElement, own: readonly string[] = [], rows: readonly Row[] = CORPUS, classes: readonly string[] = []): Promise<string[]> {
  const found: string[] = [];
  for (const row of rows) {
    const shown = await render(position(boxText(row.text)));
    found.push(...problemsOf(shown.container, row, own, classes));
    shown.unmount();
  }
  return found;
}

/** Whether a value is a `ServerText` box (the brand check of its private field, as the walk reads it). */
function isText(value: unknown): value is ServerText {
  try {
    readText(value as ServerText);
    return true;
  } catch {
    return false;
  }
}

/** The segment of an object's own key at `index` of `Reflect.ownKeys`: a string key is itself, a
 * symbol key `@sym<index>` (a string key spelled so, or beginning `#`, would collide: none is). */
function segmentOf(key: string | symbol, index: number): string {
  return typeof key === "symbol" ? `@sym${String(index)}` : key;
}

/** The path to every `ServerText` leaf of a value, as a position in the data: a list's index, an
 * object's own key (a string key, a symbol key `@sym<n>`, enumerable or not, a data property: an
 * accessor is not called), a `BoxedMap`'s keys (`#key<n>`) and values (`#val<n>`), a native `Map`'s
 * (`#mkey<n>`, `#mval<n>`) and a `Set`'s members (`#set<n>`). What it does not walk: an accessor's
 * result, a prototype's members, a class's private fields (a box's own `#text` is the leaf itself)
 * and a typed array; and the sweep replaces a leaf of the value it is given alone (pass every prop
 * of the component in the value: `{ hit, at }`, not `hit`). */
export function leavesOf(value: unknown, at: readonly string[] = [], ancestors: readonly object[] = [], isLeaf: (value: unknown) => boolean = isText): (readonly string[])[] {
  if (isLeaf(value)) {
    return [at];
  }
  if (typeof value !== "object" || value === null) {
    return [];
  }
  if (ancestors.includes(value)) {
    throw new Error(`a cycle at @/${at.join("/")}`);
  }
  const inside = [...ancestors, value];
  const walk = (member: unknown, segment: string): (readonly string[])[] => leavesOf(member, [...at, segment], inside, isLeaf);
  if (Array.isArray(value)) {
    return value.flatMap((member: unknown, index) => walk(member, String(index)));
  }
  if (value instanceof BoxedMap) {
    return entriesOf(value as BoxedMap<unknown>).flatMap(([key, member], index) => [...walk(key, `#key${String(index)}`), ...walk(member, `#val${String(index)}`)]);
  }
  if (value instanceof Map) {
    return [...(value as Map<unknown, unknown>)].flatMap(([key, member], index) => [...walk(key, `#mkey${String(index)}`), ...walk(member, `#mval${String(index)}`)]);
  }
  if (value instanceof Set) {
    return [...(value as Set<unknown>)].flatMap((member, index) => walk(member, `#set${String(index)}`));
  }
  return Reflect.ownKeys(value).flatMap((key, index) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return descriptor !== undefined && "value" in descriptor ? walk(descriptor.value, segmentOf(key, index)) : [];
  });
}

/** How many `ServerText` leaves a value holds, by a traversal written differently from `leavesOf`
 * (the independent count a sweep's own count is held to): an explicit stack, no brand check by
 * `readText`, every own property by `Object.getOwnPropertyNames` and `getOwnPropertySymbols` (an
 * array's indexes among them, no special case), the maps and the set by their `forEach`. */
export function countTexts(value: unknown): number {
  let found = 0;
  const stack: unknown[] = [value];
  while (stack.length > 0) {
    const step = stack.pop();
    if (ServerText.holds(step)) {
      found += 1;
    } else if (BoxedMap.holds(step)) {
      for (const [key, member] of entriesOf(step)) {
        stack.push(key, member);
      }
    } else if (step instanceof Map) {
      (step as Map<unknown, unknown>).forEach((member, key) => stack.push(key, member));
    } else if (step instanceof Set) {
      (step as Set<unknown>).forEach((member) => stack.push(member));
    } else if (typeof step === "object" && step !== null) {
      for (const key of [...Object.getOwnPropertyNames(step), ...Object.getOwnPropertySymbols(step)]) {
        const descriptor = Object.getOwnPropertyDescriptor(step, key);
        if (descriptor !== undefined && "value" in descriptor) {
          stack.push(descriptor.value);
        }
      }
    }
  }
  return found;
}

/** `value` with the leaf at `path` replaced by `by`: a copy, the data around the leaf as it was
 * (an object's descriptors, symbol keys and non-enumerable properties included). `by` is a text box
 * for the sweeps, or any leaf (a number box) for the coverage sweeps (`coverage.tsx`). */
export function replacedAt(value: unknown, path: readonly string[], by: unknown): unknown {
  const [head, ...rest] = path;
  if (head === undefined) {
    return by;
  }
  const replace = (member: unknown, segment: string): unknown => (head === segment ? replacedAt(member, rest, by) : member);
  if (Array.isArray(value)) {
    return value.map((member: unknown, index) => replace(member, String(index)));
  }
  if (value instanceof BoxedMap) {
    return boxMap(entriesOf(value as BoxedMap<unknown>).map(([key, member], index) => [replace(key, `#key${String(index)}`) as ServerText, replace(member, `#val${String(index)}`)] as const));
  }
  if (value instanceof Map) {
    return new Map([...(value as Map<unknown, unknown>)].map(([key, member], index) => [replace(key, `#mkey${String(index)}`), replace(member, `#mval${String(index)}`)] as const));
  }
  if (value instanceof Set) {
    return new Set([...(value as Set<unknown>)].map((member, index) => replace(member, `#set${String(index)}`)));
  }
  const object = value as object;
  const descriptors: Record<string | symbol, PropertyDescriptor> = Object.getOwnPropertyDescriptors(object);
  Reflect.ownKeys(object).forEach((key, index) => {
    const descriptor = descriptors[key];
    if (descriptor !== undefined && "value" in descriptor && head === segmentOf(key, index)) {
      descriptors[key] = { ...descriptor, value: replacedAt(descriptor.value, rest, by) };
    }
  });
  const prototype: object | null = Object.getPrototypeOf(object) as object | null;
  return Object.create(prototype, descriptors) as unknown;
}

/** What a sweep did: how many leaves it found, how many renderings it made (`leaves` times the
 * rows of the corpus), and every problem found. */
export interface Swept {
  readonly leaves: number;
  readonly renders: number;
  readonly problems: string[];
}

/** Every `ServerText` leaf of `value` replaced in turn by every row of `rows`, rendered by `make`,
 * and every problem found. The sweep checks itself: its leaves are those an independent traversal
 * counts (`countTexts`), and it made one rendering for each leaf and row, or it throws (so that a
 * sweep that finds none, or renders fewer, cannot pass); the sweep of `injection.test.tsx`, over a
 * component that renders one leaf raw, holds that a leaf rendered raw is found at the rows React
 * does not escape, and a sentinel at every row. `inspect`, when given, is asked of every rendering (the
 * leaf's path, the row and the rendering's container) for further problems of its own: what a position
 * shows beyond the visible form's safety, such as the cut at 2,000 code points (round 1 of #123, m1). */
export async function leafSweep<T>(
  value: T,
  make: (value: T) => ReactElement,
  own: readonly string[] = [],
  classes: readonly string[] = [],
  rows: readonly Row[] = HOSTILE,
  inspect?: (path: readonly string[], row: Row, container: Element) => readonly string[],
): Promise<Swept> {
  const paths = leavesOf(value);
  const counted = countTexts(value);
  if (paths.length !== counted) {
    throw new Error(`the sweep found ${String(paths.length)} leaves, an independent count ${String(counted)}`);
  }
  const problems: string[] = [];
  let renders = 0;
  for (const path of paths) {
    for (const row of rows) {
      const shown = await render(make(replacedAt(value, path, boxText(row.text)) as T));
      renders += 1;
      problems.push(...[...problemsOf(shown.container, row, own, classes), ...(inspect?.(path, row, shown.container) ?? [])].map((problem) => `@/${path.join("/")}: ${problem}`));
      shown.unmount();
    }
  }
  if (renders !== counted * rows.length) {
    throw new Error(`the sweep made ${String(renders)} renderings of ${String(counted)} leaves and ${String(rows.length)} rows`);
  }
  return { leaves: paths.length, renders, problems };
}
