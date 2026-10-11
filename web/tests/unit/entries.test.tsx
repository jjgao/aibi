/**
 * Both entries start the client before they render anything (D419): on an engine the client
 * cannot run on, the page shows the fixed refusal and nothing else; otherwise the body readers
 * are sealed by the time the entry has run.
 */
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REFUSALS } from "../../src/api/client";
import { freshReaders, words } from "./render";

const ENTRIES = [
  ["the catalogue entry", "/", () => import("../../src/catalogue/main")],
  ["the operator entry", "/curate", () => import("../../src/operator/main")],
] as const;

beforeEach(() => {
  document.body.replaceChildren();
  const root = document.createElement("div");
  root.id = "root";
  document.body.append(root);
  vi.resetModules();
  freshReaders();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.doUnmock("../../src/api/client");
  window.history.replaceState(null, "", "/");
});

describe.each(ENTRIES)("%s", (_entry, address, load) => {
  it("shows the fixed refusal alone on an engine the client cannot run on", async () => {
    vi.doMock("../../src/api/client", async (original) => ({ ...(await original<typeof import("../../src/api/client")>()), start: () => false }));
    window.history.replaceState(null, "", address);
    await act(async () => {
      await load();
    });
    expect(words(document.body)).toBe(REFUSALS.unsupported);
  });

  it("starts the client once, before anything is rendered, and seals the body readers", async () => {
    const rendered: number[] = [];
    vi.doMock("../../src/api/client", async (original) => {
      const client = await original<typeof import("../../src/api/client")>();
      return {
        ...client,
        start: () => {
          rendered.push(document.getElementById("root")?.childNodes.length ?? -1);
          return client.start();
        },
      };
    });
    window.history.replaceState(null, "", address);
    await act(async () => {
      await load();
    });
    expect(rendered).toEqual([0]);
    expect(Object.getOwnPropertyDescriptor(Response.prototype, "json")?.configurable).toBe(false);
    expect(words(document.body)).not.toBe(REFUSALS.unsupported);
    expect(words(document.body)).not.toBe("");
  });
});
