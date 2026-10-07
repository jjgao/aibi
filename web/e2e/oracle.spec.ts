/**
 * The run-time oracle (D419), against the built bundles served by the real server.
 *
 * - After start-up, on both entries and both builds, every body reader of `Response` and
 *   `Request` but the stream (`json`, `text`, `arrayBuffer`, `blob`, `bytes`, `formData`) throws
 *   the client's fixed words: a body read anywhere but by the client's capped reader fails.
 * - In the end-to-end build, whose harness runs requests through the client to `/api` and
 *   `/operator` (answers and refusals), every such response the page received was decoded: the
 *   decoder's count equals the responses the browser saw, and is not zero.
 */
import { expect, test } from "./fixtures";

const SEALED = "A body is read by the client's decoder alone (D419).";
const READERS = ["json", "text", "arrayBuffer", "blob", "bytes", "formData"];

test.describe("the run-time oracle", () => {
  for (const address of ["/", "/curate"]) {
    test(`seals every body reader but the stream after start-up, at ${address}`, async ({ page, origin, watcher }) => {
      await page.goto(`${origin}${address}`);
      await expect(page.locator("main")).toBeVisible();
      const read = await page.evaluate(async (readers) => {
        const found: string[] = [];
        for (const name of readers) {
          for (const [kind, made] of [
            ["Response", (): Body => new Response("{}")],
            ["Request", (): Body => new Request(window.location.origin, { method: "POST", body: "{}" })],
          ] as const) {
            try {
              const reader = (made() as unknown as Record<string, () => Promise<unknown>>)[name];
              await reader?.call(made());
              found.push(`${kind}.${name}: read`);
            } catch (error) {
              found.push(`${kind}.${name}: ${error instanceof Error ? error.message : "?"}`);
            }
          }
        }
        const stream = new Response("{}").body?.getReader();
        found.push(`stream: ${(await stream?.read())?.done === false ? "read" : "?"}`);
        return found;
      }, READERS);
      expect(read).toEqual([
        ...READERS.flatMap((name) => [`Response.${name}: ${SEALED}`, `Request.${name}: ${SEALED}`]),
        "stream: read",
      ]);
      watcher.clean();
    });
  }

  test("every /api and /operator response the page received passed through the decoder", async ({ page, origin, watcher, bundle }) => {
    test.skip(bundle !== "e2e", "the decoder's counter is read by the end-to-end build's harness");
    const received: string[] = [];
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.origin === origin && /^\/(?:api|operator)(?:\/|$)/u.test(url.pathname)) {
        received.push(`${url.pathname} ${String(response.status())}`);
      }
    });
    watcher.expect(`${origin}/api/tools/describe_dataset`, 404);
    watcher.expect(`${origin}/operator/datasets`, 401);
    // Chromium 141 reports a 401 that carries `WWW-Authenticate` as aborted (`net::ERR_ABORTED`)
    // after its body was delivered, about half the time, whoever reads it (probed with a bare
    // `fetch` of `/operator/datasets`: 13 of 24); that the body was read is the decoder's count.
    watcher.expect(`${origin}/operator/datasets`, "failed");
    await page.goto(`${origin}/curate`);
    const oracle = page.getByRole("button", { name: "oracle" });
    await oracle.click();
    await expect(oracle).not.toHaveAttribute("data-oracle", "");
    const decoded = await oracle.getAttribute("data-oracle");
    expect(received.sort()).toEqual([
      "/api/health 200",
      "/api/tools/describe_dataset 404",
      "/api/tools/list_analyses 200",
      "/api/tools/search_catalog 200",
      "/operator/datasets 401",
    ]);
    expect(decoded).toBe(String(received.length));
    watcher.clean();
  });
});
