/**
 * `Disclosure` (D430; D427's R25, §8.4): the effective disclosure setting, as the answer states it,
 * on every result, cohort count and catalogue answer: every `disclosure` of the orchard's answers
 * (a catalogue hit's, a dataset's, a cohort count's) and the settings of either kind. Its words say
 * the counts from 1 to *k* − 1 are suppressed, which a live cell holds (D435, B10): on every live
 * result under a floor, every count the page shows (each population's, each analysed count's and
 * each category row's numerator and denominator) is `null`, 0 or at least *k*. The words before
 * B10 ("counts below it") were false at 0: the floor-5 answer (d) holds `n_true: 0` and `0`/`0`.
 * The words say counts of units: a second cell holds them over every answer under a floor, the
 * catalogue's and the dataset's among them, whose totals of datasets and columns are no counts of units.
 */
import { describe, expect, it } from "vitest";

import { liveResults, underKCounts } from "./result-support";

import type { ServerNumber } from "../../src/api/box";
import { Disclosure, NO_SETTING } from "../../src/layout/Disclosure";
import { walked } from "./answers";
import { leavesOf } from "./injection";
import { render } from "./render";
import { componentOf, ORCHARD } from "./typed-support";

type Setting = { readonly min_cell_count: ServerNumber | null };

/** Every `disclosure` member of a walked answer, at any depth (a walked answer is plain objects,
 * lists, boxes and maps). */
function settingsOf(value: unknown): Setting[] {
  if (Array.isArray(value)) {
    return value.flatMap((member: unknown) => settingsOf(member));
  }
  if (typeof value !== "object" || value === null) {
    return [];
  }
  const own = Object.entries(value).flatMap(([key, member]: [string, unknown]) => (key === "disclosure" ? [member as Setting] : settingsOf(member)));
  return own;
}

const textOf = async (setting: Setting): Promise<string> => {
  const shown = await render(<Disclosure value={setting} />);
  const text = shown.container.textContent;
  shown.unmount();
  return text;
};

describe("Disclosure", () => {
  it("R25: states the effective disclosure setting (k, or none) as the answer says it, on every catalogue answer, dataset and cohort count", async () => {
    expect.hasAssertions();
    for (const k of ["2", "5", "11", "9007199254740991"]) {
      expect(await textOf((walked("DisclosureOut", `{"min_cell_count":${k}}`) as Setting))).toBe(`Disclosure: minimum cell count k = ${k}: counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).`);
      expect(await textOf((walked("Disclosure", `{"min_cell_count":${k}}`) as Setting))).toContain(`minimum cell count k = ${k}:`);
    }
    expect(await textOf((walked("DisclosureOut", '{"min_cell_count":null}') as Setting))).toBe(NO_SETTING);
    const found = ORCHARD.filter((answer) => answer.status === 200).flatMap((answer) => settingsOf(walked(componentOf(answer), answer.body)));
    expect(found.length).toBeGreaterThanOrEqual(3);
    for (const setting of found) {
      const text = await textOf(setting);
      if (setting.min_cell_count === null) {
        expect(text).toBe(NO_SETTING);
      } else {
        expect(text).toMatch(/^Disclosure: minimum cell count k = [1-9][0-9]*: counts of units from 1 to k − 1,/u);
      }
    }
  });

  it("has no text position (the leaf sweep of the other components finds none to sweep): a number or nothing", () => {
    for (const setting of ORCHARD.filter((answer) => answer.status === 200).flatMap((answer) => settingsOf(walked(componentOf(answer), answer.body)))) {
      expect(leavesOf(setting)).toEqual([]);
    }
  });

  it("B10: on every live result under a floor, every count shown is null, 0 or at least k; a 0 occurs, so the words before B10 were false", () => {
    const floored = liveResults().filter(({ result }) => result.k !== null);
    // (d), (e), (h) and (h'): the four values answers under a floor.
    expect(floored.map(({ index }) => index)).toEqual([28, 29, 32, 33]);
    let zeros = 0;
    for (const { result } of floored) {
      const counts = underKCounts(result.raw);
      expect(counts.length).toBeGreaterThan(0);
      for (const count of counts) {
        expect(count === null || count === 0 || (result.k !== null && count >= result.k), `${String(count)} under k = ${String(result.k)}`).toBe(true);
        zeros += count === 0 ? 1 : 0;
      }
    }
    expect(zeros).toBeGreaterThan(0);
  });

  it("B10: on every answer under a floor, whatever the screen, every count of units it holds is null, 0 or at least k (the sentence says counts of units: a total of datasets or of columns is none)", () => {
    const floored = ORCHARD.flatMap((answer, index) => {
      if (answer.status !== 200) {
        return [];
      }
      const body = JSON.parse(answer.body) as unknown;
      const k = settingOf(body);
      return k === null ? [] : [{ index, k, counts: unitCounts(body) }];
    });
    expect(floored.map(({ index }) => index)).toEqual(expect.arrayContaining([15, 16, 20, 28, 29, 32, 33]));
    for (const { index, k, counts } of floored) {
      for (const [kind, count] of counts) {
        expect(count === null || count === 0 || count >= k, `answer ${String(index)}: ${kind} ${String(count)} under k = ${String(k)}`).toBe(true);
      }
    }
    expect(floored.reduce((total, { counts }) => total + counts.length, 0)).toBeGreaterThan(20);
    // A table's row count (n_rows) and a histogram bin's count are among those held.
    const kinds = new Set(floored.flatMap(({ counts }) => counts.map(([kind]) => kind)));
    expect([kinds.has("stat"), kinds.has("bin")]).toEqual([true, true]);
  });
});

/** The first effective disclosure setting of a raw answer, or null. */
function settingOf(value: unknown): number | null {
  if (Array.isArray(value)) {
    return value.reduce<number | null>((found, member) => found ?? settingOf(member), null);
  }
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const k = (record["disclosure"] as { min_cell_count?: unknown } | undefined)?.min_cell_count;
  if (typeof k === "number") {
    return k;
  }
  return Object.values(record).reduce<number | null>((found, member) => found ?? settingOf(member), null);
}

/** The counts of units a raw answer holds: a population's members and maps, an analysed count's, a
 * proportion's numerator and denominator, and a statistic's count; never inside a chart or a document. */
function unitCounts(value: unknown, member = ""): (readonly [string, number | null])[] {
  if (Array.isArray(value)) {
    return value.flatMap((one) => unitCounts(one, member));
  }
  if (typeof value !== "object" || value === null) {
    return [];
  }
  const record = value as Record<string, unknown>;
  const found: (readonly [string, number | null])[] = [];
  for (const [key, inner] of Object.entries(record)) {
    if (["charts", "document", "source", "descriptor", "params"].includes(key)) {
      continue;
    }
    // A statistic's count (a table's `rows`, `n_rows`) and a histogram bin's count are counts of units too.
    const counted = ["n_true", "n_false", "n_unknown", "lift_differs", "n", "excluded_units", "numerator", "denominator"].includes(key) || (key === "count" && (typeof record["reference"] === "string" || member === "bins"));
    const mapped = ["unknown_by_reason", "unknown_by_leaf", "excluded"].includes(key);
    const kind = key === "count" ? (member === "bins" ? "bin" : "stat") : key;
    if (counted && (typeof inner === "number" || inner === null)) {
      found.push([kind, inner]);
    } else if (mapped && typeof inner === "object" && inner !== null && !Array.isArray(inner)) {
      found.push(...Object.values(inner).flatMap((one) => (typeof one === "number" || one === null ? [[key, one] as const] : [])));
    } else {
      found.push(...unitCounts(inner, key));
    }
  }
  return found;
}
