/**
 * The one decoder (D419): every number boxed from its source text, which must be given; every
 * object and array frozen; a refusal's words fixed; the oracle's counter counts every call.
 */
import { describe, expect, it } from "vitest";

import { isServerNumber, textOf, type ServerNumber } from "../../src/api/box";
import { decodedCount as exported } from "../../src/api/oracle";
import { decode, decodedCount, DecodeError, revive, sourceTextSupported, UNDECODABLE } from "../../src/api/decode";
import type { JsonOut } from "../../src/api/json";

/** The member `key` of a decoded object. */
function member(value: JsonOut, key: string): JsonOut {
  if (typeof value !== "object" || value === null || Array.isArray(value) || isServerNumber(value)) {
    throw new Error("not an object");
  }
  const found = (value as Readonly<Record<string, JsonOut>>)[key];
  if (found === undefined) {
    throw new Error(`no ${key}`);
  }
  return found;
}

function text(value: JsonOut): string {
  if (!isServerNumber(value)) {
    throw new Error("not a box");
  }
  return textOf(value);
}

describe("decode boxes every number from its source text", () => {
  it("keeps the wire vectors exactly", () => {
    const decoded = decode('{"a": 1.10, "b": [-0, 9007199254740993, 1e-7, 5e-324, 1.0, 1E+2, -0.0], "c": "12", "d": null, "e": true}');
    expect(text(member(decoded, "a"))).toBe("1.10");
    const list = member(decoded, "b");
    expect(Array.isArray(list)).toBe(true);
    expect((list as readonly JsonOut[]).map(text)).toEqual(["-0", "9007199254740993", "1e-7", "5e-324", "1.0", "1E+2", "-0.0"]);
    expect(member(decoded, "c")).toBe("12");
    expect(member(decoded, "d")).toBe(null);
    expect(member(decoded, "e")).toBe(true);
  });

  it("boxes a number at the top and in nested arrays and objects", () => {
    expect(text(decode(" 42 "))).toBe("42");
    const deep = decode('[[{"x": [3]}]]') as readonly (readonly { x: readonly ServerNumber[] }[])[];
    expect(textOf(deep[0]?.[0]?.x[0] as ServerNumber)).toBe("3");
  });

  it("freezes every object and array it makes", () => {
    const decoded = decode('{"a": {"b": [1, {"c": 2}]}}');
    const a = member(decoded, "a");
    const b = member(a, "b") as readonly JsonOut[];
    expect([decoded, a, b, b[1]].every((value) => Object.isFrozen(value))).toBe(true);
    expect(() => {
      (decoded as Record<string, unknown>)["a"] = 1;
    }).toThrow(TypeError);
  });

  it("keeps a __proto__ member as an own member, the prototype untouched", () => {
    const decoded = decode('{"__proto__": {"polluted": 1}, "x": 2}') as object;
    expect(Object.hasOwn(decoded, "__proto__")).toBe(true);
    expect(Object.getPrototypeOf(decoded)).toBe(Object.prototype);
    expect(({} as Record<string, unknown>)["polluted"]).toBeUndefined();
  });
});

describe("decode refuses a number whose source text is not given", () => {
  it.each([
    ["no context", undefined],
    ["a context without a source", {}],
    ["a source that is not text", { source: 1 }],
  ])("%s", (_case, context) => {
    expect(() => revive.call({}, "a", 1, context)).toThrow(DecodeError);
  });

  it("refuses the number of a holder changed under the reviver, which the engine gives no source", () => {
    const sources: unknown[] = [];
    expect((): unknown =>
      JSON.parse("[1, 2]", function (this: unknown, key: string, value: unknown, context?: { source?: unknown }) {
        if (key === "0" && Array.isArray(this)) {
          this[1] = 3;
        }
        if (key === "1") {
          sources.push(context?.source);
        }
        return revive.call(this, key, value, context);
      }),
    ).toThrow(DecodeError);
    expect(sources).toEqual([undefined]);
  });

  it("keeps a value that is no number as it is", () => {
    expect(revive.call({}, "a", "1", undefined)).toBe("1");
    expect(revive.call({}, "a", null, undefined)).toBe(null);
  });
});

describe("decode's refusals", () => {
  it.each(["", "{", "[1,]", "{'a': 1}", "NaN", "1 2", '{"a": 1}x', " "])("refuses %j with the fixed words alone", (body) => {
    let caught: unknown;
    try {
      decode(body);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(DecodeError);
    expect((caught as DecodeError).message).toBe(UNDECODABLE);
    expect((caught as DecodeError).cause).toBeUndefined();
  });

  it("never quotes the body", () => {
    try {
      decode('{"secret-ish": 1, oops}');
    } catch (error) {
      expect(String(error)).not.toContain("secret");
      expect(String(error)).not.toContain("oops");
    }
  });
});

describe("the oracle's counter and the engine check", () => {
  it("counts every call, refused or not", () => {
    const before = decodedCount();
    decode("1");
    expect(() => decode("x")).toThrow(DecodeError);
    expect(decodedCount()).toBe(before + 2);
  });

  it("is exported by oracle.ts, the harness's module, and by no other that the index re-exports", () => {
    expect(exported).toBe(decodedCount);
  });

  it("finds JSON source text access in this engine", () => {
    expect(sourceTextSupported()).toBe(true);
  });
});
