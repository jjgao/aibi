/**
 * The result screen (D435), the end of the end-to-end path: a document built on the dataset's screen
 * and run, its result read in place, through the real server and both builds, under the documents'
 * policy (no CSP or Trusted Types violation, nothing failing unexpectedly): the orchard's two-cohort
 * run, its re-run served from the result cache, an empty cohort, and the hostile values
 * (`fixtures/hostile_values`), each text shown as text. On each, what the page shows is what its DOM
 * holds (`seen`, D435's round 3): no text hidden from the rendered text, none added before or after an
 * element, and no stylesheet rule declaring a property the app's stylesheets do not declare today
 * (a value of an allowed property that hides text is not seen); its controls fail it.
 */
import type { Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { choose } from "./options";

/** A dataset's builder. */
async function open(page: Page, origin: string, dataset: string): Promise<Locator> {
  await page.goto(`${origin}/#/datasets/${dataset}`);
  const builder = page.locator("section.builder");
  await expect(builder.getByRole("heading", { level: 2, name: "Build an analysis" })).toBeVisible();
  return builder;
}

/** Builds a document: the unit, conditions on the first cohort, a second cohort of every unit, the view's columns (each
 * list's option chosen by its id, `choose`). */
async function build(builder: Locator, unit: string, conditions: readonly (readonly [string, string])[], second: boolean, view: readonly string[]): Promise<void> {
  await choose(builder.locator(":scope > label > select"), unit);
  for (const [column, value] of conditions) {
    await builder.locator('section.cohort[aria-label="cohort first"]').getByRole("button", { name: "Add a condition" }).click();
    const leaf = builder.locator('section.cohort[aria-label="cohort first"] .leaf').last();
    await choose(leaf.locator("select"), column);
    await leaf.locator("input").fill(value);
  }
  if (second) {
    await builder.getByRole("button", { name: "Add a second cohort" }).click();
  }
  for (const [index, column] of view.entries()) {
    if (index > 0) {
      await builder.getByRole("button", { name: "Add a column" }).click();
    }
    await choose(builder.locator("section.view .column select").last(), column);
  }
}

/** The properties the app's stylesheets declare today, as the browser expands them (pinned: a rule
 * that declares another fails `seen`, and a new one is added here with its reason): boxes and
 * spacing (`border-*`, `margin-*`, `padding-*`), type (`font-*`, `color`), `white-space` (and its
 * longhands: a text from data keeps its tabs and line feeds), and the escape's isolate, left to right
 * (`unicode-bidi: isolate`, `direction: ltr`, pinned with their values). */
const ALLOWED_PREFIXES = ["border-", "margin-", "padding-", "font-"];
const ALLOWED = ["color", "white-space", "white-space-collapse", "text-wrap-mode"];
const PINNED: Readonly<Record<string, string>> = { "unicode-bidi": "isolate", direction: "ltr" };

/** What a stylesheet or an attribute would hide or add, on the run's view as the browser lays it out:
 * its rendered text (`innerText`) against its DOM's (`textContent`), whitespace aside; every element's
 * `::before` and `::after` content; and every rule of the stylesheets the page has loaded, each of
 * whose properties is one the app declares today (`ALLOWED`, `PINNED`), whether or not this document
 * has an element of the rule's class. A value of an allowed property that hides text (a transparent
 * colour, a zero font size, a margin that moves it away) is not seen. */
async function seenProblems(shown: Locator): Promise<string[]> {
  return shown.evaluate(
    (root, allowed) => {
      const found: string[] = [];
      const strip = (text: string): string => text.replace(/\s+/gu, "");
      if (!(root instanceof HTMLElement) || strip(root.innerText) !== strip(root.textContent)) {
        found.push("the rendered text is not the DOM's text");
      }
      for (const element of [root, ...root.querySelectorAll("*")]) {
        for (const pseudo of ["::before", "::after"]) {
          const content = getComputedStyle(element, pseudo).content;
          if (content !== "none" && content !== "normal") {
            found.push(`${element.tagName}${pseudo}: ${content}`);
          }
        }
      }
      // Each rule, then the rules nested in it (a style rule is itself a grouping rule in the CSSOM).
      const rulesOf = (rules: CSSRuleList): CSSRule[] => [...rules].flatMap((rule) => [rule, ...(rule instanceof CSSGroupingRule ? rulesOf(rule.cssRules) : [])]);
      for (const sheet of document.styleSheets) {
        for (const rule of rulesOf(sheet.cssRules)) {
          if (!(rule instanceof CSSStyleRule)) {
            continue;
          }
          for (const name of rule.style) {
            const value = rule.style.getPropertyValue(name).trim();
            const pinned = allowed.pinned[name];
            const ok = pinned === undefined ? allowed.exact.includes(name) || allowed.prefixes.some((prefix) => name.startsWith(prefix)) : value === pinned;
            if (!ok) {
              found.push(`${rule.selectorText} declares ${name}: ${value}`);
            }
          }
        }
      }
      return found;
    },
    { prefixes: ALLOWED_PREFIXES, exact: ALLOWED, pinned: PINNED },
  );
}

/** The run's view shows what its DOM holds (`seenProblems`). */
async function seen(shown: Locator): Promise<void> {
  expect(await seenProblems(shown)).toEqual([]);
}

/** Validates and runs the form's document; the run's view. */
async function run(builder: Locator): Promise<Locator> {
  await builder.getByRole("button", { name: "Validate", exact: true }).click();
  await expect(builder.locator('section.outcome[aria-label="validation"]')).toContainText("The document is valid.");
  await builder.getByRole("button", { name: "Run", exact: true }).click();
  const shown = builder.locator('section.outcome[aria-label="run"]');
  await expect(shown.locator('section[aria-label="provenance"]')).toBeVisible();
  return shown;
}

test.describe("the result screen (D435)", () => {
  test("(a) the orchard's run, a document no other spec runs: each position's label, readback, population, analysed counts and values; the provenance and its issuance; the charts' words", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin, "orchard");
    await build(builder, "trees", [["trees.variety", "apple"]], true, ["trees.variety", "trees.height_m"]);
    const shown = await run(builder);
    const positions = shown.locator('section[aria-label="position"]');
    await expect(positions).toHaveCount(2);
    await expect(positions.nth(0).locator("h3")).toHaveText("first");
    await expect(positions.nth(0).locator("p.readback")).toContainText("the variety is apple");
    await expect(positions.nth(0)).toContainText("n_true 13; n_false 27; n_unknown 0");
    await expect(positions.nth(0)).toContainText("Analysed: n 13; excluded_units 0;");
    await expect(positions.nth(0)).toContainText("n 12; mean 6.25; sd 3.0507078165268764; median 6.25");
    await expect(positions.nth(0).locator("span.open", { hasText: "no lower edge" })).toHaveCount(1);
    await expect(positions.nth(1)).toContainText("numerator 13; denominator 40; estimate 0.325");
    await expect(shown).toContainText("Disclosure: none (no minimum cell count applies).");
    // The issuance's sentence matches the issuance: served from the cache only when its values come
    // from another issuance.
    const issuance = shown.locator('section[aria-label="provenance"] p', { hasText: "Issuance" });
    const ids = await issuance.locator("bdi").allTextContents();
    if ((await issuance.textContent())?.includes("served from the result cache") === true) {
      expect(ids).toHaveLength(2);
      expect(ids[1]).not.toBe(ids[0]);
    } else {
      await expect(issuance).toContainText("computed for this request.");
      expect(ids).toHaveLength(1);
    }
    await expect(shown).toContainText("This result carries charts; none is drawn on this page");
    await seen(shown);
    watcher.clean();
  });

  test("(b) the same document run again is served from the result cache, naming the first run's issuance", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin, "orchard");
    await build(builder, "trees", [["trees.variety", "pear"]], false, ["trees.variety"]);
    const first = await run(builder);
    const issuance = (await first.locator('section[aria-label="provenance"] p', { hasText: "Issuance" }).locator("bdi").first().textContent()) ?? "";
    expect(issuance).toMatch(/^iss:/u);
    await builder.getByRole("button", { name: "Run", exact: true }).click();
    const again = builder.locator('section.outcome[aria-label="run"] section[aria-label="provenance"]');
    await expect(again).toContainText(`served from the result cache: its values come from issuance ${issuance}`);
    await seen(builder.locator('section.outcome[aria-label="run"]'));
    watcher.clean();
  });

  test("(c) an empty cohort: every number not estimable with its reason, never 0 or blank; no category row", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin, "orchard");
    await build(builder, "trees", [["trees.variety", "zzz"]], false, ["trees.height_m", "trees.variety"]);
    const shown = await run(builder);
    await expect(shown).toContainText("mean not estimable: no units");
    await expect(shown).toContainText("histogram not estimable: no units");
    await expect(shown).toContainText("This column lists no category row for this position.");
    await seen(shown);
    watcher.clean();
  });

  test("(d) the hostile values: each a text, no element made, escapes and the cut mark shown", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin, "hostile_values");
    await build(builder, "items", [], false, ["items.kind"]);
    const shown = await run(builder);
    const values = shown.locator('section[aria-label="column"]');
    await expect(values.locator("bdi.data", { hasText: "<img src=x onerror=alert(1)>" })).toHaveCount(1);
    await expect(values.locator("bdi.data", { hasText: "</bdi><b>bold</b><bdi>" })).toHaveCount(1);
    await expect(values.locator(".escape", { hasText: "\\u202e" })).toHaveCount(1);
    await expect(values.locator(".escape", { hasText: "\\u200b" })).toHaveCount(1);
    await expect(values.locator(".cut")).toHaveCount(1);
    await expect(page.locator("img")).toHaveCount(0);
    await expect(page.locator("main b, section.builder b")).toHaveCount(0);
    await seen(shown);
    watcher.clean();
  });

  test("(e) controls: seen fails on a rule of another class that hides, content after an element, numbers reversed by a bidi override, and a note filtered away", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin, "orchard");
    await build(builder, "trees", [["trees.variety", "plum"]], false, ["trees.height_m"]);
    const shown = await run(builder);
    await seen(shown);
    // Rules added to a stylesheet the page loaded (the CSSOM: no inline style, so the policy holds).
    await page.evaluate(() => {
      const sheet = document.styleSheets[0];
      for (const rule of [".x { display: none; }", '.note::after { content: "x"; }', "bdi.number { unicode-bidi: bidi-override; direction: rtl; }", ".note { filter: opacity(0); }"]) {
        sheet?.insertRule(rule, 0);
      }
    });
    const problems = await seenProblems(shown);
    expect(problems).toEqual(
      expect.arrayContaining([".x declares display: none", '.note::after declares content: "x"', 'P::after: "x"', "bdi.number declares unicode-bidi: bidi-override", "bdi.number declares direction: rtl", ".note declares filter: opacity(0)"]),
    );
    watcher.clean();
  });
});
