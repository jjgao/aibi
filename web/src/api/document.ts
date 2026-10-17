/**
 * The document seam (D432): what a screen can ever put into a request document. A screen holds
 * choices and typed values; this module alone writes the document from them, and the index exports
 * no function that takes a document (`validateDocument`, `countCohort` and `runAnalysis` are
 * `typed.ts`' and this module's alone, by a lint row and the import-graph test).
 *
 * - **`optionsOf(answer)`** mints every choice a screen can hold, from a `describe_dataset` answer
 *   the walk of `typed.ts` itself returned (`walkedAs`: an object the walk gave, with its operation;
 *   anything else, a copy, a spread, a `Proxy` or another operation's answer cast, is refused with
 *   `notAChoice`), memoised per answer (the same answer gives the same options, so the same
 *   choices). Each choice records the answer it was minted from, so a draft's choices are of one
 *   answer or the draft is refused, and a view choice the unit option it was minted under, so a
 *   draft's view choices are of its own unit's options or the draft is refused. It gives, frozen:
 *   the dataset's id (`idOf`, or `null`); the units, each a graph table of the table grammar with
 *   its view columns (the columns whose id's table part is the unit's, of `DATATYPES.view`'s
 *   datatypes, or of `view_under_k`'s when the answer's `disclosure.min_cell_count` is not `null`);
 *   the leaf columns (every column of `DATATYPES.leaf`'s datatypes); and booleans of the page's own
 *   lists (`left`: a table, a leaf column or **any unit's** column left out, one boolean over every
 *   unit, so a screen shows it beside the list, never beside one unit; `partial`: `columns_total` is
 *   a number other than the count of columns the answer lists, which holds for every page of a paged
 *   answer but a whole one, and for an answer whose `columns_total` is below its columns; `underK`),
 *   never a count or a text.
 * - **`validateDraft`, `countDraft` and `runDraft`** send the document `documentOf` writes from a
 *   `Draft` through `typed.ts`' functions.
 *
 * **What `documentOf` writes, and nothing else** (its totality): `{aibi: "1", dataset, unit,
 * cohorts: {first: {all: [...]}, second?: {...}}, views: [{analysis: "summary.distribution",
 * cohorts: ["first"(, "second")], params: {columns: [{column}, ...]}}]}`, each leaf `{kind: "value",
 * column, values: [value]}`. Every string is (a) a literal of this module at its fixed position
 * (`"1"`, `"value"`, `"summary.distribution"`, `"first"`, `"second"`), (b) the draft's `DatasetId`,
 * equal to the one of the answer its choices were minted from, (c) a choice's text at its role's
 * position (`choiceText`, which gives nothing for another role), or (d) a typed value at a leaf's
 * `values/0`, as §7.1's literal: a value that starts with `$` is written with one more (`$$`), so
 * it is never a parameter reference. No number, no `null`, no other key. It reads each member of the
 * draft by its name and writes a new object: it never spreads, iterates the keys of or passes on a
 * draft object, so an excess member (`notes`, a `toJSON`) reaches nothing. A draft that is not one
 * (another role at a position, choices of two answers, a view choice of another unit's options, a
 * dataset not theirs, no cohort or three, no view column, an empty or non-string value) is refused
 * with `notAChoice`, thrown before anything is sent, as the client's own refusals are. Each list's
 * length (cohorts, leaves, view columns) is read once, and is a whole number (`Number.isSafeInteger`)
 * of at least the list's least (one cohort or two, no view column fewer than one, a cohort's leaves
 * none or more) or the draft is refused: a length that is `NaN`, a text, an object, negative or
 * fractional sends nothing. There is no client bound on the number of leaves or of view columns (the
 * server refuses in-band, D432's table of rules).
 */
import type { Typed } from "../layout/Field";
import type { ServerText } from "./box";
import { ClientError, type RequestBody } from "./client";
import { DATATYPES } from "./datatypes";
import { isCountOf } from "./numbers";
import { type Choice, choiceFor, choiceText, type DatasetId, idOf, kindOf, ofTable, type Role } from "./text";
import { type Answered, countCohort, type Outcome, runAnalysis, validateDocument, walkedAs } from "./typed";

/** A value leaf of a cohort: its column, and what the person typed. */
export interface LeafDraft {
  readonly column: Choice<"leaf">;
  readonly value: Typed;
}

/** A cohort: all of its value leaves. */
export interface CohortDraft {
  readonly leaves: readonly LeafDraft[];
}

/** What a screen hands the seam: a dataset, a unit, one or two cohorts and the view's columns. */
export interface Draft {
  readonly dataset: DatasetId;
  readonly unit: Choice<"unit">;
  readonly cohorts: readonly [CohortDraft] | readonly [CohortDraft, CohortDraft];
  readonly view: { readonly columns: readonly [Choice<"view">, ...Choice<"view">[]] };
}

/** An option of a picker: its label (the id's box, shown as data) and its choice. */
export interface Option<R extends Role> {
  readonly label: ServerText;
  readonly choice: Choice<R>;
}

/** A unit table's option, with the columns its view may summarise. */
export interface UnitOption extends Option<"unit"> {
  readonly view: readonly Option<"view">[];
}

/** What a describe answer offers a screen (the module's docstring). */
export interface Options {
  readonly dataset: DatasetId | null;
  readonly units: readonly UnitOption[];
  readonly leaves: readonly Option<"leaf">[];
  /** What the lists leave out: a graph table, a leaf column, or a column of any unit's (`view` is one
   * boolean over every unit, not one unit's). */
  readonly left: { readonly tables: boolean; readonly leaves: boolean; readonly view: boolean };
  /** Whether `columns_total` is a number other than the count of columns the answer lists: any page
   * of a paged answer but a whole one (the first, a middle and the last alike), and an answer whose
   * `columns_total` is below the columns it lists. */
  readonly partial: boolean;
  readonly underK: boolean;
}

/** The options of each answer, by the answer: `optionsOf`'s memory. */
const OPTIONS = new WeakMap<object, Options>();

/** The options of each answer, by the token its choices record (a symbol per answer: a choice holds
 * no object, so nothing a caller hands the seam reaches an answer through it). */
const SOURCES = new WeakMap<symbol, Options>();

/** The unit choice each view choice was minted under (a view choice of one unit's options is no
 * view of another unit's draft). */
const UNITS = new WeakMap<Choice<"view">, Choice<"unit">>();

/** A column's datatype, or `null` (absent, or a word the page does not know). */
function datatypeOf(column: { readonly datatype?: ServerText }): string | null {
  return column.datatype === undefined ? null : kindOf(column.datatype, "ColumnBrief.datatype");
}

/** The options of a `describe_dataset` answer the walk returned (the module's docstring). */
export function optionsOf(answer: Answered<"describe_dataset">): Options {
  const known = OPTIONS.get(answer);
  if (known !== undefined) {
    return known;
  }
  if (walkedAs(answer) !== "describe_dataset") {
    throw new ClientError("notAChoice");
  }
  const source = Symbol("aibi.answer");
  const underK = answer.disclosure.min_cell_count !== null;
  const viewTypes: readonly string[] = underK ? DATATYPES.view_under_k : DATATYPES.view;
  const leafTypes: readonly string[] = DATATYPES.leaf;
  const columns = answer.tables.flatMap((table) => table.columns);
  let leftTables = false;
  let leftView = false;
  let leftLeaves = false;
  const units: UnitOption[] = [];
  for (const table of answer.graph.tables) {
    const choice = choiceFor(table, "unit", source);
    if (choice === null) {
      leftTables = true;
      continue;
    }
    const view: Option<"view">[] = [];
    for (const column of columns) {
      if (!ofTable(column.id, table)) {
        continue;
      }
      const datatype = datatypeOf(column);
      const made = datatype !== null && viewTypes.includes(datatype) ? choiceFor(column.id, "view", source) : null;
      if (made === null) {
        leftView = true;
        continue;
      }
      UNITS.set(made, choice);
      view.push(Object.freeze({ label: column.id, choice: made }));
    }
    units.push(Object.freeze({ label: table, choice, view: Object.freeze(view) }));
  }
  const leaves: Option<"leaf">[] = [];
  for (const column of columns) {
    const datatype = datatypeOf(column);
    const made = datatype !== null && leafTypes.includes(datatype) ? choiceFor(column.id, "leaf", source) : null;
    if (made === null) {
      leftLeaves = true;
      continue;
    }
    leaves.push(Object.freeze({ label: column.id, choice: made }));
  }
  const options: Options = Object.freeze({
    dataset: idOf(answer.dataset),
    units: Object.freeze(units),
    leaves: Object.freeze(leaves),
    left: Object.freeze({ tables: leftTables, leaves: leftLeaves, view: leftView }),
    partial: !isCountOf(answer.columns_total, columns.length),
    underK,
  });
  OPTIONS.set(answer, options);
  SOURCES.set(source, options);
  return options;
}

/** A value leaf as written. */
interface WrittenLeaf {
  readonly kind: "value";
  readonly column: string;
  readonly values: readonly [string];
}

/** A cohort as written. */
interface WrittenCohort {
  readonly all: readonly WrittenLeaf[];
}

/** The document, as `documentOf` writes it. */
type Written = RequestBody<"run_analysis">["document"] & RequestBody<"count_cohort">["document"];

/** The token of the answer a draft's choices were minted from: the first choice read sets it. */
interface Source {
  answer: symbol | null;
}

/** The refusal of a draft that is not one. */
function refused(): never {
  throw new ClientError("notAChoice");
}

/** A choice's text at its role's position, of the draft's one answer (the one place
 * `choiceText` is called). */
function id(choice: Choice<Role>, role: Role, source: Source): string {
  const read = choiceText(choice, role);
  if (read === null || (source.answer !== null && read.source !== source.answer)) {
    return refused();
  }
  source.answer = read.source;
  return read.text;
}

/** §7.1's literal of a typed value: one more `$` before a value that starts with one. */
function literal(value: Typed): string {
  if (typeof value !== "string" || value === "") {
    return refused();
  }
  return value.startsWith("$") ? `$${value}` : value;
}

/** Whether a value is an object (a member of a draft read by its name). */
function isObject(value: unknown): boolean {
  return typeof value === "object" && value !== null;
}

/** Whether a value is a list (a predicate of no type: `Array.isArray` would widen a tuple to `any[]`). */
function isList(value: unknown): boolean {
  return Array.isArray(value);
}

/** A list's length, read once as any value, and refused unless it is a whole number of at least
 * `least` (`NaN`, `undefined`, a text, an object, a negative or a fractional number: never a loop of
 * no turn that sends an empty list). */
function lengthOf(list: { readonly length: number }, least: number): number {
  const length: unknown = list.length;
  if (typeof length !== "number" || !Number.isSafeInteger(length) || length < least) {
    return refused();
  }
  return length;
}

/** A cohort's leaves, written. */
function cohortOf(cohort: CohortDraft | undefined, source: Source): WrittenCohort {
  if (cohort === undefined || !isObject(cohort)) {
    return refused();
  }
  const leaves = cohort.leaves;
  if (!isList(leaves)) {
    return refused();
  }
  const count = lengthOf(leaves, 0);
  const all: WrittenLeaf[] = [];
  for (let index = 0; index < count; index += 1) {
    const leaf = leaves[index];
    if (leaf === undefined || !isObject(leaf)) {
      return refused();
    }
    const column = id(leaf.column, "leaf", source);
    all.push({ kind: "value", column, values: [literal(leaf.value)] });
  }
  return { all };
}

/** The document of a draft (the module's docstring). */
function documentOf(draft: Draft): Written {
  if (!isObject(draft)) {
    return refused();
  }
  const source: Source = { answer: null };
  const chosenUnit = draft.unit;
  const unit = id(chosenUnit, "unit", source);
  const cohorts = draft.cohorts;
  // The type says 1 or 2 cohorts, a draft made past it may say 3 (or `NaN`).
  const many = isList(cohorts) ? lengthOf(cohorts, 1) : refused();
  if (many > 2) {
    return refused();
  }
  const two = many === 2;
  const first = cohortOf(cohorts[0], source);
  const second = two ? cohortOf(cohorts[1], source) : null;
  const view = draft.view;
  if (!isObject(view)) {
    return refused();
  }
  const chosen = view.columns;
  // The length read once (the cohorts' and the leaves' too): a list that answers another length at
  // its next read is read at the first.
  const count = isList(chosen) ? lengthOf(chosen, 1) : refused();
  const columns: { readonly column: string }[] = [];
  for (let index = 0; index < count; index += 1) {
    const column = chosen[index];
    if (column === undefined || !isObject(column)) {
      return refused();
    }
    const text = id(column, "view", source);
    if (UNITS.get(column) !== chosenUnit) {
      return refused();
    }
    columns.push({ column: text });
  }
  const dataset = draft.dataset;
  const options = source.answer === null ? undefined : SOURCES.get(source.answer);
  if (options === undefined || options.dataset === null || typeof dataset !== "string" || dataset !== options.dataset) {
    return refused();
  }
  return {
    aibi: "1",
    dataset: options.dataset,
    unit,
    cohorts: second === null ? { first } : { first, second },
    views: [{ analysis: "summary.distribution", cohorts: two ? ["first", "second"] : ["first"], params: { columns } }],
  };
}

/** `validate_document` of the draft's document. */
export function validateDraft(draft: Draft): Promise<Outcome<Answered<"validate_document">>> {
  return validateDocument({ document: documentOf(draft) });
}

/** `count_cohort` of the draft's document. */
export function countDraft(draft: Draft): Promise<Outcome<Answered<"count_cohort">>> {
  return countCohort({ document: documentOf(draft) });
}

/** `run_analysis` of the draft's document. */
export function runDraft(draft: Draft): Promise<Outcome<Answered<"run_analysis">>> {
  return runAnalysis({ document: documentOf(draft) });
}
