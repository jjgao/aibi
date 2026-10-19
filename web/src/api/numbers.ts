/**
 * The numbers' module (D430): what the page may know of a server number besides showing it, and
 * the one module beside `decode.ts` and `provenance.ts` that reads a box's text (`textOf`, D419,
 * D425). Everything it gives is a boolean, a key of a map the server sent, or (to `format.tsx`
 * alone, the internals below) the glyphs a number is shown by: never a JS number made from a
 * server number, never its text as a `string`.
 *
 * **The grammar.** A box holds its number's JSON source text (`decode.ts`: the reviver's
 * `context.source`), so its text is of JSON's number grammar (RFC 8259 §6, `NUMBER_TEXT`); this
 * module reads it as text, by that grammar, and never converts it. The lint refuses D419's number
 * names (`Number`, `String`, `Math`, `parseInt`, `parseFloat`, `JSON`, unary `+`, a bitwise
 * operator) and the names of a rounding (`Intl`, `toFixed`, `toPrecision`, `toExponential`,
 * `toLocaleString`) in this module and `format.tsx`, and no more: a conversion by another
 * spelling (`Float32Array.of(n)[0]`, a type predicate narrowing a number) is held by the tests'
 * number corpus alone (`format.test.tsx`), not by the lint (`eslint.config.mjs`). A text that is
 * not of the grammar is read by nothing: `glyphsOf` gives `null` (`Num` says it is not shown),
 * `isZero` and `isPositive` throw in fixed words, and `largestOf` gives `null`; no predicate
 * gives a number.
 *
 * **The sanctioned predicates** (Q4 of D427, pinned by their exact signatures in the index's gate,
 * `NUMBER_PINS`): `isZero` and `isPositive` (a boolean of one number), and `largestOf` (the key of a
 * map of counts whose value is largest; a tie is broken by the `Reason` vocabulary's order, the order
 * of `Population.unknown_by_reason`'s keys, for the keys of the vocabulary, and by `compareText`
 * (the smaller key's text) between keys outside it; `null` for a `null` map, an empty one, one
 * whose largest is zero, or one with a value that is no plain whole number). Comparing numbers the
 * page shows reveals nothing a person could not read off the page, and each gives a boolean or a
 * key, never a number or its text; no export of the index takes two numbers given separately (a
 * test), so there is no two-number comparator to bisect a withheld count with, and a withheld count
 * is `null`, never a box: `largestOf` refuses a map with a `null` member by type
 * (`BoxedMap<ServerNumber | null>`) and by the brand check at run time.
 *
 * **The internals** (`format.tsx`'s alone, `API_INTERNALS`): `glyphsOf` (a number's source text as
 * glyphs of a closed set, each a literal type, which the display lint lets stand at a JSX position,
 * D429); `countGlyphs` (a whole count the page made: the D312 remainder and `Retry-After`'s seconds,
 * never a server number); `remainingOf` (how many members of a list of a given length the page
 * does not show, past `LISTED`).
 *
 * **`document.ts`'s alone** (`API_INTERNALS`): `isCountOf` (whether a server number is the whole count
 * the page made: `optionsOf` asks it once, of `columns_total` and the number of columns the answer
 * lists, and gives only the boolean `partial`, D432). It would be a bisection oracle were it exported;
 * it is no export of the index.
 */
import { type BoxedMap, entriesOf, type ServerNumber, type ServerText, textOf } from "./box";
import { VOCABULARIES } from "./generated/shapes";
import { compareText, kindOf } from "./text";
import { LISTED } from "./visible-ranges";

export { LISTED };

/** The glyphs a number's text is written in (JSON's number grammar), each its own literal. */
export const GLYPHS = Object.freeze(["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "-", "+", ".", "e", "E"] as const);

/** A glyph of a number's text. */
export type Glyph = (typeof GLYPHS)[number];

const GLYPH: ReadonlyMap<string, Glyph> = new Map(GLYPHS.map((glyph) => [glyph, glyph] as const));

/** JSON's number grammar (RFC 8259 §6): what `decode.ts` boxes, and all this module reads. */
export const NUMBER_TEXT: RegExp = Object.freeze(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/u);

/** A number of the grammar whose value is zero: its integer part `0`, its fraction all zeros. */
const ZERO = Object.freeze(/^-?0(?:\.0+)?(?:[eE][+-]?[0-9]+)?$/u);

/** A plain whole number, as a count is written: no sign, no fraction, no exponent. */
const WHOLE = Object.freeze(/^(?:0|[1-9][0-9]*)$/u);

/** The words of a box whose text is not of the grammar: fixed, and never its text. */
export const UNREAD = "A server number's text is not of JSON's number grammar: it is read by nothing (D430).";

/** The words of a count the page made that is no whole number: fixed. */
export const NOT_A_COUNT = "A count the page shows is a whole number from 0 (D430).";

/** The largest whole number a count is, 2^53 - 1 (the generated document's bound of a count). */
const MAX_COUNT = 9007199254740991;

/** Whether a number the page made is a whole count, from 0 to 2^53 - 1 (`NaN` and the infinities
 * are none: their remainder is `NaN`). */
function isCount(count: number): boolean {
  return count % 1 === 0 && count >= 0 && count <= MAX_COUNT;
}

/** The box's text, if it is of the grammar. */
function sourceOf(number: ServerNumber): string | null {
  const text = textOf(number);
  return NUMBER_TEXT.test(text) ? text : null;
}

/** The box's text, which must be of the grammar. */
function readable(number: ServerNumber): string {
  const text = sourceOf(number);
  if (text === null) {
    throw new TypeError(UNREAD);
  }
  return text;
}

/** A text as glyphs, or `null` if a character of it is none. */
function glyphsOfText(text: string): readonly Glyph[] | null {
  const glyphs: Glyph[] = [];
  for (const character of text) {
    const glyph = GLYPH.get(character);
    if (glyph === undefined) {
      return null;
    }
    glyphs.push(glyph);
  }
  return Object.freeze(glyphs);
}

/** A number's source text as glyphs, exactly as the server wrote it (`1e-07`, `-0.0`, `1.10`), or
 * `null` when it is not of the grammar. `format.tsx`'s alone. */
export function glyphsOf(number: ServerNumber): readonly Glyph[] | null {
  const text = sourceOf(number);
  return text === null ? null : glyphsOfText(text);
}

/** A whole count the page made (never a server number), as glyphs. `format.tsx`'s alone. */
export function countGlyphs(count: number): readonly Glyph[] {
  if (!isCount(count)) {
    throw new RangeError(NOT_A_COUNT);
  }
  const glyphs = glyphsOfText(count.toString(10));
  if (glyphs === null) {
    throw new RangeError(NOT_A_COUNT);
  }
  return glyphs;
}

/** How many members of a list of `length` the page does not show, past `LISTED` (D312's lists
 * alone: `format.tsx`'s `listOf` is its position-typed door). `format.tsx`'s alone. */
export function remainingOf(length: number): number {
  if (!isCount(length)) {
    throw new RangeError(NOT_A_COUNT);
  }
  return length > LISTED ? length - LISTED : 0;
}

/** Whether a server number is zero (`0`, `-0`, `0.0`, `0e5`): its value, read from its text. */
export function isZero(number: ServerNumber): boolean {
  return ZERO.test(readable(number));
}

/** Whether a server number is exactly the whole count the page made (`document.ts`'s alone, D432):
 * its text is the count's decimal text, with no sign, fraction or exponent; any other text (`3.0`,
 * `3e0`) is no match, fail closed. A boolean of one server number and one count the page made,
 * never of two server numbers. */
export function isCountOf(number: ServerNumber, count: number): boolean {
  return isCount(count) && sourceOf(number) === count.toString(10);
}

/** Whether a server number is above zero: no minus sign, and not zero. */
export function isPositive(number: ServerNumber): boolean {
  const text = readable(number);
  return !text.startsWith("-") && !ZERO.test(text);
}

/** Whether one plain whole number's text is above another's: the longer, or at one length the later. */
function above(text: string, other: string): boolean {
  return text.length === other.length ? text > other : text.length > other.length;
}

/** The place of a reason in the `Reason` vocabulary's order; after them all for another key. */
function rankOf(key: ServerText): number {
  const reason = kindOf(key, "Reason");
  const reasons: readonly string[] = VOCABULARIES.Reason;
  return reason === null ? reasons.length : reasons.indexOf(reason);
}

/** The key of the map's largest count (ties by the `Reason` vocabulary's order, and between two
 * keys outside it by `compareText`, the smaller key's text first: the winner is a function of the
 * keys' text, not of the order the server wrote them in, and carries no meaning), or `null`: for a
 * `null` map (suppressed), an empty map, a map whose largest count is zero, or one with a value
 * that is no plain whole number (fail closed: no reason is named). */
export function largestOf(map: BoxedMap<ServerNumber> | null): ServerText | null {
  if (map === null) {
    return null;
  }
  let best: { readonly key: ServerText; readonly text: string; readonly rank: number } | null = null;
  for (const [key, value] of entriesOf(map)) {
    const text = textOf(value);
    if (!WHOLE.test(text)) {
      return null;
    }
    const rank = rankOf(key);
    if (best === null || above(text, best.text) || (text === best.text && (rank < best.rank || (rank === best.rank && compareText(key, best.key) < 0)))) {
      best = { key, text, rank };
    }
  }
  return best === null || best.text === "0" ? null : best.key;
}
