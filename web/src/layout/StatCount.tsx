/**
 * A statistic's count (D430; D427's R51, per R14; A1, §8.1, D311): a table's row count, as the
 * catalogue's and the dataset's answers give it (`StatCount`). The count is shown through `Num`, by
 * its source text, and always with its `stat:` reference, through `Ref` (a reference is never cut:
 * it names what is counted). A `null` count says why: with a reason at the page's word `/count` in
 * its `not_estimable` (`memberOf`), the reason, through `Figure` (suppressed, a reason in its words,
 * or a reason the page does not know as its text); without one, `Count`'s suppressed (a count is
 * `null` only when the disclosure pass suppressed it, R14). The page never computes with it.
 */
import type { ReactElement } from "react";

import { type BoxedMap, memberOf, Num, type ServerNumber, type ServerText } from "../api";
import { Count, Figure } from "./Figure";
import { Ref } from "./Text";

/** A statistic's count, as the typed answers give it. */
export interface Stat {
  readonly count: ServerNumber | null;
  readonly reference: ServerText;
  readonly not_estimable?: BoxedMap<ServerText>;
}

/** A count with its reference: the count, or why it has none, then the `stat:` reference. */
export function StatCount({ value }: { readonly value: Stat }): ReactElement {
  const reasons = value.not_estimable;
  const reasoned = reasons !== undefined && memberOf(reasons, "/count").found;
  return (
    <span className="stat">
      {value.count !== null ? <Num value={value.count} /> : reasoned ? <Figure value={null} notEstimable={reasons} at="/count" /> : <Count value={null} />} <Ref value={value.reference} />
    </span>
  );
}
