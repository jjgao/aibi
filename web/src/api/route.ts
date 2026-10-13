/**
 * A request's route (D419, D425): what `routes.ts`, generated from the document, gives for each
 * operation, and the one thing `exchange` and `send` send to. A route is made here alone (`route`,
 * which the generated functions call), so that the client refuses an object that merely looks like
 * one (a URL composed elsewhere); its URL is this origin's path, each literal segment
 * percent-encoded by the generator and each parameter by `segment`.
 *
 * **No readable URL (D425).** A route shows its operation and its method, and nothing else: the
 * URL and the dataset its path names are held in ES private fields of the class that makes it,
 * which no code outside this module can name (not a property: `Object.keys` gives the two, `"url"
 * in route` is false, `structuredClone` and a spread give a plain object that is no route).
 * `urlOf` and `datasetOf` read them, for `client.ts` alone (the lint's table of the internals of
 * `src/api/`, the import-graph test), so that no component reads a URL's digits back, whatever
 * built it.
 */
import type { operations } from "./generated/openapi";

export type OperationId = Extract<keyof operations, string>;

export type Method = "GET" | "POST";

/** A route as the app sees it: its operation and its method. */
export interface Route<O extends OperationId = OperationId> {
  readonly operation: O;
  readonly method: Method;
}

/** The words of a refused parameter: fixed, and never the value. */
export const REFUSED_SEGMENT = "A path parameter is empty, . or .., not well-formed text, or no safe whole number.";

let urlOfMade: (value: object) => string | null;
let datasetOfMade: (value: object) => string | null;

/** A route made here: its URL and the dataset its path names in private fields. */
class Made<O extends OperationId> implements Route<O> {
  readonly operation: O;
  readonly method: Method;
  readonly #url: string;
  readonly #dataset: string | null;

  constructor(operation: O, method: Method, url: string, dataset: string | null) {
    this.operation = operation;
    this.method = method;
    this.#url = url;
    this.#dataset = dataset;
    Object.freeze(this);
  }

  static {
    urlOfMade = (value) => (#url in value ? value.#url : null);
    datasetOfMade = (value) => (#dataset in value ? value.#dataset : null);
  }
}

Object.freeze(Made.prototype);
Object.freeze(Made);

/** A route, frozen, its URL and its dataset private. The generated functions alone call it;
 * `dataset` is the value of the path's `{dataset}` parameter, `null` for a path without one. */
export function route<O extends OperationId>(operation: O, method: Method, url: string, dataset: string | null = null): Route<O> {
  return new Made(operation, method, url, dataset);
}

/** The URL of a route `route` made; `null` for anything else (the brand check of the private
 * field: an object that merely looks like a route has none). `client.ts`'s alone. */
export function urlOf(value: unknown): string | null {
  return typeof value === "object" && value !== null ? urlOfMade(value) : null;
}

/** The dataset a route's path names, `null` for a path without one or anything `route` did not
 * make. `client.ts`'s alone: what it records an answer's provenance under (D425). */
export function datasetOf(value: unknown): string | null {
  return typeof value === "object" && value !== null ? datasetOfMade(value) : null;
}

/** A whole number's decimal digits: a safe, non-negative integer alone (the document caps every
 * request integer at 2^53 - 1). */
function decimal(value: number): string {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new RangeError(REFUSED_SEGMENT);
  }
  return String(value);
}

/** One path segment: a string percent-encoded whole (`/`, `?`, `#` and `%` included), refused if
 * it is empty, `.` or `..`, which a URL parser would remove or climb, or if it is not
 * well-formed text (a lone surrogate, which the engine's own `URIError` would report in words of
 * its own); a number as its digits. */
export function segment(value: string | number): string {
  if (typeof value === "number") {
    return decimal(value);
  }
  if (value === "" || value === "." || value === "..") {
    throw new RangeError(REFUSED_SEGMENT);
  }
  try {
    return encodeURIComponent(value);
  } catch {
    throw new RangeError(REFUSED_SEGMENT);
  }
}

/** A query string from its entries in order, those without a value left out; `""` if none. */
export function search(entries: readonly (readonly [string, string | number | undefined])[]): string {
  const query = new URLSearchParams();
  for (const [name, value] of entries) {
    if (value !== undefined) {
      query.append(name, typeof value === "number" ? decimal(value) : value);
    }
  }
  const text = query.toString();
  return text === "" ? "" : `?${text}`;
}
