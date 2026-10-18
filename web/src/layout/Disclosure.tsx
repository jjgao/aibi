/**
 * The effective disclosure setting (D430; D427's R25, §8.4): stated on every result, cohort count
 * and catalogue answer, as the answer says it (`Disclosure`/`DisclosureOut`'s `min_cell_count`, the
 * largest of the floor and the dataset's): a minimum cell count *k*, shown through `Num`, with what
 * it does, or none. The page states the setting and never applies it: what is suppressed the server
 * suppressed.
 */
import type { ReactElement } from "react";

import { Num, type ServerNumber } from "../api";

/** The words of a setting of none: fixed. */
export const NO_SETTING = "Disclosure: none (no minimum cell count applies).";

/** The effective disclosure setting of an answer. */
export function Disclosure({ value }: { readonly value: { readonly min_cell_count: ServerNumber | null } }): ReactElement {
  const k = value.min_cell_count;
  return k === null ? (
    <p className="disclosure">{NO_SETTING}</p>
  ) : (
    <p className="disclosure">
      Disclosure: minimum cell count <Num value={k} />: counts below it, and counts that would reveal them, are suppressed (§8.4).
    </p>
  );
}
