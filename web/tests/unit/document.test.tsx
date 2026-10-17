/**
 * The document seam (D432): `optionsOf` held to an oracle of its own over the orchard's answers (the
 * whole one, its first page, under a floor), the table's synthetic answers (every plan, full and
 * minimal) and crafted ones; `optionsOf` mints from an answer the walk returned alone (`walkedAs`,
 * minor 7 of the plan's round 3), memoised; and every draft that is not one refused with
 * `notAChoice`, before anything is sent.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { readText, type ServerText } from "../../src/api/box";
import { ClientError } from "../../src/api/client";
import { countDraft, type Draft, type Options, optionsOf, runDraft, validateDraft } from "../../src/api/document";
import { SHAPES } from "../../src/api/generated/shapes";
import { idOfAddress } from "../../src/api/text";
import { searchCatalog, walkComponent } from "../../src/api/typed";
import type { Typed } from "../../src/layout/Field";
import { decode } from "../../src/api/decode";
import { DESCRIBED, described, type ExpectedOptions, expectedOptions, type Typist, typist } from "./document-support";
import { json, orchardText, serving } from "./screens";
import { PLANS, REF, synthetic, UNMADE } from "./typed-support";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** What `optionsOf` gave, by text: the oracle's form. */
function byText(options: Options): ExpectedOptions {
  const text = (box: ServerText): string => readText(box);
  return {
    dataset: options.dataset,
    units: options.units.map((unit) => ({ label: text(unit.label), view: unit.view.map((option) => text(option.label)) })),
    leaves: options.leaves.map((option) => text(option.label)),
    left: { ...options.left },
    firstPage: options.firstPage,
    underK: options.underK,
  };
}

/** The whole orchard answer, edited as a server could answer (each a text the walk takes). */
function crafted(edit: (answer: { dataset: string; tables: { columns: { id: string; datatype?: string; label: unknown; identifier: boolean }[] }[]; graph: { tables: string[] }; columns_next?: number; disclosure: { min_cell_count: number | null } }) => void): string {
  const answer = JSON.parse(DESCRIBED.whole) as Parameters<typeof edit>[0];
  edit(answer);
  return JSON.stringify(answer);
}

const column = (id: string, datatype?: string): { id: string; datatype?: string; label: unknown; identifier: boolean } => ({ id, ...(datatype === undefined ? {} : { datatype }), label: { data: id }, identifier: false });

const CRAFTED: readonly (readonly [string, string])[] = [
  ["a column of another table listed under the unit's", crafted((a) => a.tables[1]?.columns.push(column("harvests.weight_kg", "number")))],
  ["a column whose table part is a prefix of the unit's", crafted((a) => a.tables[1]?.columns.push(column("tree.variety2", "category")))],
  ["a column whose table part extends the unit's", crafted((a) => a.tables[1]?.columns.push(column("treesx.variety", "category")))],
  ["a column with no datatype", crafted((a) => a.tables[1]?.columns.push(column("trees.unknown")))],
  ["columns of every datatype", crafted((a) => a.tables[1]?.columns.push(...["number", "integer", "string", "boolean", "category", "list<category>", "date", "datetime", "time_offset"].map((type) => column(`trees.c_${type.replace(/[<>]/gu, "_")}`, type))))],
  ["column ids off the grammar", crafted((a) => a.tables[1]?.columns.push(...["trees.Variety", "trees.a__b", "trees", ".x", "trees.x.y", "trees.<img>", `trees.${"a".repeat(64)}`, "trees.\u202Ex"].map((id) => column(id, "category"))))],
  ["graph tables off the grammar", crafted((a) => a.graph.tables.push("Trees", "a__b", "<img src=x>", "", "x".repeat(65)))],
  ["a graph table with no columns", crafted((a) => a.graph.tables.push("sites"))],
  ["a dataset id off the grammar", crafted((a) => (a.dataset = "Orchard!"))],
  ["under a floor of 5", crafted((a) => (a.disclosure.min_cell_count = 5))],
  ["a first page", crafted((a) => (a.columns_next = 3))],
  ["no graph table", crafted((a) => (a.graph.tables = []))],
  ["every column a category", crafted((a) => {
      for (const table of a.tables) {
        for (const one of table.columns) {
          one.datatype = "category";
        }
      }
    })],
];

/** The table's synthetic describe answers: every plan, full and minimal. */
const SYNTHETIC: readonly (readonly [string, string])[] = PLANS.flatMap((plan) =>
  [false, true].flatMap((minimal) => {
    const value = synthetic(REF("DatasetDescription"), 0, minimal, plan);
    return value === UNMADE ? [] : [[`synthetic ${String(plan.prefer)}${minimal ? " minimal" : ""}`, JSON.stringify(value)] as const];
  }),
);

describe("optionsOf, held to an oracle of its own (minor 5 of round 3)", () => {
  const CASES: readonly (readonly [string, string])[] = [["the orchard", DESCRIBED.whole], ["the orchard's first page", DESCRIBED.firstPage], ["the orchard under a floor of 50", DESCRIBED.floored], ...SYNTHETIC, ...CRAFTED];

  it.each(CASES)("%s", async (_name, text) => {
    const options = optionsOf(await described(text));
    expect(byText(options)).toEqual(expectedOptions(text));
    expect(Object.isFrozen(options) && Object.isFrozen(options.units) && Object.isFrozen(options.leaves) && Object.isFrozen(options.left)).toBe(true);
    for (const unit of options.units) {
      expect(Object.isFrozen(unit) && Object.isFrozen(unit.view)).toBe(true);
    }
  });

  it("covers what the oracle tells apart: units, views, leaves and every boolean both ways", () => {
    const all = [DESCRIBED.whole, DESCRIBED.firstPage, DESCRIBED.floored, ...SYNTHETIC.map(([, text]) => text), ...CRAFTED.map(([, text]) => text)].map(expectedOptions);
    expect(SYNTHETIC.length).toBeGreaterThanOrEqual(3);
    for (const key of ["tables", "leaves", "view"] as const) {
      expect(new Set(all.map((one) => one.left[key]))).toEqual(new Set([true, false]));
    }
    expect(new Set(all.map((one) => one.firstPage))).toEqual(new Set([true, false]));
    expect(new Set(all.map((one) => one.underK))).toEqual(new Set([true, false]));
    expect(new Set(all.map((one) => one.dataset === null))).toEqual(new Set([true, false]));
  });

  it("the orchard's lists are the plan's: under no floor the unit trees summarises height_m and variety; under a floor variety alone; the leaves are the string and category columns", async () => {
    const whole = byText(optionsOf(await described(DESCRIBED.whole)));
    expect(whole.units).toEqual([
      { label: "harvests", view: ["harvests.grade", "harvests.kg"] },
      { label: "trees", view: ["trees.height_m", "trees.variety"] },
    ]);
    expect(whole.leaves).toEqual(["harvests.grade", "harvests.harvest_id", "harvests.tree_id", "trees.tree_id", "trees.variety"]);
    const floored = byText(optionsOf(await described(DESCRIBED.floored)));
    expect(floored.units).toEqual([
      { label: "harvests", view: ["harvests.grade"] },
      { label: "trees", view: ["trees.variety"] },
    ]);
    expect(floored.underK).toBe(true);
  });
});

describe("optionsOf mints from an answer the walk returned alone, memoised (minors 7 and 10 of round 3)", () => {
  it("refuses a copy, a spread, a Proxy, a prototype's child, a body walked apart from the seam, and another operation's answer", async () => {
    const answer = await described(DESCRIBED.whole);
    const notAChoice = new ClientError("notAChoice");
    const copies: readonly (readonly [string, unknown])[] = [
      ["a spread", { ...answer }],
      ["Object.create", Object.create(answer) as unknown],
      ["a Proxy", new Proxy(answer, {})],
      ["a Proxy whose graph is composed", new Proxy(answer, { get: (target, key) => (key === "graph" ? { tables: answer.caveats.map((caveat) => caveat.code), edges: [] } : (Reflect.get(target, key) as unknown)) })],
      ["the body walked apart from the seam", walkComponent("DatasetDescription", decode(DESCRIBED.whole)).ok ? (walkComponent("DatasetDescription", decode(DESCRIBED.whole)) as { value: unknown }).value : null],
      ["a frozen literal", Object.freeze({ dataset: answer.dataset, graph: answer.graph, tables: answer.tables, disclosure: answer.disclosure })],
    ];
    for (const [name, copy] of copies) {
      expect(() => optionsOf(copy as typeof answer), name).toThrow(notAChoice);
    }
    vi.stubGlobal("fetch", () => Promise.resolve(json(orchardText("search_catalog"))));
    const other = await searchCatalog({});
    expect(other.kind).toBe("ok");
    expect(() => optionsOf((other.kind === "ok" ? other.value : null) as unknown as typeof answer)).toThrow(notAChoice);
    expect(() => optionsOf(answer)).not.toThrow();
  });

  it("gives the same options, the same choices, for the same answer, and other choices for another answer of the same text", async () => {
    const answer = await described(DESCRIBED.whole);
    const first = optionsOf(answer);
    expect(optionsOf(answer)).toBe(first);
    const again = optionsOf(await described(DESCRIBED.whole));
    expect(again).not.toBe(first);
    expect(again.units[0]?.choice).not.toBe(first.units[0]?.choice);
    expect(byText(again)).toEqual(byText(first));
  });

  it("after the modules are reset, a fresh seam refuses an answer the old one walked, and takes one it walks itself (minor 10)", async () => {
    const old = await described(DESCRIBED.whole);
    vi.resetModules();
    const fresh = await import("../../src/api/document");
    const seam = await import("../../src/api/typed");
    expect(fresh.optionsOf).not.toBe(optionsOf);
    expect(() => fresh.optionsOf(old)).toThrow("A document holds the choices and values of its own draft (D432).");
    vi.stubGlobal("fetch", () => Promise.resolve(json(DESCRIBED.whole)));
    const outcome = await seam.describeDataset({ dataset: "orchard" });
    expect(outcome.kind === "ok" ? fresh.optionsOf(outcome.value).units.length : 0).toBe(2);
  });
});

describe("a draft that is not one is refused with notAChoice, and nothing is sent", () => {
  let person: Typist;
  let options: Options;
  let other: Options;
  let floored: Options;

  beforeAll(async () => {
    person = await typist();
    options = optionsOf(await described(DESCRIBED.whole));
    other = optionsOf(await described(DESCRIBED.whole));
    floored = optionsOf(await described(DESCRIBED.floored));
    return () => {
      person.close();
    };
  });

  const typed = (raw: string): Typed => {
    const found = person.type(raw);
    if (found === null) {
      throw new Error(`nothing typed of ${raw}`);
    }
    return found;
  };

  /** The orchard's draft: unit trees, `trees.variety` is `apple`, the view `trees.variety`. */
  function draftOf(from: Options): Draft {
    const trees = from.units.find((unit) => readText(unit.label) === "trees");
    const variety = from.leaves.find((leaf) => readText(leaf.label) === "trees.variety");
    const view = trees?.view.find((one) => readText(one.label) === "trees.variety");
    if (from.dataset === null || trees === undefined || variety === undefined || view === undefined) {
      throw new Error("the orchard's options lack trees");
    }
    return { dataset: from.dataset, unit: trees.choice, cohorts: [{ leaves: [{ column: variety.choice, value: typed("apple") }] }], view: { columns: [view.choice] } };
  }

  it("writes the orchard's draft as the template says (the golden)", async () => {
    const sent = serving(() => json(orchardText("validate_document")));
    await validateDraft(draftOf(options));
    expect(sent.map((one) => [one.tool, one.body])).toEqual([
      [
        "validate_document",
        '{"document":{"aibi":"1","dataset":"orchard","unit":"trees","cohorts":{"first":{"all":[{"kind":"value","column":"trees.variety","values":["apple"]}]}},"views":[{"analysis":"summary.distribution","cohorts":["first"],"params":{"columns":[{"column":"trees.variety"}]}}]}}',
      ],
    ]);
  });

  it.each([
    ["a leaf's choice at the unit", (good: Draft): unknown => ({ ...good, unit: good.cohorts[0].leaves[0]?.column })],
    ["a view's choice at the unit", (good: Draft): unknown => ({ ...good, unit: good.view.columns[0] })],
    ["the unit's choice at a leaf", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [{ column: good.unit, value: good.cohorts[0].leaves[0]?.value }] }] })],
    ["a view's choice at a leaf", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [{ column: good.view.columns[0], value: good.cohorts[0].leaves[0]?.value }] }] })],
    ["a leaf's choice in the view", (good: Draft): unknown => ({ ...good, view: { columns: [good.cohorts[0].leaves[0]?.column] } })],
    ["no choice at the unit", (good: Draft): unknown => ({ ...good, unit: {} })],
    ["a copy of a choice", (good: Draft): unknown => ({ ...good, unit: Object.create(good.unit) as unknown })],
    ["a string at the unit", (good: Draft): unknown => ({ ...good, unit: "trees" })],
    ["no cohort", (good: Draft): unknown => ({ ...good, cohorts: [] })],
    ["three cohorts", (good: Draft): unknown => ({ ...good, cohorts: [good.cohorts[0], good.cohorts[0], good.cohorts[0]] })],
    ["cohorts that are no list", (good: Draft): unknown => ({ ...good, cohorts: { 0: good.cohorts[0], length: 1 } })],
    ["no view column", (good: Draft): unknown => ({ ...good, view: { columns: [] } })],
    ["no view", (good: Draft): unknown => ({ ...good, view: null })],
    ["leaves that are no list", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: { 0: good.cohorts[0].leaves[0], length: 1 } }] })],
    ["a leaf that is null", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [null] }] })],
    ["an empty value", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [{ column: good.cohorts[0].leaves[0]?.column, value: "" }] }] })],
    ["a number as a value", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [{ column: good.cohorts[0].leaves[0]?.column, value: 1 }] }] })],
    ["a value that is no string", (good: Draft): unknown => ({ ...good, cohorts: [{ leaves: [{ column: good.cohorts[0].leaves[0]?.column, value: { toString: () => "apple" } }] }] })],
    ["another dataset id", (good: Draft): unknown => ({ ...good, dataset: idOfAddress("pears") })],
    ["a dataset that is no string", (good: Draft): unknown => ({ ...good, dataset: null })],
    ["no draft at all", (): unknown => null],
  ])("%s", async (_name, make) => {
    const sent = serving(() => json(orchardText("validate_document")));
    const bad = make(draftOf(options)) as Draft;
    for (const send of [validateDraft, countDraft, runDraft]) {
      expect(() => send(bad)).toThrow(new ClientError("notAChoice"));
    }
    await Promise.resolve();
    expect(sent).toEqual([]);
  });

  it("refuses choices of two answers in one draft, whichever position holds the other's (minor 7)", () => {
    const sent = serving(() => json(orchardText("validate_document")));
    const mine = draftOf(options);
    const theirs = draftOf(other);
    const lower = draftOf(floored);
    const mixed: readonly Draft[] = [
      { ...mine, unit: theirs.unit },
      { ...mine, cohorts: theirs.cohorts },
      { ...mine, view: theirs.view },
      { ...mine, cohorts: [mine.cohorts[0], theirs.cohorts[0]] },
      { ...mine, view: { columns: [mine.view.columns[0], theirs.view.columns[0]] } },
      { ...mine, unit: lower.unit },
      { ...lower, view: mine.view },
    ];
    for (const draft of mixed) {
      expect(() => validateDraft(draft)).toThrow(new ClientError("notAChoice"));
    }
    expect(sent).toEqual([]);
    expect(() => validateDraft(theirs)).not.toThrow();
    expect(() => validateDraft(lower)).not.toThrow();
  });

  it("names its refusal in fixed words", () => {
    expect(new ClientError("notAChoice").message).toBe("A document holds the choices and values of its own draft (D432).");
  });
});

describe("the walk's registry of answers", () => {
  it("records each operation's walked answer with its operation, and nothing else", async () => {
    const seam = await import("../../src/api/typed");
    vi.stubGlobal("fetch", () => Promise.resolve(json(DESCRIBED.whole)));
    const outcome = await seam.describeDataset({ dataset: "orchard" });
    expect(outcome.kind === "ok" ? seam.walkedAs(outcome.value) : null).toBe("describe_dataset");
    vi.stubGlobal("fetch", () => Promise.resolve(json(orchardText("search_catalog"))));
    const hits = await seam.searchCatalog({});
    expect(hits.kind === "ok" ? seam.walkedAs(hits.value) : null).toBe("search_catalog");
    expect(seam.walkedAs({})).toBeUndefined();
    expect(Object.keys(SHAPES).length).toBeGreaterThan(0);
  });
});
