/**
 * A request's route (D419): what `routes.ts`, generated from the document, gives for each
 * operation, and the one thing `exchange` sends to. A route is made here alone (`route`, which
 * the generated functions call) and recorded, so that `exchange` refuses an object that merely
 * looks like one (a URL composed elsewhere); its URL is this origin's path, each literal segment
 * percent-encoded by the generator and each parameter by `segment`.
 */
import type { operations } from "./generated/openapi";

export type OperationId = Extract<keyof operations, string>;

export type Method = "GET" | "POST";

export interface Route<O extends OperationId = OperationId> {
  readonly operation: O;
  readonly method: Method;
  readonly url: string;
}

/** The words of a refused parameter: fixed, and never the value. */
export const REFUSED_SEGMENT = "A path parameter is empty, . or .., not well-formed text, or no safe whole number.";

const MADE = new WeakSet<Route>();

/** A route, frozen and recorded. The generated functions alone call it. */
export function route<O extends OperationId>(operation: O, method: Method, url: string): Route<O> {
  const made: Route<O> = Object.freeze({ operation, method, url });
  MADE.add(made);
  return made;
}

/** Whether `route` made this object. */
export function isRoute(value: Route): boolean {
  return MADE.has(value);
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
