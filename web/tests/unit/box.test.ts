/**
 * The server-number box (D419): its text is the server's exactly and reachable by `textOf` alone;
 * every conversion throws one fixed message; it has no own property, clones to nothing, and is
 * frozen with its prototype and class.
 */
import { inspect } from "node:util";

import { describe, expect, it } from "vitest";

import { box, FORGED, isServerNumber, OPAQUE, ServerNumber, textOf } from "../../src/api/box";

/** Wire vectors: what a JS number would lose (digits, text, the sign of zero). */
const VECTORS = ["0", "-0", "1.10", "1.0", "1e-7", "5e-324", "9007199254740991", "9007199254740993", "-12345678901234567890", "1E+2"];

/** Run a conversion on a box, its type erased as a component's accident would erase it. */
const erase = (value: ServerNumber): number => value as unknown as number;
const asText = (value: ServerNumber): string => value as unknown as string;

describe("the box keeps the server's text exactly", () => {
  it.each(VECTORS)("%s", (text) => {
    expect(textOf(box(text))).toBe(text);
  });

  it("refuses to read anything that is not a box, without its text", () => {
    const fake = Object.create(ServerNumber.prototype) as ServerNumber;
    expect(() => textOf(fake)).toThrow(TypeError);
    const proxied = new Proxy(box("137"), {});
    let message = "";
    try {
      textOf(proxied);
    } catch (error) {
      message = error instanceof Error ? error.message : "";
    }
    expect(message).not.toBe("");
    expect(message).not.toContain("137");
  });
});

describe("every conversion throws the fixed words, which hold no text", () => {
  const n = box("137.25");
  const m = box("4");
  it.each([
    ["valueOf", () => n.valueOf()],
    ["toString", () => n.toString()],
    ["toLocaleString", () => n.toLocaleString()],
    ["toJSON", () => n.toJSON()],
    ["Symbol.toPrimitive", () => n[Symbol.toPrimitive]()],
    ["Number()", () => Number(n)],
    ["String()", () => String(n)],
    ["BigInt()", () => BigInt(erase(n))],
    ["a template", () => `x${asText(n)}`],
    ["unary plus", () => +asText(n)],
    ["unary minus", () => -erase(n)],
    ["subtraction", () => erase(n) - erase(m)],
    ["addition to a string", () => "x" + String(erase(n))],
    ["a comparison", () => n < m],
    ["loose equality", () => erase(n) == 137.25],
    ["JSON.stringify", () => JSON.stringify({ n })],
    ["Intl", () => new Intl.NumberFormat("en").format(erase(n))],
    ["Math", () => Math.floor(erase(n))],
    ["parseInt", () => Number.parseInt(String(n), 10)],
    ["isNaN", () => isNaN(erase(n))],
    ["a Date", () => new Date(erase(n))],
    ["an array's length", () => Array.from({ length: erase(n) })],
    ["a bitwise operator", () => erase(n) | 0],
    ["a sort comparator", () => [n, m].sort((a, b) => (a < b ? -1 : 1))],
  ])("%s", (_conversion, convert) => {
    expect(convert).toThrow(new TypeError(OPAQUE));
  });

  it("says the same of every number, so nothing of any", () => {
    const words = (text: string): string => {
      try {
        return String(box(text));
      } catch (error) {
        return error instanceof Error ? error.message : "";
      }
    };
    expect(new Set(["1", "999", "1.10", "-0"].map(words))).toEqual(new Set([OPAQUE]));
  });
});

describe("the box has nothing to read", () => {
  const n = box("1.10");

  it("has no own property, string or symbol", () => {
    expect(Reflect.ownKeys(n)).toEqual([]);
    expect(Object.getOwnPropertyNames(n)).toEqual([]);
    expect(Object.entries(n)).toEqual([]);
  });

  it("clones to an empty object", () => {
    expect(structuredClone(n)).toEqual({});
  });

  it("shows no text when inspected", () => {
    expect(inspect(n, { showHidden: true, depth: 5 })).not.toContain("1.10");
  });

  it("has no static surface but the brand check, and its prototype nothing but the five conversions", () => {
    expect(Reflect.ownKeys(ServerNumber).map(String).sort()).toEqual(["holds", "length", "name", "prototype"]);
    expect(Reflect.ownKeys(ServerNumber.prototype).map(String).sort()).toEqual(
      ["Symbol(Symbol.toPrimitive)", "constructor", "toJSON", "toLocaleString", "toString", "valueOf"],
    );
  });

  it("is frozen, as are its prototype and its class", () => {
    expect(Object.isFrozen(n)).toBe(true);
    expect(Object.isFrozen(ServerNumber.prototype)).toBe(true);
    expect(Object.isFrozen(ServerNumber)).toBe(true);
    expect(() => {
      Object.defineProperty(ServerNumber.prototype, "valueOf", { value: () => 1 });
    }).toThrow(TypeError);
  });
});

describe("isServerNumber", () => {
  it("knows a box by its private field, not its prototype", () => {
    expect(isServerNumber(box("1"))).toBe(true);
    expect(isServerNumber(Object.create(ServerNumber.prototype))).toBe(false);
    expect(isServerNumber(new Proxy(box("1"), {}))).toBe(false);
    expect(isServerNumber({})).toBe(false);
    expect(isServerNumber(1)).toBe(false);
    expect(isServerNumber("1")).toBe(false);
    expect(isServerNumber(null)).toBe(false);
  });
});

/** A constructor as a forger finds it, its type erased (the constructor is private). */
type Forge = new (...given: unknown[]) => ServerNumber;

describe("a box is made by `box` alone, whatever its text or class", () => {
  const n = box("1");

  it("refuses the class itself, with the text or without", () => {
    expect(() => new (ServerNumber as unknown as Forge)("999")).toThrow(new TypeError(FORGED));
    expect(() => new (ServerNumber as unknown as Forge)()).toThrow(new TypeError(FORGED));
  });

  it("refuses a subclass that calls super with a text", () => {
    class Forged extends (ServerNumber as unknown as Forge) {
      constructor() {
        super("999");
      }
    }
    expect(() => new Forged()).toThrow(new TypeError(FORGED));
  });

  it("refuses Reflect.construct, with the class or as a new target", () => {
    expect(() => Reflect.construct(ServerNumber as unknown as Forge, ["42"])).toThrow(new TypeError(FORGED));
    class Other {
      readonly other = 1;
    }
    expect(() => Reflect.construct(ServerNumber as unknown as Forge, ["42"], Other)).toThrow(new TypeError(FORGED));
    const hollow: unknown = Reflect.construct(Other, [], ServerNumber as unknown as Forge);
    expect(isServerNumber(hollow)).toBe(false);
    expect(() => textOf(hollow as ServerNumber)).toThrow(TypeError);
  });

  it("refuses the constructor found on a box's prototype", () => {
    const found = (Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n) as object, "constructor") as { value: Forge }).value;
    expect(found).toBe(ServerNumber);
    expect(() => new found("7")).toThrow(new TypeError(FORGED));
    expect(() => {
      Reflect.apply(found, undefined, ["7"]);
    }).toThrow(TypeError);
  });

  it("refuses a token guessed: a symbol, a string, a registered symbol, the class's own name", () => {
    for (const guess of [Symbol("aibi.server-number"), Symbol.for("aibi.server-number"), "aibi.server-number", {}, ServerNumber]) {
      expect(() => new (ServerNumber as unknown as Forge)(guess, "5")).toThrow(new TypeError(FORGED));
    }
  });

  it("says the same of every text, so nothing of any", () => {
    const words = (text: string): string => {
      try {
        const made: unknown = new (ServerNumber as unknown as Forge)(text);
        return typeof made;
      } catch (error) {
        return error instanceof Error ? error.message : "";
      }
    };
    expect(new Set(["1", "999", "-0", "1.10"].map(words))).toEqual(new Set([FORGED]));
  });

  it("makes no box that passes the brand check but through `box`", () => {
    expect(isServerNumber(n)).toBe(true);
    class Subclass extends (ServerNumber as unknown as Forge) {}
    expect(() => isServerNumber(new Subclass("1"))).toThrow(TypeError);
    expect(isServerNumber(Object.create(ServerNumber.prototype))).toBe(false);
  });
});
