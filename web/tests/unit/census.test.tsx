/**
 * The census of the result screen's words (D435's §4b, B7.9), its static half: the words of every
 * module `Result` and `Parameters` reach and of each constant imported through the api's index
 * (`census-words.ts`, from the TypeScript program, as stated there and no more: a private constant, a
 * table, a `.ts` module, a shown attribute, a call that gives a list of strings), less the reviewed
 * exemptions, equal the census's keys (`census.ts`), both ways; each live row's cell holds on every
 * live result where its value occurs, which it must at least once. The runtime half, over every body the sweep makes, is `result-sweep.test.tsx`'s.
 *
 * The mutants: a sentence added to `Values.tsx` as an exported word, as a module-private constant, in
 * a new `.ts` module, as a `title`, and as a private `as const` table in `Analysed.tsx` (round 1's
 * c1–c4), as an `aria-description`, through a call that gives its argument back, and `NOT_READ` so
 * spelled in `format.tsx` (round 2's n1b, n5, n8), a constant of another api module imported through
 * the index, and a word through `Array.of` (round 3's t1b, t2); r3's gloss of `other_values` put back; the `Disclosure` words before B10: each fails the
 * equality. A live cell turned against its claim fails on the live answers.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { check, WEB } from "../gates/program";
import { CENSUS, EXEMPT, type Live, RUNTIME_ONLY } from "./census";
import { censusModules, censusProgram, enumerated, ROOTS } from "./census-words";
import { liveBody, liveResults, shownOf } from "./result-support";

/** The page words of an enumeration: every word, less the exemptions of its own module. */
function wordsOf(words: Map<string, string[]>): { readonly words: string[]; readonly unused: string[] } {
  const used = new Set<string>();
  const found = new Set<string>();
  for (const [module, list] of words) {
    for (const word of list) {
      const exemption = EXEMPT.find(([at, text]) => at === module && text === word);
      if (exemption === undefined) {
        found.add(word);
      } else {
        used.add(`${module}: ${word}`);
      }
    }
  }
  return { words: [...found].sort(), unused: EXEMPT.map(([module, word]) => `${module}: ${word}`).filter((one) => !used.has(one)) };
}

const STATIC_KEYS = Object.keys(CENSUS)
  .filter((key) => !Object.hasOwn(RUNTIME_ONLY, key))
  .sort();

/** The difference of the enumeration and the census, both ways. */
function difference(words: readonly string[]): { readonly unlisted: string[]; readonly stale: string[] } {
  return { unlisted: words.filter((word) => !STATIC_KEYS.includes(word)), stale: STATIC_KEYS.filter((key) => !words.includes(key)) };
}

/** The enumeration with modules' texts replaced or added. */
function withTexts(files: Readonly<Record<string, string | ((text: string) => string)>>): string[] {
  const texts = Object.fromEntries(Object.entries(files).map(([module, change]) => [module, typeof change === "string" ? change : change(readFileSync(path.join(WEB, module), "utf8"))]));
  const roots = Object.fromEntries(ROOTS.map((root) => [root, readFileSync(path.join(WEB, root), "utf8")]));
  return wordsOf(enumerated(check({ ...roots, ...texts }).program)).words;
}

/** Every live result, rendered once: its own result section. */
let shown: Promise<{ readonly result: ReturnType<typeof liveResults>[number]["result"]; readonly section: Element }[]> | undefined;
function sections(): NonNullable<typeof shown> {
  shown ??= (async () => {
    const out = [];
    for (const { index, result } of liveResults()) {
      const host = await shownOf(liveBody(index));
      const section = host.querySelectorAll('section[aria-label="result"]')[result.at];
      if (section === undefined) {
        throw new Error(`no section for answer ${String(index)} result ${String(result.at)}`);
      }
      out.push({ result, section });
    }
    return out;
  })();
  return shown;
}

const MERGED = "These values were merged by the disclosure setting.";

describe("the census of the result screen's words (B7.9)", () => {
  it("reads every module Result and Parameters reach inside src/, and past the api's index each rendering value's module", () => {
    expect(censusModules(censusProgram())).toEqual([
      "src/api/format.tsx",
      "src/api/text.tsx",
      "src/layout/Caveat.tsx",
      "src/layout/Disclosure.tsx",
      "src/layout/Figure.tsx",
      "src/layout/Population.tsx",
      "src/layout/Proportion.tsx",
      "src/layout/Readback.tsx",
      "src/layout/ReleaseRef.tsx",
      "src/layout/Segments.tsx",
      "src/layout/Text.tsx",
      "src/layout/form.ts",
      "src/layout/key.ts",
      "src/result/Analysed.tsx",
      "src/result/Charts.tsx",
      "src/result/Figures.tsx",
      "src/result/Provenance.tsx",
      "src/result/Values.tsx",
    ]);
  });

  it("every word of those modules, less the reviewed exemptions, is a key of the census, every key a word of theirs or a runtime-only word, and no exemption is stale", () => {
    const found = wordsOf(enumerated());
    expect(difference(found.words)).toEqual({ unlisted: [], stale: [] });
    expect(found.unused).toEqual([]);
  });

  it("each live row's value occurs on a live result, and its cell holds on every live result where it occurs", { timeout: 120_000 }, async () => {
    const all = await sections();
    expect(all.length).toBeGreaterThanOrEqual(13);
    const live = Object.entries(CENSUS).filter((entry): entry is [string, Live] => entry[1].held === "live");
    expect(live.length).toBeGreaterThanOrEqual(10);
    for (const [word, row] of live) {
      const where = all.filter(({ result }) => row.occurs(result));
      expect(where.length, word).toBeGreaterThan(0);
      for (const { result, section } of where) {
        expect(row.holds(result, section), `${word} on answer ${String(result.index)}`).toEqual([]);
      }
    }
  });

  it("mutants: an unlisted sentence in any spelling (c1–c4, t1b and t2 among them), r3's other_values gloss and the Disclosure words before B10 each fail the equality", { timeout: 120_000 }, () => {
    const values = "src/result/Values.tsx";
    const exported = withTexts({ [values]: (text) => text.replace('  noLow: "no lower edge",', `  noLow: "no lower edge",\n  merged: "${MERGED}",`) });
    expect(difference(exported).unlisted).toEqual([MERGED]);
    // c1: a module-private constant, rendered.
    const c1 = withTexts({ [values]: (text) => text.replace("export const VALUE_WORDS = {", `const MERGED = "${MERGED}";\n\nexport const VALUE_WORDS = {`).replace("{VALUE_WORDS.empty}</p>", "{VALUE_WORDS.empty} {MERGED}</p>") });
    expect(difference(c1).unlisted).toEqual([MERGED]);
    // c2: exported from a new `.ts` module and rendered.
    const c2 = withTexts({
      "src/result/words.ts": `export const EXTRA = "${MERGED}";\n`,
      [values]: (text) => text.replace('import { Ref, Text } from "../layout/Text";', 'import { Ref, Text } from "../layout/Text";\nimport { EXTRA } from "./words";').replace("{VALUE_WORDS.empty}</p>", "{VALUE_WORDS.empty} {EXTRA}</p>"),
    });
    expect(difference(c2).unlisted).toEqual([MERGED]);
    // A word behind a re-export (`export *`, `export … from`).
    for (const reexport of ['export * from "./words2";', 'export { EXTRA } from "./words2";']) {
      const through = withTexts({
        "src/result/words2.ts": `export const EXTRA = "${MERGED}";\n`,
        "src/result/words.ts": `${reexport}\n`,
        [values]: (text) => text.replace('import { Ref, Text } from "../layout/Text";', 'import { Ref, Text } from "../layout/Text";\nimport { EXTRA } from "./words";').replace("{VALUE_WORDS.empty}</p>", "{VALUE_WORDS.empty} {EXTRA}</p>"),
      });
      expect(difference(through).unlisted, reexport).toEqual([MERGED]);
    }
    // c3: a title attribute.
    const c3 = withTexts({ [values]: (text) => text.replace('<div role="list" className="categories">', `<div role="list" className="categories" title="${MERGED}">`) });
    expect(difference(c3).unlisted).toEqual([MERGED]);
    // c4: a private as-const table.
    const c4 = withTexts({ "src/result/Analysed.tsx": (text) => text.replace("export const ANALYSED_WORDS = {", `const MORE = { gloss: "${MERGED}" } as const;\nvoid MORE;\n\nexport const ANALYSED_WORDS = {`) });
    expect(difference(c4).unlisted).toEqual([MERGED]);
    // n1b: an aria attribute of any name; n5: a word through a call that gives it back; n8: NOT_READ
    // spelled through such a call in format.tsx.
    const n1b = withTexts({ [values]: (text) => text.replace('<div className="values" aria-label="values">', `<div className="values" aria-label="values" aria-description="${MERGED}">`) });
    expect(difference(n1b).unlisted).toEqual([MERGED]);
    const n5 = withTexts({ [values]: (text) => text.replace("export const VALUE_WORDS = {", "const say = (word: string): string => word;\n\nexport const VALUE_WORDS = {").replace("{VALUE_WORDS.empty}</p>", `{VALUE_WORDS.empty} <bdi className="data">{say("${MERGED}")}</bdi></p>`) });
    expect(difference(n5).unlisted).toEqual([MERGED]);
    const n8 = withTexts({ "src/api/format.tsx": (text) => text.replace('export const NOT_READ = "not shown: not a number this page reads";', 'const say = (word: string): string => word;\nexport const NOT_READ = say("not shown: the number is not read");') });
    expect(difference(n8).unlisted).toEqual(["not shown: the number is not read"]);
    // Round 3's t1b: a constant of another api module, imported through the index; t2: a word through
    // a call that gives a list of strings.
    const t1b = withTexts({
      [values]: (text) =>
        text
          .replace('import { type Boxed, type ColumnRead, type components, type Kind, Num, type ServerNumber, type ServerText } from "../api";', 'import { type Boxed, type ColumnRead, type components, type Kind, Num, type ServerNumber, type ServerText, SHAPE_MISMATCH } from "../api";')
          .replace("{VALUE_WORDS.empty}</p>", "{VALUE_WORDS.empty} {SHAPE_MISMATCH}</p>"),
    });
    expect(difference(t1b).unlisted).toEqual(["The page and the server disagree about the data's shape; reload."]);
    const t2 = withTexts({ [values]: (text) => text.replace("{VALUE_WORDS.empty}</p>", `{VALUE_WORDS.empty} {Array.of<"${MERGED}">("${MERGED}")}</p>`) });
    expect(difference(t2).unlisted).toEqual([MERGED]);
    const gloss = withTexts({ [values]: (text) => text.replace('<span className="member">other_values</span>', '<span className="member">a row that also holds values it does not name</span>') });
    expect(difference(gloss).unlisted).toEqual(["a row that also holds values it does not name"]);
    const landed = withTexts({ "src/layout/Disclosure.tsx": (text) => text.replace("counts of units from 1 to k − 1, and counts", "counts below it, and counts") });
    expect(difference(landed).unlisted).toEqual([": counts below it, and counts that would reveal them, are suppressed (§8.4)."]);
  });

  it("a live cell turned against its claim fails: the k sentence's cell with k raised above the live counts", async () => {
    const row = CENSUS[": counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4)."];
    if (row?.held !== "live") {
      throw new Error("not live");
    }
    const floored = (await sections()).filter(({ result }) => result.k !== null);
    expect(floored.some(({ result, section }) => row.holds({ ...result, k: 1000 }, section).length > 0)).toBe(true);
  });
});
