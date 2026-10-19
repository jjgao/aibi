/**
 * The typed answers' table held to an independent validator (D426, MA3 of the code review's round 2).
 *
 * The walk (`src/api/typed.ts`) trusts a table (`src/api/generated/shapes.ts`) that the generator
 * (`scripts/generate-shapes.mjs`) makes from `schemas/openapi.json`. The tests of the table's own
 * declarations and of the misses it states cannot see a constraint the generator drops: the misses
 * are made from the table they test. This file is the oracle that does not read the table.
 *
 * **The oracle.** `@redocly/ajv`'s JSON Schema draft 2020-12 validator over the document's own
 * components, with what the walk does not check removed (D426's residuals, `RESIDUALS`, and the
 * annotations, `ANNOTATIONS`, which the generator names: the list is derived from them, so a keyword
 * added to the wrong list makes the two disagree) and the walk's four readings of a schema applied
 * (`oneOf` as `anyOf`, whose exclusivity is a residual; `integer` as `number` and a numeric `const`
 * as `number`, since the walk never reads a number, D419; a fail-closed vocabulary, `FAIL_CLOSED`, as
 * any string; an `x-aibi-json` value as the JSON types its `anyOf` lists, which the generator holds to
 * be all of them: only the mark is stripped, so an arm that narrowed the value would disagree). The verdict of the walk on a body must equal the
 * oracle's, over a set of inputs that the tests make deterministic and bounded (below): every
 * disagreement fails. A constraint the generator drops makes the walk take what the oracle refuses.
 *
 * **The inputs** (each once; `INPUTS` says how many of each arm):
 * - **bodies**: every valid body of the table, built to reach every position of it (`bodies`);
 * - **misses**: every miss the table's constraints make (`variants`), one a constraint and position;
 * - **drops, additions, renames**: at every object node of a body, each key dropped, a key added (a
 *   plain one, and a pointer, which a map of pointers takes) and each key renamed;
 * - **values**: at every leaf, a string changed, a boolean flipped, the value of every other JSON
 *   type, and (a string of the document's vocabulary) each word that stands with it in an `enum`
 *   of the document, which is what a check on a position the others accept (`Proportion`'s
 *   `/denominator` is `suppressed`, and the `NotEstimableReason` of the pattern map takes any of
 *   its twelve words) is refused by alone;
 * - **words and keys** (MA4 of the code review's round 3): at every vocabulary position of a body
 *   (found by walking the body down the table), every word the TABLE accepts there (`acceptedWords`:
 *   its enums' members and string consts, through references, alternatives and checks), and at every
 *   map whose keys a vocabulary names, every word it accepts as a key. The arms above draw from the
 *   document (a swap is a word the document has at another enum), so a generator that adds a word to
 *   an enum, a const or a key vocabulary, and regenerates the table, passed them: this arm puts the
 *   added word to the oracle, which refuses a word the document does not have. `VOCABULARIES` (what
 *   `kindOf` reads) is held to the table and to the document at the end of this file.
 *
 * **The bound.** The arms of drops, additions, renames and values are not taken over every node of
 * every body: two nodes with the same component, the same path (a list's index read as one place)
 * and the same operation are one input, so the 19 golden envelopes and the orchard's answers add
 * what they alone reach; and a body of more than `BODY_CAP` characters (the analyses' listing,
 * whose descriptors are opaque JSON that the walk takes as it is) is left out of them (every place
 * class only they reach is inside an opaque value: a test asserts it). The words and keys are taken
 * from those bodies too, one input for each place and word. The set is some 26,700 inputs, judged in
 * about eighteen seconds, and its counts, with the candidates the dedupe merged, are pinned
 * (`INPUT_COUNTS`).
 *
 * **Its own controls.** `verdicts` runs the walk and the oracle through a pair of judges, and
 * `disagreement` names what differs: a deliberately wrong pair is run through both, and must be seen
 * (a comparison of the walk with itself would pass every other test). The pair is built once, by
 * `judgesOf(schemas)`, and the module's `oracle` is its member; a narrowed document, a loosened one and
 * a mutated table are run through `verdicts` against the real oracle and must disagree (n1 of round 4).
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import Ajv2020 from "@redocly/ajv/dist/2020";
import { describe, expect, it } from "vitest";

import { ANNOTATIONS, FAIL_CLOSED, JSON_TYPES as GENERATOR_JSON_TYPES, RESIDUAL } from "../../scripts/generate-shapes.mjs";
import { decode } from "../../src/api/decode";
import { FALLBACKS, type Shape, SHAPES, VOCABULARIES as TABLE_VOCABULARIES } from "../../src/api/generated/shapes";
import { walkComponent } from "../../src/api/typed";
import { type Body, bodies, entryShape, jsonTypeOf, JSON_TYPES, REF, SAMPLE, typesOf, variants, walkOf } from "./typed-support";

type Json = Record<string, unknown>;

const DOCUMENT = JSON.parse(readFileSync(path.join(import.meta.dirname, "../../../schemas/openapi.json"), "utf8")) as { components: { schemas: Json } };

/** The keywords the oracle does not check: the walk's residuals and the annotations (the one list). */
const DROPPED: ReadonlySet<string> = new Set([...RESIDUAL, ...ANNOTATIONS]);

/** The keys of these are names (of a member, of a pattern, of a component), not keywords. */
const NAMES = new Set(["properties", "patternProperties", "schemas"]);

/** A schema as the oracle reads it: without what the walk does not check, and as the walk reads it. */
function stripped(value: unknown, parentKey: string, dropped: ReadonlySet<string> = DROPPED): unknown {
  if (Array.isArray(value)) {
    return value.map((member) => stripped(member, "", dropped));
  }
  if (typeof value !== "object" || value === null) {
    return value;
  }
  const schema = value as Json;
  if (NAMES.has(parentKey)) {
    return Object.fromEntries(Object.entries(schema).map(([name, member]) => [name, stripped(member, "", dropped)]));
  }
  if (typeof schema["const"] === "number") {
    return { type: "number" };
  }
  const out: Json = {};
  for (const [keyword, member] of Object.entries(schema)) {
    if (dropped.has(keyword) || keyword === "x-aibi-json") {
      // Only the mark of an opaque value goes: its `anyOf` stays, read as the JSON types it is held to be
      // (the generator's census holds it, `jsonArms`), so an arm that narrowed it makes the two disagree.
      continue;
    }
    if (keyword === "oneOf") {
      out["anyOf"] = stripped(member, keyword, dropped);
    } else if (keyword === "type") {
      out[keyword] = member === "integer" ? "number" : Array.isArray(member) ? (member as unknown[]).map((one) => (one === "integer" ? "number" : one)) : member;
    } else if (keyword === "enum" || keyword === "const" || keyword === "required") {
      out[keyword] = member;
    } else {
      out[keyword] = stripped(member, keyword, dropped);
    }
  }
  return out;
}

/** The oracle's validator of a component, over a document read as the walk reads it. */
function oracleOf(schemas: Json): (component: string, value: unknown) => boolean {
  const own = stripped(schemas, "schemas") as Json;
  for (const name of FAIL_CLOSED.keys()) {
    own[name] = { type: "string" };
  }
  const ajv = new Ajv2020({ strict: false, allowUnionTypes: true, validateFormats: false });
  ajv.addSchema({ $id: "aibi", components: { schemas: own } });
  const compiled = new Map<string, (value: unknown) => boolean>();
  return (component, value) => {
    let validate = compiled.get(component);
    if (validate === undefined) {
      const found = ajv.compile({ $ref: `aibi#/components/schemas/${component}` });
      validate = (input: unknown) => found(input);
      compiled.set(component, validate);
    }
    return validate(value);
  };
}

/** The two judges of a body: the walk, and the oracle (a pair a test may replace by a deliberately wrong one). */
interface Judges {
  readonly walk: (component: string, text: string) => boolean;
  readonly oracle: (component: string, value: unknown) => boolean;
}

/** The judges of a document's schemas: the walk of the table, and the oracle of those schemas. The one
 * construction of the pair: the module's `oracle` is `JUDGES.oracle`, and a control builds its own judges
 * of a narrowed document through it, so no wiring of the oracle to the walk can pass unseen (n1 of round 4). */
const judgesOf = (schemas: Json): Judges => ({ walk: (component, text) => walkComponent(component, decode(text)).ok, oracle: oracleOf(schemas) });
const JUDGES: Judges = judgesOf(DOCUMENT.components.schemas);

/** The walk's verdict on a component's body, and the oracle's. */
function verdicts(component: string, text: string, judges: Judges = JUDGES): readonly [walk: boolean, oracle: boolean] {
  return [judges.walk(component, text), judges.oracle(component, JSON.parse(text))];
}

/** What a disagreement of the two verdicts is called (nothing when they agree): the one comparison
 * the oracle's inputs and its controls are both judged by. */
function disagreement(arm: string, label: string, [walk, valid]: readonly [boolean, boolean]): string | undefined {
  return walk === valid ? undefined : `${walk ? "walk takes, oracle refuses" : "walk refuses, oracle takes"}: ${arm}: ${label}`;
}

/** Every `enum` of the document (a list of strings), and every string `const` (a list of one). */
function vocabularies(value: unknown, parentKey: string, out: string[][]): string[][] {
  if (Array.isArray(value)) {
    for (const member of value) {
      vocabularies(member, "", out);
    }
  } else if (typeof value === "object" && value !== null) {
    for (const [key, member] of Object.entries(value as Json)) {
      if (!NAMES.has(parentKey) && (key === "enum" || key === "const")) {
        const words = (key === "enum" && Array.isArray(member) ? member : [member]).filter((word): word is string => typeof word === "string");
        if (words.length > 0) {
          out.push(words);
        }
      } else {
        vocabularies(member, key, out);
      }
    }
  }
  return out;
}

const LISTS = vocabularies(DOCUMENT.components.schemas, "schemas", []);

/** The words of the document that stand with a word in one of its vocabularies (an `enum` the word is
 * a member of): what a position that takes the word in a check, and its neighbours in the enum, may
 * be changed to without the others refusing it. */
const NEIGHBOURS = new Map<string, readonly string[]>();
for (const words of LISTS) {
  for (const word of words) {
    NEIGHBOURS.set(word, [...new Set([...(NEIGHBOURS.get(word) ?? []), ...words.filter((other) => other !== word)])].sort());
  }
}

type Step = string | number;

/** The path of every object and list of a value (the root first). */
function nodesOf(value: unknown, at: readonly Step[] = [], out: (readonly Step[])[] = []): (readonly Step[])[] {
  if (typeof value === "object" && value !== null) {
    out.push(at);
    for (const [key, member] of Object.entries(value)) {
      nodesOf(member, [...at, Array.isArray(value) ? Number(key) : key], out);
    }
  }
  return out;
}

const get = (value: unknown, at: readonly Step[]): unknown => at.reduce<unknown>((found, step) => (found as Record<Step, unknown>)[step], value);

/** A copy of a value with `change` applied to the node at a path. */
function changedAt(value: unknown, at: readonly Step[], change: (node: Record<string, unknown>) => void): unknown {
  const copy = JSON.parse(JSON.stringify(value)) as unknown;
  change(get(copy, at) as Record<string, unknown>);
  return copy;
}

/** A place in a body, one of a kind: the component, the path with a list's index read as one place,
 * the operation and what it puts there. */
const placeOf = (component: string, at: readonly Step[], operation: string): string => `${component} ${at.map((step) => (typeof step === "number" ? "[]" : step)).join("/")} ${operation}`;

/** The size of a body (its JSON text, in characters) past which the arms that change its nodes leave it out. */
const BODY_CAP = 20_000;

/** The words a node of the table accepts (from the table alone, the document unread): the members of
 * its enums and the value of its string consts, through references, alternatives and checks. A `str`
 * node takes any string and names none. */
function acceptedWords(shape: Shape, depth = 0): readonly string[] {
  if (depth > 40) {
    return [];
  }
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? [] : acceptedWords(found, depth + 1);
    }
    case "enum":
      return shape.v;
    case "const":
      return typeof shape.v === "string" ? [shape.v] : [];
    case "or":
    case "all":
      return shape.o.flatMap((member) => acceptedWords(member, depth + 1));
    default:
      return [];
  }
}

/** A position of a body where the table accepts words: at a string leaf (`value`: the words its node
 * accepts), at a map whose keys are named by a vocabulary (`key`: the words it accepts as a key, each
 * with the value of an entry its own shape would take). */
interface Position {
  readonly kind: "value" | "key";
  readonly at: readonly Step[];
  readonly words: readonly string[];
  /** For a `key`: the value an entry of each word takes. */
  readonly samples: ReadonlyMap<string, unknown>;
}

/**
 * Every vocabulary position of a body, found by walking the body down the table (an `or`'s and an
 * `allOf`'s members that take the value are followed; a segment and an opaque JSON value hold none).
 * What each position accepts is the table's own set (`acceptedWords`), not the document's: this is
 * where a word the generator added meets the oracle (MA4 of the code review's round 3).
 */
function positionsOf(shape: Shape, value: unknown, at: readonly Step[], out: Position[], depth = 0): void {
  if (depth > 40) {
    return;
  }
  if (typeof value === "string") {
    const words = acceptedWords(shape);
    if (words.length > 0) {
      out.push({ kind: "value", at, words, samples: new Map() });
    }
  }
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      if (found !== undefined) {
        positionsOf(found, value, at, out, depth + 1);
      }
      return;
    }
    case "or":
    case "all":
      for (const member of shape.o) {
        if (walkOf(member, value)) {
          positionsOf(member, value, at, out, depth + 1);
        }
      }
      return;
    case "arr":
      if (Array.isArray(value)) {
        (value as unknown[]).forEach((item, index) => {
          positionsOf(shape.pi?.[index] ?? shape.i, item, [...at, index], out, depth + 1);
        });
      }
      return;
    case "rec":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        for (const [name, member] of Object.entries(shape.p)) {
          if (Object.hasOwn(value, name)) {
            positionsOf(member, (value as Json)[name], [...at, name], out, depth + 1);
          }
        }
      }
      return;
    case "map":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        const entries = Object.entries(value as Json);
        const words = shape.k === undefined ? [] : acceptedWords(shape.k);
        if (words.length > 0) {
          const samples = new Map(words.map((word) => [word, (entries.find(([key]) => entryShape(shape, key) === entryShape(shape, word)) ?? entries[0])?.[1] ?? 1]));
          out.push({ kind: "key", at, words, samples });
        }
        for (const [key, member] of entries) {
          positionsOf(entryShape(shape, key), member, [...at, key], out, depth + 1);
        }
      }
      return;
    default:
  }
}

/**
 * The places of a body by class, found by walking it down the table: every path of it, a list's index
 * read as `[]` and a map's key as the entry's shape (`{v}`, or `{i}` for the ith pattern it matches),
 * and the paths at which the body holds an opaque JSON value (`opaque`: its inside is any JSON).
 */
function classesOf(shape: Shape, value: unknown, at: readonly string[], places: (readonly string[])[], opaque: (readonly string[])[], depth = 0): void {
  places.push(at);
  if (depth > 40) {
    return;
  }
  switch (shape.t) {
    case "json":
      opaque.push(at);
      return;
    case "ref": {
      const found = SHAPES[shape.n];
      if (found !== undefined) {
        classesOf(found, value, at, places, opaque, depth + 1);
      }
      return;
    }
    case "or":
    case "all":
      for (const member of shape.o) {
        if (walkOf(member, value)) {
          classesOf(member, value, at, places, opaque, depth + 1);
        }
      }
      return;
    case "arr":
      if (Array.isArray(value)) {
        (value as unknown[]).forEach((item, index) => {
          classesOf(shape.pi?.[index] ?? shape.i, item, [...at, "[]"], places, opaque, depth + 1);
        });
      }
      return;
    case "rec":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        for (const [name, member] of Object.entries(shape.p)) {
          if (Object.hasOwn(value, name)) {
            classesOf(member, (value as Json)[name], [...at, name], places, opaque, depth + 1);
          }
        }
      }
      return;
    case "map":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        for (const [key, member] of Object.entries(value as Json)) {
          const index = (shape.pp ?? []).findIndex(([text]) => new RegExp(text, "u").test(key));
          classesOf(entryShape(shape, key), member, [...at, `{${index < 0 ? "v" : String(index)}}`], places, opaque, depth + 1);
        }
      }
      return;
    default:
  }
}

interface Input {
  readonly arm: string;
  readonly component: string;
  readonly label: string;
  /** The input's value, made when it is judged (the set is large, and most of it is copies). */
  readonly make: () => unknown;
}

/** The deterministic, bounded set of inputs (see the header), and how many candidates of each arm the
 * dedupe by place merged (both are pinned by a test: INPUT_COUNTS). */
function inputs(): { readonly out: Input[]; readonly merged: ReadonlyMap<string, number>; readonly all: readonly Body[]; readonly small: readonly Body[] } {
  const out: Input[] = [];
  const seen = new Set<string>();
  const merged = new Map<string, number>();
  const once = (arm: string, key: string): boolean => {
    if (seen.has(key)) {
      merged.set(arm, (merged.get(arm) ?? 0) + 1);
      return false;
    }
    seen.add(key);
    return true;
  };
  const add = (arm: string, component: string, label: string, make: () => unknown): void => {
    out.push({ arm, component, label, make });
  };
  const all = bodies();
  // The arms below change a body at its nodes, and a body that is mostly an opaque JSON value (the
  // analyses' listing: descriptors, which the walk reads as any JSON) has thousands of nodes where any
  // change is taken: those bodies are left out of them (the places only they reach are all inside an
  // opaque value: a test asserts it, `the cap hides no position`).
  const small = all.filter(({ value }) => JSON.stringify(value).length <= BODY_CAP);
  for (const { component, source, value } of all) {
    add("bodies", component, `${component} (${source})`, () => value);
  }
  for (const { component, value } of all) {
    for (const [at, what, replacement] of variants(REF(component), value, component, 0)) {
      if (once("misses", `miss ${at} :: ${what}`)) {
        add("misses", component, `${at} :: ${what}`, () => replacement);
      }
    }
  }
  for (const { component, value } of small) {
    for (const at of nodesOf(value)) {
      const node = get(value, at);
      if (Array.isArray(node) || typeof node !== "object" || node === null) {
        continue;
      }
      const keys = Object.keys(node);
      for (const key of keys) {
        if (once("drops", placeOf(component, at, `drop ${key}`))) {
          add("drops", component, placeOf(component, at, `drop ${key}`), () =>
            changedAt(value, at, (target) => {
              Reflect.deleteProperty(target, key);
            }),
          );
        }
        if (once("renames", placeOf(component, at, `rename ${key}`))) {
          add("renames", component, placeOf(component, at, `rename ${key}`), () =>
            changedAt(value, at, (target) => {
              target[`${key}~`] = target[key];
              Reflect.deleteProperty(target, key);
            }),
          );
        }
      }
      for (const added of ["~probe", "/outside"]) {
        if (once("additions", placeOf(component, at, `add ${added}`))) {
          add("additions", component, placeOf(component, at, `add ${added}`), () =>
            changedAt(value, at, (target) => {
              target[added] = keys[0] === undefined ? 1 : target[keys[0]];
            }),
          );
        }
      }
    }
  }
  for (const { component, value } of small) {
    for (const at of nodesOf(value)) {
      const node = get(value, at) as Record<string, unknown>;
      for (const [key, leaf] of Object.entries(node)) {
        if (typeof leaf === "object" && leaf !== null) {
          continue;
        }
        const where = [...at, key];
        const put = (operation: string, arm: string, replacement: unknown): void => {
          if (once(arm, placeOf(component, where, operation))) {
            add(arm, component, placeOf(component, where, operation), () =>
              changedAt(value, at, (target) => {
                target[key] = replacement;
              }),
            );
          }
        };
        if (typeof leaf === "string") {
          put("changed", "values", `${leaf}~`);
          for (const word of NEIGHBOURS.get(leaf) ?? []) {
            put(`swapped for ${word}`, "swaps", word);
          }
        }
        if (typeof leaf === "boolean") {
          put("flipped", "values", !leaf);
        }
        for (const type of JSON_TYPES) {
          if (type !== jsonTypeOf(leaf)) {
            put(`of ${type}`, "values", SAMPLE[type]);
          }
        }
      }
    }
  }
  // The table's own words: at every vocabulary position of every body, every word the TABLE accepts
  // there (a word the document does not have is a word a generator added: the oracle refuses it, so
  // the walk must too), and at every map whose keys a vocabulary names, every word it accepts as a key.
  for (const { component, value } of small) {
    const found: Position[] = [];
    positionsOf(REF(component), value, [], found);
    for (const { kind, at, words, samples } of found) {
      for (const word of words) {
        if (kind === "value") {
          const parent = at.slice(0, -1);
          const key = at.at(-1);
          if (key !== undefined && once("words", placeOf(component, at, `table word ${word}`))) {
            add("words", component, placeOf(component, at, `table word ${word}`), () =>
              changedAt(value, parent, (target) => {
                target[key] = word;
              }),
            );
          }
        } else if (once("keys", placeOf(component, at, `table key ${word}`))) {
          add("keys", component, placeOf(component, at, `table key ${word}`), () =>
            changedAt(value, at, (target) => {
              target[word] = samples.get(word);
            }),
          );
        }
      }
    }
  }
  return { out, merged, all, small };
}

/** The set's shape, pinned as `census.json` pins the keyword lists (a change of the document or of an arm is a
 * reviewed change to this table, not a silent one): the inputs of each arm, the candidates the dedupe by
 * place merged, and the bodies. Without it an arm thinned (to the root, to the last step of a path) still
 * passes while it tests less. Last moved by D431's four orchard answers (175 bodies to 179, and the
 * places they add); then by D433's eight builder answers (179 bodies to 187). */
const INPUT_COUNTS = {
  inputs: { additions: 1740, bodies: 187, drops: 2683, keys: 235, misses: 2048, renames: 2683, swaps: 3828, values: 12525, words: 1289 },
  merged: { additions: 13456, drops: 16455, keys: 1178, misses: 37566, renames: 16455, swaps: 12193, values: 72985, words: 12157 },
  bodies: { all: 187, small: 185, over: 2 },
};

describe("the walk agrees with an independent validator of the document, over a bounded set of inputs (MA3)", () => {
  const { out: input, merged, all, small } = inputs();
  const countOf = (counts: ReadonlyMap<string, number>): Record<string, number> => Object.fromEntries([...counts].sort(([a], [b]) => (a < b ? -1 : 1)));

  it("the set is made of every arm, and is bounded", () => {
    const counts = new Map<string, number>();
    for (const { arm } of input) {
      counts.set(arm, (counts.get(arm) ?? 0) + 1);
    }
    expect([...counts.keys()].sort()).toEqual(["additions", "bodies", "drops", "keys", "misses", "renames", "swaps", "values", "words"]);
    for (const [arm, count] of counts) {
      expect(count, arm).toBeGreaterThan(100);
    }
    expect(input.length).toBeLessThan(40_000);
    expect(NEIGHBOURS.size).toBeGreaterThan(100);
  });

  it("the arms' counts are the reviewed ones (the oracle's inputs move only with a reviewed change)", () => {
    const counts = new Map<string, number>();
    for (const { arm } of input) {
      counts.set(arm, (counts.get(arm) ?? 0) + 1);
    }
    const found = { inputs: countOf(counts), merged: countOf(merged), bodies: { all: all.length, small: small.length, over: all.length - small.length } };
    expect(found, "the oracle's inputs changed: the document, the table or an arm moved; review it and update INPUT_COUNTS").toEqual(INPUT_COUNTS);
  });

  it("the cap hides no position: every place class only an over-cap body reaches is inside an opaque JSON value (m3)", () => {
    const classes = (body: Body): { places: string[]; opaque: (readonly string[])[] } => {
      const places: (readonly string[])[] = [];
      const opaque: (readonly string[])[] = [];
      classesOf(REF(body.component), body.value, [], places, opaque);
      return { places: places.map((at) => `${body.component} ${JSON.stringify(at)}`), opaque };
    };
    const kept = new Set(small.flatMap((body) => classes(body).places));
    const over = all.filter((body) => !small.includes(body));
    expect(over.length).toBeGreaterThan(0);
    for (const body of over) {
      const { places, opaque } = classes(body);
      expect(opaque.length, `${body.component} (${body.source}): the over-cap body holds an opaque JSON value`).toBeGreaterThan(0);
      for (const place of new Set(places)) {
        if (kept.has(place)) {
          continue;
        }
        const at = JSON.parse(place.slice(place.indexOf(" ") + 1)) as string[];
        const inside = opaque.some((root) => root.length <= at.length && root.every((step, index) => step === at[index]));
        expect(inside, `${place} is reached by an over-cap body alone and is no part of an opaque JSON value: the cap hides a position`).toBe(true);
      }
    }
  });

  it("no alternatives of the table has two object arms (the dedupe by place would merge them: m3)", () => {
    const seenShapes = new Set<Shape>();
    const crowded: string[] = [];
    const visit = (shape: Shape, at: string): void => {
      if (seenShapes.has(shape)) {
        return;
      }
      seenShapes.add(shape);
      switch (shape.t) {
        case "or": {
          const objects = shape.o.filter((arm) => typesOf(arm).has("object"));
          if (objects.length > 1) {
            crowded.push(at);
          }
          shape.o.forEach((arm, index) => {
            visit(arm, `${at}|${String(index)}`);
          });
          return;
        }
        case "all":
          shape.o.forEach((arm, index) => {
            visit(arm, `${at}&${String(index)}`);
          });
          return;
        case "arr":
          visit(shape.i, `${at}[]`);
          (shape.pi ?? []).forEach((arm, index) => {
            visit(arm, `${at}[${String(index)}]`);
          });
          return;
        case "rec":
          for (const [name, member] of Object.entries(shape.p)) {
            visit(member, `${at}.${name}`);
          }
          return;
        case "map":
          visit(shape.v, `${at}{}`);
          for (const [, member] of shape.pp ?? []) {
            visit(member, `${at}{}`);
          }
          return;
        default:
      }
    };
    for (const [name, shape] of Object.entries(SHAPES)) {
      visit(shape, name);
    }
    expect(seenShapes.size).toBeGreaterThan(300);
    expect(crowded, "an `or` with two object arms: the dedupe by (component, path) would merge their nodes: put the walked arm in the place's key").toEqual([]);
  });

  it("every body of the table is valid by the oracle too, and every input is judged alike by both", () => {
    const disagree: string[] = [];
    let refused = 0;
    for (const { arm, component, label, make } of input) {
      const judged = verdicts(component, JSON.stringify(make()));
      const wrong = disagreement(arm, label, judged);
      if (wrong !== undefined) {
        disagree.push(wrong);
      }
      if (!judged[1]) {
        refused += 1;
      }
      if (arm === "bodies" && !judged[0]) {
        disagree.push(`a body the walk refuses: ${label}`);
      }
    }
    expect(disagree.slice(0, 20)).toEqual([]);
    expect(disagree).toHaveLength(0);
    // The comparison is not vacuous: most of the set is refused by the oracle, and the bodies are taken.
    expect(refused).toBeGreaterThan(5000);
    expect(input.length - refused).toBeGreaterThan(all.length);
  }, 120_000);

  it("the oracle sees what the walk does not read, and refuses what it does (its controls)", () => {
    const strict = oracleOf(DOCUMENT.components.schemas);
    const raw = new Ajv2020({ strict: false, allowUnionTypes: true, validateFormats: false });
    raw.addSchema({ $id: "aibi", components: { schemas: DOCUMENT.components.schemas } });
    const body = bodies().find(({ component, source }) => component === "ResultEnvelope" && source.startsWith("envelope"));
    expect(body).toBeDefined();
    const digest = (value: string): unknown => ({ ...(body?.value as Json), digest: value });
    // `pattern` is a residual: the walk takes another form of digest, the oracle (stripped) too, and the document's own schema refuses it.
    expect(verdicts("ResultEnvelope", JSON.stringify(digest("md5:x")))).toEqual([true, true]);
    expect(raw.validate({ $ref: "aibi#/components/schemas/ResultEnvelope" }, digest("md5:x"))).toBe(false);
    // What it checks: a member of the wrong type, a required member dropped, an extra key.
    expect(verdicts("ResultEnvelope", JSON.stringify(digest(1 as never)))).toEqual([false, false]);
    expect(strict("ResultEnvelope", { ...(body?.value as Json), "~extra": 1 })).toBe(false);
    // The list that drives the strip is the generator's: a residual and an annotation are in it, `required` is not.
    expect(DROPPED.has("pattern")).toBe(true);
    expect(DROPPED.has("x-aibi-computed")).toBe(true);
    expect(DROPPED.has("required")).toBe(false);
  });

  it("the stripped document still rejects known misses (the oracle is no vacuous validator)", () => {
    const body = bodies().find(({ component, source }) => component === "ResultEnvelope" && source.startsWith("envelope"))?.value as Json;
    const first = Object.keys(body)[0] ?? "";
    const misses: readonly (readonly [string, unknown])[] = [
      ["a member of the wrong type", { ...body, digest: 1 }],
      ["a required member dropped", Object.fromEntries(Object.entries(body).filter(([key]) => key !== first))],
      ["a key renamed", { ...Object.fromEntries(Object.entries(body).filter(([key]) => key !== first)), [`${first}~`]: body[first] }],
      ["an extra key", { ...body, "~extra": 1 }],
      ["not an object", []],
    ];
    for (const [what, miss] of misses) {
      expect(verdicts("ResultEnvelope", JSON.stringify(miss)), what).toEqual([false, false]);
    }
    for (const component of Object.keys(SHAPES)) {
      if (!typesOf(REF(component)).has("string")) {
        expect(JUDGES.oracle(component, "~not-a-body"), `${component}: a string where no string is`).toBe(false);
      }
    }
  });

  it("the comparison is run through `verdicts`, and a deliberately wrong judge disagrees (O3: a vacuous comparison would pass)", () => {
    const body = bodies().find(({ component, source }) => component === "ResultEnvelope" && source.startsWith("envelope"))?.value as Json;
    const wrong = { ...body, digest: 1 };
    const text = JSON.stringify(wrong);
    // The real pair agrees (both refuse): nothing to report.
    expect(disagreement("control", "real", verdicts("ResultEnvelope", text))).toBeUndefined();
    // A walk that takes everything, beside the real oracle: a wrong table, as it would be after a dropped constraint.
    const takesAll: Judges = { walk: () => true, oracle: JUDGES.oracle };
    expect(verdicts("ResultEnvelope", text, takesAll)).toEqual([true, false]);
    expect(disagreement("control", "a walk that takes all", verdicts("ResultEnvelope", text, takesAll))).toBe("walk takes, oracle refuses: control: a walk that takes all");
    // A walk that refuses everything, and an oracle that takes everything (vacuous), each against the other.
    const refusesAll: Judges = { walk: () => false, oracle: JUDGES.oracle };
    expect(disagreement("control", "a walk that refuses all", verdicts("ResultEnvelope", JSON.stringify(body), refusesAll))).toBe("walk refuses, oracle takes: control: a walk that refuses all");
    const vacuous: Judges = { ...JUDGES, oracle: () => true };
    expect(disagreement("control", "an oracle that takes all", verdicts("ResultEnvelope", text, vacuous))).toBe("walk refuses, oracle takes: control: an oracle that takes all");
    // The walk's own verdict is not the oracle's: a comparison of the walk with itself would give [false, false] here.
    expect(verdicts("ResultEnvelope", text, { ...JUDGES, walk: () => true })[1]).toBe(false);
  });

  it("the judges are built from the document: a narrowed document, a loosened one and a mutated table disagree through the real pair (n1: no wiring of the oracle to the walk passes)", () => {
    const body = bodies().find(({ component, source }) => component === "ResultEnvelope" && source.startsWith("envelope"))?.value as Json;
    const text = JSON.stringify(body);
    const number = JSON.stringify({ ...body, digest: 1 });
    expect(disagreement("control", "real", verdicts("ResultEnvelope", text))).toBeUndefined();
    // A document narrowed where the table is not (`digest` a number): the oracle built from it refuses what the walk takes.
    const narrowed = JSON.parse(JSON.stringify(DOCUMENT.components.schemas)) as Json;
    ((narrowed["ResultEnvelope"] as Json)["properties"] as Json)["digest"] = { type: "number" };
    expect(disagreement("control", "a narrowed document", verdicts("ResultEnvelope", text, judgesOf(narrowed)))).toBe("walk takes, oracle refuses: control: a narrowed document");
    // A document that takes anything where the table takes less: the walk refuses what the oracle built from it takes.
    const loosened = JSON.parse(JSON.stringify(DOCUMENT.components.schemas)) as Json;
    ((loosened["ResultEnvelope"] as Json)["properties"] as Json)["digest"] = {};
    expect(disagreement("control", "a loosened document", verdicts("ResultEnvelope", number, judgesOf(loosened)))).toBe("walk refuses, oracle takes: control: a loosened document");
    // A table that reads a member as any, against the real oracle (the module's own): the walk takes what the oracle refuses.
    const anyDigest = (shape: unknown): unknown => {
      if (Array.isArray(shape)) {
        return shape.map(anyDigest);
      }
      if (typeof shape !== "object" || shape === null) {
        return shape;
      }
      const node: Json = Object.fromEntries(Object.entries(shape).map(([key, member]) => [key, anyDigest(member)]));
      const members = node["p"] as Json | undefined;
      if (node["t"] === "rec" && members !== undefined && Object.hasOwn(members, "digest")) {
        members["digest"] = { t: "any" };
      }
      return node;
    };
    const mutated = anyDigest(SHAPES["ResultEnvelope"]) as Shape;
    const mutatedTable: Judges = { walk: (_component, input) => walkComponent(mutated, decode(input)).ok, oracle: JUDGES.oracle };
    expect(walkComponent(mutated, decode(number)).ok).toBe(true);
    expect(disagreement("control", "a mutated table", verdicts("ResultEnvelope", number, mutatedTable))).toBe("walk takes, oracle refuses: control: a mutated table");
    // The pair of the module is the pair `judgesOf` builds, and its oracle is not the walk: a component of the
    // document that the table does not reach (the request side's `DescriptorJson`) is no component to the walk
    // (it refuses), and the oracle, which reads the document, takes a value of it.
    expect(Object.hasOwn(DOCUMENT.components.schemas, "DescriptorJson")).toBe(true);
    expect(SHAPES["DescriptorJson"]).toBeUndefined();
    expect(JUDGES.walk("DescriptorJson", JSON.stringify({ a: [1, null] }))).toBe(false);
    expect(JUDGES.oracle("DescriptorJson", { a: [1, null] })).toBe(true);
    expect(JUDGES.oracle("ResultEnvelope", { ...body, digest: 1 })).toBe(false);
  });

  it("the arms beside an `x-aibi-json` mark are held to the JSON types, in the oracle's own reading of the document (n2: DJ1-DJ3)", () => {
    const marked = Object.entries(DOCUMENT.components.schemas).filter(([, schema]) => Object.hasOwn(schema as Json, "x-aibi-json"));
    expect(marked.map(([name]) => name)).toContain("OutputJson");
    const NAMED = ["null", "boolean", "number", "string", "array", "object"].sort();
    // The generator's list, the support's, what the table's `json` node takes and the document's arms are one set.
    expect([...GENERATOR_JSON_TYPES].sort()).toEqual(NAMED);
    expect([...JSON_TYPES].sort()).toEqual(NAMED);
    expect([...typesOf({ t: "json" })].sort()).toEqual(NAMED);
    for (const [name, schema] of marked) {
      const arms = (schema as Json)["anyOf"] as Json[];
      // The mark states whether `null` is one of them (the request side's four say not; the answers' one, `OutputJson`, says so).
      const nullable = ((schema as Json)["x-aibi-json"] as Json)["null"] === true;
      expect(arms.flatMap((arm) => (Array.isArray(arm["type"]) ? (arm["type"] as string[]) : [String(arm["type"])])).sort(), `${name}: the arms' types`).toEqual(NAMED.filter((one) => nullable || one !== "null"));
    }
    // The oracle keeps the arms (only the mark is stripped), so a document whose arms narrow the value disagrees with the walk.
    const mutate = (change: (arms: Json[]) => void): Json => {
      const copy = JSON.parse(JSON.stringify(DOCUMENT.components.schemas)) as Json;
      change((copy["OutputJson"] as Json)["anyOf"] as Json[]);
      return copy;
    };
    const judged = (document: Json, value: unknown): readonly [boolean, boolean] => verdicts("OutputJson", JSON.stringify(value), judgesOf(document));
    for (const value of [null, true, 1, "x", [], {}, { a: [null, { b: "x" }] }]) {
      expect(judged(DOCUMENT.components.schemas, value), JSON.stringify(value)).toEqual([true, true]);
    }
    // DJ1: no `null`; DJ2: the object arm requires a key; DJ3: an arm gains an enum.
    expect(judged(mutate((arms) => { (arms[0] as Json)["type"] = ["string", "boolean"]; }), null)).toEqual([true, false]);
    expect(judged(mutate((arms) => { (arms[3] as Json)["required"] = ["must"]; }), {})).toEqual([true, false]);
    expect(judged(mutate((arms) => { (arms[0] as Json)["enum"] = ["only"]; }), "x")).toEqual([true, false]);
  });

  it("a table that drops a constraint would disagree with the oracle (the comparison has teeth)", () => {
    // `Population.unknown_by_reason`: a map of the reasons, each required, its keys the reasons alone.
    const body = bodies().find(({ component, source }) => component === "Population" && source === "synthetic 0")?.value as Json;
    const counts = body["unknown_by_reason"] as Json;
    const reasons = Object.keys(counts);
    expect(reasons.length).toBeGreaterThanOrEqual(6);
    const found = SHAPES["Population"];
    const record = found?.t === "all" ? found.o[0] : found;
    const arms = record?.t === "rec" && record.p["unknown_by_reason"]?.t === "or" ? record.p["unknown_by_reason"].o : [];
    const map = arms.find((arm) => arm.t === "map");
    expect(map?.t).toBe("map");
    if (map?.t !== "map") {
      return;
    }
    const dropped = Object.fromEntries(Object.entries(counts).filter(([reason]) => reason !== reasons.at(-1)));
    const bogus = { ...counts, BOGUS: 1 };
    // As it stands, the table refuses a reason dropped and a key that is no reason, as the oracle does.
    for (const input of [dropped, bogus]) {
      expect(verdicts("Population", JSON.stringify({ ...body, unknown_by_reason: input }))).toEqual([false, false]);
      expect(walkComponent(map, decode(JSON.stringify(input))).ok).toBe(false);
    }
    // A table that kept the first required key only (E8b) takes the first; one without its `propertyNames` (E7) takes the second:
    // the walk would take what the oracle refuses, and this comparison would fail.
    const first = reasons[0];
    const keptFirst = { ...map, r: first === undefined ? [] : [first] };
    expect(walkComponent(keptFirst, decode(JSON.stringify(dropped))).ok).toBe(true);
    const unnamed = Object.fromEntries(Object.entries(map).filter(([key]) => key !== "k"));
    expect(walkComponent(unnamed as typeof map, decode(JSON.stringify(bogus))).ok).toBe(true);
  });

  it("a word the table accepts and the document does not is put to the oracle (MA4: the table's words, at every vocabulary position)", () => {
    // Where the table names a vocabulary, every word of it is an input; the control: a table whose enum gained a word.
    const words = input.filter(({ arm }) => arm === "words");
    const keys = input.filter(({ arm }) => arm === "keys");
    expect(words.length).toBeGreaterThan(1000);
    expect(keys.length).toBeGreaterThan(50);
    const widened = (shape: Shape): Shape => (shape.t === "enum" ? { t: "enum", v: [...shape.v, "~widened"] } : shape);
    expect(acceptedWords(widened(SHAPES["NotEstimableReason"] ?? { t: "any" }))).toContain("~widened");
    expect(acceptedWords(REF("NotEstimableReason"))).not.toContain("~widened");
    // The reasons (a map's key vocabulary and a value's) are positions of the set.
    expect(keys.some(({ label }) => label.startsWith("Population unknown_by_reason table key"))).toBe(true);
    expect(words.some(({ label }) => label.includes("table word"))).toBe(true);
  });
});

/** The words of the document's vocabularies by list (an `enum`, or a string `const` as a list of one), over the
 * components the answers reach, leaving out what the walk does not read: a condition (`if`, `then`, `else`,
 * `not`, `contains`) and the inside of an opaque JSON value. */
function documentLists(): Set<string> {
  const reached = [...Object.keys(SHAPES), ...FAIL_CLOSED.keys()];
  const conditions = new Set(["if", "then", "else", "not", "contains"]);
  const out = new Set<string>();
  const visit = (value: unknown, parentKey: string): void => {
    if (Array.isArray(value)) {
      for (const member of value) {
        visit(member, "");
      }
    } else if (typeof value === "object" && value !== null && !Object.hasOwn(value, "x-aibi-json")) {
      for (const [key, member] of Object.entries(value as Json)) {
        if (NAMES.has(parentKey)) {
          visit(member, key);
        } else if (key === "enum" || key === "const") {
          const words = (key === "enum" && Array.isArray(member) ? member : [member]).filter((word): word is string => typeof word === "string");
          if (words.length > 0) {
            out.add(JSON.stringify(words));
          }
        } else if (!conditions.has(key)) {
          visit(member, key);
        }
      }
    }
  };
  for (const name of reached) {
    visit(DOCUMENT.components.schemas[name], name);
  }
  return out;
}

/** The vocabularies the table states, by place, from the table alone: the places are named as the generator
 * names them (a component, its member's path, `[]` an item, `{}` a map's value, `{pattern}` a pattern's, `{key}` a key's). */
function tableVocabularies(): Record<string, readonly string[]> {
  const out: Record<string, readonly string[]> = {};
  const visit = (shape: Shape, at: string): void => {
    switch (shape.t) {
      case "enum":
        out[at] = shape.v;
        return;
      case "const":
        if (typeof shape.v === "string") {
          out[at] = [shape.v];
        }
        return;
      case "or":
      case "all":
        for (const member of shape.o) {
          visit(member, at);
        }
        return;
      case "arr":
        visit(shape.i, `${at}[]`);
        (shape.pi ?? []).forEach((member, index) => {
          visit(member, `${at}[${String(index)}]`);
        });
        return;
      case "rec":
        for (const [name, member] of Object.entries(shape.p)) {
          visit(member, `${at}.${name}`);
        }
        return;
      case "map":
        visit(shape.v, `${at}{}`);
        for (const [pattern, member] of shape.pp ?? []) {
          visit(member, `${at}{${pattern}}`);
        }
        if (shape.k !== undefined) {
          visit(shape.k, `${at}{key}`);
        }
        return;
      default:
    }
  };
  for (const [name, shape] of Object.entries(SHAPES)) {
    visit(shape, name);
  }
  for (const name of FAIL_CLOSED.keys()) {
    const listed = DOCUMENT.components.schemas[name] as { enum: string[] };
    out[name] = listed.enum;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** The lists (a vocabulary's words, JSON text) the table has and the document does not, and the document's the table does not (the comparison's own filters). */
function listsDiffer(table: Record<string, readonly string[]>, document: ReadonlySet<string>): { readonly table: string[]; readonly document: string[] } {
  const listed = new Set(Object.values(table).map((words) => JSON.stringify(words)));
  return { table: [...listed].filter((words) => !document.has(words)), document: [...document].filter((words) => !listed.has(words)) };
}

describe("VOCABULARIES is held to the table and to the document, each way, and pinned (MA4)", () => {
  const fixture = JSON.parse(readFileSync(path.join(import.meta.dirname, "../fixtures/vocabularies.json"), "utf8")) as { vocabularies: Record<string, string[]>; fallbacks: Record<string, string> };

  it("is the reviewed fixture's: a word added or taken off any vocabulary, or a place, is a change to the fixture, in review", () => {
    expect({ vocabularies: TABLE_VOCABULARIES, fallbacks: FALLBACKS }).toEqual(fixture);
    expect(Object.keys(fixture.vocabularies).length).toBeGreaterThan(30);
  });

  it("is, place by place, the words of the table's own nodes (both ways: no place and no word more or less)", () => {
    expect(TABLE_VOCABULARIES).toEqual(tableVocabularies());
  });

  it("is, list by list, the words of the document's vocabularies the walk reads (both ways)", () => {
    const found = listsDiffer(TABLE_VOCABULARIES, documentLists());
    expect(found.table, "a vocabulary the table lists and the document does not have").toEqual([]);
    expect(found.document, "a vocabulary the document has and the table does not list").toEqual([]);
  });

  it("every fail-closed vocabulary lists its fallback, and the fallbacks are the generator's", () => {
    expect(FALLBACKS).toEqual(Object.fromEntries(FAIL_CLOSED));
    for (const [name, member] of Object.entries(FALLBACKS)) {
      expect((TABLE_VOCABULARIES as Record<string, readonly string[]>)[name], name).toContain(member);
    }
  });

  it("the comparison's controls: a word more, a word less, a place more and a list the document lacks are each seen", () => {
    const table = tableVocabularies();
    const more = { ...table, Severity: [...(table["Severity"] ?? []), "~extra"] };
    expect(more).not.toEqual(TABLE_VOCABULARIES);
    const less = { ...table, Severity: (table["Severity"] ?? []).slice(1) };
    expect(less).not.toEqual(TABLE_VOCABULARIES);
    const place = { ...table, "Nowhere.extra": ["x"] };
    expect(place).not.toEqual(TABLE_VOCABULARIES);
    expect(documentLists().has(JSON.stringify(["~extra"]))).toBe(false);
    expect(documentLists().has(JSON.stringify(table["Severity"]))).toBe(true);
  });

  it("the list comparison's controls run through its own filters (n4): a deliberate extra list on either side is flagged", () => {
    const document = documentLists();
    expect(listsDiffer(TABLE_VOCABULARIES, document)).toEqual({ table: [], document: [] });
    // A list the table has and the document lacks (a word added to a vocabulary, a place added).
    expect(listsDiffer({ ...TABLE_VOCABULARIES, X: ["~extra"] }, document)).toEqual({ table: [JSON.stringify(["~extra"])], document: [] });
    expect(listsDiffer({ ...TABLE_VOCABULARIES, Severity: [...TABLE_VOCABULARIES.Severity, "~extra"] }, document).table).toHaveLength(1);
    // A list the document has and the table lacks (a word taken off, a place dropped), and a deliberate extra word on the document's side.
    const [first] = [...document];
    const lacking = new Set([...document].filter((words) => words !== first));
    expect(listsDiffer(TABLE_VOCABULARIES, lacking)).toEqual({ table: [first], document: [] });
    expect(listsDiffer(TABLE_VOCABULARIES, new Set([...document, JSON.stringify(["~extra"])]))).toEqual({ table: [], document: [JSON.stringify(["~extra"])] });
  });
});
