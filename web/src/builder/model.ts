/**
 * The builder's form as data (D433): a pure helper of `Builder.tsx`, no React and no text. A `State`
 * holds the unit picked, one or two cohorts of conditions (a value leaf each: a column picked and
 * what the person typed) and the view's columns picked. Every row (a cohort, a condition, a view
 * column) carries a `Key` this module mints (`keyOf(next)`), never its index, so that a screen keys
 * each row by it and a removal leaves every other row's controls in place. A pick is the option's
 * index in the list it was drawn from and the option itself (an `optionsOf` option: its choice is
 * what the seam writes). Every edit gives a new `State`, addressing its row by key; an edit that
 * would break a bound (a fifth condition or view column, a third cohort, no cohort or no view
 * column) gives the state unchanged. Changing the unit clears exactly the view picks that are not of
 * the new unit's options. `draftOf` gives the seam's `Draft`, or `null` while a pick is missing or a
 * value is empty (the screen never sends an empty value, D432).
 */
import type { Choice, CohortDraft, Draft, LeafDraft, Option, Options, UnitOption } from "../api";
import { type Typed, UNTYPED } from "../layout/Field";
import { type Key, keyOf } from "../layout/key";

/** An option picked: its index in its list, and the option. */
export interface Pick<O> {
  readonly at: Key;
  readonly option: O;
}

/** A condition: a value leaf's column, and what the person typed. */
export interface LeafState {
  readonly key: Key;
  readonly column: Pick<Option<"leaf">> | null;
  readonly value: Typed;
}

/** A cohort: its conditions. */
export interface CohortState {
  readonly key: Key;
  readonly leaves: readonly LeafState[];
}

/** A column of the view. */
export interface ColumnState {
  readonly key: Key;
  readonly column: Pick<Option<"view">> | null;
}

/** The form. */
export interface State {
  readonly next: Key;
  readonly unit: Pick<UnitOption> | null;
  readonly cohorts: readonly [CohortState] | readonly [CohortState, CohortState];
  readonly columns: readonly ColumnState[];
}

/** The most conditions a cohort holds, and view columns the view holds, in this form. */
export const MAX_ROWS = 4;

/** The form before any edit: no unit, one cohort with no condition, one view column not picked. */
const START: State = { next: keyOf(2), unit: null, cohorts: [{ key: keyOf(0), leaves: [] }], columns: [{ key: keyOf(1), column: null }] };

/** The form before any edit (frozen). */
export const EMPTY: State = Object.freeze(START);

/** What a list's `selectedIndex` picks of `list` (its first option, `NoOption`, picks nothing). */
export function pickOf<O>(list: readonly O[], selected: number): Pick<O> | null {
  const at = selected - 1;
  const option = at >= 0 && at < list.length ? list[at] : undefined;
  return option === undefined ? null : { at: keyOf(at), option };
}

/** The state with each cohort replaced by `edit`'s. */
function cohortsWith(state: State, edit: (cohort: CohortState) => CohortState): State {
  const [first, second] = state.cohorts;
  return { ...state, cohorts: second === undefined ? [edit(first)] : [edit(first), edit(second)] };
}

/** The state with the condition `leaf` of the cohort `cohort` replaced by `edit`'s. */
function leafWith(state: State, cohort: Key, leaf: Key, edit: (one: LeafState) => LeafState): State {
  return cohortsWith(state, (one) => (one.key === cohort ? { ...one, leaves: one.leaves.map((found) => (found.key === leaf ? edit(found) : found)) } : one));
}

/** The unit picked; the view picks not of its options cleared. */
export function withUnit(state: State, unit: Pick<UnitOption> | null): State {
  const view: readonly Option<"view">[] = unit === null ? [] : unit.option.view;
  return {
    ...state,
    unit,
    columns: state.columns.map((one) => (one.column === null || view.includes(one.column.option) ? one : { key: one.key, column: null })),
  };
}

/** A condition's column picked. */
export function withLeafColumn(state: State, cohort: Key, leaf: Key, column: Pick<Option<"leaf">> | null): State {
  return leafWith(state, cohort, leaf, (one) => ({ ...one, column }));
}

/** A condition's value typed. */
export function withLeafValue(state: State, cohort: Key, leaf: Key, value: Typed): State {
  return leafWith(state, cohort, leaf, (one) => ({ ...one, value }));
}

/** A condition added at the end of a cohort (at most `MAX_ROWS`). */
export function addLeaf(state: State, cohort: Key): State {
  const target = state.cohorts.find((one) => one.key === cohort);
  if (target === undefined || target.leaves.length >= MAX_ROWS) {
    return state;
  }
  const added = cohortsWith(state, (one) => (one.key === cohort ? { ...one, leaves: [...one.leaves, { key: state.next, column: null, value: UNTYPED }] } : one));
  return { ...added, next: keyOf(state.next + 1) };
}

/** A condition removed. */
export function removeLeaf(state: State, cohort: Key, leaf: Key): State {
  return cohortsWith(state, (one) => (one.key === cohort ? { ...one, leaves: one.leaves.filter((found) => found.key !== leaf) } : one));
}

/** A second cohort added, with no condition. */
export function addCohort(state: State): State {
  const [first, second] = state.cohorts;
  return second === undefined ? { ...state, cohorts: [first, { key: state.next, leaves: [] }], next: keyOf(state.next + 1) } : state;
}

/** A cohort removed, when there are two (the other stays). */
export function removeCohort(state: State, cohort: Key): State {
  const [first, second] = state.cohorts;
  if (second === undefined) {
    return state;
  }
  return first.key === cohort ? { ...state, cohorts: [second] } : second.key === cohort ? { ...state, cohorts: [first] } : state;
}

/** A view column picked. */
export function withColumn(state: State, column: Key, pick: Pick<Option<"view">> | null): State {
  return { ...state, columns: state.columns.map((one) => (one.key === column ? { key: one.key, column: pick } : one)) };
}

/** A view column added at the end (at most `MAX_ROWS`). */
export function addColumn(state: State): State {
  return state.columns.length >= MAX_ROWS ? state : { ...state, columns: [...state.columns, { key: state.next, column: null }], next: keyOf(state.next + 1) };
}

/** A view column removed, when there are two or more. */
export function removeColumn(state: State, column: Key): State {
  return state.columns.length <= 1 ? state : { ...state, columns: state.columns.filter((one) => one.key !== column) };
}

/** What keeps the form from a draft: a pick missing, a value empty, or nothing. */
export type Problem = "incomplete" | "empty" | null;

/** What keeps the form from a draft (a missing pick before an empty value). */
export function problemOf(state: State): Problem {
  const leaves = state.cohorts.flatMap((cohort) => cohort.leaves);
  if (state.unit === null || state.columns.some((one) => one.column === null) || leaves.some((leaf) => leaf.column === null)) {
    return "incomplete";
  }
  return leaves.some((leaf) => leaf.value.length === 0) ? "empty" : null;
}

/** A cohort's draft, or `null` while a pick is missing or a value empty. */
function cohortDraft(cohort: CohortState): CohortDraft | null {
  const leaves: LeafDraft[] = [];
  for (const leaf of cohort.leaves) {
    if (leaf.column === null || leaf.value.length === 0) {
      return null;
    }
    leaves.push({ column: leaf.column.option.choice, value: leaf.value });
  }
  return { leaves };
}

/** The seam's draft of the form, or `null` while a pick is missing or a value empty. */
export function draftOf(options: Options, state: State): Draft | null {
  const dataset = options.dataset;
  const unit = state.unit;
  if (dataset === null || unit === null || problemOf(state) !== null) {
    return null;
  }
  const [firstState, secondState] = state.cohorts;
  const first = cohortDraft(firstState);
  const second = secondState === undefined ? null : cohortDraft(secondState);
  if (first === null || (secondState !== undefined && second === null)) {
    return null;
  }
  const chosen: Choice<"view">[] = [];
  for (const one of state.columns) {
    if (one.column === null || !unit.option.view.includes(one.column.option)) {
      return null;
    }
    chosen.push(one.column.option.choice);
  }
  const [head, ...rest] = chosen;
  if (head === undefined) {
    return null;
  }
  return { dataset, unit: unit.option.choice, cohorts: second === null ? [first] : [first, second], view: { columns: [head, ...rest] } };
}
