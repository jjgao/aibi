/**
 * The values seam (D434): `valuesOf` reads a result's `values` from a `run_analysis` answer this seam
 * walked, by the published model of its analysis and version (`VALUES`), with the walk that read the
 * answer. Every body goes through the real decoder and walk at a stubbed `fetch`.
 *
 * - **Every live answer** of `orchard-answers.json` (the server's own, written by
 *   `server/tests/core/analyses/test_openapi_results.py`): the expected read (the labels, the arm of
 *   each column, `variables`) is computed by this test from the raw JSON, never by the walk;
 *   `summary.members` and `compare.existence` are `unread/analysis`.
 * - **Synthetic bodies**: `typed-support.ts`' `DistributionValues` bodies (every arm of every
 *   alternative, D434's `PLANS` enumeration) spliced into a live answer's result, each with the
 *   disclosure setting unset and set, each read with arms equal to its raw kinds; coverage counters
 *   assert that every kind, both open bin edges, `other_values`, a row that names no value and no
 *   `other_values`, every `not_estimable` pointer, both `variables` readings and each arm with *k*
 *   set and unset are reached.
 * - **Misses**, each `unread/shape`: a member renamed or added, a `kind` changed, a number as a
 *   string, the labels one short, one long or not text; another version or analysis is
 *   `unread/analysis`; a copied or composed result throws `notAResult`, which a route's error
 *   element shows in its own fixed words, never the error's.
 */
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { entriesOf, readText } from "../../src/api/box";
import { ClientError, REFUSALS } from "../../src/api/client";
import { VALUES, VOCABULARIES } from "../../src/api/generated/shapes";
import { type Answered, type ColumnRead, type ValuesRead, valuesOf } from "../../src/api";
import { runAnalysis } from "../../src/api/typed";
import { Failure } from "../../src/catalogue/Failure";
import { render } from "./render";
import { bodies, ORCHARD } from "./typed-support";

type Json = Record<string, unknown>;
type Result = Answered<"run_analysis">["results"][number];

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The walked answer of a raw body, through the real decoder and walk. */
async function ran(body: string): Promise<Answered<"run_analysis">> {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }))),
  );
  const outcome = await runAnalysis({ document: { aibi: "1", dataset: "orchard", unit: "trees", cohorts: { all_trees: { all: [] } }, views: [] } });
  if (outcome.kind !== "ok") {
    throw new Error(`not ok: ${outcome.kind}`);
  }
  return outcome.value;
}

/** Each result of a raw body, read. */
async function reads(body: unknown): Promise<ValuesRead[]> {
  const answer = await ran(JSON.stringify(body));
  return answer.results.map((result) => valuesOf(result));
}

/** What the test expects of a raw result, computed from its JSON alone. */
interface Expected {
  readonly read: "summary.distribution" | "analysis" | "shape";
  readonly labels?: readonly string[];
  readonly arms?: readonly (readonly string[])[];
  readonly variables?: "labelled" | "unlabelled";
}

const object = (value: unknown): Json => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : {});
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

function expected(result: Json): Expected {
  const derivation = object(result["derivation"]);
  const analysis = object(derivation["analysis"]);
  if (`${String(analysis["id"])}@${String(analysis["version"])}` !== "summary.distribution@1.0.0") {
    return { read: "analysis" };
  }
  const labels = list(object(object(object(derivation["document"])["view"])["params"])["columns"]).map((column) => object(column)["column"]);
  if (labels.length === 0 || !labels.every((label) => typeof label === "string")) {
    return { read: "shape" };
  }
  const positions = list(object(result["values"])["positions"]).map((position) => list(object(position)["columns"]).map((column) => String(object(column)["kind"])));
  if (positions.some((columns) => columns.length !== labels.length)) {
    return { read: "shape" };
  }
  const arms = positions.map((columns) => columns.map((kind) => (kind === "numbers" || kind === "categories" ? kind : `other ${kind}`)));
  const labelled = list(result["analysed"]).every((entry) => {
    const variables = object(entry)["variables"];
    return variables === undefined ? labels.length === 1 : list(variables).length === labels.length;
  });
  return { read: "summary.distribution", labels, arms, variables: labelled ? "labelled" : "unlabelled" };
}

/** A read as the test compares it: its kind, labels, arms and `variables`. */
function seen(read: ValuesRead): Expected {
  if (read.kind === "unread") {
    return { read: read.cause };
  }
  return {
    read: read.kind,
    labels: read.columns.map((label) => readText(label)),
    arms: read.positions.map((position) => position.columns.map((column) => (column.kind === "other" ? `other ${readText(column.name)}` : column.kind))),
    variables: read.variables,
  };
}

const RUNS = ORCHARD.flatMap((answer, index) => (answer.operation === "run_analysis" && answer.status === 200 ? [[index, answer.body] as const] : []));

/** The live answers by the index the server's test gives them (D434's values answers, from 25). */
const LIVE = (index: number): Json => JSON.parse(ORCHARD[index]?.body ?? "null") as Json;
const BUILT = 25;
const FLOOR_FIVE = 28;
const BINNED = 32;
const UNSORTED = 35;

describe("every live answer reads as its raw JSON says (D434)", () => {
  it("there are the live answers the server's test writes: fourteen runs, four of them before the values answers", () => {
    expect(RUNS.map(([index]) => index)).toEqual([6, 7, 8, 21, 25, 26, 27, 28, 29, 30, 31, 32, 33, 35]);
  });

  it("the answer whose columns are written in an order that is not sorted (j) reads its labels in that order, each over its own arm (the server's cell holds the labels' column types)", async () => {
    const [read] = await reads(LIVE(UNSORTED));
    expect(read === undefined ? null : seen(read)).toEqual({
      read: "summary.distribution",
      labels: ["trees.variety", "trees.height_m"],
      arms: [
        ["categories", "numbers"],
        ["categories", "numbers"],
      ],
      variables: "labelled",
    });
  });

  it.each(RUNS)("orchard answer %i", async (_index, body) => {
    const parsed = JSON.parse(body) as Json;
    const found = await reads(parsed);
    expect(found.map(seen)).toEqual(list(parsed["results"]).map((result) => expected(object(result))));
  });

  it("summary.members and compare.existence are unread (this page reads no values of theirs), and every summary.distribution answer reads, labelled", async () => {
    const kinds: string[] = [];
    for (const [, body] of RUNS) {
      for (const read of await reads(JSON.parse(body))) {
        kinds.push(read.kind === "unread" ? `unread ${read.cause}` : `${read.kind} ${read.variables}`);
      }
    }
    expect(kinds).toEqual(["summary.distribution labelled", "unread analysis", "unread analysis", ...Array<string>(11).fill("summary.distribution labelled")]);
  });

  it("the agent's document under a floor (h) reads, its numbers arm walked: every member null", async () => {
    const [read] = await reads(LIVE(BINNED));
    if (read?.kind !== "summary.distribution") {
      throw new Error("not read");
    }
    const [numbers] = read.positions[0]?.columns ?? [];
    expect(numbers?.kind).toBe("numbers");
    if (numbers?.kind === "numbers") {
      expect([numbers.value.n, numbers.value.mean, numbers.value.histogram, numbers.value.q1_bin]).toEqual([null, null, null, null]);
    }
  });

  it("the refused run (i) gives no result to read: it is a refusal", () => {
    const refused = ORCHARD[34];
    expect([refused?.operation, refused?.status]).toEqual(["run_analysis", 422]);
  });

  it("the labels are the canonical document's, not the document as written", async () => {
    const body = structuredClone(LIVE(BUILT));
    const result = object(list(body["results"])[0]);
    const written = object(object(result["source"])["document"]);
    const view = object(list(written["views"])[0]);
    object(view["params"])["columns"] = [{ column: "trees.variety" }, { column: "trees.height_m" }];
    const [read] = await reads(body);
    expect(read === undefined ? null : seen(read).labels).toEqual(["trees.height_m", "trees.variety"]);
  });

  it("two results of one answer each read their own values (a result's read is keyed by the walked result)", async () => {
    const body = structuredClone(LIVE(BUILT));
    const other = object(list(LIVE(FLOOR_FIVE)["results"])[0]);
    body["results"] = [list(body["results"])[0], other];
    const found = await reads(body);
    expect(found.map(seen)).toEqual(list(body["results"]).map((result) => expected(object(result))));
    expect(found[0]?.kind === "summary.distribution" && found[1]?.kind === "summary.distribution" ? [found[0].positions.length, found[1].positions[0]?.columns.length] : null).toEqual([2, 1]);
  });
});

/** A live answer whose result's values, labels and analysed counts are replaced. */
function spliced(values: unknown, minCellCount: number | null): Json {
  const body = structuredClone(LIVE(BUILT));
  const result = object(list(body["results"])[0]);
  result["values"] = values;
  const positions = list(object(values)["positions"]);
  const columns = list(object(positions[0])["columns"]).length;
  const count = columns === 0 ? 1 : columns;
  const derivation = object(result["derivation"]);
  object(derivation["disclosure"])["min_cell_count"] = minCellCount;
  object(object(object(derivation["document"])["view"])["params"])["columns"] = Array.from({ length: count }, (_unused, index) => ({ column: `t.c${String(index)}` }));
  for (const entry of list(result["analysed"])) {
    const variables = list(object(entry)["variables"]);
    object(entry)["variables"] = Array.from({ length: count }, () => variables[0]);
  }
  return body;
}

describe("synthetic bodies: every arm, with the disclosure setting unset and set (D434)", () => {
  /** The values models made from the table: each `DistributionValues` body, and each
   * `DistributionPosition` body (where the arms past the plans' reach are forced) as one position. */
  const made = bodies().flatMap((body) =>
    body.component === "DistributionValues" ? [body.value] : body.component === "DistributionPosition" ? [{ positions: [body.value], view: {} }] : [],
  );

  it("every DistributionValues body reads with arms equal to its raw kinds, and the counters reach every case", async () => {
    expect(made.length).toBeGreaterThanOrEqual(10);
    const kinds = new Set<string>();
    const reached = new Set<string>();
    const pointers = new Set<string>();
    const note = (column: ColumnRead, floor: number | null): void => {
      reached.add(`${column.kind} k ${floor === null ? "unset" : "set"}`);
      if (column.kind === "other") {
        kinds.add(readText(column.name));
        return;
      }
      kinds.add(column.kind);
      for (const [key] of column.value.not_estimable === undefined ? [] : entriesOf(column.value.not_estimable)) {
        pointers.add(`${column.kind} ${readText(key)}`);
      }
      if (column.kind === "numbers") {
        for (const bin of column.value.histogram?.bins ?? []) {
          reached.add(bin.low === null ? "low null" : "low");
          reached.add(bin.high === null ? "high null" : "high");
        }
      } else {
        for (const row of column.value.categories ?? []) {
          reached.add(row.other_values === true ? "other_values" : row.values.length === 0 ? "names none" : "named");
        }
      }
    };
    const unlabelled = structuredClone(LIVE(BUILT));
    delete object(list(object(list(unlabelled["results"])[0])["analysed"])[0])["variables"];
    // A row that names no value and is no `other_values` row (m14): no body made from the table has
    // one, so it is made from the floor-5 answer (d) by dropping the member.
    const namesNone = structuredClone(LIVE(FLOOR_FIVE));
    const row = object(list(object(list(object(list(object(object(list(namesNone["results"])[0])["values"])["positions"])[0])["columns"])[0])["categories"])[0]);
    delete row["other_values"];
    expect(row["values"]).toEqual([]);
    for (const [floor, body] of [...made.flatMap((one) => [null, 5].map((k) => [k, spliced(one, k)] as const)), [null, unlabelled] as const, [5, namesNone] as const]) {
      const found = await reads(body);
      const [want] = list(body["results"]).map((result) => expected(object(result)));
      expect(found.map(seen)).toEqual([want]);
      const [read] = found;
      if (read?.kind === "summary.distribution") {
        reached.add(`variables ${read.variables}`);
        read.positions.forEach((position) => {
          position.columns.forEach((column) => {
            note(column, floor);
          });
        });
      }
    }
    expect([...kinds].sort()).toEqual(["categories", "category_rows", "memberships", "number_rows", "numbers"]);
    expect([...reached].sort()).toEqual(
      ["categories k set", "categories k unset", "high", "high null", "low", "low null", "named", "names none", "numbers k set", "numbers k unset", "other k set", "other k unset", "other_values", "variables labelled", "variables unlabelled"].sort(),
    );
    const every = [...VOCABULARIES["NumberDistribution.not_estimable{key}"].map((key) => `numbers ${key}`), ...VOCABULARIES["CategoryDistribution.not_estimable{key}"].map((key) => `categories ${key}`)];
    expect(every.length).toBe(13);
    expect([...pointers].sort()).toEqual(every.sort());
  });
});

describe("misses (D434)", () => {
  /** The first live answer's raw body, changed. */
  const changed = (change: (result: Json) => void): Json => {
    const body = structuredClone(LIVE(BUILT));
    change(object(list(body["results"])[0]));
    return body;
  };
  const columnOf = (result: Json, position: number, column: number): Json => object(list(object(list(object(result["values"])["positions"])[position])["columns"])[column]);
  const labelsOf = (result: Json): unknown[] => list(object(object(object(object(result["derivation"])["document"])["view"])["params"])["columns"]);

  it.each([
    ["a member renamed", (r: Json) => { const c = columnOf(r, 0, 0); c["meen"] = c["mean"]; delete c["mean"]; }, "shape"],
    ["a member added", (r: Json) => { columnOf(r, 1, 1)["extra"] = 1; }, "shape"],
    ["a kind changed", (r: Json) => { columnOf(r, 0, 1)["kind"] = "category"; }, "shape"],
    ["a kind swapped to the other arm the page reads", (r: Json) => { columnOf(r, 0, 0)["kind"] = "categories"; }, "shape"],
    ["a number as a string", (r: Json) => { columnOf(r, 0, 0)["n"] = "12"; }, "shape"],
    ["a position of no columns", (r: Json) => { object(r["values"])["positions"] = [{}]; }, "shape"],
    ["a position with a column fewer", (r: Json) => { list(object(list(object(r["values"])["positions"])[1])["columns"]).pop(); }, "shape"],
    ["the labels one short", (r: Json) => { labelsOf(r).pop(); }, "shape"],
    ["the labels one long", (r: Json) => { labelsOf(r).push({ column: "trees.tags" }); }, "shape"],
    ["the labels none", (r: Json) => { labelsOf(r).splice(0); }, "shape"],
    ["the labels none and the positions none (a bound the walk does not read)", (r: Json) => { labelsOf(r).splice(0); object(r["values"])["positions"] = []; }, "shape"],
    ["a label that is not text", (r: Json) => { object(labelsOf(r)[0])["column"] = 7; }, "shape"],
    ["another version", (r: Json) => { object(object(r["derivation"])["analysis"])["version"] = "1.0.1"; }, "analysis"],
    ["another analysis", (r: Json) => { object(object(r["derivation"])["analysis"])["id"] = "summary.members"; }, "analysis"],
    ["the analysis and version joined differently", (r: Json) => { Object.assign(object(object(r["derivation"])["analysis"]), { id: "summary.distribution@1.0", version: "0" }); }, "analysis"],
  ])("%s is unread", async (_case, change, cause) => {
    const [read] = await reads(changed(change));
    expect(read).toEqual({ kind: "unread", cause });
  });

  it("the unchanged body reads (the misses' control)", async () => {
    const [read] = await reads(changed(() => undefined));
    expect(read?.kind).toBe("summary.distribution");
  });

  it("an analysed count with its variables one fewer reads them unlabelled, and the values still read", async () => {
    const [read] = await reads(changed((r) => { list(object(list(r["analysed"])[1])["variables"]).pop(); }));
    expect(read?.kind === "summary.distribution" ? [read.variables, read.positions.length] : null).toEqual(["unlabelled", 2]);
  });

  it("a copied or composed result throws notAResult; the walked one reads", async () => {
    const answer = await ran(JSON.stringify(LIVE(BUILT)));
    const [result] = answer.results;
    if (result === undefined) {
      throw new Error("no result");
    }
    expect(valuesOf(result).kind).toBe("summary.distribution");
    const forged: readonly Result[] = [{ ...result }, Object.freeze({ ...result }), Object.create(result) as Result, structuredClone(result)];
    for (const one of forged) {
      expect(() => valuesOf(one)).toThrow(ClientError);
      expect(() => valuesOf(one)).toThrow(REFUSALS.notAResult);
    }
  });

  it("a forged result thrown during render shows the route's error element, Failure's fixed words, and no text of the error", async () => {
    const answer = await ran(JSON.stringify(LIVE(BUILT)));
    const [result] = answer.results;
    if (result === undefined) {
      throw new Error("no result");
    }
    const copy: Result = { ...result };
    function Forged() {
      const read = valuesOf(copy);
      return <p>{read.kind}</p>;
    }
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const router = createMemoryRouter([{ errorElement: <Failure />, children: [{ index: true, element: <Forged /> }] }]);
    const shown = await render(<RouterProvider router={router} />);
    try {
      expect(shown.words()).toBe("aibi This screen failed to load. Reload the page to try again.");
      expect(shown.words()).not.toContain(REFUSALS.notAResult);
      expect(shown.words()).not.toContain("D434");
    } finally {
      shown.unmount();
      error.mockRestore();
    }
  });

  it("VALUES lists the one published values model this page reads", () => {
    expect({ ...VALUES }).toEqual({ "summary.distribution@1.0.0": "DistributionValues" });
  });
});
