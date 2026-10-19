/**
 * The screens over the server's own answers (D431, D422): the fixture's text through a stubbed
 * `fetch`, the real seam (decode, walk) and the real routes under `<StrictMode>`, through the hash
 * router. **Calls:** `#/` sends exactly one request, `search_catalog` with exactly `{"limit":50}`;
 * `#/datasets/orchard` exactly one, `describe_dataset` with exactly `{"dataset":"orchard"}`; neither
 * sends `describe_column` or `count_cohort`; a third call waits while two are outstanding (the tab's
 * one queue), and a screen left before its call's turn never sends it; a hash change between two ids
 * shows the second's answer. **What is shown** of the orchard's answers: its tables' rows with their
 * `stat:` references, the release `@1` published, no disclosure setting, the standing caveat with its
 * tokens, the six analyses, the graph's edge, the columns, the 404 through `Refusals`, and the four
 * answers of D431 (a page of one hit and its `next_offset`, three columns and `columns_next`, both
 * tools under a floor of 50: the trees' 40 rows suppressed, *k* shown). The queue is module state:
 * every cell imports a fresh module.
 */
import { StrictMode } from "react";
import { createHashRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { render, type Rendered } from "./render";
import { notShownProblems, orchard, type Sent, serving } from "./screens";

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

/** The app's routes, from a fresh module graph (a queue of their own), rendered at `hash` under
 * StrictMode through the hash router. */
async function at(hash: string): Promise<Rendered> {
  vi.resetModules();
  const { catalogueRoutes } = await import("../../src/catalogue/routes");
  window.history.replaceState(null, "", `/${hash}`);
  shown = await render(
    <StrictMode>
      <RouterProvider router={createHashRouter(catalogueRoutes())} />
    </StrictMode>,
  );
  return shown;
}

/** Go to another hash, as a link would. */
async function go(hash: string): Promise<void> {
  const { act } = await import("react");
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

/** `fetch` answered by the orchard's answers, by tool and dataset (a dataset other than the
 * orchard: the 404). */
function orchardServer(): Sent[] {
  return serving((one) => {
    if (one.tool === "search_catalog") {
      return orchard("search_catalog");
    }
    return one.body === '{"dataset":"orchard"}' ? orchard("describe_dataset") : orchard("describe_dataset", 404);
  });
}

/** `fetch` whose answers wait until the test releases them, in order. `release` answers a call and
 * resolves once the page has read the whole answer and the microtasks after it have run (the body is
 * a stream of the test's, which says when it is closed): the queue has then settled the call, so that
 * what it does next can be asserted without waiting for a time. */
function heldServer(): { readonly sent: Sent[]; release: (index: number) => Promise<void> } {
  const waiting: ((response: Response) => void)[] = [];
  const sent = serving(
    () =>
      new Promise<Response>((resolve) => {
        waiting.push(resolve);
      }),
  );
  return {
    sent,
    release: async (index) => {
      const one = sent[index];
      const answer = one?.tool === "search_catalog" ? orchard("search_catalog") : one?.body === '{"dataset":"orchard"}' ? orchard("describe_dataset") : orchard("describe_dataset", 404);
      const bytes = new TextEncoder().encode(await answer.text());
      let closed: () => void = () => undefined;
      const read = new Promise<void>((resolve) => {
        closed = resolve;
      });
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(bytes);
        },
        pull(controller) {
          controller.close();
          closed();
        },
      });
      waiting[index]?.(new Response(body, { status: answer.status, headers: answer.headers }));
      await read;
      const { act } = await import("react");
      await act(async () => {
        for (let turn = 0; turn < 20; turn += 1) {
          await Promise.resolve();
        }
      });
    },
  };
}

const bodies = (sent: readonly Sent[]): (readonly [string, string | null])[] => sent.map((one) => [one.tool, one.body] as const);

describe("the calls a load makes (D422: k = 1 for each screen; D431)", () => {
  it("#/ sends exactly one request, search_catalog, with exactly {\"limit\":50}", async () => {
    const sent = orchardServer();
    const found = await at("#/");
    await found.until((words) => words.includes("Datasets: 1"));
    expect(bodies(sent)).toEqual([["search_catalog", '{"limit":50}']]);
  });

  it("#/datasets/orchard sends exactly one request, describe_dataset, with exactly {\"dataset\":\"orchard\"}: no describe_column, no count_cohort", async () => {
    const sent = orchardServer();
    const found = await at("#/datasets/orchard");
    await found.until((words) => words.startsWith("Dataset orchard"));
    expect(bodies(sent)).toEqual([["describe_dataset", '{"dataset":"orchard"}']]);
  });

  it("a third call waits while two are outstanding, and is sent when one settles", async () => {
    const server = heldServer();
    const found = await at("#/datasets/a");
    await go("#/datasets/b");
    await go("#/datasets/c");
    expect(bodies(server.sent)).toEqual([
      ["describe_dataset", '{"dataset":"a"}'],
      ["describe_dataset", '{"dataset":"b"}'],
    ]);
    await server.release(0);
    await found.until(() => server.sent.length === 3);
    expect(server.sent[2]).toEqual({ tool: "describe_dataset", body: '{"dataset":"c"}' });
    await server.release(1);
    await server.release(2);
    expect(await found.until((words) => words.startsWith("The dataset could not be described."))).toContain("UNKNOWN_DATASET");
  });

  it("a screen left before its call's turn never sends it; coming back mounts it anew and sends it", async () => {
    const server = heldServer();
    const found = await at("#/datasets/a");
    await go("#/datasets/b");
    await go("#/datasets/orchard");
    await go("#/nope");
    await found.until((words) => words === "aibi Nothing is here. The catalogue");
    await server.release(0);
    await server.release(1);
    expect(bodies(server.sent).map(([, body]) => body)).toEqual(['{"dataset":"a"}', '{"dataset":"b"}']);
    await go("#/datasets/orchard");
    await found.until(() => server.sent.length === 3);
    await server.release(2);
    expect(await found.until((words) => words.startsWith("Dataset orchard"))).toContain("Applicable analyses");
    expect(bodies(server.sent).map(([, body]) => body)).toEqual(['{"dataset":"a"}', '{"dataset":"b"}', '{"dataset":"orchard"}']);
  });

  it("a hash change between two ids shows the second's answer", async () => {
    const sent = orchardServer();
    const found = await at("#/datasets/orchard");
    await found.until((words) => words.startsWith("Dataset orchard"));
    await go("#/datasets/walnuts");
    expect(await found.until((words) => words.startsWith("The dataset could not be described."))).toContain("UNKNOWN_DATASET");
    expect(found.container.textContent).not.toContain("Applicable analyses");
    expect(bodies(sent).map(([, body]) => body)).toEqual(['{"dataset":"orchard"}', '{"dataset":"walnuts"}']);
  });
});

describe("the orchard's answers, through the real seam and the routes", () => {
  it("the catalogue: the hit, its release and disclosure, its tables' rows with their references", async () => {
    orchardServer();
    const found = await at("#/");
    await found.until((words) => words.includes("Datasets: 1"));
    const text = found.container.textContent;
    const manifest = "sha256:af9437cdad3f3621ce3afc862cf8eb6dbd78996ef4a7c693c0801ed839612900";
    expect(text).toContain(`Dataset orchard, release @1 · manifest ${manifest}, status published`);
    expect(text).toContain("Disclosure: none (no minimum cell count applies).");
    expect(text).toContain(`harvests harvests, grain One row per harvest_id, role measurement, rows 60 stat:${manifest}/harvests/n_rows`);
    expect(text).toContain(`trees trees, grain One row per tree_id, role entity, rows 40 stat:${manifest}/trees/n_rows`);
    expect(found.container.querySelector("h2 a")?.getAttribute("href")).toBe("#/datasets/orchard");
  });

  it("the dataset: the caveat with its tokens, the six analyses, the edge, the tables' columns, and what it does not show", async () => {
    orchardServer();
    const found = await at("#/datasets/orchard");
    await found.until((words) => words.startsWith("Dataset orchard"));
    const text = found.container.textContent;
    expect(text).toContain("Release @1 · manifest sha256:af9437cdad3f3621ce3afc862cf8eb6dbd78996ef4a7c693c0801ed839612900");
    expect(text).toContain("Disclosure: none");
    const tokens = [...found.container.querySelectorAll("[aria-label=caveats] .token")].map((token) => token.textContent);
    expect(tokens).toContain("rel:harvests.tree_id/fields/child_columns");
    expect(tokens).toHaveLength(16);
    expect([...found.container.querySelectorAll("[aria-label='applicable analyses'] > [role=listitem]")]).toHaveLength(6);
    expect(text).toContain("survival.km version 1.1.0: unavailable; missing: endpoint");
    expect(text).toContain("summary.distribution version 1.0.0: available with caveats; unconfirmed: columns");
    expect(text).toContain("Relationship rel:harvests.tree_id: harvests to trees, many to one");
    expect(text).toContain("harvests.harvest_id harvest_id, string, identifier");
    expect(text).toContain("trees.height_m height_m, number");
    expect(text).not.toContain("not an identifier");
    expect(text).toContain("Columns: 9");
    expect(found.container.querySelector(".legend")?.textContent).toContain("are not shown on this page yet");
    expect(notShownProblems(found.container, "orchard")).toEqual([]);
  });

  it("the 404, through Refusals, echoing nothing of the address", async () => {
    orchardServer();
    const found = await at("#/datasets/no_such_dataset");
    await found.until((words) => words.startsWith("The dataset could not be described."));
    expect(found.container.textContent).toBe(
      "The dataset could not be described.The request was refused.UNKNOWN_DATASET at /dataset: No release of the dataset was published; these datasets have oneAvailable (not applied): orchard",
    );
  });

  it("D431's four answers: a page of one hit, three columns, and both tools under a floor of 50", async () => {
    serving((one) => (one.tool === "search_catalog" ? orchard("search_catalog", 200, 1) : orchard("describe_dataset", 200, 1)));
    let found = await at("#/");
    await found.until((words) => words.includes("Datasets: 2"));
    expect(found.container.textContent).toContain("More datasets follow from position 1; this page shows the first page.");
    expect(found.container.querySelectorAll("section.hit")).toHaveLength(1);
    found.unmount();
    found = await at("#/datasets/orchard");
    await found.until((words) => words.startsWith("Dataset orchard"));
    expect(found.container.textContent).toContain("Columns: 9; the columns from position 3 on are not on this page.");
    expect(found.container.querySelectorAll("[aria-label=columns] > [role=listitem]")).toHaveLength(3);
    expect(notShownProblems(found.container, "orchard")).toEqual([]);
    found.unmount();
    vi.unstubAllGlobals();
    serving((one) => (one.tool === "search_catalog" ? orchard("search_catalog", 200, 2) : orchard("describe_dataset", 200, 2)));
    found = await at("#/");
    await found.until((words) => words.includes("Datasets: 2"));
    const floored = found.container.textContent;
    expect(floored).toContain("Disclosure: minimum cell count k = 50");
    expect(floored).toMatch(/trees trees, grain One row per tree_id, role entity, rows suppressed stat:sha256:[0-9a-f]{64}\/trees\/n_rows\?floor=50/u);
    expect(floored).toContain("SUPPRESSED (note): Counts from 1 to min_cell_count - 1");
    found.unmount();
    found = await at("#/datasets/orchard");
    await found.until((words) => words.startsWith("Dataset orchard"));
    expect(found.container.textContent).toMatch(/Table: rows suppressed stat:sha256:[0-9a-f]{64}\/trees\/n_rows\?floor=50/u);
    expect(found.container.textContent).toContain("summary.members version 1.0.0: unavailable; missing: min_cell_count");
    expect(notShownProblems(found.container, "orchard")).toEqual([]);
  });
});
