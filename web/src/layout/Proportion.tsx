/**
 * A proportion (D435; D427's R12 and R13; §8.2, P1, P2): its numerator, its denominator and its
 * estimate, each under its own name through `Figure` (a `null` with the reason at its own pointer of
 * the proportion's `not_estimable`); its `denominator_definition`'s three members under their own
 * names, as sent (`counts` a data token, `position` a number or "none given", `predicate` a data
 * token or "none given"), with no sentence on what any of them means; its `denominator_text`, when
 * the server gives one, through `Segments`; its `excluded`, when present, by reason, every reason and
 * its zero, or suppressed; and, when it has one, fixed words that its interval is not shown yet
 * (R16, M5.4-rest). Never a bare estimate, and the page makes no number of it.
 */
import { Fragment, type ReactElement } from "react";

import { type Boxed, type components, entriesOf, Num } from "../api";
import { Count, Figure } from "./Figure";
import { keyOf } from "./key";
import { Segments } from "./Segments";
import { Ref } from "./Text";

/** A proportion, as the typed answers give it. */
export type BoxedProportion = Boxed<components["schemas"]["Proportion"]>;

/** The fixed words of a proportion. */
export const PROPORTION_WORDS = {
  none: "none given",
  interval: "This proportion has an interval, not shown on this page yet (R16).",
} as const;

/** A proportion's `excluded`, when the server sent one: by reason, every entry, or suppressed. */
function Excluded({ value }: { readonly value: BoxedProportion }): ReactElement | null {
  const excluded = value.excluded;
  if (excluded === undefined) {
    return null;
  }
  return (
    <span className="excluded">
      ; excluded{" "}
      {excluded === null ? (
        <Figure value={null} notEstimable={value.not_estimable} at="/excluded" />
      ) : (
        <span role="list">
          {entriesOf(excluded).map(([reason, count], index) => (
            <Fragment key={keyOf(index)}>
              {index === 0 ? null : ", "}
              <span role="listitem">
                <Ref value={reason} /> <Count value={count} />
              </span>
            </Fragment>
          ))}
        </span>
      )}
    </span>
  );
}

/** A proportion (the module's docstring). */
export function Proportion({ value }: { readonly value: BoxedProportion }): ReactElement {
  const definition = value.denominator_definition;
  return (
    <span className="proportion">
      numerator <Figure value={value.numerator} notEstimable={value.not_estimable} at="/numerator" />; denominator{" "}
      <Figure value={value.denominator} notEstimable={value.not_estimable} at="/denominator" />; estimate{" "}
      <Figure value={value.estimate} notEstimable={value.not_estimable} at="/estimate" />; denominator definition: counts <Ref value={definition.counts} token />, position{" "}
      {definition.position === null ? <span className="absent">{PROPORTION_WORDS.none}</span> : <Num value={definition.position} />}, predicate{" "}
      {definition.predicate === null ? <span className="absent">{PROPORTION_WORDS.none}</span> : <Ref value={definition.predicate} token />}
      {value.denominator_text === undefined ? null : (
        <span className="denominator-text">
          {" "}
          (<Segments value={value.denominator_text} />)
        </span>
      )}
      <Excluded value={value} />
      {value.ci === undefined ? null : <span className="note"> {PROPORTION_WORDS.interval}</span>}
    </span>
  );
}
