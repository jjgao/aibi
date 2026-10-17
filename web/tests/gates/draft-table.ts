/**
 * The checked-in table of the document seam's surface gate (`draft-gate.ts`, D432): every path of a
 * `Draft` a screen hands `validateDraft`, `countDraft` and `runDraft`, from their one parameter
 * (`#0`), and the kind found there. A change to `Draft` changes this table in review, or fails the
 * gate. The union's members are numbered in the order of their kinds (never the checker's ids).
 *
 * | path | kind |
 * |---|---|
 * | `dataset` | `DatasetId` (`src/api/text.tsx`) |
 * | `unit` | `Choice<"unit">` |
 * | `cohorts` | `readonly [CohortDraft] \| readonly [CohortDraft, CohortDraft]`: one or two |
 * | `cohorts[i].leaves` | a `readonly` list of `{column, value}` |
 * | `leaves[].column` | `Choice<"leaf">` |
 * | `leaves[].value` | `Typed` (`src/layout/Field.tsx`) |
 * | `view.columns` | `readonly [Choice<"view">, ...Choice<"view">[]]`: one or more |
 */
import { DRAFT_ROOTS, OPTIONS_ROOT } from "./draft-gate";

/** A cohort's rows, at a path. */
const cohort = (at: string): string[] => [
  `${at} -> {leaves}`,
  `${at}.leaves -> readonly list`,
  `${at}.leaves[] -> {column,value}`,
  `${at}.leaves[].column -> Choice<leaf>`,
  `${at}.leaves[].value -> Typed`,
];

/** The rows of a `Draft`, from its root's parameter. */
export const DRAFT_TABLE: readonly string[] = [
  " -> {cohorts,dataset,unit,view}",
  ".cohorts -> union of 2",
  ".cohorts|0 -> readonly tuple [R,R]",
  ...cohort(".cohorts|0[0]"),
  ...cohort(".cohorts|0[1]"),
  ".cohorts|1 -> readonly tuple [R]",
  ...cohort(".cohorts|1[0]"),
  ".dataset -> DatasetId",
  ".unit -> Choice<unit>",
  ".view -> {columns}",
  ".view.columns -> readonly tuple [R,S]",
  ".view.columns[0] -> Choice<view>",
  ".view.columns[1] -> Choice<view>",
];

/** The whole table the gate must find: each `*Draft` root's rows, and `optionsOf`'s pin. */
export function expectedTable(): string[] {
  return [
    ...DRAFT_ROOTS.flatMap((root) => [`${root} -> 1 parameters`, ...DRAFT_TABLE.map((row) => `${root}(#0)${row}`)]),
    `${OPTIONS_ROOT} -> 1 parameters`,
    `${OPTIONS_ROOT}(#0) -> describeDataset's ok value`,
  ].sort();
}

/** The roots the gate must find: every value export of `document.ts`. */
export const ROOTS: readonly string[] = [...DRAFT_ROOTS, OPTIONS_ROOT].sort();
