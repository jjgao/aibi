/**
 * A readback (D433; D427's R11; §7.7, A2, D296): the server's sentence about a cohort, a view or a
 * count, rendered from its segments in the server's order through `Segments` (the server's words
 * escaped and kept whole, each data token isolated and marked), never rewritten, paraphrased,
 * reordered or composed by the page. A pack's summary is part of the segments, labelled by the
 * server where it writes it, so it follows the readback as the server placed it.
 */
import type { ReactElement } from "react";

import type { BoxedSegment } from "../api";
import { Segments } from "./Segments";

/** The readback of an output: its segments, in order. */
export function Readback({ value }: { readonly value: readonly BoxedSegment[] }): ReactElement {
  return (
    <p className="readback">
      <Segments value={value} />
    </p>
  );
}
