/**
 * The values seam's `view: "none"` (D434) is read from the generated table, from the model of the
 * result's own key, never from the analysis id: the generated module is replaced for this file by a
 * copy whose `DistributionValues.view` each cell sets. A view that is not a reference to a map of no
 * entries (an inline map of no entries, which the reading refuses though it means the same: stricter
 * than needed; a map of any JSON value, a map with a pattern, a map whose keys are constrained)
 * makes the same live answer `unread/analysis`, though its id and version are listed; a reference to
 * another map of no entries reads (the control); a map with a required key refuses the live `view:
 * {}` at the walk, so it is `unread/shape` and never `view: "none"`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Shape } from "../../src/api/generated/shapes";
import { valuesOf } from "../../src/api";
import { runAnalysis } from "../../src/api/typed";
import { ORCHARD } from "./typed-support";

/** The replaced table, which each cell changes. */
const table = vi.hoisted((): { shapes: Record<string, Shape> } => ({ shapes: {} }));

vi.mock("../../src/api/generated/shapes", async (original) => {
  const actual = await original<typeof import("../../src/api/generated/shapes")>();
  table.shapes = { ...actual.SHAPES };
  return { ...actual, SHAPES: table.shapes };
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The table's `DistributionValues` with its `view` replaced, and a component `ZzView` beside it. */
function withView(view: Shape, component?: Shape): void {
  const values = table.shapes["DistributionValues"];
  if (values?.t !== "rec") {
    throw new Error("DistributionValues is no record");
  }
  table.shapes["DistributionValues"] = { ...values, p: { ...values.p, view } };
  if (component !== undefined) {
    table.shapes["ZzView"] = component;
  }
}

/** The read of the builder's live answer (a), through the real decoder and walk. */
async function readOfBuilt(): Promise<unknown> {
  const body = ORCHARD[25]?.body ?? "";
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }))),
  );
  const outcome = await runAnalysis({ document: { aibi: "1", dataset: "orchard", unit: "trees", cohorts: { all_trees: { all: [] } }, views: [] } });
  if (outcome.kind !== "ok") {
    throw new Error(`not ok: ${outcome.kind}`);
  }
  const [result] = outcome.value.results;
  if (result === undefined) {
    throw new Error("no result");
  }
  const read = valuesOf(result);
  return read.kind === "unread" ? read : { kind: read.kind, view: read.view };
}

const REF: Shape = { t: "ref", n: "ZzView" };
const ANALYSIS = { kind: "unread", cause: "analysis" };

describe("view: none is the table's reading, of the result's own model (D434)", () => {
  it.each<readonly [string, Shape, Shape | undefined, unknown]>([
    ["an inline map of no entries (a reference is asked: stricter than needed, as D434 says)", { t: "map", v: { t: "never" } }, undefined, ANALYSIS],
    ["a reference to a map of any JSON value", REF, { t: "map", v: { t: "json" } }, ANALYSIS],
    ["a reference to a map with a pattern", REF, { t: "map", v: { t: "never" }, pp: [["^x$", { t: "json" }]] }, ANALYSIS],
    ["a reference to a map whose keys are constrained", REF, { t: "map", v: { t: "never" }, k: { t: "str" } }, ANALYSIS],
    ["a reference to a map with a required key (the live view refused at the walk)", REF, { t: "map", v: { t: "never" }, r: ["x"] }, { kind: "unread", cause: "shape" }],
    ["CONTROL a reference to another map of no entries", REF, { t: "map", v: { t: "never" } }, { kind: "summary.distribution", view: "none" }],
  ])("%s", async (_case, view, component, expected) => {
    withView(view, component);
    expect(await readOfBuilt()).toEqual(expected);
  });
});
