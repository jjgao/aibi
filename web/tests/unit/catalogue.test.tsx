import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { createHashRouter, createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { catalogueRoutes, type NotFoundModule } from "../../src/catalogue/routes";
import { render, type Rendered } from "./render";
import { orchard, serving } from "./screens";

const NOT_FOUND = "aibi Nothing is here. The catalogue";
const FAILURE = "aibi This screen failed to load. Reload the page to try again.";

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

async function at(entry: string, load?: () => Promise<NotFoundModule>): Promise<Rendered> {
  const router = createMemoryRouter(catalogueRoutes(load), { initialEntries: [entry] });
  shown = await render(<RouterProvider router={router} />);
  return shown;
}

/** The catalogue's words over the orchard's answer, as far as the first hit's heading. */
const CATALOGUE = /^Catalogue The latest published release of each dataset is listed; text in the descriptors comes from the datasets and is shown as it is\. Datasets: 1 orchard /u;

describe("the catalogue entry", () => {
  it("shows the catalogue at /, its links the fixed path /curate (noopener noreferrer) and each hit's #/datasets/<id>", async () => {
    serving(() => orchard("search_catalog"));
    const found = await at("/");
    expect(await found.until((words) => CATALOGUE.test(words))).toMatch(/ Curate$/u);
    const links = [...found.container.querySelectorAll("a")];
    expect(links.map((link) => [link.getAttribute("href"), link.getAttribute("rel"), link.target])).toEqual([
      ["#/datasets/orchard", null, ""],
      ["/curate", "noopener noreferrer", ""],
    ]);
  });

  it("shows the lazy not-found screen, with its class, for any other hash", async () => {
    for (const entry of ["/nope", "/datasets", "/datasets/a/b", "/curate", "/withdraw?x=1"]) {
      const found = await at(entry);
      expect(await found.until((words) => words === NOT_FOUND)).toBe(NOT_FOUND);
      expect(found.container.querySelector("main")?.className).toBe("not-found");
      found.unmount();
      shown = undefined;
    }
  });

  it("shows the error element's fixed words, never the error, when a screen fails", async () => {
    const secret = "secret <b>message</b> from data";
    const found = await at("/nope", () => Promise.reject(new Error(secret)));
    expect(await found.until((words) => words === FAILURE)).toBe(FAILURE);
    expect(found.container.innerHTML).not.toContain("secret");
  });

  it("routes by the hash under the hash router", async () => {
    serving(() => orchard("search_catalog"));
    window.history.replaceState(null, "", "/datasets/x#/nope");
    const router = createHashRouter(catalogueRoutes());
    shown = await render(<RouterProvider router={router} />);
    expect(await shown.until((words) => words === NOT_FOUND)).toBe(NOT_FOUND);
    window.history.replaceState(null, "", "/#/");
    const catalogue = createHashRouter(catalogueRoutes());
    shown.unmount();
    shown = await render(<RouterProvider router={catalogue} />);
    expect(await shown.until((words) => CATALOGUE.test(words))).toMatch(CATALOGUE);
  });

  it("composes no link from the URL or from data: the screens' literal hrefs are the fixed paths, and Link's template the one composed", () => {
    const hrefs: string[] = [];
    for (const directory of ["catalogue", "dataset", "router", "layout"]) {
      const at_ = path.join(import.meta.dirname, "../../src", directory);
      for (const name of readdirSync(at_).filter((file) => file.endsWith(".tsx"))) {
        hrefs.push(...[...readFileSync(path.join(at_, name), "utf8").matchAll(/href=("[^"]*"|\{[^}]*\})/gu)].map((m) => `${directory}/${name} ${m[1] ?? ""}`));
      }
    }
    expect(hrefs.sort()).toEqual(['catalogue/Catalogue.tsx "/curate"', 'catalogue/NotFound.tsx "#/"', "layout/Link.tsx {target}", 'router/address.tsx "#/"']);
  });
});
