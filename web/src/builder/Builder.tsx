/**
 * The builder-lite screens (D433; D427's R41 and R49a; §7.7, A2, A3, §8.3): under the dataset's
 * description, a form that builds one document of the document seam's (D432) template, and three
 * buttons that send it, each press one call through the tab's one queue (D431's `loader`).
 *
 * - **The form** (`model.ts`): a unit table, one or two cohorts (`first`, `second`) of conditions,
 *   each a column and a typed value (`Field`), and the columns of one `summary.distribution` view.
 *   Every list is drawn from `optionsOf`' options, each shown as its id, then ` — ` and its label
 *   when it has one (D441: `Option`'s `label` and `name`), every `<select>` is controlled (its value
 *   the pick's index, or `NoOption`'s `""`), every row keyed by the id the model minted. Fixed words
 *   say what the lists leave out, when they do.
 * - **The buttons.** Validate is enabled while the form gives a draft and no builder call is
 *   pending; Count and Run while the validation shown is of the form as it stands, done, valid and
 *   nothing pending; they send the validated draft. Each press makes its store in the `onClick`
 *   handler; nothing is sent on load and nothing is sent again by itself. After a deadline the
 *   buttons are enabled again while the overdue call keeps its place in the queue (D431: at most two
 *   outstanding, a third waits).
 * - **The outcome views** (validation, count, run): each carries the state it was asked about, and
 *   stands under fixed words whenever the form has been edited since (by edit, conservative: an edit
 *   that restores the same document still marks it). A failure and a deadline have words of the
 *   builder's own: a press asks again (a reload would lose the form). Each shows what the
 *   answer gives as data: names, ids with their status (`IdStatus`), releases, disclosure, caveats
 *   (under the banner when one blocks: R49a's codes are shown wherever the answers carry them),
 *   readbacks (`Readback`, R41) and cohort counts (`Counted`); refusals through `Refusal`. **Not
 *   shown** (each with its reason in D433): a cohort's and a view's `form`, `computation_id`,
 *   `leaves`, `unit`, `packs` and `position`; the validation's and the count's `params` and the
 *   `translation` (fixed words when present); the count's `views` (the validation shows them) and
 *   its `size`, `digest` and `issuance`. A run's answer is the result screen's (D435): its
 *   parameters once (`Parameters`) and each result in place (`Result`), kept under the stale words
 *   after an edit until the next run.
 */
import { type ReactElement, useState, useSyncExternalStore } from "react";

import { type Answered, countDraft, type Draft, type Options, type Outcome, runDraft, SHAPE_MISMATCH, UNREACHABLE, validateDraft } from "../api";
import { type Loaded, loader, type Store } from "../catalogue/load";
import { Inspection } from "../layout/Caveat";
import { Disclosure } from "../layout/Disclosure";
import { Field } from "../layout/Field";
import { keyOf } from "../layout/key";
import { NoOption, Option } from "../layout/Option";
import { Counted } from "../layout/Population";
import { Readback } from "../layout/Readback";
import { Refusal, Refusals } from "../layout/Refusal";
import { ReleaseRef } from "../layout/ReleaseRef";
import { Ref } from "../layout/Text";
import { Result } from "../result/Figures";
import { Parameters } from "../result/Provenance";
import { IdStatus } from "./Ids";
import {
  addCohort,
  addColumn,
  addLeaf,
  draftOf,
  EMPTY,
  MAX_ROWS,
  pickOf,
  problemOf,
  removeCohort,
  removeColumn,
  removeLeaf,
  type State,
  withColumn,
  withLeafColumn,
  withLeafValue,
  withUnit,
} from "./model";

/** The fixed words of the screen. */
export const WORDS = {
  heading: "Build an analysis",
  noDataset: "This dataset's id cannot be sent in a document: the builder is not offered.",
  leaves: "Conditions are offered on columns of text and of categories only.",
  view: "Only the unit table's columns of categories, booleans, numbers and time offsets are offered for the distribution.",
  underK: "Under this dataset's disclosure setting only the unit table's columns of categories and booleans are offered for the distribution.",
  partial: "Only the columns of this page of the dataset's description are offered.",
  tables: "A table whose id this page cannot send is not offered.",
  oneLine: "One line.",
  noCondition: "No condition.",
  chooseUnit: "Choose a unit table first.",
  incomplete: "Pick a unit, a column for each condition and each analysis column, and type each condition's value.",
  empty: "A condition needs a value.",
  notValid: "Validate the document to count its cohorts or run it.",
  pending: "Waiting for the server…",
  failed: "The request failed. Press the button again to ask again.",
  deadline: "No answer came within 45 seconds; the server may still be working on this or an earlier request. Press the button again to ask again.",
  stale: "The form has been edited since this was asked; Validate again.",
  valid: "The document is valid.",
  invalid: "The document is not valid.",
  problems: "The server found these problems in the document:",
  translation: "The answer holds a translation of the document, which is not shown here.",
} as const;

/** A call asked: its store, the form it was asked about and the draft it sent (`null`: not asked). */
interface Asked<T> {
  readonly store: Store<Outcome<T>>;
  readonly state: State | null;
  readonly draft: Draft | null;
}

/** What the idle store gives: one frozen constant, read only where a call was asked. */
const NOT_ASKED: Loaded<never> = Object.freeze({ kind: "pending" });

/** The store of a call not asked: it sends nothing and never changes. */
export const IDLE: Store<never> = Object.freeze({
  subscribe: () => () => undefined,
  snapshot: () => NOT_ASKED,
});

const IDLE_ASKED: Asked<never> = Object.freeze({ store: IDLE, state: null, draft: null });

type Validated = Answered<"validate_document">;
type Counts = Answered<"count_cohort">;
type Ran = Answered<"run_analysis">;

/** Whether an asked call waits for its answer. */
function pending<T>(asked: Asked<T>, loaded: Loaded<Outcome<T>>): boolean {
  return asked.state !== null && loaded.kind === "pending";
}

/** An asked call's words while it has no answer (pending, failed, past its deadline). */
function Waiting({ loaded }: { readonly loaded: Exclude<Loaded<unknown>, { readonly kind: "done" }> }): ReactElement {
  return <p className="state">{loaded.kind === "pending" ? WORDS.pending : loaded.kind === "failed" ? WORDS.failed : WORDS.deadline}</p>;
}

/** An answer that is no answer of the operation: its refusals, or why it was not read. */
function NotAnswered({ outcome }: { readonly outcome: Exclude<Outcome<unknown>, { readonly kind: "ok" }> }): ReactElement {
  return outcome.kind === "refused" ? <Refusals refused={outcome} /> : <p className="state">{outcome.cause === "shape" ? SHAPE_MISMATCH : UNREACHABLE}</p>;
}

/** The stale words, when the form no longer shows the document a call was asked about. */
function Stale({ asked, current }: { readonly asked: State; readonly current: State }): ReactElement | null {
  return asked === current ? null : <p className="stale">{WORDS.stale}</p>;
}

/** A validation's answer. */
export function ValidationOf({ value }: { readonly value: Validated }): ReactElement {
  return (
    <>
      <p className="verdict">{value.valid ? WORDS.valid : WORDS.invalid}</p>
      {value.refusals.length === 0 ? null : (
        <>
          <p>{WORDS.problems}</p>
          <div role="list">
            {value.refusals.map((refusal, index) => (
              <Refusal key={keyOf(index)} value={refusal} />
            ))}
          </div>
        </>
      )}
      {value.translation === undefined ? null : <p className="note">{WORDS.translation}</p>}
      {value.cohorts.map((cohort, index) => (
        <section key={keyOf(index)} className="checked" aria-label="cohort">
          <p>
            Cohort <Ref value={cohort.cohort.data} />. <IdStatus id={cohort.id} status={cohort.status} />
          </p>
          <p className="meta">
            Release <ReleaseRef value={cohort.release} />
          </p>
          <Disclosure value={cohort.disclosure} />
          <Inspection caveats={cohort.caveats}>
            <Readback value={cohort.readback} />
          </Inspection>
        </section>
      ))}
      {value.views.map((view, index) => (
        <section key={keyOf(index)} className="checked" aria-label="view">
          <p>
            View <Ref value={view.analysis.id} /> version <Ref value={view.analysis.version} />. <IdStatus id={view.id} status={view.status} />
          </p>
          <Inspection caveats={view.caveats}>
            <Readback value={view.readback} />
          </Inspection>
        </section>
      ))}
    </>
  );
}

/** A count's answer. */
export function CountsOf({ value }: { readonly value: Counts }): ReactElement {
  return (
    <>
      {value.counts.map((named, index) => (
        <section key={keyOf(index)} className="checked" aria-label="count">
          <p>
            Cohort <Ref value={named.cohort.data} />
          </p>
          <Counted value={named.count} />
        </section>
      ))}
    </>
  );
}

/** A run's answer: its parameters, once, then each result as the result screen shows it (D435). */
export function RunOf({ value }: { readonly value: Ran }): ReactElement {
  return (
    <>
      <Parameters params={value.params} />
      {value.results.map((result, index) => (
        <section key={keyOf(index)} className="checked" aria-label="result">
          <Result result={result} />
        </section>
      ))}
    </>
  );
}

/** The validation's view (nothing before Validate is pressed). */
function ValidationView({ asked, loaded, current }: { readonly asked: Asked<Validated>; readonly loaded: Loaded<Outcome<Validated>>; readonly current: State }): ReactElement | null {
  return asked.state === null ? null : (
    <section className="outcome" aria-label="validation">
      <h3>Validation</h3>
      <Stale asked={asked.state} current={current} />
      {loaded.kind !== "done" ? <Waiting loaded={loaded} /> : loaded.value.kind === "ok" ? <ValidationOf value={loaded.value.value} /> : <NotAnswered outcome={loaded.value} />}
    </section>
  );
}

/** The count's view (nothing before Count is pressed). */
function CountView({ asked, loaded, current }: { readonly asked: Asked<Counts>; readonly loaded: Loaded<Outcome<Counts>>; readonly current: State }): ReactElement | null {
  return asked.state === null ? null : (
    <section className="outcome" aria-label="count">
      <h3>Count</h3>
      <Stale asked={asked.state} current={current} />
      {loaded.kind !== "done" ? <Waiting loaded={loaded} /> : loaded.value.kind === "ok" ? <CountsOf value={loaded.value.value} /> : <NotAnswered outcome={loaded.value} />}
    </section>
  );
}

/** The run's view (nothing before Run is pressed). */
function RunView({ asked, loaded, current }: { readonly asked: Asked<Ran>; readonly loaded: Loaded<Outcome<Ran>>; readonly current: State }): ReactElement | null {
  return asked.state === null ? null : (
    <section className="outcome" aria-label="run">
      <h3>Run</h3>
      <Stale asked={asked.state} current={current} />
      {loaded.kind !== "done" ? <Waiting loaded={loaded} /> : loaded.value.kind === "ok" ? <RunOf value={loaded.value.value} /> : <NotAnswered outcome={loaded.value} />}
    </section>
  );
}

/** The builder of a dataset's options (the module's docstring). */
export function Builder({ options }: { readonly options: Options }): ReactElement {
  const [state, setState] = useState<State>(EMPTY);
  const [validation, setValidation] = useState<Asked<Validated>>(IDLE_ASKED);
  const [count, setCount] = useState<Asked<Counts>>(IDLE_ASKED);
  const [run, setRun] = useState<Asked<Ran>>(IDLE_ASKED);
  const validated = useSyncExternalStore(validation.store.subscribe, validation.store.snapshot);
  const counted = useSyncExternalStore(count.store.subscribe, count.store.snapshot);
  const ran = useSyncExternalStore(run.store.subscribe, run.store.snapshot);
  if (options.dataset === null) {
    return (
      <section className="builder" aria-label="builder">
        <h2>{WORDS.heading}</h2>
        <p className="state">{WORDS.noDataset}</p>
      </section>
    );
  }
  const busy = pending(validation, validated) || pending(count, counted) || pending(run, ran);
  const draft = draftOf(options, state);
  const problem = problemOf(state);
  const ready = validation.state === state && validation.draft !== null && validated.kind === "done" && validated.value.kind === "ok" && validated.value.value.valid && !busy;
  const asked = ready ? validation.draft : null;
  const unit = state.unit;
  const view = unit === null ? [] : unit.option.view;
  const [, second] = state.cohorts;
  return (
    <section className="builder" aria-label="builder">
      <h2>{WORDS.heading}</h2>
      <div className="note">
        {options.left.leaves ? <p>{WORDS.leaves}</p> : null}
        {options.left.view ? <p>{options.underK ? WORDS.underK : WORDS.view}</p> : null}
        {options.partial ? <p>{WORDS.partial}</p> : null}
        {options.left.tables ? <p>{WORDS.tables}</p> : null}
      </div>
      <label>
        Unit table{" "}
        <select
          value={unit === null ? "" : unit.at}
          onChange={(event) => {
            setState(withUnit(state, pickOf(options.units, event.currentTarget.selectedIndex)));
          }}
        >
          <NoOption />
          {options.units.map((one, index) => (
            <Option key={keyOf(index)} label={one.label} name={one.name} at={keyOf(index)} />
          ))}
        </select>
      </label>
      {state.cohorts.map((cohort, index) => (
        <section key={cohort.key} className="cohort" aria-label={index === 0 ? "cohort first" : "cohort second"}>
          <h3>{index === 0 ? "Cohort first" : "Cohort second"}</h3>
          {cohort.leaves.length === 0 ? <p className="state">{WORDS.noCondition}</p> : null}
          {cohort.leaves.map((leaf) => (
            <div key={leaf.key} className="leaf">
              <label>
                Column{" "}
                <select
                  value={leaf.column === null ? "" : leaf.column.at}
                  onChange={(event) => {
                    setState(withLeafColumn(state, cohort.key, leaf.key, pickOf(options.leaves, event.currentTarget.selectedIndex)));
                  }}
                >
                  <NoOption />
                  {options.leaves.map((one, at) => (
                    <Option key={keyOf(at)} label={one.label} name={one.name} at={keyOf(at)} />
                  ))}
                </select>
              </label>{" "}
              <Field
                value={leaf.value}
                onChange={(value) => {
                  setState(withLeafValue(state, cohort.key, leaf.key, value));
                }}
              >
                is{" "}
              </Field>{" "}
              <button
                type="button"
                onClick={() => {
                  setState(removeLeaf(state, cohort.key, leaf.key));
                }}
              >
                Remove this condition
              </button>
            </div>
          ))}
          <p className="note">{WORDS.oneLine}</p>
          <button
            type="button"
            disabled={cohort.leaves.length >= MAX_ROWS}
            onClick={() => {
              setState(addLeaf(state, cohort.key));
            }}
          >
            Add a condition
          </button>
          {second === undefined ? null : (
            <button
              type="button"
              onClick={() => {
                setState(removeCohort(state, cohort.key));
              }}
            >
              Remove this cohort
            </button>
          )}
        </section>
      ))}
      {second === undefined ? (
        <button
          type="button"
          onClick={() => {
            setState(addCohort(state));
          }}
        >
          Add a second cohort
        </button>
      ) : null}
      <section className="view" aria-label="view">
        <h3>Distribution of columns</h3>
        {unit === null ? <p className="state">{WORDS.chooseUnit}</p> : null}
        {state.columns.map((column) => (
          <div key={column.key} className="column">
            <label>
              Column{" "}
              <select
                value={column.column === null ? "" : column.column.at}
                disabled={unit === null}
                onChange={(event) => {
                  setState(withColumn(state, column.key, pickOf(view, event.currentTarget.selectedIndex)));
                }}
              >
                <NoOption />
                {view.map((one, at) => (
                  <Option key={keyOf(at)} label={one.label} name={one.name} at={keyOf(at)} />
                ))}
              </select>
            </label>{" "}
            <button
              type="button"
              disabled={state.columns.length <= 1}
              onClick={() => {
                setState(removeColumn(state, column.key));
              }}
            >
              Remove this column
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={state.columns.length >= MAX_ROWS}
          onClick={() => {
            setState(addColumn(state));
          }}
        >
          Add a column
        </button>
      </section>
      <div className="send">
        {problem === "incomplete" ? <p className="state">{WORDS.incomplete}</p> : problem === "empty" ? <p className="state">{WORDS.empty}</p> : null}
        <button
          type="button"
          disabled={draft === null || busy}
          onClick={() => {
            if (draft !== null) {
              setValidation({ store: loader(() => validateDraft(draft)), state, draft });
            }
          }}
        >
          Validate
        </button>{" "}
        <button
          type="button"
          disabled={asked === null}
          onClick={() => {
            if (asked !== null) {
              setCount({ store: loader(() => countDraft(asked)), state, draft: asked });
            }
          }}
        >
          Count
        </button>{" "}
        <button
          type="button"
          disabled={asked === null}
          onClick={() => {
            if (asked !== null) {
              setRun({ store: loader(() => runDraft(asked)), state, draft: asked });
            }
          }}
        >
          Run
        </button>
        {asked === null && !busy && problem === null ? <p className="state">{WORDS.notValid}</p> : null}
      </div>
      <ValidationView asked={validation} loaded={validated} current={state} />
      <CountView asked={count} loaded={counted} current={state} />
      <RunView asked={run} loaded={ran} current={state} />
    </section>
  );
}

