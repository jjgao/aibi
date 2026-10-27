/**
 * The screens' views swept by construction (D428's harness, D431, D443): `Hits` (the catalogue),
 * `Description` (the dataset) and `ColumnDescription` (the column) are each given the bodies
 * `typed-support.ts` makes from the table
 * (`synthetic`, built to reach every position) under every plan (each arm of an `or`, each kind of
 * segment), in full and minimal, walked as the seam walks them, and every `ServerText` leaf of each
 * is replaced in turn by every hostile row (`leafSweep`): no element, span or raw unsafe code point
 * the text spelled. `StatCount`, `Graph` and `Tables` are swept within `Description` here and alone
 * in their own files.
 *
 * **Coverage.** Every `ServerText` position the table reaches from either answer outside an
 * `x-aibi-json` value's interior (string and enum members, constants, map keys and segment texts,
 * as paths with `[]` and `{key}`/`{}`) must be among the sweeps' leaves, united over the plans; the
 * cell names any missing.
 *
 * **What is not shown raw, asserted exactly (m5, n7).** A second sweep replaces each leaf by a
 * sentinel row that must render raw: the positions where it never shows, united over the plans,
 * are exactly the named exceptions E1–E5, each present, no more:
 * - E1 every leaf under `descriptor`, `tables[].descriptor`, `relationships[]`, `coverage[]` and
 *   `endpoints[]` (descriptor members, not shown in this PR: T8's words; M5.2-rest);
 * - E2 `caveats[].severity` (read fail-closed: an unknown severity is `block`, R23);
 * - E3 `release.label`'s text arm, in both answers, and `release.status` in `DatasetDescription`
 *   alone (`ReleaseRef` shows a disagreement as `@draft` with `DISAGREE`, R27; the catalogue shows an
 *   unknown status as its text, R52);
 * - E4 the keys of a row count's `not_estimable` (read by the page's word `/count`: another key is
 *   no reason, so the null is suppressed, R14).
 * - E5 (`ColumnDescription` alone, D443) every leaf under its `descriptor` (not shown: T8's words),
 *   its distribution's `kind` (the arms are told apart by the member each alone has; the constant is
 *   not shown), `release.status` (as E3), the keys of every count's `not_estimable` (as E4), and the
 *   reasons of a category's and of the pooled row's `not_estimable`: these two are shown (a null count
 *   with its reason at `/count`, through `StatCount`, as a bin's is), but no plan reaches them, since
 *   a plan takes the same arm at every alternative and the categories are the distribution's first
 *   arm, where every count is a number; `column.test.tsx` holds both shown, as text, with a recorded
 *   pooled reason and a hostile one.
 *
 * **The cut (R01; round 1 of #123, m1).** A third sweep puts the 5,000-character row at every leaf,
 * and asserts, by the leaf's shape in the table, how many cut marks the rendering holds: **every leaf
 * whose shape is `Data` (the text of a descriptor, which the server's page cuts) renders exactly one
 * `.cut`**, and every other leaf renders none (ids, references, pointers and the words of a
 * vocabulary are shown whole), but the members of the page's vocabularies, which an unknown member
 * shows as text (`VOCABULARY`, asserted exactly): a leaf not shown (E1-E5) renders none. A screen
 * that showed a `Data` leaf through `Ref`, or an id through `Text`, fails here.
 *
 * The orchard's own answers are swept too, over a few rows (the smoke test).
 */
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { boxMap, boxText, type ServerText } from "../../src/api/box";
import { type Shape, SHAPES } from "../../src/api/generated/shapes";
import type { Answered } from "../../src/api/typed";
import { Hits } from "../../src/catalogue/Catalogue";
import { ColumnDescription } from "../../src/dataset/Column";
import { Description } from "../../src/dataset/Dataset";
import { Ref, Text } from "../../src/layout/Text";
import { walked } from "./answers";
import { HOSTILE, leafSweep, type Row, type Swept as Sweep } from "./injection";
import { render } from "./render";
import { notShownProblems, orchardText } from "./screens";
import { PLANS, REF, synthetic, UNMADE } from "./typed-support";

/** The tags the screens write around the visible form, and their spans' classes. */
const OWN = ["MAIN", "H1", "H2", "H3", "P", "SECTION", "DIV", "A"];
const CLASSES = ["", "list", "more", "table", "stat", "suppressed", "not-estimable", "not-shown", "release", "label", "label draft", "disagree", "affects", "number unread"];

/** The row of 5,000 characters: past the cut at 2,000 code points. */
const LONG: readonly Row[] = HOSTILE.filter((row) => row.name === "5,000 characters");

/** The positions of a vocabulary's member (`kindOf`), which a member the page does not know shows as
 * a text through `Text`, so that it is cut: a release's status, a table's role, an analysis's status,
 * a relationship's cardinality, a column's datatype, the column screen's `edges_from` and its no
 * distribution's `reason` (D443), and the reason of a count that is not estimable (a row count, a
 * column's rows, states and bins: the word `StatCount` shows for it, in the plans where the count is
 * `null` with a reason at `/count`: there at most once, and in some plan once, `CONDITIONAL`).
 * Asserted exactly. */
const VOCABULARY: Readonly<Record<string, readonly string[]>> = {
  CatalogHits: ["/hits/[]/release/status", "/hits/[]/tables/[]/role", "/hits/[]/tables/[]/rows/not_estimable/{}"],
  DatasetDescription: ["/applicable_analyses/[]/status", "/graph/edges/[]/cardinality", "/tables/[]/columns/[]/datatype", "/tables/[]/rows/not_estimable/{}"],
  ColumnDescription: ["/statistics/distribution/bins/[]/not_estimable/{}", "/statistics/distribution/edges_from", "/statistics/distribution/reason", "/statistics/rows/not_estimable/{}", "/statistics/states/NOT_APPLICABLE/not_estimable/{}", "/statistics/states/NOT_ASSESSED/not_estimable/{}", "/statistics/states/PRESENT/not_estimable/{}", "/statistics/states/UNKNOWN/not_estimable/{}"],
};

/** The vocabulary positions a plan shows or not, by its arm of the shape: cut once where shown. */
const CONDITIONAL: ReadonlySet<string> = new Set(["/hits/[]/tables/[]/rows/not_estimable/{}", "/tables/[]/rows/not_estimable/{}", "/statistics/distribution/bins/[]/not_estimable/{}", "/statistics/rows/not_estimable/{}", "/statistics/states/NOT_APPLICABLE/not_estimable/{}", "/statistics/states/NOT_ASSESSED/not_estimable/{}", "/statistics/states/PRESENT/not_estimable/{}", "/statistics/states/UNKNOWN/not_estimable/{}"]);

/** The positions of every `Data` leaf the screens show (the text of a descriptor, cut by R01), asserted
 * exactly: the catalogue's hits (label, name, description, tags, facets' values, data-use terms,
 * tables' labels and grains), the dataset's tables' labels and columns' labels, and the column's
 * label, its categories' values, its bins' edges and its smallest and largest values (D443: a date or
 * an instant, data; a number edge is no leaf). */
const DATA_SHOWN: Readonly<Record<string, readonly string[]>> = {
  CatalogHits: [
    "/hits/[]/label/data",
    "/hits/[]/name/data",
    "/hits/[]/description/data",
    "/hits/[]/domain_tags/[]/data",
    "/hits/[]/facets/{}/[]/data",
    "/hits/[]/data_use/[]/label/data",
    "/hits/[]/data_use/[]/system/data",
    "/hits/[]/data_use/[]/code/data",
    "/hits/[]/tables/[]/label/data",
    "/hits/[]/tables/[]/grain/data",
  ],
  DatasetDescription: ["/tables/[]/label/data", "/tables/[]/columns/[]/label/data"],
  ColumnDescription: ["/label/data", "/statistics/distribution/bins/[]/high/data", "/statistics/distribution/bins/[]/low/data", "/statistics/distribution/categories/[]/value/data", "/statistics/distribution/max/value/data", "/statistics/distribution/min/value/data"],
};

/** A row that must render raw and appears nowhere else: where it does not show, its leaf is not shown. */
const SENTINEL: Row = { name: "sentinel", text: "\ua66e", raw: true };

type Swept = { readonly component: string; readonly make: (value: never) => ReactElement };

const SCREENS: readonly Swept[] = [
  { component: "CatalogHits", make: (value: Answered<"search_catalog">) => <Hits answer={value} /> },
  { component: "DatasetDescription", make: (value: Answered<"describe_dataset">) => <Description answer={value} /> },
  { component: "ColumnDescription", make: (value: Answered<"describe_column">) => <ColumnDescription answer={value} /> },
];

// ---------------------------------------------------------------------------------------------
// Positions, from the table and from a leaf's path.

/** A shape with its references followed. */
function resolved(shape: Shape): Shape {
  let found = shape;
  for (let depth = 0; found.t === "ref" && depth < 50; depth += 1) {
    const next = SHAPES[found.n];
    if (next === undefined) {
      return found;
    }
    found = next;
  }
  return found;
}

/** Every `ServerText` position a shape reaches, as a path: `[]` an item, `{key}` a map's key, `{}`
 * a map's value, `*` an `x-aibi-json` value (its interior not followed). */
function positionsOf(shape: Shape, at: string, out: Set<string>, depth = 0): Set<string> {
  if (depth > 40) {
    return out;
  }
  const found = resolved(shape);
  switch (found.t) {
    case "str":
    case "enum":
      out.add(at);
      break;
    case "const":
      if (typeof found.v === "string") {
        out.add(at);
      }
      break;
    case "seg":
      out.add(`${at}/text`);
      out.add(`${at}/data`);
      break;
    case "json":
    case "any":
      out.add(`${at}/*`);
      break;
    case "arr":
      for (const item of [found.i, ...(found.pi ?? [])]) {
        positionsOf(item, `${at}/[]`, out, depth + 1);
      }
      break;
    case "rec":
      for (const [name, member] of Object.entries(found.p)) {
        positionsOf(member, `${at}/${name}`, out, depth + 1);
      }
      break;
    case "map":
      out.add(`${at}/{key}`);
      for (const value of [found.v, ...(found.pp ?? []).map(([, entry]) => entry)]) {
        positionsOf(value, `${at}/{}`, out, depth + 1);
      }
      break;
    case "or":
    case "all":
      for (const arm of found.o) {
        positionsOf(arm, at, out, depth + 1);
      }
      break;
    default:
      break;
  }
  return out;
}

/** One step of a leaf's path through a shape: the position's token and the shape below, or `null`. */
function step(shape: Shape, segment: string): { readonly token: string; readonly next: Shape | null } | null {
  const found = resolved(shape);
  switch (found.t) {
    case "json":
    case "any":
      return { token: "*", next: null };
    case "arr":
      return /^\d+$/u.test(segment) ? { token: "[]", next: found.pi?.[Number(segment)] ?? found.i } : null;
    case "rec": {
      const member = Object.hasOwn(found.p, segment) ? found.p[segment] : undefined;
      return member === undefined ? null : { token: segment, next: member };
    }
    case "map":
      if (segment.startsWith("#key")) {
        return { token: "{key}", next: null };
      }
      return segment.startsWith("#val") ? { token: "{}", next: found.v.t === "never" ? (found.pp?.[0]?.[1] ?? found.v) : found.v } : null;
    case "seg":
      return segment === "text" || segment === "data" ? { token: segment, next: null } : null;
    case "or":
    case "all":
      for (const arm of found.o) {
        const taken = step(arm, segment);
        if (taken !== null) {
          return taken;
        }
      }
      return null;
    default:
      return null;
  }
}

/** A leaf's path as a position of the table (as `positionsOf` writes them). */
function positionOf(component: string, path: readonly string[], root: Shape = REF(component)): string {
  let shape: Shape | null = root;
  let at = "";
  for (const segment of path) {
    if (shape === null) {
      break;
    }
    const taken = step(shape, segment);
    if (taken === null) {
      return `${at}/?${segment}`;
    }
    at = `${at}/${taken.token}`;
    if (taken.token === "*") {
      break;
    }
    shape = taken.next;
  }
  return at;
}

/** Whether a shape is, or holds in any arm of an `or` or an `all` (references followed), `Data`: a
 * required `Data | None` is shown through the same text as a `Data` (round 2 of #123, r2-m3). */
function holdsData(shape: Shape, depth = 0): boolean {
  if (depth > 50) {
    return false;
  }
  if (shape.t === "ref") {
    const next = SHAPES[shape.n];
    return shape.n === "Data" || (next !== undefined && holdsData(next, depth + 1));
  }
  return (shape.t === "or" || shape.t === "all") && shape.o.some((arm) => holdsData(arm, depth + 1));
}

/** Whether a leaf's shape is `Data`: the `data` member of a `Data` record (the shape of a descriptor's
 * text, named in the table, alone or in any arm of an `or` or an `all`), found by the path's steps
 * through the table (from `root`, a component's). */
function isDataLeaf(component: string, path: readonly string[], root: Shape = REF(component)): boolean {
  let shape: Shape | null = root;
  for (const segment of path.slice(0, -1)) {
    if (shape === null) {
      return false;
    }
    shape = step(shape, segment)?.next ?? null;
  }
  return shape !== null && holdsData(shape) && path.at(-1) === "data";
}

/** The leaf paths of a sweep's problems, as positions. */
function problemPositions(component: string, problems: readonly string[]): Set<string> {
  return new Set(
    problems.map((problem) => {
      const found = /^@\/(.*?): sentinel: /u.exec(problem);
      if (found?.[1] === undefined) {
        throw new Error(`not a sentinel problem: ${problem}`);
      }
      return positionOf(component, found[1] === "" ? [] : found[1].split("/"));
    }),
  );
}

// ---------------------------------------------------------------------------------------------
// The exceptions.

const EXCEPTIONS: readonly (readonly [string, (component: string, position: string) => boolean])[] = [
  ["E5 the column's descriptor, its distribution's kind, and the reasons no plan reaches", (component, position) => component === "ColumnDescription" && (/^\/descriptor\//u.test(position) || position === "/statistics/distribution/kind" || position === "/release/status" || /\/not_estimable\/\{key\}$/u.test(position) || /^\/statistics\/distribution\/(?:categories\/\[\]|pooled)\/not_estimable\/\{\}$/u.test(position))],
  ["E1 descriptor members", (component, position) => component === "DatasetDescription" && /^\/(?:descriptor|tables\/\[\]\/descriptor|relationships|coverage|endpoints)\//u.test(position)],
  ["E2 a caveat's severity", (_component, position) => position === "/caveats/[]/severity"],
  ["E3 a release's label as text", (_component, position) => position === "/hits/[]/release/label" || position === "/release/label"],
  ["E3 the dataset's release status", (component, position) => component === "DatasetDescription" && position === "/release/status"],
  ["E4 the keys of a row count's not_estimable", (_component, position) => position === "/hits/[]/tables/[]/rows/not_estimable/{key}" || position === "/tables/[]/rows/not_estimable/{key}"],
];

const exceptionOf = (component: string, position: string): string | null => EXCEPTIONS.find(([, holds]) => holds(component, position))?.[0] ?? null;

/** The cut sweep (R01; m1 of round 1 of #123): the 5,000-character row at every leaf of `value`, and
 * every rendering held to the count of cut marks its leaf's position calls for: one at a `Data` leaf
 * that is shown, and none at any other leaf but a vocabulary's unknown member (shown as text, one),
 * with the marks the body itself holds (a segment the server cut shows the server's own) taken off.
 * The positions that cut are added to `cutData` and `cutVocabulary`. */
async function cutSweep(component: string, make: (value: never) => ReactElement, value: never, cutData: Set<string>, cutVocabulary: Set<string>, root: Shape = REF(component)): Promise<Sweep> {
  const plain = await render(make(value));
  const baseline = plain.container.querySelectorAll(".cut").length;
  plain.unmount();
  return leafSweep(value, make, OWN, CLASSES, LONG, (path, _row, container) => {
    const position = positionOf(component, path, root);
    const data = isDataLeaf(component, path, root);
    const vocabulary = VOCABULARY[component]?.includes(position) === true;
    const cut = exceptionOf(component, position) === null && (data || vocabulary);
    const allowed = cut ? (CONDITIONAL.has(position) ? [0, 1] : [1]) : [0];
    const cuts = container.querySelectorAll(".cut").length - baseline;
    if (cut && cuts === 1) {
      (data ? cutData : cutVocabulary).add(position);
    }
    return allowed.includes(cuts) ? [] : [`${String(cuts)} cut marks, ${allowed.join(" or ")} wanted (${data ? "a Data leaf" : vocabulary ? "a vocabulary's member" : "not a Data leaf"} at ${position})`];
  });
}

// ---------------------------------------------------------------------------------------------

describe.each(SCREENS)("$component's view, swept by construction", ({ component, make }) => {
  const bodies = PLANS.flatMap((plan) => [false, true].map((minimal) => ({ name: `plan ${String(plan.prefer)}${minimal ? " minimal" : ""}`, body: synthetic(REF(component), 0, minimal, plan) })));
  const swept = new Set<string>();
  const shown = new Set<string>();
  const cutData = new Set<string>();
  const cutVocabulary = new Set<string>();

  it.each(bodies)("shows a hostile text in every position as text alone: $name", { timeout: 600_000 }, async ({ body }) => {
    expect(body).not.toBe(UNMADE);
    const value = walked(component, JSON.stringify(body)) as never;
    const found = await leafSweep(value, make, OWN, CLASSES);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
    const sentinel = await leafSweep(value, make, OWN, CLASSES, [SENTINEL]);
    expect(sentinel.problems.filter((problem) => !/: sentinel: U\+a66e not raw$/u.test(problem))).toEqual([]);
    const notShown = problemPositions(component, sentinel.problems);
    // Every leaf's position: a rendering that shows none, so that each leaf is a problem at its own path.
    const all = await leafSweep(value, () => <p>{"\ua66f"}</p>, ["P"], [], [SENTINEL]);
    for (const position of problemPositions(component, all.problems)) {
      swept.add(position);
      if (!notShown.has(position)) {
        shown.add(position);
      }
    }
  });

  it.each(bodies)("cuts every Data leaf once, and no other leaf but a vocabulary's unknown member: $name", { timeout: 600_000 }, async ({ body }) => {
    const value = walked(component, JSON.stringify(body)) as never;
    const found = await cutSweep(component, make, value, cutData, cutVocabulary);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
  });

  it.each(bodies)("a dataset page says once that members are not shown, carrying the dataset: $name", async ({ body }) => {
    if (component !== "DatasetDescription") {
      return;
    }
    const dataset = (body as { readonly dataset?: unknown }).dataset;
    expect(typeof dataset).toBe("string");
    const shown = await render(make(walked(component, JSON.stringify(body)) as never));
    expect(notShownProblems(shown.container, dataset as string)).toEqual([]);
    shown.unmount();
  });

  it("cuts Data leaves at every position the screens show one, and the vocabularies' members at the named positions alone (united over the plans)", () => {
    expect([...cutData].sort()).toEqual([...(DATA_SHOWN[component] ?? [])].sort());
    expect([...cutVocabulary].sort()).toEqual([...(VOCABULARY[component] ?? [])].sort());
  });

  it("covers every ServerText position the table reaches, outside x-aibi-json interiors (united over the plans)", () => {
    const required = [...positionsOf(REF(component), "", new Set())].filter((position) => !position.includes("*"));
    expect(required.length).toBeGreaterThan(10);
    expect(required.filter((position) => !swept.has(position))).toEqual([]);
  });

  it("shows every position raw but the named exceptions E1-E5, each present (united over the plans)", () => {
    const never = [...swept].filter((position) => !shown.has(position));
    expect(never.filter((position) => exceptionOf(component, position) === null)).toEqual([]);
    // Each exception's positions in this answer are never shown, and each exception of this answer is present.
    expect([...swept].filter((position) => exceptionOf(component, position) !== null && !never.includes(position))).toEqual([]);
    const present = new Set(never.map((position) => exceptionOf(component, position)));
    const expected = component === "CatalogHits" ? ["E2 a caveat's severity", "E3 a release's label as text", "E4 the keys of a row count's not_estimable"] : component === "ColumnDescription" ? ["E2 a caveat's severity", "E3 a release's label as text", "E5 the column's descriptor, its distribution's kind, and the reasons no plan reaches"] : EXCEPTIONS.map(([name]) => name).filter((name) => !name.startsWith("E5"));
    expect([...present].sort()).toEqual([...expected].sort());
  });
});

describe("the cut sweep looks through an `or` and an `all` around `Data` (r2-m3: a required `Data | None`)", () => {
  /** A synthetic component: one member of each wrapping of `Data`, and one that is not. */
  const ROOT: Shape = {
    t: "rec",
    p: {
      alone: { t: "ref", n: "Data" },
      maybe: { t: "or", o: [{ t: "ref", n: "Data" }, { t: "null" }] },
      both: { t: "all", o: [{ t: "ref", n: "Data" }] },
      nested: { t: "or", o: [{ t: "null" }, { t: "all", o: [{ t: "ref", n: "Data" }] }] },
      plain: { t: "or", o: [{ t: "str" }, { t: "null" }] },
    },
    r: ["alone", "maybe", "both", "nested", "plain"],
  };
  const NAMES = ["alone", "maybe", "both", "nested"] as const;
  const value = (): never =>
    ({
      alone: { data: boxText("a") },
      maybe: { data: boxText("m") },
      both: { data: boxText("b") },
      nested: { data: boxText("n") },
      plain: boxText("p"),
    }) as never;
  const through = (shown: "Text" | "Ref") => (one: Record<string, { data: ServerText }> & { plain: ServerText }) => (
    <main>
      {NAMES.map((name) => (shown === "Text" ? <Text key={name} value={one[name]?.data ?? boxText("")} /> : <Ref key={name} value={one[name]?.data ?? boxText("")} />))}
      <Ref value={one.plain} />
    </main>
  );

  it("takes each wrapping's `data` for a Data leaf, and a plain `or` for none", () => {
    for (const name of NAMES) {
      expect(isDataLeaf("Synthetic", [name, "data"], ROOT), name).toBe(true);
    }
    expect(isDataLeaf("Synthetic", ["plain"], ROOT)).toBe(false);
    expect(isDataLeaf("Synthetic", ["maybe", "other"], ROOT)).toBe(false);
  });

  it("holds a Data | None shown through Text to one cut, and fails the same shown through Ref", async () => {
    const cut = new Set<string>();
    const right = await cutSweep("Synthetic", through("Text") as never, value(), cut, new Set<string>(), ROOT);
    expect(right.problems).toEqual([]);
    expect([...cut].sort()).toEqual(["/alone/data", "/both/data", "/maybe/data", "/nested/data"]);
    const wrong = await cutSweep("Synthetic", through("Ref") as never, value(), new Set<string>(), new Set<string>(), ROOT);
    expect(wrong.problems.filter((problem) => /0 cut marks, 1 wanted \(a Data leaf at \/(?:alone|maybe|both|nested)\/data\)/u.test(problem))).toHaveLength(4);
  });
});

describe("the orchard's own answers, swept (the smoke test)", () => {
  const ROWS = HOSTILE.filter((row) => ["an img with onerror", "a right-to-left override", "the isolate's end tag"].includes(row.name));

  it("search_catalog's answers", { timeout: 600_000 }, async () => {
    for (const index of [0, 1, 2]) {
      const found = await leafSweep(walked("CatalogHits", orchardText("search_catalog", index)) as Answered<"search_catalog">, (value) => <Hits answer={value} />, OWN, CLASSES, ROWS);
      expect(found.problems).toEqual([]);
    }
  });

  it("search_catalog's answers, cut at every Data leaf: the hit's label through its link (a dataset that is an id, which no synthetic body makes)", { timeout: 600_000 }, async () => {
    const cutData = new Set<string>();
    for (const index of [0, 1, 2]) {
      const value = walked("CatalogHits", orchardText("search_catalog", index)) as Answered<"search_catalog">;
      const found = await cutSweep("CatalogHits", (one: Answered<"search_catalog">) => <Hits answer={one} />, value as never, cutData, new Set<string>());
      expect(found.problems).toEqual([]);
    }
    expect(["/hits/[]/label/data", "/hits/[]/tables/[]/label/data"].filter((position) => !cutData.has(position))).toEqual([]);
  });

  it("describe_dataset's answers (whole, paged, under a floor) each say once that members are not shown, carrying the dataset", async () => {
    for (const index of [0, 1, 2]) {
      const shown = await render(<Description answer={walked("DatasetDescription", orchardText("describe_dataset", index)) as Answered<"describe_dataset">} />);
      expect(notShownProblems(shown.container, "orchard"), `answer ${String(index)}`).toEqual([]);
      shown.unmount();
    }
  });

  it("describe_dataset's answer, past its descriptors", { timeout: 600_000 }, async () => {
    const answer = walked("DatasetDescription", orchardText("describe_dataset", 2)) as Answered<"describe_dataset">;
    // Its descriptors emptied (E1: not shown), the rest of the server's answer as it is.
    const none: typeof answer.descriptor = boxMap([]);
    const shown = { ...answer, descriptor: none, relationships: [], coverage: [], endpoints: [], tables: answer.tables.map((table) => ({ ...table, descriptor: none })) } as typeof answer;
    const found = await leafSweep(shown, (value) => <Description answer={value} />, OWN, CLASSES, ROWS);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(20);
    const cutData = new Set<string>();
    const cut = await cutSweep("DatasetDescription", (value: Answered<"describe_dataset">) => <Description answer={value} />, shown as never, cutData, new Set<string>());
    expect(cut.problems).toEqual([]);
    expect([...cutData]).toEqual(["/tables/[]/label/data", "/tables/[]/columns/[]/label/data"]);
  });
});
