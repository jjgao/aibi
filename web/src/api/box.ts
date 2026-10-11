/**
 * The server-number box (D419): every number of a response's JSON body reaches the app as a
 * `ServerNumber`, never as a JS number, a string of digits or a BigInt. A client that could do
 * arithmetic on the numbers it shows could rebuild a count the server withheld from the ones it
 * showed (complementary suppression, §8.4), so no value a component holds can be computed with:
 * a number is shown only through `format.ts` (M5.1c-2), whose output is a node, never a string.
 *
 * The box is opaque by construction. Its source text (the number exactly as the server wrote it:
 * `1.10`, `-0`, `9007199254740993`) is held in an ES private field, `#text`, which no code outside
 * this class can name: not a property (`Object.getOwnPropertyNames` gives `[]`, `structuredClone`
 * gives `{}`, a `Proxy` around a box throws on the field), and not a module `WeakMap`, which a
 * patched `WeakMap.prototype.get` would leak. Every conversion a value goes through (`valueOf`,
 * `toString`, `toLocaleString`, `toJSON` and `Symbol.toPrimitive`) throws a `TypeError` whose
 * message holds no text of the number, so arithmetic, comparison, a template, `String(n)`,
 * `JSON.stringify` and `Intl` all throw at run time where the type checker and the lint have not
 * refused them already. Instances, the prototype and the class are frozen.
 *
 * What the box is against: a component's accident or a lazy hand, not deliberate code (the bundle
 * is the operator's own trusted code, D410): code that patches a builtin is deliberate, and the
 * lint refuses it besides (`no-extend-native` and assignments to the builtins' members).
 *
 * `box` and `textOf` are this module's internals: the lint lets only `decode.ts` import them (and
 * the request half's builders, when they come), and a test over the TypeScript program holds that
 * no other module of `src/` names them and that `src/api/index.ts` exports neither.
 */

/** The words of every refused conversion: fixed, and never the number's text. */
export const OPAQUE = "A server number is opaque: it is shown, never converted (D419).";

/** The words of a refused construction: fixed, and never the text asked for. */
export const FORGED = "A server number is made by the decoder alone (D419).";

/** The construction token: held by this module alone, which no other code can name. */
const TOKEN: unique symbol = Symbol("aibi.server-number");

let make: (text: string) => ServerNumber;
let read: (number: ServerNumber) => string;

export class ServerNumber {
  readonly #text: string;

  private constructor(token: typeof TOKEN, text: string) {
    if (token !== TOKEN) {
      throw new TypeError(FORGED);
    }
    this.#text = text;
    Object.freeze(this);
  }

  static {
    make = (text) => new ServerNumber(TOKEN, text);
    read = (number) => number.#text;
  }

  /** Whether `value` is a box this class made: the brand check of the private field, which an
   * object passes only by being constructed with the token (`box`), whatever its prototype says
   * and whatever class it is of. */
  static holds(value: unknown): value is ServerNumber {
    return typeof value === "object" && value !== null && #text in value;
  }

  valueOf(): never {
    throw new TypeError(OPAQUE);
  }

  toString(): never {
    throw new TypeError(OPAQUE);
  }

  toLocaleString(): never {
    throw new TypeError(OPAQUE);
  }

  toJSON(): never {
    throw new TypeError(OPAQUE);
  }

  [Symbol.toPrimitive](): never {
    throw new TypeError(OPAQUE);
  }
}

Object.freeze(ServerNumber.prototype);
Object.freeze(ServerNumber);

/** A box of a number's source text, as the server wrote it. `decode.ts` alone calls it. */
export function box(text: string): ServerNumber {
  return make(text);
}

/** The source text a box holds; a value that is no box throws (the brand check). For the request
 * half's builders (M5.1c-1c, M5.3) and the tests; no module of `src/` calls it yet. */
export function textOf(number: ServerNumber): string {
  return read(number);
}

/** Whether a value is a server number: what a walker over a decoded tree asks. It gives nothing
 * of the number. */
export function isServerNumber(value: unknown): value is ServerNumber {
  return ServerNumber.holds(value);
}
