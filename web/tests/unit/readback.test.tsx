/**
 * `Readback` (D433; D427's R11; §7.7, A2, D296): the server's readbacks of the builder's answers
 * (a validation's cohorts and views, a count's, a run's), and a written one, shown from their
 * segments in order: the server's words whole and unmarked, each data token isolated and marked,
 * never rewritten, reordered or cut by the page.
 */
import { describe, expect, it } from "vitest";

import type { BoxedSegment } from "../../src/api/typed";
import { Readback } from "../../src/layout/Readback";
import { walked } from "./answers";
import { render } from "./render";
import { answersOf } from "./screens";

/** The segments of a readback written as the server writes them, walked. */
function segmentsOf(segments: readonly unknown[]): readonly BoxedSegment[] {
  const body = JSON.stringify({ valid: true, refusals: [], cohorts: [], views: [{ position: 0, analysis: { id: "a", version: "1" }, id: "drv:x", computation_id: "c", status: "issued", form: {}, readback: segments, caveats: [] }] });
  const found = walked("DocumentValidation", body) as { readonly views: readonly { readonly readback: readonly BoxedSegment[] }[] };
  const view = found.views[0];
  if (view === undefined) {
    throw new Error("no view");
  }
  return view.readback;
}

/** Each segment of a rendering, in order: a data token's text marked `data:`, a text's as it is. */
function partsOf(container: Element): string[] {
  const parts: string[] = [];
  const paragraph = container.querySelector("p.readback");
  for (const node of paragraph?.childNodes ?? []) {
    parts.push(node instanceof Element ? `data:${node.textContent}` : (node.textContent ?? ""));
  }
  return parts;
}

describe("Readback", () => {
  it("R11: renders a readback from its segments, in order, never rewritten: the server's words as they are, each data token isolated and marked", async () => {
    expect.hasAssertions();
    const segments = segmentsOf([{ text: "Rows of " }, { data: "trees" }, { text: " for which " }, { text: "the " }, { data: "variety" }, { text: " is " }, { data: "<b>apple</b>" }, { data: "x".repeat(3000), truncated: true }, { text: "." }]);
    const shown = await render(<Readback value={segments} />);
    expect(partsOf(shown.container)).toEqual(["Rows of ", "data:trees", " for which ", "the ", "data:variety", " is ", "data:<b>apple</b>", `data:${"x".repeat(3000)}…`, "."]);
    expect(shown.container.querySelectorAll("bdi.data.token")).toHaveLength(4);
    expect(shown.container.querySelector("b")).toBeNull();
    shown.unmount();
  });

  it("R11: every readback of the builder's answers is shown segment by segment, in the server's order", async () => {
    expect.hasAssertions();
    const readbacks: unknown[][] = [];
    for (const operation of ["validate_document", "count_cohort", "run_analysis"]) {
      for (const answer of answersOf(operation)) {
        const body = JSON.parse(answer.body) as {
          cohorts?: { readback: unknown[] }[];
          views?: { readback: unknown[] }[];
          counts?: { count: { readback: unknown[] } }[];
          results?: { readback: { cohorts: unknown[][]; view: unknown[] } }[];
        };
        readbacks.push(...(body.cohorts ?? []).map((one) => one.readback), ...(body.views ?? []).map((one) => one.readback));
        readbacks.push(...(body.counts ?? []).map((one) => one.count.readback));
        for (const result of body.results ?? []) {
          readbacks.push(...result.readback.cohorts, result.readback.view);
        }
      }
    }
    expect(readbacks.length).toBeGreaterThan(20);
    for (const readback of readbacks) {
      const shown = await render(<Readback value={segmentsOf(readback)} />);
      const expected = readback.map((segment) => {
        const one = segment as { readonly text?: string; readonly data?: string; readonly truncated?: boolean };
        return one.data === undefined ? (one.text ?? "") : `data:${one.data}${one.truncated === true ? "…" : ""}`;
      });
      // An empty text segment renders no node.
      expect(partsOf(shown.container)).toEqual(expected.filter((part) => part !== ""));
      shown.unmount();
    }
  });
});
