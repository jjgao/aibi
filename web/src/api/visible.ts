/**
 * The visible form of a text (D312, D428): the server's page's (`aibi.core.api.markup`,
 * `aibi.core.schema.visible`), computed here over the same classes, which the server exports
 * (`schemas/visible-form.json`, generated into `visible-ranges.ts`). `text.tsx`'s `showText`, the one
 * door from a server text to the DOM, renders what `visibleForm` gives; nothing else of the app
 * calls it (an internal of `src/api/`, `text.tsx`'s alone).
 *
 * **The rule.** A text is cut at `CUT` code points first (by code point, as `Array.from` counts
 * them: a surrogate pair is never split, a lone surrogate is one), unless the position is not cut
 * (`cut` is `null`: pointers, statistic references, data tokens and the server's own words, as the
 * server's page); then a carriage return immediately before a line feed is dropped; then each code
 * point is *unsafe* when it is not one of `KEPT` (tab, line feed, ZWNJ, ZWJ) and either the
 * server's list holds it (`ranges`) or the running engine's `\p{Cc}\p{Cf}\p{Cs}` does (`engine`), a
 * second arm so that a browser whose Unicode is newer than the server's escapes more, never less;
 * each unsafe code point is written `\xNN`, `\uNNNN` or `\UNNNNNNNN` (lower-case hex), and each
 * maximal run of them is one escaped run (one span when rendered), so that an escape is told from
 * the same characters written in the data and a text of invisible characters grows the page a few
 * times at most.
 *
 * `ranges` and `engine` are parameters so that a test tells the two arms apart (a reduced list
 * proves the engine arm; a stub expression the list's).
 */
import { CUT, KEPT, UNSAFE } from "./visible-ranges";

/** A run of the visible form: shown as it is, or an escaped run (its escapes' text). */
export interface Run {
  readonly escaped: boolean;
  readonly text: string;
}

/** The visible form of a text: its runs, and whether it was cut. */
export interface Visible {
  readonly runs: readonly Run[];
  readonly truncated: boolean;
}

/** The engine's arm: one code point of the control, format or surrogate category (frozen: a
 * non-global expression's `test` writes no `lastIndex`). */
export const ENGINE: RegExp = Object.freeze(/^[\p{Cc}\p{Cf}\p{Cs}]$/u);

const KEPT_POINTS: ReadonlySet<number> = new Set(KEPT);

/** Whether the inclusive, ascending ranges hold a code point (a binary search). */
function listed(point: number, ranges: readonly (readonly [number, number])[]): boolean {
  let low = 0;
  let high = ranges.length - 1;
  while (low <= high) {
    const middle = (low + high) >>> 1;
    const range = ranges[middle];
    if (range === undefined) {
      return false;
    }
    if (point < range[0]) {
      high = middle - 1;
    } else if (point > range[1]) {
      low = middle + 1;
    } else {
      return true;
    }
  }
  return false;
}

/** A code point's escape: `\xNN` below U+0100, `\uNNNN` below U+10000, `\UNNNNNNNN` above. */
function escapeOf(point: number): string {
  const hex = point.toString(16);
  if (point < 0x100) {
    return `\\x${hex.padStart(2, "0")}`;
  }
  return point < 0x10000 ? `\\u${hex.padStart(4, "0")}` : `\\U${hex.padStart(8, "0")}`;
}

/** The visible form of `text` (the module's rule), cut at `cut` code points unless it is `null`. */
export function visibleForm(text: string, ranges: readonly (readonly [number, number])[] = UNSAFE, engine: RegExp = ENGINE, cut: number | null = CUT): Visible {
  const points: string[] = [];
  let truncated = false;
  for (const point of text) {
    if (cut !== null && points.length === cut) {
      truncated = true;
      break;
    }
    points.push(point);
  }
  const runs: Run[] = [];
  let parts: string[] = [];
  let escaped = false;
  const close = (): void => {
    if (parts.length > 0) {
      runs.push(Object.freeze({ escaped, text: parts.join("") }));
    }
    parts = [];
  };
  points.forEach((point, index) => {
    if (point === "\r" && points[index + 1] === "\n") {
      return;
    }
    const code = point.codePointAt(0) ?? 0;
    const unsafe = !KEPT_POINTS.has(code) && (listed(code, ranges) || engine.test(point));
    if (unsafe !== escaped) {
      close();
      escaped = unsafe;
    }
    parts.push(unsafe ? escapeOf(code) : point);
  });
  close();
  return Object.freeze({ runs: Object.freeze(runs), truncated });
}
