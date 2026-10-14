/**
 * A message or a readback (D399, D428): the server's words and the data tokens between them, never
 * confusable. A text segment is the server's words, shown through the visible form too, in the
 * `whole` form of its own (escaped, never cut, with no `<bdi>` and no data mark: the server's
 * page's `segments`, which does not cut them); a data segment is data, through `Ref` (isolated,
 * marked as data, not cut by the page) with the server's own cut mark when the server cut it
 * (`truncated`): one mark, never a second. A data token spelled like the server's words stays marked
 * and isolated, so it cannot pass for them.
 */
import type { ReactElement } from "react";

import { type BoxedSegment, showText } from "../api";
import { FORM } from "./form";
import { keyOf } from "./key";
import { Ref } from "./Text";

/** The segments of a message, in order. */
export function Segments({ value }: { readonly value: readonly BoxedSegment[] }): ReactElement {
  return (
    <>
      {value.map((segment, index) =>
        segment.kind === "text" ? (
          <FORM key={keyOf(index)} value="whole">
            {showText(segment.text)}
          </FORM>
        ) : (
          <Ref key={keyOf(index)} value={segment.data} token truncated={segment.truncated} />
        ),
      )}
    </>
  );
}
