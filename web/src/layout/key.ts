/**
 * `Key` (D428): an index, the one kind of value an element's `key` and an `<option>`'s DOM `value`
 * are made of in the app (never a text: a box throws there, and an address's or an operator's raw
 * text is no key). `keyOf` is its one mint (this module's one type assertion: a whole number is an
 * index); a test refuses any other module that mints one (an assertion, a type predicate or an
 * overload signature that gives a `Key`).
 */

declare const keyBrand: unique symbol;

/** An index, made by `keyOf` alone. */
export type Key = number & { readonly [keyBrand]: true };

/** The words of a refused index: fixed. */
export const NO_INDEX = "A key is an index: a whole number from 0 (D428).";

/** The largest index, 2^53 - 1. */
const MAX_INDEX = 9007199254740991;

/** The key of the `index`th element of a list. */
export function keyOf(index: number): Key {
  if (!(index >= 0 && index <= MAX_INDEX && index % 1 === 0)) {
    throw new RangeError(NO_INDEX);
  }
  return index as Key;
}
