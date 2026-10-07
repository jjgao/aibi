/**
 * The one decoder (D419): every response body the app reads is parsed here, by `JSON.parse` with
 * a reviver that boxes each number from its source text (`context.source`, JSON source text
 * access: Chromium 114 and later, Node 21 and later). A number whose source the parser does not
 * give (`context.source` undefined: a reviver whose holder was changed under it gets no source,
 * and a browser without the feature gives none at all) is refused, never rebuilt from the JS
 * number, which has already lost digits (`9007199254740993`) or text (`1.10`, `-0`). Every object
 * and array is frozen as it is made. A refusal's message is fixed and never quotes the body (the
 * engine's own `SyntaxError` does, so it is replaced, without a cause).
 *
 * `decode` is `client.ts`'s alone (the lint, the import-graph test and the build's gate hold it);
 * `decodedCount` counts every call, the run-time oracle's counter, which `oracle.ts` exports and
 * the end-to-end harness alone imports.
 */
import { box } from "./box";
import type { JsonOut } from "./json";

/** The words of a refused body: fixed, and never any of its text. */
export const UNDECODABLE = "The server's answer is not JSON this client can read exactly.";

export class DecodeError extends Error {
  constructor() {
    super(UNDECODABLE);
    this.name = "DecodeError";
  }
}

/** What JSON source text access gives a reviver beside the key and the value. */
interface Context {
  readonly source?: unknown;
}

type Reviver = (this: unknown, key: string, value: unknown, context?: Context) => unknown;

const parse = JSON.parse as (text: string, reviver: Reviver) => unknown;

/** The reviver: a number becomes the box of its source text, which must be given; an object or
 * an array is frozen; anything else is kept. */
export function revive(this: unknown, _key: string, value: unknown, context?: Context): unknown {
  if (typeof value === "number") {
    const source = context?.source;
    if (typeof source !== "string") {
      throw new DecodeError();
    }
    return box(source);
  }
  if (typeof value === "object" && value !== null) {
    return Object.freeze(value);
  }
  return value;
}

let decoded = 0;

/** A response body's value, its numbers boxed and its objects and arrays frozen. */
export function decode(text: string): JsonOut {
  decoded += 1;
  try {
    return parse(text, revive) as JsonOut;
  } catch (error) {
    if (error instanceof DecodeError) {
      throw error;
    }
    throw new DecodeError();
  }
}

/** How many bodies `decode` was given since the page loaded (the oracle's counter). */
export function decodedCount(): number {
  return decoded;
}

/** Whether this engine gives a reviver the source text of a number: without it the client cannot
 * read a server number exactly, and the app refuses to start. */
export function sourceTextSupported(): boolean {
  let seen: unknown = undefined;
  try {
    parse("1.10", function (this: unknown, _key, value, context) {
      seen = context?.source;
      return value;
    });
  } catch {
    return false;
  }
  return seen === "1.10";
}
