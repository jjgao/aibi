/**
 * Refusals (D430; D427's R42a, R70, R72a and R73; A3, §8.6). `Refusals` shows a refused outcome
 * (D426: a well-formed `{refusals}` body at any status of 400 or more) as refusals, every one, in the
 * server's order, with its `Retry-After` where the server gave one (`RetryAfter`); `Refusal` shows
 * one: its code, its `path` as data (or the document's top level for a `null` path), its message
 * from its segments, its alternatives **listed, never applied** (text, never a control that would
 * act on one), the limit it hit (its name, and its maximum through `Num`) and, for a refusal that
 * carries cohort counts, that it does (they are shown as cohort counts are, D427's R71, M5.4's
 * `Population`: until then the page says so in fixed words, never a number). `WITHHELD_UNDER_K`
 * (§8.4, D353) is led by fixed words: the output is withheld under the disclosure setting its
 * message names, no value is shown, and its alternatives (`count_cohort` and the disclosed
 * analyses) are offered instead, never run.
 */
import { Fragment, type ReactElement } from "react";

import { type BoxedRefusals, kindOf, Num, type Refused, RetryAfter } from "../api";
import { keyOf } from "./key";
import { Segments } from "./Segments";
import { Ref } from "./Text";

/** A refusal, as the typed answers give it. */
export type BoxedRefusal = BoxedRefusals[number];

/** The words over a refused outcome: fixed. */
export const REFUSED = "The request was refused.";

/** The words of a `null` path: fixed. */
export const DOCUMENT_LEVEL = "the document as a whole";

/** The words that lead a refusal withheld under the disclosure setting: fixed. */
export const WITHHELD = "Withheld under the disclosure setting (§8.4): no value is shown. The message names the setting that binds; the alternatives below are offered instead, and none is run.";

/** The words of a refusal that carries cohort counts: fixed (R71 shows them, with `Population`). */
export const CARRIES_COUNTS = "This refusal carries cohort counts, shown with the cohort counts (not here).";

/** One refusal: its code, where, its message, its alternatives, its limit. */
export function Refusal({ value }: { readonly value: BoxedRefusal }): ReactElement {
  const withheld = kindOf(value.code, "RefusalCode") === "WITHHELD_UNDER_K";
  const alternatives = value.alternatives ?? [];
  const counts = value.counts ?? [];
  return (
    <div role="listitem" className={withheld ? "refusal withheld" : "refusal"}>
      {withheld ? <p className="withheld">{WITHHELD}</p> : null}
      <p>
        <Ref value={value.code} /> at {value.path === null ? DOCUMENT_LEVEL : <Ref value={value.path} />}: <Segments value={value.message} />
      </p>
      {alternatives.length === 0 ? null : (
        <div className="alternatives">
          {withheld ? "Offered instead (not run): " : "Available (not applied): "}
          <span role="list">
            {alternatives.map((alternative, index) => (
              <Fragment key={keyOf(index)}>
                {index === 0 ? null : ", "}
                <span role="listitem">
                  <Segments value={[alternative]} />
                </span>
              </Fragment>
            ))}
          </span>
        </div>
      )}
      {value.limit === undefined ? null : (
        <p className="limit">
          Limit: <Ref value={value.limit.name} />, at most <Num value={value.limit.max} />.
        </p>
      )}
      {counts.length === 0 ? null : <p className="counts">{CARRIES_COUNTS}</p>}
    </div>
  );
}

/** A refused outcome: every refusal in the server's order, and its `Retry-After`. */
export function Refusals({ refused }: { readonly refused: Refused }): ReactElement {
  return (
    <section className="refused" aria-label="refused">
      <p>{REFUSED}</p>
      <div role="list">
        {refused.refusals.map((refusal, index) => (
          <Refusal key={keyOf(index)} value={refusal} />
        ))}
      </div>
      <RetryAfter refused={refused} />
    </section>
  );
}
