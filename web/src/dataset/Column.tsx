/**
 * The column's screen (`#/datasets/<id>/columns/<column>`; D443, D431; D427's R67): the latest
 * published release of one column, as `describe_column` gives it. One call a mount, through the
 * tab's one queue (`../catalogue/load.ts`): the route makes the call (`describe`, a closure over the
 * dataset's and the column's ids the router module's doors gave) and hands it here with the
 * `DatasetId` alone, so the address's column id is never in this screen's render scope (T4): the
 * loading, failure and refusal words cannot name it, and the heading shows the answer's own
 * `column` through `Ref`. `Column` is the loader, `ColumnDescription` the view of an answer (the
 * position the injection sweeps render). The screen has no stylesheet of its own (the dataset
 * screen's classes, `meta`, `state`, `legend`, are its too, the stylesheet loaded with the
 * dataset's chunk or not at all: nothing here depends on it).
 *
 * Shown: the column's id and label (`Column <id> (<label>)`: the id through `Ref`, the label, data,
 * through `Text`), its table's id and its dataset's (the answer's, through `Ref`; a coverage table's
 * column is shown alike, never looked up), its release and effective disclosure, its caveats, the
 * table's row count and the column's four observation states, each a count with its `stat:`
 * reference (`StatCount`: a suppressed count as suppressed, never 0 or blank), and its distribution:
 * **categories** (each value, data, through `Text`, with its count and reference; the row pooled
 * under the disclosure setting, `pooled`, apart from the categories and never as one; a list
 * column's `multi_membership` said in words), a **histogram** (each bin's edges, a number through
 * `Num` or a date or instant, data, through `Text`, an open end, whose edge is absent, as `−∞` or
 * `+∞`; `[` or `(` and `]` or `)` as `includes_low` and `includes_high` say; where its edges come
 * from in words; the smallest and largest values with their references when the answer has them,
 * never made up from the bins), or **none** (why, in the words of `NONE_WORDS`, a reason the page
 * does not know as its text). A histogram's edges and extremes, and the missing states' counts, are
 * computed by the server from the column's data and its declaration (its declared range, its
 * missing-value codes): the screen shows them as served, and reads none of the answer's `descriptor`.
 * **Not shown, said in fixed words (`NOT_SHOWN`, T8):** the descriptor's member values (its datatype,
 * units, definition, permissible values, missing-value codes, declared range, list syntax and source),
 * which `describe_column` gives, whole or, under a disclosure setting, without its curation evidence;
 * the caveats, which the screen does show, name a member and its curation status (an
 * `UNCONFIRMED_SEMANTICS` caveat quotes `<column>/fields/datatype (proposed)`), and that is the only
 * mention of one. A link goes back to the dataset's screen, by the `DatasetId` the address named.
 *
 * **Disclosure.** The screen shows what the answer serves and computes nothing (T2): no proportion,
 * no total of the categories or the bins, no "other", no minimum from an edge. Under *k* the server
 * suppresses the states as a linked set, shows a distribution only while PRESENT is shown, pools the
 * categories of 1 to *k* − 1 rows (a list column's pooled count is always suppressed) and shows bins
 * from a declared range alone, merged, without a minimum or maximum (D271, D272, D329); each count
 * is shown as served, its reference carrying the floor.
 */
import { type ReactElement, useState, useSyncExternalStore } from "react";

import { type Answered, type DatasetId, isServerNumber, kindOf, type Kind, Num, type Outcome, SHAPE_MISMATCH, UNREACHABLE } from "../api";
import { DEADLINE, loader } from "../catalogue/load";
import { Caveats } from "../layout/Caveat";
import { Disclosure } from "../layout/Disclosure";
import { keyOf } from "../layout/key";
import { Link } from "../layout/Link";
import { Refusals } from "../layout/Refusal";
import { ReleaseRef } from "../layout/ReleaseRef";
import { StatCount } from "../layout/StatCount";
import { Ref, Text } from "../layout/Text";

/** The words while the column loads: fixed, and never its id. */
export const PENDING = "Loading the column…";

/** The words over a refused column: fixed. */
export const REFUSED = "The column could not be described.";

/** The words of a load that threw: fixed, never the error's message. */
export const FAILED = "This screen failed. Return to the dataset or reload the page.";

/** The words of the link back to the dataset's screen: fixed. */
export const BACK = "The dataset";

/** The fixed words of what this screen shows and does not show (T8): its id and label, counts and
 * distribution from the column's data and declaration, and the server's caveats (which name a
 * descriptor field and its curation status), and not the descriptor's member values; and only what is
 * true of `describe_column` (under a disclosure setting it serves the descriptor without its curation
 * evidence, D271). */
export const NOT_SHOWN =
  "The values of the column's descriptor are not shown on this page yet. What this page shows is the column's id and label, counts and a distribution computed from the column's data and its declaration (a histogram's edges may be the column's declared range, and its missing-value codes decide the counts of its missing states), and the server's caveats, which name each field of the descriptor that queries read and an operator has not confirmed, with its curation status. The page does not display the descriptor's member values (datatype, units, definition, permissible values, missing-value codes, declared range, list syntax, source): the caveats' naming of a member and its curation status is the only mention of one. The describe_column tool gives the descriptor in the same answer: whole, or, under a disclosure setting, without its curation evidence.";

/** The words of a list column's categories (`multi_membership`): fixed. */
export const MULTI_MEMBERSHIP = "A row is counted under each value its list holds, so the categories' counts may add up to more than the rows.";

/** The words before the pooled row (`pooled`): fixed. */
export const POOLED = "Categories of fewer rows than the disclosure setting allows, pooled:";

/** The words of a histogram's open ends (an edge the answer does not give). */
export const OPEN = { low: "−∞", high: "+∞" } as const;

/** The words of each reason a column has no distribution (`NoDistribution.reason`). */
export const NONE_WORDS = {
  identifier: "an identifier has no value distribution",
  text: "free text is not counted as categories",
  undeclared: "the column has no declared datatype",
  categories: "it has more distinct values than the categories counted",
  out_of_range: "its values or its declared range are beyond what is counted exactly",
  empty: "it has neither a declared range nor a present value",
  no_declared_range: "under the disclosure setting a histogram's edges come from a declared range alone, and the column declares none",
  suppressed: "suppressed under the disclosure setting",
  unrepresentable: "a value cannot be carried in an answer",
} as const satisfies Record<NonNullable<Kind<"NoDistribution.reason">>, string>;

/** The words of where a histogram's edges come from (`HistogramOut.edges_from`). */
export const EDGES_WORDS = {
  range: "the column's declared range",
  data: "the column's smallest and largest values",
} as const satisfies Record<NonNullable<Kind<"HistogramOut.edges_from">>, string>;

/** The four observation states, in the page's order, each shown by its own name (the answer's key, §6.3). */
const STATES = ["PRESENT", "NOT_APPLICABLE", "NOT_ASSESSED", "UNKNOWN"] as const;

type Described = Answered<"describe_column">;
type Distribution = Described["statistics"]["distribution"];
type Categories = Extract<Distribution, { readonly categories: unknown }>;
type Histogram = Extract<Distribution, { readonly bins: unknown }>;
type None = Extract<Distribution, { readonly reason: unknown }>;
type Bin = Histogram["bins"][number];
type Edge = NonNullable<Bin["low"]>;
type Extreme = NonNullable<Histogram["min"]>;

/** A bin's edge or an extreme: a number through `Num`, a date or an instant (data) through `Text`. */
function EdgeOf({ edge }: { readonly edge: Edge }): ReactElement {
  return isServerNumber(edge) ? <Num value={edge} /> : <Text value={edge.data} />;
}

/** A bin: its interval, closed or open at each end as the answer says, an absent edge as an open
 * end's words, then its count with its reference. */
function BinLine({ bin }: { readonly bin: Bin }): ReactElement {
  return (
    <p role="listitem">
      {bin.includes_low ? "[" : "("}
      {bin.low === undefined ? OPEN.low : <EdgeOf edge={bin.low} />}, {bin.high === undefined ? OPEN.high : <EdgeOf edge={bin.high} />}
      {bin.includes_high ? "]" : ")"}: <StatCount value={bin} />
    </p>
  );
}

/** The smallest or largest value, with its reference. */
function ExtremeLine({ words, value }: { readonly words: "Smallest value" | "Largest value"; readonly value: Extreme }): ReactElement {
  return (
    <p>
      {words}: <EdgeOf edge={value.value} /> <Ref value={value.reference} />
    </p>
  );
}

/** A categorical distribution: each category's value and count, then the pooled row. */
function CategoriesView({ value }: { readonly value: Categories }): ReactElement {
  return (
    <section className="distribution">
      <h2>Categories</h2>
      {value.multi_membership ? <p className="note">{MULTI_MEMBERSHIP}</p> : null}
      <div role="list" aria-label="categories">
        {value.categories.map((category, index) => (
          <p role="listitem" key={keyOf(index)}>
            <Text value={category.value.data} />: <StatCount value={category} />
          </p>
        ))}
      </div>
      {value.pooled === undefined ? null : (
        <p className="pooled">
          {POOLED} <StatCount value={value.pooled} />
        </p>
      )}
    </section>
  );
}

/** A histogram: where its edges come from, its bins, and its extremes when the answer has them. */
function HistogramView({ value }: { readonly value: Histogram }): ReactElement {
  const edges = kindOf(value.edges_from, "HistogramOut.edges_from");
  return (
    <section className="distribution">
      <h2>Histogram</h2>
      <p>Edges from {edges === null ? <Text value={value.edges_from} /> : EDGES_WORDS[edges]}.</p>
      <div role="list" aria-label="bins">
        {value.bins.map((bin, index) => (
          <BinLine key={keyOf(index)} bin={bin} />
        ))}
      </div>
      {value.min === undefined ? null : <ExtremeLine words="Smallest value" value={value.min} />}
      {value.max === undefined ? null : <ExtremeLine words="Largest value" value={value.max} />}
    </section>
  );
}

/** No distribution, and why. */
function NoneView({ value }: { readonly value: None }): ReactElement {
  const reason = kindOf(value.reason, "NoDistribution.reason");
  return <p className="state">No distribution: {reason === null ? <Text value={value.reason} /> : NONE_WORDS[reason]}.</p>;
}

/** The distribution, by its arm (told apart by the member each arm alone has; its `kind` is not shown). */
function DistributionView({ value }: { readonly value: Distribution }): ReactElement {
  if ("categories" in value) {
    return <CategoriesView value={value} />;
  }
  if ("bins" in value) {
    return <HistogramView value={value} />;
  }
  return <NoneView value={value} />;
}

/** An answer of `describe_column`. */
export function ColumnDescription({ answer }: { readonly answer: Described }): ReactElement {
  const { statistics } = answer;
  return (
    <>
      <h1>
        Column <Ref value={answer.column} /> (<Text value={answer.label.data} />)
      </h1>
      <p className="meta">
        Table <Ref value={answer.table} /> of the dataset <Ref value={answer.dataset} />; release <ReleaseRef value={answer.release} />
      </p>
      <Disclosure value={answer.disclosure} />
      <Caveats caveats={answer.caveats} />
      <section className="states">
        <h2>Rows and observation states</h2>
        <p>
          Rows: <StatCount value={statistics.rows} />
        </p>
        <div role="list" aria-label="states">
          {STATES.map((state, index) => (
            <p role="listitem" key={keyOf(index)}>
              {state}: <StatCount value={statistics.states[state]} />
            </p>
          ))}
        </div>
      </section>
      <DistributionView value={statistics.distribution} />
      <p className="legend">{NOT_SHOWN}</p>
    </>
  );
}

/** What a load of the column shows, in each state. */
function State({ outcome }: { readonly outcome: Outcome<Described> }): ReactElement {
  if (outcome.kind === "refused") {
    return (
      <>
        <p className="state">{REFUSED}</p>
        <Refusals refused={outcome} />
      </>
    );
  }
  if (outcome.kind === "invalid") {
    return <p className="state">{outcome.cause === "shape" ? SHAPE_MISMATCH : UNREACHABLE}</p>;
  }
  return <ColumnDescription answer={outcome.value} />;
}

/** The column's screen: one `describe` a mount (the route's call), and a link back to the dataset
 * the address named. */
export function Column({ dataset, describe }: { readonly dataset: DatasetId; readonly describe: () => Promise<Outcome<Described>> }): ReactElement {
  const [store] = useState(() => loader(describe));
  const loaded = useSyncExternalStore(store.subscribe, store.snapshot);
  return (
    <main className="column">
      {loaded.kind === "pending" ? (
        <p className="state">{PENDING}</p>
      ) : loaded.kind === "failed" ? (
        <p className="state">{FAILED}</p>
      ) : loaded.kind === "deadline" ? (
        <p className="state">{DEADLINE}</p>
      ) : (
        <State outcome={loaded.value} />
      )}
      <p className="meta">
        <Link to={dataset}>{BACK}</Link>
      </p>
    </main>
  );
}
