/**
 * The builder driven through its rendered controls alone, by seeded sequences of a person's actions
 * (D433; B6.4 of the plan), under `<StrictMode>`, over the real seam: every answer is held and
 * released by the seed (so that edits happen while calls are pending, and answers arrive late and
 * out of order), deadlines are passed by the clock, and the queue is a fresh module's each seed.
 * After each action the oracles read the DOM alone:
 *
 * - (i) Validate is disabled exactly when the form shown is incomplete, a value is empty, or a call
 *   is shown pending;
 * - (ii) a Validate body is the document the form shows, written by the oracle's own writer;
 * - (iii) a Count or Run body is the validated body;
 * - (iv) each outcome view carries the stale words exactly when an edit was made since it was asked;
 * - (v) Count and Run are enabled exactly when the validation view says valid, shows no stale words,
 *   and nothing is shown pending;
 * - (vi) each action's effect, predicted from the DOM before it: a removal gives the rows before
 *   without that row, a choice changes its list alone (a change of unit clears exactly the view
 *   columns, whose lists are the unit's own), typing changes its field alone (line breaks dropped),
 *   an addition adds one empty row;
 * - (vii) after a removal, every surviving row's list and field is the same DOM node as before.
 *
 * Coverage counters with floors are asserted over the run.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WORDS } from "../../src/builder/Builder";
import { idPart } from "../../e2e/options";
import { ANSWERS, type BuilderPage, builderPage, respond, settle } from "./builder-support";
import type { Sent } from "./screens";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** A seeded generator of numbers in [0, 1) (mulberry32). */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The form as the DOM shows it. */
interface Shown {
  readonly unit: string;
  readonly cohorts: readonly { readonly leaves: readonly { readonly column: string; readonly value: string }[] }[];
  readonly columns: readonly string[];
}

const NONE = "(choose)";

/** The id the chosen option shows: the part of its `textContent` (not its `text`, which collapses an
 * empty name's `id — ` to `id —`) before the first ` — ` (an id holds no space, so a label holding the
 * separator, or another option's id, cannot move it; D441). */
const selectedText = (select: HTMLSelectElement | null): string => {
  const option = select?.options[select.selectedIndex];
  return option === undefined ? "?" : idPart(option.textContent);
};

function shownOf(container: Element): Shown {
  return {
    unit: selectedText(container.querySelector("section.builder > label > select")),
    cohorts: [...container.querySelectorAll("section.cohort")].map((cohort) => ({
      leaves: [...cohort.querySelectorAll(".leaf")].map((leaf) => ({ column: selectedText(leaf.querySelector("select")), value: leaf.querySelector("input")?.value ?? "?" })),
    })),
    columns: [...container.querySelectorAll("section.view .column select")].map((select) => selectedText(select as HTMLSelectElement)),
  };
}

/** The oracle's own writer: the document the form shows, or `null` (incomplete or an empty value). */
function documentOf(shown: Shown): string | null {
  const leaves = shown.cohorts.flatMap((cohort) => cohort.leaves);
  if (shown.unit === NONE || shown.columns.includes(NONE) || shown.columns.length === 0 || leaves.some((leaf) => leaf.column === NONE || leaf.value === "")) {
    return null;
  }
  const names = ["first", "second"].slice(0, shown.cohorts.length);
  const cohorts: Record<string, unknown> = {};
  shown.cohorts.forEach((cohort, index) => {
    cohorts[names[index] ?? "?"] = { all: cohort.leaves.map((leaf) => ({ kind: "value", column: leaf.column, values: [leaf.value.startsWith("$") ? `$${leaf.value}` : leaf.value] })) };
  });
  return JSON.stringify({
    document: { aibi: "1", dataset: "orchard", unit: shown.unit, cohorts, views: [{ analysis: "summary.distribution", cohorts: names, params: { columns: shown.columns.map((column) => ({ column })) } }] },
  });
}

const VALUES = ["apple", "pear", "t1", "$x", "$$y", "<b>x</b>", "\u202eabc", "", "a\r\nb", "apple", ""];

type Kind = "choose" | "complete" | "type" | "addLeaf" | "removeLeaf" | "addColumn" | "removeColumn" | "addCohort" | "removeCohort" | "validate" | "count" | "run" | "release" | "deadline";

/** A held request: its tool, its body, and its answer's release. */
interface Held {
  readonly sent: Sent;
  readonly resolve: (response: Response) => void;
  readonly order: number;
  released: boolean;
}

const SEEDS = 60;
const ACTIONS = 60;

describe("the builder, driven through its controls (B6.4)", () => {
  it("holds the oracles over seeded sequences of a person's actions, with every answer held and released by the seed", { timeout: 300_000 }, async () => {
    const counters = new Map<string, number>();
    const bump = (name: string): void => {
      counters.set(name, (counters.get(name) ?? 0) + 1);
    };
    const failures: string[] = [];
    const clean = (): boolean => failures.length === 0;
    for (let seed = 1; seed <= SEEDS && clean(); seed += 1) {
      const next = random(seed * 7919);
      const pick = <T,>(list: readonly T[]): T | undefined => list[Math.floor(next() * list.length)];
      const validValidation = next() < 0.8;
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      const held: Held[] = [];
      let order = 0;
      const page: BuilderPage = await builderPage(
        (sent) =>
          new Promise<Response>((resolve) => {
            held.push({ sent, resolve, order: (order += 1), released: false });
          }),
      );
      const container = page.container;
      /** Each view's whether an edit was made since it was asked (`null`: not asked). */
      const edited: Record<"validation" | "count" | "run", boolean | null> = { validation: null, count: null, run: null };
      const pressedValidations: string[] = [];
      const fail = (step: number, what: string): void => {
        failures.push(`seed ${String(seed)} step ${String(step)}: ${what}`);
      };
      const view = (label: "validation" | "count" | "run"): Element | null => container.querySelector(`section.outcome[aria-label="${label}"]`);
      const pendingShown = (): boolean => ["validation", "count", "run"].some((label) => (view(label as "validation")?.textContent ?? "").includes(WORDS.pending));
      const edit = (): void => {
        if (pendingShown()) {
          bump("edit while pending");
        }
        for (const key of ["validation", "count", "run"] as const) {
          if (edited[key] !== null) {
            edited[key] = true;
          }
        }
      };
      const buttons = (name: string): HTMLButtonElement[] => [...container.querySelectorAll("button")].filter((one) => one.textContent === name && !one.disabled);
      const click = async (button: HTMLButtonElement): Promise<void> => {
        await act(async () => {
          button.click();
          await Promise.resolve();
        });
        await settle();
      };
      const choose = async (select: HTMLSelectElement, index: number): Promise<void> => {
        await act(async () => {
          select.value = select.options[index]?.value ?? "";
          select.dispatchEvent(new Event("change", { bubbles: true }));
          await Promise.resolve();
        });
      };
      const typeInto = async (input: HTMLInputElement, text: string): Promise<void> => {
        await act(async () => {
          Reflect.set(HTMLInputElement.prototype, "value", text, input);
          input.dispatchEvent(new Event("input", { bubbles: true }));
          await Promise.resolve();
        });
      };
      for (let step = 0; step < ACTIONS && clean(); step += 1) {
        const before = shownOf(container);
        const document = documentOf(before);
        const selects = [...container.querySelectorAll("select")].filter((one) => !one.disabled);
        const inputs = [...container.querySelectorAll("input")];
        const kinds: Kind[] = ["choose", "type", "addLeaf", "removeLeaf", "addColumn", "removeColumn", "addCohort", "removeCohort"];
        if (document === null) {
          kinds.push("complete", "complete", "complete", "complete");
        }
        for (const [name, kind] of [
          ["Validate", "validate"],
          ["Count", "count"],
          ["Run", "run"],
        ] as const) {
          if (buttons(name).length > 0) {
            kinds.push(kind, kind, kind, kind, kind);
          }
        }
        if (held.some((one) => !one.released)) {
          kinds.push("release", "release", "release");
          if (pendingShown()) {
            kinds.push("deadline");
          }
        }
        const kind = pick(kinds) ?? "choose";
        let predicted: Shown;
        let kept: Element[] = [];
        let removed: Element | null = null;
        if (kind === "choose" || kind === "complete") {
          const open = kind === "complete" ? selects.filter((one) => one.selectedIndex === 0 && one.options.length > 1) : selects;
          const select = pick(open);
          if (select === undefined) {
            continue;
          }
          const index = kind === "complete" ? 1 + Math.floor(next() * (select.options.length - 1)) : Math.floor(next() * select.options.length);
          const text = idPart(select.options[index]?.textContent ?? "?");
          const isUnit = select.closest("section.cohort") === null && select.closest("section.view") === null;
          const isView = select.closest("section.view") !== null;
          const leafAt = [...container.querySelectorAll("section.cohort .leaf select")].indexOf(select);
          const columnAt = [...container.querySelectorAll("section.view .column select")].indexOf(select);
          let flat = 0;
          predicted = {
            unit: isUnit ? text : before.unit,
            cohorts: before.cohorts.map((cohort) => ({
              leaves: cohort.leaves.map((leaf) => {
                const at = flat;
                flat += 1;
                return at === leafAt ? { ...leaf, column: text } : leaf;
              }),
            })),
            columns: isUnit ? before.columns.map((column) => (text === before.unit ? column : NONE)) : before.columns.map((column, at) => (isView && at === columnAt ? text : column)),
          };
          bump(isUnit ? "choose unit" : isView ? "choose view" : "choose leaf");
          if (index === 0) {
            bump("choose none");
          }
          edit();
          await choose(select, index);
        } else if (kind === "type") {
          const input = pick(inputs);
          if (input === undefined) {
            continue;
          }
          const text = pick(VALUES) ?? "";
          const sanitized = text.replace(/[\r\n]/gu, "");
          const at = inputs.indexOf(input);
          let flat = 0;
          predicted = {
            ...before,
            cohorts: before.cohorts.map((cohort) => ({
              leaves: cohort.leaves.map((leaf) => {
                const here = flat;
                flat += 1;
                return here === at ? { ...leaf, value: sanitized } : leaf;
              }),
            })),
          };
          if (sanitized !== input.value) {
            edit();
            bump(sanitized === "" ? "clear" : "type");
          }
          await typeInto(input, text);
        } else if (kind === "addLeaf" || kind === "addColumn" || kind === "addCohort") {
          const name = kind === "addLeaf" ? "Add a condition" : kind === "addColumn" ? "Add a column" : "Add a second cohort";
          const all = buttons(name);
          const button = pick(all);
          if (button === undefined) {
            continue;
          }
          const cohortAt = [...container.querySelectorAll("section.cohort")].indexOf(button.closest("section.cohort") ?? container);
          predicted =
            kind === "addLeaf"
              ? { ...before, cohorts: before.cohorts.map((cohort, at) => (at === cohortAt ? { leaves: [...cohort.leaves, { column: NONE, value: "" }] } : cohort)) }
              : kind === "addColumn"
                ? { ...before, columns: [...before.columns, NONE] }
                : { ...before, cohorts: [...before.cohorts, { leaves: [] }] };
          bump(kind);
          edit();
          await click(button);
        } else if (kind === "removeLeaf" || kind === "removeColumn" || kind === "removeCohort") {
          const name = kind === "removeLeaf" ? "Remove this condition" : kind === "removeColumn" ? "Remove this column" : "Remove this cohort";
          const all = buttons(name);
          const button = pick(all);
          if (button === undefined) {
            continue;
          }
          const rows = kind === "removeCohort" ? [...container.querySelectorAll("section.cohort")] : kind === "removeColumn" ? [...container.querySelectorAll("section.view .column")] : [...(button.closest("section.cohort")?.querySelectorAll(".leaf") ?? [])];
          const row = kind === "removeCohort" ? button.closest("section.cohort") : button.parentElement;
          const at = row === null ? -1 : rows.indexOf(row);
          bump(`${kind} at ${at === 0 ? "first" : at === rows.length - 1 ? "last" : "middle"}`);
          removed = row;
          kept = [...container.querySelectorAll("section.cohort select, section.cohort input, section.view select")].filter((one) => row === null || !row.contains(one));
          const cohortAt = [...container.querySelectorAll("section.cohort")].indexOf(button.closest("section.cohort") ?? container);
          predicted =
            kind === "removeCohort"
              ? { ...before, cohorts: before.cohorts.filter((_, index) => index !== at) }
              : kind === "removeColumn"
                ? { ...before, columns: before.columns.filter((_, index) => index !== at) }
                : { ...before, cohorts: before.cohorts.map((cohort, index) => (index === cohortAt ? { leaves: cohort.leaves.filter((_, leaf) => leaf !== at) } : cohort)) };
          edit();
          await click(button);
        } else if (kind === "validate" || kind === "count" || kind === "run") {
          const button = buttons(kind === "validate" ? "Validate" : kind === "count" ? "Count" : "Run")[0];
          if (button === undefined) {
            continue;
          }
          const sentBefore = page.sent.length;
          if (kind === "validate" && document !== null) {
            pressedValidations.push(document);
          }
          if (kind !== "validate" && document !== pressedValidations.at(-1)) {
            fail(step, `(iii) ${kind} enabled while the form shows another document than the one validated`);
          }
          edited[kind === "validate" ? "validation" : kind] = false;
          bump(kind);
          if (before.cohorts.length === 2) {
            bump("two-cohort send");
          }
          await click(button);
          const sent = page.sent.slice(sentBefore);
          for (const one of sent) {
            const tool = { validate: "validate_document", count: "count_cohort", run: "run_analysis" }[kind];
            if (one.tool === tool && one.body !== document) {
              fail(step, `(ii)/(iii) the ${kind} body is not the document the form shows`);
            }
          }
          predicted = before;
        } else if (kind === "release") {
          const waiting = held.filter((one) => !one.released);
          const one = pick(waiting);
          if (one === undefined) {
            continue;
          }
          one.released = true;
          if (one.sent.tool === "validate_document" && held.some((other) => other.sent.tool === "validate_document" && other.order > one.order)) {
            bump("late answer after a newer validate");
          }
          bump("release");
          // Synthetic answers, by tool, to whatever document the seed built: the oracles read the bodies sent and the gates, never the answers' contents.
          const answer = one.sent.tool === "validate_document" ? (validValidation ? ANSWERS.validated : ANSWERS.stringView) : one.sent.tool === "count_cohort" ? ANSWERS.counted : ANSWERS.ran;
          one.resolve(respond(answer));
          await settle();
          predicted = before;
        } else {
          bump("deadline");
          await act(async () => {
            await vi.advanceTimersByTimeAsync(45_000);
          });
          await settle();
          predicted = before;
        }
        // The oracles, from the DOM alone.
        const after = shownOf(container);
        if (JSON.stringify(after) !== JSON.stringify(predicted)) {
          fail(step, `(vi) ${kind}: ${JSON.stringify(predicted)} predicted, ${JSON.stringify(after)} shown`);
        }
        if (removed !== null) {
          const still = new Set(container.querySelectorAll("section.cohort select, section.cohort input, section.view select"));
          if (kept.some((one) => !still.has(one)) || still.size !== kept.length) {
            fail(step, `(vii) ${kind}: a surviving row's control is not the node it was`);
          }
        }
        const pending = pendingShown();
        const shownDocument = documentOf(after);
        const validate = [...container.querySelectorAll("button")].find((one) => one.textContent === "Validate");
        if (validate?.disabled !== (shownDocument === null || pending)) {
          fail(step, `(i) Validate disabled ${String(validate?.disabled)}, the form ${shownDocument === null ? "incomplete" : "complete"}, ${pending ? "pending" : "nothing pending"}`);
        }
        for (const key of ["validation", "count", "run"] as const) {
          const text = view(key)?.textContent ?? null;
          const stale = text === null ? null : text.includes(WORDS.stale);
          if (stale !== edited[key]) {
            fail(step, `(iv) the ${key} view's stale words ${String(stale)}, an edit since it was asked ${String(edited[key])}`);
          }
        }
        // (iv) the result stays shown under the stale words (D435): an answered run shows its result.
        const runText = view("run")?.textContent ?? null;
        if (runText !== null && ![WORDS.pending, WORDS.failed, WORDS.deadline].some((words) => runText.includes(words)) && !/Derivation drv:[0-9a-f]{64}/u.test(runText)) {
          fail(step, "(iv) the run view's answer does not show its result");
        }
        const validationText = view("validation")?.textContent ?? "";
        const ready = validationText.includes(WORDS.valid) && !validationText.includes(WORDS.stale) && !pending;
        for (const name of ["Count", "Run"]) {
          const button = [...container.querySelectorAll("button")].find((one) => one.textContent === name);
          if (button?.disabled !== !ready) {
            fail(step, `(v) ${name} disabled ${String(button?.disabled)}, ready ${String(ready)}`);
          }
        }
        if (kind === "validate" && pending) {
          bump("validate pending");
        }
      }
      // Every validate body sent is one pressed, in order (a store past its deadline unsent is no body).
      const sentValidations = page.sent.filter((one) => one.tool === "validate_document").map((one) => one.body);
      let cursor = 0;
      for (const body of sentValidations) {
        while (cursor < pressedValidations.length && pressedValidations[cursor] !== body) {
          cursor += 1;
        }
        if (cursor === pressedValidations.length) {
          fail(-1, "(ii) a validate body that no press asked for");
        }
        cursor += 1;
      }
      for (const one of held.filter((entry) => !entry.released)) {
        one.resolve(respond(ANSWERS.validated));
      }
      await settle();
      page.close();
      vi.useRealTimers();
    }
    expect(failures).toEqual([]);
    const floors: Readonly<Record<string, number>> = {
      "choose unit": 20,
      "choose leaf": 20,
      "choose view": 20,
      "choose none": 10,
      type: 20,
      clear: 3,
      addLeaf: 20,
      addColumn: 10,
      addCohort: 10,
      "removeLeaf at first": 3,
      "removeLeaf at middle": 1,
      "removeLeaf at last": 3,
      "removeColumn at first": 3,
      "removeColumn at last": 3,
      "removeCohort at first": 3,
      "removeCohort at last": 3,
      validate: 40,
      count: 10,
      run: 10,
      "two-cohort send": 10,
      release: 40,
      "late answer after a newer validate": 1,
      "edit while pending": 50,
      deadline: 3,
    };
    const short = Object.entries(floors).filter(([name, floor]) => (counters.get(name) ?? 0) < floor);
    expect(short.map(([name, floor]) => `${name}: ${String(counters.get(name) ?? 0)} < ${String(floor)}`)).toEqual([]);
    expect((counters.get("count") ?? 0) + (counters.get("run") ?? 0)).toBeGreaterThanOrEqual(20);
  });
});
