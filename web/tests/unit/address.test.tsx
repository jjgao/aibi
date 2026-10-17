/**
 * The address (D428): the router module reads it through `idOfAddress` alone and echoes nothing of
 * it. Through the REAL hash router (`createHashRouter`, the catalogue's routes): a corpus of hashes
 * a link from another site could hold (D314) reaches the DOM as fixed words or an id of the
 * grammar, nothing un-visible, nothing thrown. The pre-router redirect: `/datasets/<segment>`
 * becomes `/#/datasets/<id>`, or `/`, from the raw segment (no decoding: a malformed escape cannot
 * throw), the address's query and hash dropped. The root's error element shows fixed words alone.
 */
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogueRoutes, type NotFoundModule } from "../../src/catalogue/routes";
import { NotFound } from "../../src/catalogue/NotFound";
import { redirectAddress } from "../../src/router/address";
import { isUnsafe } from "./injection";
import { render, type Rendered } from "./render";
import { orchard, type Sent, serving } from "./screens";

const NOT_FOUND = "aibi Nothing is here. The catalogue";
const REFUSED = "aibi No dataset has that address. The catalogue";
const FAILURE = "aibi This screen failed to load. Reload the page to try again.";
/** The dataset's screen over the server's 404 (`UNKNOWN_DATASET`), which echoes nothing of the address. */
const DATASET_REFUSED = "The dataset could not be described. The request was refused. UNKNOWN_DATASET at /dataset : No release of the dataset was published; these datasets have one Available (not applied): orchard";
/** The attribute values the screens of the corpus write: fixed. */
const ATTRIBUTES = new Set(["#/", "not-found", "", "dataset", "state", "refused", "refusal", "list", "listitem", "data", "data token", "alternatives"]);

let shown: Rendered | undefined;

let sent: Sent[] = [];

beforeEach(() => {
  // Every tool call the routes make, answered by the server's own answers over the orchard: the
  // catalogue, the dataset, and the 404 for any other dataset.
  sent = serving((one) => (one.tool === "search_catalog" ? orchard("search_catalog") : one.body === '{"dataset":"orchard"}' && answering ? orchard("describe_dataset") : orchard("describe_dataset", 404)));
});

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  answering = false;
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

/** Whether the orchard's own description answers `orchard` (else the 404 answers every dataset). */
let answering = false;

const load = (): Promise<NotFoundModule> => Promise.resolve({ NotFound });

/** The hash corpus: the segment after `#/datasets/`, and whether the door gives an id (`orchard`
 * when the router's decoding makes one of it). */
const HASHES: readonly (readonly [string, boolean])[] = [
  ["orchard", true],
  ["%E2%80%AEabc", false],
  ["%E2%80%AEtxt.exe", false],
  ["%252F", false],
  ["%3Cimg%3E", false],
  ["%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E", false],
  ["javascript:alert(1)", false],
  ["%2f", false],
  ["a%2Fb", false],
  ["/", false],
  ["a/b", false],
  ["?", false],
  ["a?b=c", true],
  ["#", false],
  ["a#b", true],
  ["a%23b", false],
  ["a%3Fb", false],
  ["__", false],
  ["a__b", false],
  ["_a", false],
  ["a".repeat(64), true],
  ["a".repeat(65), false],
  ["caf%C3%A9", false],
  ["café", false],
  ["%E0%A4%A", false],
  ["%", false],
  ["%%", false],
  ["%zz", false],
  ["orch%61rd", true],
  ["orch%2561rd", false],
  ["a%0Ab", false],
  ["a%00b", false],
  ["%F0%9F%98%80", false],
  ["😀", false],
  ["ORCHARD", false],
  ["orchard%20", false],
  ["orchard%E2%80%8B", false],
  ["orchard.", false],
  ["..", false],
  ["%2e%2e", false],
  ["", false],
];

describe("the dataset route through the real hash router", () => {
  it.each(HASHES)("#/datasets/%s: fixed words or an id, nothing un-visible, nothing thrown", async (segment, isId) => {
    window.history.replaceState(null, "", `/#/datasets/${segment}`);
    const router = createHashRouter(catalogueRoutes(load));
    shown = await render(<RouterProvider router={router} />);
    const words = await shown.until((found) => found === NOT_FOUND || found === REFUSED || found === FAILURE || found === DATASET_REFUSED);
    // An id lands on the dataset's screen, its id in the request's body alone (the 404 echoes none of
    // it); anything else on the fixed words, never echoed.
    expect(words).toBe(segment === "" || segment === "/" || segment === "a/b" || segment === "?" || segment === "#" ? NOT_FOUND : isId ? DATASET_REFUSED : REFUSED);
    const bodies = sent.map((one) => [one.tool, one.body]);
    expect(bodies).toEqual(isId && words === DATASET_REFUSED ? [["describe_dataset", JSON.stringify({ dataset: segment === "orch%61rd" ? "orchard" : segment.replace(/[?#].*$/u, "") })]] : []);
    const text = shown.container.textContent;
    expect(Array.from(text).filter((character) => isUnsafe(character.codePointAt(0) ?? 0))).toEqual([]);
    expect(shown.container.querySelectorAll("img, script, b")).toHaveLength(0);
    for (const element of shown.container.querySelectorAll("*")) {
      for (const attribute of element.attributes) {
        expect(ATTRIBUTES.has(attribute.value), attribute.value).toBe(true);
      }
    }
  });
});

describe("the pre-router redirect", () => {
  it.each([
    ["/datasets/orchard", "/#/datasets/orchard"],
    ["/datasets/orchard?x=1", "/#/datasets/orchard"],
    ["/datasets/orchard#/datasets/other", "/#/datasets/orchard"],
    ["/datasets/orchard#/withdraw?x", "/#/datasets/orchard"],
    ["/datasets/a_b2", "/#/datasets/a_b2"],
    [`/datasets/${"a".repeat(64)}`, `/#/datasets/${"a".repeat(64)}`],
    [`/datasets/${"a".repeat(65)}`, "/"],
    ["/datasets/%E2%80%AEabc", "/"],
    ["/datasets/javascript:alert(1)", "/"],
    ["/datasets/%6Aavascript:", "/"],
    ["/datasets/%252F", "/"],
    ["/datasets/%2f%2fevil.example", "/"],
    ["/datasets/a%2Fb", "/"],
    ["/datasets/a%3Fb", "/"],
    ["/datasets/%23x", "/"],
    ["/datasets/%3Cimg%3E", "/"],
    ["/datasets/orch%61rd", "/"],
    ["/datasets/caf%C3%A9", "/"],
    ["/datasets/%E0%A4%A", "/"],
    ["/datasets/a__b", "/"],
    ["/datasets/_a", "/"],
    ["/datasets/ORCHARD", "/"],
    ["/datasets/", "/"],
  ])("%s becomes %s", (from, to) => {
    window.history.replaceState(null, "", from);
    const before = window.history.length;
    expect(() => {
      redirectAddress();
    }).not.toThrow();
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe(to);
    expect(window.history.length).toBe(before);
    expect(window.history.state).toBeNull();
  });

  it.each(["/", "/#/datasets/orchard", "/#/nope", "/datasets/a/b", "/curate", "/datasetsx/a"])("leaves %s alone", (address) => {
    window.history.replaceState(null, "", address);
    redirectAddress();
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe(address);
  });

  it("lands an id on its hash route, the dataset's screen, through the real hash router, and a refused segment on the catalogue", async () => {
    answering = true;
    window.history.replaceState(null, "", "/datasets/orchard");
    redirectAddress();
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load))} />);
    expect(await shown.until((found) => found.startsWith("Dataset orchard Release @"))).toContain("Applicable analyses");
    expect(sent.map((one) => [one.tool, one.body])).toEqual([["describe_dataset", '{"dataset":"orchard"}']]);
    shown.unmount();
    window.history.replaceState(null, "", "/datasets/%E2%80%AEtxt.exe");
    redirectAddress();
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load))} />);
    expect(await shown.until((found) => found.startsWith("Catalogue The latest published release"))).toMatch(/ Curate$/u);
    expect(shown.container.textContent.includes("\u202e")).toBe(false);
    expect(sent.map((one) => one.tool)).toEqual(["describe_dataset", "search_catalog"]);
  });
});

describe("the root's error element", () => {
  it("shows fixed words, never the error's message, whatever it holds", async () => {
    window.history.replaceState(null, "", "/#/datasets/orchard");
    const secret = "\u202esecret <img src=x onerror=alert(1)> from the address";
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load, () => Promise.reject(new Error(secret))))} />);
    expect(await shown.until((found) => found === FAILURE)).toBe(FAILURE);
    expect(shown.container.innerHTML).not.toContain("secret");
    expect(shown.container.textContent.includes("\u202e")).toBe(false);
  });
});
