import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { createHashRouter, createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it } from "vitest";

import { catalogueRoutes, type NotFoundModule } from "../../src/catalogue/routes";
import { render, type Rendered } from "./render";

const HOME = "aibi The catalogue opens here. Curate";
const NOT_FOUND = "aibi Nothing is here. The catalogue";
const FAILURE = "aibi This screen failed to load. Reload the page to try again.";

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  window.history.replaceState(null, "", "/");
});

async function at(entry: string, load?: () => Promise<NotFoundModule>): Promise<Rendered> {
  const router = createMemoryRouter(catalogueRoutes(load), { initialEntries: [entry] });
  shown = await render(<RouterProvider router={router} />);
  return shown;
}

describe("the catalogue entry", () => {
  it("shows fixed words at /, and one link: the fixed path /curate, noopener noreferrer", async () => {
    const found = await at("/");
    expect(found.words()).toBe(HOME);
    const links = [...found.container.querySelectorAll("a")];
    expect(links.map((link) => [link.getAttribute("href"), link.getAttribute("rel"), link.target])).toEqual([
      ["/curate", "noopener noreferrer", ""],
    ]);
  });

  it("shows the lazy not-found screen, with its class, for any other hash", async () => {
    for (const entry of ["/nope", "/datasets/x", "/curate", "/withdraw?x=1"]) {
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
    window.history.replaceState(null, "", "/datasets/x#/nope");
    const router = createHashRouter(catalogueRoutes());
    shown = await render(<RouterProvider router={router} />);
    expect(await shown.until((words) => words === NOT_FOUND)).toBe(NOT_FOUND);
    window.history.replaceState(null, "", "/#/");
    const home = createHashRouter(catalogueRoutes());
    shown.unmount();
    shown = await render(<RouterProvider router={home} />);
    expect(shown.words()).toBe(HOME);
  });

  it("composes no link from the URL or from data: the one href in its source is the fixed path", () => {
    const directory = path.join(import.meta.dirname, "../../src/catalogue");
    const hrefs = readdirSync(directory)
      .filter((name) => name.endsWith(".tsx"))
      .flatMap((name) => [...readFileSync(path.join(directory, name), "utf8").matchAll(/href=("[^"]*")/gu)].map((m) => m[1]));
    expect(hrefs.sort()).toEqual(['"#/"', '"/curate"']);
  });
});
