/**
 * Routes (D419): a parameter is percent-encoded whole and refused if empty, `.` or `..`; a number
 * is a safe whole number's digits; a query keeps its order and leaves out what has no value; the
 * generated functions build each operation's URL from its template.
 */
import { describe, expect, it } from "vitest";

import * as routes from "../../src/api/generated/routes";
import { isRoute, REFUSED_SEGMENT, route, search, segment } from "../../src/api/route";

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
  it("build each operation's URL and method", () => {
    expect(routes.health()).toEqual({ operation: "health", method: "GET", url: "/api/health" });
    expect(routes.search_catalog()).toEqual({ operation: "search_catalog", method: "POST", url: "/api/tools/search_catalog" });
    expect(routes.descriptor({ dataset: "shop", descriptor: "orders/total" }, { release: "draft" }).url).toBe(
      "/operator/datasets/shop/descriptors/orders%2Ftotal?release=draft",
    );
    expect(routes.descriptor({ dataset: "shop", descriptor: "x" }).url).toBe("/operator/datasets/shop/descriptors/x");
    expect(routes.queue({ dataset: "shop" }, { release: 12 }).url).toBe("/operator/datasets/shop/queue?release=12");
    expect(routes.reject({ dataset: "shop", proposal: 3 })).toEqual({
      operation: "reject",
      method: "POST",
      url: "/operator/datasets/shop/proposals/3/reject",
    });
    expect(routes.take_over({ dataset: "shop" }).url).toBe("/operator/datasets/shop/session/take-over");
    expect(routes.upload({ dataset: "shop" }, { extension: ".csv" }).url).toBe("/operator/datasets/shop/uploads?extension=.csv");
  });

  it("refuse a parameter that would climb or vanish", () => {
    expect(() => routes.descriptor({ dataset: "shop", descriptor: ".." })).toThrow(REFUSED_SEGMENT);
    expect(() => routes.dataset_state({ dataset: "." })).toThrow(REFUSED_SEGMENT);
    expect(() => routes.reject({ dataset: "shop", proposal: 0.5 })).toThrow(REFUSED_SEGMENT);
    expect(() => routes.dataset_state({ dataset: "\ud800" })).toThrow(new RangeError(REFUSED_SEGMENT));
  });

  it("are recorded and frozen; a lookalike is not", () => {
    const made = routes.health();
    expect(isRoute(made)).toBe(true);
    expect(Object.isFrozen(made)).toBe(true);
    expect(isRoute({ ...made })).toBe(false);
    expect(isRoute(route("health", "GET", "/api/health"))).toBe(true);
  });
});
