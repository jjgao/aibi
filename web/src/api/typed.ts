/**
 * The typed answers (D426): the one seam through which the web reads a public tool's answer. Each
 * tool the web calls has one function here (`searchCatalog`, `describeDataset`, `listAnalyses`,
 * `validateDocument`, `countCohort`, `runAnalysis`, `explain`), which takes the operation's request
 * body as the generated types state it (`RequestBody<O>`: a request's strings and JS numbers, which
 * no box is assignable to), builds the request inside `src/api/` (the route is the generated one,
 * the body is encoded by `client.ts`), and gives a closed outcome:
 *
 * - `{kind: "ok", value}`: a 200 whose body is the operation's component, walked;
 * - `{kind: "refused", status, refusals, retryAfter}`: a well-formed `{refusals}` body, with at least
 *   one refusal (a refusal with nothing in it is no reason to show), at any status of 400 or more (the status the server's, D265; `retryAfter` the `Retry-After` header's whole
 *   seconds, which a 429 and a 503 carry, or `null`), walked like an answer;
 * - `{kind: "invalid", cause}`: anything else (another status, a body of another shape at any depth,
 *   a body that is no JSON, too large or not UTF-8, a `fetch` that failed or was aborted, a body cut
 *   off). It carries nothing of the answer (A6) but its `cause`, one of two words of a closed
 *   vocabulary: `shape` (the page says `SHAPE_MISMATCH`) when the server answered and the answer is
 *   not one the page reads, `unreachable` (`UNREACHABLE`) when no answer arrived (the `fetch` failed
 *   or was aborted, or the body was cut off). Both are fixed words.
 *
 * The three that take a document (`validateDocument`, `countCohort`, `runAnalysis`) are not the
 * index's (D432): `document.ts` alone imports them (a lint row, the import-graph test), and sends
 * through them the document it writes from a `Draft`, so a screen hands over choices and typed
 * values, never a document.
 *
 * A request this client refuses before sending (`ClientError`'s `notARoute`, `notFinite`: the
 * caller's own fault) is thrown, not an outcome.
 *
 * **The walk.** `exchange` reads the body through the one decoder (every number a `ServerNumber`,
 * D419); the walk then validates it against the table generated from the same document
 * (`generated/shapes.ts`, `scripts/generate-shapes.mjs`) at full depth and rebuilds it, frozen,
 * with **every string a `ServerText`** (an enum's and a const's included: a vocabulary member is
 * read through `kindOf`), **every map whose keys are data a `BoxedMap`** with boxed keys (read
 * through `entriesOf`), every `x-aibi-json` value walked the same way (strings boxed, objects
 * `BoxedMap`s), and every segment normalised to `{kind: "text", text}` or `{kind: "data", data,
 * truncated}` from exactly `{text}` or `{data}` / `{data, truncated: true}` (both keys, another
 * key, a non-string, `truncated: false` and `__proto__` are `invalid`, D399). A miss anywhere (a
 * member renamed, an enum's value changed, a required member dropped, a key a closed record does
 * not have, a wrong JSON type, a key its `propertyNames` refuses) is `invalid`. The walk uses no
 * `Function` and no `eval` (the policy, D411): the table is data, read by this module's own code.
 *
 * **What the walk does not check** (the residuals, named in the generator and in D426): `if` /
 * `then` / `else` (a `null` estimate and its `not_estimable` reason, a caveat's code and its
 * severity), `not`, `contains`, `pattern` as a validator, the bounds (lengths, counts, numbers), a
 * numeric `const`'s value (the walk never reads a number), `oneOf`'s exclusivity and the inside of
 * an `x-aibi-json` value (a document, a descriptor), which the page shows only as a generic tree,
 * never by member name. A result's `values` is the exception (D434): `valuesOf`, below, reads the
 * values of an analysis and version the document publishes a model for, and nothing else. A stale tab that meets a server whose answers keep their
 * names and change their meaning is not detected (the digest handshake is a follow-up).
 *
 * **The values seam (D434).** When a `run_analysis` answer walks, each result's raw
 * `derivation.analysis` picks a `VALUES` entry (`<id>@<version>`, generated from the document's
 * `x-aibi-values`); the raw `values` is walked by **the same walk** with that component, and the
 * outcome is kept in a module `WeakMap` keyed by the walked result. `valuesOf(result)` gives it, or
 * throws `ClientError("notAResult")` for a result the walk did not make (a copy, a composed object).
 * What it gives (`ValuesRead`): the labels, the canonical document's
 * `view.params.columns[j].column` read by name, whose count every position's columns must have;
 * `variables`, `"labelled"` when every analysed count's `variables` is absent with one column or
 * has the labels' count, `"unlabelled"` otherwise; `view: "none"` when the model's `view` is a
 * reference to a map of no entries in the generated table (never from the analysis id); and each
 * column by its own `kind`: `numbers` and `categories` walked again by their own component, any
 * other by its `kind` alone. Another analysis or version is `unread/analysis`; values or labels of
 * another shape `unread/shape`. It reveals the answer's own boxes regrouped and two page words: no
 * text and no number.
 *
 * **The one unchecked step.** The walked value becomes the operation's `Boxed` type by a type
 * predicate (`isAnswered`, `isRefusals`; for a result's values `isValues` and `isComponent`), which the compiler takes on trust: the table and the types are generated
 * from one `schemas/openapi.json` (`DOCUMENT_DIGEST`, which a test holds to the document), a gate
 * holds the table and the types to each other node by node (a map of the table is a `BoxedMap`, a
 * string a `ServerText`, a record has the table's members, each optional exactly when the table does
 * not require it: `tests/gates/takers.ts`' `conformance`), the table is held to an independent
 * validator of the document (`tests/unit/typed-oracle.test.ts`), and the golden envelopes and the
 * orchard's answers are walked, held to their `Boxed` types and scanned for a raw string by the tests.
 */
import { boxMap, boxText, type BoxedMap, isServerNumber, type ServerNumber, type ServerText } from "./box";
import { type Answer, ClientError, exchange, noAnswer, type RequestBody } from "./client";
import type { components, operations } from "./generated/openapi";
import * as generated from "./generated/routes";
import { type Const, OPERATIONS, REFUSALS, type Shape, SHAPES, VALUES } from "./generated/shapes";
import type { JsonOut } from "./json";

type Schemas = components["schemas"];

/** A segment, normalised: server words, or data. */
export type BoxedSegment = { readonly kind: "text"; readonly text: ServerText } | { readonly kind: "data"; readonly data: ServerText; readonly truncated: boolean };

/** An `x-aibi-json` value as the walk gives it: every string boxed, every object a `BoxedMap`. */
export type BoxedJson = ServerText | ServerNumber | boolean | null | readonly BoxedJson[] | BoxedMap<BoxedJson>;

/** A generated response type as the walk gives it: every string a `ServerText`, every map a
 * `BoxedMap`, every segment normalised, every `JsonOut` a `BoxedJson` (a type, not a second run of
 * the generator; `tests/gates/api-graph.test.ts` holds that no component's holds a `string`). */
export type Boxed<T> = [T] extends [JsonOut] ? ([JsonOut] extends [T] ? BoxedJson : Each<T>) : Each<T>;
type Each<T> = T extends ServerNumber | boolean | null | undefined
  ? T
  : T extends string
    ? ServerText
    : T extends number
      ? ServerNumber
      : T extends readonly (infer U)[]
        ? readonly Boxed<U>[]
        : Same<keyof T, keyof Schemas["TextSegment"]> extends true
          ? Extract<BoxedSegment, { readonly kind: "text" }>
          : Same<keyof T, keyof Schemas["DataSegment"]> extends true
            ? Extract<BoxedSegment, { readonly kind: "data" }>
            : T extends { readonly [key: string]: infer V }
              ? string extends keyof T
                ? BoxedMap<Boxed<V>>
                : { readonly [K in keyof T]: Boxed<T[K]> }
              : { readonly [K in keyof T]: Boxed<T[K]> };
/** Whether two key sets are one (a segment is told by its exact keys: `Data` is `{data}`, a
 * `DataSegment` `{data, truncated?}`, and each is assignable to the other). */
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

/** The public tools the web calls. */
export type TypedOperation = keyof typeof OPERATIONS;

/** An operation's 200 answer, walked. */
export type Answered<O extends TypedOperation> = Boxed<operations[O]["responses"][200]["content"]["application/json"]>;

/** A refusal list, walked. */
export type BoxedRefusals = Boxed<Schemas["Refusals"]>["refusals"];

/** What a typed function gives: the answer, the refusals, or nothing this page can read. */
export type Outcome<T> =
  | { readonly kind: "ok"; readonly value: T }
  | { readonly kind: "refused"; readonly status: number; readonly refusals: BoxedRefusals; readonly retryAfter: number | null }
  | { readonly kind: "invalid"; readonly cause: InvalidCause };

/** Why an outcome is `invalid`: the server answered what the page does not read (`shape`), or no
 * answer arrived (`unreachable`). */
export type InvalidCause = "shape" | "unreachable";

/** The words of an `invalid` outcome of the cause `shape`: fixed, and never any of the answer. */
export const SHAPE_MISMATCH = "The page and the server disagree about the data's shape; reload.";

/** The words of an `invalid` outcome of the cause `unreachable`: fixed. */
export const UNREACHABLE = "The page could not reach the server; check the connection and try again.";

const INVALID_SHAPE = Object.freeze({ kind: "invalid", cause: "shape" } as const);

const INVALID_UNREACHABLE = Object.freeze({ kind: "invalid", cause: "unreachable" } as const);

/** The walk's refusal: a miss somewhere in the answer. */
const INVALID: unique symbol = Symbol("aibi.invalid");

type Walked = unknown;

/** A decoded object: no list, no box (the decoder makes nothing else that is an object). */
function isPlain(value: unknown): value is { readonly [member: string]: unknown } {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !isServerNumber(value);
}

/** The compiled patterns of the table's maps, by their text. */
const PATTERNS = new Map<string, RegExp>();

function pattern(text: string): RegExp {
  const found = PATTERNS.get(text) ?? new RegExp(text, "u");
  PATTERNS.set(text, found);
  return found;
}

/** Any JSON value of an `x-aibi-json` position, or a position the document leaves open: its strings
 * boxed, its objects `BoxedMap`s, its lists frozen. */
function walkJson(value: unknown): Walked {
  if (typeof value === "string") {
    return boxText(value);
  }
  if (value === null || typeof value === "boolean" || isServerNumber(value)) {
    return value;
  }
  if (Array.isArray(value)) {
    const out: Walked[] = [];
    for (const item of value) {
      const walked = walkJson(item);
      if (walked === INVALID) {
        return INVALID;
      }
      out.push(walked);
    }
    return Object.freeze(out);
  }
  if (isPlain(value)) {
    const out: (readonly [ServerText, Walked])[] = [];
    for (const [key, member] of Object.entries(value)) {
      const walked = walkJson(member);
      if (walked === INVALID) {
        return INVALID;
      }
      out.push([boxText(key), walked]);
    }
    return boxMap(out);
  }
  return INVALID;
}

/** Whether a constant is a list (`Array.isArray` narrows no read-only list). */
function isList(constant: Const): constant is readonly Const[] {
  return Array.isArray(constant);
}

/** Whether a decoded value is a constant of the document: strings, booleans and `null` compared,
 * a number any box (the walk never reads a number: a residual), lists and objects member by member. */
function isConstant(constant: Const, value: unknown): boolean {
  if (typeof constant === "number") {
    return isServerNumber(value);
  }
  if (constant === null || typeof constant !== "object") {
    return value === constant;
  }
  if (isList(constant)) {
    return Array.isArray(value) && value.length === constant.length && constant.every((member, index) => isConstant(member, value[index]));
  }
  if (!isPlain(value)) {
    return false;
  }
  const keys = Object.keys(constant);
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key) && isConstant(constant[key] ?? null, value[key]));
}

/** A segment: exactly `{text}` (a string), or `{data}` or `{data, truncated: true}` (a string). */
function walkSegment(value: unknown): Walked {
  if (!isPlain(value)) {
    return INVALID;
  }
  const keys = Object.keys(value);
  const text = value["text"];
  if (keys.length === 1 && keys[0] === "text" && typeof text === "string") {
    return Object.freeze({ kind: "text", text: boxText(text) });
  }
  const data = value["data"];
  const truncated = Object.hasOwn(value, "truncated");
  if (keys.length === (truncated ? 2 : 1) && typeof data === "string" && Object.hasOwn(value, "data") && (!truncated || value["truncated"] === true)) {
    return Object.freeze({ kind: "data", data: boxText(data), truncated });
  }
  return INVALID;
}

/** A record: a decoded object with the required members, each member walked by its shape; a key
 * the record has no member for is a miss (an open record, a check alone, walks it as any JSON and
 * leaves it out of what it gives). */
function walkRecord(shape: Extract<Shape, { readonly t: "rec" }>, value: unknown): Walked {
  if (!isPlain(value) || !shape.r.every((member) => Object.hasOwn(value, member))) {
    return INVALID;
  }
  const out: [string, Walked][] = [];
  for (const [key, member] of Object.entries(value)) {
    const known = Object.hasOwn(shape.p, key) ? shape.p[key] : undefined;
    const walked = known === undefined ? (shape.open === true ? walkJson(member) : INVALID) : walk(known, member);
    if (walked === INVALID) {
      return INVALID;
    }
    if (known !== undefined) {
      out.push([key, walked]);
    }
  }
  return Object.freeze(Object.fromEntries(out));
}

/** A map: the required keys present (the counts by reason: a reason dropped is a miss, never a
 * smaller count), every key checked by `propertyNames`, every value by the first pattern that matches its
 * key (and every other that does), or by the map's values' shape; rebuilt with boxed keys. */
function walkMap(shape: Extract<Shape, { readonly t: "map" }>, value: unknown): Walked {
  if (!isPlain(value) || !(shape.r ?? []).every((key) => Object.hasOwn(value, key))) {
    return INVALID;
  }
  const out: (readonly [ServerText, Walked])[] = [];
  for (const [key, member] of Object.entries(value)) {
    if (shape.k !== undefined && walk(shape.k, key) === INVALID) {
      return INVALID;
    }
    const matched = (shape.pp ?? []).filter(([text]) => pattern(text).test(key)).map(([, found]) => found);
    let walked: Walked = INVALID;
    for (const [index, found] of (matched.length === 0 ? [shape.v] : matched).entries()) {
      const one = walk(found, member);
      if (one === INVALID) {
        return INVALID;
      }
      walked = index === 0 ? one : walked;
    }
    out.push([boxText(key), walked]);
  }
  return boxMap(out);
}

/** A list: each item by its `prefixItems` shape, then by `items`. */
function walkList(shape: Extract<Shape, { readonly t: "arr" }>, value: unknown): Walked {
  if (!Array.isArray(value)) {
    return INVALID;
  }
  const out: Walked[] = [];
  for (const [index, item] of value.entries()) {
    const walked = walk(shape.pi?.[index] ?? shape.i, item);
    if (walked === INVALID) {
      return INVALID;
    }
    out.push(walked);
  }
  return Object.freeze(out);
}

/** A decoded value walked by its shape: what it gives, boxed and frozen, or `INVALID`. */
function walk(shape: Shape, value: unknown): Walked {
  switch (shape.t) {
    case "ref": {
      const found = Object.hasOwn(SHAPES, shape.n) ? SHAPES[shape.n] : undefined;
      return found === undefined ? INVALID : walk(found, value);
    }
    case "json":
    case "any":
      return walkJson(value);
    case "never":
      return INVALID;
    case "str":
      return typeof value === "string" ? boxText(value) : INVALID;
    case "num":
      return isServerNumber(value) ? value : INVALID;
    case "bool":
      return typeof value === "boolean" ? value : INVALID;
    case "null":
      return value === null ? value : INVALID;
    case "enum":
      return typeof value === "string" && shape.v.includes(value) ? boxText(value) : INVALID;
    case "const":
      return isConstant(shape.v, value) ? walkJson(value) : INVALID;
    case "seg":
      return walkSegment(value);
    case "or":
      for (const arm of shape.o) {
        const walked = walk(arm, value);
        if (walked !== INVALID) {
          return walked;
        }
      }
      return INVALID;
    case "all": {
      const [first, ...checks] = shape.o;
      const walked = first === undefined ? INVALID : walk(first, value);
      return walked === INVALID || checks.some((check) => walk(check, value) === INVALID) ? INVALID : walked;
    }
    case "arr":
      return walkList(shape, value);
    case "rec":
      return walkRecord(shape, value);
    case "map":
      return walkMap(shape, value);
  }
}

/** A decoded body walked as a component of the table (or as a shape of it, for the tests' property
 * test): `{ok: true, value}` or `{ok: false}` (a miss, an unknown component, a walk too deep for the
 * stack). The tests' door to the walk; the index does not export it, and the lint lets no module of
 * `src/` import it. */
export function walkComponent(component: string | Shape, body: unknown): { readonly ok: true; readonly value: unknown } | { readonly ok: false } {
  try {
    const walked = walk(typeof component === "string" ? { t: "ref", n: component } : component, body);
    return walked === INVALID ? { ok: false } : { ok: true, value: walked };
  } catch {
    return { ok: false };
  }
}

/** The seam's one unchecked step, in its two forms: the walked value as the operation's `Boxed`
 * type, or as the refusals', which the compiler takes on trust (the table and the types are one
 * document's: `DOCUMENT_DIGEST`). */
function isAnswered<O extends TypedOperation>(_operation: O, value: unknown): value is Answered<O> {
  return value !== INVALID;
}

function isRefusals(value: unknown): value is Boxed<Schemas["Refusals"]> {
  return value !== INVALID;
}

/** Every `ok` value the walk gave, with its operation (D432): `optionsOf` mints choices from a
 * `describe_dataset` answer this seam returned alone, so a composed object (a spread, a copy, a
 * `Proxy`, another operation's answer cast) mints nothing. A module `WeakMap`: a patched
 * `WeakMap.prototype.get` is deliberate code, D419's boundary. */
const WALKED = new WeakMap<object, TypedOperation>();

/** `document.ts`'s alone (D432): the operation whose answer this seam walked into `value`, or
 * `undefined` for any other value. */
export function walkedAs(value: object): TypedOperation | undefined {
  return WALKED.get(value);
}

/** A column of one position's values (D434), by the server's own `kind`: a `numbers` arm and a
 * `categories` arm as walked, and any other arm (`category_rows`, `number_rows`, `memberships`) by
 * its `kind` alone, which the page names and never drops. */
export type ColumnRead =
  | { readonly kind: "numbers"; readonly value: Boxed<Schemas["NumberDistribution"]> }
  | { readonly kind: "categories"; readonly value: Boxed<Schemas["CategoryDistribution"]> }
  | { readonly kind: "other"; readonly name: ServerText };

/** A result's `values`, read (D434): the analysis and version the read is of, the view's column
 * labels (the canonical document's `view.params.columns[j].column`, in order), whether the analysed
 * counts' `variables` line up with those labels, the view's values as a whole (`"none"`: the model's
 * `view` is a map of no entries in the generated table) and, per position, its columns; or `unread`,
 * with its cause (`analysis`: this page reads no values of this analysis and version; `shape`: the
 * values, or their labels, are not of the shape the page reads). */
export type ValuesRead =
  | {
      readonly kind: "summary.distribution";
      readonly columns: readonly ServerText[];
      readonly variables: "labelled" | "unlabelled";
      readonly view: "none";
      readonly positions: readonly { readonly columns: readonly ColumnRead[] }[];
    }
  | { readonly kind: "unread"; readonly cause: "analysis" | "shape" };

/** An analysis's values model, by `<id>@<version>` (`VALUES`, generated from the document's
 * `x-aibi-values`), and the component that models them. */
type ValuesKey = keyof typeof VALUES;

/** The page's reader of each published values model, by `<id>@<version>`: a key the document
 * publishes that has no reader fails to compile, and a result of any other analysis or version is
 * `unread/analysis`. */
const READERS: { readonly [K in ValuesKey]: (values: Boxed<Schemas[(typeof VALUES)[K]]>, raw: Raw, labels: readonly ServerText[], key: K) => ValuesRead } = {
  "summary.distribution@1.0.0": readDistribution,
};

const UNREAD_ANALYSIS: ValuesRead = Object.freeze({ kind: "unread", cause: "analysis" });

const UNREAD_SHAPE: ValuesRead = Object.freeze({ kind: "unread", cause: "shape" });

/** A value of the decoded body the walk read (every number a `ServerNumber`, every string raw). */
type Raw = unknown;

/** A decoded object's own member, or `undefined` (no member, or no object). */
function member(value: Raw, name: string): Raw {
  return isPlain(value) && Object.hasOwn(value, name) ? value[name] : undefined;
}

/** A decoded list's items, or `undefined`. */
function items(value: Raw): readonly Raw[] | undefined {
  return isItems(value) ? value : undefined;
}

function isItems(value: Raw): value is readonly Raw[] {
  return Array.isArray(value);
}

/** Whether a key is one the document publishes a values model for and the page has a reader of. */
function isValuesKey(key: string): key is ValuesKey {
  return Object.hasOwn(READERS, key) && Object.hasOwn(VALUES, key);
}

/** The second unchecked step (D434), as `isAnswered`: a values model walked by the component
 * `VALUES` names for its key, as that component's `Boxed` type. */
function isValues<K extends ValuesKey>(_key: K, value: unknown): value is Boxed<Schemas[(typeof VALUES)[K]]> {
  return value !== INVALID;
}

/** The same step for one arm of a position's columns, walked again by its own component (the arm
 * the column's `kind` names; the walk of the whole values took the first arm that matched). */
function isComponent<C extends "NumberDistribution" | "CategoryDistribution">(_component: C, value: unknown): value is Boxed<Schemas[C]> {
  return value !== INVALID;
}

/** The two arms the page reads, by their `kind` consts (the generated types': a renamed const fails
 * to compile here). */
const NUMBERS: Schemas["NumberDistribution"]["kind"] = "numbers";
const CATEGORIES: Schemas["CategoryDistribution"]["kind"] = "categories";

/** Whether a shape is a reference to a component that is a map of no entries (the generator's
 * reading of an empty closed object, `NoViewValues`). */
function isEmptyMap(shape: Shape | undefined): boolean {
  const found = shape?.t === "ref" && Object.hasOwn(SHAPES, shape.n) ? SHAPES[shape.n] : undefined;
  return found?.t === "map" && found.v.t === "never" && found.k === undefined && (found.pp ?? []).length === 0 && (found.r ?? []).length === 0;
}

/** `summary.distribution`'s values, read: the view's values as a whole must be a map of no entries
 * by the generated table, read from the model of the result's own key (never by the analysis id;
 * while `VALUES` holds one key, reading another key's model is caught by `noUnusedParameters` alone), every position must hold as many columns as
 * the labels, and each column's arm is its own `kind`. */
function readDistribution(values: Boxed<Schemas["DistributionValues"]>, raw: Raw, labels: readonly ServerText[], key: ValuesKey): ValuesRead {
  const model = SHAPES[VALUES[key]];
  if (model?.t !== "rec" || !isEmptyMap(model.p["view"])) {
    return UNREAD_ANALYSIS;
  }
  const rawPositions = items(member(member(raw, "values"), "positions")) ?? [];
  if (rawPositions.length !== values.positions.length) {
    return UNREAD_SHAPE;
  }
  const positions: { readonly columns: readonly ColumnRead[] }[] = [];
  for (const [index, position] of values.positions.entries()) {
    const rawColumns = items(member(rawPositions[index], "columns")) ?? [];
    if (position.columns.length !== labels.length || rawColumns.length !== labels.length) {
      return UNREAD_SHAPE;
    }
    const columns: ColumnRead[] = [];
    for (const [at, column] of position.columns.entries()) {
      const rawColumn = rawColumns[at];
      const kind = member(rawColumn, "kind");
      if (kind === NUMBERS) {
        const walked = walk({ t: "ref", n: "NumberDistribution" }, rawColumn);
        if (!isComponent("NumberDistribution", walked)) {
          return UNREAD_SHAPE;
        }
        columns.push(Object.freeze({ kind: "numbers", value: walked }));
      } else if (kind === CATEGORIES) {
        const walked = walk({ t: "ref", n: "CategoryDistribution" }, rawColumn);
        if (!isComponent("CategoryDistribution", walked)) {
          return UNREAD_SHAPE;
        }
        columns.push(Object.freeze({ kind: "categories", value: walked }));
      } else if (typeof kind === "string") {
        columns.push(Object.freeze({ kind: "other", name: column.kind }));
      } else {
        return UNREAD_SHAPE;
      }
    }
    positions.push(Object.freeze({ columns: Object.freeze(columns) }));
  }
  const analysed = items(member(raw, "analysed")) ?? [];
  const labelled = analysed.every((entry) => {
    const variables = member(entry, "variables");
    return variables === undefined ? labels.length === 1 : items(variables)?.length === labels.length;
  });
  return Object.freeze({ kind: "summary.distribution", columns: labels, variables: labelled ? "labelled" : "unlabelled", view: "none", positions: Object.freeze(positions) });
}

/** One result's values, read from its raw envelope (the decoded body the walk read): its analysis
 * and version pick a reader, the raw `values` is walked by **the same `walk`** with the component
 * `VALUES` names, and the labels are read by name from the canonical document. */
function readValues(raw: Raw): ValuesRead {
  const analysis = member(member(raw, "derivation"), "analysis");
  const id = member(analysis, "id");
  const version = member(analysis, "version");
  if (typeof id !== "string" || typeof version !== "string") {
    return UNREAD_SHAPE;
  }
  const known = `${id}@${version}`;
  if (!isValuesKey(known)) {
    return UNREAD_ANALYSIS;
  }
  const columns = items(member(member(member(member(member(raw, "derivation"), "document"), "view"), "params"), "columns"));
  const labels: ServerText[] = [];
  for (const column of columns ?? []) {
    const name = member(column, "column");
    if (typeof name !== "string") {
      return UNREAD_SHAPE;
    }
    labels.push(boxText(name));
  }
  if (columns === undefined || labels.length === 0) {
    return UNREAD_SHAPE;
  }
  try {
    const walked = walk({ t: "ref", n: VALUES[known] }, member(raw, "values"));
    return isValues(known, walked) ? READERS[known](walked, raw, Object.freeze(labels), known) : UNREAD_SHAPE;
  } catch {
    // Defensive, as `walkComponent` is: a walk that throws reads nothing.
    return UNREAD_SHAPE;
  }
}

/** Each result a `run_analysis` walk made, with its values read (D434): a module `WeakMap`, as
 * `WALKED`, so a composed or copied result reads nothing. */
const VALUES_READ = new WeakMap<object, ValuesRead>();

/** Reads the values of every result of a walked `run_analysis` answer, keyed by the walked result. */
function readAll(raw: Raw, walked: unknown): void {
  const rawResults = items(member(raw, "results")) ?? [];
  const results = items(member(walked, "results")) ?? [];
  for (const [index, result] of results.entries()) {
    if (typeof result === "object" && result !== null) {
      VALUES_READ.set(result, index < rawResults.length ? readValues(rawResults[index]) : UNREAD_SHAPE);
    }
  }
}

/** A result's values, read (D434): `result` must be one of the results the typed answers gave (a
 * `run_analysis` answer this seam walked), or this throws `ClientError("notAResult")`. Its values
 * are walked by the published model of its analysis and version (`VALUES`) with the walk that read
 * the answer; another analysis or version is `unread/analysis`, and values or labels of another
 * shape `unread/shape`. */
export function valuesOf(result: Answered<"run_analysis">["results"][number]): ValuesRead {
  const found = VALUES_READ.get(result);
  if (found === undefined) {
    throw new ClientError("notAResult");
  }
  return found;
}

/** The refusals of the client's own that are the caller's fault, thrown: the rest of what a request
 * meets (no answer, an unreadable one) is an `invalid` outcome. */
const THROWN = new Set(["notARoute", "notFinite"]);

/** An operation's outcome, from the answer `send` gets. */
async function asked<O extends TypedOperation>(operation: O, send: () => Promise<Answer<O>>): Promise<Outcome<Answered<O>>> {
  let answer: Answer<O>;
  try {
    answer = await send();
  } catch (error) {
    if (error instanceof ClientError && THROWN.has(error.refusal)) {
      throw error;
    }
    // No answer came (a `fetch` that failed or was aborted, a body cut off), or the server answered
    // what this client does not read.
    return noAnswer(error) ? INVALID_UNREACHABLE : INVALID_SHAPE;
  }
  if (answer.status === 200) {
    const walked = walkComponent(OPERATIONS[operation], answer.body);
    if (walked.ok && isAnswered(operation, walked.value)) {
      // Every component an operation answers is an object (the walk froze it).
      if (typeof walked.value === "object" && walked.value !== null) {
        WALKED.set(walked.value, operation);
        if (operation === "run_analysis") {
          readAll(answer.body, walked.value);
        }
      }
      return Object.freeze({ kind: "ok", value: walked.value });
    }
    return INVALID_SHAPE;
  }
  if (answer.status >= 400) {
    const walked = walkComponent(REFUSALS, answer.body);
    // `Refusals.refusals` has `minItems: 1`, a bound the walk does not read, but one that decides
    // this outcome's kind: a refusal with nothing in it is no `refused`.
    if (walked.ok && isRefusals(walked.value) && walked.value.refusals.length > 0) {
      return Object.freeze({ kind: "refused", status: answer.status, refusals: walked.value.refusals, retryAfter: answer.retryAfter });
    }
  }
  return INVALID_SHAPE;
}

/** `search_catalog` (D274). */
export function searchCatalog(body: RequestBody<"search_catalog">): Promise<Outcome<Answered<"search_catalog">>> {
  return asked("search_catalog", () => exchange(generated.search_catalog(), body));
}

/** `describe_dataset`. */
export function describeDataset(body: RequestBody<"describe_dataset">): Promise<Outcome<Answered<"describe_dataset">>> {
  return asked("describe_dataset", () => exchange(generated.describe_dataset(), body));
}

/** `list_analyses` (D316, D417). */
export function listAnalyses(body: RequestBody<"list_analyses">): Promise<Outcome<Answered<"list_analyses">>> {
  return asked("list_analyses", () => exchange(generated.list_analyses(), body));
}

/** `validate_document`: a 200 whether the document is valid or not (`valid`, `refusals`). */
export function validateDocument(body: RequestBody<"validate_document">): Promise<Outcome<Answered<"validate_document">>> {
  return asked("validate_document", () => exchange(generated.validate_document(), body));
}

/** `count_cohort`. */
export function countCohort(body: RequestBody<"count_cohort">): Promise<Outcome<Answered<"count_cohort">>> {
  return asked("count_cohort", () => exchange(generated.count_cohort(), body));
}

/** `run_analysis`. */
export function runAnalysis(body: RequestBody<"run_analysis">): Promise<Outcome<Answered<"run_analysis">>> {
  return asked("run_analysis", () => exchange(generated.run_analysis(), body));
}

/** `explain`. */
export function explain(body: RequestBody<"explain">): Promise<Outcome<Answered<"explain">>> {
  return asked("explain", () => exchange(generated.explain(), body));
}
