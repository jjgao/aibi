/**
 * The one decoder (D419): every response body the app reads is parsed here, by `JSON.parse` with
 * a reviver that boxes each number from its source text (`context.source`, JSON source text
 * access: Chromium 114 and later, Node 21 and later). A number whose source the parser does not
 * give (`context.source` undefined: a reviver whose holder was changed under it gets no source,
 * and a browser without the feature gives none at all) is refused, never rebuilt from the JS
 * number, which has already lost digits (`9007199254740993`) or text (`1.10`, `-0`). Every object
 * and array is frozen before `decode` returns it. A refusal's message is fixed and never quotes the body (the
 * engine's own `SyntaxError` does, so it is replaced, without a cause).
 *
 * **Before freezing (D425).** `decode` holds every object and array it makes unfrozen until the
 * parse is done, calls `beforeFreeze` (if given) with the root, and then freezes them all: the
 * one place where a node can be stamped with its provenance (`origin.ts`'s `stamp`, which refuses a
 * frozen node), so that the stamp never relies on adding a private field to a frozen object.
 * `decode` itself knows no operation: the hook is `client.ts`'s, from `provenance.ts`'s `recorder`.
 * A hook that throws throws out of `decode` as it is, and nothing is frozen or returned.
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

/** The reviver's step for a leaf: a number becomes the box of its source text, which must be
 * given; anything else is kept. (`decode` holds every object and array itself, to freeze them
 * after the hook, and never gives one to this.) */
export function revive(this: unknown, _key: string, value: unknown, context?: Context): unknown {
  if (typeof value === "number") {
    const source = context?.source;
    if (typeof source !== "string") {
      throw new DecodeError();
    }
    return box(source);
  }
  return value;
}

let decoded = 0;

/** A response body's value, its numbers boxed and its objects and arrays frozen; `beforeFreeze`,
 * if given, is called with the root while every object and array is still unfrozen. */
export function decode(text: string, beforeFreeze?: (root: JsonOut) => void): JsonOut {
  decoded += 1;
  const made: object[] = [];
  let root: JsonOut;
  try {
    root = parse(text, function (this: unknown, key: string, value: unknown, context?: Context): unknown {
      if (typeof value === "object" && value !== null) {
        made.push(value);
        return value;
      }
      return revive.call(this, key, value, context);
    }) as JsonOut;
  } catch (error) {
    if (error instanceof DecodeError) {
      throw error;
    }
    throw new DecodeError();
  }
  beforeFreeze?.(root);
  for (const value of made) {
    Object.freeze(value);
  }
  return root;
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
