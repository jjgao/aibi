/**
 * The datatypes the document seam offers (D432; minor 3 of the plan's round 3): `src/api/datatypes.ts`
 * is a module of code (a JSON module is not imported in `src/`: the build's gate refuses it), held
 * equal here to `tests/fixtures/datatypes.json`, which the server's test
 * (`server/tests/core/analyses/test_web_datatypes.py`) reads and holds to `distribution.SUMMARISED`,
 * `_CATEGORIES` and `resolve.typed_constant`. `AIBI_WRITE_WEB_FIXTURES=1` writes the fixture.
 */
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { DATATYPES, LEFT_OUT } from "../../src/api/datatypes";
import { VOCABULARIES } from "../../src/api/generated/shapes";
import { FIXTURES } from "./typed-support";

const FIXTURE = path.join(FIXTURES, "datatypes.json");

describe("the seam's datatypes (D432)", () => {
  it("are the fixture the server's test reads", () => {
    const text = `${JSON.stringify({ leaf: DATATYPES.leaf, view: DATATYPES.view, view_under_k: DATATYPES.view_under_k, left_out: LEFT_OUT }, null, 1)}\n`;
    if (process.env["AIBI_WRITE_WEB_FIXTURES"] === "1") {
      writeFileSync(FIXTURE, text);
    }
    expect(readFileSync(FIXTURE, "utf8")).toBe(text);
  });

  it("name members of ColumnBrief.datatype alone, each list sorted and without a repeat, under-k a part of the view's", () => {
    const vocabulary: readonly string[] = VOCABULARIES["ColumnBrief.datatype"];
    for (const list of [DATATYPES.leaf, DATATYPES.view, DATATYPES.view_under_k, Object.keys(LEFT_OUT)]) {
      for (const word of list) {
        expect(vocabulary).toContain(word);
      }
      expect([...list].sort()).toEqual([...list]);
      expect(new Set(list).size).toBe(list.length);
    }
    expect(DATATYPES.view_under_k.every((word) => (DATATYPES.view as readonly string[]).includes(word))).toBe(true);
    expect(Object.keys(LEFT_OUT).some((word) => (DATATYPES.leaf as readonly string[]).includes(word))).toBe(false);
  });

  it("are frozen through and through", () => {
    expect(Object.isFrozen(DATATYPES) && Object.isFrozen(DATATYPES.leaf) && Object.isFrozen(DATATYPES.view) && Object.isFrozen(DATATYPES.view_under_k) && Object.isFrozen(LEFT_OUT)).toBe(true);
  });
});
