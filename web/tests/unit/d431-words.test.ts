/**
 * The fixed words D431 states (round 1 of #123, m2): each state's words that SPEC.md's D431 row quotes
 * are the words the screens export, word for word, so that rewording one (the deadline's, a refusal's)
 * fails here until the row is changed with it. The words of R54's sentence (`LATEST`) and of the
 * disclosure and caveat components are pinned by their own rows' cells.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { EMPTY, FAILED as CATALOGUE_FAILED, NO_TABLE as NO_HIT_TABLE, NO_VALUE, PENDING as CATALOGUE_PENDING, REFUSED as CATALOGUE_REFUSED } from "../../src/catalogue/Catalogue";
import { DEADLINE } from "../../src/catalogue/load";
import { FAILED as DATASET_FAILED, MEMBERS_NOT_SHOWN, NO_ANALYSIS, PENDING as DATASET_PENDING, REFUSED as DATASET_REFUSED } from "../../src/dataset/Dataset";
import { NO_RELATIONSHIP, NO_TABLE as NO_GRAPH_TABLE } from "../../src/dataset/Graph";
import { NO_COLUMN, NO_TABLES } from "../../src/dataset/Tables";

const SPEC = readFileSync(path.join(import.meta.dirname, "../../../SPEC.md"), "utf8");
const ROW = SPEC.split("\n").find((line) => line.startsWith("| D431 |")) ?? "";

/** The words each export holds, by its name; the dataset's id stands as `<id>` in D431's quote. */
const WORDS: readonly (readonly [string, string])[] = [
  ["load.DEADLINE", DEADLINE],
  ["Catalogue.PENDING", CATALOGUE_PENDING],
  ["Catalogue.EMPTY", EMPTY],
  ["Catalogue.REFUSED", CATALOGUE_REFUSED],
  ["Catalogue.FAILED", CATALOGUE_FAILED],
  ["Catalogue.NO_TABLE", NO_HIT_TABLE],
  ["Catalogue.NO_VALUE", NO_VALUE],
  ["Dataset.PENDING", DATASET_PENDING],
  ["Dataset.REFUSED", DATASET_REFUSED],
  ["Dataset.FAILED", DATASET_FAILED],
  ["Dataset.NO_ANALYSIS", NO_ANALYSIS],
  ["Dataset.MEMBERS_NOT_SHOWN", `${MEMBERS_NOT_SHOWN[0]}<id>${MEMBERS_NOT_SHOWN[1]}`],
  ["Graph.NO_TABLE", NO_GRAPH_TABLE],
  ["Graph.NO_RELATIONSHIP", NO_RELATIONSHIP],
  ["Tables.NO_TABLES", NO_TABLES],
  ["Tables.NO_COLUMN", NO_COLUMN],
];

describe("the words D431 quotes are the screens' own", () => {
  it("finds D431's row", () => {
    expect(ROW.length).toBeGreaterThan(1000);
  });

  it.each(WORDS)("%s is quoted in D431, word for word", (_name, words) => {
    expect(ROW).toContain(words);
  });

  it("finds a word that was changed (the control)", () => {
    expect(ROW).not.toContain(DEADLINE.replace("45 seconds", "30 seconds"));
    expect(ROW).not.toContain(CATALOGUE_REFUSED.replace("listed", "found"));
  });
});
