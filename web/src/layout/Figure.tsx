/**
 * A number that may be `null` (D430; D427's R06 and R14): never shown as 0 or blank. `Count` is a
 * count, which is `null` only when the disclosure pass suppressed it (§8.4, `StatCount`'s count
 * among them), so a `null` is shown as suppressed. `Figure` is any other number of an output, whose
 * `null` says why in the enclosing `not_estimable` (a map from a pointer to a reason, §8.2): the
 * reason at the figure's own pointer (`at`, the page's word: `/estimate`, `/ci/low`) is shown,
 * `suppressed` as suppressed and any other in the reason's words (`REASON_WORDS`, one for each member
 * of the generated vocabulary, which the type checker holds complete); a reason the page does not
 * know is shown as the server's text, through `Text`; and a `null` with no reason there fails closed
 * as not shown, never as suppressed by default (a missing reason is the page and the server
 * disagreeing, not a suppression). A number is shown through `Num`, by its exact source text.
 */
import type { ReactElement } from "react";

import { type BoxedMap, type Kind, kindOf, memberOf, Num, type ServerNumber, type ServerText } from "../api";
import { Text } from "./Text";

/** The words of each reason a number is not estimable (§8.2's `NotEstimableReason`). */
export const REASON_WORDS = {
  no_units: "no units",
  no_events: "no events",
  zero_denominator: "a zero denominator",
  zero_variance: "zero variance",
  not_reached: "not reached",
  beyond_follow_up: "beyond the follow-up",
  separation: "separation",
  not_converged: "the fit did not converge",
  degenerate_table: "a degenerate table",
  overlapping_cohorts: "the cohorts overlap",
  confounded_with_dataset: "confounded with the dataset",
  suppressed: "suppressed",
} as const satisfies Record<NonNullable<Kind<"NotEstimableReason">>, string>;

/** The words of a suppressed number. */
export const SUPPRESSED = "suppressed";

/** The words of a `null` with no reason at its pointer: fixed, fail-closed. */
export const NOT_SHOWN = "not shown: no reason was given";

/** A count: its value, or suppressed. */
export function Count({ value }: { readonly value: ServerNumber | null }): ReactElement {
  return value === null ? <span className="suppressed">{SUPPRESSED}</span> : <Num value={value} />;
}

/** A number of an output: its value, or why it has none (the reason at `at` in `notEstimable`). */
export function Figure({ value, notEstimable, at }: { readonly value: ServerNumber | null; readonly notEstimable: BoxedMap<ServerText> | null | undefined; readonly at: `/${string}` }): ReactElement {
  if (value !== null) {
    return <Num value={value} />;
  }
  const reason = notEstimable === null || notEstimable === undefined ? null : memberOf(notEstimable, at);
  if (reason === null || !reason.found) {
    return <span className="not-shown">{NOT_SHOWN}</span>;
  }
  const kind = kindOf(reason.value, "NotEstimableReason");
  if (kind === "suppressed") {
    return <span className="suppressed">{SUPPRESSED}</span>;
  }
  return (
    <span className="not-estimable">
      not estimable: {kind === null ? <Text value={reason.value} /> : REASON_WORDS[kind]}
    </span>
  );
}
