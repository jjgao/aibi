/**
 * `Disclosure` (D430; D427's R25, §8.4): the effective disclosure setting, as the answer states it,
 * on every result, cohort count and catalogue answer: every `disclosure` of the orchard's answers
 * (a catalogue hit's, a dataset's, a cohort count's) and the settings of either kind.
 */
import { describe, expect, it } from "vitest";

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
      expect(await textOf((walked("DisclosureOut", `{"min_cell_count":${k}}`) as Setting))).toBe(`Disclosure: minimum cell count ${k}: counts below it, and counts that would reveal them, are suppressed (§8.4).`);
      expect(await textOf((walked("Disclosure", `{"min_cell_count":${k}}`) as Setting))).toContain(`minimum cell count ${k}:`);
    }
    expect(await textOf((walked("DisclosureOut", '{"min_cell_count":null}') as Setting))).toBe(NO_SETTING);
    const found = ORCHARD.filter((answer) => answer.status === 200).flatMap((answer) => settingsOf(walked(componentOf(answer), answer.body)));
    expect(found.length).toBeGreaterThanOrEqual(3);
    for (const setting of found) {
      const text = await textOf(setting);
      if (setting.min_cell_count === null) {
        expect(text).toBe(NO_SETTING);
      } else {
        expect(text).toMatch(/^Disclosure: minimum cell count [1-9][0-9]*: counts below it/u);
      }
    }
  });

  it("has no text position (the leaf sweep of the other components finds none to sweep): a number or nothing", () => {
    for (const setting of ORCHARD.filter((answer) => answer.status === 200).flatMap((answer) => settingsOf(walked(componentOf(answer), answer.body)))) {
      expect(leavesOf(setting)).toEqual([]);
    }
  });
});
