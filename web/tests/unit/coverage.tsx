/**
 * The coverage sweep (D433, B6.2): which members of a whole walked answer a view shows. Every leaf
 * of the answer, each text leaf (`ServerText`) and each number leaf (`ServerNumber`), keys of a
 * `BoxedMap` included, is replaced in turn by a sentinel of its kind (a text, or a number of its
 * own source text), the view is rendered to markup, and the leaf counts as shown when the sentinel
 * appears in it. Paths are normalised by position (a list's index is `[]`, a map's key `{key}` and
 * its value `{}`) and end in the leaf's kind (`@text`, `@number`), so that the set of shown paths is
 * a property of the view and the answer's shape, which a cell compares exactly with all paths minus
 * the view's exception list. An exception is a path prefix (matched at a segment boundary, `/` or
 * `@`), or a prefix that applies only to the answers a predicate names (a member a view shows in one
 * state of the answer and not in another, such as `lift_differs` under *k*).
 */
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { box, boxText, isServerNumber, ServerText } from "../../src/api/box";
import { leavesOf, replacedAt } from "./injection";

/** The sentinels: a text and a number no answer holds. */
export const SENTINEL_TEXT = "zqsentinelqz";
export const SENTINEL_NUMBER = "987654321";

/** A path normalised by position. */
export function normalised(path: readonly string[]): string {
  return path.map((segment) => (/^\d+$/u.test(segment) ? "[]" : segment.startsWith("#key") ? "{key}" : segment.startsWith("#val") ? "{}" : segment)).join("/");
}

/** What a sweep found of one answer: every path (normalised, with its leaf's kind), those of which
 * some leaf is shown, and those of which some leaf is not (an instance a view drops: the second item
 * of a list, the second cohort's member). */
export interface Coverage {
  readonly all: ReadonlySet<string>;
  readonly shown: ReadonlySet<string>;
  readonly missed: ReadonlySet<string>;
  readonly leaves: number;
}

/** Every leaf of `value` (text and number), and whether `make` shows each, by normalised path: a
 * path is in `shown` when any of its leaves is shown, and in `missed` when any is not. */
export function coverage<T>(value: T, make: (value: T) => ReactElement): Coverage {
  const texts = leavesOf(value, [], [], (leaf) => ServerText.holds(leaf));
  const numbers = leavesOf(value, [], [], (leaf) => isServerNumber(leaf));
  const all = new Set<string>();
  const shown = new Set<string>();
  const missed = new Set<string>();
  for (const [paths, by, sentinel, kind] of [
    [texts, boxText(SENTINEL_TEXT), SENTINEL_TEXT, "@text"],
    [numbers, box(SENTINEL_NUMBER), SENTINEL_NUMBER, "@number"],
  ] as const) {
    for (const path of paths) {
      const at = `${normalised(path)}${kind}`;
      all.add(at);
      if (renderToStaticMarkup(make(replacedAt(value, path, by) as T)).includes(sentinel)) {
        shown.add(at);
      } else {
        missed.add(at);
      }
    }
  }
  return { all, shown, missed, leaves: texts.length + numbers.length };
}

/** Whether a path is under a prefix: the whole path, or its start up to a segment boundary (`/`, or
 * the kind's `@`). */
export function under(path: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}@`));
}

/** An exception of a view: a prefix, or a prefix that applies to the answers `when` names. */
export type Exception<T> = string | { readonly path: string; readonly when: (answer: T) => boolean };

/** One answer swept: the answer and its coverage. */
export interface Swept<T> {
  readonly answer: T;
  readonly found: Coverage;
}

/** The prefixes of the exceptions that apply to `answer`. */
const applying = <T,>(exceptions: readonly Exception<T>[], answer: T): string[] => exceptions.flatMap((one) => (typeof one === "string" ? [one] : one.when(answer) ? [one.path] : []));

/** What is wrong with the coverages of a view's answers against its exception list: a path of which
 * a leaf is shown that is excepted, a path not excepted of which any leaf (any instance) is not shown,
 * and an exception that names no path of any of the answers it applies to (a stale list). */
export function coverageProblems<T>(swept: readonly Swept<T>[], exceptions: readonly Exception<T>[]): string[] {
  const problems: string[] = [];
  for (const { answer, found } of swept) {
    const prefixes = applying(exceptions, answer);
    for (const path of [...found.all].sort()) {
      const excepted = under(path, prefixes);
      if (excepted && found.shown.has(path)) {
        problems.push(`shown, but on the exception list: ${path}`);
      }
      if (!excepted && !found.shown.has(path)) {
        problems.push(`not shown, and not on the exception list: ${path}`);
      }
      if (!excepted && found.shown.has(path) && found.missed.has(path)) {
        problems.push(`shown for some instances and not for others, and not on the exception list: ${path}`);
      }
    }
  }
  for (const exception of exceptions) {
    const prefix = typeof exception === "string" ? exception : exception.path;
    if (!swept.some(({ answer, found }) => applying([exception], answer).length > 0 && [...found.all].some((path) => under(path, [prefix])))) {
      problems.push(`an exception that names no path of the answers it applies to: ${prefix}`);
    }
  }
  return [...new Set(problems)];
}
