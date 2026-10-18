/**
 * Routes (D419, D425): a parameter is percent-encoded whole and refused if empty, `.` or `..`; a
 * number is a safe whole number's digits; a query keeps its order and leaves out what has no value;
 * the generated functions build each operation's URL from its template, held with the dataset in
 * private fields that `urlOf` and `datasetOf` alone read.
 */
import { describe, expect, it } from "vitest";

import * as routes from "../../src/api/generated/routes";
import { type RequestId, requestId } from "../../src/api/ids";
import { datasetOf, REFUSED_SEGMENT, route, search, segment, urlOf } from "../../src/api/route";

describe("segment", () => {
  it.each([
    ["shop", "shop"],
    ["a/b", "a%2Fb"],
    ["a?b#c", "a%3Fb%23c"],
    ["50%", "50%25"],
    ["...", "..."],
    [".a", ".a"],
    ["%2e%2e", "%252e%252e"],
    ["é", "%C3%A9"],
    [0, "0"],
    [9007199254740991, "9007199254740991"],
  ])("%j is %s", (given, expected) => {
    expect(segment(given)).toBe(expected);
  });

  it.each(["\ud800", "a\udc00", "\udc00\ud800", "x\ud83d", "\ud83dy"])("refuses a segment that is not well-formed text, %j, in the fixed words and not the engine's URIError", (given) => {
    expect(() => segment(given)).toThrow(new RangeError(REFUSED_SEGMENT));
    let thrown: unknown;
    try {
      segment(given);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(RangeError);
    expect(thrown).not.toBeInstanceOf(URIError);
  });

  it("encodes a well-formed pair, an astral character, whole", () => {
    expect(segment("\ud83d\ude00")).toBe("%F0%9F%98%80");
  });

  it.each(["", ".", "..", -1, 1.5, 9007199254740992, Number.NaN, Infinity, -0.5])("refuses %j", (given) => {
    expect(() => segment(given)).toThrow(new RangeError(REFUSED_SEGMENT));
  });
});

describe("search", () => {
  it("keeps the order, encodes, and leaves out an entry without a value", () => {
    expect(search([["b", "x y"], ["a", undefined], ["c", 7]])).toBe("?b=x+y&c=7");
    expect(search([["a", undefined]])).toBe("");
    expect(search([])).toBe("");
  });

  it("refuses a number that is no safe whole number", () => {
    expect(() => search([["release", 1.5]])).toThrow(REFUSED_SEGMENT);
  });
});

describe("the generated routes", () => {
  const id = (text: string): RequestId => {
    const made = requestId(text);
    if (made === null) {
      throw new Error("no id");
    }
    return made;
  };

  it("build each operation's URL and method, and hold the dataset their path names", () => {
    expect(routes.health()).toEqual({ operation: "health", method: "GET" });
    expect(urlOf(routes.health())).toBe("/api/health");
    expect(datasetOf(routes.health())).toBe(null);
    expect(urlOf(routes.search_catalog())).toBe("/api/tools/search_catalog");
    expect(urlOf(routes.descriptor({ dataset: "shop", descriptor: "orders/total" }, { release: "draft" }))).toBe(
      "/operator/datasets/shop/descriptors/orders%2Ftotal?release=draft",
    );
    expect(urlOf(routes.descriptor({ dataset: "shop", descriptor: "x" }))).toBe("/operator/datasets/shop/descriptors/x");
    expect(urlOf(routes.queue({ dataset: "shop" }, { release: id("12") }))).toBe("/operator/datasets/shop/queue?release=12");
    const reject = routes.reject({ dataset: "shop", proposal: id("3") });
    expect(reject).toEqual({ operation: "reject", method: "POST" });
    expect(urlOf(reject)).toBe("/operator/datasets/shop/proposals/3/reject");
    expect(datasetOf(reject)).toBe("shop");
    expect(urlOf(routes.take_over({ dataset: "shop" }))).toBe("/operator/datasets/shop/session/take-over");
    expect(urlOf(routes.upload({ dataset: "shop" }, { extension: ".csv" }))).toBe("/operator/datasets/shop/uploads?extension=.csv");
    expect(datasetOf(routes.dataset_state({ dataset: "a/b" }))).toBe("a/b");
  });

  it("refuse a parameter that would climb or vanish", () => {
    expect(() => routes.descriptor({ dataset: "shop", descriptor: ".." })).toThrow(REFUSED_SEGMENT);
    expect(() => routes.dataset_state({ dataset: "." })).toThrow(REFUSED_SEGMENT);
    expect(() => routes.dataset_state({ dataset: "\ud800" })).toThrow(new RangeError(REFUSED_SEGMENT));
  });

  it("are frozen, and their URL and dataset are no property: a lookalike or a copy is no route (D425)", () => {
    const made = routes.queue({ dataset: "shop" }, { release: id("12") });
    expect(Object.isFrozen(made)).toBe(true);
    expect(Object.isFrozen(Object.getPrototypeOf(made))).toBe(true);
    expect(Reflect.ownKeys(made)).toEqual(["operation", "method"]);
    expect("url" in made).toBe(false);
    expect("dataset" in made).toBe(false);
    // @ts-expect-error -- a route has no readable URL (D425, m9)
    expect(made.url).toBe(undefined);
    expect(JSON.stringify(made)).toBe('{"operation":"queue","method":"GET"}');
    for (const copy of [{ ...made }, structuredClone(made), Object.create(made) as object, new Proxy(made, {}), { operation: "queue", method: "GET", url: "/operator/datasets/shop/queue?release=12" }]) {
      expect(urlOf(copy)).toBe(null);
      expect(datasetOf(copy)).toBe(null);
    }
    for (const other of [null, undefined, 1, "x", Symbol("s"), () => undefined]) {
      expect(urlOf(other)).toBe(null);
      expect(datasetOf(other)).toBe(null);
    }
    expect(urlOf(route("health", "GET", "/api/health"))).toBe("/api/health");
  });

  it("take no plain number at a request id's position, at compile time (D425, m6)", () => {
    // @ts-expect-error -- a plain number is no RequestId
    expect(() => routes.reject({ dataset: "shop", proposal: 3 })).not.toThrow();
    // @ts-expect-error -- a plain number is no RequestId
    expect(urlOf(routes.queue({ dataset: "shop" }, { release: 2 }))).toBe("/operator/datasets/shop/queue?release=2");
  });
});
