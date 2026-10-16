/**
 * The classes of rounds 1 and 2 of #119, at run time: each case below renders a raw U+202E through
 * the real React and react-router, so the class is real. `tests/gates/display-lint.test.ts` reads
 * this file's cases and lints them through `eslint.config.mjs` as a file of `src/`: those between
 * `// case:` and `// end` (the own-children exemption's writes) are refused by the display lint
 * (D429, R90); those between `// open:` and `// end` (R90's flow class: a route built apart, a
 * component called as a function) are OPEN, asserted not refused, until 2b-1L2 closes the class.
 * The two hold the same text.
 */
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";
import { describe, expect, it } from "vitest";

const RAW = "evil‮text";

// case: own children with a default
export function OwnDefault({ children = RAW }: { readonly children?: string }) {
  return <p>{children}</p>;
}
// end

// case: own children reassigned
export function OwnReassigned({ children }: { readonly children: string }) {
  children = RAW;
  return <p>{children}</p>;
}
// end

// open: a route's element built apart
const home = { index: true, element: RAW };
export const routes: RouteObject[] = [{ children: [home] }];
// end

// open: a getter named children, in a direct call
export function OwnShown({ children }: { readonly children: ReactNode }) {
  return <div>{children}</div>;
}
export const gotten = OwnShown({
  get children() {
    return RAW;
  },
});
// end

// open: a class field named children, in a direct call
class Props {
  readonly children = RAW;
}
export const fielded = OwnShown(new Props());
// end

/** What an element renders, as text. */
async function rendered(element: ReactNode): Promise<string> {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => {
    root.render(element);
    await Promise.resolve();
  });
  const text = host.textContent;
  root.unmount();
  return text;
}

describe("each class renders the raw text at run time (the lint refuses the first two, the rest are OPEN: display-lint.test.ts)", () => {
  it("own children with a default, and reassigned", async () => {
    expect(await rendered(<OwnDefault />)).toBe(RAW);
    expect(await rendered(<OwnReassigned>x</OwnReassigned>)).toBe(RAW);
  });

  it("a route's element built apart, through the real router", async () => {
    expect(await rendered(<RouterProvider router={createHashRouter(routes)} />)).toContain(RAW);
  });

  it("a getter and a class field named children, in a direct call", async () => {
    expect(await rendered(gotten)).toBe(RAW);
    expect(await rendered(fielded)).toBe(RAW);
  });
});
