/**
 * Answers for the shared components' tests (D430): a JSON text read through the one decoder (every
 * number a `ServerNumber` of its source text) and walked by the typed answers' table (every string a
 * `ServerText`, every map of data keys a `BoxedMap`), as a component meets them; and the JSON number
 * grammar's corpus, enumerated from its parts (RFC 8259 §6), with an oracle of its own.
 */
import { decode } from "../../src/api/decode";
import { walkComponent } from "../../src/api/typed";

/** `json` decoded and walked as `component` (the caller states the type it reads it as); a text that
 * does not walk throws (a fixture of the test's that the table refuses is the test's mistake). */
export function walked(component: string, json: string): unknown {
  const found = walkComponent(component, decode(json));
  if (!found.ok) {
    throw new Error(`${component} does not walk: ${json.slice(0, 300)}`);
  }
  return found.value;
}

/** The parts of JSON's number grammar: a sign, an integer part, a fraction, an exponent. */
const SIGNS = ["", "-"];
const INTEGERS = ["0", "1", "7", "10", "123", "468", "9007199254740993", `1${"0".repeat(30)}`];
const FRACTIONS = ["", ".0", ".10", ".5", ".000001", ".00"];
const EXPONENTS = ["", "e0", "e-07", "E+3", "e400", "E-400", "e+0"];

/** Every number text of the parts above (672): the server writes a subset of them. */
export const NUMBER_TEXTS: readonly string[] = SIGNS.flatMap((sign) => INTEGERS.flatMap((integer) => FRACTIONS.flatMap((fraction) => EXPONENTS.map((exponent) => `${sign}${integer}${fraction}${exponent}`))));

/** Texts that are no JSON number: each refused by the oracle below, and by the page. */
export const NOT_NUMBERS: readonly string[] = [
  "",
  " 1",
  "1 ",
  "+1",
  "01",
  "-01",
  "1.",
  ".5",
  "1e",
  "1e+",
  "--1",
  "0x10",
  "1_000",
  "NaN",
  "Infinity",
  "-Infinity",
  "1,5",
  "١",
  "１",
  "1‮",
  "<img src=x onerror=alert(1)>",
  "1e-07\n",
  "true",
  "null",
  '"1"',
];

/** The oracle of the grammar: the engine's JSON parser, independent of the page's expression, with
 * the whitespace JSON allows around a value refused (a box holds the number's own text). */
export function isNumberText(text: string): boolean {
  if (text.trim() !== text || text === "") {
    return false;
  }
  try {
    return typeof JSON.parse(text) === "number";
  } catch {
    return false;
  }
}

/** The oracle of a number's sign and zero: its mantissa's digits as a BigInt (exact at any size). */
export function signOf(text: string): -1 | 0 | 1 {
  const mantissa = text.replace(/^-/u, "").replace(/[eE].*$/u, "").replace(".", "");
  if (BigInt(mantissa) === 0n) {
    return 0;
  }
  return text.startsWith("-") ? -1 : 1;
}
