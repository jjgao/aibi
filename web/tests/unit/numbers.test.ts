/**
 * The sanctioned predicates and the numbers' internals (D430): `isZero`, `isPositive` and
 * `largestOf` held to oracles of their own over corpora enumerated from the grammar and from the
 * `Reason` vocabulary, never a JS number made from a server number; the glyphs of every number of
 * the grammar; a text that is no number read by nothing.
 */
import { describe, expect, it } from "vitest";

import { box, boxMap, boxText, type BoxedMap, entriesOf, readText, type ServerNumber } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { VOCABULARIES } from "../../src/api/generated/shapes";
import { countGlyphs, GLYPHS, glyphsOf, isCountOf, isPositive, isZero, largestOf, LISTED, NOT_A_COUNT, NUMBER_TEXT, remainingOf, UNREAD } from "../../src/api/numbers";
import { isNumberText, NOT_NUMBERS, NUMBER_TEXTS, signOf } from "./answers";

function decoded(text: string): ServerNumber {
  const [found] = decode(`[${text}]`) as readonly ServerNumber[];
  if (found === undefined) {
    throw new Error("no number");
  }
  return found;
}

describe("isZero and isPositive", () => {
  it("agree with the mantissa's BigInt over every number of the corpus, of any size and exponent", () => {
    const wrong = NUMBER_TEXTS.filter((text) => {
      const sign = signOf(text);
      const value = decoded(text);
      return isZero(value) !== (sign === 0) || isPositive(value) !== (sign === 1);
    });
    expect(wrong).toEqual([]);
    expect(NUMBER_TEXTS.filter((text) => signOf(text) === 0).length).toBe(42);
    expect(isZero(decoded("-0.0"))).toBe(true);
    expect(isPositive(decoded("-0.0"))).toBe(false);
    expect(isZero(decoded("1e-400"))).toBe(false);
    expect(isPositive(decoded("1e-400"))).toBe(true);
    expect(isPositive(decoded("-1e400"))).toBe(false);
  });

  it("read a box whose text is no JSON number by nothing: they throw, in fixed words", () => {
    for (const text of NOT_NUMBERS) {
      expect(() => isZero(box(text)), JSON.stringify(text)).toThrow(UNREAD);
      expect(() => isPositive(box(text)), JSON.stringify(text)).toThrow(UNREAD);
    }
  });

  it("refuse a value that is no box (a null, a suppressed count, is never zero): the brand check throws", () => {
    const values: readonly unknown[] = [null, undefined, 0, "0", {}, boxText("0")];
    for (const value of values) {
      expect(() => isZero(value as ServerNumber)).toThrow(TypeError);
      expect(() => isPositive(value as ServerNumber)).toThrow(TypeError);
    }
  });
});

describe("the grammar and the glyphs", () => {
  it("NUMBER_TEXT is the oracle's grammar over the corpus and its refusals", () => {
    expect(NUMBER_TEXTS.filter((text) => !NUMBER_TEXT.test(text))).toEqual([]);
    expect(NOT_NUMBERS.filter((text) => NUMBER_TEXT.test(text))).toEqual([]);
    for (const text of [...NUMBER_TEXTS, ...NOT_NUMBERS]) {
      expect(NUMBER_TEXT.test(text), JSON.stringify(text)).toBe(isNumberText(text));
    }
  });

  it("gives a number's text as its glyphs, exactly, and null for a text that is no number", () => {
    for (const text of NUMBER_TEXTS) {
      expect(glyphsOf(decoded(text))?.join("")).toBe(text);
    }
    for (const text of NOT_NUMBERS) {
      expect(glyphsOf(box(text))).toBeNull();
    }
    expect(new Set(NUMBER_TEXTS.join(""))).toEqual(new Set(GLYPHS));
  });

  it("gives a whole count the page made as glyphs, and refuses any other number", () => {
    for (const count of [0, 1, 9, 10, 64, 65, 1000, 9007199254740991]) {
      expect(countGlyphs(count).join("")).toBe(String(count));
    }
    for (const count of [-1, 0.5, Number.NaN, Number.POSITIVE_INFINITY, 9007199254740992]) {
      expect(() => countGlyphs(count)).toThrow(NOT_A_COUNT);
      expect(() => remainingOf(count)).toThrow(NOT_A_COUNT);
    }
  });

  it("tells whether a server number is exactly a count the page made (D432's partial): the plain whole number's text alone", () => {
    for (const count of [0, 1, 9, 10, 40, 64, 1000, 9007199254740991]) {
      expect(isCountOf(decoded(String(count)), count), String(count)).toBe(true);
      expect(isCountOf(decoded(String(count + 1)), count), String(count)).toBe(false);
    }
    for (const text of ["3.0", "3e0", "0.3e1", "30e-1", "-3", "3.5", "-0"]) {
      expect(isCountOf(decoded(text), 3), text).toBe(false);
    }
    expect(isCountOf(decoded("0"), 0)).toBe(true);
    expect(isCountOf(box("three"), 3)).toBe(false);
    expect(isCountOf(box("03"), 3)).toBe(false);
    // No count is a negative, a fraction, a non-finite or a number past 2^53 - 1, even when the
    // number's text is that of the value the page made.
    for (const [text, count] of [["-1", -1], ["0.5", 0.5], ["9007199254740992", 9007199254740992], ["1", Number.NaN], ["1", Number.POSITIVE_INFINITY]] as const) {
      expect(isCountOf(decoded(text), count), text).toBe(false);
    }
  });

  it("counts the members past LISTED, and none at or below it", () => {
    expect([0, 1, LISTED - 1, LISTED, LISTED + 1, 200].map(remainingOf)).toEqual([0, 0, 0, 0, 1, 136]);
  });
});

const REASONS: readonly string[] = VOCABULARIES.Reason;

/** A map of reasons to counts, in the order given (the engine's key order is the walk's). */
function counts(entries: readonly (readonly [string, string])[]): BoxedMap<ServerNumber> {
  return boxMap(entries.map(([key, value]) => [boxText(key), decoded(value)] as const));
}

/** The oracle of `largestOf`: the largest by BigInt, ties by the vocabulary's order. */
function oracle(entries: readonly (readonly [string, string])[]): string | null {
  let best: readonly [string, bigint, number] | null = null;
  for (const [key, value] of entries) {
    const rank = REASONS.includes(key) ? REASONS.indexOf(key) : REASONS.length;
    const amount = BigInt(value);
    if (best === null || amount > best[1] || (amount === best[1] && (rank < best[2] || (rank === best[2] && key < best[0])))) {
      best = [key, amount, rank];
    }
  }
  return best === null || best[1] === 0n ? null : best[0];
}

/** A small generator of maps (a linear congruential sequence: the same maps every run). */
function maps(count: number): (readonly (readonly [string, string])[])[] {
  const values = ["0", "1", "2", "9", "10", "11", "99", "100", "9007199254740991", "9007199254740993"];
  let seed = 7;
  const next = (bound: number): number => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed % bound;
  };
  return Array.from({ length: count }, () => {
    const keys = [...REASONS].sort(() => next(3) - 1);
    return keys.slice(0, 1 + next(keys.length)).map((key) => [key, values[next(values.length)] ?? "0"] as const);
  });
}

describe("largestOf", () => {
  it("names the key of the largest count, ties by the Reason vocabulary's order, over 2,000 generated maps", () => {
    const generated = maps(2000);
    const wrong = generated.filter((entries) => {
      const found = largestOf(counts(entries));
      return (found === null ? null : readText(found)) !== oracle(entries);
    });
    expect(wrong).toEqual([]);
    expect(generated.filter((entries) => oracle(entries) !== null).length).toBeGreaterThan(1000);
  });

  it("breaks every tie by the vocabulary's order, whatever the map's own order", () => {
    for (const [first, second] of REASONS.flatMap((one, index) => REASONS.slice(index + 1).map((other) => [one, other] as const))) {
      for (const order of [
        [first, second],
        [second, first],
      ]) {
        const found = largestOf(counts(order.map((key) => [key, "5"] as const)));
        expect(found === null ? null : readText(found)).toBe(first);
      }
    }
  });

  it("ranks a key the vocabulary does not have after every reason it has, at a tie", () => {
    for (const reason of REASONS) {
      for (const order of [
        ["A_NEWER_REASON", reason],
        [reason, "A_NEWER_REASON"],
      ]) {
        const found = largestOf(counts(order.map((key) => [key, "3"] as const)));
        expect(found === null ? null : readText(found)).toBe(reason);
      }
    }
    const larger = largestOf(counts([["NO_ROWS", "3"], ["A_NEWER_REASON", "4"]]));
    expect(larger === null ? null : readText(larger)).toBe("A_NEWER_REASON");
  });

  it("breaks a tie between two keys outside the vocabulary by the keys' text, whatever the order the map holds them in (m4 of #121 round 3)", () => {
    for (const [first, second] of [
      ["ZZ_NEW", "AA_NEW"],
      ["AA_NEW", "ZZ_NEW"],
      ["A_NEWER_REASON", "ANOTHER_NEW_REASON"],
      ["\u{10000}", "\uFFFF"],
      ["\uFFFF", "\u{10000}"],
    ] as const) {
      const smaller = first < second ? first : second;
      for (const amount of ["3", "9007199254740993"]) {
        const found = largestOf(counts([[first, amount], [second, amount]]));
        expect(found === null ? null : readText(found)).toBe(smaller);
        const reversed = largestOf(counts([[second, amount], [first, amount]]));
        expect(reversed === null ? null : readText(reversed)).toBe(smaller);
      }
    }
    const tied = largestOf(counts([["ZZ_NEW", "3"], ["AA_NEW", "3"]]));
    const reversed = largestOf(counts([["AA_NEW", "3"], ["ZZ_NEW", "3"]]));
    expect(tied === null ? null : readText(tied)).toBe("AA_NEW");
    expect(reversed === null ? null : readText(reversed)).toBe("AA_NEW");
    // By UTF-16 code units U+10000 (D800 DC00) sorts before U+FFFF; by code points it sorts after (m5r4 of round 4).
    const astral = largestOf(counts([["\uFFFF", "3"], ["\u{10000}", "3"]]));
    expect(astral === null ? null : readText(astral)).toBe("\u{10000}");
  });

  it("gives null for a null map (suppressed), an empty map, a map of zeros, and a value that is no plain whole number", () => {
    expect(largestOf(null)).toBeNull();
    expect(largestOf(counts([]))).toBeNull();
    expect(largestOf(counts(REASONS.map((key) => [key, "0"] as const)))).toBeNull();
    for (const odd of ["1e3", "-1", "1.5", "0.0"]) {
      expect(largestOf(counts([["NO_ROWS", "4"], ["NOT_ASSESSED", odd]]))).toBeNull();
    }
  });

  it("gives the box of the key the server sent, never a new one or a text", () => {
    const map = counts([["NO_ROWS", "4"], ["NOT_COVERED", "9"]]);
    const found = largestOf(map);
    const [, second] = entriesOf(map);
    expect(found).toBe(second?.[0]);
    expect(found === null ? null : readText(found)).toBe("NOT_COVERED");
  });

  it("refuses a suppressed member at run time too: a null where a count stands throws (the brand check)", () => {
    const withheld = boxMap([[boxText("NO_ROWS"), null]]) as unknown as BoxedMap<ServerNumber>;
    expect(() => largestOf(withheld)).toThrow(TypeError);
  });
});
