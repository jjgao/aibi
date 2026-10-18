/**
 * The builder-lite screens (D433), the end-to-end path's third step, through the real server and
 * both builds, under the documents' policy (no CSP or Trusted Types violation, nothing failing
 * unexpectedly), over the orchard (`fixtures/orchard`, 40 trees, no disclosure floor): a document
 * built through the form's controls, validated, counted and run; hostile values typed; a middle
 * condition removed; the lists the form offers.
 */
import type { Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures";

/** The tool calls a page sends (`POST /api/tools/<name>`), with their bodies, in order. */
function toolCalls(page: Page): { readonly tool: string; readonly body: string }[] {
  const calls: { tool: string; body: string }[] = [];
  page.on("request", (request) => {
    const found = /\/api\/tools\/([a-z_]+)$/u.exec(new URL(request.url()).pathname);
    if (found?.[1] !== undefined) {
      calls.push({ tool: found[1], body: request.postData() ?? "" });
    }
  });
  return calls;
}

/** The orchard's dataset screen, with its builder. */
async function open(page: Page, origin: string): Promise<Locator> {
  await page.goto(`${origin}/#/datasets/orchard`);
  const builder = page.locator("section.builder");
  await expect(builder.getByRole("heading", { level: 2, name: "Build an analysis" })).toBeVisible();
  return builder;
}

const unit = (builder: Locator): Locator => builder.locator(":scope > label > select");
const leaves = (builder: Locator, cohort: "first" | "second"): Locator => builder.locator(`section.cohort[aria-label="cohort ${cohort}"] .leaf`);
const columns = (builder: Locator): Locator => builder.locator("section.view .column select");

/** Add a condition to the first cohort: its column and its value. */
async function condition(builder: Locator, column: string, value: string): Promise<void> {
  await builder.locator('section.cohort[aria-label="cohort first"]').getByRole("button", { name: "Add a condition" }).click();
  const leaf = leaves(builder, "first").last();
  await leaf.locator("select").selectOption({ label: column });
  await leaf.locator("input").fill(value);
}

/** The document the form shows, as the seam writes it. */
function documentOf(first: readonly (readonly [string, string])[], second: boolean, view: readonly string[]): string {
  const cohorts: Record<string, unknown> = { first: { all: first.map(([column, value]) => ({ kind: "value", column, values: [value] })) } };
  if (second) {
    cohorts["second"] = { all: [] };
  }
  return JSON.stringify({
    document: {
      aibi: "1",
      dataset: "orchard",
      unit: "trees",
      cohorts,
      views: [{ analysis: "summary.distribution", cohorts: second ? ["first", "second"] : ["first"], params: { columns: view.map((column) => ({ column })) } }],
    },
  });
}

test.describe("the builder (#/datasets/orchard)", () => {
  test("(a) builds, validates, counts and runs a two-cohort document: one call a press, the bodies the document shown", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    const builder = await open(page, origin);
    await unit(builder).selectOption({ label: "trees" });
    await condition(builder, "trees.variety", "apple");
    await builder.getByRole("button", { name: "Add a second cohort" }).click();
    await columns(builder).first().selectOption({ label: "trees.height_m" });
    await builder.getByRole("button", { name: "Add a column" }).click();
    await columns(builder).last().selectOption({ label: "trees.variety" });
    await builder.getByRole("button", { name: "Validate", exact: true }).click();
    const validation = builder.locator('section.outcome[aria-label="validation"]');
    await expect(validation).toContainText("The document is valid.");
    await expect(validation.locator("p.readback").first()).toContainText("the variety is apple");
    await builder.getByRole("button", { name: "Count", exact: true }).click();
    const count = builder.locator('section.outcome[aria-label="count"]');
    await expect(count).toContainText("n_true 13; n_false 27; n_unknown 0");
    await expect(count).toContainText("n_true 40; n_false 0; n_unknown 0");
    await builder.getByRole("button", { name: "Run", exact: true }).click();
    const run = builder.locator('section.outcome[aria-label="run"]');
    // The run shows the result in place (D435; result.spec.ts reads it).
    await expect(run.locator('section[aria-label="provenance"]')).toContainText(/Derivation drv:[0-9a-f]{64}/u);
    expect(calls.map((one) => one.tool)).toEqual(["describe_dataset", "validate_document", "count_cohort", "run_analysis"]);
    const expected = documentOf([["trees.variety", "apple"]], true, ["trees.height_m", "trees.variety"]);
    expect(calls.slice(1).map((one) => one.body)).toEqual([expected, expected, expected]);
    watcher.clean();
  });

  test("(b) hostile values are one leaf's string value each: no element, visible escapes in the readback, $x sent as $$x", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    const builder = await open(page, origin);
    await unit(builder).selectOption({ label: "trees" });
    await columns(builder).first().selectOption({ label: "trees.variety" });
    const values = ["<img src=x onerror=alert(1)>", "$x", "‮abc"];
    for (const value of values) {
      await condition(builder, "trees.variety", value);
    }
    await builder.getByRole("button", { name: "Validate", exact: true }).click();
    const validation = builder.locator('section.outcome[aria-label="validation"]');
    await expect(validation).toContainText("The document is valid.");
    await expect(page.locator("img")).toHaveCount(0);
    const readback = validation.locator("section.checked p.readback").first();
    await expect(readback.locator("bdi.token", { hasText: "<img src=x onerror=alert(1)>" })).toHaveCount(1);
    await expect(readback.locator("bdi.token .escape", { hasText: "\\u202e" })).toHaveCount(1);
    await expect(readback.locator("bdi.token", { hasText: /^\$x$/u })).toHaveCount(1);
    const validate = calls.find((one) => one.tool === "validate_document");
    expect(validate?.body).toBe(documentOf([["trees.variety", "<img src=x onerror=alert(1)>"], ["trees.variety", "$$x"], ["trees.variety", "‮abc"]], false, ["trees.variety"]));
    watcher.clean();
  });

  test("(c) a middle condition removed: the body and the readback hold exactly the other two", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    const builder = await open(page, origin);
    await unit(builder).selectOption({ label: "trees" });
    await columns(builder).first().selectOption({ label: "trees.variety" });
    await condition(builder, "trees.variety", "apple");
    await condition(builder, "trees.tree_id", "t1");
    await condition(builder, "trees.variety", "pear");
    await leaves(builder, "first").nth(1).getByRole("button", { name: "Remove this condition" }).click();
    await expect(leaves(builder, "first")).toHaveCount(2);
    await expect(leaves(builder, "first").nth(1).locator("input")).toHaveValue("pear");
    await builder.getByRole("button", { name: "Validate", exact: true }).click();
    const readback = builder.locator('section.outcome[aria-label="validation"] section.checked p.readback').first();
    await expect(readback).toContainText("the variety is apple");
    await expect(readback).toContainText("the variety is pear");
    await expect(readback).not.toContainText("tree_id");
    const validate = calls.find((one) => one.tool === "validate_document");
    expect(validate?.body).toBe(documentOf([["trees.variety", "apple"], ["trees.variety", "pear"]], false, ["trees.variety"]));
    watcher.clean();
  });

  test("(d) the lists: the view holds the unit's summarised columns alone, the conditions columns of text and categories", async ({ page, origin, watcher }) => {
    const builder = await open(page, origin);
    await unit(builder).selectOption({ label: "trees" });
    expect(await columns(builder).first().locator("option").allTextContents()).toEqual(["(choose)", "trees.height_m", "trees.variety"]);
    await builder.locator('section.cohort[aria-label="cohort first"]').getByRole("button", { name: "Add a condition" }).click();
    const offered = await leaves(builder, "first").first().locator("select option").allTextContents();
    expect(offered).toEqual(["(choose)", "harvests.grade", "harvests.harvest_id", "harvests.tree_id", "trees.tree_id", "trees.variety"]);
    expect(offered).not.toContain("trees.height_m");
    watcher.clean();
  });
});
