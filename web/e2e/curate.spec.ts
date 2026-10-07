/**
 * Matrix (2): the fetch-metadata matrix of `/curate` (D412). Admitted with `Sec-Fetch-Site`
 * `none` or `same-origin`: a typed address, a click from the catalogue entry, a reload, a script's
 * `location.reload()`, back and forward, and `history.back()` that another site's page calls in
 * the same tab, which reopens `/curate` as a fresh load: `pageshow.persisted` is false and no
 * script state comes back. Refused (`ORIGIN_NOT_ALLOWED`, 403) from another site, from the same
 * site on another port, from `localhost` to `127.0.0.1`, in a frame, and in a window another
 * origin opens.
 */
import type { BrowserContext, Page } from "@playwright/test";

import { expect, other, test } from "./fixtures";

interface Seen {
  readonly status: number;
  readonly site: string | undefined;
  readonly dest: string | undefined;
}

/** Every answer at `/curate` the context's pages and frames get, with what the browser sent. */
function curate(context: BrowserContext): { readonly seen: Seen[]; settled(): Promise<Seen[]> } {
  const seen: Seen[] = [];
  const pending: Promise<void>[] = [];
  context.on("response", (response) => {
    if (new URL(response.url()).pathname !== "/curate") {
      return;
    }
    pending.push(
      response
        .request()
        .allHeaders()
        .then((headers) => {
          seen.push({ status: response.status(), site: headers["sec-fetch-site"], dest: headers["sec-fetch-dest"] });
        }),
    );
  });
  return {
    seen,
    async settled() {
      await Promise.all(pending);
      return seen;
    },
  };
}

/** The only values `/curate` admits (D412). Which one a reload or a traversal sends is
 * Chromium's choice (the entry's original value, or `same-origin` for a reload Playwright
 * starts); either is admitted, and the matrix pins the admission. */
const ADMITTED = ["none", "same-origin"];

async function operatorShown(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "aibi operator" })).toBeVisible();
}

async function refusedPage(page: Page): Promise<void> {
  await expect(page.getByText("ORIGIN_NOT_ALLOWED")).toBeVisible();
  await expect(page.getByRole("heading", { name: "aibi operator" })).toHaveCount(0);
}

test.describe("admitted at /curate", () => {
  test("a typed address (none)", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${origin}/curate`);
    await operatorShown(page);
    expect(await answers.settled()).toEqual([{ status: 200, site: "none", dest: "document" }]);
  });

  test("a click from the catalogue entry (same-origin), in the same tab", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${origin}/`);
    await page.getByRole("link", { name: "Curate" }).click();
    await page.waitForURL(`${origin}/curate`);
    await operatorShown(page);
    expect(context.pages()).toHaveLength(1);
    expect(await answers.settled()).toEqual([{ status: 200, site: "same-origin", dest: "document" }]);
  });

  test("a reload, of a typed address or of a click", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${origin}/curate`);
    await page.reload();
    await operatorShown(page);
    await page.goto(`${origin}/`);
    await page.getByRole("link", { name: "Curate" }).click();
    await page.waitForURL(`${origin}/curate`);
    await page.reload();
    await operatorShown(page);
    const seen = await answers.settled();
    expect(seen.map((one) => one.status)).toEqual([200, 200, 200, 200]);
    expect(seen.every((one) => ADMITTED.includes(one.site ?? ""))).toBe(true);
  });

  test("a script's location.reload() (same-origin)", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${origin}/curate`);
    await operatorShown(page);
    await Promise.all([
      page.waitForResponse(`${origin}/curate`),
      page.evaluate(() => {
        location.reload();
      }),
    ]);
    await operatorShown(page);
    expect((await answers.settled()).map((seen) => [seen.status, seen.site])).toEqual([
      [200, "none"],
      [200, "same-origin"],
    ]);
  });

  test("back and forward", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${origin}/curate`);
    await page.goto(`${origin}/`);
    await page.goBack();
    await page.waitForURL(`${origin}/curate`);
    await operatorShown(page);
    await page.goForward();
    await page.waitForURL(`${origin}/`);
    await page.goBack();
    await operatorShown(page);
    const seen = await answers.settled();
    expect(seen.length).toBeGreaterThanOrEqual(2);
    expect(seen.every((one) => one.status === 200 && ADMITTED.includes(one.site ?? ""))).toBe(true);
  });

  test("history.back() from another site's page in the same tab: a fresh load, no state back", async ({
    page,
    context,
    origin,
    watcher,
  }) => {
    const answers = curate(context);
    await page.goto(`${origin}/curate`);
    await operatorShown(page);
    await page.evaluate(() => {
      Object.assign(window, { __aibiMarker: "held before leaving" });
    });
    // The other site's page itself calls history.back(); page.goBack() would be the person.
    await page.goto(`${other().site}/back`);
    await page.waitForURL(`${origin}/curate`);
    await operatorShown(page);
    await page.waitForLoadState("load");
    await expect.poll(() => page.evaluate(() => Reflect.get(window, "__aibiPersisted") as unknown)).toBe(false);
    expect(await page.evaluate(() => Reflect.get(window, "__aibiMarker") as unknown)).toBeUndefined();
    expect(
      await page.evaluate(() => performance.getEntriesByType("navigation").map((entry) => (entry as PerformanceNavigationTiming).type)),
    ).toEqual(["back_forward"]);
    watcher.clean();
    const seen = await answers.settled();
    expect(seen.map((one) => one.status)).toEqual([200, 200]);
    expect(seen.every((one) => ADMITTED.includes(one.site ?? ""))).toBe(true);
  });
});

test.describe("refused at /curate (ORIGIN_NOT_ALLOWED)", () => {
  test("a link from another site", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${other().site}/link?to=${encodeURIComponent(`${origin}/curate`)}`);
    await page.locator("#go").click();
    await page.waitForURL(`${origin}/curate`);
    await refusedPage(page);
    expect(await answers.settled()).toEqual([{ status: 403, site: "cross-site", dest: "document" }]);
  });

  test("a link from the same site on another port", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${other().port}/link?to=${encodeURIComponent(`${origin}/curate`)}`);
    await page.locator("#go").click();
    await page.waitForURL(`${origin}/curate`);
    await refusedPage(page);
    expect(await answers.settled()).toEqual([{ status: 403, site: "same-site", dest: "document" }]);
  });

  test("a link from this server's catalogue at localhost to 127.0.0.1", async ({ page, context, origin, bundle }) => {
    const answers = curate(context);
    const port = new URL(origin).port;
    await page.goto(`http://localhost:${port}/`);
    await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
    await page.evaluate((target) => {
      const link = document.createElement("a");
      link.href = target;
      link.id = "elsewhere";
      link.textContent = "elsewhere";
      document.body.append(link);
    }, `${origin}/curate`);
    await page.locator("#elsewhere").click();
    await page.waitForURL(`${origin}/curate`);
    await refusedPage(page);
    expect(await answers.settled(), bundle).toEqual([{ status: 403, site: "cross-site", dest: "document" }]);
  });

  test("a frame on another site's page, whose refusal may not be framed either", async ({ page, context, origin }) => {
    const answers = curate(context);
    const framed = page.waitForResponse(`${origin}/curate`);
    await page.goto(`${other().site}/frame?to=${encodeURIComponent(`${origin}/curate`)}`);
    const answer = await framed;
    expect(answer.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
    await expect.poll(async () => (await answers.settled()).length).toBe(1);
    expect(await answers.settled()).toEqual([{ status: 403, site: "cross-site", dest: "iframe" }]);
    await expect(page.frameLocator("#frame").getByText("ORIGIN_NOT_ALLOWED")).toHaveCount(0);
    await expect(page.frameLocator("#frame").getByRole("heading", { name: "aibi operator" })).toHaveCount(0);
  });

  test("a frame on this server's own pages is refused by their own policy before any request", async ({ page, context, origin, watcher }) => {
    const answers = curate(context);
    await page.goto(`${origin}/`);
    await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
    await page.evaluate(() => {
      const frame = document.createElement("iframe");
      frame.id = "frame";
      frame.src = "/curate";
      document.body.append(frame);
    });
    await expect.poll(() => watcher.violations.map((violation) => violation.directive)).toEqual(["frame-src"]);
    expect(await answers.settled()).toEqual([]);
    await expect(page.frameLocator("#frame").getByRole("heading", { name: "aibi operator" })).toHaveCount(0);
  });

  test("a window another origin opens", async ({ page, context, origin }) => {
    const answers = curate(context);
    await page.goto(`${other().site}/open?to=${encodeURIComponent(`${origin}/curate`)}`);
    const [opened] = await Promise.all([context.waitForEvent("page"), page.locator("#open").click()]);
    await opened.waitForURL(`${origin}/curate`);
    await refusedPage(opened);
    expect(await answers.settled()).toEqual([{ status: 403, site: "cross-site", dest: "document" }]);
  });
});
