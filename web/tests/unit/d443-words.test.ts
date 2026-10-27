/**
 * The fixed words D443 quotes are the column screen's own, word for word (as `d431-words.test.ts`
 * holds D431's): the states' words, the link back, the T8 notice, the list's and the pooled row's
 * words, the open ends', each reason's words beside its reason and each source of a histogram's
 * edges beside its member, so that rewording one, or giving a reason another's words, fails here
 * until the row is changed with it.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BACK, EDGES_WORDS, FAILED, MULTI_MEMBERSHIP, NONE_WORDS, NOT_SHOWN, OPEN, PENDING, POOLED, REFUSED } from "../../src/dataset/Column";

const SPEC = readFileSync(path.join(import.meta.dirname, "../../../SPEC.md"), "utf8");
const ROW = SPEC.split("\n").find((line) => line.startsWith("| D443 |")) ?? "";

/** The words each export holds, by its name, as D443 quotes them (in double quotes). */
const WORDS: readonly (readonly [string, string])[] = [
  ["Column.PENDING", PENDING],
  ["Column.REFUSED", REFUSED],
  ["Column.FAILED", FAILED],
  ["Column.BACK", BACK],
  ["Column.NOT_SHOWN", NOT_SHOWN],
  ["Column.MULTI_MEMBERSHIP", MULTI_MEMBERSHIP],
  ["Column.POOLED", POOLED],
  ["Column.OPEN.low", OPEN.low],
  ["Column.OPEN.high", OPEN.high],
];

describe("the words D443 quotes are the column screen's own", () => {
  it("finds D443's row", () => {
    expect(ROW.length).toBeGreaterThan(1000);
  });

  it.each(WORDS)("%s is quoted in D443, word for word", (_name, words) => {
    expect(ROW).toContain(`"${words}"`);
  });

  it.each(Object.entries(NONE_WORDS))("the reason %s is quoted beside its own words", (reason, words) => {
    expect(ROW).toContain(`\`${reason}\` "${words}"`);
  });

  it.each(Object.entries(EDGES_WORDS))("the edges' source %s is quoted beside its own words", (source, words) => {
    expect(ROW).toContain(`(\`${source}\`: "${words}")`);
  });

  it("finds a word that was changed and a pair that was swapped (the controls)", () => {
    expect(ROW).not.toContain(`"${NOT_SHOWN.replace("whole, or", "whole, and")}"`);
    expect(ROW).not.toContain(`\`out_of_range\` "${NONE_WORDS.empty}"`);
    expect(ROW).not.toContain(`(\`range\`: "${EDGES_WORDS.data}")`);
  });
});
