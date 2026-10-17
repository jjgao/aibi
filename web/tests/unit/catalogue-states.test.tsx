/**
 * The catalogue's loader (`Catalogue`; D431) in each state, through a stubbed `fetch` and the real
 * seam: loading, an answer, refused, invalid (a shape the page does not read, no answer), the
 * deadline and a load that throws, each in fixed words, never an error's message, and each with the
 * fixed link to the operator entry (n9). The queue is module state: each cell imports a fresh module.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SHAPE_MISMATCH, UNREACHABLE } from "../../src/api/typed";
import { FAILED, PENDING, REFUSED } from "../../src/catalogue/Catalogue";
import { DEADLINE } from "../../src/catalogue/load";
import { render, type Rendered } from "./render";
import { json, orchard, serving } from "./screens";

type Screen = typeof import("../../src/catalogue/Catalogue");

async function fresh(): Promise<Screen> {
  vi.resetModules();
  return import("../../src/catalogue/Catalogue");
}

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  vi.unstubAllGlobals();
  vi.doUnmock("../../src/api/typed");
  vi.useRealTimers();
});

/** The one link of every state: the fixed path to the operator entry. */
function curate(rendered: Rendered): string[][] {
  return [...rendered.container.querySelectorAll("a")].filter((link) => link.textContent === "Curate").map((link) => [link.getAttribute("href") ?? "", link.getAttribute("rel") ?? ""]);
}

describe("Catalogue (the loader), in each state, with the fixed /curate link in every one", () => {
  it("pending: fixed words, and the id of nothing", async () => {
    const { Catalogue } = await fresh();
    serving(() => new Promise<Response>(() => undefined));
    shown = await render(<Catalogue />);
    expect(shown.words()).toBe(`Catalogue ${PENDING} Curate`);
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });

  it("an answer: the hits, and the link", async () => {
    const { Catalogue } = await fresh();
    serving(() => orchard("search_catalog"));
    shown = await render(<Catalogue />);
    await shown.until((words) => words.includes("datasets"));
    expect(shown.container.querySelector("h2 a")?.getAttribute("href")).toBe("#/datasets/orchard");
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });

  it("refused: fixed words and the refusals, as data", async () => {
    const { Catalogue } = await fresh();
    serving(() => json('{"refusals":[{"code":"LIMIT_EXCEEDED","path":null,"message":[{"text":"No place: "},{"data":"client_tool_calls"}],"alternatives":[],"limit":{"name":"client_tool_calls","max":2}}]}', 503, { "Retry-After": "30" }));
    shown = await render(<Catalogue />);
    await shown.until((found) => found.includes(REFUSED));
    const text = shown.container.textContent;
    expect(text).toContain("LIMIT_EXCEEDED at the document as a whole: No place: client_tool_calls");
    expect(text).toContain("Try again after 30 seconds");
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });

  it.each([
    ["a shape the page does not read", () => json('{"hits": 3}'), SHAPE_MISMATCH],
    ["no answer", () => Promise.reject(new TypeError("network <b>secret</b>")), UNREACHABLE],
  ])("invalid, %s: fixed words", async (_name, answer, words) => {
    const { Catalogue } = await fresh();
    serving(answer);
    shown = await render(<Catalogue />);
    expect(await shown.until((found) => found.includes(words))).toBe(`Catalogue ${words} Curate`);
    expect(shown.container.innerHTML).not.toContain("secret");
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });

  it("deadline: its own words 45 s after it was queued", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { Catalogue } = await fresh();
    serving(() => new Promise<Response>(() => undefined));
    shown = await render(<Catalogue />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(shown.words()).toBe(`Catalogue ${DEADLINE} Curate`);
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });

  it("failed: fixed words, never the error's message", async () => {
    vi.resetModules();
    vi.doMock("../../src/api/typed", async (original) => ({ ...(await original<object>()), searchCatalog: () => Promise.reject(new Error("secret <img src=x onerror=alert(1)>")) }));
    const { Catalogue } = await import("../../src/catalogue/Catalogue");
    shown = await render(<Catalogue />);
    expect(await shown.until((found) => found.includes(FAILED))).toBe(`Catalogue ${FAILED} Curate`);
    expect(shown.container.innerHTML).not.toContain("secret");
    expect(curate(shown)).toEqual([["/curate", "noopener noreferrer"]]);
  });
});
