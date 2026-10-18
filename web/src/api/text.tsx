/**
 * The takers of a server text (D426): the one module that reads a `ServerText`'s text
 * (`readText`, `box.ts`'s, which the lint and the import-graph test let this module alone import),
 * and gives back what is not data: a vocabulary's member (`kindOf`), an id that only its grammar
 * makes (`idOf`, a `DatasetId`), an opaque choice (`choiceOf`), a boolean (`sameText`) or an order
 * (`compareText`). Each is pinned by its exact signature in the index's gate
 * (`tests/gates/api-graph.test.ts`), and no other export of the index takes a box.
 *
 * - **`kindOf(text, vocabulary)`**: the member of a generated vocabulary (`VOCABULARIES`, by place
 *   in the document: `"Severity"`, `"ApplicableAnalysis.status"`, ...) that the text is, or `null`
 *   (an open union's other member, such as a pack's caveat or refusal code, which is shown through
 *   `Text`, never dropped); a fail-closed vocabulary (`FALLBACKS`: `Severity`) never gives `null`:
 *   an unknown severity is read as `block`. The member returned is the vocabulary's own word, a
 *   literal of the code, never the box's text.
 * - **`idOf(text)`**: a `DatasetId`, a string whose grammar (`CatalogHit.dataset`'s pattern,
 *   length and `not`, generated from the document: `GRAMMARS.dataset`) bounds its characters, so it
 *   can stand in a request and a fragment; `null` otherwise. The brand proves the form, not the
 *   provenance: a label that happens to match the grammar makes a valid `DatasetId`.
 * - **`choiceOf(text, grammar)`**: a picker's choice (a table, a column, an analysis, a dataset), an
 *   opaque object that holds the box (never a string, so it is no React child), made when the text
 *   matches the id position's generated grammar; `null` otherwise. M5.3's request half reads it.
 * - **`memberOf(map, name)`**: the value of a map's member named by the page's own word (a
 *   values table's `numerator`, `estimate`, a `not_estimable` pointer: the members of an
 *   `x-aibi-json` value, which the walk does not check, m4 of round 4), as `{found: true, value}`,
 *   or `{found: false}`, which the page shows fail-closed ("not shown: the page and the server
 *   disagree"), never as a figure without it. It reads the keys' text and gives a value, never a key.
 * - **`sameText(a, b)`** and **`compareText(a, b)`**: the sanctioned equality and order (a box is
 *   never `===` another of the same text, and `sort`, `localeCompare` and `Intl.Collator` throw on
 *   one): the order of the texts' UTF-16 code units, as `Array.prototype.sort`'s default,
 *   locale-free. Both take two server texts: a component cannot box a string of its own, so neither
 *   compares a box with a text the page chose.
 * - **`showText(text)`** (D428): **the one door from a server text to the DOM**, a React element
 *   that renders the text's visible form (`visible.ts`: cut at 2,000 code points, a carriage return
 *   before a line feed dropped, each run of unsafe code points escaped in a `<span class="escape">`,
 *   the cut marked). The element holds the box, not its text (its type is this module's `Shown`,
 *   its props `{text}`, typed `unknown` to the caller), and its text is read as it renders. The
 *   position it renders in chooses the form through the layout's `FORM` context (`src/layout/form.ts`:
 *   `cut`, the default; `whole`, not cut, for pointers, references, data tokens and the server's
 *   words; `flat`, an `<option>`'s text, the escapes as literal text with no span); the layout's
 *   `Text`, `Ref`, `Segments` and `Option` add the `<bdi>`, the data mark and the server's cut mark.
 *   Every form escapes the same code points. Residuals (D419's boundary, deliberate code): narrowing
 *   the element's `props` gives the box back, never its text; a detached `createRoot` and its
 *   `textContent`, or `react-dom/server`, read the visible form's text (closed by 2b-1L's
 *   specifier allow-list, `react-dom/client` in the entries alone).
 * - **`idOfAddress(raw)`** (D428): the one door for a dataset id from the address (a hash route's
 *   parameter, as the router decoded it, or the raw path segment of `/datasets/<id>`, never
 *   decoded): the grammar of `idOf`, so a text that is not of it gives `null`, which the router
 *   module shows in fixed words, never echoed.
 */
import { Fragment, type ReactElement, type ReactNode, useContext } from "react";

import { FORM } from "../layout/form";
import { type BoxedMap, entriesOf, readText, type ServerText } from "./box";
import { FALLBACKS, GRAMMARS, VOCABULARIES } from "./generated/shapes";
import { ENGINE, visibleForm } from "./visible";
import { CUT, UNSAFE } from "./visible-ranges";

/** The name of a generated vocabulary. */
export type VocabularyName = keyof typeof VOCABULARIES;

/** What `kindOf` gives for a vocabulary: one of its members, or `null` unless it is read
 * fail-closed. */
export type Kind<K extends VocabularyName> = K extends keyof typeof FALLBACKS ? (typeof VOCABULARIES)[K][number] : (typeof VOCABULARIES)[K][number] | null;

/** Whether a value is what `kindOf` may give for a vocabulary: one of its members, or `null` where
 * it is not read fail-closed (a checked predicate: the step from the vocabulary's words to the
 * literal type). */
function isKind<K extends VocabularyName>(vocabulary: K, value: string | null): value is Kind<K> {
  const members: readonly string[] = VOCABULARIES[vocabulary];
  return value === null ? !Object.hasOwn(FALLBACKS, vocabulary) : members.includes(value);
}

/** The member of `vocabulary` that `text` is (a fail-closed vocabulary's fallback, or `null`, for
 * any other text). */
export function kindOf<K extends VocabularyName>(text: ServerText, vocabulary: K): Kind<K> {
  const raw = readText(text);
  const members: readonly string[] = VOCABULARIES[vocabulary];
  const fallbacks: { readonly [name: string]: string | undefined } = FALLBACKS;
  const found = members.find((member) => member === raw) ?? fallbacks[vocabulary] ?? null;
  if (!isKind(vocabulary, found)) {
    throw new TypeError(UNREACHABLE);
  }
  return found;
}

/** The words of a step the predicates make unreachable: fixed. */
const UNREACHABLE = "A vocabulary's member is its own (D426).";

/** The name of an id position's grammar. */
export type GrammarName = keyof typeof GRAMMARS;

/** The compiled patterns, by their text. */
const COMPILED = new Map<string, RegExp>();

function compiled(pattern: string): RegExp {
  const found = COMPILED.get(pattern) ?? new RegExp(pattern, "u");
  COMPILED.set(pattern, found);
  return found;
}

/** Whether a text matches an id position's grammar: its pattern, its length in code points, and
 * none of the patterns it may not match. */
function matches(raw: string, grammar: GrammarName): boolean {
  const { p, m, x } = GRAMMARS[grammar];
  return compiled(p).test(raw) && Array.from(raw).length <= m && !x.some((refused) => compiled(refused).test(raw));
}

declare const datasetBrand: unique symbol;

/** A dataset's id, made by `idOf` alone: a string of `GRAMMARS.dataset`'s form. */
export type DatasetId = string & { readonly [datasetBrand]: true };

/** Whether a string is of the dataset grammar (a checked predicate: the brand's one mint). */
function isDatasetId(raw: string): raw is DatasetId {
  return matches(raw, "dataset");
}

/** The dataset id a text is, or `null` if it is not of the grammar. */
export function idOf(text: ServerText): DatasetId | null {
  const raw = readText(text);
  return isDatasetId(raw) ? raw : null;
}

/** The dataset id an address names, or `null` if the text is not of the grammar (the router
 * module's door: a hash route's decoded parameter, or a path segment as written). */
export function idOfAddress(raw: string): DatasetId | null {
  return isDatasetId(raw) ? raw : null;
}

/** The words of a refused construction of a choice: fixed. */
export const FORGED_CHOICE = "A choice is made by choiceOf alone (D426).";

const CHOICE_TOKEN: unique symbol = Symbol("aibi.choice");

let makeChoice: (text: ServerText) => Choice;

/** A picker's choice: the box of an id position's text that matched the position's grammar,
 * opaque (M5.3's request half reads it; nothing does yet). */
export class Choice {
  readonly #text: ServerText;

  private constructor(token: typeof CHOICE_TOKEN, text: ServerText) {
    if (token !== CHOICE_TOKEN) {
      throw new TypeError(FORGED_CHOICE);
    }
    this.#text = text;
    Object.freeze(this);
  }

  static {
    makeChoice = (text) => new Choice(CHOICE_TOKEN, text);
  }

  /** Whether `value` is a choice this class made. */
  static holds(value: unknown): value is Choice {
    return typeof value === "object" && value !== null && #text in value;
  }
}

Object.freeze(Choice.prototype);
Object.freeze(Choice);

/** A choice of the box, if its text matches the id position's grammar; `null` otherwise. */
export function choiceOf(text: ServerText, grammar: GrammarName): Choice | null {
  return matches(readText(text), grammar) ? makeChoice(text) : null;
}

/** What `memberOf` gives: the member's value, or that the map has none of that name. */
export type Member<V> = { readonly found: true; readonly value: V } | { readonly found: false };

const MISSING: Member<never> = Object.freeze({ found: false });

/** The value of the map's member whose key is `name` (the first, should a map hold two: JSON gives
 * none), or `{found: false}`. */
export function memberOf<V>(map: BoxedMap<V>, name: string): Member<V> {
  for (const [key, value] of entriesOf(map)) {
    if (readText(key) === name) {
      return Object.freeze({ found: true, value });
    }
  }
  return MISSING;
}

/** Whether two server texts are the same text. */
export function sameText(a: ServerText, b: ServerText): boolean {
  return readText(a) === readText(b);
}

/** The order of two server texts: negative, zero or positive, by their UTF-16 code units. */
export function compareText(a: ServerText, b: ServerText): number {
  const first = readText(a);
  const second = readText(b);
  return first < second ? -1 : first > second ? 1 : 0;
}

/** The visible form of the box's text, rendered in the form its position asks for. */
function Shown({ text }: { readonly text: ServerText }): ReactNode {
  const form = useContext(FORM);
  const { runs, truncated } = visibleForm(readText(text), UNSAFE, ENGINE, form === "whole" ? null : CUT);
  if (form === "flat") {
    return `${runs.map((run) => run.text).join("")}${truncated ? "\u2026" : ""}`;
  }
  return (
    <>
      {runs.map((run, index) =>
        run.escaped ? (
          <span key={index} className="escape">
            {run.text}
          </span>
        ) : (
          <Fragment key={index}>{run.text}</Fragment>
        ),
      )}
      {truncated ? (
        <span className="cut" title="cut">
          {"\u2026"}
        </span>
      ) : null}
    </>
  );
}

/** The element that shows a server text: its visible form (the module's docstring). */
export function showText(text: ServerText): ReactElement {
  return <Shown text={text} />;
}
