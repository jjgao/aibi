/**
 * The builder-lite screens (D433; the rows' own cells are `builder.test.tsx`'s), driven through their rendered controls under
 * `<StrictMode>`, over the real seam and the server's own answers (`orchard-answers.json`'s eight
 * builder answers): the calls (none on load, one per press, the bodies of Count and Run byte-equal to
 * Validate's), the gates (Validate while a draft and nothing pending; Count and Run while the
 * validation shown is of the form as it stands, valid and nothing pending), the stale words, a
 * deadline (the buttons enabled again, a second call sent beside the overdue one, a third waiting),
 * the words, the floor case (D431's floored describe answer and the server's answers under a floor
 * of 50), and the validation's and the count's views swept with hostile texts (the run view is the
 * result screen's, swept through the real seam by `result-sweep.test.tsx`, D435).
 */
import { act, StrictMode } from "react";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Answered } from "../../src/api/typed";
import { CountsOf, ValidationOf, WORDS } from "../../src/builder/Builder";
import { SHAPE_MISMATCH, UNREACHABLE } from "../../src/api/typed";
import { DEADLINE_MS } from "../../src/catalogue/load";
import { walked } from "./answers";
import { ANSWERS, BUILT, type BuilderPage, builderPage, byRequest, DESCRIBED, synthetic, fillBuilt, freshBuilder, pageOf, respond, settle } from "./builder-support";
import { leafSweep } from "./injection";
import { render } from "./render";
import { json, type Sent } from "./screens";

let page: BuilderPage | undefined;

afterEach(() => {
  page?.close();
  page = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** The section of an outcome view (`validation`, `count`, `run`). */
const viewOf = (found: BuilderPage, label: "validation" | "count" | "run"): Element | null => found.container.querySelector(`section.outcome[aria-label="${label}"]`);

/** The text of an element, its runs of white space one space. */
const textOf = (element: Element | null | undefined): string => (element?.textContent ?? "").replace(/\s+/gu, " ").trim();

/** A body's document. */
const documentOf = (sent: Sent | undefined): unknown => (JSON.parse(sent?.body ?? "{}") as { document: unknown }).document;

describe("the builder over the server's answers", () => {
  it("validates, counts and runs the document the form shows: one call a press, Count's and Run's bodies byte-equal to Validate's", async () => {
    page = await builderPage();
    await fillBuilt(page);
    expect(page.sent).toEqual([]);
    await page.press("Validate");
    expect(textOf(viewOf(page, "validation"))).toContain(WORDS.valid);
    await page.press("Count");
    await page.press("Run");
    expect(page.sent.map((one) => one.tool)).toEqual(["validate_document", "count_cohort", "run_analysis"]);
    // Each body is the request the server's fixture answers, byte for byte (the server records it).
    expect(page.sent.map((one) => one.body)).toEqual([ANSWERS.validated.request, ANSWERS.counted.request, ANSWERS.ran.request]);
    expect(documentOf(page.sent[0])).toEqual(BUILT);
    expect(page.sent[1]?.body).toBe(page.sent[0]?.body);
    expect(page.sent[2]?.body).toBe(page.sent[0]?.body);
    const counts = textOf(viewOf(page, "count"));
    expect(counts).toContain("n_true 13; n_false 27; n_unknown 0");
    expect(counts).toContain("n_true 40; n_false 0; n_unknown 0");
    expect(counts).not.toContain("0.325");
    // Run shows the result (D435): its figures, its provenance, in place.
    const ran = textOf(viewOf(page, "run"));
    expect(ran).toMatch(/Derivation drv:[0-9a-f]{64}: analysis summary\.distribution version 1\.0\.0/u);
    expect(ran).toContain("numerator 13; denominator 40; estimate 0.325");
    expect(page.text()).not.toContain(WORDS.stale);
  });

  it("keeps the result under the stale words after an edit, and a remount (a reload) shows none (D435)", async () => {
    page = await builderPage();
    await fillBuilt(page);
    await page.press("Validate");
    await page.press("Run");
    expect(textOf(viewOf(page, "run"))).toMatch(/Derivation drv:[0-9a-f]{64}/u);
    await page.press("Add a column");
    const ran = textOf(viewOf(page, "run"));
    expect(ran).toContain(WORDS.stale);
    expect(ran).toMatch(/Derivation drv:[0-9a-f]{64}/u);
    page.close();
    page = await builderPage();
    expect(viewOf(page, "run")).toBeNull();
    expect(page.text()).not.toMatch(/Derivation drv:/u);
  });

  it("sends nothing on mount, and nothing again by itself", async () => {
    page = await builderPage();
    await settle();
    expect(page.sent).toEqual([]);
    await fillBuilt(page);
    await page.press("Validate");
    await settle();
    await settle();
    expect(page.sent.length).toBe(1);
  });

  it("shows a validation's cohorts and view: name, id and status, release, disclosure, caveats; and what it does not show", async () => {
    page = await builderPage();
    await fillBuilt(page);
    await page.press("Validate");
    const text = textOf(viewOf(page, "validation"));
    expect(text).toMatch(/Cohort first\. Id drv:[0-9a-f]{64}: not issued yetRelease orchard@1 · manifest sha256:[0-9a-f]{64}Disclosure: none/u);
    expect(text).toContain("Cohort second.");
    expect(text).toMatch(/View summary\.distribution version 1\.0\.0\. Id drv:[0-9a-f]{64}: not issued yet/u);
    expect(text).toContain("UNCONFIRMED_SEMANTICS (warning)");
    const answer = JSON.parse(ANSWERS.validated.body) as { cohorts: { computation_id: string }[] };
    expect(text).not.toContain(answer.cohorts[0]?.computation_id ?? "?");
    expect(text).not.toContain(WORDS.problems);
    expect(text).not.toContain(WORDS.translation);
  });
});

describe("the fixed words", () => {
  it("are these, by value (a reviewed change: round 1 found the stale words, the field's note and the reload words claiming more than the page holds)", () => {
    expect(WORDS).toEqual({
  heading: "Build an analysis",
  noDataset: "This dataset's id cannot be sent in a document: the builder is not offered.",
  leaves: "Conditions are offered on columns of text and of categories only.",
  view: "Only the unit table's columns of categories, booleans, numbers and time offsets are offered for the distribution.",
  underK: "Under this dataset's disclosure setting only the unit table's columns of categories and booleans are offered for the distribution.",
  partial: "Only the columns of this page of the dataset's description are offered.",
  tables: "A table whose id this page cannot send is not offered.",
  oneLine: "One line.",
  noCondition: "No condition.",
  chooseUnit: "Choose a unit table first.",
  incomplete: "Pick a unit, a column for each condition and each analysis column, and type each condition's value.",
  empty: "A condition needs a value.",
  notValid: "Validate the document to count its cohorts or run it.",
  pending: "Waiting for the server…",
  failed: "The request failed. Press the button again to ask again.",
  deadline: "No answer came within 45 seconds; the server may still be working on this or an earlier request. Press the button again to ask again.",
  stale: "The form has been edited since this was asked; Validate again.",
  valid: "The document is valid.",
  invalid: "The document is not valid.",
  problems: "The server found these problems in the document:",
  translation: "The answer holds a translation of the document, which is not shown here.",
    });
  });

  it("the deadline's words name the queue's own deadline (D431's DEADLINE_MS)", () => {
    expect(DEADLINE_MS % 1000).toBe(0);
    expect(WORDS.deadline).toContain(`within ${String(DEADLINE_MS / 1000)} seconds`);
    expect(WORDS.deadline.match(/\d+/gu)).toEqual([String(DEADLINE_MS / 1000)]);
  });
});

describe("the gates and the stale words", () => {
  it("Count and Run wait for a valid validation of the form as it stands; an edit puts every view under the stale words", async () => {
    page = await builderPage();
    await fillBuilt(page);
    expect(page.button("Count").disabled).toBe(true);
    expect(page.text()).toContain(WORDS.notValid);
    await page.press("Validate");
    expect(page.button("Count").disabled).toBe(false);
    expect(page.button("Run").disabled).toBe(false);
    await page.press("Count");
    await page.press("Run");
    await page.type(0, "applex");
    await page.type(0, "apple");
    for (const label of ["validation", "count", "run"] as const) {
      expect(textOf(viewOf(page, label)), label).toContain(WORDS.stale);
    }
    expect(page.button("Count").disabled).toBe(true);
    expect(page.button("Run").disabled).toBe(true);
    expect(page.text()).toContain(WORDS.notValid);
    await page.press("Count");
    expect(page.sent.length).toBe(3);
    await page.press("Validate");
    expect(textOf(viewOf(page, "validation"))).not.toContain(WORDS.stale);
    expect(textOf(viewOf(page, "count"))).toContain(WORDS.stale);
    expect(page.button("Count").disabled).toBe(false);
  });

  it("an invalid validation shows its lead words and its problems; Count and Run stay disabled", async () => {
    // A synthetic answer: the server's answer to a string view column (which this form does not offer), served to this form's document.
    page = await builderPage(synthetic(ANSWERS.stringView));
    await fillBuilt(page);
    await page.press("Validate");
    const text = textOf(viewOf(page, "validation"));
    expect(text).toContain(WORDS.invalid);
    expect(text).toContain(WORDS.problems);
    expect(text).toContain("NOT_SUPPORTED at /views/0/params/columns/0/column");
    expect(page.button("Count").disabled).toBe(true);
    expect(page.button("Run").disabled).toBe(true);
  });

  it("Validate is disabled while a pick is missing or a value empty, with the words of each", async () => {
    page = await builderPage();
    expect(page.button("Validate").disabled).toBe(true);
    expect(page.text()).toContain(WORDS.incomplete);
    expect(page.text()).toContain(WORDS.chooseUnit);
    expect(page.selects("Column")[0]?.disabled).toBe(true);
    await page.choose("Unit table", "trees");
    expect(page.text()).not.toContain(WORDS.chooseUnit);
    await page.choose("Column", "trees.variety", 0);
    expect(page.button("Validate").disabled).toBe(false);
    await page.press("Add a condition");
    expect(page.text()).toContain(WORDS.incomplete);
    await page.choose("Column", "trees.tree_id", 0);
    expect(page.text()).toContain(WORDS.empty);
    expect(page.button("Validate").disabled).toBe(true);
    await page.type(0, "tree1");
    expect(page.button("Validate").disabled).toBe(false);
    expect(page.text()).not.toContain(WORDS.empty);
    expect(page.text()).not.toContain(WORDS.incomplete);
  });

  it("refused, failed and unread answers are shown through their own words, never silent", async () => {
    // Synthetic answers, served to this form's document: the server's 422 to a numeric view's count, a shape the page does not read, no answer.
    for (const [answer, words] of [
      [() => respond(ANSWERS.numericCountUnderK), "MISSING_MEMBER at /views/0/params/columns/0/bins"],
      [() => json('{"valid": 3}'), SHAPE_MISMATCH],
      [() => Promise.reject(new TypeError("<b>secret</b>")), UNREACHABLE],
    ] as const) {
      page = await builderPage(answer);
      await fillBuilt(page);
      await page.press("Validate");
      expect(textOf(viewOf(page, "validation"))).toContain(words);
      expect(page.container.innerHTML).not.toContain("secret");
      expect(page.button("Validate").disabled).toBe(false);
      page.close();
      page = undefined;
    }
  });

  it("the stub answers a request by its body alone: a body the server never answered fails the cell when the page closes", async () => {
    page = await builderPage();
    await fillBuilt(page);
    await page.type(0, "pear");
    await page.press("Validate");
    const unanswered = page;
    page = undefined;
    expect(() => {
      unanswered.close();
    }).toThrow(/no answer of the server's to this request: validate_document/u);
  });

  it("a call that fails is said in the builder's own words, and a second press asks again", async () => {
    vi.resetModules();
    let calls = 0;
    vi.doMock("../../src/api/document", async (original) => ({
      ...(await original<object>()),
      validateDraft: () => {
        calls += 1;
        return calls === 1 ? Promise.reject(new Error("<b>secret</b>")) : Promise.resolve({ kind: "invalid", cause: "shape" });
      },
    }));
    try {
      page = await builderPage();
      await fillBuilt(page);
      await page.press("Validate");
      expect(textOf(viewOf(page, "validation"))).toContain(WORDS.failed);
      expect(page.text()).not.toContain("Reload");
      expect(page.container.innerHTML).not.toContain("secret");
      expect(page.button("Validate").disabled).toBe(false);
      await page.press("Validate");
      expect(calls).toBe(2);
      expect(textOf(viewOf(page, "validation"))).toContain(SHAPE_MISMATCH);
    } finally {
      vi.doUnmock("../../src/api/document");
    }
  });
});

describe("the queue and the clock", () => {
  it("a deadline: the buttons are enabled again, a second Validate is sent while the overdue one keeps its place, and a third waits", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const held: ((response: Response) => void)[] = [];
    page = await builderPage(
      () =>
        new Promise<Response>((resolve) => {
          held.push(resolve);
        }),
    );
    await fillBuilt(page);
    await page.press("Validate");
    expect(page.button("Validate").disabled).toBe(true);
    expect(textOf(viewOf(page, "validation"))).toContain(WORDS.pending);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(textOf(viewOf(page, "validation"))).toContain(WORDS.deadline);
    expect(page.text()).not.toContain("Reload");
    expect(page.button("Validate").disabled).toBe(false);
    await page.press("Validate");
    expect(page.sent.length).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    await page.press("Validate");
    expect(page.sent.length).toBe(2);
    expect(page.button("Validate").disabled).toBe(true);
    held[0]?.(respond(ANSWERS.validated));
    await settle();
    expect(page.sent.length).toBe(3);
    held[2]?.(respond(ANSWERS.validated));
    await settle();
    expect(textOf(viewOf(page, "validation"))).toContain(WORDS.valid);
    held[1]?.(respond(ANSWERS.validated));
    await settle();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("no clock runs once each call has settled", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    page = await builderPage();
    await fillBuilt(page);
    expect(vi.getTimerCount()).toBe(0);
    for (const name of ["Validate", "Count", "Run"]) {
      await page.press(name);
      expect(vi.getTimerCount(), name).toBe(0);
    }
  });

  it("the idle store gives one frozen object, and sends nothing", async () => {
    const { builder } = await freshBuilder();
    const first = builder.IDLE.snapshot();
    expect(builder.IDLE.snapshot()).toBe(first);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(builder.IDLE)).toBe(true);
    const unsubscribe = builder.IDLE.subscribe(() => undefined);
    unsubscribe();
    expect(builder.IDLE.snapshot()).toBe(first);
  });
});

describe("the lists and their words", () => {
  it("offers each unit's own view columns of the offered datatypes, and leaves of text and categories, with the words of what is left out", async () => {
    page = await builderPage();
    const options = (select: HTMLSelectElement | undefined): string[] => [...(select?.options ?? [])].map((one) => one.text);
    expect(options(page.selects("Unit table")[0])).toEqual(["(choose)", "harvests \u2014 harvests", "trees \u2014 trees"]);
    expect(options(page.selects("Column")[0])).toEqual(["(choose)"]);
    await page.choose("Unit table", "trees");
    expect(options(page.selects("Column")[0])).toEqual(["(choose)", "trees.height_m \u2014 height_m", "trees.variety \u2014 variety"]);
    await page.choose("Unit table", "harvests");
    expect(options(page.selects("Column")[0])).toEqual(["(choose)", "harvests.grade \u2014 grade", "harvests.kg \u2014 kg"]);
    await page.press("Add a condition");
    expect(options(page.selects("Column")[0])).toEqual([
      "(choose)",
      "harvests.grade \u2014 grade",
      "harvests.harvest_id \u2014 harvest_id",
      "harvests.tree_id \u2014 tree_id",
      "trees.tree_id \u2014 tree_id",
      "trees.variety \u2014 variety",
    ]);
    const text = page.text();
    expect(text).toContain(WORDS.leaves);
    expect(text).toContain(WORDS.view);
    expect(text).not.toContain(WORDS.underK);
    expect(text).not.toContain(WORDS.partial);
    expect(text).not.toContain(WORDS.tables);
    expect(text).toContain(WORDS.oneLine);
  });

  it("changing the unit clears exactly the view picks that are not of the new unit's", async () => {
    page = await builderPage();
    await page.choose("Unit table", "trees");
    await page.choose("Column", "trees.variety", 0);
    await page.choose("Unit table", "trees");
    expect(page.selects("Column")[0]?.selectedIndex).toBe(2);
    await page.choose("Unit table", "harvests");
    expect(page.selects("Column")[0]?.selectedIndex).toBe(0);
  });

  it("the first page of the columns: the words of a partial list", async () => {
    page = await builderPage(byRequest, DESCRIBED.firstPage);
    expect(page.text()).toContain(WORDS.partial);
  });

  it("a table whose id the page cannot send is not offered, with its words", async () => {
    const body = JSON.parse(DESCRIBED.whole) as { graph: { tables: string[] } };
    body.graph.tables.push("Not-An-Id");
    page = await builderPage(byRequest, JSON.stringify(body));
    expect([...(page.selects("Unit table")[0]?.options ?? [])].map((one) => one.text)).toEqual(["(choose)", "harvests \u2014 harvests", "trees \u2014 trees"]);
    expect(page.text()).toContain(WORDS.tables);
  });

  it("under a floor (D431's floored answer): only the unit's columns of categories and booleans are offered, with their words", async () => {
    page = await builderPage(byRequest, DESCRIBED.floored);
    await page.choose("Unit table", "trees");
    expect([...(page.selects("Column")[0]?.options ?? [])].map((one) => one.text)).toEqual(["(choose)", "trees.variety \u2014 variety"]);
    expect(page.text()).toContain(WORDS.underK);
    expect(page.text()).not.toContain(WORDS.view);
  });

  it("the server's answers under a floor: every count suppressed; a numeric view's refusals, valid false and a 422, shown through Refusal", async () => {
    page = await builderPage(byRequest, DESCRIBED.floored);
    await page.choose("Unit table", "trees");
    await page.press("Add a condition");
    await page.choose("Column", "trees.variety", 0);
    await page.type(0, "apple");
    await page.choose("Column", "trees.variety", 1);
    await page.press("Validate");
    await page.press("Count");
    expect(page.sent[1]?.body).toBe(ANSWERS.countedUnderK.request);
    const counted = textOf(viewOf(page, "count"));
    expect(counted).toContain("n_true suppressed; n_false suppressed; n_unknown suppressed");
    expect(counted).toContain("Disclosure: minimum cell count k = 50");
    page.close();
    page = await builderPage(byRequest);
    await page.choose("Unit table", "trees");
    await page.press("Add a condition");
    await page.choose("Column", "trees.variety", 0);
    await page.type(0, "apple");
    await page.choose("Column", "trees.height_m", 1);
    await page.press("Validate");
    expect(page.sent[0]?.body).toBe(ANSWERS.numericUnderK.request);
    const validation = textOf(viewOf(page, "validation"));
    expect(validation).toContain(WORDS.invalid);
    expect(validation).toContain("MISSING_MEMBER at /views/0/params/columns/0/bins");
  });

  it("a dataset whose id the page cannot send offers no builder, in fixed words", async () => {
    const body = JSON.parse(DESCRIBED.whole) as { dataset: string };
    body.dataset = "Not An Id";
    page = await builderPage(byRequest, JSON.stringify(body));
    expect(page.text()).toBe(`${WORDS.heading}${WORDS.noDataset}`);
    expect(page.container.querySelectorAll("button")).toHaveLength(0);
  });
});

describe("the dataset's screen", () => {
  async function at(hash: string, sent: Sent[]): Promise<BuilderPage> {
    vi.resetModules();
    const { catalogueRoutes } = await import("../../src/catalogue/routes");
    vi.stubGlobal("fetch", (url: string, init?: RequestInit): Promise<Response> => {
      const one: Sent = { tool: url.slice(url.lastIndexOf("/") + 1), body: typeof init?.body === "string" ? init.body : null };
      sent.push(one);
      return Promise.resolve(one.tool === "describe_dataset" ? json(DESCRIBED.whole) : byRequest(one));
    });
    window.history.replaceState(null, "", `/${hash}`);
    const shown = await render(
      <StrictMode>
        <RouterProvider router={createHashRouter(catalogueRoutes())} />
      </StrictMode>,
    );
    await shown.until((words) => words.includes(WORDS.heading));
    return pageOf(shown, sent);
  }

  it("its load sends one describe_dataset and nothing else; the builder stands under the description, keyed by the dataset", async () => {
    const sent: Sent[] = [];
    page = await at("#/datasets/orchard", sent);
    await settle();
    expect(sent.map((one) => one.tool)).toEqual(["describe_dataset"]);
    await page.choose("Unit table", "trees");
    expect(page.selects("Unit table")[0]?.selectedIndex).toBe(2);
    await act(async () => {
      window.location.hash = "#/datasets/walnuts";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    await settle();
    expect(sent.map((one) => one.tool)).toEqual(["describe_dataset", "describe_dataset"]);
    expect(page.selects("Unit table")[0]?.selectedIndex).toBe(0);
  });
});

describe("the outcome views, swept", () => {
  const OWN = ["DIV", "P", "SECTION", "H3"];
  const CLASSES = ["", "number", "release", "label", "label draft", "disagree", "suppressed", "none", "largest", "affects", "id"];

  it("shows a hostile text at every position of a validation as text alone, by construction", { timeout: 180_000 }, async () => {
    const found = await leafSweep(walked("DocumentValidation", ANSWERS.validated.body) as Answered<"validate_document">, (value) => <ValidationOf value={value} />, OWN, CLASSES);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(100);
  });

  it("shows a hostile text at every position of a count as text alone, by construction", { timeout: 180_000 }, async () => {
    const found = await leafSweep(walked("CohortCounts", ANSWERS.counted.body) as Answered<"count_cohort">, (value) => <CountsOf value={value} />, OWN, CLASSES);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(100);
  });
});
