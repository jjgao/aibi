/**
 * The column's route (D443) through the REAL hash router and the catalogue's routes: a corpus of
 * column addresses (the dataset's half and the column's, each through its door) reaches the DOM as
 * fixed words or the column's screen, nothing un-visible, nothing echoed; one `describe_column` a
 * mount, under StrictMode too; the screen keyed by both ids, so another column of the dataset, or
 * another dataset's column of the same id, mounts it anew with its own call and content; dataset →
 * column → dataset → back, each a sibling route mounted anew; the dataset screen's links land on the
 * column's screen; the lazy module's failure shows the root's fixed words; and the address's column
 * is never echoed, whatever the load's state.
 */
import { StrictMode } from "react";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { catalogueRoutes, type NotFoundModule } from "../../src/catalogue/routes";
import { NotFound } from "../../src/catalogue/NotFound";
import { NOT_SHOWN, PENDING, POOLED, REFUSED as COLUMN_REFUSED } from "../../src/dataset/Column";
import { isUnsafe } from "./injection";
import { render, type Rendered } from "./render";
import { json, orchardText, type Sent, serving } from "./screens";
import { answerAt, ORCHARD } from "./typed-support";

const load = (): Promise<NotFoundModule> => Promise.resolve({ NotFound });

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

const REFUSED = "aibi No dataset has that address. The catalogue";
const NOT_FOUND = "aibi Nothing is here. The catalogue";
const FAILURE = "aibi This screen failed to load. Reload the page to try again.";

/** A recorded column answer's text, by its place in the section. */
const column = (place: number): string => ORCHARD[answerAt("columns", place)]?.body ?? "";

/** The server's 422 for a column the dataset does not have (synthetic; the server's words). */
const UNKNOWN_COLUMN = '{"refusals":[{"code":"UNKNOWN_COLUMN","path":"/column","message":[{"text":"The table has no such column; it has these"}],"alternatives":[{"data":"trees.variety"}]}]}';

/** The answers: the orchard's description; a column of the first six recorded ones by its id; grove's
 * `trees.variety` (the floor-14 answer, re-addressed); any other column refused. */
function answer(one: Sent): Response {
  if (one.tool === "describe_dataset") {
    return json(orchardText("describe_dataset"));
  }
  const body = JSON.parse(one.body ?? "{}") as { readonly column?: string; readonly dataset?: string };
  if (body.dataset === "grove") {
    return json(column(10).replace('"dataset":"orchard"', '"dataset":"grove"'));
  }
  const found = [0, 1, 2, 3, 4, 5].map(column).find((text) => text.includes(`"column":"${body.column ?? ""}"`));
  return found === undefined ? json(UNKNOWN_COLUMN, 422) : json(found);
}

/** The attribute values the corpus's screens write: fixed (a column's screen's link back, too). */
const ATTRIBUTES = new Set(["#/", "#/datasets/orchard", "not-found", "", "column", "dataset", "state", "meta", "refused", "refusal", "list", "listitem", "data", "data token", "alternatives", "legend", "states", "distribution", "categories", "bins", "stat", "release", "affects", "caveats", "caveat warn", "label", "number", "note", "disclosure", "not-shown"]);

/** The column corpus: the hash after `#/datasets/`, and the column the doors give (`null`: fixed words). */
const HASHES: readonly (readonly [string, string | null])[] = [
  ["orchard/columns/trees.variety", "trees.variety"],
  ["orchard/columns/trees%2Evariety", "trees.variety"],
  ["orch%61rd/columns/trees.variety", "trees.variety"],
  ["orchard/columns/trees.variety?x=1", "trees.variety"],
  ["orchard/columns/trees.variety/", "trees.variety"],
  ["orchard/columns/trees.variety%2F", null],
  ["orchard/columns/%252E", null],
  ["orchard/columns/trees%252Evariety", null],
  ["orchard/columns/stat.variety", "stat.variety"],
  ["orchard/columns/value.variety", "value.variety"],
  ["orchard/columns/cohort.height_m", "cohort.height_m"],
  ["orchard/columns/model.kind", "model.kind"],
  ["orchard/columns/%E2%80%AEx", null],
  ["orchard/columns/trees.%E2%80%AEx", null],
  ["orchard/columns/%", null],
  ["orchard/columns/%zz", null],
  ["orchard/columns/trees.Variety", null],
  ["orchard/columns/trees.a__b", null],
  ["orchard/columns/trees", null],
  ["orchard/columns/.x", null],
  ["orchard/columns/trees.x.y", null],
  ["orchard/columns/javascript:alert(1)", null],
  ["orchard/columns/%3Cimg%3E.x", null],
  [`orchard/columns/t.${"a".repeat(64)}`, `t.${"a".repeat(64)}`],
  [`orchard/columns/t.${"a".repeat(65)}`, null],
  ["ORCHARD/columns/trees.variety", null],
  ["%E2%80%AEx/columns/trees.variety", null],
  ["a__b/columns/trees.variety", null],
];

describe("the column route through the real hash router", () => {
  it.each(HASHES)("#/datasets/%s: the screen of the door's column, or fixed words; nothing echoed or un-visible", async (rest, named) => {
    const sent = serving(answer);
    window.history.replaceState(null, "", `/#/datasets/${rest}`);
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load))} />);
    const words = await shown.until((found) => found === REFUSED || found === NOT_FOUND || found.startsWith("Column ") || found.startsWith(COLUMN_REFUSED));
    if (named === null) {
      expect(words).toBe(REFUSED);
      expect(sent).toEqual([]);
    } else {
      // A column of the grammar the orchard does not have is the server's to refuse (its 422).
      expect(words.startsWith(named.startsWith("trees.") ? `Column ${named} (` : COLUMN_REFUSED)).toBe(true);
      expect(sent.map((one) => [one.tool, one.body])).toEqual([["describe_column", JSON.stringify({ dataset: "orchard", column: named })]]);
    }
    const text = shown.container.textContent;
    expect(Array.from(text).filter((character) => isUnsafe(character.codePointAt(0) ?? 0))).toEqual([]);
    expect(shown.container.querySelectorAll("img, script, b")).toHaveLength(0);
    for (const element of shown.container.querySelectorAll("*")) {
      for (const attribute of element.attributes) {
        expect(ATTRIBUTES.has(attribute.value), attribute.value).toBe(true);
      }
    }
  });

  it("an address of no column (no column segment, or an empty one) is not found", async () => {
    for (const rest of ["orchard/columns", "orchard/columns/"]) {
      const sent = serving(answer);
      window.history.replaceState(null, "", `/#/datasets/${rest}`);
      shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load))} />);
      expect(await shown.until((found) => found === NOT_FOUND)).toBe(NOT_FOUND);
      expect(sent).toEqual([]);
      shown.unmount();
      shown = undefined;
    }
  });

  it("sends one describe_column a mount under StrictMode (D422: k = 1)", async () => {
    const sent = serving(answer);
    window.history.replaceState(null, "", "/#/datasets/orchard/columns/trees.variety");
    shown = await render(
      <StrictMode>
        <RouterProvider router={createHashRouter(catalogueRoutes(load))} />
      </StrictMode>,
    );
    await shown.until((found) => found.startsWith("Column trees.variety"));
    expect(sent.map((one) => [one.tool, one.body])).toEqual([["describe_column", '{"dataset":"orchard","column":"trees.variety"}']]);
  });

  it("another column of the same dataset mounts the screen anew: one call each, the new column shown", async () => {
    const sent = serving(answer);
    window.history.replaceState(null, "", "/#/datasets/orchard/columns/trees.variety");
    const router = createHashRouter(catalogueRoutes(load));
    shown = await render(<RouterProvider router={router} />);
    await shown.until((found) => found.startsWith("Column trees.variety"));
    await router.navigate("/datasets/orchard/columns/trees.height_m");
    expect(await shown.until((found) => found.startsWith("Column trees.height_m"))).toContain("Histogram");
    expect(sent.map((one) => one.body)).toEqual(['{"dataset":"orchard","column":"trees.variety"}', '{"dataset":"orchard","column":"trees.height_m"}']);
  });

  it("another dataset's column of the same id mounts the screen anew, with its own call and content, and back again", async () => {
    const sent = serving(answer);
    window.history.replaceState(null, "", "/#/datasets/orchard/columns/trees.variety");
    const router = createHashRouter(catalogueRoutes(load));
    shown = await render(<RouterProvider router={router} />);
    expect(await shown.until((found) => found.startsWith("Column trees.variety"))).toContain("apple");
    await router.navigate("/datasets/grove/columns/trees.variety");
    const grove = await shown.until((found) => found.includes("of the dataset grove"));
    expect(grove).not.toContain("apple");
    expect(shown.container.textContent).toContain(`${POOLED} 26 stat:`);
    expect(shown.container.querySelector("main > p.meta a")?.getAttribute("href")).toBe("#/datasets/grove");
    await router.navigate("/datasets/orchard/columns/trees.variety");
    expect(await shown.until((found) => found.includes("of the dataset orchard"))).toContain("apple");
    expect(sent.map((one) => one.body)).toEqual(['{"dataset":"orchard","column":"trees.variety"}', '{"dataset":"grove","column":"trees.variety"}', '{"dataset":"orchard","column":"trees.variety"}']);
  });

  it("dataset → column → dataset → back: each a sibling route mounted anew, one call each", async () => {
    const sent = serving(answer);
    window.history.replaceState(null, "", "/#/datasets/orchard");
    const router = createHashRouter(catalogueRoutes(load));
    shown = await render(<RouterProvider router={router} />);
    await shown.until((found) => found.startsWith("Dataset orchard"));
    await router.navigate("/datasets/orchard/columns/trees.variety");
    await shown.until((found) => found.startsWith("Column trees.variety"));
    await router.navigate("/datasets/orchard");
    await shown.until((found) => found.startsWith("Dataset orchard"));
    await router.navigate(-1);
    await shown.until((found) => found.startsWith("Column trees.variety"));
    expect(sent.map((one) => one.tool)).toEqual(["describe_dataset", "describe_column", "describe_dataset", "describe_column"]);
  });

  it("the dataset screen links each column's id to its screen, and a link lands there with one call", async () => {
    const sent = serving(answer);
    window.history.replaceState(null, "", "/#/datasets/orchard");
    const router = createHashRouter(catalogueRoutes(load));
    shown = await render(<RouterProvider router={router} />);
    await shown.until((found) => found.startsWith("Dataset orchard"));
    const links = [...shown.container.querySelectorAll("[aria-label=columns] a")];
    expect(links.map((link) => link.getAttribute("href"))).toEqual(links.map((link) => `#/datasets/orchard/columns/${link.textContent}`));
    expect(links.map((link) => link.textContent)).toContain("trees.variety");
    const target = links.find((link) => link.textContent === "trees.variety");
    if (!(target instanceof HTMLAnchorElement)) {
      throw new Error("a link to trees.variety");
    }
    target.click();
    expect(await shown.until((found) => found.startsWith("Column trees.variety"))).toContain(NOT_SHOWN.slice(0, 40));
    expect(sent.map((one) => one.tool)).toEqual(["describe_dataset", "describe_column"]);
  });
});

describe("the address's column is never echoed (T4), whatever the load's state", () => {
  const ADDRESS = "/#/datasets/orchard/columns/trees.zz_sentinel";

  it.each([
    ["pending (a fetch that never answers)", () => new Promise<Response>(() => undefined), PENDING],
    ["refused", () => json(UNKNOWN_COLUMN, 422), COLUMN_REFUSED],
    ["a shape the page does not read", () => json('{"column":"trees.zz_sentinel"}'), "disagree about the data's shape"],
    ["no answer", () => Promise.reject(new TypeError("zz_sentinel")), "could not reach the server"],
    ["an answer (another column's: the heading is the answer's own)", () => json(column(0)), "Column trees.variety"],
  ])("%s", async (_name, reply, words) => {
    serving(reply);
    window.history.replaceState(null, "", ADDRESS);
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load))} />);
    await shown.until((found) => found.includes(words));
    expect(shown.container.innerHTML).not.toContain("zz_sentinel");
  });

  it("the lazy module's failure: the root's fixed words, never the error's message", async () => {
    window.history.replaceState(null, "", ADDRESS);
    const secret = "‮secret zz_sentinel <img src=x onerror=alert(1)>";
    shown = await render(<RouterProvider router={createHashRouter(catalogueRoutes(load, undefined, () => Promise.reject(new Error(secret))))} />);
    expect(await shown.until((found) => found === FAILURE)).toBe(FAILURE);
    expect(shown.container.innerHTML).not.toContain("secret");
    expect(shown.container.innerHTML).not.toContain("zz_sentinel");
  });
});
