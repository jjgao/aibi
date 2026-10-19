/**
 * Cohort counts (D433; D427's R18, R20, R40a and R71; §6.6, §8.4, D297). `Population` shows a cohort
 * count's population: `n_true`, `n_false` and `n_unknown`, then `n_unknown` by reason and by leaf
 * (every entry, zeros included, each key as data), then `lift_differs`. Each is shown through
 * `Count` (by its source text, never summed or compared but by the sanctioned predicates), and as
 * suppressed where it is `null` **or** where the entry's `suppressed` pointers name it (fail closed:
 * a value the server listed as suppressed is never shown). When `n_unknown` is above zero
 * (`isPositive`) its largest reason (`largestOf`: ties in the vocabulary's order) is named beside
 * it; a suppressed `n_unknown` or a suppressed map names none. `lift_differs` is suppressed under any
 * *k* (§8.4, R20), a 0 included: under the count's effective disclosure setting (its `disclosure`,
 * which `Counted` hands on) it is shown as suppressed whatever value the answer holds.
 *
 * `Counted` shows one cohort count as a screen shows it, the builder's counts and a refusal's
 * `counts` alike (R71): its id, its releases, its disclosure setting, its caveats (under the banner
 * when one blocks), its readback and its population, and fixed words for its size, which is not
 * shown yet (R40b, M5.3-rest). Not shown: its `digest` and `issuance` (M5.4-min's provenance, R26).
 */
import { Fragment, type ReactElement } from "react";

import { type Answered, entriesOf, isPositive, kindOf, largestOf, type ServerNumber } from "../api";
import { Inspection } from "./Caveat";
import { Disclosure } from "./Disclosure";
import { Count, SUPPRESSED } from "./Figure";
import { keyOf } from "./key";
import { Readback } from "./Readback";
import { ReleaseRef } from "./ReleaseRef";
import { Ref } from "./Text";

/** A cohort count, as the typed answers give it. */
export type BoxedCohortCount = Answered<"count_cohort">["counts"][number]["count"];

/** A cohort count's population. */
export type BoxedPopulation = BoxedCohortCount["population"];

/** A pointer of `Population.suppressed`. */
type Pointer = "/n_true" | "/n_false" | "/n_unknown" | "/unknown_by_reason" | "/unknown_by_leaf" | "/lift_differs";

/** The words of a count's size, which the page does not show yet: fixed. */
export const SIZE_NOT_SHOWN = "The cohort's size as a share of the unit table is not shown yet.";

/** Whether the population's `suppressed` pointers name `pointer`. */
function listed(population: BoxedPopulation, pointer: Pointer): boolean {
  return (population.suppressed ?? []).some((one) => kindOf(one, "Population.suppressed[]") === pointer);
}

/** A count of the population: its value, or suppressed where it is `null` or listed. */
function Member({ population, value, pointer }: { readonly population: BoxedPopulation; readonly value: ServerNumber | null; readonly pointer: Pointer }): ReactElement {
  return listed(population, pointer) ? <Count value={null} /> : <Count value={value} />;
}

/** A map of unknowns: every entry, its key as data and its count; suppressed where it is `null` or
 * listed. */
function Unknowns({ population, pointer }: { readonly population: BoxedPopulation; readonly pointer: "/unknown_by_reason" | "/unknown_by_leaf" }): ReactElement {
  const map = pointer === "/unknown_by_reason" ? population.unknown_by_reason : population.unknown_by_leaf;
  if (map === null || listed(population, pointer)) {
    return <span className="suppressed">{SUPPRESSED}</span>;
  }
  const entries = entriesOf(map);
  return entries.length === 0 ? (
    <span className="none">none</span>
  ) : (
    <span role="list">
      {entries.map(([key, value], index) => (
        <Fragment key={keyOf(index)}>
          {index === 0 ? null : ", "}
          <span role="listitem">
            <Ref value={key} /> <Count value={value} />
          </span>
        </Fragment>
      ))}
    </span>
  );
}

/** A cohort count's population (the module's docstring). */
export function Population({ value, disclosure }: { readonly value: BoxedPopulation; readonly disclosure: { readonly min_cell_count: ServerNumber | null } }): ReactElement {
  // §8.4: `lift_differs` is suppressed under any *k*, a 0 included: under an effective setting it is
  // shown as suppressed whatever value came (fail closed; the answer's schema does not tie it).
  const underK = disclosure.min_cell_count !== null;
  const unknown = listed(value, "/n_unknown") ? null : value.n_unknown;
  const reasons = listed(value, "/unknown_by_reason") ? null : value.unknown_by_reason;
  const largest = unknown !== null && isPositive(unknown) ? largestOf(reasons) : null;
  return (
    <div className="population" aria-label="population">
      <p>
        n_true <Member population={value} value={value.n_true} pointer="/n_true" />; n_false <Member population={value} value={value.n_false} pointer="/n_false" />; n_unknown{" "}
        <Member population={value} value={value.n_unknown} pointer="/n_unknown" />
        {largest === null ? null : (
          <span className="largest">
            {" "}
            (largest reason <Ref value={largest} />)
          </span>
        )}
      </p>
      <p>
        n_unknown by reason: <Unknowns population={value} pointer="/unknown_by_reason" />
      </p>
      <p>
        n_unknown by leaf: <Unknowns population={value} pointer="/unknown_by_leaf" />
      </p>
      <p>
        lift_differs {underK ? <Count value={null} /> : <Member population={value} value={value.lift_differs} pointer="/lift_differs" />}
      </p>
    </div>
  );
}

/** One cohort count, as every screen shows one (the module's docstring). */
export function Counted({ value }: { readonly value: BoxedCohortCount }): ReactElement {
  return (
    <div className="counted">
      <p>
        Count <Ref value={value.id} />
      </p>
      <p className="meta">
        Releases:{" "}
        {value.releases.map((release, index) => (
          <Fragment key={keyOf(index)}>
            {index === 0 ? null : ", "}
            <ReleaseRef value={release} />
          </Fragment>
        ))}
      </p>
      <Disclosure value={value.disclosure} />
      <Inspection caveats={value.caveats}>
        <Readback value={value.readback} />
        <Population value={value.population} disclosure={value.disclosure} />
        <p className="note">{SIZE_NOT_SHOWN}</p>
      </Inspection>
    </div>
  );
}
