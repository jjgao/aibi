// @ts-check
/**
 * The typed answers' table (D426): `src/api/generated/shapes.ts`, generated from
 * `schemas/openapi.json` by `scripts/generate-types.mjs` beside the types and the routes, and held
 * fresh by `npm run generate:check`. `src/api/typed.ts` walks every answer of a public tool the web
 * calls against it, validating and boxing in one pass (every string a `ServerText`, every map a
 * `BoxedMap` with boxed keys), so that what it returns is the shape the generated types say, at
 * full depth, or the answer is `invalid`.
 *
 * **What the table holds** (one node per schema the answers reach, the components by name): the
 * JSON type of every member; a record's members, the required ones and nothing else
 * (`additionalProperties: false`, which every record an answer is built from states); a map's
 * values (`additionalProperties` as a schema, `patternProperties` by their patterns, the first that
 * matches a key) and its keys (`propertyNames`); a list's items and `prefixItems`; `enum` and
 * `const` (strings compared, numbers never read: the box, D419); `anyOf`, `oneOf` and a type list
 * as alternatives; `allOf` as a conjunction whose first member gives the shape and whose others are
 * checks; the segments (`TextSegment | DataSegment`) as one node with their exact key sets; and the
 * `x-aibi-json` components (`OutputJson`) as any JSON value; an empty closed object (exactly `properties:
 * {}` and `additionalProperties: false`: `NoViewValues`) as a map of no entries (the `empty` builder,
 * D434), as the types' generator renders it (`Record<string, never>`). The values models the
 * document publishes at its top level (`x-aibi-values`) are roots beside the operations' answers,
 * listed by `<id>@<version>` (`VALUES`, D434: what `typed.ts`' `valuesOf` walks a result's `values`
 * by); an entry that is not exactly one `$ref` to a component fails. The enum and const vocabularies are
 * listed by their place (`VOCABULARIES`, for `text.tsx`'s `kindOf`), a fail-closed one (`Severity`)
 * is not enumerated by the walk (an unknown severity is read as `block`, never refused), and the
 * grammars of four id positions (`GRAMMARS`, for `idOf` and `choiceFor`, D432) are read from their
 * `pattern`, `maxLength` and `not`.
 *
 * **The keyword census, by position.** Each node builder declares the keywords it consumes
 * (`CONSUMES`: builder to keywords), and the generation fails on any keyword at a node that its
 * builder did not consume, that is no annotation (`ANNOTATIONS`: `title`, `description`, `default`,
 * the `x-aibi-*` marks) and that is no named residual (`RESIDUALS`: each with the reason the walk
 * does not check it). So a keyword interpreted somewhere is not silently dropped where another
 * builder builds the node (`required` beside `additionalProperties` on a map, `properties` beside
 * `anyOf`, `required` alone in an `allOf` check, `items` on an object): the census asks of every
 * schema what its own node consumed, not whether the name is known (MA1 of the typed answers' code
 * review, round 1). A new keyword is read by a builder or named a residual before any answer carries it.
 * The arms of a segment pair are censused as `ref` nodes (a `$ref`, annotations and residuals: no
 * `required`, `nullable` or `uniqueItems` beside it), and an `x-aibi-json` mark has `anyOf` beside it
 * and no other keyword (round 3, m1 and m3), the `anyOf` held to the JSON types: each of the six once,
 * its arms no more than a type (round 4, n2: `jsonArms`).
 *
 * **`CONSUMES` is true by construction** (MA3, round 2): the tests hand every builder its schema
 * through a `Proxy` that records the keys it reads (`observer`, below) and assert that the keys a
 * builder reads at a node are exactly the keys the node has that `CONSUMES` declares for it, over
 * every node of the real document and over a synthetic node of each builder but `failClosed` (whose
 * one schema, `Severity`, only the real document gives) carrying every keyword it declares. A builder that declares a keyword it does not apply, or applies one it does not
 * declare, fails there; `CONSUMES`, `ANNOTATIONS` and `DELEGATING` are pinned by a reviewed fixture
 * (`tests/fixtures/census.json`), so a word added to any of them shows in review. The two builders
 * that hand their siblings to another node (`DELEGATING`: an `allOf`'s base, a type list's arms) hand
 * them on whole, to `node`, which censuses every key it was given.
 *
 * **What the generation refuses beside the census:** `required` that is not a list of strings
 * (read as no required member it would drop the constraint), a record's `additionalProperties`
 * that is a schema (a record is closed or open, and is not asked what an extra member is: the walk
 * keeps it out, or reads it as any JSON), a `type` beside a `const` or an `enum` that the value
 * given is not of (the type is read and held to the value, not ignored), and an empty type list.
 *
 * **Annotations that mean something on the server.** `x-aibi-computed` is an annotation of the
 * walk (it checks nothing), but on the server its `null` means *not estimable*, with a reason
 * (`not_estimable`, SPEC §8.2): the walk keeps `null` and an absent member apart (the round trip
 * holds that), and the renderer of M5.1c-2b reads a `null` of such a member as "not estimable",
 * never as absent or zero (missing is not negative).
 *
 * The generation also fails on: an operation the web calls whose 2xx answer is not exactly its `200`
 * component, or one of whose other answers is not `Refusals`; a record an answer is built from that
 * does not state `additionalProperties: false`; segments whose
 * two schemas are not exactly `{text}` and `{data, truncated?: true}`; a non-string `enum`; a member
 * named `__proto__`; a pattern JavaScript cannot compile with the `u` flag; and a grammar position
 * that is not exactly a pattern, a length and `not` patterns.
 */
import { createHash } from "node:crypto";

/** The public tools the web calls, each with a function of `src/api/typed.ts`. */
export const TYPED_OPERATIONS = ["search_catalog", "describe_dataset", "list_analyses", "validate_document", "count_cohort", "run_analysis", "explain"];

/** The JSON types, the closed set an opaque (`x-aibi-json`) value may take: the arms beside the mark list each once. */
export const JSON_TYPES = ["null", "boolean", "number", "string", "array", "object"];

/** The answer of every status but a 2xx: a refusal list (D265). */
export const REFUSALS = "Refusals";

/** The vocabularies the walk does not enumerate, each read fail-closed as its member given here
 * (an unknown severity is `block`: shown, and the result read as no answer). */
export const FAIL_CLOSED = new Map([["Severity", "block"]]);

/** The id positions whose grammars `idOf` and `choiceFor` read: a component's member of the
 * answers, by the name of the grammar a `Choice`'s role is made under (D432). */
export const GRAMMAR_POSITIONS = new Map([
  ["dataset", ["CatalogHit", "dataset"]],
  ["table", ["TableSummary", "id"]],
  ["column", ["ColumnBrief", "id"]],
  ["analysis", ["ApplicableAnalysis", "analysis"]],
]);

/**
 * The keywords each node builder consumes: the census (`census`) refuses any other keyword of a
 * schema its builder builds that is no annotation and no residual. Each list is the keys the builder
 * (or the dispatch that picks it) reads at its node, no more and no fewer: the tests hold that with
 * a `Proxy` (`observer`). `type` beside a `const` or an `enum` is read and held to the value given.
 * An `x-aibi-json` node's own keywords are the mark and `anyOf` (the JSON types the document lists
 * beside it, each arm a JSON type and no more, `jsonArms`: the inside of the value is read as any
 * JSON, a named residual of D426); any other keyword beside the mark fails the generation, as it does at every
 * other node (it would be blind to the walk and to the oracle).
 * @type {Record<string, readonly string[]>}
 */
export const CONSUMES = {
  json: ["x-aibi-json", "anyOf"],
  allOf: ["allOf"],
  ref: ["$ref"],
  segments: ["anyOf", "oneOf"],
  alternatives: ["anyOf", "oneOf", "type"],
  const: ["const", "type"],
  enum: ["enum", "type"],
  typeList: ["type"],
  string: ["type"],
  number: ["type"],
  boolean: ["type"],
  null: ["type"],
  list: ["type", "items", "prefixItems"],
  record: ["type", "properties", "required", "additionalProperties"],
  map: ["type", "required", "additionalProperties", "patternProperties", "propertyNames"],
  empty: ["type", "properties", "additionalProperties"],
  any: [],
  failClosed: ["type", "enum"],
};

/**
 * The builders that hand the rest of their schema to another node, whole, so that the node it is
 * handed to censuses every key (an `allOf`'s base, built by `node` from the siblings of the `allOf`;
 * a type list's arms, each built by `node` from the schema with one of the types): they read every
 * key of their own and census none beyond `CONSUMES`.
 */
export const DELEGATING = ["allOf", "typeList"];

/** The keywords the walk interprets: those some builder consumes. */
export const INTERPRETED = [...new Set(Object.values(CONSUMES).flat())];

/** The keywords the walk does not check, each with its reason (D426's residuals): the one place. */
export const RESIDUALS = new Map([
  ["if", "a member's presence tied to another's (a null estimate and its not_estimable reason, a caveat's code and its severity)"],
  ["then", "the other half of if"],
  ["else", "the other half of if"],
  ["not", "a negative condition (an id position's grammar reads its own not)"],
  ["contains", "a condition on some item of a list"],
  ["minContains", "a count bound of contains"],
  ["maxContains", "a count bound of contains"],
  ["pattern", "a string's pattern as a validator (a map key's pattern is read, to pick its value's shape)"],
  ["minLength", "a length bound: no length of a string is read"],
  ["maxLength", "a length bound: no length of a string is read"],
  ["minItems", "a count bound: no length of a list is read"],
  ["maxItems", "a count bound: no length of a list is read"],
  ["minProperties", "a count bound: no size of an object is read"],
  ["maxProperties", "a count bound: no size of an object is read"],
  ["minimum", "a numeric bound: the walk never reads a number (D419)"],
  ["maximum", "a numeric bound: the walk never reads a number (D419)"],
  ["exclusiveMinimum", "a numeric bound: the walk never reads a number (D419)"],
  ["exclusiveMaximum", "a numeric bound: the walk never reads a number (D419)"],
]);

/** The residuals by name. */
export const RESIDUAL = [...RESIDUALS.keys()];

/** The annotations: nothing to check (the numbers are boxed by the decoder, every string by the
 * walk). `x-aibi-computed` checks nothing here but means something on the server: its `null` is
 * *not estimable* (SPEC §8.2), which the walk keeps apart from an absent member. */
export const ANNOTATIONS = ["title", "description", "default", "x-aibi-computed", "x-aibi-data", "x-aibi-server-number"];

/** The keywords that, alone in an `allOf` member, check nothing the walk reads. */
const CONDITIONS = new Set(["if", "then", "else", "not", "contains", "minContains", "maxContains"]);

export class ShapeError extends Error {}

/** Whether a schema is an empty closed object (D434): `properties: {}`, `additionalProperties: false`,
 * `type`, and nothing else but annotations (`ANNOTATIONS`). @param {JsonObject} schema */
function isEmptyClosed(schema) {
  const members = schema["properties"];
  return (
    isObject(members) &&
    Object.keys(members).length === 0 &&
    schema["additionalProperties"] === false &&
    // `Reflect.ownKeys`, as the census's own, is no read of a keyword (the observer's).
    Reflect.ownKeys(schema).every((key) => typeof key === "string" && ((CONSUMES["empty"] ?? []).includes(key) || ANNOTATIONS.includes(key)))
  );
}

/** @typedef {Record<string, unknown>} JsonObject */
/**
 * @typedef {{ t: "ref", n: string } | { t: "json" | "any" | "never" | "str" | "num" | "bool" | "null" | "seg" }
 *   | { t: "enum", v: string[] } | { t: "const", v: unknown } | { t: "or" | "all", o: Shape[] }
 *   | { t: "arr", i: Shape, pi?: Shape[] } | { t: "rec", p: Record<string, Shape>, r: string[], open?: true }
 *   | { t: "map", v: Shape, pp?: [string, Shape][], k?: Shape, r?: string[] }} Shape
 */

/** @param {unknown} value @returns {value is JsonObject} */
function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** @param {unknown} value @param {string} at @returns {JsonObject} */
function object(value, at) {
  if (!isObject(value)) {
    throw new ShapeError(`${at}: a schema is an object`);
  }
  return value;
}

/** @param {string} pattern @param {string} at */
function compiled(pattern, at) {
  try {
    new RegExp(pattern, "u");
  } catch {
    throw new ShapeError(`${at}: the pattern ${JSON.stringify(pattern)} does not compile with the u flag`);
  }
  return pattern;
}

/**
 * A schema's keywords against what its node's builder consumes (`CONSUMES`), the annotations and the
 * named residuals. A delegating builder (`DELEGATING`) hands its other keys on, whole, to the node
 * that builds them, whose census reads each.
 * @param {JsonObject} schema @param {string} at @param {string} builder
 * @param {Record<string, readonly string[]>} [consumes] the builders' table (the tests' synthetic ones)
 * @param {readonly string[]} [delegating] the builders that hand their other keys on
 */
export function censusOf(schema, at, builder, consumes = CONSUMES, delegating = DELEGATING) {
  const consumed = consumes[builder];
  if (consumed === undefined) {
    throw new ShapeError(`${at}: the builder ${builder} declares no keywords`);
  }
  if (delegating.includes(builder)) {
    return;
  }
  for (const keyword of Reflect.ownKeys(schema).map(String)) {
    if (!consumed.includes(keyword) && !ANNOTATIONS.includes(keyword) && !RESIDUALS.has(keyword)) {
      throw new ShapeError(`${at}: the keyword ${keyword} is neither consumed by the ${builder} node, an annotation, nor a named residual`);
    }
  }
}

/**
 * A list of strings (`required`): absent is empty, anything else that is no list of strings is
 * refused (read as an empty list it would drop the constraint).
 * @param {unknown} value @param {string} at @returns {string[]}
 */
function strings(value, at) {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value) || !value.every((member) => typeof member === "string")) {
    throw new ShapeError(`${at}: required is a list of strings`);
  }
  return value;
}

/**
 * What the tests watch: the schema each node is built from is handed through `visit` (a `Proxy` that
 * records the keys read), and the builder a schema's node is built by is named by `built`.
 * @typedef {{ visit: (schema: JsonObject) => JsonObject, built: (schema: JsonObject, builder: string) => void }} Observer
 */

/** Whether a JSON type word (`type`) is the type of a constant. @param {unknown} type @param {unknown} value */
function typeOfConst(type, value) {
  switch (type) {
    case "string":
      return typeof value === "string";
    case "integer":
      return typeof value === "number" && Number.isInteger(value);
    case "number":
      return typeof value === "number";
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    case "array":
      return Array.isArray(value);
    case "object":
      return isObject(value);
    default:
      return false;
  }
}

/**
 * The node builders of a document: `node` builds any schema of it (a `$ref` reads the document's
 * components, the table filling as it goes), `component` a component by its reference. The tests
 * use it to build one synthetic schema, and to watch the reads of each builder (`observer`).
 * @param {JsonObject} document @param {Observer} [observer]
 */
export function builderOf(document, observer) {
  const components = object(object(document["components"], "#/components")["schemas"], "#/components/schemas");
  /** @type {Map<string, Shape>} */
  const table = new Map();
  /** @type {Map<string, string[]>} */
  const vocabularies = new Map();
  /** @type {Set<string>} */
  const reaching = new Set();

  /** @param {string} name @param {string[]} members @param {string} at */
  const vocabulary = (name, members, at) => {
    const known = vocabularies.get(name);
    if (known !== undefined && JSON.stringify(known) !== JSON.stringify(members)) {
      throw new ShapeError(`${at}: two vocabularies named ${name}`);
    }
    vocabularies.set(name, members);
  };

  /** @param {string} ref @param {string} at */
  const component = (ref, at) => {
    const found = /^#\/components\/schemas\/([A-Za-z0-9._-]+)$/u.exec(ref);
    if (found?.[1] === undefined || !Object.hasOwn(components, found[1])) {
      throw new ShapeError(`${at}: ${ref} is no component`);
    }
    const name = found[1];
    if (!table.has(name) && !reaching.has(name)) {
      reaching.add(name);
      const schema = object(components[name], `#/components/schemas/${name}`);
      table.set(name, FAIL_CLOSED.has(name) ? failClosed(name, observer === undefined ? schema : observer.visit(schema)) : node(schema, name, false));
      reaching.delete(name);
    }
    return name;
  };

  /**
   * The `anyOf` beside an `x-aibi-json` mark is held to the JSON types (n2 of the code review's round 4):
   * the walk reads the value as any JSON, so an arm that narrowed it would be blind to the walk and,
   * were it stripped, to the oracle. The arms are objects whose `type`s (each a JSON type, one or a list)
   * are the six JSON types, each once (the mark states `null: true`, which the walk takes); each carries
   * annotations, and nothing else but `minimum`/`maximum` on `number`, `items` on `array` and
   * `additionalProperties` on `object`, each a reference to a component that is itself an
   * `x-aibi-json` one (never `required`, `enum`, `const`, `not`, `pattern`...).
   * @param {JsonObject} schema @param {string} at
   */
  const jsonArms = (schema, at) => {
    const mark = schema["x-aibi-json"];
    if (!isObject(mark) || mark["null"] !== true) {
      throw new ShapeError(`${at}: an x-aibi-json mark states null: true (the walk takes null as a JSON value)`);
    }
    const arms = schema["anyOf"];
    if (!Array.isArray(arms)) {
      throw new ShapeError(`${at}: beside x-aibi-json, anyOf lists the JSON types`);
    }
    const allowed = new Map([
      ["minimum", "number"],
      ["maximum", "number"],
      ["items", "array"],
      ["additionalProperties", "object"],
    ]);
    /** @type {string[]} */
    const types = [];
    for (const arm of arms) {
      if (!isObject(arm)) {
        throw new ShapeError(`${at}: an arm beside x-aibi-json is a schema of JSON types`);
      }
      const type = arm["type"];
      const own = Array.isArray(type) ? type : [type];
      if (own.length === 0 || !own.every((one) => typeof one === "string" && JSON_TYPES.includes(one))) {
        throw new ShapeError(`${at}: an arm beside x-aibi-json has a type of the JSON types ${JSON_TYPES.join(", ")}`);
      }
      for (const keyword of Reflect.ownKeys(arm).map(String)) {
        if (keyword !== "type" && !ANNOTATIONS.includes(keyword) && (own.length !== 1 || own[0] !== allowed.get(keyword))) {
          throw new ShapeError(`${at}: the keyword ${keyword} is not allowed in an arm beside x-aibi-json (an arm is a JSON type)`);
        }
      }
      for (const keyword of ["items", "additionalProperties"]) {
        if (!Object.hasOwn(arm, keyword)) {
          continue;
        }
        const inside = arm[keyword];
        const named = isObject(inside) && Object.keys(inside).join() === "$ref" ? inside["$ref"] : undefined;
        const found = typeof named === "string" ? /^#\/components\/schemas\/([A-Za-z0-9._-]+)$/u.exec(named)?.[1] : undefined;
        const target = found === undefined || !Object.hasOwn(components, found) ? undefined : components[found];
        if (!isObject(target) || !Object.hasOwn(target, "x-aibi-json")) {
          throw new ShapeError(`${at}: the ${keyword} of an arm beside x-aibi-json is a reference to an x-aibi-json component`);
        }
      }
      types.push(...own.map(String));
    }
    if (types.length !== JSON_TYPES.length || !JSON_TYPES.every((one) => types.includes(one))) {
      throw new ShapeError(`${at}: beside x-aibi-json, anyOf lists each of the JSON types ${JSON_TYPES.join(", ")} once`);
    }
  };

  /** A fail-closed vocabulary: its members listed, the walk taking any string. @param {string} name @param {JsonObject} schema @returns {Shape} */
  const failClosed = (name, schema) => {
    census(schema, name, "failClosed");
    const members = schema["enum"];
    if (schema["type"] !== "string" || !Array.isArray(members) || !members.every((member) => typeof member === "string")) {
      throw new ShapeError(`${name}: a fail-closed vocabulary is a string enum`);
    }
    if (!members.includes(FAIL_CLOSED.get(name) ?? "")) {
      throw new ShapeError(`${name}: the fail-closed member is not in the vocabulary`);
    }
    vocabulary(name, members, name);
    return { t: "str" };
  };

  /**
   * The census of a schema at the node its builder builds: every keyword is consumed by the
   * builder (or delegated by it to another node's census), an annotation or a named residual.
   * @param {JsonObject} schema @param {string} at @param {string} builder
   */
  const census = (schema, at, builder) => {
    observer?.built(schema, builder);
    censusOf(schema, at, builder);
  };

  /**
   * A schema's node. `check`: the node only checks (an `allOf` member past the first), so a record
   * may be open; otherwise the node gives the walk's output, and a record is closed.
   * @param {unknown} given @param {string} at @param {boolean} check @returns {Shape}
   */
  const node = (given, at, check) => {
    if (given === true || given === undefined) {
      return { t: "any" };
    }
    if (given === false) {
      return { t: "never" };
    }
    const schema = observer === undefined ? object(given, at) : observer.visit(object(given, at));
    if (Object.hasOwn(schema, "x-aibi-json")) {
      census(schema, at, "json");
      jsonArms(schema, at);
      return { t: "json" };
    }
    if (Array.isArray(schema["allOf"])) {
      census(schema, at, "allOf");
      const allOf = schema["allOf"];
      // The siblings of the allOf are handed on, whole, to the node that builds them (its census
      // reads each key), unless every one is an annotation or a residual: a condition (`if`...) or
      // a bound of the walk's, which the allOf's own members and the siblings' node would not read.
      const rest = Object.fromEntries(Reflect.ownKeys(schema).map(String).filter((keyword) => keyword !== "allOf").map((keyword) => [keyword, schema[keyword]]));
      const checks = allOf.filter((member) => !(isObject(member) && Object.keys(member).every((keyword) => CONDITIONS.has(keyword) || keyword === "title" || keyword === "description")));
      const base = Object.keys(rest).some((keyword) => !ANNOTATIONS.includes(keyword) && !RESIDUALS.has(keyword));
      const members = [
        ...(base ? [node(rest, at, check)] : []),
        ...checks.map((member, index) => node(member, at, base || index > 0 ? true : check)),
      ];
      if (members.length === 0) {
        return { t: "any" };
      }
      return members.length === 1 && members[0] !== undefined ? members[0] : { t: "all", o: members };
    }
    if (typeof schema["$ref"] === "string") {
      census(schema, at, "ref");
      if (/\/(?:TextSegment|DataSegment)$/u.test(schema["$ref"])) {
        throw new ShapeError(`${at}: a segment outside the pair TextSegment | DataSegment`);
      }
      return { t: "ref", n: component(schema["$ref"], at) };
    }
    const arms = schema["anyOf"] ?? schema["oneOf"];
    if (Array.isArray(arms)) {
      if (Array.isArray(schema["anyOf"]) && Array.isArray(schema["oneOf"])) {
        throw new ShapeError(`${at}: anyOf beside oneOf`);
      }
      const refs = arms.map((arm) => (isObject(arm) && typeof arm["$ref"] === "string" ? arm["$ref"] : null));
      if (refs.length === 2 && refs.includes("#/components/schemas/TextSegment") && refs.includes("#/components/schemas/DataSegment")) {
        census(schema, at, "segments");
        // Each arm is a reference and nothing else (annotations and residuals aside): the census reads it
        // as the `ref` node it is, so a keyword beside the `$ref` (`required`, `nullable`, ...) fails (D426).
        const named = arms.map((arm) => {
          const watched = observer === undefined ? object(arm, at) : observer.visit(object(arm, at));
          census(watched, at, "ref");
          return watched["$ref"];
        });
        if (named.join() !== refs.join()) {
          throw new ShapeError(`${at}: a segment pair's arms are the references TextSegment and DataSegment`);
        }
        segments(components);
        return { t: "seg" };
      }
      census(schema, at, "alternatives");
      /** @type {Shape} */
      const alternatives = { t: "or", o: arms.map((arm) => node(arm, at, check)) };
      // A `type` beside the arms is a check of its own on the value (`Caveat.code`: a string).
      return Object.hasOwn(schema, "type") ? { t: "all", o: [alternatives, node({ type: schema["type"] }, at, true)] } : alternatives;
    }
    if (Object.hasOwn(schema, "const")) {
      census(schema, at, "const");
      const value = schema["const"];
      const type = schema["type"];
      if (type !== undefined && !typeOfConst(type, value)) {
        throw new ShapeError(`${at}: a const that is not of its type ${JSON.stringify(type)}`);
      }
      if (typeof value === "string") {
        vocabulary(at, [value], at);
      }
      return { t: "const", v: value };
    }
    if (Array.isArray(schema["enum"])) {
      census(schema, at, "enum");
      const members = schema["enum"];
      if (!members.every((member) => typeof member === "string")) {
        throw new ShapeError(`${at}: an enum of an answer is of strings`);
      }
      if (Object.hasOwn(schema, "type") && schema["type"] !== "string") {
        throw new ShapeError(`${at}: an enum of strings is not of the type ${JSON.stringify(schema["type"])}`);
      }
      vocabulary(at, members, at);
      return { t: "enum", v: members };
    }
    const type = schema["type"];
    if (Array.isArray(type)) {
      census(schema, at, "typeList");
      if (type.length === 0) {
        throw new ShapeError(`${at}: a type list is not empty`);
      }
      /** @type {unknown[]} */
      const types = type;
      // Every arm is built from the whole schema with one of the types: its node censuses every key.
      return { t: "or", o: types.map((one) => node({ ...schema, type: one }, at, check)) };
    }
    if (type === "string") {
      census(schema, at, "string");
      return { t: "str" };
    }
    if (type === "integer" || type === "number") {
      census(schema, at, "number");
      return { t: "num" };
    }
    if (type === "boolean") {
      census(schema, at, "boolean");
      return { t: "bool" };
    }
    if (type === "null") {
      census(schema, at, "null");
      return { t: "null" };
    }
    if (type === "array" || (type === undefined && (Object.hasOwn(schema, "items") || Object.hasOwn(schema, "prefixItems")))) {
      census(schema, at, "list");
      /** @type {Shape} */
      const list = { t: "arr", i: node(schema["items"], `${at}[]`, check) };
      if (Array.isArray(schema["prefixItems"])) {
        list.pi = schema["prefixItems"].map((item, index) => node(item, `${at}[${String(index)}]`, check));
      }
      return list;
    }
    const objectish = ["properties", "additionalProperties", "patternProperties", "propertyNames"].some((keyword) => Object.hasOwn(schema, keyword));
    if (type === "object" || (type === undefined && objectish)) {
      // A closed object with no member (`NoViewValues`, D434) is a map of no entries: what the types'
      // generator renders it (`Record<string, never>`, a `BoxedMap` once boxed), so the table and the
      // type agree. Exactly `type`, `properties: {}` and `additionalProperties: false`, beside
      // annotations: any other keyword (`required`, `patternProperties`, `propertyNames`) leaves the
      // node a record, read and censused as today.
      const members = schema["properties"];
      if (isEmptyClosed(schema)) {
        census(schema, at, "empty");
        return { t: "map", v: { t: "never" } };
      }
      return isObject(members) ? record(schema, at, check) : map(schema, at, check);
    }
    if (type === undefined) {
      census(schema, at, "any");
      return { t: "any" };
    }
    throw new ShapeError(`${at}: the type ${JSON.stringify(type)} is not read`);
  };

  /** @param {JsonObject} schema @param {string} at @param {boolean} check @returns {Shape} */
  const record = (schema, at, check) => {
    census(schema, at, "record");
    const properties = object(schema["properties"], at);
    const additional = schema["additionalProperties"];
    if (additional !== undefined && typeof additional !== "boolean") {
      throw new ShapeError(`${at}: a record's additionalProperties is a boolean: a schema there is not read`);
    }
    const closed = additional === false;
    if (!closed && !check) {
      throw new ShapeError(`${at}: a record an answer is built from states additionalProperties: false`);
    }
    /** @type {Record<string, Shape>} */
    const members = {};
    for (const [name, member] of Object.entries(properties)) {
      if (name === "__proto__") {
        throw new ShapeError(`${at}: a member named __proto__`);
      }
      members[name] = node(member, `${at}.${name}`, check);
    }
    /** @type {Shape} */
    const made = { t: "rec", p: members, r: strings(schema["required"], at) };
    if (!closed) {
      made.open = true;
    }
    return made;
  };

  /** @param {JsonObject} schema @param {string} at @param {boolean} check @returns {Shape} */
  const map = (schema, at, check) => {
    census(schema, at, "map");
    const patterns = schema["patternProperties"];
    const additional = schema["additionalProperties"];
    const required = strings(schema["required"], at);
    /** @type {Shape} */
    const made = { t: "map", v: node(additional === undefined && patterns !== undefined ? false : additional, `${at}{}`, check) };
    if (required.length > 0) {
      made.r = required;
    }
    if (isObject(patterns)) {
      made.pp = Object.entries(patterns).map(([pattern, member]) => [compiled(pattern, at), node(member, `${at}{${pattern}}`, check)]);
    }
    if (Object.hasOwn(schema, "propertyNames")) {
      const names = object(schema["propertyNames"], `${at}{key}`);
      made.k = node(Object.hasOwn(names, "$ref") ? names : { type: "string", ...names }, `${at}{key}`, true);
    }
    return made;
  };

  return { node, component, table, vocabularies, components };
}

/**
 * The table of a document: the components the typed operations' answers reach, each operation's
 * component, the vocabularies and the grammars.
 * @param {JsonObject} document @param {Observer} [observer]
 */
export function shapesOf(document, observer) {
  const { component, table, vocabularies, components } = builderOf(document, observer);
  const paths = object(document["paths"], "#/paths");

  /** @type {Record<string, string>} */
  const operations = {};
  for (const operation of TYPED_OPERATIONS) {
    const item = object(paths[`/api/tools/${operation}`], `/api/tools/${operation}`);
    const responses = object(object(item["post"], `/api/tools/${operation} post`)["responses"], `${operation} responses`);
    for (const [status, response] of Object.entries(responses)) {
      const content = object(object(response, `${operation} ${status}`)["content"], `${operation} ${status} content`);
      const schema = object(object(content["application/json"], `${operation} ${status} JSON`)["schema"], `${operation} ${status} schema`);
      const ref = schema["$ref"];
      if (typeof ref !== "string" || Object.keys(schema).length !== 1) {
        throw new ShapeError(`${operation} ${status}: an answer is one component`);
      }
      const name = component(ref, `${operation} ${status}`);
      if (status === "200") {
        operations[operation] = name;
      } else if (/^[45][0-9]{2}$|^default$/u.test(status)) {
        if (name !== REFUSALS) {
          throw new ShapeError(`${operation} ${status}: an answer but a 200's is ${REFUSALS}`);
        }
      } else {
        throw new ShapeError(`${operation} ${status}: the one answer of 2xx is 200's`);
      }
    }
    if (operations[operation] === undefined) {
      throw new ShapeError(`${operation}: no 200 answer`);
    }
  }
  component(`#/components/schemas/${REFUSALS}`, REFUSALS);

  /** The values models the document publishes (D434), each analysis's at each version. @type {Record<string, string>} */
  const values = {};
  for (const [analysis, versions] of Object.entries(object(document["x-aibi-values"] ?? {}, "#/x-aibi-values"))) {
    for (const [version, reference] of Object.entries(object(versions, `#/x-aibi-values/${analysis}`))) {
      const ref = object(reference, `#/x-aibi-values/${analysis}/${version}`)["$ref"];
      if (typeof ref !== "string" || Object.keys(object(reference, analysis)).length !== 1) {
        throw new ShapeError(`x-aibi-values ${analysis}@${version}: a values model is one component`);
      }
      values[`${analysis}@${version}`] = component(ref, `x-aibi-values ${analysis}@${version}`);
    }
  }

  /** @type {Record<string, { p: string, m: number, x: string[] }>} */
  const grammars = {};
  for (const [name, [owner, member]] of GRAMMAR_POSITIONS) {
    if (owner === undefined || member === undefined || !table.has(owner)) {
      throw new ShapeError(`${name}: the grammar's component ${String(owner)} no answer reaches`);
    }
    grammars[name] = grammar(object(object(object(components[owner], owner)["properties"], owner)[member], `${owner}.${member}`), `${owner}.${member}`);
  }

  return {
    components: Object.fromEntries([...table].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
    operations,
    values,
    vocabularies: Object.fromEntries([...vocabularies].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
    fallbacks: Object.fromEntries(FAIL_CLOSED),
    grammars,
  };
}

/** The segments' two schemas, exactly as the walk's segment node reads them. @param {JsonObject} components */
function segments(components) {
  const text = JSON.stringify(strip(components["TextSegment"]));
  const data = JSON.stringify(strip(components["DataSegment"]));
  if (text !== JSON.stringify({ additionalProperties: false, properties: { text: { type: "string" } }, required: ["text"], type: "object" })) {
    throw new ShapeError(`TextSegment is not exactly {text}`);
  }
  if (data !== JSON.stringify({ additionalProperties: false, properties: { data: { type: "string" }, truncated: { const: true, type: "boolean" } }, required: ["data"], type: "object" })) {
    throw new ShapeError(`DataSegment is not exactly {data, truncated?: true}`);
  }
}

/** A schema without its annotations, its keys sorted. @param {unknown} value @returns {unknown} */
function strip(value) {
  if (Array.isArray(value)) {
    return value.map(strip);
  }
  if (!isObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.keys(value)
      .filter((key) => !ANNOTATIONS.includes(key) && key !== "maxLength")
      .sort()
      .map((key) => [key, strip(value[key])]),
  );
}

/** An id position's grammar: its pattern, its length in code points and the patterns it may not
 * match. @param {JsonObject} schema @param {string} at */
function grammar(schema, at) {
  const keys = Object.keys(schema).filter((key) => !ANNOTATIONS.includes(key)).sort().join(",");
  const not = schema["not"];
  const refused = isObject(not) && typeof not["pattern"] === "string" && Object.keys(not).length === 1 ? [not["pattern"]] : isObject(not) && Array.isArray(not["anyOf"]) && Object.keys(not).length === 1 ? not["anyOf"].map((arm) => (isObject(arm) && Object.keys(arm).length === 1 ? arm["pattern"] : undefined)) : [undefined];
  if (keys !== "maxLength,not,pattern,type" || schema["type"] !== "string" || typeof schema["pattern"] !== "string" || typeof schema["maxLength"] !== "number" || !refused.every((pattern) => typeof pattern === "string")) {
    throw new ShapeError(`${at}: a grammar is exactly a string's pattern, maxLength and not patterns`);
  }
  return { p: compiled(schema["pattern"], at), m: schema["maxLength"], x: refused.flatMap((pattern) => (typeof pattern === "string" ? [compiled(pattern, at)] : [])) };
}

const HEADER = `/**
 * Generated by \`scripts/generate-types.mjs\` (\`scripts/generate-shapes.mjs\`) from \`schemas/openapi.json\`:
 * do not edit. Run \`npm run generate\`; CI fails while this file differs from what the document gives
 * (D426). The table \`src/api/typed.ts\` walks every answer of a public tool against.
 */
`;

const TYPES = `
/** A constant of the document. */
export type Const = string | number | boolean | null | readonly Const[] | { readonly [key: string]: Const };

/** A node of the table (\`scripts/generate-shapes.mjs\` says what each holds). */
export type Shape =
  | { readonly t: "ref"; readonly n: string }
  | { readonly t: "json" | "any" | "never" | "str" | "num" | "bool" | "null" | "seg" }
  | { readonly t: "enum"; readonly v: readonly string[] }
  | { readonly t: "const"; readonly v: Const }
  | { readonly t: "or" | "all"; readonly o: readonly Shape[] }
  | { readonly t: "arr"; readonly i: Shape; readonly pi?: readonly Shape[] }
  | { readonly t: "rec"; readonly p: { readonly [member: string]: Shape }; readonly r: readonly string[]; readonly open?: true }
  | { readonly t: "map"; readonly v: Shape; readonly pp?: readonly (readonly [string, Shape])[]; readonly k?: Shape; readonly r?: readonly string[] };

/** An id position's grammar: its pattern, its length in code points at most, the patterns it may not match. */
export interface Grammar {
  readonly p: string;
  readonly m: number;
  readonly x: readonly string[];
}

/** A value frozen through and through (no table code reads is written at run time). */
function frozen<T>(value: T): T {
  if (typeof value === "object" && value !== null) {
    for (const member of Object.values(value)) {
      frozen(member);
    }
    Object.freeze(value);
  }
  return value;
}
`;

/**
 * `shapes.ts`'s text.
 * @param {JsonObject} document
 * @param {string} text the document's own text, whose digest the file records
 */
export function renderShapes(document, text) {
  const { components, operations, values, vocabularies, fallbacks, grammars } = shapesOf(document);
  const json = (/** @type {unknown} */ value) => JSON.stringify(value);
  const lines = [
    HEADER,
    TYPES,
    `\n/** The SHA-256 of the \`schemas/openapi.json\` this table was generated from (the types' too: \`openapi.ts\`). */\n`,
    `export const DOCUMENT_DIGEST = ${json(digest(text))};\n`,
    `\n/** Every component an answer reaches, by name. */\n`,
    `export const SHAPES: { readonly [component: string]: Shape } = frozen({\n${Object.entries(components)
      .map(([name, shape]) => `  ${json(name)}: ${json(shape)},\n`)
      .join("")}});\n`,
    `\n/** The component of each typed operation's 200 answer. */\n`,
    `export const OPERATIONS = frozen(${json(operations)} as const);\n`,
    `\n/** The values model of each published analysis and version (D434), by \`<id>@<version>\`. */\n`,
    `export const VALUES = frozen(${json(values)} as const);\n`,
    `\n/** The component of every other answer. */\n`,
    `export const REFUSALS = ${json(REFUSALS)};\n`,
    `\n/** The enum and const vocabularies of the answers, by place (a component, or its member's path). */\n`,
    `export const VOCABULARIES = frozen({\n${Object.entries(vocabularies)
      .map(([name, members]) => `  ${json(name)}: ${json(members)},\n`)
      .join("")}} as const);\n`,
    `\n/** The vocabularies read fail-closed, each as the member given (the walk does not enumerate them). */\n`,
    `export const FALLBACKS = frozen(${json(fallbacks)} as const);\n`,
    `\n/** The grammars of the id positions \`idOf\` and \`choiceFor\` read. */\n`,
    `export const GRAMMARS: { readonly [name in ${Object.keys(grammars).map(json).join(" | ")}]: Grammar } = frozen(${json(grammars)});\n`,
  ];
  return lines.join("");
}

/** The SHA-256 of a text, in hexadecimal. @param {string} text */
export function digest(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}
