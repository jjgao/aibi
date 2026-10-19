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
import { type Body, bodies, entryShape, jsonTypeOf, JSON_TYPES, ownAlternatives, REF, SAMPLE, typesOf, variants } from "./typed-support";

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
const placeOf = (component: string, at: readonly Step[], operation: string, arms: ReadonlyMap<string, number> = NO_ARMS): string =>
  `${component} ${at.map((step, index) => `${typeof step === "number" ? "[]" : step}${armLabel(arms, at.slice(0, index + 1))}`).join("/")} ${operation}`;

/** No arms: a body with no alternative of two object arms. */
const NO_ARMS: ReadonlyMap<string, number> = new Map();

/** The walked arm at a path, as the place's key reads it (`|i`), or nothing. */
const armLabel = (arms: ReadonlyMap<string, number>, at: readonly Step[]): string => {
  const arm = arms.get(JSON.stringify(at));
  return arm === undefined ? "" : `|${String(arm)}`;
};

/** Whether an alternative has two object arms or more (the places under it are told apart by the arm walked). */
const objectArms = (shape: Extract<Shape, { readonly o: readonly Shape[] }>): boolean => shape.o.filter((arm) => typesOf(arm).has("object")).length > 1;

/** Whether an arm of an alternative takes a value, by the oracle's own judges, never the walk under
 * test (m4 of #128's round 1): a reference arm by the oracle's Ajv validator of its component, an enum
 * or a const arm by its own values, any other arm by its JSON types (an alternative's other arms are of
 * disjoint JSON types: a cell below holds it, so the type decides). */
function armTakes(arm: Shape, value: unknown, oracle: Judges["oracle"] = JUDGES.oracle): boolean {
  switch (arm.t) {
    case "ref":
      return oracle(arm.n, value);
    case "enum":
      return typeof value === "string" && arm.v.includes(value);
    case "const":
      return JSON.stringify(arm.v) === JSON.stringify(value);
    default:
      return typesOf(arm).has(jsonTypeOf(value));
  }
}

/** Whether a traversal of the oracle follows a member of an alternative or a conjunction into a
 * value: the one arm decision of every traversal here (`positionsOf`, `classesOf`, `armsOf` and
 * `untakenArms`), never the walk under test. An alternative's arm is followed when `armTakes` says it
 * takes the value; a conjunction's every member is (a body is valid, so it is valid by each member). */
function follows(shape: Extract<Shape, { readonly o: readonly Shape[] }>, member: Shape, value: unknown): boolean {
  return shape.t === "all" || armTakes(member, value);
}

/** A body as JSON decoded from its text, as the oracle judges every input (a body made by the tests is
 * an object they built). */
const asJson = (value: unknown): unknown => JSON.parse(JSON.stringify(value)) as unknown;

/** The arm taken at each node of a body that an alternative of two object arms or more takes (D434: a
 * values model's columns), found by walking the body down the table and deciding each arm with
 * `armTakes`: the place's key holds it, so that the dedupe by place does not merge the arms (m3). A
 * check of an `allOf` is followed whole (a body is valid, so it is valid by each member). */
function armsOf(shape: Shape, value: unknown, at: readonly Step[] = [], out = new Map<string, number>(), depth = 0): Map<string, number> {
  if (depth > 40) {
    return out;
  }
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? out : armsOf(found, value, at, out, depth + 1);
    }
    case "or":
    case "all":
      shape.o.forEach((member, index) => {
        if (follows(shape, member, value)) {
          if (shape.t === "or" && objectArms(shape) && typesOf(member).has("object")) {
            out.set(JSON.stringify(at), index);
          }
          armsOf(member, value, at, out, depth + 1);
        }
      });
      return out;
    case "arr":
      if (Array.isArray(value)) {
        (value as unknown[]).forEach((item, index) => {
          armsOf(shape.pi?.[index] ?? shape.i, item, [...at, index], out, depth + 1);
        });
      }
      return out;
    case "rec":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        for (const [name, member] of Object.entries(shape.p)) {
          if (Object.hasOwn(value, name)) {
            armsOf(member, (value as Json)[name], [...at, name], out, depth + 1);
          }
        }
      }
      return out;
    case "map":
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        for (const [key, member] of Object.entries(value as Json)) {
          armsOf(entryShape(shape, key), member, [...at, key], out, depth + 1);
        }
      }
      return out;
    default:
      return out;
  }
}

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
        if (follows(shape, member, value)) {
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
        if (follows(shape, member, value)) {
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
    const arms = armsOf(REF(component), asJson(value));
    for (const at of nodesOf(value)) {
      const node = get(value, at);
      if (Array.isArray(node) || typeof node !== "object" || node === null) {
        continue;
      }
      const keys = Object.keys(node);
      for (const key of keys) {
        if (once("drops", placeOf(component, at, `drop ${key}`, arms))) {
          add("drops", component, placeOf(component, at, `drop ${key}`, arms), () =>
            changedAt(value, at, (target) => {
              Reflect.deleteProperty(target, key);
            }),
          );
        }
        if (once("renames", placeOf(component, at, `rename ${key}`, arms))) {
          add("renames", component, placeOf(component, at, `rename ${key}`, arms), () =>
            changedAt(value, at, (target) => {
              target[`${key}~`] = target[key];
              Reflect.deleteProperty(target, key);
            }),
          );
        }
      }
      for (const added of ["~probe", "/outside"]) {
        if (once("additions", placeOf(component, at, `add ${added}`, arms))) {
          add("additions", component, placeOf(component, at, `add ${added}`, arms), () =>
            changedAt(value, at, (target) => {
              target[added] = keys[0] === undefined ? 1 : target[keys[0]];
            }),
          );
        }
      }
    }
  }
  for (const { component, value } of small) {
    const arms = armsOf(REF(component), asJson(value));
    for (const at of nodesOf(value)) {
      const node = get(value, at) as Record<string, unknown>;
      for (const [key, leaf] of Object.entries(node)) {
        if (typeof leaf === "object" && leaf !== null) {
          continue;
        }
        const where = [...at, key];
        const put = (operation: string, arm: string, replacement: unknown): void => {
          if (once(arm, placeOf(component, where, operation, arms))) {
            add(arm, component, placeOf(component, where, operation, arms), () =>
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
    const arms = armsOf(REF(component), asJson(value));
    const found: Position[] = [];
    positionsOf(REF(component), asJson(value), [], found);
    for (const { kind, at, words, samples } of found) {
      for (const word of words) {
        if (kind === "value") {
          const parent = at.slice(0, -1);
          const key = at.at(-1);
          if (key !== undefined && once("words", placeOf(component, at, `table word ${word}`, arms))) {
            add("words", component, placeOf(component, at, `table word ${word}`, arms), () =>
              changedAt(value, parent, (target) => {
                target[key] = word;
              }),
            );
          }
        } else if (once("keys", placeOf(component, at, `table key ${word}`, arms))) {
          add("keys", component, placeOf(component, at, `table key ${word}`, arms), () =>
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

/** The arms of the table's alternatives that no body of `made` takes, each decided by `armTakes`
 * (the coverage cell's, and its negative control's). */
function untakenArms(made: readonly Body[]): string[] {
  const taken = new Map<Shape, Set<number>>();
  const visit = (shape: Shape, value: unknown, depth = 0): void => {
    if (depth > 40) {
      return;
    }
    switch (shape.t) {
      case "ref": {
        const found = SHAPES[shape.n];
        if (found !== undefined) {
          visit(found, value, depth + 1);
        }
        return;
      }
      case "or":
      case "all":
        shape.o.forEach((member, index) => {
          if (shape.t === "all" || armTakes(member, value)) {
            if (shape.t === "or") {
              taken.set(shape, (taken.get(shape) ?? new Set()).add(index));
            }
            visit(member, value, depth + 1);
          }
        });
        return;
      case "arr":
        if (Array.isArray(value)) {
          (value as unknown[]).forEach((item, index) => {
            visit(shape.pi?.[index] ?? shape.i, item, depth + 1);
          });
        }
        return;
      case "rec":
        if (typeof value === "object" && value !== null && !Array.isArray(value)) {
          for (const [name, member] of Object.entries(shape.p)) {
            if (Object.hasOwn(value, name)) {
              visit(member, (value as Json)[name], depth + 1);
            }
          }
        }
        return;
      case "map":
        if (typeof value === "object" && value !== null && !Array.isArray(value)) {
          for (const [key, member] of Object.entries(value as Json)) {
            visit(entryShape(shape, key), member, depth + 1);
          }
        }
        return;
      default:
    }
  };
  for (const { component, value } of made) {
    visit(REF(component), asJson(value));
  }
  const untaken: string[] = [];
  for (const [name, shape] of Object.entries(SHAPES)) {
    for (const or of ownAlternatives(shape)) {
      or.o.forEach((arm, index) => {
        if (!(taken.get(or)?.has(index) ?? false)) {
          untaken.push(`${name} arm ${String(index)} (${arm.t === "ref" ? arm.n : arm.t})`);
        }
      });
    }
  }
  return untaken;
}

/** The set's shape, pinned as `census.json` pins the keyword lists (a change of the document or of an arm is a
 * reviewed change to this table, not a silent one): the inputs of each arm, the candidates the dedupe by
 * place merged, and the bodies. Without it an arm thinned (to the root, to the last step of a path) still
 * passes while it tests less. Last moved by D431's four orchard answers (175 bodies to 179, and the
 * places they add); then by D433's eight builder answers (179 bodies to 187); then by D434's values
 * models (their components reached as roots, the arms of `DistributionPosition.columns` forced and
 * told apart in the place's key) and its eleven values answers (187 bodies to 240). */
const INPUT_COUNTS = {
  inputs: { additions: 2066, bodies: 240, drops: 3357, keys: 456, misses: 2423, renames: 3357, swaps: 5744, values: 15595, words: 2655 },
  merged: { additions: 16740, drops: 20793, keys: 1635, misses: 45939, renames: 20793, swaps: 15423, values: 92330, words: 16491 },
  bodies: { all: 240, small: 238, over: 2 },
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
      classesOf(REF(body.component), asJson(body.value), [], places, opaque);
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

  it("the alternatives of the table with two object arms are the pinned list (the dedupe by place tells their arms apart by the place's key: m3)", () => {
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
    // D434: `ColumnDistribution` (a values model's column) is the one alternative of object arms; the
    // place's key holds the arm walked there (`armsOf`), so the dedupe does not merge its arms.
    expect(crowded, "an alternative of two object arms: its places are told apart by the walked arm (armsOf); a new one is a reviewed change").toEqual(["DistributionPosition.columns[]"]);
  });

  it("the place's key tells the arms of an alternative of object arms apart, and every arm of every alternative is taken by a body (D434)", () => {
    const untaken = untakenArms(all);
    expect(untaken).toEqual([]);
    // Its negative control: without the bodies whose arm is forced, the arms past the plans' reach
    // are reported untaken (m2 of #128's round 2).
    const unforced = untakenArms(all.filter(({ source }) => !source.startsWith("synthetic arm")));
    expect(unforced.filter((arm) => arm.startsWith("DistributionPosition arm "))).toEqual(["DistributionPosition arm 3 (NumberRows)", "DistributionPosition arm 4 (MembershipDistribution)"]);
    const columns = small.filter(({ component }) => component === "DistributionValues" || component === "DistributionPosition");
    const places = new Set(columns.flatMap(({ component, value }) => [...armsOf(REF(component), asJson(value)).values()]));
    expect([...places].sort()).toEqual([0, 1, 2, 3, 4]);
  });

  it("armTakes decides an arm by the oracle alone: an alternative's arms that are no reference, enum or const are of disjoint JSON types, and every arm of two object arms or more is a reference (m4 of #128)", () => {
    const overlapping: string[] = [];
    for (const [name, shape] of Object.entries(SHAPES)) {
      for (const or of ownAlternatives(shape)) {
        if (or.t !== "or") {
          continue;
        }
        const own = or.o.filter((arm) => arm.t !== "ref" && arm.t !== "enum" && arm.t !== "const");
        const seen = new Set<string>();
        for (const arm of own) {
          for (const type of typesOf(arm)) {
            if (seen.has(type)) {
              overlapping.push(`${name}: ${type}`);
            }
            seen.add(type);
          }
        }
        if (objectArms(or) && or.o.some((arm) => arm.t !== "ref" && typesOf(arm).has("object"))) {
          overlapping.push(`${name}: an object arm that is no reference`);
        }
      }
    }
    expect(overlapping).toEqual([]);
    // Its controls, one per branch: a reference arm is judged by the oracle, not by its JSON type; an
    // enum arm refuses a string outside its values; a const arm refuses another value; a null arm
    // refuses 0, "" and {}; a type arm refuses another JSON type (m2 of #128's round 2).
    expect(armTakes(REF("NumberDistribution"), { kind: "categories", categories: [] })).toBe(false);
    expect(armTakes(REF("CategoryDistribution"), { kind: "categories", categories: [] })).toBe(true);
    // The reference branch follows the oracle it is given, never the walk: a deliberately wrong
    // oracle's verdict is the judge's, both ways.
    expect(armTakes(REF("CategoryDistribution"), { kind: "categories", categories: [] }, () => false)).toBe(false);
    expect(armTakes(REF("NumberDistribution"), { kind: "categories", categories: [] }, () => true)).toBe(true);
    // A conjunction's member is followed whatever its own verdict (a body is valid by each member);
    // an alternative's arm only when the judge takes the value.
    const enumArm: Shape = { t: "enum", v: ["a"] };
    expect(follows({ t: "all", o: [enumArm] }, enumArm, "b")).toBe(true);
    expect(follows({ t: "or", o: [enumArm] }, enumArm, "b")).toBe(false);
    expect(armTakes({ t: "enum", v: ["a", "b"] }, "b")).toBe(true);
    expect(armTakes({ t: "enum", v: ["a", "b"] }, "c")).toBe(false);
    expect(armTakes({ t: "const", v: "x" }, "x")).toBe(true);
    expect(armTakes({ t: "const", v: "x" }, "y")).toBe(false);
    expect(armTakes({ t: "const", v: { a: [1] } }, { a: [1] })).toBe(true);
    expect(armTakes({ t: "const", v: { a: [1] } }, { a: [2] })).toBe(false);
    expect(armTakes({ t: "null" }, null)).toBe(true);
    expect([0, "", {}, [], false].map((value) => armTakes({ t: "null" }, value))).toEqual([false, false, false, false, false]);
    expect(armTakes({ t: "str" }, "s")).toBe(true);
    expect([1, null, {}, [], true].map((value) => armTakes({ t: "str" }, value))).toEqual([false, false, false, false, false]);
    expect(armTakes({ t: "num" }, 1)).toBe(true);
    expect(armTakes({ t: "num" }, "1")).toBe(false);
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
