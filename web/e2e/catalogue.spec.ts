/**
 * The catalogue, the dataset and the column screens (D431, D443), the end-to-end path's first steps, through the
 * real server and both builds, under the documents' policy (no CSP or Trusted Types violation,
 * nothing failing unexpectedly). Operator cells publish and withdraw datasets in parallel, so each
 * cell asserts the orchard's or the hostile headers' own hit and page, never a total or an order.
 * The orchard (`fixtures/orchard`, 40 trees and 60 harvests) and the hostile headers
 * (`fixtures/hostile`, with a hostile file name, a table's label, D440, that the global setup
 * writes beside them) are imported by the global setup.
 */
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

/** The tool calls a page sends (`POST /api/tools/<name>`), by name, in order. */
function toolCalls(page: Page): string[] {
  const calls: string[] = [];
  page.on("request", (request) => {
    const found = /\/api\/tools\/([a-z_]+)$/u.exec(new URL(request.url()).pathname);
    if (found?.[1] !== undefined) {
      calls.push(found[1]);
    }
  });
  return calls;
}

test.describe("the catalogue (#/)", () => {
  test("shows the orchard's hit: its release @1 and its tables' rows with their references, one search_catalog", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    await page.goto(`${origin}/`);
    const hit = page.locator("section.hit", { has: page.getByRole("link", { name: "orchard", exact: true }) });
    await expect(hit).toBeVisible();
    await expect(hit).toContainText("@1");
    await expect(hit).toContainText("status published");
    await expect(hit).toContainText(/harvests harvests, grain One row per harvest_id, role measurement, rows 60 stat:sha256:[0-9a-f]{64}\/harvests\/n_rows/u);
    await expect(hit).toContainText(/trees trees, grain One row per tree_id, role entity, rows 40 stat:sha256:[0-9a-f]{64}\/trees\/n_rows/u);
    await expect(page.getByRole("link", { name: "Curate" })).toHaveAttribute("href", "/curate");
    expect(calls).toEqual(["search_catalog"]);
    watcher.clean();
  });

  test("links the orchard's hit to its screen: tables, columns, analyses with their statuses, the caveat and what it does not show", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    await page.goto(`${origin}/`);
    await page.getByRole("link", { name: "orchard", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Dataset orchard" })).toBeVisible();
    expect(await page.evaluate(() => location.hash)).toBe("#/datasets/orchard");
    const main = page.locator("main");
    await expect(main).toContainText("Relationship rel:harvests.tree_id: harvests to trees, many to one");
    await expect(main).toContainText("harvests.harvest_id harvest_id, string, identifier");
    await expect(main).toContainText("trees.height_m height_m, number");
    await expect(main).not.toContainText("not an identifier");
    await expect(main).toContainText("survival.km version 1.1.0: unavailable; missing: endpoint");
    await expect(main).toContainText("summary.distribution version 1.0.0: available with caveats; unconfirmed: columns");
    await expect(main).toContainText("UNCONFIRMED_SEMANTICS (warning)");
    await expect(main).toContainText(/Table trees \(trees\)Rows: 40 stat:sha256:[0-9a-f]{64}\/trees\/n_rows/u);
    await expect(main.locator(".legend")).toBeVisible();
    await expect(main.locator(".legend")).toContainText("are not shown on this page yet. aibi's server-rendered page at /datasets/orchard shows them");
    expect(calls).toEqual(["search_catalog", "describe_dataset"]);
    watcher.clean();
  });
});

test.describe("the dataset's screen (#/datasets/<id>)", () => {
  test("/datasets/orchard is replaced by /#/datasets/orchard and shows it, one describe_dataset", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    await page.goto(`${origin}/datasets/orchard`);
    await expect(page.getByRole("heading", { level: 1, name: "Dataset orchard" })).toBeVisible();
    await expect(page.locator("main .legend")).toBeVisible();
    expect(await page.evaluate(() => `${location.pathname}${location.search}${location.hash}`)).toBe("/#/datasets/orchard");
    expect(calls).toEqual(["describe_dataset"]);
    watcher.clean();
  });

  test("shows the hostile headers and the hostile file name as text: visible escapes, no element from data, the long label cut, no link but each column's to its screen", async ({ page, origin, watcher }) => {
    await page.goto(`${origin}/#/datasets/hostile`);
    await expect(page.getByRole("heading", { level: 1, name: "Dataset hostile" })).toBeVisible();
    const named = page.locator("section.table h3");
    await expect(named).toHaveText(["Table b_gpj (<b>\\u202egpj)", "Table hostile (hostile)"]);
    await expect(named.first().locator(".escape", { hasText: "\\u202e" })).toHaveCount(1);
    const columns = page.locator("section.table", { has: page.getByRole("heading", { level: 3, name: /^Table hostile / }) }).locator("[aria-label=columns]");
    await expect(columns).toContainText("<img src=x onerror=alert(1)>");
    await expect(columns).toContainText("</bdi><b>bold</b><bdi>");
    await expect(columns).toContainText("javascript:alert(1)");
    await expect(columns.locator(".escape", { hasText: "\\u202e" })).toHaveCount(1);
    await expect(columns.locator(".escape", { hasText: "\\u200b" })).toHaveCount(1);
    await expect(columns.locator(".escape", { hasText: "\\u2060" })).toHaveCount(1);
    await expect(columns.locator(".cut")).toHaveCount(1);
    expect(await page.locator("main img, main b, main script, main iframe").count()).toBe(0);
    // The one link of each column (D443: seven, the hostile table's six and b_gpj's id), each to its
    // column's screen, minted from the answer's id: never a label, a script URL or another origin.
    const links = await page.locator("main a").evaluateAll((anchors) => anchors.map((anchor) => [anchor.getAttribute("href"), anchor.textContent]));
    expect(links).toHaveLength(7);
    for (const [href, id] of links) {
      expect(href).toMatch(/^#\/datasets\/hostile\/columns\/[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/u);
      expect(href).toBe(`#/datasets/hostile/columns/${id ?? ""}`);
    }
    const text = await page.locator("main").evaluate((main) => main.textContent);
    expect(/[\u202e\u200b\u2060]/u.test(text)).toBe(false);
    expect(await page.evaluate(() => document.querySelectorAll("img, script:not([src])").length)).toBe(0);
    watcher.clean();
  });

  test("links each column's id to the column's screen (D443): the orchard's trees.variety, its states and categories with their references, one describe_column more", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    await page.goto(`${origin}/#/datasets/orchard`);
    await expect(page.getByRole("heading", { level: 1, name: "Dataset orchard" })).toBeVisible();
    await expect(page.locator("main .legend")).toContainText("Neither gives a column's descriptor");
    await page.getByRole("link", { name: "trees.variety", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Column trees.variety (variety)" })).toBeVisible();
    expect(await page.evaluate(() => location.hash)).toBe("#/datasets/orchard/columns/trees.variety");
    const main = page.locator("main");
    await expect(main).toContainText(/PRESENT: 40 stat:sha256:[0-9a-f]{64}\/trees\.variety\/states\/PRESENT/u);
    await expect(main).toContainText(/apple: 13 stat:sha256:[0-9a-f]{64}\/trees\.variety\/categories\/apple/u);
    await expect(main.locator(".legend")).toBeVisible();
    await expect(main.locator(".legend")).toContainText("The values of the column's descriptor are not shown on this page yet");
    await expect(page.getByRole("link", { name: "The dataset", exact: true })).toHaveAttribute("href", "#/datasets/orchard");
    expect(calls).toEqual(["describe_dataset", "describe_column"]);
    watcher.clean();
  });

  test("shows the hostile values' column (D443): every category value as text, visible escapes, the long one cut, no element from data, one describe_column", async ({ page, origin, watcher }) => {
    const calls = toolCalls(page);
    await page.goto(`${origin}/#/datasets/hostile_values/columns/items.kind`);
    await expect(page.getByRole("heading", { level: 1, name: /^Column items\.kind \(/u })).toBeVisible();
    const categories = page.locator("[aria-label=categories]");
    await expect(categories).toContainText("<img src=x onerror=alert(1)>");
    await expect(categories).toContainText("</bdi><b>bold</b><bdi>");
    await expect(categories).toContainText("javascript:alert(1)");
    await expect(categories.locator(".escape", { hasText: "\\u202e" })).toHaveCount(1);
    await expect(categories.locator(".escape", { hasText: "\\u200b" })).toHaveCount(1);
    await expect(categories.locator(".cut")).toHaveCount(1);
    expect(await page.locator("main img, main b, main script, main iframe").count()).toBe(0);
    // The one link: back to the dataset, by the address's id.
    expect(await page.locator("main a").evaluateAll((anchors) => anchors.map((anchor) => anchor.getAttribute("href")))).toEqual(["#/datasets/hostile_values"]);
    const text = await page.locator("main").evaluate((main) => main.textContent);
    expect(/[\u202e\u200b\u2060]/u.test(text)).toBe(false);
    expect(calls).toEqual(["describe_column"]);
    watcher.clean();
  });

  test("#/datasets/x shows the server's refusal in fixed words and as data (a 404)", async ({ page, origin, watcher }) => {
    watcher.expect(`${origin}/api/tools/describe_dataset`, 404);
    const calls = toolCalls(page);
    await page.goto(`${origin}/#/datasets/x`);
    await expect(page.getByText("The dataset could not be described.")).toBeVisible();
    await expect(page.getByText("UNKNOWN_DATASET", { exact: true })).toBeVisible();
    expect(calls).toEqual(["describe_dataset"]);
    watcher.clean();
  });
});
