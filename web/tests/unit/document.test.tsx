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
import { countDraft, type CohortDraft, type Draft, type Options, optionsOf, runDraft, validateDraft } from "../../src/api/document";
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
    partial: options.partial,
    underK: options.underK,
  };
}

/** The whole orchard answer, edited as a server could answer (each a text the walk takes). */
function crafted(edit: (answer: { dataset: string; tables: { columns: { id: string; datatype?: string; label: unknown; identifier: boolean }[] }[]; graph: { tables: string[] }; columns_total: number; columns_next?: number; disclosure: { min_cell_count: number | null } }) => void): string {
  const answer = JSON.parse(DESCRIBED.whole) as Parameters<typeof edit>[0];
  edit(answer);
  return JSON.stringify(answer);
}

/** The orchard's whole answer, of another dataset's id (`pears`). */
const PEARS = JSON.stringify({ ...(JSON.parse(DESCRIBED.whole) as object), dataset: "pears" });

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
  ["a first page (columns left, the next page named)", crafted((a) => {
      a.columns_total += 2;
      a.columns_next = 3;
    })],
  ["a last page (columns before it, no next page)", crafted((a) => (a.columns_total += 4))],
  ["a next page named of a whole answer (columns_total is what it lists)", crafted((a) => (a.columns_next = 3))],
  ["a columns_total below the columns listed", crafted((a) => (a.columns_total -= 1))],
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
    expect(new Set(all.map((one) => one.partial))).toEqual(new Set([true, false]));
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

  it("refuses a view choice of another unit's options, whichever the unit (a view is bound to the unit it was minted under)", () => {
    const sent = serving(() => json(orchardText("validate_document")));
    const unitOf = (name: string) => {
      const found = options.units.find((unit) => readText(unit.label) === name);
      if (found === undefined) {
        throw new Error(`no unit ${name}`);
      }
      return found;
    };
    const trees = unitOf("trees");
    const harvests = unitOf("harvests");
    const mine = draftOf(options);
    const viewOf = (unit: typeof trees, label: string) => {
      const found = unit.view.find((one) => readText(one.label) === label);
      if (found === undefined) {
        throw new Error(`no view ${label}`);
      }
      return found.choice;
    };
    const parent = viewOf(trees, "trees.variety");
    const child = viewOf(harvests, "harvests.grade");
    const foreign: readonly Draft[] = [
      { ...mine, unit: harvests.choice, view: { columns: [parent] } },
      { ...mine, unit: trees.choice, view: { columns: [child] } },
      { ...mine, view: { columns: [viewOf(trees, "trees.variety"), child] } },
      { ...mine, unit: harvests.choice, cohorts: [{ leaves: [] }], view: { columns: [child, parent] } },
    ];
    for (const draft of foreign) {
      for (const send of [validateDraft, countDraft, runDraft]) {
        expect(() => send(draft)).toThrow(new ClientError("notAChoice"));
      }
    }
    expect(sent).toEqual([]);
    expect(() => validateDraft({ ...mine, unit: harvests.choice, view: { columns: [child] } })).not.toThrow();
    expect(() => validateDraft({ ...mine, unit: harvests.choice, view: { columns: [child, viewOf(harvests, "harvests.kg")] } })).not.toThrow();
  });

  it("refuses a dataset that is not the one of the choices' answer, though it is the one of the last answer the seam optioned (the choices bind to their own answer)", async () => {
    const pears = optionsOf(await described(PEARS));
    const again = optionsOf(await described(DESCRIBED.whole));
    expect(pears.dataset).toBe("pears");
    expect(again.dataset).toBe("orchard");
    const sent = serving(() => json(orchardText("validate_document")));
    const mine = draftOf(options);
    const theirs = draftOf(pears);
    const pearsId = pears.dataset;
    const orchardId = options.dataset;
    if (pearsId === null || orchardId === null) {
      throw new Error("no dataset");
    }
    expect(() => validateDraft({ ...mine, dataset: pearsId })).toThrow(new ClientError("notAChoice"));
    expect(() => validateDraft({ ...theirs, dataset: orchardId })).toThrow(new ClientError("notAChoice"));
    expect(() => validateDraft({ ...draftOf(again), dataset: pearsId })).toThrow(new ClientError("notAChoice"));
    expect(sent).toEqual([]);
    await validateDraft(theirs);
    expect(sent.map((one) => (JSON.parse(one.body ?? "") as { document: { dataset: string } }).document.dataset)).toEqual(["pears"]);
  });

  it("reads each list's length once: a list that answers another length at its next read sends what it first said", async () => {
    const sent = serving(() => json(orchardText("validate_document")));
    const good = draftOf(options);
    const [column] = good.view.columns;
    const [cohort] = good.cohorts;
    const shrinking = (list: readonly unknown[], first: number, then: number): unknown => {
      let reads = 0;
      return new Proxy([...list], { get: (target, key) => (key === "length" ? (reads++ === 0 ? first : then) : (Reflect.get(target, key) as unknown)) });
    };
    await validateDraft({ ...good, view: { columns: shrinking([column], 1, 0) as Draft["view"]["columns"] } });
    await validateDraft({ ...good, cohorts: shrinking([cohort], 1, 2) as Draft["cohorts"] });
    await validateDraft({ ...good, cohorts: [{ leaves: shrinking(cohort.leaves, 1, 0) as CohortDraft["leaves"] }] });
    const documents = sent.map((one) => (JSON.parse(one.body ?? "") as { document: { cohorts: Record<string, { all: unknown[] }>; views: { params: { columns: unknown[] } }[] } }).document);
    expect(documents.map((document) => document.views[0]?.params.columns.length)).toEqual([1, 1, 1]);
    expect(documents.map((document) => Object.keys(document.cohorts).length)).toEqual([1, 1, 1]);
    expect(documents.map((document) => document.cohorts["first"]?.all.length)).toEqual([1, 1, 1]);
    // A list that says 0 at its first read is refused (no view column, no cohort), as an empty one is.
    expect(() => validateDraft({ ...good, view: { columns: shrinking([column], 0, 1) as Draft["view"]["columns"] } })).toThrow(new ClientError("notAChoice"));
    expect(() => validateDraft({ ...good, cohorts: shrinking([cohort], 0, 1) as Draft["cohorts"] })).toThrow(new ClientError("notAChoice"));
  });

  it("refuses a length that is no whole number at every list (cohorts, leaves, view columns), and sends nothing (m1 of round 2)", async () => {
    const sent = serving(() => json(orchardText("validate_document")));
    const good = draftOf(options);
    const [column] = good.view.columns;
    const [cohort] = good.cohorts;
    const [leaf] = cohort.leaves;
    const saying = (list: readonly unknown[], length: unknown): unknown => new Proxy([...list], { get: (target, key) => (key === "length" ? length : (Reflect.get(target, key) as unknown)) });
    const LENGTHS: readonly (readonly [string, unknown])[] = [
      ["NaN", Number.NaN],
      ["undefined", undefined],
      ["null", null],
      ["a text", "abc"],
      ["a text of a whole number", "1"],
      ["an object", {}],
      ["-1", -1],
      ["1.5", 1.5],
      ["Infinity", Number.POSITIVE_INFINITY],
      ["-Infinity", Number.NEGATIVE_INFINITY],
      ["2 ** 53", 2 ** 53],
      ["a boolean", true],
      ["a bigint", 1n],
    ];
    const lists: readonly (readonly [string, (length: unknown) => Draft])[] = [
      ["cohorts", (length) => ({ ...good, cohorts: saying([cohort], length) as Draft["cohorts"] })],
      ["leaves", (length) => ({ ...good, cohorts: [{ leaves: saying([leaf], length) as CohortDraft["leaves"] }] })],
      ["a second cohort's leaves", (length) => ({ ...good, cohorts: [cohort, { leaves: saying([leaf], length) as CohortDraft["leaves"] }] })],
      ["view columns", (length) => ({ ...good, view: { columns: saying([column], length) as Draft["view"]["columns"] } })],
    ];
    for (const [list, make] of lists) {
      for (const [name, length] of LENGTHS) {
        for (const send of [validateDraft, countDraft, runDraft]) {
          expect(() => send(make(length)), `${list} of length ${name}`).toThrow(new ClientError("notAChoice"));
        }
      }
    }
    await Promise.resolve();
    expect(sent).toEqual([]);
    // The same lists with a whole length are sent, and a cohort of no leaf is (an empty cohort is a shape).
    for (const [, make] of lists) {
      await validateDraft(make(1));
    }
    await validateDraft({ ...good, cohorts: [{ leaves: saying([], 0) as CohortDraft["leaves"] }] });
    expect(sent).toHaveLength(5);
  });

  it("writes one consistent document, or refuses, when every member of the draft answers another at each read (m2 of round 2)", async () => {
    const sent = serving(() => json(orchardText("validate_document")));
    const unitOf = (name: string) => options.units.find((unit) => readText(unit.label) === name);
    const leafOf = (label: string) => options.leaves.find((one) => readText(one.label) === label)?.choice;
    const viewOf = (unit: string, label: string) => unitOf(unit)?.view.find((one) => readText(one.label) === label)?.choice;
    const harvests = unitOf("harvests")?.choice;
    const grade = leafOf("harvests.grade");
    const variety = leafOf("trees.variety");
    const gradeView = viewOf("harvests", "harvests.grade");
    const kgView = viewOf("harvests", "harvests.kg");
    const varietyView = viewOf("trees", "trees.variety");
    const heightView = viewOf("trees", "trees.height_m");
    if (options.dataset === null || harvests === undefined || grade === undefined || variety === undefined || gradeView === undefined || kgView === undefined || varietyView === undefined || heightView === undefined) {
      throw new Error("the orchard's options lack a choice");
    }
    const trees = draftOf(options);
    const drafts: readonly (readonly [string, Draft])[] = [
      ["trees", trees],
      ["harvests", { dataset: options.dataset, unit: harvests, cohorts: [{ leaves: [{ column: grade, value: typed("A") }] }], view: { columns: [gradeView, kgView] } }],
      ["two cohorts of trees", { ...trees, cohorts: [{ leaves: [{ column: variety, value: typed("apple") }, { column: variety, value: typed("pear") }] }, { leaves: [] }], view: { columns: [varietyView, heightView] } }],
      ["a view of trees under the unit harvests (refused)", { ...trees, unit: harvests }],
      ["a view of harvests under the unit trees (refused)", { ...trees, view: { columns: [gradeView] } }],
    ];
    const structural = (value: unknown): value is object => Array.isArray(value) || (typeof value === "object" && value !== null && Object.getPrototypeOf(value) === Object.prototype);
    /** A draft whose every member (each name read, at every depth) answers the first side's value at
     * its first read, the second's at its second, and so on; the choices and the values are not wrapped. */
    const flipping = (sides: readonly [unknown, unknown], reads: Map<string, number>, at = ""): unknown =>
      new Proxy(Array.isArray(sides[0]) ? [] : {}, {
        get: (target, key) => {
          if (typeof key === "symbol") {
            return Reflect.get(target, key) as unknown;
          }
          const name = `${at}/${key}`;
          const times = reads.get(name) ?? 0;
          reads.set(name, times + 1);
          const own = sides[times % 2] as Record<string, unknown> | undefined;
          const other = sides[(times + 1) % 2] as Record<string, unknown> | undefined;
          const value = own?.[key];
          return structural(value) ? flipping([value, other?.[key]], reads, name) : value;
        },
      });
    /** What a draft sends: its body, or `refused`. */
    const outcome = async (draft: Draft): Promise<string> => {
      const before = sent.length;
      try {
        await validateDraft(draft);
      } catch (error) {
        expect(error).toEqual(new ClientError("notAChoice"));
        return "refused";
      }
      expect(sent).toHaveLength(before + 1);
      return sent[before]?.body ?? "";
    };
    let sentBodies = 0;
    let refused = 0;
    const seen = new Set<string>();
    for (const [firstName, first] of drafts) {
      for (const [secondName, second] of drafts) {
        const reads = new Map<string, number>();
        const flipped = flipping([first, second], reads) as Draft;
        const found = await outcome(flipped);
        const alone = await outcome(first);
        expect(found, `${firstName} then ${secondName}`).toBe(alone);
        for (const [name, times] of reads) {
          expect(times, `${firstName} then ${secondName}: ${name} read ${String(times)} times`).toBeLessThanOrEqual(1);
          seen.add(name);
        }
        if (alone === "refused") {
          refused += 1;
        } else {
          sentBodies += 1;
        }
      }
    }
    // The accessors were reached: every member of a draft was read somewhere.
    for (const name of ["/dataset", "/unit", "/cohorts", "/cohorts/length", "/cohorts/0", "/cohorts/1", "/cohorts/0/leaves", "/cohorts/0/leaves/length", "/cohorts/0/leaves/0", "/cohorts/0/leaves/0/column", "/cohorts/0/leaves/0/value", "/view", "/view/columns", "/view/columns/length", "/view/columns/0", "/view/columns/1"]) {
      expect(seen.has(name), name).toBe(true);
    }
    expect(sentBodies).toBeGreaterThanOrEqual(15);
    expect(refused).toBeGreaterThanOrEqual(8);
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
