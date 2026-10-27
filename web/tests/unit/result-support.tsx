/**
 * What the result screen's tests share (D435): the live answers of `orchard-answers.json` by the
 * letter the plan gives them (the server's own, written by `test_openapi_results.py`), a raw body
 * walked by **the real seam** (`runAnalysis` at a stubbed `fetch`, so that `valuesOf` reads each
 * result as the page does: a walked copy reads nothing), and the answer rendered as the builder's run
 * view renders it (`RunOf`: the parameters once, then each `Result`).
 */
import { renderToStaticMarkup, renderToString } from "react-dom/server";
import { vi } from "vitest";

import { type Answered, runAnalysis } from "../../src/api/typed";
import { RunOf } from "../../src/builder/Builder";
import { answerAt, ORCHARD } from "./typed-support";

export type Json = Record<string, unknown>;
export type Ran = Answered<"run_analysis">;

/** The live answers by letter (D434's values answers, by their place in the file's `values` section)
 * and the earlier runs of the orchard. */
export const LIVE = {
  a: answerAt("values", 0),
  b: answerAt("values", 1),
  c: answerAt("values", 2),
  d: answerAt("values", 3),
  e: answerAt("values", 4),
  f: answerAt("values", 5),
  g: answerAt("values", 6),
  h: answerAt("values", 7),
  hPrime: answerAt("values", 8),
} as const;

/** Every live `run_analysis` answer of status 200, by index. */
export const RUNS: readonly (readonly [number, string])[] = ORCHARD.flatMap((answer, index) => (answer.operation === "run_analysis" && answer.status === 200 ? [[index, answer.body] as const] : []));

/** A live answer's raw body, parsed afresh. */
export function liveBody(index: number): Json {
  const body = ORCHARD[index]?.body;
  if (body === undefined) {
    throw new Error(`no orchard answer ${String(index)}`);
  }
  return JSON.parse(body) as Json;
}

export const object = (value: unknown): Json => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : {});
export const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/** The walked answer of a raw body, through the real decoder, walk and values seam. */
export async function ran(body: string): Promise<Ran> {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }))),
  );
  try {
    const outcome = await runAnalysis({ document: { aibi: "1", dataset: "orchard", unit: "trees", cohorts: { all_trees: { all: [] } }, views: [] } });
    if (outcome.kind !== "ok") {
      throw new Error(`not ok: ${outcome.kind}`);
    }
    return outcome.value;
  } finally {
    vi.unstubAllGlobals();
  }
}

/** The markup of a raw body's answer as the run view shows it. */
export async function markupOf(body: string): Promise<string> {
  const answer = await ran(body);
  return renderToStaticMarkup(<RunOf value={answer} />);
}

/** The markup of a raw body's answer with React's text-node markers (each text node apart, as the
 * DOM has them). */
export async function stringOf(body: string): Promise<string> {
  const answer = await ran(body);
  return renderToString(<RunOf value={answer} />);
}

/** A live result: the answer's index, the result's place, its raw JSON and its disclosure setting. */
export interface LiveResult {
  readonly index: number;
  readonly at: number;
  readonly raw: Json;
  readonly k: number | null;
}

/** Every result of every live run answer. */
export function liveResults(): { readonly index: number; readonly result: LiveResult }[] {
  return RUNS.flatMap(([index]) =>
    list(liveBody(index)["results"]).map((raw, at) => {
      const k = object(object(object(raw)["derivation"])["disclosure"])["min_cell_count"];
      return { index, result: { index, at, raw: object(raw), k: typeof k === "number" ? k : null } };
    }),
  );
}

/** The counts of a map of counts. */
const valuesOfMap = (value: unknown): unknown[] => Object.values(object(value));

/** Every count the page shows of a raw result, as sent (a number or `null`): each population's
 * members and its maps' entries, each analysed count's (and its variables'), and each category
 * row's proportion's numerator and denominator. */
export function underKCounts(raw: Json): (number | null)[] {
  const found: unknown[] = [];
  for (const population of list(raw["population"]).map(object)) {
    found.push(population["n_true"], population["n_false"], population["n_unknown"], population["lift_differs"], ...valuesOfMap(population["unknown_by_reason"]), ...valuesOfMap(population["unknown_by_leaf"]));
  }
  const analysed = (entry: Json): void => {
    found.push(entry["n"], entry["excluded_units"], ...valuesOfMap(entry["excluded"]));
  };
  for (const entry of list(raw["analysed"]).map(object)) {
    analysed(entry);
    list(entry["variables"]).map(object).forEach(analysed);
  }
  for (const position of list(object(raw["values"])["positions"]).map(object)) {
    for (const column of list(position["columns"]).map(object)) {
      for (const row of list(column["categories"]).map(object)) {
        const proportion = object(row["proportion"]);
        found.push(proportion["numerator"], proportion["denominator"]);
      }
    }
  }
  return found.map((count) => {
    if (count === null || typeof count === "number") {
      return count;
    }
    throw new Error(`a count that is no number: ${JSON.stringify(count)}`);
  });
}

/** A raw body's answer as the run view shows it, in a detached element (for its words and its
 * elements). */
export async function shownOf(body: string | Json): Promise<HTMLElement> {
  const host = document.createElement("div");
  host.innerHTML = await markupOf(typeof body === "string" ? body : JSON.stringify(body));
  return host;
}

/** The element's words, as `render.ts`' `words` gives them. */
export { words } from "./render";

/** A live answer's body with its first result changed. */
export function changed(index: number, change: (result: Json, body: Json) => void): Json {
  const body = liveBody(index);
  change(object(list(body["results"])[0]), body);
  return body;
}
