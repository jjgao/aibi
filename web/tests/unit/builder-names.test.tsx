/**
 * The builder's lists name each column and table (D441): `optionsOf`'s names by its lookup rule (a
 * column's own label; a unit's, the label of the first `TableOut` whose id is the graph table's by
 * `sameText`, none without one), over table labels that differ from their ids, a coverage table placed
 * first, a graph table with no entry and a duplicate id; the builder over a describe answer of hostile
 * labels, chosen by id, its validate request byte-equal to the server's recorded one, every option of
 * text alone with no raw unsafe code point and its value its index; a brute force over every label set
 * to another option's id (by every rotation of the ids) and a seeded sweep over a pool of hostile
 * labels, each making the validate, count and run requests byte-equal to the recorded ones; `optionAt`
 * and `idPart`, the helpers the tests and the browser matrix find an option by its id with; and the
 * harness's corpus list, two parts.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { idPart, optionAt } from "../../e2e/options";
import { ANSWERS, type BuilderPage, builderPage, DESCRIBED, respond } from "./builder-support";
import { CORPUS, isUnsafe } from "./injection";
import { render } from "./render";
import { json, type Sent } from "./screens";

/** The separator, spelled here (`Option.tsx`'s own is not exported). */
const SEP = " \u2014 ";

interface RawTable {
  id: string;
  label: { data: string };
  rows: unknown;
  descriptor: unknown;
  columns: { id: string; label: { data: string } }[];
}
interface Raw {
  tables: RawTable[];
  graph: { tables: string[] };
}

/** The orchard's answer with labels that differ from the ids (`T:<id>`, `C:<id>`), a coverage table
 * first (no graph table) whose label is `ghost`, the id of a graph table with no `TableOut` of its own
 * (a unit with no entry is named by no label that happens to be its id), and, with `twin`, a second
 * `TableOut` of the id `trees` after the first: its label the table's id (a label that equals the id
 * does not choose it), and it lists the table's columns where the first lists none (the entry with
 * the most columns does not choose it either; the columns in the answer stay the same). */
function written(twin: boolean, from: string = DESCRIBED.whole): string {
  const answer = JSON.parse(from) as Raw;
  for (const table of answer.tables) {
    table.label.data = `T:${table.id}`;
    for (const column of table.columns) {
      column.label.data = `C:${column.id}`;
    }
  }
  const first = answer.tables[0];
  if (first === undefined) {
    throw new Error("no table");
  }
  answer.tables.unshift({ id: "cov_first", label: { data: "ghost" }, rows: first.rows, descriptor: first.descriptor, columns: [] });
  if (twin) {
    const trees = answer.tables.find((one) => one.id === "trees");
    if (trees === undefined) {
      throw new Error("no trees");
    }
    answer.tables.push({ ...trees, label: { data: "trees" }, columns: trees.columns });
    trees.columns = [];
  }
  answer.graph.tables = [...answer.graph.tables, "ghost"];
  return JSON.stringify(answer);
}

type Named = [string, string | null];

/** The ids and names `optionsOf` gives for an answer's text. */
async function names(text: string): Promise<{ units: Named[]; leaves: Named[]; views: [string, Named[]][] }> {
  vi.resetModules();
  const api = await import("../../src/api");
  const { readText } = await import("../../src/api/box");
  vi.stubGlobal("fetch", () => Promise.resolve(json(text)));
  const outcome = await api.describeDataset({ dataset: "orchard" });
  if (outcome.kind !== "ok") {
    throw new Error(outcome.kind);
  }
  const options = api.optionsOf(outcome.value);
  const read = (box: Parameters<typeof readText>[0] | null): string | null => (box === null ? null : readText(box));
  return {
    units: options.units.map((one): Named => [readText(one.label), read(one.name)]),
    leaves: options.leaves.map((one): Named => [readText(one.label), read(one.name)]),
    views: options.units.map((one): [string, Named[]] => [readText(one.label), one.view.map((view): Named => [readText(view.label), read(view.name)])]),
  };
}

let page: BuilderPage | undefined;

afterEach(() => {
  page?.close();
  page = undefined;
  vi.unstubAllGlobals();
});

describe("optionsOf's names (D441's lookup rule)", () => {
  it("each unit's name is its own TableOut's label, by id: a coverage table placed first changes nothing, a graph table with no entry has no name", async () => {
    const got = await names(written(false));
    expect(got.units).toEqual([
      ["harvests", "T:harvests"],
      ["trees", "T:trees"],
      ["ghost", null],
    ]);
    expect(got.leaves.length).toBeGreaterThan(0);
    for (const [id, name] of got.leaves) {
      expect(name).toBe(`C:${id}`);
    }
    expect(got.views.flatMap(([, view]) => view).length).toBeGreaterThan(0);
    for (const [, view] of got.views) {
      for (const [id, name] of view) {
        expect(name).toBe(`C:${id}`);
      }
    }
  });

  it("a duplicate TableOut id: the first one's label is the name", async () => {
    const got = await names(written(true));
    expect(got.units).toEqual([
      ["harvests", "T:harvests"],
      ["trees", "T:trees"],
      ["ghost", null],
    ]);
  });

  it("the rule over a small domain with no render: every order of duplicate ids, a coverage table first, a graph table with no entry, labels that are ids; the first entry of the id names, whichever lists the columns", async () => {
    const base = JSON.parse(DESCRIBED.whole) as Raw;
    const model = base.tables[0];
    if (model === undefined) {
      throw new Error("no table");
    }
    const labels = ["trees", "harvests", "ghost", "cov_first", "x"];
    const domain: [string, string][][] = [];
    for (const a of labels) {
      for (const b of labels) {
        domain.push([["harvests", "H"], ["trees", a], ["trees", b]]);
        domain.push([["trees", a], ["trees", b], ["harvests", "H"]]);
        domain.push([["cov_first", a], ["trees", b], ["harvests", a], ["trees", a], ["harvests", b]]);
      }
    }
    let seen = 0;
    for (const entries of domain) {
      for (const last of [false, true]) {
        // Each table's columns are listed by one entry of its id: its first, or its last.
        const answer: Raw = {
          ...base,
          graph: { ...base.graph, tables: [...base.graph.tables, "ghost"] },
          tables: entries.map(([id, label], at) => {
            const same = entries.flatMap(([one], index) => (one === id ? [index] : []));
            const lists = at === (last ? same[same.length - 1] : same[0]);
            const given = base.tables.find((one) => one.id === id) ?? model;
            return { id, label: { data: label }, rows: given.rows, descriptor: given.descriptor, columns: lists && given.id === id ? given.columns : [] };
          }),
        };
        const got = await names(JSON.stringify(answer));
        const own = (id: string): string | null => entries.find(([one]) => one === id)?.[1] ?? null;
        expect(got.units).toEqual([
          ["harvests", own("harvests")],
          ["trees", own("trees")],
          ["ghost", null],
        ]);
        expect(got.leaves.length).toBeGreaterThan(0);
        for (const [id, name] of got.leaves) {
          expect(name).toBe(base.tables.flatMap((one) => one.columns).find((one) => one.id === id)?.label.data);
        }
        seen += 1;
      }
    }
    expect(seen).toBe(2 * domain.length);
  });

  it("under a floor (D431's floored answer) the names are the answer's labels as well (declared text, D271)", async () => {
    const got = await names(written(false, DESCRIBED.floored));
    expect(got.units.slice(0, 2)).toEqual([
      ["harvests", "T:harvests"],
      ["trees", "T:trees"],
    ]);
    expect(got.leaves.length).toBeGreaterThan(0);
    for (const [id, name] of got.leaves) {
      expect(name).toBe(`C:${id}`);
    }
    expect(got.views.flatMap(([, view]) => view).length).toBeGreaterThan(0);
    for (const [, view] of got.views) {
      for (const [id, name] of view) {
        expect(name).toBe(`C:${id}`);
      }
    }
  });

  it("the builder's unit list: each id, the separator and its name, and an id alone where there is no name", async () => {
    page = await builderPage(undefined, written(true));
    expect([...(page.selects("Unit table")[0]?.options ?? [])].map((one) => one.textContent)).toEqual(["(choose)", `harvests${SEP}T:harvests`, `trees${SEP}T:trees`, "ghost"]);
    await page.choose("Unit table", "trees");
    expect([...(page.selects("Column")[0]?.options ?? [])].map((one) => one.textContent)).toEqual(["(choose)", `trees.height_m${SEP}C:trees.height_m`, `trees.variety${SEP}C:trees.variety`]);
  });
});

/** The hostile labels of the orchard's columns: another option's id, the separator inside, white space
 * alone, 4,096 characters, empty, and markup with right-to-left letters and a bidi tail. */
const COLUMNS: Readonly<Record<string, string>> = {
  "trees.variety": "trees.height_m",
  "trees.height_m": `x${SEP}trees.variety`,
  "harvests.grade": "\n\t  \n",
  "harvests.tree_id": "q".repeat(4096),
  "trees.tree_id": "",
  "harvests.harvest_id": "<b>x</b> \u05e9\u05dc\u05d5\u05dd 12\u202e\u2069",
};
/** The tables': another table's id, and markup with an override. */
const TABLES: Readonly<Record<string, string>> = { trees: "harvests", harvests: "<i>\u202etrees</i>" };

function hostile(text: string): string {
  const answer = JSON.parse(text) as Raw;
  for (const table of answer.tables) {
    const label = TABLES[table.id];
    if (label !== undefined) {
      table.label.data = label;
    }
    for (const column of table.columns) {
      const named = COLUMNS[column.id];
      if (named !== undefined) {
        column.label.data = named;
      }
    }
  }
  return JSON.stringify(answer);
}

describe("the builder over hostile labels (D441)", () => {
  it("chooses by id, shows each id and its name in the flat form, and sends the server's recorded body: no label reaches the document", async () => {
    const answer = (sent: Sent): Response => {
      if (sent.body !== ANSWERS.validated.request) {
        throw new Error("the body differs from the recorded one");
      }
      return respond(ANSWERS.validated);
    };
    page = await builderPage(answer, hostile(DESCRIBED.whole));
    const texts = (select: HTMLSelectElement | undefined): (string | null)[] => [...(select?.options ?? [])].map((one) => one.textContent);
    expect(texts(page.selects("Unit table")[0])).toEqual(["(choose)", `harvests${SEP}<i>\\u202etrees</i>`, `trees${SEP}harvests`]);
    await page.choose("Unit table", "trees");
    expect(texts(page.selects("Column")[0])).toEqual(["(choose)", `trees.height_m${SEP}x${SEP}trees.variety`, `trees.variety${SEP}trees.height_m`]);
    await page.press("Add a condition");
    expect(texts(page.selects("Column")[0])).toEqual([
      "(choose)",
      `harvests.grade${SEP}\n\t  \n`,
      `harvests.harvest_id${SEP}<b>x</b> \u05e9\u05dc\u05d5\u05dd 12\\u202e\\u2069`,
      `harvests.tree_id${SEP}${"q".repeat(2000)}\u2026`,
      `trees.tree_id${SEP}`,
      `trees.variety${SEP}trees.height_m`,
    ]);
    await page.choose("Column", "trees.variety", 0);
    await page.type(0, "apple");
    await page.press("Add a second cohort");
    await page.choose("Column", "trees.height_m", 1);
    await page.press("Add a column");
    await page.choose("Column", "trees.variety", 2);
    let options = 0;
    for (const select of page.container.querySelectorAll("select")) {
      for (const [index, option] of [...select.options].entries()) {
        options += 1;
        expect([...option.childNodes].every((node) => node.nodeType === Node.TEXT_NODE)).toBe(true);
        expect(Array.from(option.textContent).filter((character) => isUnsafe(character.codePointAt(0) ?? 0))).toEqual([]);
        expect(option.value).toBe(index === 0 ? "" : String(index - 1));
      }
    }
    expect(options).toBe(3 + 6 + 3 + 3);
    await page.press("Validate");
    expect(page.sent.map((one) => one.body)).toEqual([ANSWERS.validated.request]);
  });
});

const RECORDED = [ANSWERS.validated.request, ANSWERS.counted.request, ANSWERS.ran.request];

interface Described {
  tables: { id: string; label: { data: string }; columns: { id: string; label: { data: string } }[] }[];
}
const BASE = JSON.parse(DESCRIBED.whole) as Described;
/** Every table's id, then every column's, of the orchard's answer. */
const IDS = [...BASE.tables.map((one) => one.id), ...BASE.tables.flatMap((one) => one.columns.map((column) => column.id))];

const optionTexts = (select: HTMLSelectElement | undefined): string[] => [...(select?.options ?? [])].map((one) => one.textContent);

/** The page of an answer's text, its two-cohort form built by id, then validated, counted and run:
 * the lists' texts (the units', the leaves', the view columns') as they were shown, and the three
 * requests sent. */
async function flow(text: string): Promise<{ units: string[]; leaves: string[]; views: string[]; sent: (string | null)[] }> {
  page = await builderPage(undefined, text);
  const units = optionTexts(page.selects("Unit table")[0]);
  await page.choose("Unit table", "trees");
  await page.press("Add a condition");
  const leaves = optionTexts(page.selects("Column")[0]);
  await page.choose("Column", "trees.variety", 0);
  await page.type(0, "apple");
  await page.press("Add a second cohort");
  await page.choose("Column", "trees.height_m", 1);
  await page.press("Add a column");
  const views = optionTexts(page.selects("Column")[2]);
  await page.choose("Column", "trees.variety", 2);
  for (const select of page.container.querySelectorAll("select")) {
    for (const option of select.options) {
      expect([...option.childNodes].every((node) => node.nodeType === Node.TEXT_NODE)).toBe(true);
      expect(Array.from(option.textContent).filter((character) => isUnsafe(character.codePointAt(0) ?? 0))).toEqual([]);
    }
  }
  await page.press("Validate");
  await page.press("Count");
  await page.press("Run");
  const sent = page.sent.map((one) => one.body);
  page.close();
  page = undefined;
  return { units, leaves, views, sent };
}

/** Every id of a list is found exactly once, whatever the names are. */
function findsEach(texts: readonly string[]): void {
  for (const id of texts.slice(1).map(idPart)) {
    expect(optionAt(texts, id)).toBeGreaterThan(0);
  }
}

/** What an option of an id must say, from the answer alone (written here, not `optionsOf`'s rule): the
 * id, the separator and the label of the first `TableOut` of that id, or else of the first column of
 * that id. */
function shownFor(answer: Described, id: string): string {
  const table = answer.tables.filter((one) => one.id === id)[0];
  const column = answer.tables.flatMap((one) => one.columns).filter((one) => one.id === id)[0];
  const label = table?.label.data ?? column?.label.data;
  if (label === undefined) {
    throw new Error(`no label of ${id}`);
  }
  return `${id}${SEP}${label}`;
}

describe("no label reaches a document, whatever it is (D441)", () => {
  it("brute force: every table's and column's label another option's id, by every rotation of the ids (so every ordered pair): each id found once, the three requests byte-equal to the recorded ones", async () => {
    expect(IDS.length).toBeGreaterThan(5);
    for (let rotation = 1; rotation < IDS.length; rotation += 1) {
      const answer = JSON.parse(DESCRIBED.whole) as Described;
      let at = 0;
      const next = (): string => {
        at += 1;
        return IDS[(at - 1 + rotation) % IDS.length] ?? "";
      };
      for (const table of answer.tables) {
        table.label.data = next();
      }
      for (const table of answer.tables) {
        for (const column of table.columns) {
          column.label.data = next();
        }
      }
      expect(at).toBe(IDS.length);
      const got = await flow(JSON.stringify(answer));
      expect(got.units.map(idPart)).toEqual(["(choose)", "harvests", "trees"]);
      expect(got.leaves.map(idPart)).toEqual(["(choose)", "harvests.grade", "harvests.harvest_id", "harvests.tree_id", "trees.tree_id", "trees.variety"]);
      findsEach(got.units);
      findsEach(got.leaves);
      expect(got.views.length).toBeGreaterThan(1);
      for (const texts of [got.units, got.leaves, got.views]) {
        expect(texts.slice(1)).toEqual(texts.slice(1).map((text) => shownFor(answer, idPart(text))));
      }
      expect(got.sent).toEqual(RECORDED);
    }
  }, 120_000);

  it("a seeded sweep: 40 answers, each label drawn from a pool of hostile text (ids and separators among it): the lists keep their ids in order, each found once, the requests byte-equal to the recorded ones and holding no label", async () => {
    const pool = [
      ...CORPUS.filter((row) => !row.raw).map((row) => row.text.slice(0, 4096)),
      ...IDS,
      ...IDS.map((id) => `${id}${SEP}`),
      ...IDS.map((id) => `${SEP}${id}`),
      ...IDS.map((id) => `x${SEP}${id}${SEP}y`),
      SEP,
      "\u2014",
      "",
      " ",
      "\n\t  \n",
      "q".repeat(4096),
      "\u05e9\u05dc\u05d5\u05dd 12 \u0645\u0631\u062d\u0628\u0627 \u0661\u0662",
      "(choose)",
      "LABELSENTINEL",
      '"},"dataset":"x',
      "$x",
      "\\u202e",
    ];
    let state = 1;
    const draw = (): string => {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return pool[Math.floor((state / 2 ** 32) * pool.length)] ?? "";
    };
    const first = new Set<string>();
    for (let seed = 1; seed <= 40; seed += 1) {
      // The seed is scrambled (consecutive small seeds would give the generator's first draw the
      // same top bits).
      state = Math.imul(seed, 0x9e3779b1) >>> 0;
      const answer = JSON.parse(DESCRIBED.whole) as Described;
      for (const table of answer.tables) {
        table.label.data = draw();
        for (const column of table.columns) {
          column.label.data = draw();
        }
      }
      first.add(answer.tables[0]?.label.data ?? "");
      const got = await flow(JSON.stringify(answer));
      expect(got.units.map(idPart)).toEqual(["(choose)", "harvests", "trees"]);
      expect(got.leaves.map(idPart)).toEqual(["(choose)", "harvests.grade", "harvests.harvest_id", "harvests.tree_id", "trees.tree_id", "trees.variety"]);
      findsEach(got.units);
      findsEach(got.leaves);
      expect(got.sent).toEqual(RECORDED);
      for (const body of got.sent) {
        expect(body ?? "").not.toContain("LABELSENTINEL");
      }
    }
    // The first slot (the first table's label) is an answer's first draw: it varies across the seeds.
    expect(first.size).toBeGreaterThan(20);
  }, 120_000);
});

describe("optionAt: an option by its id", () => {
  it("finds the option whose text is the id or starts with it and the separator, never a longer id's or one whose name is the id", () => {
    const texts = ["(choose)", `trees_2${SEP}trees`, `trees${SEP}harvests`, `harvests${SEP}trees`, "ghost", `empty${SEP}`, `sep${SEP}x${SEP}trees`];
    expect(optionAt(texts, "trees")).toBe(2);
    expect(optionAt(texts, "trees_2")).toBe(1);
    expect(optionAt(texts, "harvests")).toBe(3);
    expect(optionAt(texts, "ghost")).toBe(4);
    expect(optionAt(texts, "empty")).toBe(5);
    expect(optionAt(texts, "sep")).toBe(6);
    expect(optionAt([...texts.slice(0, 2), "trees", ...texts.slice(3)], "trees")).toBe(2);
    expect(() => optionAt(texts, "tree")).toThrow("0 options of the id tree");
    expect(() => optionAt(texts, "x")).toThrow("0 options of the id x");
    expect(() => optionAt(["trees", `trees${SEP}a`], "trees")).toThrow("2 options of the id trees");
  });
});

describe("idPart: the id of an option's text", () => {
  it("is the text before the first separator: a name holding the separator, another id or nothing cannot move it, and an id alone is itself", () => {
    expect(idPart(`trees${SEP}x${SEP}harvests`)).toBe("trees");
    expect(idPart(`trees${SEP}harvests${SEP}`)).toBe("trees");
    expect(idPart(`trees${SEP}harvests`)).toBe("trees");
    expect(idPart(`trees${SEP}`)).toBe("trees");
    expect(idPart("trees")).toBe("trees");
    expect(idPart("(choose)")).toBe("(choose)");
  });

  it("an empty and a white-space-only name, read through textContent (an option's text collapses the white space and gives `id \u2014`)", async () => {
    const answer = JSON.parse(DESCRIBED.whole) as Described;
    for (const table of answer.tables) {
      table.label.data = "";
      for (const column of table.columns) {
        column.label.data = " \n ";
      }
    }
    page = await builderPage(undefined, JSON.stringify(answer));
    const select = page.selects("Unit table")[0];
    const options = [...(select?.options ?? [])];
    expect(options.map((one) => idPart(one.textContent))).toEqual(["(choose)", "harvests", "trees"]);
    expect(options.map((one) => one.textContent)).toEqual(["(choose)", `harvests${SEP}`, `trees${SEP}`]);
    expect(options.map((one) => one.text)).toEqual(["(choose)", "harvests \u2014", "trees \u2014"]);
  });
});

describe("the harness's corpus list (the policy matrix's cell, D441)", () => {
  it("passes each box as the option's id and its name: every option is the same flat text twice, the separator between", async () => {
    const { default: Harness } = await import("../../src/harness/Harness");
    const shown = await render(<Harness />);
    const button = shown.container.querySelector<HTMLButtonElement>('button[aria-label="corpus"]');
    await act(async () => {
      button?.click();
      await Promise.resolve();
    });
    const options = [...shown.container.querySelectorAll('select[aria-label="corpus list"] option')].map((one) => one.textContent);
    expect(options.length).toBeGreaterThan(0);
    for (const text of options) {
      const half = (text.length - SEP.length) / 2;
      expect([text.slice(0, half), text.slice(half, half + SEP.length)]).toEqual([text.slice(half + SEP.length), SEP]);
    }
    shown.unmount();
  });
});
