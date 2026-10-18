/**
 * One position's values (D435; D427's R34a; §8.1, §9.1, D434): the columns `valuesOf` read, each
 * under its label (the canonical document's column, through `Text`) and by its own `kind`:
 *
 * - **numbers**, with no disclosure setting and no bin index: the statistics, each under its member's
 *   name through `Figure` (a `null` says why, from the arm's `not_estimable` at its own pointer), by
 *   `STATISTICS`, whose key set is the vocabulary's pointers less the three bin indices (a pointer
 *   left out, or a bin index added, fails to compile); then the histogram, or why it has none:
 *   `edges_from` as the server's word (a data token, not explained) and each bin's edges, its two
 *   `includes_*` members as sent and its count, a `null` edge in fixed words.
 * - **numbers under the disclosure setting or carrying a bin index** (`deferred`): fixed words alone,
 *   nothing of the arm read (R34b, M5.4-rest).
 * - **categories**: why there are none (`null`), fixed words for an empty list, or each row: its
 *   values as sent (`Text` each), the member name `other_values` where the row has it, and its
 *   `Proportion`; a row that names no value and is no `other_values` row is shown in fail-closed
 *   words, nothing of its proportion.
 * - **another kind** (`category_rows`, `number_rows`, `memberships`): the kind as a data token, in
 *   fixed words (R29, M5.4-rest).
 *
 * `Unread` shows the fixed words of a read the page did not make. Nothing here makes a number.
 */
import { Fragment, type ReactElement } from "react";

import { type Boxed, type ColumnRead, type components, type Kind, Num, type ServerNumber, type ServerText } from "../api";
import { Figure } from "../layout/Figure";
import { keyOf } from "../layout/key";
import { Proportion } from "../layout/Proportion";
import { Ref, Text } from "../layout/Text";

type Numbers = Boxed<components["schemas"]["NumberDistribution"]>;
type Categories = Boxed<components["schemas"]["CategoryDistribution"]>;
type Row = NonNullable<Categories["categories"]>[number];
type Bin = NonNullable<Numbers["histogram"]>["bins"][number];

/** The fixed words of the values. */
export const VALUE_WORDS = {
  deferred: "This column's numbers are not shown on this page yet (R34b).",
  empty: "This column lists no category row for this position.",
  unnamed: "A row that names no category: not shown (the page and the server disagree).",
  other: "Values of this kind are not shown on this page yet (R29):",
  noLow: "no lower edge",
  noHigh: "no upper edge",
  noLabel: "This column's label is not listed.",
  analysis: "This page does not read the values of this analysis and version yet.",
  shape: "The values are not shown: the page and the server disagree about their shape.",
} as const;

/** A pointer of a numbers arm's `not_estimable` that this page reads: every one but the bin indices. */
type Statistic = Exclude<NonNullable<Kind<"NumberDistribution.not_estimable{key}">>, "/q1_bin" | "/median_bin" | "/q3_bin">;

/** The statistics of a numbers arm, by pointer: each its member's name and its reader; the histogram
 * is shown apart. */
export const STATISTICS = {
  "/n": { name: "n", of: (arm: Numbers) => arm.n },
  "/mean": { name: "mean", of: (arm: Numbers) => arm.mean },
  "/sd": { name: "sd", of: (arm: Numbers) => arm.sd },
  "/median": { name: "median", of: (arm: Numbers) => arm.median },
  "/q1": { name: "q1", of: (arm: Numbers) => arm.q1 },
  "/q3": { name: "q3", of: (arm: Numbers) => arm.q3 },
  "/min": { name: "min", of: (arm: Numbers) => arm.min },
  "/max": { name: "max", of: (arm: Numbers) => arm.max },
  "/histogram": { name: "histogram", of: null },
} as const satisfies Record<Statistic, { readonly name: string; readonly of: ((arm: Numbers) => ServerNumber | null) | null }>;

/** The numbered statistics, in the order shown. */
const NUMBERED = ["/n", "/mean", "/sd", "/median", "/q1", "/q3", "/min", "/max"] as const satisfies readonly Statistic[];

/** Whether a numbers arm is not read on this page (R34b): the result is under a disclosure setting,
 * or the arm carries any bin index (`q1_bin`, `median_bin`, `q3_bin`; present, `null` or not). */
export function deferred(disclosure: { readonly min_cell_count: ServerNumber | null }, arm: Numbers): boolean {
  return disclosure.min_cell_count !== null || arm.q1_bin !== undefined || arm.median_bin !== undefined || arm.q3_bin !== undefined;
}

/** One bin: its edges (a `null` edge in fixed words), its `includes_*` members as sent and its count. */
function BinOf({ bin }: { readonly bin: Bin }): ReactElement {
  return (
    <div role="listitem" className="bin">
      low {bin.low === null ? <span className="open">{VALUE_WORDS.noLow}</span> : <Num value={bin.low} />}, high{" "}
      {bin.high === null ? <span className="open">{VALUE_WORDS.noHigh}</span> : <Num value={bin.high} />}, includes_low {bin.includes_low ? "true" : "false"}, includes_high{" "}
      {bin.includes_high ? "true" : "false"}, count <Num value={bin.count} />
    </div>
  );
}

/** A numbers arm read: its statistics and its histogram. */
function NumbersOf({ arm }: { readonly arm: Numbers }): ReactElement {
  const histogram = arm.histogram;
  return (
    <div className="numbers">
      <p>
        {NUMBERED.map((at, index) => (
          <Fragment key={keyOf(index)}>
            {index === 0 ? null : "; "}
            {STATISTICS[at].name} <Figure value={STATISTICS[at].of(arm)} notEstimable={arm.not_estimable} at={at} />
          </Fragment>
        ))}
      </p>
      {histogram === null ? (
        <p>
          {STATISTICS["/histogram"].name} <Figure value={null} notEstimable={arm.not_estimable} at="/histogram" />
        </p>
      ) : (
        <>
          <p>
            {STATISTICS["/histogram"].name}: edges from <Ref value={histogram.edges_from} token />
          </p>
          <div role="list" className="bins">
            {histogram.bins.map((bin, index) => (
              <BinOf key={keyOf(index)} bin={bin} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** One category row (the module's docstring). */
function RowOf({ row }: { readonly row: Row }): ReactElement {
  const other = row.other_values === true;
  if (!other && row.values.length === 0) {
    return (
      <div role="listitem" className="unnamed">
        {VALUE_WORDS.unnamed}
      </div>
    );
  }
  return (
    <div role="listitem" className="category">
      {other ? <span className="member">other_values</span> : null}
      {row.values.map((one, index) => (
        <Fragment key={keyOf(index)}>
          {index === 0 && !other ? null : ", "}
          <Text value={one.data} />
        </Fragment>
      ))}
      : <Proportion value={row.proportion} />
    </div>
  );
}

/** A categories arm read. */
function CategoriesOf({ arm }: { readonly arm: Categories }): ReactElement {
  const rows = arm.categories;
  if (rows === null) {
    return (
      <p>
        categories <Figure value={null} notEstimable={arm.not_estimable} at="/categories" />
      </p>
    );
  }
  return rows.length === 0 ? (
    <p>{VALUE_WORDS.empty}</p>
  ) : (
    <div role="list" className="categories">
      {rows.map((row, index) => (
        <RowOf key={keyOf(index)} row={row} />
      ))}
    </div>
  );
}

/** One column, by its arm. */
function ColumnOf({ column, disclosure }: { readonly column: ColumnRead; readonly disclosure: { readonly min_cell_count: ServerNumber | null } }): ReactElement {
  switch (column.kind) {
    case "numbers":
      return deferred(disclosure, column.value) ? <p className="deferred">{VALUE_WORDS.deferred}</p> : <NumbersOf arm={column.value} />;
    case "categories":
      return <CategoriesOf arm={column.value} />;
    case "other":
      return (
        <p className="deferred">
          {VALUE_WORDS.other} <Ref value={column.name} token />
        </p>
      );
  }
}

/** One position's columns, each under its label. */
export function Values({ columns, labels, disclosure }: { readonly columns: readonly ColumnRead[]; readonly labels: readonly ServerText[]; readonly disclosure: { readonly min_cell_count: ServerNumber | null } }): ReactElement {
  return (
    <div className="values" aria-label="values">
      {columns.map((column, index) => {
        const label = labels[index];
        return (
          <section key={keyOf(index)} className="column" aria-label="column">
            <p className="label">{label === undefined ? VALUE_WORDS.noLabel : <Text value={label} />}</p>
            <ColumnOf column={column} disclosure={disclosure} />
          </section>
        );
      })}
    </div>
  );
}

/** The words of a read the page did not make. */
export function Unread({ cause }: { readonly cause: "analysis" | "shape" }): ReactElement {
  return <p className="state">{cause === "analysis" ? VALUE_WORDS.analysis : VALUE_WORDS.shape}</p>;
}
