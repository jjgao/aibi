/**
 * The takers of a server text (D426): the one module that reads a `ServerText`'s text
 * (`readText`, `box.ts`'s, which the lint and the import-graph test let this module alone import),
 * and gives back what is not data: a vocabulary's member (`kindOf`), an id that only its grammar
 * makes (`idOf`, a `DatasetId`; `columnOf`, a `ColumnId`), an opaque choice of a role (`choiceFor`, `document.ts`'s alone,
 * D432), a boolean (`sameText`) or an order (`compareText`). Each the index exports is pinned by its
 * exact signature in the index's gate (`tests/gates/api-graph.test.ts`), and no other export of the
 * index takes a box but the document seam's, each pinned there by identity (D432).
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
 * - **`Choice<R>`** (D432): a picker's choice of a role (`Role`: a document's `unit` table, a value
 *   leaf's `leaf` column, an analysis's `view` column), an opaque object that holds the box, its role
 *   (an ES private member typed `R`, so `Choice<"unit">` is no `Choice<"leaf">`) and the answer it was
 *   minted from (never a string, so it is no React child). It is made by `choiceFor` and read by
 *   `choiceText`, internals that `document.ts` alone imports (a lint row, the import-graph test):
 *   `optionsOf` mints every choice from a `describe_dataset` answer the walk returned, and
 *   `documentOf` reads each at its role's position. The index exports the type alone.
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
 *   `Text`, `Ref` and `Segments` add the `<bdi>`, the data mark and the server's cut mark, and
 *   `Option` adds none of them (an option holds text alone: its id, then ` — ` and its name, D441).
 *   Every form escapes the same code points. Residuals (D419's boundary, deliberate code): narrowing
 *   the element's `props` gives the box back, never its text; a detached `createRoot` and its
 *   `textContent`, or `react-dom/server`, read the visible form's text (closed in the app by D429's
 *   specifier allow-list, `react-dom/client` in the entries alone).
 * - **`idOfAddress(raw)`** (D428): the one door for a dataset id from the address (a hash route's
 *   parameter, as the router decoded it, or the raw path segment of `/datasets/<id>`, never
 *   decoded): the grammar of `idOf`, so a text that is not of it gives `null`, which the router
 *   module shows in fixed words, never echoed.
 * - **`columnOf(text)`** and **`columnOfAddress(raw)`** (D443): a `ColumnId`, the same for a column
 *   (`GRAMMARS.column`, `ColumnBrief.id`'s grammar, which the generator holds equal to
 *   `ColumnDescription.column`'s and `DescribeColumn.column`'s): `columnOf` mints it from a column's
 *   id in an answer (the dataset screen's link to a column), `columnOfAddress` from the column
 *   route's parameter (the router module's second door), and neither from anything else.
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

declare const columnBrand: unique symbol;

/** A column's id, made by `columnOf` or `columnOfAddress` alone: a string of `GRAMMARS.column`'s form
 * (`<table>.<column>`, its characters bounded, so it can stand in a request and a fragment). */
export type ColumnId = string & { readonly [columnBrand]: true };

/** Whether a string is of the column grammar (a checked predicate: the brand's one mint). */
function isColumnId(raw: string): raw is ColumnId {
  return matches(raw, "column");
}

/** The column id a text is, or `null` if it is not of the grammar. */
export function columnOf(text: ServerText): ColumnId | null {
  const raw = readText(text);
  return isColumnId(raw) ? raw : null;
}

/** The column id an address names, or `null` if the text is not of the grammar (the router
 * module's door: the column route's decoded parameter). */
export function columnOfAddress(raw: string): ColumnId | null {
  return isColumnId(raw) ? raw : null;
}

/** The words of a refused construction of a choice: fixed. */
export const FORGED_CHOICE = "A choice is made by optionsOf alone (D432).";

const CHOICE_TOKEN: unique symbol = Symbol("aibi.choice");

/** The roles a choice stands in (D432): a document's unit table, a value leaf's column, and a
 * column of an analysis's view. */
export type Role = "unit" | "leaf" | "view";

/** The grammar of each role's position: a table's id, or a column's. */
const GRAMMAR_OF: { readonly [R in Role]: GrammarName } = Object.freeze({ unit: "table", leaf: "column", view: "column" });

/** A choice's text and the token of the answer it was minted from, as `choiceText` gives them. */
export interface ChoiceRead {
  readonly text: string;
  readonly source: symbol;
}

let makeChoice: <R extends Role>(text: ServerText, role: R, source: symbol) => Choice<R>;
let readChoice: (choice: unknown, role: Role) => ChoiceRead | null;

/** A picker's choice of a role: the box of an answer's id that matched its role's grammar, the
 * role, and the token of the answer it came from, opaque (`document.ts` reads it through `choiceText`). */
export class Choice<R extends Role> {
  readonly #text: ServerText;
  readonly #role: R;
  readonly #source: symbol;

  private constructor(token: typeof CHOICE_TOKEN, text: ServerText, role: R, source: symbol) {
    if (token !== CHOICE_TOKEN) {
      throw new TypeError(FORGED_CHOICE);
    }
    this.#text = text;
    this.#role = role;
    this.#source = source;
    Object.freeze(this);
  }

  static {
    makeChoice = (text, role, source) => new Choice(CHOICE_TOKEN, text, role, source);
    readChoice = (choice, role) =>
      typeof choice === "object" && choice !== null && #role in choice && choice.#role === role ? Object.freeze({ text: readText(choice.#text), source: choice.#source }) : null;
  }

  /** Whether `value` is a choice this class made. */
  static holds(value: unknown): value is Choice<Role> {
    return typeof value === "object" && value !== null && #text in value;
  }
}

Object.freeze(Choice.prototype);
Object.freeze(Choice);

/** `document.ts`'s alone (D432): a choice of the role, minted from the answer `source` stands for
 * (a symbol `document.ts` makes per answer), if the box's text matches the role's grammar; `null`
 * otherwise. */
export function choiceFor<R extends Role>(text: ServerText, role: R, source: symbol): Choice<R> | null {
  return matches(readText(text), GRAMMAR_OF[role]) ? makeChoice(text, role, source) : null;
}

/** `document.ts`'s alone (D432): a choice's text and source, if it is a choice this class made of
 * that role; `null` otherwise (another role, or no choice at all). */
export function choiceText(choice: Choice<Role>, role: Role): ChoiceRead | null {
  return readChoice(choice, role);
}

/** `document.ts`'s alone (D432): whether a column id's table part (before its one dot, which the
 * column grammar gives it) is the table's id. */
export function ofTable(column: ServerText, table: ServerText): boolean {
  const raw = readText(column);
  const dot = raw.indexOf(".");
  return dot > 0 && raw.slice(0, dot) === readText(table);
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
