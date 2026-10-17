/**
 * The document seam's property test and its corpus (D432; A5.1 and A5.2 of the plan, minors 2, 9
 * and 10 of its round 3).
 *
 * **Inputs.** Seeded drafts of choices `optionsOf` drew from the orchard's describe answers (the
 * whole one, under a floor, its first page, and one with columns of every datatype), each value
 * typed into the real `Field` in jsdom (no test mint of a `Typed`; the browser's sanitising drops
 * a carriage return and a line feed, asserted), from a pool of hostile values: `$` forms, markup,
 * bidi, controls, NUL, noncharacters on every plane, lone surrogates, astral text, and the limits'
 * edges (4,095, 4,096 and 4,097 code points).
 *
 * **What is checked**, on the body as `encode` makes it, captured at a stubbed `fetch`, for each
 * draft through each of the three senders (each the same body):
 * - (i) the document is valid by the document's own JSON Schema (`@redocly/ajv`, 2020-12) once
 *   every value over 4,096 unescaped code points is cut to its first 4,096 and written again;
 * - (ii) totality: exactly the template's keys in its order, every string a literal of the seam at
 *   its position, the dataset, a choice's text at its role's position or a value at `values/0`; no
 *   number, `null` or boolean anywhere (so `classes.json` is unchanged);
 * - (iii) every id-position string is, by position, the text of the box the option came from;
 * - (iv) every value is, by position, the oracle's literal of what was typed (`$` -> `$$`);
 * - and the draft with excess members at every level (`notes`, `toJSON`, an own `__proto__`, an
 *   extra leaf member, a member on a tuple, a prototype's members) gives the same body.
 *
 * **The corpus** (`tests/fixtures/builder-documents.json`): every listed value alone, the
 * structural cases, the limits' edges (a document of exactly `MAX_DOCUMENT_BYTES` by the server's
 * measure and one byte more, a body of exactly `MAX_BODY_BYTES` and one byte more) and seeded
 * drafts, each entry with its typed values by position (cohort, leaf, column id), its body's
 * length, measure and SHA-256, and its body when it is at most 64 KiB. Written from a fixed seed,
 * held equal on every run (`AIBI_WRITE_WEB_FIXTURES=1` writes it), and read by the server's test
 * (`server/tests/core/catalog/test_builder_documents.py`), which rebuilds each body by its own
 * oracle, holds it to the digest and POSTs it to a real `create_app`.
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import Ajv2020 from "@redocly/ajv/dist/2020";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { readText } from "../../src/api/box";
import { countDraft, type CohortDraft, type Draft, type LeafDraft, type Option, type Options, optionsOf, runDraft, validateDraft } from "../../src/api/document";
import type { Typed } from "../../src/layout/Field";
import { classOf, codePoints, DESCRIBED, described, digestOf, documentMeasure, literalOf, MAX_BODY_BYTES, MAX_DOCUMENT_BYTES, MAX_STRING, type Typist, typist, utf8Length } from "./document-support";
import { json, orchardText, serving } from "./screens";
import { FIXTURES } from "./typed-support";

const CORPUS = path.join(FIXTURES, "builder-documents.json");
const WRITE = process.env["AIBI_WRITE_WEB_FIXTURES"] === "1";
const E = "\u{1F600}";

/** A value as the corpus records it: the text, or a run of one text (a long value). */
type Spec = string | { readonly repeat: string; readonly times: number; readonly prefix: string; readonly suffix: string };

const run = (repeat: string, times: number, prefix = "", suffix = ""): Spec => ({ repeat, times, prefix, suffix });
const expand = (spec: Spec): string => (typeof spec === "string" ? spec : `${spec.prefix}${spec.repeat.repeat(spec.times)}${spec.suffix}`);
/** The spec of a typed value: the run it was typed from (a run holds no line break, so the field
 * hands it on whole), or the text the field handed on. */
function specOf(typed: string, from: Spec): Spec {
  if (typeof from === "string") {
    return typed;
  }
  if (expand(from) !== typed) {
    throw new Error("a run the field did not hand on whole");
  }
  return from;
}

/** The values of class A (valid wherever they stand): P1's, the edges at and below `MAX_STRING`, and
 * more of the class; a text with a line break stands for what the browser leaves of it. */
const A_VALUES: readonly Spec[] = [
  "$", "$$", "$x", "$$x", "${x}", "{{x}}", "$\u202E", "$ ", "$1", "$first",
  "<img src=x onerror=alert(1)>", "</bdi><b>b</b>", "javascript:alert(1)", "<script>alert(1)</script>",
  "\u202Eabc", "\u2066x\u2069", "a\u200Bb", "\u2028", "\u2029", "\u0000", "a\u0000b", "\u0000".repeat(10), "\u007F\u0085", "\u000B\u000C", "\u001F", "\u0008",
  "' OR 1=1; --", '"', "\\", "\\%", "%", "_", "a%b_c", "*", "?", "[a]", E, "\uFEFFa", "e\u0301", "\u0301".repeat(300), "\uFFFD", "a\tb", "\uE000", "\u{10FFFD}", "\u{E0001}", "\u{F0000}",
  "trees", "trees.variety", "is", " is ", "the ", "orchard", "1", "variety", "apple", " ", "\u00A0", "NULL", "null", "true",
  "a\r\nb", "x\ry", "z\nw",
  run("a", 200), run("a", 201), run("\u2026", 250), run("x", 199, "", E), run(E, 300), run("x", 250, "", "$"),
  run("a", MAX_STRING - 1), run("a", MAX_STRING), run(E, MAX_STRING - 1), run(E, MAX_STRING), run("a", MAX_STRING - 1, "$"), run(E, MAX_STRING - 1, "$"), run("a", MAX_STRING - 2, "$$"),
];

/** The values of class B (more than `MAX_STRING` code points, in a document under the limit). */
const B_VALUES: readonly Spec[] = [run("a", MAX_STRING + 1), run(E, MAX_STRING + 1), run("a", MAX_STRING, "$"), run(E, MAX_STRING, "$"), run("a", MAX_STRING - 1, "$$"), run("a", 100_000)];

/** The values of class C (a noncharacter on any plane, or a lone surrogate, anywhere in it). */
const C_VALUES: readonly Spec[] = [
  ...Array.from({ length: 17 }, (_unused, plane) => [`a${String.fromCodePoint(plane * 0x10000 + 0xfffe)}`, `a${String.fromCodePoint(plane * 0x10000 + 0xffff)}`]).flat(),
  ...Array.from({ length: 0x20 }, (_unused, index) => String.fromCodePoint(0xfdd0 + index)),
  ...["\uD800", "\uDBFF", "\uDC00", "\uDFFF"].flatMap((lone) => [lone, `a${lone}`, `${lone}a`, `a${lone}b`]),
  run("a", 5000, "", "\uFFFE"),
];

/** A text the browser empties: never sent (the seam refuses an empty value besides). */
const EMPTIED = ["\r\n", "\r", "\n"];

const V = "trees.variety";
const T = "trees.tree_id";
const G = "harvests.grade";
const H = "harvests.tree_id";
const HEIGHT = "trees.height_m";

/** A draft as the corpus records it: the unit, each cohort's leaves (column, value) and the view. */
interface Shape {
  readonly name: string;
  readonly unit: string;
  readonly cohorts: readonly (readonly (readonly [string, Spec])[])[];
  readonly view: readonly string[];
}

/** The structural cases (P1's, and more). */
const STRUCTURES: readonly Shape[] = [
  { name: "two equal leaves", unit: "trees", cohorts: [[[V, "apple"], [V, "apple"]]], view: [V] },
  { name: "one value on two columns", unit: "trees", cohorts: [[[V, "apple"], [T, "apple"]]], view: [V] },
  { name: "a value equal to a column id", unit: "trees", cohorts: [[[T, "trees.variety"]]], view: [V] },
  { name: "two cohorts", unit: "trees", cohorts: [[[V, "apple"], [T, "t1"]], [[V, "pear"]]], view: [V, HEIGHT] },
  { name: "two cohorts with swapped values", unit: "trees", cohorts: [[[V, "apple"], [T, "t1"]], [[T, "apple"], [V, "t1"]]], view: [V] },
  { name: "a child table's leaf", unit: "trees", cohorts: [[[G, "A"]]], view: [V] },
  { name: "a child table's leaf beside the unit's", unit: "trees", cohorts: [[[G, "A"], [V, "apple"]]], view: [V] },
  { name: "a child table's leaf of a $ form", unit: "trees", cohorts: [[[H, "$x"]]], view: [V] },
  { name: "a parent's leaf (a lookup)", unit: "harvests", cohorts: [[[V, "apple"], [H, "t1"]]], view: [G] },
  { name: "a B leaf beside a C leaf", unit: "trees", cohorts: [[[V, run("a", MAX_STRING + 1)], [T, "x\uFFFE"]]], view: [V] },
  { name: "a B leaf in the second cohort", unit: "trees", cohorts: [[[V, "apple"]], [[V, run("a", MAX_STRING + 1)]]], view: [V] },
  { name: "an empty first cohort", unit: "trees", cohorts: [[], [[V, "apple"]]], view: [V] },
  { name: "an empty second cohort", unit: "trees", cohorts: [[[V, "apple"]], []], view: [V, HEIGHT] },
  { name: "two empty cohorts", unit: "trees", cohorts: [[], []], view: [V] },
  { name: "four leaves and both view columns", unit: "harvests", cohorts: [[[G, "A"], [H, "tree1"], [V, "apple"], [T, "tree1"]]], view: [G, "harvests.kg"] },
  { name: "a $ leaf of each kind in two cohorts", unit: "trees", cohorts: [[[V, "$"], [T, "$$"]], [[V, "$x"], [T, "$$x"]]], view: [V] },
];

/** The dataset's limits' edges: a document of exactly `MAX_DOCUMENT_BYTES` by the server's measure
 * and one byte more, and a body of exactly `MAX_BODY_BYTES` and one byte more; a value's run of
 * `a` is grown to the edge (each `a` one byte in both measures). */
function edges(): Shape[] {
  const shape = (name: string, times: number): Shape => ({ name, unit: "trees", cohorts: [[[V, run("a", times)]]], view: [V] });
  const one = documentOf(shape("", 1));
  const measure = documentMeasure(one);
  const bytes = utf8Length(JSON.stringify({ document: one }));
  return [
    shape("a document of MAX_DOCUMENT_BYTES (the server's measure)", MAX_DOCUMENT_BYTES - measure + 1),
    shape("a document of MAX_DOCUMENT_BYTES + 1", MAX_DOCUMENT_BYTES - measure + 2),
    shape("a document of 3 MB", 3_000_000),
    shape("a body of MAX_BODY_BYTES", MAX_BODY_BYTES - bytes + 1),
    shape("a body of MAX_BODY_BYTES + 1", MAX_BODY_BYTES - bytes + 2),
  ];
}

/** The oracle's document of a shape (the template of `document.ts`' docstring, written apart). */
function documentOf(shape: Shape): unknown {
  const names = ["first", "second"].slice(0, shape.cohorts.length);
  return {
    aibi: "1",
    dataset: "orchard",
    unit: shape.unit,
    cohorts: Object.fromEntries(shape.cohorts.map((leaves, index): [string, unknown] => [names[index] ?? "", { all: leaves.map(([column, value]) => ({ kind: "value", column, values: [literalOf(expand(value))] })) }])),
    views: [{ analysis: "summary.distribution", cohorts: names, params: { columns: shape.view.map((column) => ({ column })) } }],
  };
}

/** A seeded generator (mulberry32). */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = <T,>(next: () => number, from: readonly T[]): T => {
  const found = from[Math.floor(next() * from.length)];
  if (found === undefined) {
    throw new Error("nothing to pick");
  }
  return found;
};

/** The document's JSON Schema, compiled (P2's options). */
const SCHEMA = JSON.parse(readFileSync(path.join(FIXTURES, "../../../schemas/document.as-written.schema.json"), "utf8")) as object;
const validDocument = new Ajv2020({ strict: false, allErrors: true }).compile(SCHEMA);

/** (i)'s cut: every value over `MAX_STRING` unescaped code points cut to its first `MAX_STRING`,
 * written again. */
function cut(document: unknown, typed: readonly (readonly string[])[]): unknown {
  const copy = structuredClone(document) as { cohorts: Record<string, { all: { values: string[] }[] }> };
  Object.values(copy.cohorts).forEach((cohort, index) => {
    cohort.all.forEach((leaf, at) => {
      const value = typed[index]?.[at] ?? "";
      const points = Array.from(value);
      leaf.values[0] = literalOf(points.length > MAX_STRING ? points.slice(0, MAX_STRING).join("") : value);
    });
  });
  return copy;
}

/** What a draft was made of, by position: the unit's label, each leaf's column label and typed
 * value, the view's labels. */
interface Made {
  readonly unit: string;
  readonly cohorts: readonly (readonly (readonly [string, string])[])[];
  readonly view: readonly string[];
}

/** (ii), (iii) and (iv): the document is the template's, each string where it belongs, by
 * position. The problems found, or none. */
function totality(document: unknown, made: Made, dataset: string): string[] {
  const problems: string[] = [];
  const keys = (value: unknown, expected: readonly string[], at: string): value is Record<string, unknown> => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      problems.push(`${at}: not an object`);
      return false;
    }
    if (JSON.stringify(Object.keys(value)) !== JSON.stringify(expected)) {
      problems.push(`${at}: keys ${JSON.stringify(Object.keys(value))}`);
      return false;
    }
    return true;
  };
  const list = (value: unknown, length: number, at: string): value is unknown[] => {
    if (!Array.isArray(value) || value.length !== length) {
      problems.push(`${at}: not a list of ${String(length)}`);
      return false;
    }
    return true;
  };
  const is = (value: unknown, expected: string, at: string): void => {
    if (value !== expected) {
      problems.push(`${at}: ${JSON.stringify(value)} is not ${JSON.stringify(expected)}`);
    }
  };
  if (!keys(document, ["aibi", "dataset", "unit", "cohorts", "views"], "")) {
    return problems;
  }
  is(document["aibi"], "1", "/aibi");
  is(document["dataset"], dataset, "/dataset");
  is(document["unit"], made.unit, "/unit");
  const names = ["first", "second"].slice(0, made.cohorts.length);
  const cohorts = document["cohorts"];
  if (keys(cohorts, names, "/cohorts")) {
    names.forEach((name, index) => {
      const cohort = cohorts[name];
      const leaves = made.cohorts[index] ?? [];
      if (keys(cohort, ["all"], `/cohorts/${name}`) && list(cohort["all"], leaves.length, `/cohorts/${name}/all`)) {
        cohort["all"].forEach((leaf: unknown, at) => {
          const here = `/cohorts/${name}/all/${String(at)}`;
          const [column, value] = leaves[at] ?? ["", ""];
          if (keys(leaf, ["kind", "column", "values"], here)) {
            is(leaf["kind"], "value", `${here}/kind`);
            is(leaf["column"], column, `${here}/column`);
            if (list(leaf["values"], 1, `${here}/values`)) {
              is(leaf["values"][0], literalOf(value), `${here}/values/0`);
            }
          }
        });
      }
    });
  }
  const views = document["views"];
  if (list(views, 1, "/views")) {
    const view = views[0];
    if (keys(view, ["analysis", "cohorts", "params"], "/views/0")) {
      is(view["analysis"], "summary.distribution", "/views/0/analysis");
      is(JSON.stringify(view["cohorts"]), JSON.stringify(names), "/views/0/cohorts");
      const params = view["params"];
      if (keys(params, ["columns"], "/views/0/params") && list(params["columns"], made.view.length, "/views/0/params/columns")) {
        params["columns"].forEach((one: unknown, at) => {
          if (keys(one, ["column"], `/views/0/params/columns/${String(at)}`)) {
            is(one["column"], made.view[at] ?? "", `/views/0/params/columns/${String(at)}/column`);
          }
        });
      }
    }
  }
  return problems;
}

/** Every scalar of a JSON value that is not a string, by its pointer (none may be). */
function nonStrings(value: unknown, at = ""): string[] {
  if (typeof value === "string") {
    return [];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item: unknown, index) => nonStrings(item, `${at}/${String(index)}`));
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, item]) => nonStrings(item, `${at}/${key}`));
  }
  return [at];
}

/** The draft with excess members at every level: each must reach nothing. */
function withExcess(draft: Draft, kind: number): Draft {
  const leaf = (one: LeafDraft): LeafDraft => ({ column: one.column, value: one.value, notes: "x", toJSON: () => ({ kind: "value", column: "trees.planted", values: ["$x"] }) }) as LeafDraft;
  const cohort = (one: CohortDraft): CohortDraft => ({ leaves: Object.assign(one.leaves.map(leaf), { notes: "x" }), toJSON: () => ({ all: [] }) }) as CohortDraft;
  const cohorts = Object.assign(draft.cohorts.map(cohort), { extra: { leaves: [] }, toJSON: () => [] }) as unknown as Draft["cohorts"];
  const view = { columns: Object.assign([...draft.view.columns], { notes: "x" }), notes: "x", toJSON: () => ({}) } as unknown as Draft["view"];
  const base = { dataset: draft.dataset, unit: draft.unit, cohorts, view, notes: "x", toJSON: () => ({ aibi: "1" }) };
  if (kind === 0) {
    return base;
  }
  if (kind === 1) {
    const owned = { ...base };
    Object.defineProperty(owned, "__proto__", { value: { notes: "x", unit: "harvests" }, enumerable: true });
    return owned;
  }
  const proto = { notes: "x", aibi: "2", views: [], cohorts: [] };
  return Object.assign(Object.create(proto) as object, { dataset: draft.dataset, unit: draft.unit, cohorts: draft.cohorts, view: draft.view });
}

let person: Typist;
const typedOf = (raw: string): Typed => {
  const found = person.type(raw);
  if (found === null) {
    throw new Error(`the field handed on nothing for ${JSON.stringify(raw.slice(0, 20))}`);
  }
  return found;
};

/** The body each sender writes for a draft, captured at `fetch` (each answered by the orchard's). */
async function bodiesOf(draft: Draft): Promise<string[]> {
  const sent = serving((one) => json(orchardText(one.tool === "validate_document" ? "validate_document" : one.tool === "count_cohort" ? "count_cohort" : "run_analysis")));
  await validateDraft(draft);
  await countDraft(draft);
  await runDraft(draft);
  vi.unstubAllGlobals();
  return sent.map((one) => one.body ?? "");
}

/** A draft of a shape, from options, each value typed into the field. */
function draftOf(options: Options, shape: Shape): { readonly draft: Draft; readonly made: Made } {
  const find = <R extends "unit" | "leaf" | "view">(from: readonly Option<R>[], label: string): Option<R> => {
    const found = from.find((option) => readText(option.label) === label);
    if (found === undefined) {
      throw new Error(`no option ${label}`);
    }
    return found;
  };
  const unit = options.units.find((option) => readText(option.label) === shape.unit);
  if (unit === undefined || options.dataset === null) {
    throw new Error(`no unit ${shape.unit}`);
  }
  const cohorts = shape.cohorts.map((leaves) => leaves.map(([column, spec]) => ({ column: find(options.leaves, column), value: typedOf(expand(spec)) })));
  const view = shape.view.map((column) => find(unit.view, column).choice);
  const [first, ...rest] = view;
  if (first === undefined || (cohorts.length !== 1 && cohorts.length !== 2)) {
    throw new Error("a shape of no view or of another number of cohorts");
  }
  const draftCohorts = cohorts.map((leaves) => ({ leaves: leaves.map(({ column, value }) => ({ column: column.choice, value })) }));
  return {
    draft: {
      dataset: options.dataset,
      unit: unit.choice,
      cohorts: draftCohorts.length === 1 ? [draftCohorts[0] ?? { leaves: [] }] : [draftCohorts[0] ?? { leaves: [] }, draftCohorts[1] ?? { leaves: [] }],
      view: { columns: [first, ...rest] },
    },
    made: { unit: shape.unit, cohorts: cohorts.map((leaves) => leaves.map(({ column, value }) => [readText(column.label), value] as const)), view: shape.view },
  };
}

/** A corpus entry (the module's docstring). */
interface Entry {
  readonly name: string;
  readonly class: string;
  readonly unit: string;
  readonly cohorts: readonly (readonly (readonly [string, Spec])[])[];
  readonly view: readonly string[];
  readonly bytes: number;
  readonly measure: number;
  readonly sha256: string;
  readonly body: string | null;
}

/** The pool the seeded drafts draw values from: every listed value short enough to type many times. */
const POOL: readonly Spec[] = [...A_VALUES, ...C_VALUES, ...B_VALUES.slice(0, 5)];

/** A seeded draft's shape over the orchard's options. */
function seeded(next: () => number, options: Options, name: string): Shape | null {
  const units = options.units.filter((unit) => unit.view.length > 0);
  if (units.length === 0 || options.leaves.length === 0) {
    return null;
  }
  const unit = pick(next, units);
  const cohorts = Array.from({ length: next() < 0.4 ? 2 : 1 }, () => Array.from({ length: Math.floor(next() * 5) }, () => [readText(pick(next, options.leaves).label), pick(next, POOL)] as const));
  // Distinct view columns, in a seeded order: the server refuses a column twice in one view.
  const shuffled = unit.view.map((option) => [next(), readText(option.label)] as const).sort(([a], [b]) => a - b);
  const view = shuffled.slice(0, 1 + Math.floor(next() * shuffled.length)).map(([, label]) => label);
  return { name, unit: readText(unit.label), cohorts, view };
}

let options: Options;
let entries: Entry[];
const typedValues: Set<string> = new Set();

beforeAll(async () => {
  person = await typist();
  options = optionsOf(await described(DESCRIBED.whole));
  const shapes: Shape[] = [
    ...A_VALUES.map((value, index) => ({ name: `A value ${String(index)}`, unit: "trees", cohorts: [[[V, value] as const]], view: [V] })),
    ...B_VALUES.map((value, index) => ({ name: `B value ${String(index)}`, unit: "trees", cohorts: [[[V, value] as const]], view: [V] })),
    ...C_VALUES.map((value, index) => ({ name: `C value ${String(index)}`, unit: "trees", cohorts: [[[V, value] as const]], view: [V] })),
    ...STRUCTURES,
    ...edges(),
  ];
  const next = random(20261017);
  for (let index = 0; shapes.length < 220 + edges().length; index += 1) {
    const shape = seeded(next, options, `seeded ${String(index)}`);
    if (shape !== null) {
      shapes.push(shape);
    }
  }
  entries = [];
  for (const shape of shapes) {
    const { draft, made } = draftOf(options, shape);
    made.cohorts.flat().forEach(([, value]) => typedValues.add(value));
    const [body = ""] = await bodiesOf(draft);
    const document = (JSON.parse(body) as { document: unknown }).document;
    const values = made.cohorts.flat().map(([, value]) => value);
    const bytes = utf8Length(body);
    const measure = documentMeasure(document);
    entries.push({
      name: shape.name,
      class: classOf(values, measure, bytes),
      unit: made.unit,
      cohorts: made.cohorts.map((leaves, at) => leaves.map(([column, value], index) => [column, specOf(value, shape.cohorts[at]?.[index]?.[1] ?? value)] as const)),
      view: made.view,
      bytes,
      measure,
      sha256: digestOf(body),
      body: bytes <= 65_536 ? body : null,
    });
  }
}, 120_000);

afterAll(() => {
  person.close();
});

describe("the corpus (A5.2)", () => {
  it("is the one written from its fixed seed (AIBI_WRITE_WEB_FIXTURES=1 writes it)", () => {
    const text = `${JSON.stringify({ about: "The document seam's corpus (D432): written by web/tests/unit/document-property.test.tsx, read by server/tests/core/catalog/test_builder_documents.py.", entries }, null, 1)}\n`;
    if (WRITE) {
      writeFileSync(CORPUS, text);
    }
    expect(readFileSync(CORPUS, "utf8")).toBe(text);
  });

  it("holds every listed value, every structural case, both sides of every limit's edge and every class", () => {
    for (const spec of [...A_VALUES, ...B_VALUES, ...C_VALUES]) {
      const typed = expand(spec).replace(/[\r\n]/gu, "");
      expect(typedValues.has(typed), JSON.stringify(typed.slice(0, 20))).toBe(true);
    }
    const names = new Set(entries.map((entry) => entry.name));
    for (const shape of [...STRUCTURES, ...edges()]) {
      expect(names.has(shape.name), shape.name).toBe(true);
    }
    const classes = new Map<string, number>();
    entries.forEach((entry) => classes.set(entry.class, (classes.get(entry.class) ?? 0) + 1));
    expect([...classes.keys()].sort()).toEqual(["A", "B", "C", "D", "D'"]);
    const byName = new Map(entries.map((entry) => [entry.name, entry]));
    expect(byName.get("a document of MAX_DOCUMENT_BYTES (the server's measure)")?.measure).toBe(MAX_DOCUMENT_BYTES);
    expect(byName.get("a document of MAX_DOCUMENT_BYTES (the server's measure)")?.class).toBe("B");
    expect(byName.get("a document of MAX_DOCUMENT_BYTES + 1")?.measure).toBe(MAX_DOCUMENT_BYTES + 1);
    expect(byName.get("a document of MAX_DOCUMENT_BYTES + 1")?.class).toBe("D");
    expect(byName.get("a body of MAX_BODY_BYTES")?.bytes).toBe(MAX_BODY_BYTES);
    expect(byName.get("a body of MAX_BODY_BYTES")?.class).toBe("D");
    expect(byName.get("a body of MAX_BODY_BYTES + 1")?.bytes).toBe(MAX_BODY_BYTES + 1);
    expect(byName.get("a body of MAX_BODY_BYTES + 1")?.class).toBe("D'");
    expect(entries.filter((entry) => entry.class === "A").length).toBeGreaterThanOrEqual(80);
  });

  it("the browser's field drops a line break, and a text that is a line break alone is never handed on (so never sent)", () => {
    expect(typedOf("a\r\nb")).toBe("ab");
    for (const raw of EMPTIED) {
      expect(person.type(raw)).toBeNull();
    }
  });

  it("each body is the oracle's document of the entry, byte for byte", () => {
    for (const entry of entries) {
      const body = JSON.stringify({ document: documentOf({ name: entry.name, unit: entry.unit, cohorts: entry.cohorts, view: entry.view }) });
      expect(digestOf(body), entry.name).toBe(entry.sha256);
      if (entry.body !== null) {
        expect(entry.body, entry.name).toBe(body);
      }
    }
  });
});

describe("the property test over drafts (A5.1)", () => {
  /** The answers drafts are drawn from. */
  const ANSWERS = [DESCRIBED.whole, DESCRIBED.floored, DESCRIBED.firstPage];

  it("every body is ajv-valid once cut, total, by position, and the same through the three senders and with excess members (i)-(iv), over seeded drafts", async () => {
    const started = Date.now();
    let drafts = 0;
    for (const [index, text] of ANSWERS.entries()) {
      const from = optionsOf(await described(text));
      const next = random(1000 + index);
      for (let seed = 0; seed < 40; seed += 1) {
        const shape = seeded(next, from, `property ${String(index)}.${String(seed)}`);
        if (shape === null) {
          continue;
        }
        drafts += 1;
        const { draft, made } = draftOf(from, shape);
        const bodies = await bodiesOf(draft);
        expect(new Set(bodies).size, shape.name).toBe(1);
        const [body = ""] = bodies;
        for (const kind of [0, 1, 2]) {
          const [excess] = await bodiesOf(withExcess(draft, kind));
          expect(excess, `${shape.name} with excess ${String(kind)}`).toBe(body);
        }
        const document = (JSON.parse(body) as { document: unknown }).document;
        expect(nonStrings(document), shape.name).toEqual([]);
        expect(totality(document, made, from.dataset ?? ""), shape.name).toEqual([]);
        const typed = made.cohorts.map((leaves) => leaves.map(([, value]) => value));
        expect(validDocument(cut(document, typed)), `${shape.name}: ${JSON.stringify(validDocument.errors?.slice(0, 2))}`).toBe(true);
      }
    }
    expect(drafts).toBeGreaterThanOrEqual(80);
    expect(Date.now() - started).toBeLessThan(20_000);
  }, 60_000);

  it("the corpus's every entry passes the same checks", () => {
    for (const entry of entries.filter((one) => one.body !== null)) {
      const document = (JSON.parse(entry.body ?? "") as { document: unknown }).document;
      const made: Made = { unit: entry.unit, cohorts: entry.cohorts.map((leaves) => leaves.map(([column, value]) => [column, expand(value)] as const)), view: entry.view };
      expect(totality(document, made, "orchard"), entry.name).toEqual([]);
      expect(validDocument(cut(document, made.cohorts.map((leaves) => leaves.map(([, value]) => value)))), entry.name).toBe(true);
    }
  });

  it("the checks see what they hold (their controls): a value not escaped, an id moved, a key added, a number", () => {
    const made: Made = { unit: "trees", cohorts: [[[V, "$x"]]], view: [V] };
    const good = documentOf({ name: "", unit: "trees", cohorts: [[[V, "$x"]]], view: [V] }) as { cohorts: { first: { all: { values: string[]; column: string }[] } }; unit: string };
    expect(totality(good, made, "orchard")).toEqual([]);
    const unescaped = structuredClone(good);
    (unescaped.cohorts.first.all[0] ?? { values: [] }).values[0] = "$x";
    expect(totality(unescaped, made, "orchard")).toEqual(['/cohorts/first/all/0/values/0: "$x" is not "$$x"']);
    const moved = structuredClone(good);
    moved.unit = "harvests";
    expect(totality(moved, made, "orchard")).toEqual(['/unit: "harvests" is not "trees"']);
    expect(totality({ ...good, notes: "x" }, made, "orchard")).toHaveLength(1);
    expect(nonStrings({ ...good, n: 1 })).toEqual(["/n"]);
    expect(validDocument(cut(good, [["$x"]]))).toBe(true);
    expect(validDocument({ ...good, aibi: 1 })).toBe(false);
    expect(codePoints("\uD800a")).toEqual([0xd800, 0x61]);
  });
});
