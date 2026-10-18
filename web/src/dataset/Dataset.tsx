/**
 * The dataset's screen (`#/datasets/<id>`; D431, D311; D427's R63, R64 and R68a): the latest
 * published release of one dataset, as `describe_dataset` gives it for `{ dataset: id }` (nothing
 * else is sent: the columns' paging takes the server's default). One call a load, through the tab's
 * one queue (`../catalogue/load.ts`). The id comes from the address through the router module's door
 * (`idOfAddress`, R08), goes into the request's body and is never rendered: the loading words do not
 * name it, and the heading shows the answer's own `dataset` through `Ref`. `Dataset` is the loader,
 * `Description` the view of an answer (the position the injection sweeps render).
 *
 * Shown: the dataset's id, its release and effective disclosure, its standing caveats, the
 * applicable analyses (worded as R45 words them: status in words, `missing` and `unconfirmed`
 * listed whenever not empty, whatever the status), how many columns it has and where the columns
 * not on this page start, the table graph (`Graph`) and the tables (`Tables`). **Not shown, said in
 * fixed words (`MEMBERS_NOT_SHOWN`):** the descriptors' other members (M5.2-rest), which the server's own
 * page shows when it runs without a web bundle, and `describe_dataset` gives whole. Under the
 * description stands the builder (D433), given the options `optionsOf` mints from the same answer;
 * the route keys this screen by its `DatasetId`, so another dataset's screen is a new builder.
 */
import "./dataset.css";

import { Fragment, type ReactElement, useState, useSyncExternalStore } from "react";

import { type Answered, type DatasetId, describeDataset, kindOf, Num, optionsOf, type Outcome, SHAPE_MISMATCH, UNREACHABLE } from "../api";
import { Builder } from "../builder/Builder";
import { DEADLINE, loader } from "../catalogue/load";
import { Caveats } from "../layout/Caveat";
import { Disclosure } from "../layout/Disclosure";
import { keyOf } from "../layout/key";
import { Refusals } from "../layout/Refusal";
import { ReleaseRef } from "../layout/ReleaseRef";
import { Ref, Text } from "../layout/Text";
import { Graph } from "./Graph";
import { Tables } from "./Tables";

/** The words while the dataset loads: fixed, and never its id. */
export const PENDING = "Loading the dataset…";

/** The words over a refused dataset: fixed. */
export const REFUSED = "The dataset could not be described.";

/** The words of a load that threw: fixed, never the error's message. */
export const FAILED = "This screen failed. Return to the catalogue or reload the page.";

/** The words of a dataset with no applicable analysis: fixed (an empty list of the server is said,
 * never shown as blank). */
export const NO_ANALYSIS = "No analysis.";

/** The words before an applicable analysis's status the page does not know: fixed. */
export const UNKNOWN_STATUS = "status not known to this page:";

/** The fixed words of what this page does not show yet (T8), around the dataset's id. */
export const MEMBERS_NOT_SHOWN = [
  "Other descriptor members (beyond the ids, labels, datatypes and identifier flags of the columns and the graph below), among them the dataset's and the tables' labels, definitions and provenance, the dataset descriptor's name, packs and source, the tables' grains, roles, primary keys and sources, the columns' units, each descriptor's kind and version, each member's curation status and who set it, the extensions, the relationships' child and parent columns, and the coverage and endpoint descriptors, are not shown on this page yet. aibi's server-rendered page at /datasets/",
  " shows them when a server runs without a web bundle (its default until M5.2-rest, D410), and the describe_dataset tool gives them whole.",
] as const;

/** The words of each applicable analysis's status (R45's). */
const STATUS = { available: "available", unavailable: "unavailable", available_with_caveats: "available with caveats" } as const;

type Described = Answered<"describe_dataset">;
type Applicable = Described["applicable_analyses"][number];

/** The texts of a list, each through `Ref`, comma-separated. */
function Refs({ values }: { readonly values: Applicable["missing"] }): ReactElement {
  return (
    <span role="list">
      {values.map((value, index) => (
        <Fragment key={keyOf(index)}>
          {index === 0 ? null : ", "}
          <span role="listitem">
            <Ref value={value} />
          </span>
        </Fragment>
      ))}
    </span>
  );
}

/** One applicable analysis: its id and version, its status in words, what it misses and what is
 * unconfirmed, whatever the status. */
function Analysis({ value }: { readonly value: Applicable }): ReactElement {
  const status = kindOf(value.status, "ApplicableAnalysis.status");
  return (
    <p role="listitem">
      <Ref value={value.analysis} /> version <Ref value={value.version} />:{" "}
      {status === null ? (
        <>
          {UNKNOWN_STATUS} <Text value={value.status} />
        </>
      ) : (
        STATUS[status]
      )}
      {value.missing.length === 0 ? null : (
        <>
          ; missing: <Refs values={value.missing} />
        </>
      )}
      {value.unconfirmed.length === 0 ? null : (
        <>
          ; unconfirmed: <Refs values={value.unconfirmed} />
        </>
      )}
    </p>
  );
}

/** An answer of `describe_dataset`. */
export function Description({ answer }: { readonly answer: Described }): ReactElement {
  return (
    <>
      <h1>
        Dataset <Ref value={answer.dataset} />
      </h1>
      <p className="meta">
        Release <ReleaseRef value={answer.release} />
      </p>
      <Disclosure value={answer.disclosure} />
      <Caveats caveats={answer.caveats} />
      <section className="analyses">
        <h2>Applicable analyses</h2>
        {answer.applicable_analyses.length === 0 ? (
          <p className="state">{NO_ANALYSIS}</p>
        ) : (
          <div role="list" aria-label="applicable analyses">
            {answer.applicable_analyses.map((one, index) => (
              <Analysis key={keyOf(index)} value={one} />
            ))}
          </div>
        )}
      </section>
      <p>
        Columns: <Num value={answer.columns_total} />
        {answer.columns_next === undefined ? null : (
          <>
            ; the columns from position <Num value={answer.columns_next} /> on are not on this page.
          </>
        )}
      </p>
      <p className="legend">
        {MEMBERS_NOT_SHOWN[0]}
        <Ref value={answer.dataset} />
        {MEMBERS_NOT_SHOWN[1]}
      </p>
      <Graph graph={answer.graph} />
      <Tables tables={answer.tables} />
    </>
  );
}

/** What a load of the dataset shows, in each state. */
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
  return (
    <>
      <Description answer={outcome.value} />
      <Builder options={optionsOf(outcome.value)} />
    </>
  );
}

/** The dataset's screen: one `describe_dataset` a mount, for the id the address named. */
export function Dataset({ id }: { readonly id: DatasetId }): ReactElement {
  const [store] = useState(() => loader(() => describeDataset({ dataset: id })));
  const loaded = useSyncExternalStore(store.subscribe, store.snapshot);
  return (
    <main className="dataset">
      {loaded.kind === "pending" ? (
        <p className="state">{PENDING}</p>
      ) : loaded.kind === "failed" ? (
        <p className="state">{FAILED}</p>
      ) : loaded.kind === "deadline" ? (
        <p className="state">{DEADLINE}</p>
      ) : (
        <State outcome={loaded.value} />
      )}
    </main>
  );
}
