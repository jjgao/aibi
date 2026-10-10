import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CURATE_PATH, forgetAddress } from "../../src/operator/url";
import { freshReaders, words } from "./render";

const OPERATOR = "aibi operator Enter your name and the curator token to begin. Your name Curator token Unlock The operator screens are not built yet. The catalogue";
const ORIGIN = window.location.origin;

/** A fresh document body with its `<div id="root">`, and fresh modules, as a page load gives. */
function freshPage(): void {
  document.body.replaceChildren();
  const root = document.createElement("div");
  root.id = "root";
  document.body.append(root);
  vi.resetModules();
  freshReaders();
}

beforeEach(freshPage);

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

/** Load the operator entry at `address` as a browser would: its module runs on import. */
async function load(address: string): Promise<{ words: string; href: string; added: number }> {
  window.history.replaceState(null, "", address);
  const before = window.history.length;
  await act(async () => {
    await import("../../src/operator/main");
  });
  const root = document.getElementById("root");
  if (root === null) {
    throw new Error("no root");
  }
  return { words: words(root), href: window.location.href, added: window.history.length - before };
}

describe("the operator entry", () => {
  it("shows fixed words, and no harness outside the end-to-end build", async () => {
    expect((await load("/curate")).words).toBe(OPERATOR);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(document.querySelector("[data-harness]")).toBeNull();
    expect(words(document.body)).toBe(OPERATOR);
  });

  it.each(["/curate#/withdraw?x", "/curate#", "/curate?dataset=x#/publish", "/curate#/datasets/x/take-over"])(
    "ignores %s: the same words, the address reset to the fixed path, no history entry added",
    async (address) => {
      const plain = await load("/curate");
      freshPage();
      const given = await load(address);
      expect(given.words).toBe(plain.words);
      expect(given.href).toBe(`${ORIGIN}/curate`);
      expect(given.added).toBe(0);
      expect(window.history.state).toBeNull();
    },
  );

  it("forgets the address by replacing it with the fixed path, state null", () => {
    window.history.replaceState({ held: 1 }, "", "/curate#/withdraw?x");
    const before = window.history.length;
    forgetAddress();
    expect(window.location.href).toBe(`${ORIGIN}${CURATE_PATH}`);
    expect(window.history.length).toBe(before);
    expect(window.history.state).toBeNull();
  });

  it("reads nothing from its URL: its source names no part of the location", () => {
    const directory = path.join(import.meta.dirname, "../../src/operator");
    for (const name of readdirSync(directory)) {
      const source = readFileSync(path.join(directory, name), "utf8")
        .replace(/\/\*[\s\S]*?\*\//gu, "")
        .replace(/\/\/.*$/gmu, "");
      expect(source, name).not.toMatch(/\blocation\b|document\.URL|documentURI|\.hash\b|URLSearchParams|\bnew URL\b|hashchange|popstate/u);
    }
  });
});
