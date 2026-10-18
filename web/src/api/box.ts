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
 * `box` and `textOf` are this module's internals: the lint lets only `decode.ts` import `box`, and
 * only `decode.ts` and `provenance.ts` import `textOf` (`provenance.ts`'s `idAt` is the one function
 * that reads a box's text, from a node stamped for its feed in its era: D425, MA3 of round 3), and a
 * test over the TypeScript program holds that no other module of `src/` names them and that
 * `src/api/index.ts` exports neither.
 *
 * **The text box (D426).** `ServerText` is the number box's twin for every string of a public
 * tool's answer: `typed.ts`'s walk makes one of each string (`boxText`, its alone) as it validates
 * the answer against the generated table, so that no component holds a response's text as a
 * `string`: a box is shown through `Text` (M5.1c-2b), and every conversion throws (React refuses it
 * as a child, a `title`, an `href`, a `key` or a `style`, the DOM as text, a template, `String`,
 * `JSON.stringify`, a comparison, a sort). Its text is read by `text.ts` alone (`readText`), whose
 * takers give a vocabulary member, a `DatasetId`, a `Choice`, a boolean or an order, never the text.
 * A map whose keys are data (`additionalProperties`, `patternProperties`, every object of an
 * `x-aibi-json` value) is a `BoxedMap` (`boxMap`, the walk's alone): its entries, keys boxed, are in
 * an ES private field and read through `entriesOf`, so no raw key exists after the walk
 * (`Object.keys`, `for...in`, a spread, `URLSearchParams` and `Headers` over it give nothing).
 * Neither box is equal to another of the same text (`===` compares identity): `text.ts`'s
 * `sameText` and `compareText` are the sanctioned equality and order.
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

/** The source text a box holds; a value that is no box throws (the brand check). `provenance.ts`'s
 * `idAt` (D425) and the tests alone call it. */
export function textOf(number: ServerNumber): string {
  return read(number);
}

/** Whether a value is a server number: what a walker over a decoded tree asks. It gives nothing
 * of the number. */
export function isServerNumber(value: unknown): value is ServerNumber {
  return ServerNumber.holds(value);
}

/** The words of every refused conversion of a server text: fixed, and never its text. */
export const OPAQUE_TEXT = "A server text is opaque: it is shown through Text, never converted (D426).";

/** The words of a refused construction of a server text or map: fixed. */
export const FORGED_TEXT = "A server text is made by the typed answers' walk alone (D426).";

/** The construction token of the text box and the map: held by this module alone. */
const TEXT_TOKEN: unique symbol = Symbol("aibi.server-text");

let makeText: (text: string) => ServerText;
let readOfText: (text: ServerText) => string;

/** A string of a public tool's answer (D426): opaque, as a `ServerNumber` is. */
export class ServerText {
  readonly #text: string;

  private constructor(token: typeof TEXT_TOKEN, text: string) {
    if (token !== TEXT_TOKEN) {
      throw new TypeError(FORGED_TEXT);
    }
    this.#text = text;
    Object.freeze(this);
  }

  static {
    makeText = (text) => new ServerText(TEXT_TOKEN, text);
    readOfText = (text) => text.#text;
  }

  /** Whether `value` is a text box this class made (the brand check of the private field). */
  static holds(value: unknown): value is ServerText {
    return typeof value === "object" && value !== null && #text in value;
  }

  valueOf(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toString(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toLocaleString(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toJSON(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  [Symbol.toPrimitive](): never {
    throw new TypeError(OPAQUE_TEXT);
  }
}

Object.freeze(ServerText.prototype);
Object.freeze(ServerText);

/** A box of a response's string. `typed.ts`'s walk alone calls it. */
export function boxText(text: string): ServerText {
  return makeText(text);
}

/** The text a text box holds; a value that is no box throws (the brand check). `text.ts`'s takers
 * and the tests alone call it. */
export function readText(text: ServerText): string {
  return readOfText(text);
}

let makeMap: <V>(entries: readonly (readonly [ServerText, V])[]) => BoxedMap<V>;
let entriesOfMap: <V>(map: BoxedMap<V>) => readonly (readonly [ServerText, V])[];

/** A map of an answer whose keys are data (D426): its entries, each key a `ServerText`, in a
 * private field, in the order the engine gave the decoded object's keys (integer-like keys first,
 * ascending; the JSON text's order is lost at the parse), so that nothing may cut or rank by it. */
export class BoxedMap<V> {
  readonly #entries: readonly (readonly [ServerText, V])[];

  private constructor(token: typeof TEXT_TOKEN, entries: readonly (readonly [ServerText, V])[]) {
    if (token !== TEXT_TOKEN) {
      throw new TypeError(FORGED_TEXT);
    }
    this.#entries = Object.freeze(entries.map((entry) => Object.freeze([entry[0], entry[1]] as const)));
    Object.freeze(this);
  }

  static {
    makeMap = (entries) => new BoxedMap(TEXT_TOKEN, entries);
    entriesOfMap = (map) => map.#entries;
  }

  /** Whether `value` is a map this class made. */
  static holds(value: unknown): value is BoxedMap<unknown> {
    return typeof value === "object" && value !== null && #entries in value;
  }

  valueOf(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toString(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toLocaleString(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  toJSON(): never {
    throw new TypeError(OPAQUE_TEXT);
  }

  [Symbol.toPrimitive](): never {
    throw new TypeError(OPAQUE_TEXT);
  }
}

Object.freeze(BoxedMap.prototype);
Object.freeze(BoxedMap);

/** A map of boxed keys. `typed.ts`'s walk alone calls it. */
export function boxMap<V>(entries: readonly (readonly [ServerText, V])[]): BoxedMap<V> {
  return makeMap(entries);
}

/** A map's entries, each key a `ServerText`: the one way to read a `BoxedMap` (D426), in the
 * engine's key order, which nothing may cut or rank by. */
export function entriesOf<V>(map: BoxedMap<V>): readonly (readonly [ServerText, V])[] {
  return entriesOfMap(map);
}
