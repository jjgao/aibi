/**
 * Matrix (1): every screen of both entries runs under the documents' policy with no CSP or
 * Trusted Types violation and nothing failing unexpectedly, the lazy chunk and its stylesheet
 * loaded, and the error element shown; (3) an unknown file of `/assets` is a JSON 404 that is
 * never stored, never a document; (4) the documents' headers are D411's.
 */
import { expect, test, words, WORDS } from "./fixtures";

const POLICY =
  "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; " +
  "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'; " +
  "trusted-types 'none'";

/** R54's sentence on the catalogue (D431). */
const CATALOGUE_SENTENCE = "The latest published release of each dataset is listed; text in the descriptors comes from the datasets and is shown as it is.";

const DOCUMENT_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "content-security-policy": POLICY,
  "x-frame-options": "DENY",
  "cache-control": "no-store",
  "cross-origin-opener-policy": "same-origin",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "cross-origin-resource-policy": "same-origin",
};

test.describe("the catalogue entry under the policy", () => {
  test("shows the catalogue (D431): its heading, R54's sentence, the orchard's hit and the fixed /curate link", async ({ page, origin, watcher }) => {
    await page.goto(`${origin}/`);
    await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Catalogue" })).toBeVisible();
    await expect(page.getByText(CATALOGUE_SENTENCE)).toBeVisible();
    await expect(page.getByRole("link", { name: "orchard", exact: true })).toHaveAttribute("href", "#/datasets/orchard");
    watcher.clean();
  });

  test("serves the same entry at /datasets/<id>, which replaces its address by /#/datasets/<id> (D428)", async ({ page, origin, watcher }) => {
    // `x` is an id: the dataset's screen asks describe_dataset, and the server has no such dataset.
    watcher.expect(`${origin}/api/tools/describe_dataset`, 404);
    await page.goto(`${origin}/datasets/x?q=1`);
    await expect(page.getByText("The dataset could not be described.")).toBeVisible();
    await expect(page.getByText("UNKNOWN_DATASET", { exact: true })).toBeVisible();
    // The address is the redirect's (the router's own `history.state`, `{idx: 0}`, is not): and the
    // redirect added no entry, and ran before the router (a router that started at `/datasets/x`
    // would have shown the catalogue, `/` having no hash, and a missing redirect leaves the path).
    expect(await page.evaluate(() => [location.pathname, location.search, location.hash] as const)).toEqual(["/", "", "#/datasets/x"]);
    const direct = await page.context().newPage();
    await direct.goto(`${origin}/#/datasets/x`);
    await expect(direct.getByText("The dataset could not be described.")).toBeVisible();
    expect(await page.evaluate(() => history.length)).toBe(await direct.evaluate(() => history.length));
    watcher.clean();
  });

  test("lands an address that names no dataset on the catalogue, echoing nothing of it (D428)", async ({ page, origin, watcher }) => {
    await page.goto(`${origin}/datasets/%E2%80%AEtxt.exe`);
    await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Catalogue" })).toBeVisible();
    expect(await words(page)).not.toContain("txt.exe");
    expect(await page.evaluate(() => `${location.pathname}${location.search}${location.hash}`)).toBe("/");
    const fresh = await page.context().newPage();
    await fresh.goto(`${origin}/#/datasets/%E2%80%AEtxt.exe`);
    await expect(fresh.getByText("No dataset has that address.")).toBeVisible();
    expect(await words(fresh)).toBe(WORDS.refused);
    watcher.clean();
  });

  test("loads the lazy not-found screen and its stylesheet", async ({ page, origin, watcher, manifest }) => {
    const lazy = manifest["src/catalogue/NotFound.tsx"];
    expect(lazy?.css?.length).toBe(1);
    const requested: string[] = [];
    page.on("requestfinished", (request) => requested.push(new URL(request.url()).pathname));
    await page.goto(`${origin}/#/nope`);
    await expect(page.getByText("Nothing is here.")).toBeVisible();
    expect(await words(page)).toBe(WORDS.notFound);
    expect(await page.locator("main").evaluate((main) => getComputedStyle(main).borderLeftWidth)).toBe("4px");
    expect(requested).toContain(`/${lazy?.file ?? ""}`);
    expect(requested).toContain(`/${lazy?.css?.[0] ?? ""}`);
    await page.getByRole("link", { name: "The catalogue" }).click();
    await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
    watcher.clean();
  });

  test("shows the error element's fixed words when the lazy chunk fails", async ({ page, origin, watcher, manifest }) => {
    const file = `${origin}/${manifest["src/catalogue/NotFound.tsx"]?.file ?? ""}`;
    await page.route(file, (route) => route.fulfill({ status: 404, contentType: "application/json", body: "{}" }));
    watcher.expect(file, 404);
    watcher.expect(file, "failed");
    await page.goto(`${origin}/#/nope`);
    await expect(page.getByText("This screen failed to load.")).toBeVisible();
    expect(await words(page)).toBe(WORDS.failure);
    watcher.clean();
  });
});

test.describe("the visible form under the policy (D428)", () => {
  test("renders the injection corpus through every component that opens a box, with no violation", async ({ page, origin, watcher, bundle }) => {
    test.skip(bundle !== "e2e", "the harness, which renders the corpus, is in the end-to-end build alone");
    await page.goto(`${origin}/curate`);
    await expect(page.getByText(WORDS.harness)).toBeVisible();
    await page.getByRole("button", { name: "corpus", exact: true }).click();
    const corpus = page.locator("[data-corpus]");
    await expect(corpus.locator("bdi.data").first()).toBeVisible();
    // The escapes' styling is the emitted stylesheet's, loaded under style-src 'self' (D411).
    const escape = corpus.locator(".escape").first();
    expect(await escape.evaluate((element) => [getComputedStyle(element).unicodeBidi, getComputedStyle(element).direction])).toEqual(["isolate", "ltr"]);
    expect(await corpus.locator("bdi.data").first().evaluate((element) => getComputedStyle(element).whiteSpace)).toBe("pre-wrap");
    // Nothing the data spelled became an element; no raw override, isolate or lone surrogate is in the text.
    expect(await corpus.locator("img, script, b").count()).toBe(0);
    const tags = await corpus.evaluate((root) => [...new Set([...root.querySelectorAll("*")].map((element) => element.tagName.toLowerCase()))].sort());
    expect(tags).toEqual(["bdi", "option", "p", "select", "span"]);
    // (The zero-width non-joiner and joiner, which D312 keeps, are raw: the corpus holds a ZWJ sequence.)
    expect(await corpus.evaluate((root) => /(?![\t\n\u200c\u200d])[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(root.textContent))).toBe(false);
    expect(await corpus.evaluate((root) => root.textContent.includes("\u{1f468}\u200d\u{1f469}\u200d\u{1f467}") && root.textContent.includes("a\u200cb"))).toBe(true);
    expect(await corpus.locator(".cut").count()).toBeGreaterThan(0);
    watcher.clean();
  });
});

test.describe("the operator entry under the policy", () => {
  test("shows fixed words", async ({ page, origin, watcher, bundle }) => {
    await page.goto(`${origin}/curate`);
    await expect(page.getByRole("heading", { name: "aibi operator" })).toBeVisible();
    if (bundle === "e2e") {
      await expect(page.getByText(WORDS.harness)).toBeVisible();
    }
    expect(await words(page)).toBe(bundle === "e2e" ? `${WORDS.operator} ${WORDS.harness}` : WORDS.operator);
    watcher.clean();
  });

  test("ignores a hash and resets it to the fixed path, adding no history entry", async ({ page, origin, watcher, bundle }) => {
    await page.goto(`${origin}/curate`);
    await expect(page.getByRole("heading", { name: "aibi operator" })).toBeVisible();
    if (bundle === "e2e") {
      await expect(page.getByText(WORDS.harness)).toBeVisible();
    }
    const plain = await words(page);
    const entries = await page.evaluate(() => history.length);
    const fresh = await page.context().newPage();
    await fresh.goto(`${origin}/curate#/withdraw?x`);
    await expect(fresh.getByRole("heading", { name: "aibi operator" })).toBeVisible();
    if (bundle === "e2e") {
      await expect(fresh.getByText(WORDS.harness)).toBeVisible();
    }
    expect(await words(fresh)).toBe(plain);
    expect(await fresh.evaluate(() => [location.href, history.length, history.state] as const)).toEqual([
      `${origin}/curate`,
      entries,
      null,
    ]);
    watcher.clean();
  });
});

test.describe("headers and refusals", () => {
  test("the documents carry D411's headers", async ({ request, origin }) => {
    for (const address of ["/", "/datasets/x", "/curate"]) {
      const answer = await request.get(`${origin}${address}`, { headers: { "sec-fetch-site": "none" } });
      expect(answer.status(), address).toBe(200);
      const headers = answer.headers();
      for (const [name, value] of Object.entries(DOCUMENT_HEADERS)) {
        expect(headers[name], `${address} ${name}`).toBe(value);
      }
      expect(headers["etag"]).toBeUndefined();
      expect(headers["last-modified"]).toBeUndefined();
    }
  });

  test("/curate is refused for each fetch-metadata vector only its own rule refuses (D412)", async ({ request, origin }) => {
    // Absent and `none, none` pass the generic rule ("a request from another site needs an
    // Origin"); a `same-site` or `cross-site` request with the server's own Origin passes it
    // too. Only the /curate rule (an exact `none` or `same-origin`) refuses these.
    const vectors: [string, Record<string, string>][] = [
      ["absent", {}],
      ["same-site, with the server's own Origin", { "sec-fetch-site": "same-site", origin }],
      ["cross-site, with the server's own Origin", { "sec-fetch-site": "cross-site", origin }],
      ["none, none", { "sec-fetch-site": "none, none" }],
    ];
    for (const [name, headers] of vectors) {
      for (const address of ["/curate", "/curate/x"]) {
        const answer = await request.get(`${origin}${address}`, { headers });
        expect(answer.status(), `${name} ${address}`).toBe(403);
        expect(await answer.text(), `${name} ${address}`).toContain("ORIGIN_NOT_ALLOWED");
      }
    }
    const admitted = await request.get(`${origin}/curate`, { headers: { "sec-fetch-site": "same-origin" } });
    expect(admitted.status()).toBe(200);
  });

  test("a bundle's file is served immutable, with its type", async ({ request, origin, manifest }) => {
    const file = manifest["operator.html"]?.file ?? "";
    const answer = await request.get(`${origin}/${file}`);
    expect(answer.status()).toBe(200);
    expect(answer.headers()["content-type"]).toBe("text/javascript; charset=utf-8");
    expect(answer.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
  });

  test("an unknown file of /assets is a JSON 404, never stored, never a document", async ({ request, origin }) => {
    for (const address of ["/assets/x.js", "/assets/index.html", "/assets/..%2Findex.html", "/assets/", "/assets/a/b.js"]) {
      const answer = await request.get(`${origin}${address}`);
      expect(answer.status(), address).toBe(404);
      expect(answer.headers()["content-type"], address).toBe("application/json");
      expect(answer.headers()["cache-control"], address).toBe("no-store");
      const body = await answer.text();
      expect(body.toLowerCase(), address).not.toContain("<!doctype");
      expect((JSON.parse(body) as { refusals: { code: string }[] }).refusals.map((refusal) => refusal.code), address).toEqual([
        "NOT_FOUND",
      ]);
    }
  });

  test("/favicon.ico is the page path's NOT_FOUND, never stored (no icon link)", async ({ request, origin }) => {
    const answer = await request.get(`${origin}/favicon.ico`);
    expect(answer.status()).toBe(404);
    expect(answer.headers()["cache-control"]).toBe("no-store");
    expect(answer.headers()["content-type"]).toBe("text/html; charset=utf-8");
    expect(await answer.text()).toContain("NOT_FOUND");
  });
});
