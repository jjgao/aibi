/**
 * An id and its status (D433; D427's R43; §7.6, §12.2): the id a validation gives a cohort or a view,
 * through `Ref`, and its status in words of its own, one for each status `explain` can give
 * (`Explanation.status`, of which a validation's statuses are a part): an id not yet issued is shown
 * as such. A status the page does not know is shown as the server's text, marked, never dropped and
 * never read as one it knows.
 */
import type { ReactElement } from "react";

import { kindOf, type Kind, type ServerText } from "../api";
import { Ref, Text } from "../layout/Text";

/** The words of each id status: fixed, one for each. */
export const STATUS_WORDS = {
  issued: "issued",
  not_issued: "not issued yet",
  unknown: "unknown",
  withdrawn: "withdrawn",
  discarded: "discarded",
  unknown_release: "of an unknown release",
  erased: "erased",
} as const satisfies Record<NonNullable<Kind<"Explanation.status">>, string>;

/** The words after a status the page does not know: fixed. */
export const NOT_KNOWN = "(not known to this page)";

/** An id, and its status in words. */
export function IdStatus({ id, status }: { readonly id: ServerText; readonly status: ServerText }): ReactElement {
  const known = kindOf(status, "Explanation.status");
  return (
    <span className="id">
      Id <Ref value={id} />:{" "}
      {known === null ? (
        <>
          <Text value={status} /> {NOT_KNOWN}
        </>
      ) : (
        STATUS_WORDS[known]
      )}
    </span>
  );
}
