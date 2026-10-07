/**
 * Matrix (5): the files a document makes the browser fetch are exactly what the server-written
 * document names (its script, `modulepreload` and `stylesheet` links) and the lazy chunks the
 * test triggered (each lazy module's file, its stylesheets and its static imports' files, read
 * from the build's manifest by the module's own key); and the operator entry's fetch no chart
 * code, no router and none of the catalogue's chunks.
 */
import type { Page } from "@playwright/test";

import { expect, type Manifest, test, WORDS } from "./fixtures";

/** The `/assets/` paths a document the server wrote names, from its text. */
function named(html: string): Set<string> {
  const found = new Set<string>();
  for (const match of html.matchAll(/<(script|link)\b([^>]*)>/gu)) {
    const attributes = match[2] ?? "";
    const target = /\b(?:src|href)="(\/assets\/[^"]+)"/u.exec(attributes)?.[1];
    if (target === undefined) {
      continue;
    }
    const rel = /\brel="([^"]+)"/u.exec(attributes)?.[1];
    if (match[1] === "script" || rel === "modulepreload" || rel === "stylesheet") {
      found.add(target);
    }
  }
  return found;
}

/** A lazy module's files: its own, its stylesheets and its static imports', transitively. */
function lazy(manifest: Manifest, key: string): Set<string> {
  const found = new Set<string>();
  const pending = [key];
  for (let current = pending.pop(); current !== undefined; current = pending.pop()) {
    const chunk = manifest[current];
    if (chunk === undefined) {
      throw new Error(`the manifest has no ${current}`);
    }
    if (found.has(`/${chunk.file}`)) {
      continue;
    }
    found.add(`/${chunk.file}`);
    for (const css of chunk.css ?? []) {
      found.add(`/${css}`);
    }
    pending.push(...(chunk.imports ?? []));
  }
  return found;
}

function fetched(page: Page): Set<string> {
  const found = new Set<string>();
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/assets/")) {
      found.add(url.pathname);
    }
  });
  return found;
}

/** The manifest's key of each file it names. */
function keys(manifest: Manifest): Map<string, string> {
  const found = new Map<string, string>();
  for (const [key, chunk] of Object.entries(manifest)) {
    found.set(`/${chunk.file}`, key);
    for (const css of chunk.css ?? []) {
      found.set(`/${css}`, key);
    }
  }
  return found;
}

test("the catalogue entry fetches what its document names and the lazy chunk it opened", async ({ page, origin, manifest, watcher }) => {
  const requested = fetched(page);
  const answer = await page.goto(`${origin}/#/nope`);
  await expect(page.getByText("Nothing is here.")).toBeVisible();
  const oracle = new Set([...named((await answer?.text()) ?? ""), ...lazy(manifest, "src/catalogue/NotFound.tsx")]);
  expect(oracle.size).toBeGreaterThanOrEqual(4);
  expect([...requested].sort()).toEqual([...oracle].sort());
  watcher.clean();
});

test("the operator entry fetches what its document names, the harness in the end-to-end build alone, and no chart", async ({
  page,
  origin,
  manifest,
  bundle,
  watcher,
}) => {
  const requested = fetched(page);
  const answer = await page.goto(`${origin}/curate`);
  await expect(page.getByRole("heading", { name: "aibi operator" })).toBeVisible();
  if (bundle === "e2e") {
    await expect(page.getByText(WORDS.harness)).toBeVisible();
  }
  const document = named((await answer?.text()) ?? "");
  expect(document.size).toBeGreaterThanOrEqual(2);
  const oracle = new Set([...document, ...(bundle === "e2e" ? lazy(manifest, "src/harness/Harness.tsx") : [])]);
  expect([...requested].sort()).toEqual([...oracle].sort());
  const owner = keys(manifest);
  const sources = [...requested].map((file) => owner.get(file) ?? `unknown ${file}`);
  for (const source of sources) {
    expect(source).not.toMatch(/^src\/chart\/|node_modules\/(vega|d3|react-router)|^index\.html$|^src\/catalogue\//u);
  }
  if (bundle === "prod") {
    expect(Object.keys(manifest).filter((key) => key.startsWith("src/harness/"))).toEqual([]);
  }
  watcher.clean();
});
