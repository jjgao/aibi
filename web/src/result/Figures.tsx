/**
 * The result screen (D435; D427's R10, R33, R37a and R49b; §8.1, §8.3, A2): one result of a
 * `run_analysis` answer, shown in place under the builder's run (no route, no call of its own).
 * `Result` reads its own prop's values (`valuesOf`, D434), so that one result's read never meets
 * another's envelope, and renders the module-private `Figures`, in this order:
 *
 * - under `Inspection` (R22's banner when a caveat blocks), every caveat at the head, its `affects`
 *   pointers as data (R37a);
 * - the view: the analysis and its version, the disclosure setting (`Disclosure`, R25) and the view's
 *   readback (R10); fixed words when the values model gives no values for the view as a whole (D434's
 *   `view: "none"`, read from the generated table: R49b), or a read the page did not make;
 * - one section per position, up to the longest of the six per-position lists (the labels, the
 *   cohorts, the cohorts' readbacks, the populations, the analysed counts and the values' positions),
 *   a list's missing entry in fixed words: the cohort's label as data (R33), its `position` and id,
 *   the member's name `reference` marked when the server marks it (R33), its readback beside its
 *   figures (R10), its population (`Population`, R18, R20), its analysed counts (`Analysed`, R19) and
 *   its values (`Values`, R34a);
 * - the provenance (`Provenance`, R26a, R28a) and the charts' words (`Charts`, R31a).
 *
 * The page computes nothing of the result: no share, total or rest of a list.
 */
import type { ReactElement } from "react";

import type { Key } from "../layout/key";

import { type Answered, Num, valuesOf, type ValuesRead } from "../api";
import { Inspection } from "../layout/Caveat";
import { Disclosure } from "../layout/Disclosure";
import { keyOf } from "../layout/key";
import { Population } from "../layout/Population";
import { Readback } from "../layout/Readback";
import { Ref, Text } from "../layout/Text";
import { Analysed } from "./Analysed";
import { Charts } from "./Charts";
import { Provenance } from "./Provenance";
import { Unread, Values } from "./Values";

type BoxedResult = Answered<"run_analysis">["results"][number];

/** The fixed words of the screen. */
export const RESULT_WORDS = {
  noView: "This result gives no values for the view as a whole.",
  reference: "the reference position",
  label: "This position's label is not listed.",
  cohort: "This position's cohort is not listed.",
  readback: "This position's readback is not listed.",
  population: "This position's population is not listed.",
  analysed: "This position's analysed counts are not listed.",
  values: "This position's values are not listed.",
} as const;

/** One position (the module's docstring). */
function Position({ result, read, index }: { readonly result: BoxedResult; readonly read: ValuesRead; readonly index: Key }): ReactElement {
  const label = result.labels[index];
  const cohort = result.cohorts[index];
  const readback = result.readback.cohorts[index];
  const population = result.population[index];
  const analysed = result.analysed[index];
  const values = read.kind === "unread" ? null : read.positions[index];
  const disclosure = result.derivation.disclosure;
  return (
    <section className="position" aria-label="position">
      <h3>{label === undefined ? RESULT_WORDS.label : <Text value={label.data} />}</h3>
      {cohort === undefined ? (
        <p className="missing">{RESULT_WORDS.cohort}</p>
      ) : (
        <p className="meta">
          position <Num value={cohort.position} />, cohort <Ref value={cohort.id} />
          {cohort.reference ? <span className="reference">, {RESULT_WORDS.reference}</span> : null}
        </p>
      )}
      {readback === undefined ? <p className="missing">{RESULT_WORDS.readback}</p> : <Readback value={readback} />}
      {population === undefined ? <p className="missing">{RESULT_WORDS.population}</p> : <Population value={population} disclosure={disclosure} />}
      {analysed === undefined ? (
        <p className="missing">{RESULT_WORDS.analysed}</p>
      ) : (
        <Analysed value={analysed} labels={read.kind !== "unread" && read.variables === "labelled" ? read.columns : null} />
      )}
      {read.kind === "unread" ? null : values === undefined || values === null ? (
        <p className="missing">{RESULT_WORDS.values}</p>
      ) : (
        <Values columns={values.columns} labels={read.columns} disclosure={disclosure} />
      )}
    </section>
  );
}

/** The words of the view's values as a whole, by the read's `view` (D434: from the generated table):
 * a read of another `view` fails to compile here until it has its own. */
const VIEW_WORDS = { none: RESULT_WORDS.noView } as const satisfies Record<Extract<ValuesRead, { readonly kind: "summary.distribution" }>["view"], string>;

/** A result and its read (the module's docstring). */
function Figures({ result, read }: { readonly result: BoxedResult; readonly read: ValuesRead }): ReactElement {
  const lists: readonly (readonly unknown[])[] = [result.labels, result.cohorts, result.readback.cohorts, result.population, result.analysed, read.kind === "unread" ? [] : read.positions];
  const longest = lists.reduce((one, other) => (other.length > one.length ? other : one), []);
  return (
    <Inspection caveats={result.caveats}>
      <section className="view" aria-label="view">
        <p>
          Analysis <Ref value={result.derivation.analysis.id} /> version <Ref value={result.derivation.analysis.version} />
        </p>
        <Disclosure value={result.derivation.disclosure} />
        <Readback value={result.readback.view} />
        {read.kind === "unread" ? <Unread cause={read.cause} /> : <p className="note">{VIEW_WORDS[read.view]}</p>}
      </section>
      {longest.map((_unused, index) => (
        <Position key={keyOf(index)} result={result} read={read} index={keyOf(index)} />
      ))}
      <Provenance result={result} />
      <Charts charts={result.charts} />
    </Inspection>
  );
}

/** One result of a `run_analysis` answer (the module's docstring). */
export function Result({ result }: { readonly result: BoxedResult }): ReactElement {
  return <Figures result={result} read={valuesOf(result)} />;
}
