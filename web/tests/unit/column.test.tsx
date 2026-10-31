/**
 * The column's screen (D443; D427's R67) over the server's own answers (the `columns` section of
 * `tests/fixtures/orchard-answers.json`, D442's ten and D443's four): the four observation states and
 * the table's rows, each a count with its `stat:` reference, a suppressed count as suppressed, never
 * 0; the categories (each value, data, as text; a list's memberships said; the pooled row apart from
 * the categories, its count or suppressed); the histograms (every bin's ends, open or closed as the
 * answer says, an absent edge as an open end's words, numeric and date edges, the edges' source in
 * words, merged bins under a floor with no smallest or largest value, and both shown without one);
 * and the reason a distribution is not given, each reason's words held as a pair. The heading: the
 * answer's own id through `Ref` and its label through `Text`, whatever the label holds. The fixed
 * notice (T8) on every answer, once and seen, the fixture's claim it states (an answer under a floor
 * carries no curation evidence), and the class of what it says the screen shows of the descriptor,
 * held over the whole domain: a synthetic descriptor built from the generated schema (every member,
 * every number, every option of every site of an alternative, each site chosen of its own, the record
 * of each variant checked populated) put into one recorded answer of each screen path, and the recorded
 * descriptors' own values in every recorded answer, each on the screen (all of it but the heading's id
 * and label and the notice, the caveats and the meta line read, each text node alone) only where an
 * allow-list gives the reason, and every entry used. The column census: every word of `Column.tsx`,
 * classified. The loader's states are `column-states.test.tsx`'s (this file names no hook, D427).
 */
import { describe, expect, it } from "vitest";

import { boxMap, boxText, readText } from "../../src/api/box";
import type { Answered } from "../../src/api/typed";
import { SHAPES } from "../../src/api/generated/shapes";
import { BACK, ColumnDescription, EDGES_WORDS, FAILED, MULTI_MEMBERSHIP, NONE_WORDS, NOT_SHOWN, OPEN, PENDING, POOLED, REFUSED, STATE_WORDS } from "../../src/dataset/Column";
import { SEVERITY_WORDS } from "../../src/layout/Caveat";
import { NO_SETTING } from "../../src/layout/Disclosure";
import { walked } from "./answers";
import { censusModules, censusProgram, enumerated } from "./census-words";
import { correlated, gaps, membersOf, siteCount, type Synthetic, variants } from "./descriptor-schema";
import { HOSTILE, leafSweep } from "./injection";
import { render } from "./render";
import { answerAt, ORCHARD, SECTIONS } from "./typed-support";

type Described = Answered<"describe_column">;

/** The recorded answers of the `columns` section, by name (`_COLUMNS_ASKED`, then D443's four). */
const PLACES = {
  variety: 0,
  height: 1,
  plantedData: 2,
  tags: 3,
  treeId: 4,
  kg: 5,
  heightK5: 6,
  varietyK50: 7,
  hostile: 8,
  varietyK5: 9,
  varietyK14: 10,
  tagsK21: 11,
  plantedRangeK5: 12,
  plantedRange: 13,
} as const;

type Place = keyof typeof PLACES;

/** The text of a recorded answer. */
function textOf(place: Place): string {
  const found = ORCHARD[answerAt("columns", PLACES[place])];
  if (found?.operation !== "describe_column" || found.status !== 200) {
    throw new Error(`no describe_column answer at ${place}`);
  }
  return found.body;
}

const of = (text: string): Described => walked("ColumnDescription", text) as Described;

/** A recorded answer as raw JSON, changed by `change`, walked. */
function changed(place: Place, change: (raw: Record<string, unknown>) => void): Described {
  const raw = JSON.parse(textOf(place)) as Record<string, unknown>;
  change(raw);
  return of(JSON.stringify(raw));
}

/** The manifests of the two releases the section records. */
const R1 = "stat:sha256:a428da8595c242e1695d446104ed50e11444a8fa16e086e54438b468b45f19fa";
const R2 = "stat:sha256:06a2337b818584f1ca7d788dcdd3de707cac3b530216a710a129fcfa45df1c88";

interface Shown {
  readonly words: string;
  readonly states: string[];
  readonly categories: string[];
  readonly bins: string[];
  readonly pooled: string[];
  readonly caveats: string[];
  readonly html: string;
  readonly container: Element;
}

async function shown(answer: Described): Promise<Shown> {
  const found = await render(<ColumnDescription answer={answer} />);
  const items = (label: string): string[] => [...found.container.querySelectorAll(`[aria-label=${label}] [role=listitem]`)].map((item) => item.textContent);
  const result = {
    words: found.container.textContent,
    states: [found.container.querySelector(".states > p")?.textContent ?? "", ...items("states")],
    categories: items("categories"),
    bins: items("bins"),
    pooled: [...found.container.querySelectorAll(".pooled")].map((one) => one.textContent),
    caveats: items("caveats"),
    html: found.container.innerHTML,
    container: found.container.cloneNode(true) as Element,
  };
  found.unmount();
  return result;
}

const showing = async (place: Place): Promise<Shown> => shown(of(textOf(place)));

/** Each reason's words, as a literal table: a pair swapped fails here (D443 quotes them). */
const PAIRS: readonly (readonly [string, string])[] = [
  ["identifier", "an identifier has no value distribution"],
  ["text", "free text is not counted as categories"],
  ["undeclared", "the column has no declared datatype"],
  ["categories", "it has more distinct values than the categories counted"],
  ["out_of_range", "its values or its declared range are beyond what is counted exactly"],
  ["empty", "it has neither a declared range nor a present value"],
  ["no_declared_range", "under the disclosure setting a histogram's edges come from a declared range alone, and the column declares none"],
  ["suppressed", "suppressed under the disclosure setting"],
  ["unrepresentable", "a value cannot be carried in an answer"],
];

describe("ColumnDescription (R67), over the recorded answers", () => {
  it("R67: the rows and the four states, each a count with its reference; the categories, each value as text with its count and reference", async () => {
    expect.hasAssertions();
    const variety = await showing("variety");
    expect(variety.states).toEqual([
      `Rows: 40 ${R1}/trees/n_rows`,
      `PRESENT: 40 ${R1}/trees.variety/states/PRESENT`,
      `NOT_APPLICABLE: 0 ${R1}/trees.variety/states/NOT_APPLICABLE`,
      `NOT_ASSESSED: 0 ${R1}/trees.variety/states/NOT_ASSESSED`,
      `UNKNOWN (no usable value): 0 ${R1}/trees.variety/states/UNKNOWN`,
    ]);
    expect(variety.categories).toEqual([`apple: 13 ${R1}/trees.variety/categories/apple`, `pear: 14 ${R1}/trees.variety/categories/pear`, `plum: 13 ${R1}/trees.variety/categories/plum`]);
    expect(variety.pooled).toEqual([]);
    expect(variety.bins).toEqual([]);
    expect(variety.words).toContain("Column trees.variety (variety)Table trees of the dataset orchard; release @1 · manifest");
    expect(variety.words).not.toContain(MULTI_MEMBERSHIP);
    const tags = await showing("tags");
    expect(tags.words).toContain(`Categories${MULTI_MEMBERSHIP}old: 20`);
    expect(tags.categories).toEqual([`old: 20 ${R1}/trees.tags/categories/old`, `tall: 20 ${R1}/trees.tags/categories/tall`, `young: 20 ${R1}/trees.tags/categories/young`]);
  });

  it("R67: histogram bins with their open and inclusive ends (an absent edge as the open end's words, never 0), numeric and date edges, the edges' source, the smallest and largest values with their references", async () => {
    expect.hasAssertions();
    const height = await showing("height");
    expect(height.bins).toHaveLength(12);
    expect(height.bins.slice(0, 3)).toEqual([`(${OPEN.low}, 1.25): 0 ${R1}/trees.height_m/bins/0`, `[1.25, 2.225): 4 ${R1}/trees.height_m/bins/1`, `[2.225, 3.2): 3 ${R1}/trees.height_m/bins/2`]);
    expect(height.bins.slice(-2)).toEqual([`[10.025, 11.0]: 4 ${R1}/trees.height_m/bins/10`, `(11.0, ${OPEN.high}): 0 ${R1}/trees.height_m/bins/11`]);
    expect(height.words).toContain(`HistogramEdges from ${EDGES_WORDS.data}.`);
    expect(height.words).toContain(`Smallest value: 1.25 ${R1}/trees.height_m/minLargest value: 11.0 ${R1}/trees.height_m/max`);
    const planted = await showing("plantedData");
    expect(planted.bins[1]).toBe(`[2010-01-09, 2010-02-12): 5 ${R1}/trees.planted/bins/1`);
    expect(planted.bins.at(-1)).toBe(`(2010-12-24, ${OPEN.high}): 0 ${R1}/trees.planted/bins/11`);
    expect(planted.words).toContain(`Smallest value: 2010-01-09 ${R1}/trees.planted/minLargest value: 2010-12-24 ${R1}/trees.planted/max`);
    // Another table's column is shown alike: its own table, never looked up.
    const kg = await showing("kg");
    expect(kg.words).toContain("Column harvests.kg (kg)Table harvests of the dataset orchard");
    expect(kg.bins[5]).toBe(`[13.2, 14.0): 0 ${R1}/harvests.kg/bins/5`);
  });

  it("R67: under a floor, bins from the declared range alone, merged, with no minimum or maximum; without one, the range's bins and both", async () => {
    expect.hasAssertions();
    const floored = await showing("plantedRangeK5");
    expect(floored.words).toContain(`Edges from ${EDGES_WORDS.range}.`);
    expect(floored.bins).toEqual([
      `(${OPEN.low}, 2010-01-01): 0 ${R2}/trees.planted/bins/0?floor=5`,
      `[2010-01-01, 2010-03-15): 9 ${R2}/trees.planted/bins/1?floor=5`,
      `[2010-03-15, 2010-04-20): 5 ${R2}/trees.planted/bins/2?floor=5`,
      `[2010-04-20, 2010-05-27): 5 ${R2}/trees.planted/bins/3?floor=5`,
      `[2010-05-27, 2010-08-08): 7 ${R2}/trees.planted/bins/4?floor=5`,
      `[2010-08-08, 2010-10-20): 7 ${R2}/trees.planted/bins/5?floor=5`,
      `[2010-10-20, 2011-01-01]: 7 ${R2}/trees.planted/bins/6?floor=5`,
      `(2011-01-01, ${OPEN.high}): 0 ${R2}/trees.planted/bins/7?floor=5`,
    ]);
    expect(floored.words).not.toContain("Smallest value");
    expect(floored.words).not.toContain("Largest value");
    expect(floored.words).toContain("release @2");
    const ranged = await showing("plantedRange");
    expect(ranged.words).toContain(`Edges from ${EDGES_WORDS.range}.`);
    expect(ranged.bins).toHaveLength(12);
    expect(ranged.bins[1]).toBe(`[2010-01-01, 2010-02-06): 4 ${R2}/trees.planted/bins/1`);
    expect(ranged.words).toContain(`Smallest value: 2010-01-09 ${R2}/trees.planted/minLargest value: 2010-12-24 ${R2}/trees.planted/max`);
  });

  it("R67: the pooled suppressed-categories row, apart from the categories: its count with its reference, or suppressed (a list's), never 0 and never a category", async () => {
    expect.hasAssertions();
    const pooled = await showing("varietyK14");
    expect(pooled.categories).toEqual([`pear: 14 ${R1}/trees.variety/categories/pear?floor=14`]);
    expect(pooled.pooled).toEqual([`${POOLED} 26 ${R1}/trees.variety/pooled?floor=14`]);
    expect(pooled.words).not.toContain("apple");
    const hidden = await showing("tagsK21");
    expect(hidden.categories).toEqual([]);
    expect(hidden.pooled).toEqual([`${POOLED} suppressed ${R1}/trees.tags/pooled?floor=21`]);
    expect(hidden.container.querySelector(".pooled .suppressed")?.textContent).toBe("suppressed");
    expect(hidden.words).toContain(MULTI_MEMBERSHIP);
    const floored = await showing("varietyK5");
    expect(floored.categories).toEqual([`apple: 13 ${R1}/trees.variety/categories/apple?floor=5`, `pear: 14 ${R1}/trees.variety/categories/pear?floor=5`, `plum: 13 ${R1}/trees.variety/categories/plum?floor=5`]);
    expect(floored.pooled).toEqual([]);
  });

  it("R67: the reason a distribution is not given, and suppressed counts as suppressed under a floor", async () => {
    expect.hasAssertions();
    expect((await showing("treeId")).words).toContain(`No distribution: ${NONE_WORDS.identifier}.`);
    expect((await showing("heightK5")).words).toContain(`No distribution: ${NONE_WORDS.no_declared_range}.`);
    const floored = await showing("varietyK50");
    expect(floored.words).toContain(`No distribution: ${NONE_WORDS.suppressed}.`);
    expect(floored.states).toEqual([
      `Rows: suppressed ${R1}/trees/n_rows?floor=50`,
      `PRESENT: suppressed ${R1}/trees.variety/states/PRESENT?floor=50`,
      `NOT_APPLICABLE: suppressed ${R1}/trees.variety/states/NOT_APPLICABLE?floor=50`,
      `NOT_ASSESSED: suppressed ${R1}/trees.variety/states/NOT_ASSESSED?floor=50`,
      `UNKNOWN (no usable value): suppressed ${R1}/trees.variety/states/UNKNOWN?floor=50`,
    ]);
    expect(floored.html).not.toMatch(/>0</u);
  });

  it("R67: every reason's own words, held as pairs (a reason given another's words fails), and the table is the vocabulary's", async () => {
    expect.hasAssertions();
    expect(Object.entries(NONE_WORDS)).toEqual(PAIRS);
    for (const [reason, words] of PAIRS) {
      const answer = changed("treeId", (raw) => {
        Object.assign((raw["statistics"] as Record<string, Record<string, unknown>>)["distribution"] ?? {}, { reason });
      });
      const found = await shown(answer);
      expect(found.words, reason).toContain(`No distribution: ${words}.`);
      expect(PAIRS.filter(([other]) => other !== reason).some(([, others]) => found.words.includes(`No distribution: ${others}.`)), reason).toBe(false);
    }
    // A reason the vocabulary does not hold is refused by the walk (`NoDistribution.reason` is a
    // closed enum): the screen never meets one; the sweep's vocabulary position shows a forged one as text.
    expect(() => changed("treeId", (raw) => Object.assign((raw["statistics"] as Record<string, Record<string, unknown>>)["distribution"] ?? {}, { reason: "fresh" }))).toThrow();
  });

  it("R67: a null category count and the pooled row's reason are shown through StatCount, a reason the page does not know as text (the positions no sweep plan reaches)", async () => {
    expect.hasAssertions();
    const recorded = of(textOf("tagsK21"));
    const distribution = recorded.statistics.distribution;
    if (!("categories" in distribution) || distribution.pooled === undefined) {
      throw new Error("tags under 21 has categories and a pooled row");
    }
    for (const row of HOSTILE.slice(0, 10)) {
      const reasons = boxMap([[boxText("/count"), boxText(row.text)]] as const);
      const one = { count: null, reference: boxText(`${R1}/trees.tags/categories/x`), value: { data: boxText("x") }, not_estimable: reasons };
      const answer = { ...recorded, statistics: { ...recorded.statistics, distribution: { ...distribution, categories: [one], pooled: { ...distribution.pooled, not_estimable: reasons } } } } as unknown as Described;
      const found = await shown(answer);
      expect(found.categories, row.name).toEqual([`x: not estimable: ${found.container.querySelector("[aria-label=categories] .not-estimable bdi")?.textContent ?? "?"} ${R1}/trees.tags/categories/x`]);
      expect(found.container.querySelectorAll(".not-estimable")).toHaveLength(2);
      expect(found.container.querySelectorAll("img, script, b")).toHaveLength(0);
    }
  });
});

describe("ColumnDescription: the heading and data as text", () => {
  const LABELS = ["trees.variety", `${"L".repeat(4095)}‮`, "‮txt.exe", "<img src=x onerror=alert(1)></bdi><script>alert(1)</script>", "   ", "", "x) Column evil (‮y", "javascript:alert(1)"];

  it.each(LABELS.map((label) => [label.slice(0, 30), label]))("the label %j: the page's own parentheses around one isolate of it, after the answer's id", async (_name, label) => {
    const found = await render(<ColumnDescription answer={changed("variety", (raw) => Object.assign(raw, { label: { data: label } }))} />);
    const heading = found.container.querySelector("h1");
    const children = [...(heading?.childNodes ?? [])].map((node) => (node.nodeType === Node.TEXT_NODE ? `text ${node.textContent ?? ""}` : node.nodeName));
    expect(children).toEqual(["text Column ", "BDI", "text  (", "BDI", "text )"]);
    expect(heading?.querySelectorAll("bdi")[0]?.textContent).toBe("trees.variety");
    expect(found.container.querySelectorAll("img, script, b, a")).toHaveLength(0);
    found.unmount();
  });

  it("hostile category values (recorded) as plain text: never markup, the long one cut", async () => {
    const hostile = await showing("hostile");
    expect(hostile.container.querySelectorAll("img, script, b, a")).toHaveLength(0);
    expect(hostile.words).toContain("</bdi><b>bold</b><bdi>: 4");
    expect(hostile.words).toContain("javascript:alert(1): 4");
    expect(hostile.container.querySelectorAll("[aria-label=categories] .cut")).toHaveLength(1);
    expect(hostile.words).toContain("Column items.kind (<img src=x onerror=alert(1)>");
  });

  it("hostile bin edges, smallest and largest values as plain text", { timeout: 120_000 }, async () => {
    const { problems, leaves } = await leafSweep(
      of(textOf("plantedRange")),
      (answer: Described) => <ColumnDescription answer={answer} />,
      ["H1", "H2", "P", "SECTION", "DIV"],
      ["", "stat", "suppressed", "not-estimable", "not-shown", "release", "label", "label draft", "disagree", "number unread", "affects"],
      HOSTILE.slice(0, 12),
    );
    expect(leaves).toBeGreaterThan(30);
    expect(problems).toEqual([]);
  });

  it("hostile category values swept at every leaf as plain text", { timeout: 120_000 }, async () => {
    const { problems } = await leafSweep(of(textOf("varietyK14")), (answer: Described) => <ColumnDescription answer={answer} />, ["H1", "H2", "P", "SECTION", "DIV"], ["", "stat", "suppressed", "not-estimable", "not-shown", "release", "label", "label draft", "disagree", "number unread", "affects"], HOSTILE.slice(0, 12));
    expect(problems).toEqual([]);
  });
});

/** What a recorded answer says, as raw JSON: the members the cells below read. */
interface Raw {
  readonly column: string;
  readonly label: { readonly data: string };
  readonly disclosure: { readonly min_cell_count: number | null };
  readonly caveats: readonly { readonly code: string; readonly severity: keyof typeof SEVERITY_WORDS; readonly message: readonly ({ readonly text: string } | { readonly data: string })[] }[];
  readonly descriptor: Record<string, unknown>;
  readonly statistics: {
    readonly rows: { readonly count: number | null; readonly reference: string };
    readonly states: Record<"PRESENT" | "NOT_APPLICABLE" | "NOT_ASSESSED" | "UNKNOWN", { readonly count: number | null; readonly reference: string }>;
    readonly distribution: {
      readonly categories?: unknown;
      readonly multi_membership?: boolean;
      readonly pooled?: unknown;
      readonly bins?: readonly { readonly low?: unknown; readonly high?: unknown }[];
      readonly edges_from?: string;
      readonly reason?: string;
    };
  };
}

/** Every `describe_column` answer of the `columns` section, as raw JSON, by its place in it (the file's
 * own count: an answer appended to the section is held by the cells below without a line added). */
const RECORDED: readonly Raw[] = ORCHARD.slice(SECTIONS.columns.start, SECTIONS.columns.end).map((found) => {
  if (found.operation !== "describe_column" || found.status !== 200) {
    throw new Error("the columns section holds a describe_column 200 alone");
  }
  return JSON.parse(found.body) as Raw;
});

describe("every recorded describe_column answer, read from its raw text", () => {
  const STATES = ["PRESENT", "NOT_APPLICABLE", "NOT_ASSESSED", "UNKNOWN"] as const;
  const WORDS: Readonly<Record<(typeof STATES)[number], string>> = STATE_WORDS;
  const counted = (value: { readonly count: number | null; readonly reference: string }): string => `${value.count === null ? "suppressed" : String(value.count)} ${value.reference}`;

  it("holds the file's count, the section's, and the places this file names", () => {
    expect(RECORDED).toHaveLength(Object.keys(PLACES).length);
    expect(RECORDED.length).toBeGreaterThanOrEqual(14);
  });

  it("R67: the rows and the four states, each with its own count and reference in the answer's order; the list words exactly when the answer says a list; the disclosure as the answer says it", async () => {
    expect.hasAssertions();
    for (const [at, raw] of RECORDED.entries()) {
      const found = await shown(of(JSON.stringify(raw)));
      expect(found.states, String(at)).toEqual([`Rows: ${counted(raw.statistics.rows)}`, ...STATES.map((state) => `${WORDS[state]}: ${counted(raw.statistics.states[state])}`)]);
      const list = "categories" in raw.statistics.distribution && raw.statistics.distribution.multi_membership === true;
      expect(found.words.includes(MULTI_MEMBERSHIP), `${String(at)}: ${raw.column}`).toBe(list);
      const setting = raw.disclosure.min_cell_count;
      expect(found.container.querySelector(".disclosure")?.textContent, String(at)).toBe(
        setting === null ? NO_SETTING : `Disclosure: minimum cell count k = ${String(setting)}: counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).`,
      );
      // Every caveat the answer carries, in the answer's order, each with its code and its severity's words
      // (an answer under a floor carries two: the one that cuts them to one fails here).
      expect(
        found.caveats.map((one) => one.slice(0, one.indexOf("):") + 1)),
        String(at),
      ).toEqual(raw.caveats.map((caveat) => `${caveat.code} (${SEVERITY_WORDS[caveat.severity]})`));
    }
    expect(RECORDED.some((raw) => raw.caveats.length > 1)).toBe(true);
    // The cells above hold what the section records: a state other than PRESENT that is not 0, a list
    // column's pooled row, a setting and none.
    const others = RECORDED.flatMap((raw) => [raw.statistics.states.NOT_APPLICABLE, raw.statistics.states.NOT_ASSESSED, raw.statistics.states.UNKNOWN].map((one) => one.count));
    expect(others.some((count) => count !== null && count > 0)).toBe(true);
    expect(RECORDED.some((raw) => raw.statistics.distribution.multi_membership === true)).toBe(true);
    expect(RECORDED.some((raw) => raw.statistics.distribution.multi_membership === false)).toBe(true);
    expect(RECORDED.some((raw) => raw.disclosure.min_cell_count === null)).toBe(true);
    expect(RECORDED.some((raw) => raw.disclosure.min_cell_count !== null)).toBe(true);
  });
});

describe("the fixed notice (T8, D443)", () => {
  it("R67: says what the screen shows and that the descriptor is not shown, once on every recorded answer, seen", async () => {
    expect.hasAssertions();
    for (const place of Object.keys(PLACES) as Place[]) {
      const found = await render(<ColumnDescription answer={of(textOf(place))} />);
      const notices = [...found.container.querySelectorAll(".legend")].map((one) => one.textContent);
      expect(notices, place).toEqual([NOT_SHOWN]);
      expect(found.container.textContent.split(NOT_SHOWN).length - 1, place).toBe(1);
      for (let at: Element | null = found.container.querySelector(".legend"); at !== null; at = at.parentElement) {
        expect(getComputedStyle(at).display, place).not.toBe("none");
        expect(at.hasAttribute("hidden"), place).toBe(false);
      }
      found.unmount();
    }
    expect(NOT_SHOWN).toMatch(/^The values of the column's descriptor are not shown on this page yet\. /u);
  });

  it("states what the fixture holds: an answer under a floor carries no curation evidence, every other one some", () => {
    const evidence = (Object.keys(PLACES) as Place[]).map((place) => {
      const raw = JSON.parse(textOf(place)) as { disclosure: { min_cell_count: number | null }; descriptor: { curation?: Record<string, Record<string, unknown>> } };
      return { place, floored: raw.disclosure.min_cell_count !== null, held: Object.values(raw.descriptor.curation ?? {}).some((member) => Object.hasOwn(member, "evidence")) };
    });
    expect(evidence.filter(({ floored }) => floored).length).toBe(6);
    for (const { place, floored, held } of evidence) {
      expect(held, place).toBe(!floored);
    }
    expect(NOT_SHOWN).toContain("under a disclosure setting, without its curation evidence");
  });
});

/** The text nodes under `root`, in order. */
function textNodesOf(root: Node): string[] {
  return [...root.childNodes].flatMap((child) => (child instanceof Text ? [child.data] : textNodesOf(child)));
}

/** The screen as the class cells read it: its text nodes, each held alone (a value shown right after another
 * element's text is not glued to it), the whole screen but two regions, each asserted present and each why:
 * the heading's id and label, which are the answer's own `column` and `label` (D442 makes them the descriptor's
 * `/id` and `/label`) and are shown on purpose, and the notice, which names the descriptor's members in words
 * and is held word for word by its own cells. The caveats, the meta line, the states, the distribution and the
 * link back are read like the rest. */
function nodesOf(found: Shown): string[] {
  const clone = found.container.cloneNode(true) as Element;
  const heading = [...clone.querySelectorAll("h1 > bdi")];
  const notice = [...clone.querySelectorAll(".legend")];
  expect(heading, "the heading's id and label").toHaveLength(2);
  expect(notice, "the notice").toHaveLength(1);
  for (const one of [...heading, ...notice]) {
    one.remove();
  }
  return textNodesOf(clone);
}

const escaped = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");

/** Whether `value` stands alone (a whole word or number, not part of a longer one) in `text`. */
const stands = (text: string, value: string): boolean => new RegExp(`(?<![\\w.])${escaped(value)}(?![\\w])`, "u").test(text);

/** Whether `value` stands alone in any of the screen's text nodes. */
const standsIn = (nodes: readonly string[], value: string): boolean => nodes.some((node) => stands(node, value));

/** What a caveat's message says, as the texts and the data tokens of its segments. */
const wordsOf = (raw: Raw): string[] => raw.caveats.flatMap((caveat) => caveat.message.map((segment) => ("text" in segment ? segment.text : segment.data)));

/** The status each pointer of an answer's `UNCONFIRMED_SEMANTICS` caveat is given: the data token that names it
 * (`<descriptor id><member pointer>`) and the text after it, ` (<status>)`. */
function statusesNamed(raw: Raw): Map<string, string> {
  const named = new Map<string, string>();
  for (const caveat of raw.caveats.filter((one) => one.code === "UNCONFIRMED_SEMANTICS")) {
    for (const [index, segment] of caveat.message.entries()) {
      const after = caveat.message[index + 1];
      const status = after !== undefined && "text" in after ? /^ \(([a-z_]+)\)/u.exec(after.text)?.[1] : undefined;
      if ("data" in segment && status !== undefined) {
        named.set(segment.data, status);
      }
    }
  }
  return named;
}

/** Whether a histogram of the answer has an open end. */
const openEnd = (raw: Raw): boolean => (raw.statistics.distribution.bins ?? []).some((bin) => bin.low === undefined || bin.high === undefined);

/** The values a descriptor holds that the screen's own words or its served counts may coincide with:
 * each pointer pattern with why it is no reading of the descriptor, and (when given) which answers, pointers and
 * values it is allowed on. Every entry is used by one of the two cells (`scan`, below): an entry nothing needs
 * fails, so the list cannot go stale. */
interface Allowed {
  readonly at: RegExp;
  readonly why: string;
  readonly when?: (raw: Raw, pointer: string, value: string) => boolean;
}

const ALLOWED: readonly Allowed[] = [
  { at: /^\/id$/u, why: "the column's id: the references the screen shows (`stat:…/<column>/…`) carry it, as the heading does (D442)" },
  { at: /^\/kind$/u, why: "the word `column`, which the page's own words use (`the column's declared range`, `the column has no declared datatype`)" },
  { at: /^(?:\/curation\/\/fields\/missing_codes\/inferred|\/fields\/missing_codes)\/[^/]+$/u, why: "a missing code's state (declared, or the curation's inferred one): its name (`UNKNOWN`) is the page's own label of the state's count, which the code decides (the notice says so)" },
  {
    at: /^\/fields\/range\/(?:min|max)$/u,
    why: "a declared range's end: a histogram's outer edge when its edges are the declared range's (`edges_from` is `range`, which the screen says in words: the notice says so)",
    when: (raw) => "bins" in raw.statistics.distribution && raw.statistics.distribution.edges_from === "range",
  },
  {
    at: /^\/curation\/.+\/status$/u,
    why: "a member's curation status, shown by the answer's `UNCONFIRMED_SEMANTICS` caveat next to the member it names, which is the notice's own claim (the caveats name a member and its curation status, and that is the only mention of one)",
    when: (raw, pointer, value) => statusesNamed(raw).get(`${String(raw.descriptor["id"])}${pointer.slice("/curation/".length, -"/status".length)}`) === value,
  },
  {
    at: /^\/curation\/.+\/status$/u,
    why: "a status word the caveats say of another member or in their fixed sentence (`imported by default, proposed or undeclared`): the word is the server's, and no caveat names this member (the synthetic descriptor's members are marks no caveat names)",
    when: (raw, _pointer, value) => wordsOf(raw).some((words) => stands(words, value)),
  },
  {
    at: /^\/fields\/derived(?:\/args\/\d+)*\/operator$/u,
    why: "the operator `+` stands in the page's open end `+∞` (a histogram's open high edge, an absent edge): a coincidence of the page's own word",
    when: (raw, _pointer, value) => value === "+" && openEnd(raw),
  },
  {
    at: /^\/fields\/derived(?:\/args\/\d+)*\/operator$/u,
    why: "the operator `-` stands in the `SUPPRESSED` caveat's own words (`Counts from 1 to min_cell_count - 1`), which the answers under a floor carry",
    when: (raw, _pointer, value) => value === "-" && wordsOf(raw).some((words) => stands(words, "-")),
  },
  {
    at: /^(?:\/curation\/\/fields\/list_syntax\/inferred|\/fields\/list_syntax)\/delimiter$/u,
    why: "a list column's delimiter `;`: the page's own punctuation stands alone in a text node (`; release`, between the table's line and the release)",
    when: (_raw, _pointer, value) => value === ";",
  },
];

/** The string values of `value` (parsed JSON), by pointer: a member's name is not a value. */
function stringsOf(value: unknown, at = ""): [string, string][] {
  if (typeof value === "string") {
    return [[at, value]];
  }
  if (Array.isArray(value)) {
    return value.flatMap((one, index) => stringsOf(one, `${at}/${String(index)}`));
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, one]) => stringsOf(one, `${at}/${key}`));
  }
  return [];
}

/** The recorded answers a synthetic descriptor is put into: the first of each screen path (a categorical
 * distribution with or without a pooled row, a list's, a numeric or a date histogram of the data's edges, a
 * range's under a floor and without one, a reason for none, each under a floor or none): every path of the
 * page is rendered over every variant, and no path twice. */
const SCREENS: readonly Raw[] = (() => {
  const seen = new Set<string>();
  return RECORDED.filter((raw) => {
    const d = raw.statistics.distribution;
    const edge = d.bins?.[0]?.low ?? d.bins?.[0]?.high;
    const path = [d.categories === undefined ? (d.bins === undefined ? `none ${String(d.reason)}` : "bins") : "categories", d.edges_from, d.pooled === undefined, d.multi_membership, typeof edge, openEnd(raw), raw.disclosure.min_cell_count === null].join(" ");
    const fresh = !seen.has(path);
    seen.add(path);
    return fresh;
  });
})();

interface Scan {
  readonly synthetic: string[];
  readonly recorded: string[];
  readonly used: Set<Allowed>;
}

/** The two class cells' reading of the screens, made once: the synthetic descriptor of each variant put into
 * each of `SCREENS`, and the recorded descriptors' own values in each recorded answer; the entries of
 * `ALLOWED` each cell used. */
async function scan(): Promise<Scan> {
  const used = new Set<Allowed>();
  const allowed = (pointer: string, raw: Raw, value: string): boolean => {
    const entry = ALLOWED.find((one) => one.at.test(pointer) && (one.when === undefined || one.when(raw, pointer, value)));
    if (entry !== undefined) {
      used.add(entry);
    }
    return entry !== undefined;
  };
  const synthetic: string[] = [];
  for (const [at, raw] of SCREENS.entries()) {
    for (const [index, one] of variants().entries()) {
      const descriptor = { ...one.descriptor, id: raw.column, label: raw.label.data };
      const found = await shown(of(JSON.stringify({ ...raw, descriptor })));
      const nodes = nodesOf(found);
      const where = `screen ${String(at)}, variant ${String(index)}`;
      for (const [pointer, mark] of one.texts) {
        if (pointer !== "/id" && pointer !== "/label" && found.html.includes(mark)) {
          synthetic.push(`${where}: ${pointer} (${mark})`);
        }
      }
      for (const [pointer, value] of one.numbers) {
        if (new RegExp(`(?<![\\d.])${String(value)}(?![\\d])`, "u").test(found.html)) {
          synthetic.push(`${where}: ${pointer} (${String(value)})`);
        }
      }
      for (const [pointer, value] of one.fixed) {
        if (standsIn(nodes, value) && !allowed(pointer, { ...raw, descriptor }, value)) {
          synthetic.push(`${where}: ${pointer} (${value}) on the screen, allowed by no entry`);
        }
      }
    }
  }
  const recorded: string[] = [];
  for (const [at, raw] of RECORDED.entries()) {
    const nodes = nodesOf(await shown(of(JSON.stringify(raw))));
    for (const [pointer, value] of stringsOf(raw.descriptor)) {
      if (standsIn(nodes, value) && !allowed(pointer, raw, value)) {
        recorded.push(`answer ${String(at)}: ${pointer} (${value})`);
      }
    }
  }
  return { synthetic, recorded, used };
}

let scanning: Promise<Scan> | undefined;
const scanned = (): Promise<Scan> => (scanning ??= scan());

/** What is wrong with a synthetic descriptor's record of itself: every string leaf, every free key and every
 * number must be recorded (a mark is a leaf the screen's cells can find), and every list and every map must hold
 * something (an empty one proves nothing of what it would hold). */
function unrecorded(one: Synthetic): string[] {
  const problems: string[] = [];
  const walk = (value: unknown, at: string): void => {
    if (typeof value === "string") {
      if (one.texts.get(at) !== value && one.fixed.get(at) !== value) {
        problems.push(`${at}: a string not recorded`);
      }
    } else if (typeof value === "number") {
      if (one.numbers.get(at) !== value && one.fixed.get(at) !== String(value)) {
        problems.push(`${at}: a number not recorded`);
      }
    } else if (Array.isArray(value)) {
      if (value.length === 0) {
        problems.push(`${at}: an empty list`);
      }
      for (const [index, member] of value.entries()) {
        walk(member, `${at}/${String(index)}`);
      }
    } else if (typeof value === "object" && value !== null) {
      if (Object.keys(value).length === 0) {
        problems.push(`${at}: an empty map`);
      }
      for (const [key, member] of Object.entries(value)) {
        if (/^zq\d+z$/u.test(key) && one.texts.get(`${at}/${key}#key`) !== key) {
          problems.push(`${at}/${key}: a free key not recorded`);
        }
        walk(member, `${at}/${key}`);
      }
    }
  };
  walk(one.descriptor, "");
  return problems;
}

describe("what the screen shows of the column's descriptor (the notice's claim, T8, D443)", () => {
  it("the synthetic descriptor holds every member of the generated schema, every option of every alternative's site (so a member the model gains joins it)", () => {
    const all = variants();
    expect(all.length).toBeGreaterThanOrEqual(9);
    for (const one of all) {
      expect(membersOf("ColumnDescriptor").filter((member) => !Object.hasOwn(one.descriptor, member))).toEqual([]);
      const fields = one.descriptor["fields"] as Record<string, unknown>;
      expect(membersOf("ColumnFields").filter((member) => !Object.hasOwn(fields, member))).toEqual([]);
    }
    expect(membersOf("ColumnFields")).toEqual(expect.arrayContaining(["absent", "completeness", "concepts", "datatype", "derived", "list_syntax", "maps_to", "missing_codes", "permissible_values", "range", "source", "units"]));
    const fixed = (at: RegExp): Set<string> => new Set(all.flatMap((one) => [...one.fixed].filter(([where]) => at.test(where)).map(([, value]) => value)));
    expect(fixed(/^\/fields\/derived\/op$/u)).toEqual(new Set(["date_diff", "arith", "value_map", "unit_convert"]));
    expect(fixed(/^\/curation\/[^/]+\/status$/u)).toEqual(new Set(["asserted", "proposed", "imported", "imported_default", "undeclared"]));
    expect(fixed(/^\/fields\/datatype$/u).size).toBe(9);
    expect(fixed(/^\/fields\/derived(?:\/args\/\d+)*\/operator$/u)).toEqual(new Set(["+", "-", "*", "/"]));
    expect(all.some((one) => one.numbers.has("/version") && one.numbers.has("/fields/range/min"))).toBe(true);
  });

  it("every option of every site of the schema's alternatives (oneOf, anyOf, enum) is built by some variant, each site chosen of its own; the shared choice the generator had leaves some out (the control)", () => {
    const all = variants();
    expect(gaps(all)).toEqual([]);
    expect(siteCount(all)).toBeGreaterThanOrEqual(20);
    // `arm % n` at every site, which correlates the nested sites: the cell finds what it leaves out.
    expect(gaps(correlated()).length).toBeGreaterThan(0);
  });

  it("every variant is populated: each string, free key and number recorded under its pointer, each list and map non-empty (so a mark the generator dropped or a list it built empty fails)", () => {
    for (const [index, one] of variants().entries()) {
      expect(unrecorded(one), `variant ${String(index)}`).toEqual([]);
    }
    // The controls: a string, a free key and a number the record lacks, and an empty list and map.
    const [first] = variants();
    expect(first).toBeDefined();
    const blank = { ...first, texts: new Map<string, string>(), numbers: new Map<string, number>() } as Synthetic;
    expect(unrecorded(blank).length).toBeGreaterThan(0);
    expect(unrecorded({ ...(first as Synthetic), descriptor: { list: [], map: {} } })).toEqual(["/list: an empty list", "/map: an empty map"]);
  });

  it("the detector finds a value that stands alone and not one inside another word or number (the controls), and a value glued after another element's text", () => {
    expect(stands("Edges from 2010-01-01, x", "2010-01-01")).toBe(true);
    expect(stands("the column's", "column")).toBe(true);
    expect(stands("Columns", "Column")).toBe(false);
    expect(stands("trees_variety", "trees")).toBe(false);
    expect(stands("n 77100123", "7710012")).toBe(false);
    // Read by text node, a value shown right after another element's text is its own node, not glued to it
    // (`…f19fanumber`: the datatype shown after the release's manifest).
    const host = document.createElement("p");
    const manifest = document.createElement("span");
    manifest.append("sha256:f19fa");
    host.append("release ", manifest, "number");
    expect(stands(host.textContent, "number")).toBe(false);
    expect(standsIn(textNodesOf(host), "number")).toBe(true);
  });

  it("the regions the class cells leave out are each present on every recorded answer: the heading's id and label (two elements) and the one notice", async () => {
    expect.hasAssertions();
    for (const [at, raw] of RECORDED.entries()) {
      const nodes = nodesOf(await shown(of(JSON.stringify(raw))));
      expect(nodes.join(""), String(at)).not.toContain(NOT_SHOWN);
      expect(nodes.join(""), String(at)).toContain("Column");
    }
  });

  it("R67: no value of a synthetic descriptor holding every member (each string and key a mark of its own, each number a number of its own) is on the screen of any screen path, caveats included, but the page's own words the allow-list gives", { timeout: 120_000 }, async () => {
    expect.hasAssertions();
    expect(SCREENS.length).toBeGreaterThanOrEqual(6);
    expect((await scanned()).synthetic).toEqual([]);
  });

  it("R67: the recorded descriptors' own string values are on the screen (outside the heading's id and label and the notice, the caveats included) only where the allow-list gives the reason, and every entry of it is used by one of the two cells", { timeout: 120_000 }, async () => {
    expect.hasAssertions();
    const { recorded, used } = await scanned();
    expect(recorded).toEqual([]);
    expect(ALLOWED.filter((entry) => !used.has(entry)).map((entry) => String(entry.at))).toEqual([]);
    // The id and label the notice says are shown are the heading's own, which D442 makes the descriptor's.
    for (const [at, raw] of RECORDED.entries()) {
      expect(raw.column, String(at)).toBe(raw.descriptor["id"]);
      expect(raw.label.data, String(at)).toBe(raw.descriptor["label"]);
    }
  });

  it("holds the words the notice says: what is shown (the caveats' naming of a member and its status among it), what is not, and the descriptor given whole or without its evidence", () => {
    expect(NOT_SHOWN).toMatch(/^The values of the column's descriptor are not shown on this page yet\. What this page shows is the column's id and label, counts and a distribution computed from the column's data and its declaration /u);
    expect(NOT_SHOWN).toContain("a histogram's edges may be the column's declared range, and its missing-value codes decide the counts of its missing states");
    expect(NOT_SHOWN).toContain("and the server's caveats, which name each field of the descriptor that queries read and an operator has not confirmed, with its curation status.");
    expect(NOT_SHOWN).toContain("The page does not display the descriptor's member values (datatype, units, definition, permissible values, missing-value codes, declared range, list syntax, source): the caveats' naming of a member and its curation status is the only mention of one.");
    expect(NOT_SHOWN).not.toMatch(/curation status\) is not shown/u);
  });
});

describe("the observation states' words (D443, §6.2)", () => {
  const shape = SHAPES["StateCounts"];

  it("are keyed by the answer's own states, in its order: a fifth state, or a reordering, fails here until the table is changed with it", () => {
    expect(shape?.t).toBe("rec");
    expect(Object.keys(STATE_WORDS)).toEqual(shape?.t === "rec" ? [...shape.r] : []);
    expect(Object.keys(STATE_WORDS)).toEqual(["PRESENT", "NOT_APPLICABLE", "NOT_ASSESSED", "UNKNOWN"]);
  });

  it("each label begins with its own code, the state the `stat:` reference beside the count names, and only UNKNOWN has a gloss", () => {
    for (const [state, words] of Object.entries(STATE_WORDS)) {
      expect(words.startsWith(state), state).toBe(true);
      expect(words === state, state).toBe(state !== "UNKNOWN");
    }
    expect(STATE_WORDS.UNKNOWN).toBe("UNKNOWN (no usable value)");
  });

  it("are shown beside each count, in the table's order, with the state's own reference", async () => {
    expect.hasAssertions();
    const found = await showing("variety");
    expect(found.states.slice(1).map((line) => line.slice(0, line.indexOf(":")))).toEqual(Object.values(STATE_WORDS));
    for (const [state, line] of Object.keys(STATE_WORDS).map((one, at) => [one, found.states[at + 1]] as const)) {
      expect(line?.endsWith(`/trees.variety/states/${state}`), state).toBe(true);
    }
  });
});

/** Every word of `Column.tsx` (the census's enumeration, D435's, rooted at the column screen), each
 * with why it is there: a new word fails here until it is classified. */
const COLUMN_WORDS: Readonly<Record<string, string>> = {
  [PENDING]: "own: fixed, quoted in D443",
  [REFUSED]: "own: fixed, quoted in D443",
  [FAILED]: "own: fixed, quoted in D443",
  [BACK]: "own: the link back's words, quoted in D443",
  [NOT_SHOWN]: "own: T8, quoted in D443",
  [MULTI_MEMBERSHIP]: "live: shown when multi_membership is true (the tags cells)",
  [POOLED]: "live: shown before the pooled row (the pooled cells)",
  ...Object.fromEntries(PAIRS.map(([reason, words]) => [words, `live: NoDistribution.reason ${reason}, held as a pair`])),
  [EDGES_WORDS.range]: "live: edges_from range (the range cells)",
  [EDGES_WORDS.data]: "live: edges_from data (the histogram cells)",
  UNKNOWN: "neutral: a state's own name (§6.2), the code the gloss follows",
  PRESENT: "neutral: a state's own name (§6.2)",
  NOT_APPLICABLE: "neutral: a state's own name (§6.2)",
  NOT_ASSESSED: "neutral: a state's own name (§6.2)",
  [STATE_WORDS.UNKNOWN]: "live: UNKNOWN's words, a code and a gloss true of every cell in the state (§6.2, §6.4), quoted in D443",
  Categories: "neutral: a section's heading",
  categories: "no word: an aria-label and a member's name",
  Histogram: "neutral: a section's heading",
  bins: "no word: an aria-label and a member's name",
  states: "no word: an aria-label",
  "HistogramOut.edges_from": "no word: a vocabulary's name",
  "NoDistribution.reason": "no word: a vocabulary's name",
  "Edges from": "neutral: the label before the edges' source",
  "Smallest value": "live: shown when the answer has min (the histogram cells)",
  "Largest value": "live: shown when the answer has max (the histogram cells)",
  "No distribution:": "neutral: the label before the reason",
  Column: "neutral: the heading's label",
  Table: "neutral: the table's label",
  "of the dataset": "neutral: the dataset's label",
  "; release": "neutral: the release's label",
  "Rows and observation states": "neutral: a section's heading",
  "Rows:": "neutral: the rows' label",
};

describe("the column census (the census reaches Column.tsx, D443)", () => {
  const ROOTS = ["src/dataset/Column.tsx"];

  it("reads Column.tsx and the layout it reaches, and every word of Column.tsx is classified, every entry a word of it", () => {
    const program = censusProgram(ROOTS);
    expect(censusModules(program, ROOTS)).toContain("src/dataset/Column.tsx");
    expect(censusModules(program, ROOTS)).toContain("src/layout/StatCount.tsx");
    const words = [...new Set(enumerated(program, ROOTS).get("src/dataset/Column.tsx") ?? [])].sort();
    expect(words).toEqual(Object.keys(COLUMN_WORDS).sort());
  });

  it("each word's why is one of the classes", () => {
    for (const why of Object.values(COLUMN_WORDS)) {
      expect(why).toMatch(/^(?:own|live|neutral|no word): /u);
    }
  });
});

describe("the recorded answers' ids", () => {
  it("are each the column asked (the heading's id is the answer's own)", () => {
    for (const place of Object.keys(PLACES) as Place[]) {
      expect(readText(of(textOf(place)).column)).toMatch(/^[a-z_]+\.[a-z_]+$/u);
    }
  });
});
